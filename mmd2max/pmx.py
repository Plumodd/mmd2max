"""PMX 2.0/2.1 reader for mesh, skeletal, morph and physics transfer.

Parses the sections this pipeline actually consumes: vertices, faces, textures,
materials, bones, morphs (all types, for stream alignment), display frames
(skipped but byte-accurate), rigid bodies and joints.

Bone morph offsets carry a **vec4 quaternion** rotation (PMX 2.0/2.1 spec,
cross-checked against felixjones' spec and the Go/Blender PMX implementations).
Reading it as a vec3 euler — which v2 did — desynchronises the byte stream by
4 bytes per item and corrupts every section after it. `_parse_tail` therefore
verifies the end-of-file invariant: a correct parse lands exactly on the last
byte of the file.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
import struct
from typing import BinaryIO


class PMXError(ValueError):
    """Raised when a PMX file is malformed or unsupported."""


@dataclass
class Header:
    version: float
    encoding: str
    additional_uvs: int
    vertex_index_size: int
    texture_index_size: int
    material_index_size: int
    bone_index_size: int
    morph_index_size: int
    rigid_index_size: int


@dataclass
class Vertex:
    position: tuple[float, float, float]
    normal: tuple[float, float, float]
    uv: tuple[float, float]
    weights: list[tuple[int, float]] = field(default_factory=list)
    deform_type: str = "BDEF1"


@dataclass
class Material:
    name: str
    name_en: str
    diffuse: tuple[float, float, float, float]
    specular: tuple[float, float, float]
    specular_power: float
    ambient: tuple[float, float, float]
    draw_flags: int
    texture_index: int
    sphere_index: int
    sphere_mode: int
    index_count: int


@dataclass
class Bone:
    name: str
    name_en: str
    position: tuple[float, float, float]
    parent_index: int
    layer: int
    flags: int
    tail_index: int = -1
    tail_offset: tuple[float, float, float] = (0.0, 1.0, 0.0)
    inherit_parent_index: int = -1
    inherit_weight: float = 0.0
    ik_target_index: int = -1
    ik_iterations: int = 0
    ik_angle_limit: float = 0.0


@dataclass
class Morph:
    """PMX 变形（表情/形状变化）。

    panel: 0=系统 1=眉毛 2=眼 3=口 4=其他
    morph_type: 0=group 1=vertex 2=bone 3..7=uv(uv1..uv4) 8=material 9=flip 10=impulse
    仅 vertex(1) 类型会被导出为 Max Morpher 通道；其余类型保留元信息供将来扩展。
    """
    name: str
    name_en: str
    panel: int
    morph_type: int
    items: list = field(default_factory=list)          # vertex: (vidx, (dx,dy,dz)); bone: (bidx, (dx,dy,dz), (x,y,z,w))
    group_refs: list = field(default_factory=list)      # group 类型: [(morph_index, weight)]


@dataclass
class RigidBody:
    """PMX 物理刚体。

    shape: 0=球 1=盒 2=胶囊
    physics_mode: 0=跟随骨(运动学) 1=物理 2=物理+骨
    bone_index: 关联的骨骼序号（-1 表示无）
    size: 形状尺寸 (x,y,z)，胶囊的 y 为半高（不含两端半球）
    """
    name: str
    name_en: str
    bone_index: int
    group: int
    nocollide_mask: int
    shape: int
    size: tuple[float, float, float]
    position: tuple[float, float, float]
    rotation: tuple[float, float, float]   # 弧度
    mass: float
    move_damping: float
    rotation_damping: float
    repulsion: float
    friction: float
    physics_mode: int


@dataclass
class Joint:
    """PMX 物理关节（6DOF 弹簧约束）。"""
    name: str
    name_en: str
    joint_type: int
    rigid_a: int
    rigid_b: int
    position: tuple[float, float, float]
    rotation: tuple[float, float, float]
    pos_min: tuple[float, float, float]
    pos_max: tuple[float, float, float]
    rot_min: tuple[float, float, float]
    rot_max: tuple[float, float, float]
    pos_spring: tuple[float, float, float]
    rot_spring: tuple[float, float, float]


@dataclass
class PMXModel:
    name: str
    name_en: str
    header: Header
    vertices: list[Vertex]
    faces: list[tuple[int, int, int]]
    textures: list[str]
    materials: list[Material]
    bones: list[Bone]
    morphs: list[Morph] = field(default_factory=list)
    rigid_bodies: list[RigidBody] = field(default_factory=list)
    joints: list[Joint] = field(default_factory=list)
    source: Path = Path(".")
    deform_types: set = field(default_factory=set)
    warnings: list = field(default_factory=list)


class Reader:
    def __init__(self, stream: BinaryIO):
        self.stream = stream

    def read(self, size: int) -> bytes:
        data = self.stream.read(size)
        if len(data) != size:
            raise PMXError(f"文件意外结束：需要 {size} 字节，仅读到 {len(data)} 字节")
        return data

    def unpack(self, fmt: str):
        return struct.unpack("<" + fmt, self.read(struct.calcsize("<" + fmt)))

    def u8(self) -> int:
        return self.unpack("B")[0]

    def i32(self) -> int:
        return self.unpack("i")[0]

    def f32(self) -> float:
        return self.unpack("f")[0]

    def vec2(self):
        return self.unpack("2f")

    def vec3(self):
        return self.unpack("3f")

    def vec4(self):
        return self.unpack("4f")

    def text(self, encoding: str) -> str:
        size = self.i32()
        if size < 0 or size > 256 * 1024 * 1024:
            raise PMXError(f"非法文本长度：{size}")
        raw = self.read(size)
        try:
            return raw.decode(encoding).rstrip("\x00")
        except UnicodeDecodeError as exc:
            raise PMXError(f"文本解码失败（{encoding}）：{exc}") from exc

    def index(self, size: int, signed: bool) -> int:
        formats = {
            (1, False): "B", (2, False): "H", (4, False): "i",
            (1, True): "b", (2, True): "h", (4, True): "i",
        }
        if size not in (1, 2, 4):
            raise PMXError(f"非法索引宽度：{size}")
        return self.unpack(formats[(size, signed)])[0]


def _count(reader: Reader, label: str, maximum: int = 100_000_000) -> int:
    value = reader.i32()
    if value < 0 or value > maximum:
        raise PMXError(f"{label} 数量非法：{value}")
    return value


def _finite(values: tuple[float, ...]) -> bool:
    return all(math.isfinite(v) for v in values)


def load_pmx(path: str | Path) -> PMXModel:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise PMXError(f"找不到 PMX 文件：{source}")
    with source.open("rb") as stream:
        r = Reader(stream)
        if r.read(4) != b"PMX ":
            raise PMXError("不是有效的 PMX 文件（文件头必须为 'PMX '）")
        version = r.f32()
        if not (2.0 <= version <= 2.11):
            raise PMXError(f"仅支持 PMX 2.0/2.1，当前版本为 {version}")
        globals_count = r.u8()
        if globals_count != 8:
            raise PMXError(f"不支持的 PMX 全局字段数：{globals_count}（应为 8）")
        settings = r.read(globals_count)
        encoding = "utf-16-le" if settings[0] == 0 else "utf-8" if settings[0] == 1 else None
        if encoding is None:
            raise PMXError(f"未知文本编码标记：{settings[0]}")
        if settings[1] > 4:
            raise PMXError(f"附加 UV 数量非法：{settings[1]}")
        if any(size not in (1, 2, 4) for size in settings[2:]):
            raise PMXError("PMX 索引宽度只能为 1、2 或 4 字节")
        header = Header(version, encoding, settings[1], *settings[2:8])
        name = r.text(encoding)
        name_en = r.text(encoding)
        r.text(encoding)  # Local comment.
        r.text(encoding)  # English comment.

        vertices: list[Vertex] = []
        unbound = 0
        deform_types: set = set()
        for vertex_index in range(_count(r, "顶点")):
            position = r.vec3()
            normal = r.vec3()
            uv = r.vec2()
            for _ in range(header.additional_uvs):
                r.vec4()
            if not _finite(position):
                raise PMXError(f"顶点 {vertex_index} 坐标含非法值（NaN/Inf）")
            if not _finite(normal):
                raise PMXError(f"顶点 {vertex_index} 法线含非法值（NaN/Inf）")
            deform = r.u8()
            weights: list[tuple[int, float]] = []
            if deform == 0:  # BDEF1
                weights = [(r.index(header.bone_index_size, True), 1.0)]
            elif deform == 1:  # BDEF2
                b0 = r.index(header.bone_index_size, True)
                b1 = r.index(header.bone_index_size, True)
                w0 = r.f32()
                weights = [(b0, w0), (b1, 1.0 - w0)]
            elif deform == 2:  # BDEF4
                bones = [r.index(header.bone_index_size, True) for _ in range(4)]
                raw_weights = list(r.unpack("4f"))
                weights = list(zip(bones, raw_weights))
            elif deform == 3:  # SDEF
                deform_types.add("SDEF")
                b0 = r.index(header.bone_index_size, True)
                b1 = r.index(header.bone_index_size, True)
                w0 = r.f32()
                weights = [(b0, w0), (b1, 1.0 - w0)]
                r.vec3()  # C
                r.vec3()  # R0
                r.vec3()  # R1
            elif deform == 4:  # PMX 2.1 QDEF
                deform_types.add("QDEF")
                bones = [r.index(header.bone_index_size, True) for _ in range(4)]
                raw_weights = list(r.unpack("4f"))
                weights = list(zip(bones, raw_weights))
            else:
                raise PMXError(f"顶点 {vertex_index} 使用未知权重类型 {deform}")
            r.f32()  # Edge scale.
            # 过滤非法/负权重，并合并重复骨骼索引（QDEF/BDEF4 可能重复）
            merged: dict = {}
            for b, w in weights:
                if not math.isfinite(w):
                    raise PMXError(f"顶点 {vertex_index} 的权重含非法值（NaN/Inf）")
                if b >= 0 and w > 0.0:
                    merged[b] = merged.get(b, 0.0) + w
            weights = [(b, merged[b]) for b in merged]
            if not weights:
                # 真正的「无绑定」顶点（MMD 允许 bone=-1）：绑定到 0 号骨骼作为兜底，
                # 但记录计数以便导出时告警；这里不再静默伪装成合法数据。
                unbound += 1
                weights = [(0, 1.0)]
            total = sum(w for _, w in weights)
            if total <= 0.0:
                weights = [(weights[0][0], 1.0)]
            else:
                weights = [(b, w / total) for b, w in weights]
            vertices.append(Vertex(position, normal, uv, weights,
                                   "SDEF" if "SDEF" in deform_types and deform == 3
                                   else "QDEF" if "QDEF" in deform_types and deform == 4
                                   else {0: "BDEF1", 1: "BDEF2", 2: "BDEF4"}[deform]))

        index_count = _count(r, "面索引")
        if index_count % 3:
            raise PMXError(f"面索引数 {index_count} 不是 3 的倍数")
        flat_faces = [r.index(header.vertex_index_size, False) for _ in range(index_count)]
        if any(index < 0 or index >= len(vertices) for index in flat_faces):
            raise PMXError("面数据引用了不存在的顶点（含负数索引）")
        faces = [tuple(flat_faces[i:i + 3]) for i in range(0, index_count, 3)]

        textures = [r.text(encoding) for _ in range(_count(r, "贴图", 1_000_000))]
        materials: list[Material] = []
        material_index_total = 0
        for _ in range(_count(r, "材质", 1_000_000)):
            mat_name = r.text(encoding)
            mat_name_en = r.text(encoding)
            diffuse = r.vec4()
            specular = r.vec3()
            specular_power = r.f32()
            ambient = r.vec3()
            draw_flags = r.u8()
            r.vec4()  # Edge colour.
            r.f32()  # Edge size.
            texture_index = r.index(header.texture_index_size, True)
            sphere_index = r.index(header.texture_index_size, True)
            sphere_mode = r.u8()
            toon_shared = r.u8()
            if toon_shared:
                r.u8()
            else:
                r.index(header.texture_index_size, True)
            r.text(encoding)  # Memo.
            mat_indices = _count(r, "材质面索引")
            if mat_indices % 3:
                raise PMXError(f"材质「{mat_name}」的索引数 {mat_indices} 不是 3 的倍数")
            material_index_total += mat_indices
            if not _finite(diffuse) or not _finite(specular) or not _finite(ambient):
                raise PMXError(f"材质「{mat_name}」的颜色含非法值（NaN/Inf）")
            materials.append(Material(
                mat_name, mat_name_en, diffuse, specular, specular_power,
                ambient, draw_flags, texture_index, sphere_index, sphere_mode,
                mat_indices,
            ))
        if material_index_total != index_count:
            raise PMXError(
                f"材质面索引总和 {material_index_total} 与总面索引数 {index_count} 不一致")
        if any(not _finite(d) for m in materials for d in (m.diffuse, m.specular, m.ambient)):
            # 二次保险（上面已检查，这里保留防御性约束）
            pass

        bones: list[Bone] = []
        for _ in range(_count(r, "骨骼", 1_000_000)):
            bone_name = r.text(encoding)
            bone_name_en = r.text(encoding)
            position = r.vec3()
            parent_index = r.index(header.bone_index_size, True)
            layer = r.i32()
            flags = r.unpack("H")[0]
            tail_index = -1
            tail_offset = (0.0, 1.0, 0.0)
            if flags & 0x0001:
                tail_index = r.index(header.bone_index_size, True)
            else:
                tail_offset = r.vec3()
            inherit_parent_index = -1
            inherit_weight = 0.0
            if flags & 0x0300:
                inherit_parent_index = r.index(header.bone_index_size, True)
                inherit_weight = r.f32()
            if flags & 0x0400:
                r.vec3()  # Fixed axis.
            if flags & 0x0800:
                r.vec3()  # Local X axis.
                r.vec3()  # Local Z axis.
            if flags & 0x2000:
                r.i32()  # External parent key.
            ik_target_index = -1
            ik_iterations = 0
            ik_angle_limit = 0.0
            if flags & 0x0020:
                ik_target_index = r.index(header.bone_index_size, True)
                ik_iterations = r.i32()
                ik_angle_limit = r.f32()
                for _ in range(_count(r, "IK 链接", 100_000)):
                    r.index(header.bone_index_size, True)
                    limited = r.u8()
                    if limited:
                        r.vec3()
                        r.vec3()
            if not _finite(position):
                raise PMXError(f"骨骼「{bone_name}」坐标含非法值（NaN/Inf）")
            bones.append(Bone(
                bone_name, bone_name_en, position, parent_index, layer, flags,
                tail_index, tail_offset, inherit_parent_index, inherit_weight,
                ik_target_index, ik_iterations, ik_angle_limit,
            ))

        bone_count = len(bones)
        # 顶点权重引用的骨骼必须存在
        for vi, v in enumerate(vertices):
            for b, w in v.weights:
                if b < 0 or b >= bone_count:
                    raise PMXError(f"顶点 {vi} 引用了不存在的骨骼索引 {b}")
        # 骨骼索引边界与父链环检测
        _validate_bone_hierarchy(bones)

        # ---- 段顺序：骨骼 → 变形 → 显示枠 → 刚体 → 关节 ----
        morphs, rigid_bodies, joints = _parse_tail(
            r, stream, source.stat().st_size, header, bone_count)

    if len({bone.name for bone in bones}) != len(bones):
        raise PMXError("PMX 中存在重名骨骼；为避免 FBX/3ds Max 权重错绑，请先修正重名骨骼")

    warnings: list = []
    if unbound:
        warnings.append(f"{unbound} 个顶点在 PMX 中无绑定骨骼，已兜底绑定到 0 号骨骼。")
    if "SDEF" in deform_types:
        warnings.append("模型含 SDEF 顶点：本工具按近似线性混合蒙皮(LBS)处理，"
                        "肩/肘/腕的旋转中心与体积保真会丢失。")
    if "QDEF" in deform_types:
        warnings.append("模型含 QDEF 顶点：本工具按近似线性混合蒙皮(LBS)处理，"
                        "双四元数的抗糖纸效应会丢失。")
    return PMXModel(name, name_en, header, vertices, faces, textures, materials,
                    bones, morphs, rigid_bodies, joints, source, deform_types, warnings)


def _parse_tail(r: Reader, stream: BinaryIO, file_size: int,
                header: Header, bone_count: int):
    """解析 PMX 尾部（变形 → 显示枠 → 刚体 → 关节），并用「末尾不变式」验收。

    为什么要有这条判据：尾部各段全是变长结构，任何一段的字段宽度读错，后面所有段
    就跟着错位，而报错往往落在离真正错误很远的位置（比如「关节数量非法」），排查
    成本极高 —— v2 就是这样把「骨 morph 旋转读成 vec3」藏了几千项才爆的。
    PMX 没有校验和，但有一条免费的强判据：**正确的解析必须正好停在最后一个字节**。
    差任何一个字节都直接判定文件与本解析器不符，绝不带着错位的流继续。
    """
    morphs = _parse_morphs(r, header)
    _parse_display_frames(r, header)
    rigid_bodies = _parse_rigid_bodies(r, header, bone_count)
    joints = _parse_joints(r, header)
    position = stream.tell()
    if position != file_size:
        raise PMXError(
            f"PMX 尾部解析未落在文件末尾：读到 {position} 字节，文件共 {file_size} 字节"
            f"（变形 {len(morphs)} / 刚体 {len(rigid_bodies)} / 关节 {len(joints)}）。"
            "多半是某段的字段宽度与 PMX 2.0/2.1 规范不一致，请核对变形/刚体/关节段。"
        )
    return morphs, rigid_bodies, joints


def _parse_morphs(r: Reader, header: Header) -> list:
    """解析变形段。仅 vertex(1) 类型会用于 Max Morpher；其余类型保留元信息。

    各类型项布局（依据 felixjones PMX 2.0/2.1 规范）：
      0 group : morph_index(morph_index_size) + weight(f32)
      1 vertex: vertex_index(vertex_index_size) + offset(vec3)
      2 bone  : bone_index(bone_index_size) + offset(vec3) + rotation(vec4 四元数)
      3..7 uv : vertex_index(vertex_index_size) + uv(vec4)
      8 material: material_index(material_index_size) + op(u8)
                  + diffuse(vec4)+specular(vec3)+specpow(f32)+ambient(vec3)
                  + edge_color(vec4)+edge_size(f32)+tex_tint(vec4)+sph_tint(vec4)+toon_tint(vec4)
      9 flip    : rigid_index(rigid_index_size) + local_flag(u8)        (PMX 2.1)
      10 impulse : rigid_index(rigid_index_size) + local_flag(u8) + velocity(vec3) + torque(vec3)
    """
    out: list = []
    for _ in range(_count(r, "变形", 100_000)):
        name_jp = r.text(header.encoding)
        name_en = r.text(header.encoding)
        panel = r.u8()
        mtype = r.u8()
        count = _count(r, "变形项目", 1_000_000)
        items: list = []
        group_refs: list = []
        if mtype == 0:
            for _ in range(count):
                mindex = r.index(header.morph_index_size, True)
                weight = r.f32()
                group_refs.append((mindex, weight))
        elif mtype == 1:
            for _ in range(count):
                vidx = r.index(header.vertex_index_size, False)
                off = r.vec3()
                items.append((vidx, off))
        elif mtype == 2:
            for _ in range(count):
                bindex = r.index(header.bone_index_size, True)
                off = r.vec3()
                # 规范里这是四元数 (x,y,z,w)，不是欧拉角：每项 28 字节而非 24。
                rot = r.vec4()
                items.append((bindex, off, rot))
        elif 3 <= mtype <= 7:
            for _ in range(count):
                vidx = r.index(header.vertex_index_size, False)
                uv = r.vec4()
                items.append((vidx, uv))
        elif mtype == 8:
            for _ in range(count):
                mindex = r.index(header.material_index_size, True)
                op = r.u8()
                diffuse = r.vec4()
                specular = r.vec3()
                specpow = r.f32()
                ambient = r.vec3()
                edge_color = r.vec4()
                edge_size = r.f32()
                tex_tint = r.vec4()
                sph_tint = r.vec4()
                toon_tint = r.vec4()
                items.append((mindex, op, diffuse, specular, specpow, ambient,
                             edge_color, edge_size, tex_tint, sph_tint, toon_tint))
        elif mtype == 9:
            for _ in range(count):
                rbindex = r.index(header.rigid_index_size, True)
                local_flag = r.u8()
                items.append((rbindex, local_flag))
        elif mtype == 10:
            for _ in range(count):
                rbindex = r.index(header.rigid_index_size, True)
                local_flag = r.u8()
                velocity = r.vec3()
                torque = r.vec3()
                items.append((rbindex, local_flag, velocity, torque))
        else:
            # PMX 2.0/2.1 只定义 0..10。走到这里说明文件与规范不符，或者前面的段
            # 已经错位。旧版在这里按「32 字节/项」保守丢弃，会把错位藏起来、
            # 让错误在离根因很远的位置才爆，所以改成直接报错。
            raise PMXError(f"变形「{name_jp}」的类型未定义：{mtype}（合法范围 0..10）")
        out.append(Morph(name_jp, name_en, panel, mtype, items, group_refs))
    return out


def _parse_display_frames(r: Reader, header: Header) -> None:
    """显示枠仅用于 MMD GUI 分组，与几何/骨骼无关，跳过但需正确消费字节。"""
    for _ in range(_count(r, "显示枠", 1000)):
        r.text(header.encoding)
        r.text(header.encoding)
        r.u8()  # special flag
        for _ in range(_count(r, "显示要素", 100_000)):
            dtype = r.u8()
            if dtype != 0:
                r.index(header.morph_index_size, True)
            else:
                r.index(header.bone_index_size, True)


def _parse_rigid_bodies(r: Reader, header: Header, bone_count: int) -> list:
    """解析物理刚体段。

    字段序（已与 Go pmx 库 / felixjones 规范交叉验证）：
      name_jp, name_en, bone_index(bone_index_size), group(u8),
      nocollision_group(uint16), shape(u8), size(vec3), position(vec3),
      rotation(vec3), mass(f32), move_damping(f32), rotation_damping(f32),
      repulsion(f32), friction(f32), physics_mode(u8), [PMX2.1 reserved(u8)]
    """
    out: list = []
    for _ in range(_count(r, "刚体", 100_000)):
        name_jp = r.text(header.encoding)
        name_en = r.text(header.encoding)
        bone_index = r.index(header.bone_index_size, True)
        group = r.u8()
        nocollide_mask = r.unpack("H")[0]
        shape = r.u8()
        size = r.vec3()
        position = r.vec3()
        rotation = r.vec3()
        mass = r.f32()
        move_damping = r.f32()
        rotation_damping = r.f32()
        repulsion = r.f32()
        friction = r.f32()
        physics_mode = r.u8()
        if header.version >= 2.1:
            r.u8()  # reserved
        if bone_index < -1 or bone_index >= bone_count:
            raise PMXError(f"刚体「{name_jp}」引用了不存在的骨骼索引 {bone_index}")
        out.append(RigidBody(name_jp, name_en, bone_index, group, nocollide_mask,
                            shape, size, position, rotation, mass, move_damping,
                            rotation_damping, repulsion, friction, physics_mode))
    return out


def _parse_joints(r: Reader, header: Header) -> list:
    """解析关节段（6DOF 弹簧约束）。rigid A/B 使用 rigid_index_size。"""
    out: list = []
    for _ in range(_count(r, "关节", 100_000)):
        name_jp = r.text(header.encoding)
        name_en = r.text(header.encoding)
        jtype = r.u8()
        rigid_a = r.index(header.rigid_index_size, True)
        rigid_b = r.index(header.rigid_index_size, True)
        position = r.vec3()
        rotation = r.vec3()
        pos_min = r.vec3()
        pos_max = r.vec3()
        rot_min = r.vec3()
        rot_max = r.vec3()
        pos_spring = r.vec3()
        rot_spring = r.vec3()
        out.append(Joint(name_jp, name_en, jtype, rigid_a, rigid_b, position,
                        rotation, pos_min, pos_max, rot_min, rot_max,
                        pos_spring, rot_spring))
    return out


def _validate_bone_hierarchy(bones: list[Bone]) -> None:
    count = len(bones)
    for i, bone in enumerate(bones):
        for label, idx in (("parent", bone.parent_index),
                           ("tail", bone.tail_index),
                           ("inherit", bone.inherit_parent_index),
                           ("ik_target", bone.ik_target_index)):
            if idx != -1 and not (0 <= idx < count):
                raise PMXError(f"骨骼「{bone.name}」的 {label}_index={idx} 越界")
        # 父链环检测
        if bone.parent_index != -1:
            seen = {i}
            cur = bone.parent_index
            while cur != -1:
                if cur in seen:
                    raise PMXError(f"骨骼「{bone.name}」的父链存在环（节点 {cur}）")
                if not (0 <= cur < count):
                    break
                seen.add(cur)
                cur = bones[cur].parent_index

from __future__ import annotations

import argparse
from pathlib import Path
import struct
import zlib


def text(value: str) -> bytes:
    raw = value.encode("utf-8")
    return struct.pack("<i", len(raw)) + raw


def write_png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    width = height = 2
    rows = [
        b"\x00" + b"\xff\x00\x00\xff" + b"\xff\xff\xff\xff",
        b"\x00" + b"\xff\xff\xff\xff" + b"\x00\x00\xff\xff",
    ]

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(b"".join(rows)))
    png += chunk(b"IEND", b"")
    path.write_bytes(png)


def write_bone(data: bytearray, b: dict) -> None:
    """写一根 PMX 骨骼记录（bone_index_size=1 字节）。flags 控制可选块。"""
    name = b["name"]
    pos = b["pos"]
    parent = b["parent"]
    flags = b.get("flags", 0x0002)
    data += text(name) + text(name + "_en")
    data += struct.pack("<3fbih", *pos, parent, 0, flags)
    if flags & 0x0001:                       # 尾指向骨骼
        data += struct.pack("<b", -1)
    else:                                   # 尾为偏移量
        data += struct.pack("<3f", 0, 0.5, 0)
    if flags & 0x0300:                      # 附加旋转/平移
        data += struct.pack("<bf", -1, 1.0)
    if flags & 0x0400:                      # 轴固定化：规范里只有 1 个 vec3
        data += struct.pack("<3f", *b.get("fixed_axis", (1, 0, 0)))
    if flags & 0x0800:                      # ローカル軸信息：局部 X + 局部 Z 各 1 个 vec3
        data += struct.pack("<3f", *b.get("local_x", (1, 0, 0)))
        data += struct.pack("<3f", *b.get("local_z", (0, 0, 1)))
    if flags & 0x2000:                      # 外部亲
        data += struct.pack("<i", 0)
    if flags & 0x0020:                      # IK
        ik = b.get("ik", {"target": -1, "iter": 10, "angle": 1.0, "links": []})
        data += struct.pack("<bi", ik["target"], ik["iter"])
        data += struct.pack("<f", ik["angle"])
        data += struct.pack("<i", len(ik["links"]))
        for link in ik["links"]:
            data += struct.pack("<b", link.get("bone", -1))
            if link.get("limited"):
                data += struct.pack("<3f", *link["min"]) + struct.pack("<3f", *link["max"])
            else:
                data += struct.pack("<b", 0)


def create_sample(directory: Path, extra_bones: list | None = None,
                  corrupt: dict | None = None) -> Path:
    """生成合成样本 PMX。

    `extra_bones` 追加在标准 23+2 根骨**之后**：默认 None ⇒ 字节输出与既有测试
    断言的序号完全一致（顶点权重、刚体、显示枠引用的都是前面的固定序号）。
    只有需要覆盖可选骨段（轴固定化 / 局部轴）的用例才传它。

    `corrupt` 是解析器负例的开关：默认 None ⇒ 字节同样一字不变；给了就只把指到的那一个
    字段换成非法值。为什么要走「生成」而不是「事后按字节改」：PMX 是小端二进制，
    `1.0f` 这种字节串在文件里出现几十次，事后替换会改到别处去 —— 那测试证的就不再是
    「这个守卫接在读取路径上」。支持的键：`uv`（换 0 号顶点的 UV）、`bone_name`（换 0 号骨的
    日文名）、`morph_name`、`morph_offset`、`rigid`（并入第 1 个刚体）、`joint`（并入唯一那个关节）。
    """
    corrupt = corrupt or {}
    directory.mkdir(parents=True, exist_ok=True)
    write_png(directory / "textures" / "checker.png")
    # 原始 23 根标准骨（保持名称/序号不变，供既有测试断言）
    bones = [
        {"name": "センター", "pos": (0, 8, 0), "parent": -1},
        {"name": "下半身", "pos": (0, 7.5, 0), "parent": 0},
        {"name": "上半身", "pos": (0, 9, 0), "parent": 0},
        {"name": "上半身2", "pos": (0, 11, 0), "parent": 2},
        {"name": "首", "pos": (0, 13, 0), "parent": 3},
        {"name": "頭", "pos": (0, 14, 0), "parent": 4},
        {"name": "左肩", "pos": (1, 12.5, 0), "parent": 3},
        {"name": "左腕", "pos": (2, 12.5, 0), "parent": 6},
        {"name": "左ひじ", "pos": (4, 12.5, 0), "parent": 7},
        {"name": "左手首", "pos": (6, 12.5, 0), "parent": 8},
        {"name": "右肩", "pos": (-1, 12.5, 0), "parent": 3},
        {"name": "右腕", "pos": (-2, 12.5, 0), "parent": 10},
        {"name": "右ひじ", "pos": (-4, 12.5, 0), "parent": 11},
        {"name": "右手首", "pos": (-6, 12.5, 0), "parent": 12},
        {"name": "左足", "pos": (1, 7, 0), "parent": 1},
        {"name": "左ひざ", "pos": (1, 4, 0), "parent": 14},
        {"name": "左足首", "pos": (1, 1, 0), "parent": 15},
        # MMD 角色朝 -Z，所以「つま先」必须在 -Z（脚的前方）；+X = 角色自身的左侧。
        # 这两条一起决定 semantic.measure_forward / _lr_is_mirrored 的结果，
        # 旧样本把脚尖写成 +Z（朝后），会让左右自检在正确手性下误报反了。
        {"name": "左つま先", "pos": (1, 0.2, -1), "parent": 16},
        {"name": "右足", "pos": (-1, 7, 0), "parent": 1},
        {"name": "右ひざ", "pos": (-1, 4, 0), "parent": 18},
        {"name": "右足首", "pos": (-1, 1, 0), "parent": 19},
        {"name": "右つま先", "pos": (-1, 0.2, -1), "parent": 20},
        {"name": "髪_辅助", "pos": (0, 14, 1), "parent": 5},
        # 追加：用于骨骼分类测试的无用/控制骨
        {"name": "左足IK", "pos": (1, 1, 1), "parent": 15,
         "flags": 0x0002 | 0x0020,
         "ik": {"target": 18, "iter": 10, "angle": 1.0, "links": []}},
        {"name": "物理用ダミー", "pos": (0, 14, 1), "parent": 5},
    ]
    vertices = [
        ((-2, 8, 0), (0, 0)),
        ((2, 8, 0), (1, 0)),
        ((2, 12, 0), (1, 1)),
        ((-2, 12, 0), (0, 1)),
    ]
    if corrupt.get("uv"):
        vertices[0] = (vertices[0][0], corrupt["uv"])
    if corrupt.get("bone_name"):
        bones[0]["name"] = corrupt["bone_name"]
    bones = bones + list(extra_bones or [])
    data = bytearray(b"PMX ")
    data += struct.pack("<fB", 2.0, 8)
    data += bytes((1, 0, 1, 1, 1, 1, 1, 1))
    for value in ("MMD2Max Test", "MMD2Max Test", "Synthetic test asset", "Synthetic test asset"):
        data += text(value)
    data += struct.pack("<i", len(vertices))
    for index, (position, uv) in enumerate(vertices):
        data += struct.pack("<8f", *position, 0, 0, 1, *uv)
        data += struct.pack("<Bbb", 1, 0, 2)
        data += struct.pack("<f", 0.5)
        data += struct.pack("<f", 1.0)
    data += struct.pack("<i", 6)
    data += bytes((0, 2, 1, 0, 3, 2))
    data += struct.pack("<i", 1) + text("textures\\checker.png")
    data += struct.pack("<i", 1)
    data += text("Test Material") + text("Test Material")
    data += struct.pack("<4f", 1, 1, 1, 1)
    data += struct.pack("<3ff3f", 0.2, 0.2, 0.2, 8, 0.5, 0.5, 0.5)
    data += struct.pack("<B4ff", 1, 0, 0, 0, 1, 0)
    data += struct.pack("<bbBB", 0, -1, 0, 1)
    data += struct.pack("<B", 1)
    data += text("") + struct.pack("<i", 6)
    data += struct.pack("<i", len(bones))
    for b in bones:
        write_bone(data, b)

    # ---- 变形段：1 个顶点 morph（顶点0 偏移），用于 Morpher 测试 ----
    data += struct.pack("<i", 1)
    data += text(corrupt.get("morph_name", "あ")) + text("a")
    data += struct.pack("<BBi", 2, 1, 1)          # panel=2(眼), type=1(顶点), count=1
    data += struct.pack("<b3f", 0, *corrupt.get("morph_offset", (0.1, 0.2, 0.3)))
    # 顶点索引(1字节)=0, 偏移 vec3

    # ---- 显示枠段：2 个特殊帧（Root / 表情）----
    data += struct.pack("<i", 2)
    data += text("Root") + text("Root")
    data += struct.pack("<B", 1)                 # special
    data += struct.pack("<i", 1)
    data += struct.pack("<Bb", 0, 0)             # type=0 骨骼, index=0
    data += text("表情") + text("表情")
    data += struct.pack("<B", 1)
    data += struct.pack("<i", 1)
    data += struct.pack("<Bb", 1, 0)             # type=1 变形, index=0

    # ---- 刚体段：2 个（盒+球），用于物理刚体导入测试 ----
    data += struct.pack("<i", 2)
    rigids = [
        {"name": "物理剛体_足", "bone": 17, "group": 1, "nocollide": 0x0001,
         "shape": 1, "size": (0.5, 0.3, 0.5), "pos": (1.0, 1.0, 0.0),
         "rot": (0.0, 0.0, 0.0), "mass": 1.0, "mdamp": 0.5, "rdamp": 0.5,
         "rep": 0.0, "fric": 0.5, "mode": 0},
        {"name": "物理剛体_髪", "bone": 5, "group": 2, "nocollide": 0x0002,
         "shape": 0, "size": (0.3, 0.3, 0.3), "pos": (0.0, 14.0, 1.0),
         "rot": (0.0, 0.0, 0.0), "mass": 0.5, "mdamp": 0.5, "rdamp": 0.5,
         "rep": 0.0, "fric": 0.5, "mode": 2},
    ]
    rigids[0].update(corrupt.get("rigid", {}))
    for rb in rigids:
        data += text(rb["name"]) + text(rb["name"] + "_en")
        data += struct.pack("<b", rb["bone"])          # bone_index(1字节)
        data += struct.pack("<B", rb["group"])         # group
        data += struct.pack("<H", rb["nocollide"])     # 非衝突グループ uint16
        data += struct.pack("<B", rb["shape"])         # shape
        data += struct.pack("<3f", *rb["size"])
        data += struct.pack("<3f", *rb["pos"])
        data += struct.pack("<3f", *rb["rot"])
        data += struct.pack("<f", rb["mass"])
        data += struct.pack("<f", rb["mdamp"])
        data += struct.pack("<f", rb["rdamp"])
        data += struct.pack("<f", rb["rep"])
        data += struct.pack("<f", rb["fric"])
        data += struct.pack("<B", rb["mode"])          # physics_mode
        # PMX 2.0 无 reserved 字节（解析器仅 2.1+ 读 1 字节）

    # ---- 关节段：1 个 6DOF 弹簧，连接两个刚体 ----
    data += struct.pack("<i", 1)
    jt = {"name": "関節_足髪", "a": 0, "b": 1,
          "pos": (0.5, 7.5, 0.5), "rot": (0, 0, 0),
          "pmin": (-1, -1, -1), "pmax": (1, 1, 1),
          "rmin": (-0.5, -0.5, -0.5), "rmax": (0.5, 0.5, 0.5),
          "pspring": (0, 0, 0), "rspring": (0, 0, 0)}
    jt.update(corrupt.get("joint", {}))
    data += text(jt["name"]) + text(jt["name"] + "_en")
    data += struct.pack("<B", 0)                  # type=0 spring 6DOF
    data += struct.pack("<bb", jt["a"], jt["b"])  # rigid A/B (1字节)
    data += struct.pack("<3f", *jt["pos"])
    data += struct.pack("<3f", *jt["rot"])
    data += struct.pack("<3f", *jt["pmin"])
    data += struct.pack("<3f", *jt["pmax"])
    data += struct.pack("<3f", *jt["rmin"])
    data += struct.pack("<3f", *jt["rmax"])
    data += struct.pack("<3f", *jt["pspring"])
    data += struct.pack("<3f", *jt["rspring"])

    path = directory / "sample.pmx"
    path.write_bytes(data)
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(create_sample(args.output))

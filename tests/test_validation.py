"""锁定 pmx.py 的边界校验（审查项 P0#9 / P0#10）。

通过手工拼一个最小 PMX 字节流，再分别注入非法字段，确认 load_pmx 会抛 PMXError。
"""
import struct
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mmd2max.pmx import PMXError, load_pmx  # noqa: E402

ENC = 1  # UTF-8


def _t(s: str) -> bytes:
    b = s.encode("utf-8")
    return struct.pack("<i", len(b)) + b


def build_pmx(face_indices, mat_index_count, vtx_bone, bone_names,
              bone_morph_rot=None):
    """拼一个最小 PMX。

    bone_morph_rot：额外写一个「骨骼变形」段，用于锁定骨 morph 的字段宽度。
      "vec4" = 规范布局（旋转为四元数）；"vec3" = v2 读错的旧布局；None = 不写。
    """
    out = bytearray()
    out += b"PMX "
    out += struct.pack("<f", 2.1)
    # globals_count=8, then 8 bytes: encoding, add_uvs, vertex/texture/material/
    # bone/morph/rigid index sizes. PMX 要求所有索引宽度必须为 1/2/4。
    out += bytes([8, ENC, 0, 2, 1, 1, 2, 2, 1])  # globals + settings
    out += _t("name"); out += _t("name_en"); out += _t(""); out += _t("")
    # vertices
    out += struct.pack("<i", 1)
    out += struct.pack("<3f", 0.0, 0.0, 0.0)   # pos
    out += struct.pack("<3f", 0.0, 0.0, 1.0)   # normal
    out += struct.pack("<2f", 0.0, 0.0)        # uv
    out += bytes([0])                            # BDEF1
    out += struct.pack("<h", vtx_bone)          # bone index (2 bytes signed)
    out += struct.pack("<f", 1.0)              # edge
    # faces
    out += struct.pack("<i", len(face_indices))
    for fi in face_indices:
        out += struct.pack("<H", fi & 0xFFFF)  # 2 bytes unsigned
    # textures
    out += struct.pack("<i", 0)
    # materials (1)
    out += struct.pack("<i", 1)
    out += _t("mat"); out += _t("mat_en")
    out += struct.pack("<4f", 1, 1, 1, 1)
    out += struct.pack("<3f", 0, 0, 0)
    out += struct.pack("<f", 1.0)
    out += struct.pack("<3f", 0, 0, 0)
    out += bytes([0])                  # draw flags
    out += struct.pack("<4f", 0, 0, 0, 1)
    out += struct.pack("<f", 1.0)
    out += struct.pack("<b", -1)       # texture index
    out += struct.pack("<b", -1)       # sphere index
    out += bytes([0])                  # sphere mode
    out += bytes([0])                  # toon share flag
    out += struct.pack("<b", -1)       # toon index
    out += _t("")
    out += struct.pack("<i", mat_index_count)
    # bones
    out += struct.pack("<i", len(bone_names))
    for name in bone_names:
        out += _t(name); out += _t(name + "_en")
        out += struct.pack("<3f", 0, 0, 0)
        out += struct.pack("<h", -1)   # parent
        out += struct.pack("<i", 0)    # layer
        out += struct.pack("<H", 0)    # flags
        out += struct.pack("<3f", 0, 0, 0)  # flags&0x0001==0 -> tail as offset
    # 段顺序：骨骼 → 变形 → 显示枠 → 刚体 → 关节
    if bone_morph_rot is None:
        out += struct.pack("<i", 0)                 # 变形数 0
    else:
        out += struct.pack("<i", 1)                 # 1 个变形
        out += _t("表情骨"); out += _t("bm_en")
        out += bytes([1])                            # panel
        out += bytes([2])                            # type=2 bone
        out += struct.pack("<i", 1)                 # 1 个偏移项
        out += struct.pack("<h", 0)                 # bone_index（2 字节有符号）
        out += struct.pack("<3f", 1.0, 2.0, 3.0)    # 位移 vec3
        if bone_morph_rot == "vec4":
            out += struct.pack("<4f", 0.0, 0.0, 0.0, 1.0)   # 旋转 = 四元数
        else:
            out += struct.pack("<3f", 0.0, 0.0, 0.0)        # 旋转 = vec3（旧/错误布局）
    for _ in range(3):
        out += struct.pack("<i", 0)                 # 显示枠 / 刚体 / 关节 均空
    return bytes(out)


def _load(data: bytes):
    with tempfile.NamedTemporaryFile(suffix=".pmx", delete=False) as f:
        f.write(data)
        path = Path(f.name)
    try:
        return load_pmx(path)
    finally:
        path.unlink(missing_ok=True)


class ValidationTests(unittest.TestCase):

    def test_valid_minimal_loads(self):
        m = _load(build_pmx([0, 0, 0], 3, 0, ["Root"]))
        self.assertIsNotNone(m)
        self.assertEqual(len(m.vertices), 1)
        self.assertEqual(len(m.bones), 1)

    def test_negative_face_index_rejected(self):
        with self.assertRaises(PMXError):
            _load(build_pmx([0, 0, -1], 3, 0, ["Root"]))

    def test_material_index_sum_mismatch_rejected(self):
        with self.assertRaises(PMXError):
            _load(build_pmx([0, 0, 0], 6, 0, ["Root"]))

    def test_vertex_bone_out_of_range_rejected(self):
        with self.assertRaises(PMXError):
            _load(build_pmx([0, 0, 0], 3, 5, ["Root"]))

    def test_duplicate_bone_name_rejected(self):
        with self.assertRaises(PMXError):
            _load(build_pmx([0, 0, 0], 3, 0, ["Root", "Root"]))

    def test_bone_morph_rotation_is_vec4(self):
        """骨 morph 的旋转是四元数（vec4），不是欧拉角。

        v2 按 vec3 读，每项少读 4 字节，导致显示枠/刚体/关节全部错位 —— 香菱
        就是在关节段炸掉的。这里用「规范布局能落到文件末尾」来锁死宽度。
        """
        m = _load(build_pmx([0, 0, 0], 3, 0, ["Root"], bone_morph_rot="vec4"))
        self.assertEqual(len(m.morphs), 1)
        morph = m.morphs[0]
        self.assertEqual(morph.morph_type, 2)
        bindex, offset, rot = morph.items[0]
        self.assertEqual(bindex, 0)
        self.assertEqual(offset, (1.0, 2.0, 3.0))
        self.assertEqual(len(rot), 4, "骨 morph 旋转应为 4 元组 (x,y,z,w)")
        self.assertAlmostEqual(rot[3], 1.0, places=6)

    def test_bone_morph_vec3_layout_rejected(self):
        """按 vec3 写的骨 morph 必须报错，而不是错位读完还假装成功。"""
        with self.assertRaises(PMXError):
            _load(build_pmx([0, 0, 0], 3, 0, ["Root"], bone_morph_rot="vec3"))

    def test_trailing_garbage_rejected(self):
        """末尾不变式：多一个字节也必须报错，不能假装解析成功。"""
        data = build_pmx([0, 0, 0], 3, 0, ["Root"], bone_morph_rot="vec4") + b"\x00"
        with self.assertRaises(PMXError):
            _load(data)


if __name__ == "__main__":
    unittest.main()

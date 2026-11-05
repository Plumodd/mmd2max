import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from mmd2max.pmx import PMXError, load_pmx  # noqa: E402
from tests.make_sample import create_sample  # noqa: E402


class PMXParserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.sample = create_sample(self.directory)

    def tearDown(self):
        self.temp.cleanup()

    def test_loads_mesh_bones_and_texture_references(self):
        model = load_pmx(self.sample)
        self.assertEqual(len(model.vertices), 4)
        self.assertEqual(len(model.faces), 2)
        self.assertEqual(len(model.bones), 25)
        self.assertEqual(len(model.materials), 1)
        self.assertEqual(model.textures, ["textures\\checker.png"])
        self.assertTrue((self.directory / "textures" / "checker.png").is_file())

    def test_skin_weights_are_preserved(self):
        model = load_pmx(self.sample)
        first = model.vertices[0]
        self.assertEqual(first.deform_type, "BDEF2")
        self.assertEqual(len(first.weights), 2)
        self.assertGreater(first.weights[0][1], 0.0)
        self.assertGreater(first.weights[1][1], 0.0)
        self.assertEqual(first.weights[0][0], 0)
        self.assertEqual(first.weights[1][0], 2)

    def test_bone_hierarchy_is_preserved(self):
        model = load_pmx(self.sample)
        by_name = {bone.name: bone for bone in model.bones}
        self.assertEqual(by_name["センター"].parent_index, -1)
        self.assertIn("下半身", by_name)
        self.assertIn("右つま先", by_name)

    def test_rejects_non_pmx_header(self):
        bad = self.directory / "bad.pmx"
        bad.write_bytes(b"PMD " + b"\x00" * 32)
        with self.assertRaises(PMXError):
            load_pmx(bad)

    def test_parses_morph_rigid_and_joint(self):
        model = load_pmx(self.sample)
        # 1 个顶点 morph
        self.assertEqual(len(model.morphs), 1)
        m = model.morphs[0]
        self.assertEqual(m.name, "あ")
        self.assertEqual(m.morph_type, 1)          # vertex morph
        self.assertEqual(len(m.items), 1)
        vidx, offset = m.items[0]
        self.assertEqual(vidx, 0)
        self.assertAlmostEqual(offset[0], 0.1)
        # 2 个刚体
        self.assertEqual(len(model.rigid_bodies), 2)
        rb0 = model.rigid_bodies[0]
        self.assertEqual(rb0.shape, 1)
        self.assertEqual(rb0.nocollide_mask, 0x0001)
        self.assertEqual(rb0.bone_index, 17)
        rb1 = model.rigid_bodies[1]
        self.assertEqual(rb1.shape, 0)
        self.assertEqual(rb1.physics_mode, 2)
        # 1 个关节
        self.assertEqual(len(model.joints), 1)
        jt = model.joints[0]
        self.assertEqual(jt.rigid_a, 0)
        self.assertEqual(jt.rigid_b, 1)
        self.assertAlmostEqual(jt.pos_min[0], -1.0)

    def test_ik_and_dummy_bones_parsed(self):
        model = load_pmx(self.sample)
        by_name = {b.name: b for b in model.bones}
        self.assertIn("左足IK", by_name)
        self.assertTrue(by_name["左足IK"].flags & 0x0020)   # IK 标志
        self.assertIn("物理用ダミー", by_name)


class PmxGuardTests(unittest.TestCase):
    """每个数值/名字守卫都要有一条**真解析**的负例。

    上一版 `load_pmx` 里有一段 `if any(not _finite(...)): pass` —— 检查写好了、条件也算了、
    什么都没做，于是「材质有 NaN」这条在所有测试里都是绿的（它只被材质段的另一个 raise 覆盖到
    三个字段，specular_power 与后面全部段的浮点都没人管）。这类守卫只有从文件读进来才能证明
    它接在路径上：单测 `_all_finite` 本身等于什么都没测。
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _load(self, **corrupt):
        return load_pmx(create_sample(self.directory, corrupt=corrupt))

    def test_nan_vertex_uv_is_rejected(self):
        nan = float("nan")
        with self.assertRaises(PMXError) as caught:
            self._load(uv=(nan, nan))
        self.assertIn("UV", str(caught.exception))

    def test_nan_morph_offset_is_rejected(self):
        """表情偏移进 Morpher 后是逐通道加法，NaN 会让整个脸部变白且不报错。"""
        with self.assertRaises(PMXError) as caught:
            self._load(morph_offset=(0.1, float("inf"), 0.3))
        self.assertIn("变形「あ」", str(caught.exception))

    def test_nan_rigid_parameter_is_rejected(self):
        with self.assertRaises(PMXError) as caught:
            self._load(rigid={"mass": float("nan")})
        self.assertIn("物理剛体_足", str(caught.exception))

    def test_nan_joint_limit_is_rejected(self):
        with self.assertRaises(PMXError) as caught:
            self._load(joint={"pmax": (1, 1, float("nan"))})
        self.assertIn("関節_足髪", str(caught.exception))

    def test_out_of_range_joint_rigid_is_rejected(self):
        """越界的刚体下标 = Max 那边建弹簧时拿到不存在的节点，物理链静默少一环。"""
        with self.assertRaises(PMXError) as caught:
            self._load(joint={"b": 9})
        self.assertIn("越界", str(caught.exception))

    def test_tab_in_a_bone_name_is_rejected(self):
        """骨名是 bones.tsv 的第 2 列：带制表符 = 那一行往后所有列都错位一格。"""
        with self.assertRaises(PMXError) as caught:
            self._load(bone_name="センター\t下半身")
        self.assertIn("控制字符", str(caught.exception))

    def test_newline_in_a_morph_name_is_rejected(self):
        """换行更糟：morphs.tsv 里凭空多出一行，行数以 `#` 统计行核对才抓得到。"""
        with self.assertRaises(PMXError) as caught:
            self._load(morph_name="a\nb")
        self.assertIn("控制字符", str(caught.exception))

    def test_the_untouched_sample_still_parses(self):
        """反向钉子：`corrupt=None` 的字节必须与样例夹具原本完全一致，否则上面七条测的是别的文件。"""
        model = load_pmx(create_sample(self.directory))
        self.assertEqual(len(model.bones), 25)
        self.assertEqual(model.morphs[0].name, "あ")
        self.assertEqual([rb.name for rb in model.rigid_bodies],
                         ["物理剛体_足", "物理剛体_髪"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

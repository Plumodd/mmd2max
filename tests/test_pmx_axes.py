"""#57：PMX 的轴固定化 / 局部轴 —— 骨朝向的第二只眼睛（v4 规则 I 的伪-真值表）。

三层断言，可信度从低到高：
  1. 字节宽度：0x0400 读 1 个 vec3、0x0800 读 2 个 vec3。写错一个宽度，后面的段整体
     错位，`load_pmx` 的「必须正好停在文件末尾」会当场抛 —— 所以本层用合成样本，
     不必真去比数值。
  2. 值捕获：读回来的轴与写进去的逐分量一致，Y = Z × X。
  3. 真实模型：作者手填的转轴，与我们**只按 pivot 反推**的骨长度轴是否对齐。
     这一层才是本任务的目的 —— 两个数字没有任何共同计算，对上才算数；
     #26/#40 那种「判据和写入同源」的红绿灯，只有第二条独立来源才治得住。

顺带钉死一条历史事故：旧版 `make_sample.write_bone` 把 0x0400 写成 2 个 vec3、
0x0800 一个都不写（正好与规范相反）。当时没有任何样本骨带这两个 flag，
所以这条 bug 一直睡着 —— 与本文件第 1 层断言一起，从此醒着。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mmd2max.pmx import load_pmx                          # noqa: E402
from tests.make_sample import create_sample               # noqa: E402

import axis_audit                                         # noqa: E402

XIANGLING = Path(os.environ.get("MMD2MAX_TEST_PMX_ROLL",
                                r"C:\Users\Administrator\Desktop\香菱\香菱.pmx"))

# 追加在样本 25 根标准骨之后：父级 7=左腕、9=左手首，序号引用全部合法。
AXIS_BONES = [
    {"name": "测试_腕捩", "pos": (3.0, 12.5, 0.0), "parent": 7,
     "flags": 0x0002 | 0x0400, "fixed_axis": (0.0, 1.0, 0.0)},
    {"name": "测试_指节", "pos": (7.0, 12.5, 0.0), "parent": 9,
     "flags": 0x0002 | 0x0400 | 0x0800,
     "fixed_axis": (1.0, 0.0, 0.0),
     "local_x": (0.0, 1.0, 0.0), "local_z": (0.0, 0.0, 1.0)},
    # 不带任何轴 flag 的哨兵骨：宽度一错，它的名称/坐标就会被读成隔壁列
    {"name": "测试_哨兵", "pos": (-3.0, 12.5, 0.0), "parent": 11},
]


class AxisBytesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _bones(self):
        path = create_sample(self.root, extra_bones=AXIS_BONES)
        return load_pmx(path).bones

    def test_axis_flags_parse(self):
        bones = self._bones()
        self.assertEqual(len(bones), 28, "两根带轴骨 + 一根哨兵骨都要落位")
        twist, finger, sentinel = bones[25], bones[26], bones[27]
        self.assertEqual(twist.name, "测试_腕捩")
        self.assertEqual(finger.name, "测试_指节")
        self.assertEqual(sentinel.name, "测试_哨兵")
        self.assertEqual(sentinel.position, (-3.0, 12.5, 0.0))
        self.assertEqual(sentinel.parent_index, 11)

    def test_declared_axes_are_kept_not_thrown_away(self):
        bones = self._bones()
        self.assertEqual(bones[25].fixed_axis, (0.0, 1.0, 0.0))
        self.assertIsNone(bones[25].local_x, "没设 0x0800 就不许凭空造出局部轴")
        self.assertFalse(bones[25].has_local_axis)
        self.assertEqual(bones[26].local_x, (0.0, 1.0, 0.0))
        self.assertEqual(bones[26].local_z, (0.0, 0.0, 1.0))
        self.assertTrue(bones[26].has_local_axis)
        self.assertIsNone(bones[27].fixed_axis)

    def test_local_y_is_cross_of_z_and_x(self):
        """PMX 只存 X、Z，Y 由 Z × X 得到（与 mmx / PmxEditor 同一约定）。"""
        bone = self._bones()[26]
        self.assertEqual(bone.local_y(), (-1.0, 0.0, 0.0))


@unittest.skipUnless(XIANGLING.is_file(), f"缺少真实模型：{XIANGLING}")
class RealModelAxisTests(unittest.TestCase):
    """这条是 #57 的全部理由：作者填的转轴 ≈ 我们反推的骨方向，且两者互不知情。"""

    @classmethod
    def setUpClass(cls):
        cls.result = axis_audit.audit_model(XIANGLING)

    def test_fixed_axis_count_is_an_observed_fact(self):
        self.assertEqual(self.result["fixed_count"], 4,
                         "香菱只有 4 根轴固定化骨；数量变了说明解析或模型变了，"
                         "判据的覆盖面要重新声明，不许悄悄扩大")
        self.assertEqual(self.result["local_count"], 0,
                         "香菱没有局部轴：手臂链的独立真值只剩轴固定化这一条腿，"
                         "腿部/脊柱没有第二来源 —— 别把判据写成「全场都有」")

    def test_declared_twist_axes_agree_with_derived_geometry(self):
        for row in self.result["rows"]:
            if not row["fixed"]:
                continue
            best = row["best"]
            self.assertIsNotNone(best, f"{row['name']} 声明了转轴却无骨可比")
            self.assertLess(best[1], 3.0,
                            f"{row['name']} 的轴固定化方向与「{best[0]}」的几何长度轴差 "
                            f"{best[1]:.2f} 度：两条独立来源对不上，"
                            f"要么解析错了，要么我们的骨方向本来就错（#26/#40 那一类）")

    def test_fixed_axis_bones_are_the_twist_bones(self):
        names = {row["name"] for row in self.result["rows"] if row["fixed"]}
        self.assertEqual(names, {"左腕捩", "左手捩", "右腕捩", "右手捩"},
                         "带转轴的只有上臂/前臂的扭转骨 ⇒ 独立判据覆盖手臂链；"
                         "腿与脊柱仍需别的办法（#36 的大腿扭转没有第二来源）")


if __name__ == "__main__":
    unittest.main()

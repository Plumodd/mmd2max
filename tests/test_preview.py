"""预览层（project / build_preview）的契约测试。

GUI 里「不开 Max 就能看出手性对不对」这句话，只有这两个纯函数算对时才成立。
用合成小样本钉死三视图规则：左右落在屏幕哪边、竖直方向朝上、脚尖在顶视图朝上、
改挂连线的终点必须是 Biped 节点。这些一旦反了，画布就是一张「看着很像」的错图。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

import gui                                    # noqa: E402
from mmd2max import export_obj                # noqa: E402
from tests import spec_table                  # noqa: E402
from tests.make_sample import create_sample   # noqa: E402

SCALE = 8.763


class ProjectionTests(unittest.TestCase):
    """三视图的坐标规则，逐条对着 coords.py 的手性约定核。"""

    def test_front_view_keeps_x_right_and_z_up(self):
        self.assertEqual(gui.project((2.0, 3.0, 4.0), "正视"), (2.0, 4.0))

    def test_top_view_screen_up_is_minus_y(self):
        # Max Top 视图从 +Z 往下看，屏幕上方是 -Y
        self.assertEqual(gui.project((2.0, 3.0, 4.0), "顶视"), (2.0, -3.0))

    def test_side_view_uses_y_as_horizontal(self):
        self.assertEqual(gui.project((2.0, 3.0, 4.0), "侧视"), (3.0, 4.0))

    def test_every_view_is_named(self):
        for view in gui.VIEWS:
            self.assertIsInstance(gui.project((1.0, 2.0, 3.0), view), tuple)


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.pmx = create_sample(Path(self.temp.name) / "src")
        self.addCleanup(self.temp.cleanup)

    def _preview(self, **kwargs):
        plan = export_obj.build_plan(self.pmx, scale=SCALE, **kwargs)
        return plan, gui.build_preview(plan)

    def _bone(self, preview, plan, name):
        """骨杆起点，Max 世界坐标 (X, Y, Z)：X=屏幕右、Y=前后、Z=高。"""
        return preview["heads"][plan.bone_names.index(name)]

    def test_one_rod_per_kept_bone(self):
        plan, prev = self._preview()
        self.assertEqual(len(prev["heads"]), len(plan.kept))
        self.assertEqual(len(prev["tails"]), len(plan.kept))
        self.assertEqual(len(prev["kinds"]), len(plan.kept))
        self.assertTrue(set(prev["kinds"]) <= set(gui.KIND_COLORS))

    def test_dropped_dots_are_exactly_the_pruned_bones(self):
        plan, prev = self._preview()
        self.assertEqual(len(prev["dropped"]), len(plan.model.bones) - len(plan.kept))
        full, prev_full = self._preview(filter_useless=False)
        self.assertEqual(prev_full["dropped"], [])
        self.assertEqual(len(full.kept), len(full.model.bones))

    def test_rods_come_from_bones_tsv_not_a_second_conversion(self):
        """骨杆端点必须等于 bones.tsv 的 hx..tz：预览与产物同源。

        曾经踩过的坑：plan.heads 已是 Max 世界坐标，预览又换算了一次，
        画出来的骨杆比实际高一个数量级（X 分量恰好被二次交换掩盖，肉眼看不出来）。
        """
        plan, prev = self._preview()
        rows = spec_table.table(spec_table.join(plan.bones_lines))
        self.assertEqual(len(rows), len(prev["heads"]))
        for i, row in enumerate(rows):
            head = (float(row["head_x"]), float(row["head_y"]), float(row["head_z"]))
            tail = (float(row["tail_x"]), float(row["tail_y"]), float(row["tail_z"]))
            for col, value in zip(head, prev["heads"][i]):
                self.assertAlmostEqual(col, value, places=5,
                                       msg=f"{row['bone_name']} head {head} != {prev['heads'][i]}")
            for col, value in zip(tail, prev["tails"][i]):
                self.assertAlmostEqual(col, value, places=5, msg=f"{row['bone_name']} tail")

    def test_left_hand_bones_land_on_the_screen_right(self):
        """det=-1：MMD 的 +X（角色自身的左）在 Max 正视图仍是屏幕右，骨名不互换。"""
        plan, prev = self._preview()
        seen = 0
        for name, head in zip(plan.bone_names, prev["heads"]):
            if name.startswith("左"):
                self.assertGreater(head[0], 0, name)
                seen += 1
            elif name.startswith("右"):
                self.assertLess(head[0], 0, name)
                seen += 1
        self.assertGreaterEqual(seen, 8, "样本里没有左右骨，这条测试会变成空跑")

    def test_vertical_axis_points_up(self):
        plan, prev = self._preview()
        head = self._bone(prev, plan, "頭")[2]
        checked = 0
        for name in ("センター", "下半身", "左足首", "右足首"):
            if name in plan.bone_names:
                self.assertGreater(head, self._bone(prev, plan, name)[2], name)
                checked += 1
        self.assertGreater(checked, 0, "样本里没有可对照的下半身骨")

    def test_toe_faces_up_in_top_view(self):
        """MMD 角色朝 -Z，换算后脚尖在顶视图应落在脚腕「上方」（屏幕 -Y 侧）。"""
        plan, prev = self._preview()
        front = gui.project(self._bone(prev, plan, "左つま先"), "顶视")[1]
        ankle = gui.project(self._bone(prev, plan, "左足首"), "顶视")[1]
        self.assertGreater(front, ankle)

    def test_reparent_links_end_on_biped_nodes(self):
        plan, prev = self._preview()
        biped_new = {plan.old_to_new[old] for old, target in plan.biped_mapping.items()
                     if target and old in plan.old_to_new}
        self.assertEqual(sum(1 for k in prev["kinds"] if k == "reparent"),
                         plan.reparented)
        for i, target in enumerate(prev["max_parent"]):
            if target >= 0:
                self.assertIn(target, biped_new, plan.bone_names[i])

    def test_hierarchy_policy_draws_no_links(self):
        plan, prev = self._preview(accessory_parent="hierarchy")
        self.assertEqual([c for c in prev["max_parent"] if c >= 0], [])
        self.assertNotIn("reparent", prev["kinds"])

    def test_body_height_uses_all_mesh_vertices(self):
        """角落标的「身高」= 全部网格顶点的竖向跨度，与 auto_scale 的目标一致。"""
        scale = export_obj.auto_scale(self.pmx, 160.0)
        plan = export_obj.build_plan(self.pmx, scale=scale)
        prev = gui.build_preview(plan)
        ys = [p[1] for p in plan.positions]          # MMD 的 Y 就是高度
        self.assertAlmostEqual(prev["body_height"], (max(ys) - min(ys)) * scale,
                               places=6)
        self.assertAlmostEqual(prev["body_height"], 160.0, delta=0.5)
        # 本合成样本整张网格压在同一平面上，厚度自然为 0
        self.assertEqual(prev["body_depth"], 0.0)


MODEL = Path(os.environ.get("MMD2MAX_TEST_PMX",
                            r"C:\Users\Administrator\Desktop\test2\克雷薇.pmx"))


class RealModelPreviewTests(unittest.TestCase):
    """真模型上再核一遍：合成样本盖不到的重名骨、非平面网格。缺文件自动 skip。"""

    @classmethod
    def setUpClass(cls):
        if not MODEL.is_file():
            raise unittest.SkipTest(f"缺少测试模型：{MODEL}")
        cls.scale = export_obj.auto_scale(MODEL)
        cls.plan = export_obj.build_plan(MODEL, scale=cls.scale)
        cls.prev = gui.build_preview(cls.plan)

    def test_rods_match_tsv_on_real_data(self):
        rows = spec_table.table(spec_table.join(self.plan.bones_lines))
        for i, row in enumerate(rows):
            self.assertAlmostEqual(float(row["head_z"]), self.prev["heads"][i][2], places=5,
                                   msg=f"{row['bone_name']} 的高度与 TSV 不一致")

    def test_mesh_has_depth_and_stands_at_the_target_height(self):
        self.assertGreater(self.prev["body_depth"], 10.0)
        self.assertAlmostEqual(self.prev["body_height"], 160.0, delta=1.0)

    def test_left_and_right_dominantly_split_the_screen(self):
        left = [h[0] for n, h in zip(self.plan.bone_names, self.prev["heads"])
                if "左" in n]
        right = [h[0] for n, h in zip(self.plan.bone_names, self.prev["heads"])
                 if "右" in n]
        self.assertGreater(sum(1 for x in left if x > 0), 5, "带「左」的骨应在屏幕右")
        self.assertGreater(sum(1 for x in right if x < 0), 5, "带「右」的骨应在屏幕左")


if __name__ == "__main__":
    unittest.main(verbosity=2)
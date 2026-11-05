"""肢段长度回归测试（修复「肘/膝跑到模型外、小臂小腿被缩成一丁点」）。

关键背景：PMX 骨骼链里插着扭转骨
    右腕 -> 右腕捩 -> 右ひじ -> 手捩 -> 右手首
如果按「第一个保留后代」取 tail，UpperArm 的尾会落到腕捩（12.37），
而真实上臂长度是 右腕 -> 右ひじ = 20.61。Max 端把 tail 当段长，就会把上臂
缩到 60%、肘被挤到模型外。

所以这里断言两件事：
  1. 有 Biped 映射的骨，tail 必须指向「Biped 链的下一节」，不是扭转骨；
  2. 导出的 limbs.tsv 段长必须等于相邻 Biped 关节 head 的真实距离。
"""
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from mmd2max import export_obj, semantic      # noqa: E402
from mmd2max.coords import convert_vec        # noqa: E402
from mmd2max.pmx import PMXError              # noqa: E402
from tests import spec_table                  # noqa: E402
from tests.make_sample import create_sample  # noqa: E402


def _export(source, outdir, *args, **kw):
    """两段式管线的测试快捷入口：build_plan（纯计算）-> export（落盘）。"""
    return export_obj.export(source, outdir, export_obj.build_plan(source, *args, **kw))


def _limb_rows(text):
    """读 limbs.tsv。`variable_width` 是**声明**不是将就：

    第 10~18 列（foot_len / toe_len / tip_* / toe_w…foot_h）按设计只有 `mode=foot`
    的行才写（写侧在表头下面那行注释里就说了），普通段是 9 列。解析器默认对窄行抛，
    所以这里必须显式承认这表是变宽的 —— 缺的列在 dict 里**不存在**，读它之前得先确定
    这一行到底是不是脚段。
    """
    return spec_table.table(text, variable_width=True)


class _Bone:
    def __init__(self, name, pos, parent, tail_index=-1, tail_offset=(0.0, 0.0, 0.0)):
        self.name = name
        self.name_en = ""
        self.position = pos
        self.parent_index = parent
        self.tail_index = tail_index
        self.tail_offset = tail_offset


class _Model:
    def __init__(self, bones):
        self.bones = bones


def _twist_chain_model():
    """右腕(0) -> 腕捩(12.37) -> ひじ(20.61) -> 手首(36.92)，沿 X 轴排开。

    扭转骨序号比真实关节小，正好复现真实模型里「先遇到的子孙是扭转骨」的情形。
    """
    bones = [
        _Bone("センター", (0.0, 0.0, 0.0), -1),
        _Bone("右腕", (0.0, 0.0, 0.0), 0),
        _Bone("右腕捩", (12.37, 0.0, 0.0), 1),
        _Bone("右ひじ", (20.61, 0.0, 0.0), 2),
        _Bone("右手首", (36.92, 0.0, 0.0), 3),
    ]
    return _Model(bones)


class LimbTailTests(unittest.TestCase):
    def test_tail_skips_twist_bone(self):
        model = _twist_chain_model()
        mapping = {
            1: "Bip001 L UpperArm",
            3: "Bip001 L Forearm",
            4: "Bip001 L Hand",
        }
        kept = [0, 1, 2, 3, 4]
        _kept, old_to_new, _parents, heads, tails = export_obj._build_skeleton_from_kept(
            model, 1.0, kept, mapping)

        i = old_to_new[1]        # 右腕 / UpperArm
        length = ((heads[i][0] - tails[i][0]) ** 2
                  + (heads[i][1] - tails[i][1]) ** 2
                  + (heads[i][2] - tails[i][2]) ** 2) ** 0.5
        self.assertAlmostEqual(length, 20.61, places=3,
                               msg="UpperArm 的 tail 必须指向肘(20.61)，不能指向腕捩(12.37)")

        j = old_to_new[3]        # 右ひじ / Forearm
        length2 = ((heads[j][0] - tails[j][0]) ** 2
                   + (heads[j][1] - tails[j][1]) ** 2
                   + (heads[j][2] - tails[j][2]) ** 2) ** 0.5
        self.assertAlmostEqual(length2, 16.31, places=3,
                               msg="Forearm 的 tail 必须指向手首(16.31)")

    def test_without_mapping_falls_back_to_first_descendant(self):
        """没有 Biped 映射时仍用「最近的保留后代」，不改变既有行为。"""
        model = _twist_chain_model()
        kept = [0, 1, 2, 3, 4]
        _kept, old_to_new, _parents, heads, tails = export_obj._build_skeleton_from_kept(
            model, 1.0, kept, None)
        i = old_to_new[1]
        length = ((heads[i][0] - tails[i][0]) ** 2
                  + (heads[i][1] - tails[i][1]) ** 2
                  + (heads[i][2] - tails[i][2]) ** 2) ** 0.5
        self.assertAlmostEqual(length, 12.37, places=3)


class LimbsTableTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.temp = tempfile.TemporaryDirectory()
        self.d = Path(self.temp.name)
        self.pmx = create_sample(self.d)

    def tearDown(self):
        self.temp.cleanup()

    def test_limbs_table_matches_joint_distance(self):
        info = _export(self.pmx, self.d, scale=8.763, filter_useless=True)
        limbs = (self.d / f"{self.pmx.stem}_limbs.tsv").read_text(encoding="utf-8")
        rows = _limb_rows(limbs)
        table_ = {(r["parent_target"], r["child_target"]): float(r["desired_len"])
                  for r in rows}

        # 样例模型：左腕 x=2 -> 左ひじ x=4，单位 2，scale 8.763 => 17.526
        self.assertIn(("Bip001 L UpperArm", "Bip001 L Forearm"), table_)
        self.assertAlmostEqual(table_[("Bip001 L UpperArm", "Bip001 L Forearm")], 17.526, places=3)
        # 左ひじ x=4 -> 左手首 x=6
        self.assertAlmostEqual(table_[("Bip001 L Forearm", "Bip001 L Hand")], 17.526, places=3)
        self.assertGreaterEqual(info["limb_count"], 10)

    def test_torso_chain_pairs_are_consecutive(self):
        """躯干必须「相邻两级」配对：Pelvis->Spine->Spine1->Neck->Head。

        Biped 的每节可见长度 = 父骨 link 长度，链路必须逐级拟合，
        否则就会出现「Pelvis 与 Spine 之间裂开一条缝」的错位。
        但不允许跨级（Pelvis->Spine1 会把中间那节挤没）。
        """
        _export(self.pmx, self.d, scale=8.763, filter_useless=True)
        rows = _limb_rows(
            (self.d / f"{self.pmx.stem}_limbs.tsv").read_text(encoding="utf-8"))
        order = ["Bip001 Pelvis", "Bip001 Spine", "Bip001 Spine1",
                 "Bip001 Spine2", "Bip001 Neck", "Bip001 Head"]
        # 只保留「本次实际存在」的中轴节点（脊柱节数随模型变化，可能是 2 节）
        present = [t for t in order
                   if any(t == r["parent_target"] or t == r["child_target"] for r in rows)]
        torso = [(r["parent_target"], r["child_target"]) for r in rows
                 if r["parent_target"] in present and r["child_target"] in present]
        self.assertIn(("Bip001 Pelvis", "Bip001 Spine"), torso)
        for parent, child in torso:
            self.assertEqual(present.index(child), present.index(parent) + 1,
                             f"躯干跨级配对 {parent}->{child} 会把中间一节挤没")

    def test_limbs_table_values_are_positive(self):
        _export(self.pmx, self.d, scale=8.763, filter_useless=True)
        limbs = (self.d / f"{self.pmx.stem}_limbs.tsv").read_text(encoding="utf-8")
        self.assertEqual(
            spec_table.header(limbs),
            ["parent_target", "child_target", "desired_len", "mmd_parent", "mmd_child",
             "child_x", "child_y", "child_z", "mode", "foot_len", "toe_len",
             "tip_x", "tip_y", "tip_z", "toe_w", "toe_h", "foot_w", "foot_h"],
            "limbs.tsv 的列名变了：Max 端 mmd2maxReadLimbs 按这些名字取列，必须同步改")
        for row in _limb_rows(limbs):
            self.assertGreater(float(row["desired_len"]), 0.0, f"段长必须为正：{row}")

    def test_every_row_carries_the_child_position_and_mode(self):
        """每行都必须带 child_x/y/z + mode：Max 端的骨盆改写靠这两样算 COM 锚点。

        历史事故：只有脚段补齐这 4 列，普通段写 5 列 —— mmd2maxReadLimbs 的
        childPos 恒为 undefined，骨盆改写静默不触发，髋宽照旧偏 2.23cm，
        日志里却一切「成功」。空字段也不行：filterString 会丢掉空列，索引整体错位。

        mode=leg 是 #36 加的：大腿扭转闭环要靠它认出膝行，而**不靠骨名匹配**
        （mmd2maxChainPriority 是全脚本唯一的骨名分支，再加一处就破了那条契约）。
        """
        _export(self.pmx, self.d, scale=8.763, filter_useless=True)
        rows = _limb_rows(
            (self.d / f"{self.pmx.stem}_limbs.tsv").read_text(encoding="utf-8"))
        self.assertTrue(rows, "肢段表不能是空的")
        for row in rows:
            self.assertIn(row["mode"], ("link", "foot", "leg"), f"mode 列不对：{row}")
            for value in (row["child_x"], row["child_y"], row["child_z"]):
                float(value)  # 必须是数字，不能是空串
        by_child = {r["child_target"]: r for r in rows}
        for name in ("Bip001 L Thigh", "Bip001 R Thigh"):
            self.assertIn(name, by_child, f"缺 {name} 行，骨盆锚点算不出来")
        for row in rows:
            if row["parent_target"] == "Bip001 Pelvis":
                self.assertEqual(row["mode"], "link",
                                 "骨盘子段是普通段；改写发生在 Max 端，不在这里改数值")

    def test_only_thigh_rows_are_marked_leg(self):
        """#36：mode=leg 只能落在「大腿→小腿」这一段，且必须两侧都有。

        这一段带的 childPos 就是膝的 PMX 目标 —— 扭转闭环拿它当判据（膝是髋→踝
        之外唯一没被钉死的关节，偏差全在绕轴方位上）。别的段标成 leg 等于让闭环
        去转一根没有扭转自由度的骨。
        """
        _export(self.pmx, self.d, scale=8.763, filter_useless=True)
        rows = [ln.split("\t") for ln in
                (self.d / f"{self.pmx.stem}_limbs.tsv").read_text(encoding="utf-8").splitlines()
                if not ln.startswith("#")]
        legs = [c for c in rows if c[8] == "leg"]
        self.assertEqual({(c[0], c[1]) for c in legs},
                         {("Bip001 L Thigh", "Bip001 L Calf"),
                          ("Bip001 R Thigh", "Bip001 R Calf")},
                         f"leg 标记只给大腿：{[(c[0], c[1]) for c in legs]}")
        for cols in legs:
            self.assertEqual(len(cols), 9, "leg 只给标记，不额外加列")
            knee = float(cols[5]) ** 2 + float(cols[6]) ** 2 + float(cols[7]) ** 2
            self.assertGreater(knee, 0.0, "膝目标不能是原点")
            self.assertAlmostEqual(float(cols[2]), 26.289, places=3,
                                   msg="段长仍是髋→膝的真实距离，没被标记改动")


class FootProfileTests(unittest.TestCase):
    """Toe0 的量法：鞋尖由「踝→脚掌」方向推出，宽高由「脚尖那一段鞋里装得下多少」推出。

    用户要求（#19，两轮反馈）：
      第一轮：「Bip001 L/R Toe0 太大了。要求：Toe0 的 pivot 接在 Bip001 Foot 的终侧
              （link 末端），并且它的宽和高与 Foot 末端一致；长度按图中蓝框
              （贴着脚尖的一小段）」。
      第二轮（照第一轮量到 5.86×4.19，比 3.57 的趾长还高、侧视戳出鞋面）：
              「我要整块骨头呆在鞋子里」。

    上一版的 bug：`_foot_tip` 取「该侧 +y 最大」的顶点当鞋尖，而这个坐标系里
    MMD 角色朝 -Z ⇒ 转成 Max 后**脚掌指向 -y**，+y 最大量到的是脚后跟/裙摆那一片。
    香菱实跑：toe_len=12.28 比整只脚 11.23 还长，Max 端照着把 Toe0 撑成一根大骨头。
    改对后同一模型实测：趾长 3.57。
    """

    SCALE = 10.0
    # PMX 坐标（x=左右、y=上、z=屏幕内；角色朝 -Z）
    ANKLE = (0.8, 0.7, -0.2)
    BALL = (0.8, 0.25, -1.0)

    @classmethod
    def setUpClass(cls):
        """一只楔形「鞋」：从脚掌再向前 0.6 个 PMX 单位（= 6cm），鞋面向前下坡。

        Max 坐标（convert_vec 后）：x∈[5,11]、y 从 -10（脚掌平面）到 -16（鞋尖）、
        鞋底 z=0.5 平、鞋面 z 从 6.0（脚掌上方）降到 4.0（鞋尖上方）。
        脚掌关节 BALL 落在 z=2.5 —— 于是「向下」有 2.0 的余量，「向上」在鞋尖处只剩
        4.2-2.5=1.7（最后一个采样面在 0.9×趾长处，圆头收口不参与），
        整块盒子能装下的最大高度 = 2×1.7 = 3.4，而不是脚掌那一片的 5.5。
        """
        cls.positions = [
            (0.5, 0.05, -1.0), (1.1, 0.05, -1.0),      # 鞋底·后（左/右）
            (0.5, 0.05, -1.6), (1.1, 0.05, -1.6),      # 鞋底·前
            (0.5, 0.60, -1.0), (1.1, 0.60, -1.0),      # 鞋面·后
            (0.5, 0.40, -1.6), (1.1, 0.40, -1.6),      # 鞋面·前
        ]
        cls.faces = [(0, 1, 5), (0, 5, 4),              # 后面（脚掌平面）
                     (2, 3, 7), (2, 7, 6),              # 前面（鞋尖）
                     (0, 1, 3), (0, 3, 2),              # 鞋底
                     (4, 5, 7), (4, 7, 6),              # 鞋面
                     (0, 2, 6), (0, 6, 4),              # 内侧
                     (1, 3, 7), (1, 7, 5)]              # 外侧
        # 裙摆：在脚踝后面很远，旧取法（+y 最大）命中的正是它这类顶点
        cls.positions.append((0.8, 0.3, 3.0))
        cls.ankle_max = convert_vec(cls.ANKLE, cls.SCALE)
        cls.ball_max = convert_vec(cls.BALL, cls.SCALE)

    def _shoe(self, faces=None):
        return export_obj._foot_profile(self.positions, self.SCALE,
                                        self.ankle_max, self.ball_max, faces)

    def test_shoe_tip_lies_ahead_of_the_ball(self):
        shoe = self._shoe(self.faces)
        self.assertIsNotNone(shoe)
        self.assertLess(shoe["tip"][1], self.ball_max[1],
                        "鞋尖必须在脚掌的前方（-y）；取到 +y 就是量到脚后跟了")
        self.assertLess(shoe["tip"][1], -15.0, "鞋尖应落在盒子前端 y=-16 那一片")

    def test_toe_length_is_only_the_tip_segment(self):
        shoe = self._shoe(self.faces)
        link = ((self.ankle_max[0] - self.ball_max[0]) ** 2
                + (self.ankle_max[1] - self.ball_max[1]) ** 2
                + (self.ankle_max[2] - self.ball_max[2]) ** 2) ** 0.5
        self.assertAlmostEqual(shoe["toe_len"], 6.0, places=3)
        self.assertLess(shoe["toe_len"], link,
                        "趾长必须短于整只脚（12.28 > 11.23 就是「Toe0 太大」的根因）")
        self.assertGreater(shoe["foot_len"], link, "踝→鞋尖应比踝→脚掌远")

    def test_height_is_the_box_that_fits_under_the_sloping_roof(self):
        """「整块骨头呆在鞋里」= 绕 pivot 中心对称的盒子最矮也要让过鞋尖那段鞋面。"""
        shoe = self._shoe(self.faces)
        self.assertAlmostEqual(shoe["toe_h"], 3.4, places=3,
                               msg="鞋面在最后一个采样面（0.9×趾长）处高 4.2，pivot z=2.5")
        band = self._shoe()          # 没有面可切 → 退回「脚掌那一片」的旧口径
        self.assertAlmostEqual(band["toe_h"], 5.5, places=3,
                               msg="兜底口径就是 Foot 末端截面：鞋底 0.5 → 鞋面 6.0")
        self.assertLess(shoe["toe_h"], band["toe_h"],
                        "新口径只会比「同 Foot 末端」更小，否则盒子照样戳出鞋面")
        az = self.ball_max[2]
        self.assertGreater(shoe["toe_h"] / 2.0, 0.0)
        self.assertLessEqual(az + shoe["toe_h"] / 2.0, 4.2 + 1e-6,
                             "盒子上沿不得高于脚尖区最低的鞋面")
        self.assertGreaterEqual(az - shoe["toe_h"] / 2.0, 0.5 - 1e-6, "盒子下沿不得穿破鞋底")

    def test_width_uses_the_whole_span_not_the_narrowest_slice(self):
        """宽度按整段跨度的最外侧算（正视轮廓包住即可）：鞋头向前收窄且向外偏，
        逐截面取最紧会把趾骨压成牙签。这只鞋前后同宽 6，pivot 在正中 → 6。"""
        shoe = self._shoe(self.faces)
        self.assertAlmostEqual(shoe["toe_w"], 6.0, places=3)

    def test_foot_row_carries_width_and_height(self):
        """limbs.tsv 的脚段要有 18 列，Max 端才可能照尺寸闭环而不是猜比例。

        列 15/16 是 #19 给 Toe0 量的脚尖截面，列 17/18 是 #37 给 Foot 量的
        「沿踝→脚掌轴，鞋里装得下的中心对称盒子」—— 同一句话管两段。
        """
        model = _Model([
            _Bone("センター", (0.0, 0.0, 0.0), -1),
            _Bone("左足首", self.ANKLE, 0, tail_index=2),
            _Bone("左つま先", self.BALL, 1),
        ])
        mapping = {1: "Bip001 L Foot", 2: "Bip001 L Toe0"}
        _kept, old_to_new, _parents, heads, _tails = export_obj._build_skeleton_from_kept(
            model, self.SCALE, [0, 1, 2], mapping)
        lines, _count = export_obj._limb_rows(model, self.SCALE, old_to_new, heads,
                                              mapping, self.positions, self.faces)
        row = [ln.split("\t") for ln in lines if ln.startswith("Bip001 L Foot\t")]
        self.assertEqual(len(row), 1, "脚段行没写出来")
        cols = row[0]
        self.assertEqual(len(cols), 18, f"脚段应有 18 列：{cols}")
        self.assertEqual(cols[8], "foot")
        self.assertAlmostEqual(float(cols[10]), 6.0, places=3, msg="趾长")
        self.assertAlmostEqual(float(cols[14]), 6.0, places=3, msg="脚尖处宽")
        self.assertAlmostEqual(float(cols[15]), 3.4, places=3, msg="脚尖处高")
        shoe = self._shoe(self.faces)
        for idx, key in ((16, "foot_w"), (17, "foot_h")):
            self.assertAlmostEqual(float(cols[idx]), shoe[key], places=6,
                                   msg=f"第 {idx + 1} 列必须就是 Python 量到的 {key}")
            self.assertGreater(float(cols[idx]), 0.0, "足背截面不能写 0")

    def test_foot_box_columns_survive_without_link_measurement(self):
        """量不到内切盒子时也必须写满 18 列：列数一变，Max 端 filterString 的索引全错位。"""
        model = _Model([
            _Bone("センター", (0.0, 0.0, 0.0), -1),
            _Bone("左足首", self.ANKLE, 0, tail_index=2),
            _Bone("左つま先", self.BALL, 1),
        ])
        mapping = {1: "Bip001 L Foot", 2: "Bip001 L Toe0"}
        _kept, old_to_new, _parents, heads, _tails = export_obj._build_skeleton_from_kept(
            model, self.SCALE, [0, 1, 2], mapping)
        # 鞋面顶点太稀（整只鞋只有 10 个点）：每个带都凑不满投票人数 ⇒ 内切盒子量不出来
        pts = [p for p in self.positions] + [(0.8, 1.5, 3.0), (0.9, 1.5, 3.0)]
        lines, _count = export_obj._limb_rows(model, self.SCALE, old_to_new, heads,
                                              mapping, pts, None)
        row = [ln.split("\t") for ln in lines if ln.startswith("Bip001 L Foot\t")]
        self.assertEqual(len(row[0]), 18, f"退回兜底口径也要 18 列：{row[0]}")

    def test_mesh_without_a_foot_falls_back_to_a_plain_row(self):
        """脚上没有网格 = 合法的退回（9 列 + 表里一行 `#` 说明），不是静默降级。

        「退回普通段」和「悄悄退回普通段」的差别就是这轮加的：写侧出声（下面那条
        用例查 `#` 行），判据侧独立复核（`verdicts.limb_rows_of_mode` 拿权重表问这双脚
        到底有没有蒙皮顶点）。样例夹具 `make_sample.py` 只有 4 个躯干顶点，全项目一大半
        用例都从它出发，所以这一支不能抛 —— 抛了就等于把夹具的缺网格外包成产品缺陷。
        """
        model = _Model([
            _Bone("センター", (0.0, 0.0, 0.0), -1),
            _Bone("左足首", self.ANKLE, 0, tail_index=2),
            _Bone("左つま先", self.BALL, 1),
        ])
        mapping = {1: "Bip001 L Foot", 2: "Bip001 L Toe0"}
        _kept, old_to_new, _parents, heads, _tails = export_obj._build_skeleton_from_kept(
            model, self.SCALE, [0, 1, 2], mapping)
        lines, _count = export_obj._limb_rows(model, self.SCALE, old_to_new, heads,
                                              mapping, [])
        row = [ln for ln in lines if ln.startswith("Bip001 L Foot\t")]
        self.assertTrue(row, "脚段本身还是要写")
        cols = row[0].split("\t")
        self.assertEqual(len(cols), 9, f"没有网格证据时退回普通段（9 列）：{row[0]}")
        self.assertEqual(cols[8], "link", f"没有鞋就是普通段：{row[0]}")
        for value, want in zip(cols[5:8], self.ball_max):
            self.assertAlmostEqual(float(value), want, places=3,
                                   msg="退回普通段时位置列就是子骨（脚掌 ball）本身")

    def _foot_only_model(self, toe_pos):
        """センター + 左足首(踝) + 左つま先(脚掌)：脚段那一行只由这三根骨决定。"""
        model = _Model([
            _Bone("センター", (0.0, 0.0, 0.0), -1),
            _Bone("左足首", self.ANKLE, 0, tail_index=2),
            _Bone("左つま先", toe_pos, 1),
        ])
        mapping = {1: "Bip001 L Foot", 2: "Bip001 L Toe0"}
        _kept, old_to_new, _parents, heads, _tails = export_obj._build_skeleton_from_kept(
            model, self.SCALE, [0, 1, 2], mapping)
        return model, old_to_new, heads, mapping

    def test_no_mesh_ahead_of_the_ball_degrades_loudly_in_the_table(self):
        """网格在脚掌平面之前一个点也没有：退回普通段，并在表里留一行 `#` 说明。

        查三件事：行是 9 列（不许留空的第 10~18 列，Max 的 filterString 会把空字段
        丢掉、索引整体错位）、mode=link、以及那行说明点名了这一对骨并说清少了哪几列。
        说明行是给**人**看的：两张 limbs.tsv 一比，「脚盒判据少了一族」当场能看出原因。
        """
        model, old_to_new, heads, mapping = self._foot_only_model(self.BALL)
        heel_only = [p for p in self.positions if p[2] >= self.BALL[2]]
        self.assertTrue(heel_only, "夹具得还剩点后跟/鞋面，否则这条测的是空网格")
        lines, _count = export_obj._limb_rows(model, self.SCALE, old_to_new, heads,
                                              mapping, heel_only, self.faces)
        cols = [ln.split("\t") for ln in lines if ln.startswith("Bip001 L Foot\t")][0]
        self.assertEqual(len(cols), 9, f"量不到趾长只能退回 9 列：{cols}")
        self.assertEqual(cols[8], "link", f"退回段必须是普通段：{cols}")
        notes = [ln for ln in lines if ln.startswith("# 脚段退化")]
        self.assertEqual(len(notes), 1, f"退化要在表里出声：{notes}")
        self.assertIn("Bip001 L Foot -> Bip001 L Toe0", notes[0])
        self.assertIn("第 10~18 列", notes[0])

    def test_a_foot_with_no_direction_aborts_the_export(self):
        """踝与脚掌水平重合：脚趾朝向推不出来，抛，不退回普通段。

        这一种和「脚上没网格」的区别是「有没有一根骨头的几何是坏的」。窗口、鞋尖、
        截面全依赖踝→脚掌这根轴，轴塌了还硬写 mode=foot，Max 端就把一根量不出方向的
        骨盒钉在猜出来的朝向上（#40 的量法错到 12.28cm 趾长就是这么来的）；而退回
        普通段会让脚链验收整族消失。两种都不许静默。
        """
        straight_down = (self.BALL[0], self.BALL[1], self.ANKLE[2])
        model, old_to_new, heads, mapping = self._foot_only_model(straight_down)
        with self.assertRaises(PMXError) as caught:
            export_obj._limb_rows(model, self.SCALE, old_to_new, heads,
                                  mapping, self.positions, self.faces)
        message = str(caught.exception)
        self.assertIn("Bip001 L Foot -> Bip001 L Toe0", message)
        self.assertIn("重合", message)


class FootLinkBoxTests(unittest.TestCase):
    """#37 立、#39 改口径：沿「踝→脚掌」轴量 Foot 骨盒截面。

    用户原话（#37）：「你的脚的骨骼也并没有包在脚的里面，实际上脚穿模出来了。」
    用户原话（#39）：「第一张是你现在的脚掌的位置和模型大小，第二张是我想要的像
    一个框一样框住脚的脚跟」—— 第一张就是 #37 那判据量出来的 1.53cm 牙签板。
    第十四轮回读推翻了 #37 的判据基础：Biped 脚盒**不绕长度轴中心对称**，竖直方向
    15.2% 在轴上方、84.8% 在轴下方，且 #scale 逐字保持这个比例。于是
        宽 = 2 × min(各段里更近的一侧)          —— 横向本来就对称，这条没错，留着
        高 = 窗内最低点离轴的距离 ÷ 0.848        —— 盒底贴着鞋底，上方那一小条允许
                                                   伸进靴筒（拿它当判据就是牙签板）
    量带也从「0..段长」改成骨盒**真正存在**的那一段 [-21.1%, +70.7%] 段长。
    两条仍然守住的规矩：
      1. 横向开口不算净空：半边鞋帮那一段没有投票权 —— 否则宽会被装饰边判没；
      2. 太稀的带不算净空（宽那一路仍按带投票）。
    #39 加的第 3 条：只有**盒子里面**（o 落在骨盒自己那段竖直余量里）的网格才给宽投票，
    盒顶以上的小腿/裙摆碰不到盒子，既不许压窄它，也不许把「这一侧还有多少空地」撑大。
    heelbox=False 那一档是**回滚位**，逐字对应第十四轮：每个用例都顺手钉一遍。

    #40 把「压平轴」从这套判据里剥出来单独成一个开关（`FOOT_BOX_FLAT`）：
    第十五轮为了框住脚跟把轴压成水平，代价是骨轴不再经过自己的子关节（Toe0 的
    pivot 被 段校验 钉在 PMX 球部，压不压平它都不动），用户看到的断链就是这么来的。
    #41 把默认翻回压平，因为「平轴必断链」那条推理的前提（盒子沿轴的长度改不了）
    被探针推翻了：长度轴 #scale 乘 k 时盒远端与子节点同步乘 k，所以 Max 端
    足盒接趾 能把平轴那只厚盒子的前端面拉到 PMX 球部。
    斜轴那一档现在当回滚位，高判据也跟着改成**逐带取紧**：斜轴上盒底是一张跟着轴
    一起斜的面，各带离鞋底不等距，拿「全窗最低点」那一张水平面的写法会量出 8.87 的盒，
    盒前角直接钻到地下 2.94cm（见 test_pitched_box_bottom_stays_inside_the_shoe_in_every_band）。
    坐标一律用 Max 空间（_foot_link_box 收的就是导出单位）。
    """

    ANKLE = (0.0, -2.0, 5.0)
    BALL = (0.0, -12.0, 5.0)        # 轴 = (0,-1,0)，段长 10；side=-x，up=+z

    @staticmethod
    def _slab(y_lo, y_hi, xs, zs, step=0.2):
        """把「一片鞋」撒成点阵：y 从 y_lo 到 y_hi，每个横截面配上 xs×zs 个点。"""
        pts = []
        y = y_lo
        while y <= y_hi + 1e-9:
            for x in xs:
                for z in zs:
                    pts.append((x, y, z))
            y += step
        return pts

    def _box(self, pts, heelbox=None, flatten=None):
        return export_obj._foot_link_box(pts, self.ANKLE, self.BALL,
                                         heelbox=heelbox, flatten=flatten)

    @staticmethod
    def _frame(ankle, ball, flatten):
        """骨盒的局部系：长度轴 u（斜/平由 flatten 决定）、横向 side = 水平且垂直 u、
        up = side × u，再加段长。和 `_foot_link_box` 里的定义一一对应，但这里另算一遍
        —— 用例要的是核对，不是复读实现。
        """
        ax, ay, az = ankle
        dx, dy, dz = ball[0] - ax, ball[1] - ay, ball[2] - az
        seg = (dx * dx + dy * dy + dz * dz) ** 0.5
        if flatten:
            horiz = (dx * dx + dy * dy) ** 0.5
            ux, uy, uz = dx / horiz, dy / horiz, 0.0
        else:
            ux, uy, uz = dx / seg, dy / seg, dz / seg
        sx, sy = uy, -ux
        sl = (sx * sx + sy * sy) ** 0.5
        sx, sy = sx / sl, sy / sl
        return ((ux, uy, uz), (sx, sy, 0.0),
                (sy * uz, -sx * uz, sx * uy - sy * ux), seg)

    @staticmethod
    def _proj(pts, ankle, frame):
        """把点投影到 (t, s, o) —— t 沿长度轴、o 沿 up（= 盒子的高方向）。"""
        (ux, uy, uz), (sx, sy, _sz), (upx, upy, upz), _seg = frame
        out = []
        for (x, y, z) in pts:
            vx, vy, vz = x - ankle[0], y - ankle[1], z - ankle[2]
            out.append((vx * ux + vy * uy + vz * uz,
                        vx * sx + vy * sy,
                        vx * upx + vy * upy + vz * upz))
        return out

    def _corners(self, box, ankle, ball, flatten):
        """把 (宽, 高) 摆回世界坐标，返回骨盒 8 个角。

        骨盒占 t ∈ [BACK, FWD] × 段长、o ∈ [-0.848H, +0.152H]、s ∈ ±宽/2。
        """
        w, h = box
        u, s, up, seg = self._frame(ankle, ball, flatten)
        out = []
        for t in (export_obj.FOOT_BOX_BACK_FRAC * seg,
                  export_obj.FOOT_BOX_FWD_FRAC * seg):
            for sd in (-w / 2.0, w / 2.0):
                for o in (-export_obj.FOOT_BOX_DOWN_FRAC * h,
                          export_obj.FOOT_BOX_UP_FRAC * h):
                    out.append(tuple(ankle[i] + t * u[i] + sd * s[i] + o * up[i]
                                     for i in range(3)))
        return out

    def test_closed_shoe_gives_the_inscribed_box(self):
        """前后同宽 6、鞋底离轴 3 的封闭鞋 → 高 = 3/0.848 = 3.5377：
        盒底正好落在鞋底上，盒顶只露出 0.152×高 = 0.54（鞋面在轴上方 3，容得下）。"""
        pts = self._slab(-12.0, -2.0, (-3.0, -1.5, 0.0, 1.5, 3.0), (2.0, 5.0, 8.0))
        box = self._box(pts)
        self.assertIsNotNone(box)
        self.assertAlmostEqual(box[0], 6.0, places=3)
        self.assertAlmostEqual(box[1], 3.0 / export_obj.FOOT_BOX_DOWN_FRAC, places=3)
        # 回滚位：第十四轮那判据（绕轴对称取 2×min）在同一个用例上是 6.0
        self.assertAlmostEqual(self._box(pts, heelbox=False)[1], 6.0, places=3)

    def test_off_center_axis_is_bound_by_the_tighter_side(self):
        """鞋面只到 z=6（轴上方余 1）：新判据**不再**拿上方当约束 —— 上方只占盒子的
        15.2%，余 1 也装得下高 3.54 的盒子（盒顶 0.54 < 1）。老判据在这里收 2.0，
        正是它把整根骨盒压成牙签板的那只手。"""
        pts = self._slab(-12.0, -2.0, (-3.0, 0.0, 3.0), (2.0, 4.0, 6.0))
        box = self._box(pts)
        self.assertAlmostEqual(box[0], 6.0, places=3, msg="宽度不受影响")
        self.assertAlmostEqual(box[1], 3.0 / export_obj.FOOT_BOX_DOWN_FRAC, places=3,
                               msg="高由**下方**净空定：鞋底离轴 3 ⇒ 3/0.848")
        self.assertLess(export_obj.FOOT_BOX_UP_FRAC * box[1], 1.0 + 1e-6,
                        msg="盒顶必须还在轴上方那 1cm 的鞋面余量里，否则这条用例没测到点")
        self.assertAlmostEqual(self._box(pts, heelbox=False)[1], 2.0, places=3,
                               msg="回滚位：取更紧的一侧 = 向上余 1 的那一半")

    def test_open_top_is_not_a_constraint(self):
        """鞋口朝上（轴上方完全没有网格）：#37 判「整只鞋不可量」，#39 改成
        「高只看下方」—— 因为骨盒本来就挂在轴下方，上方那一小条伸进靴筒不算穿模。
        这条是本次口径改动的正面记录，不是回归。"""
        pts = self._slab(-12.0, -2.0, (-3.0, 0.0, 3.0), (2.0, 3.0, 4.0))
        box = self._box(pts)
        self.assertIsNotNone(box, "开口不再判死：高 = 轴到鞋底 3 ÷ 0.848")
        self.assertAlmostEqual(box[1], 3.0 / export_obj.FOOT_BOX_DOWN_FRAC, places=3)
        self.assertIsNone(self._box(pts, heelbox=False),
                          "回滚位必须逐字回到第十四轮：上方没有面 → 整只鞋拒量")

    def test_one_sided_band_votes_only_for_the_other_direction(self):
        """半边鞋帮（x 全在轴的一侧）：横向拒投 → 宽出不来 → 整只盒子宁可不要。
        这条和上面那条分家：#37 把两个方向绑在一起判死，#39 只放开**纵向**的开口，
        横向开口仍然一票否决（否则装饰边会把宽判成 0.2）。
        """
        pts = self._slab(-12.0, -2.0, (0.5, 1.5, 3.0), (2.0, 5.0, 8.0))
        self.assertIsNone(self._box(pts), "横向全程没有投票权 → 宽不出来，整只盒子宁可不要")
        # 同一批点补上内侧鞋帮，两方向都有投票权，立刻量得出
        pts2 = self._slab(-12.0, -2.0, (-3.0, 0.5, 1.5, 3.0), (2.0, 5.0, 8.0))
        box = self._box(pts2)
        self.assertAlmostEqual(box[0], 6.0, places=3)
        self.assertAlmostEqual(box[1], 3.0 / export_obj.FOOT_BOX_DOWN_FRAC, places=3)

    def test_decoration_at_the_axis_does_not_make_a_toothpick(self):
        """后半只鞋是正常管，前半只鞋只剩一片贴轴的装饰边（x∈[0.1,0.3]）：
        贴轴那段横向是单侧的，没有投票权 → 宽仍是 6，而不是 0.2。"""
        pts = (self._slab(-12.0, -6.0, (-3.0, -1.5, 0.0, 1.5, 3.0), (2.0, 5.0, 8.0))
               + self._slab(-6.0, -2.0, (0.1, 0.2, 0.3), (2.0, 5.0, 8.0)))
        box = self._box(pts)
        self.assertIsNotNone(box)
        self.assertGreater(box[0], 5.0, f"贴轴的装饰片把宽判没了：{box}")
        self.assertAlmostEqual(box[1], 3.0 / export_obj.FOOT_BOX_DOWN_FRAC, places=3)

    def test_sparse_band_is_skipped_not_used_as_evidence(self):
        """中间两段只有 6 个零星顶点（一条装饰边）：凑不满投票人数，跳过。

        香菱第 10 带实测只有 6–8 个点，正是它把「高」从 1.53 一路压到 1.13；
        门槛从 3 提到 8 之后，鞋帮那几段（几十个点）才是判据。
        新判据的高是「窗内最低点」一个数（轴压平后盒底就是一张水平面），
        贴轴的零星点根本碰不到它 —— 所以这里钉的是「高不随噪声变」而不是「高 > 5」。
        """
        tube = (self._slab(-12.0, -8.0, (-3.0, -1.5, 0.0, 1.5, 3.0), (2.0, 5.0, 8.0))
                + self._slab(-4.0, -2.0, (-3.0, -1.5, 0.0, 1.5, 3.0), (2.0, 5.0, 8.0)))
        strays = [(-0.1, -6.0, 4.9), (0.1, -6.0, 4.9), (-0.1, -6.0, 5.1),
                  (0.1, -6.0, 5.1), (0.0, -6.0, 5.0), (0.0, -6.0, 4.95)]
        self.assertEqual(len(strays), 6, "零星点必须少于门槛，否则这条用例没测到东西")
        box = self._box(tube + strays)
        self.assertIsNotNone(box)
        self.assertGreater(box[0], 5.0, f"6 个零星点把宽判成了牙签：{box}")
        self.assertAlmostEqual(box[1], 3.0 / export_obj.FOOT_BOX_DOWN_FRAC, places=3,
                               msg=f"高被贴轴噪声带跑了：{box}")

    def test_height_is_capped_by_the_link_length(self):
        """高不许超过段长：换到高跟/悬空模型时，「轴到鞋底」可能比骨头还长，
        封顶在段长，免得又长出一根戳穿地面的盒子（香菱 8.66 < 11.23 不触发）。"""
        deep = self._slab(-12.0, -2.0, (-3.0, 0.0, 3.0), (-20.0, 5.0, 8.0))
        box = self._box(deep)
        self.assertIsNotNone(box)
        self.assertLessEqual(box[1], 10.0 + 1e-6, "段长 10 ⇒ 高最多 10")

    PITCH_ANKLE = (0.0, -2.0, 7.5)
    PITCH_BALL = (0.0, -12.0, 1.9)      # 往前下坡：和香菱的踝→球同量级（26 度）

    def _pitched_shoe(self):
        """一只带脚后跟的靴子截面：鞋底 z=0（从脚跟 y=+2 一直铺到鞋尖 y=-13）、
        脚背 z=4、靴筒 z=8，两侧 x=±3。

        鞋底必须**铺过踝后**：斜轴往后探出的那 21% 是「上后方」，盒底面在它里面
        要找得到网格才量得出下方净空（香菱实跑是那一段由小腿/靴筒的管壁补上的）。
        """
        return self._slab(-13.0, 2.0, (-3.0, 0.0, 3.0), (0.0, 4.0, 8.0))

    def test_flat_default_and_pitched_rollback_give_different_boxes(self):
        """#41：轴的俯仰仍然是**一个**开关（导出侧 FOOT_BOX_FLAT），
        但默认翻了 —— 「平轴就断链」那条理由已被探针推翻（见 README 第 23 条）。

        平轴（默认，第十五轮那只盒子）盒底是一张水平面，一个数就能定高：轴在 z=7.5、
        鞋底在 z=0 ⇒ 高 = 7.5/0.848 = 8.8444；盒子沿轴够不够到子关节由 Max 端的
        足盒接趾 负责，不靠轴的俯仰凑。
        斜轴（回滚位）盒底跟着轴一起斜，各带离鞋底不等距，判据必须逐带取紧，
        所以同一个用例上高**必须更小**；而宽那一路是横向的，跟俯仰无关。
        """
        pts = self._pitched_shoe()
        pitched = export_obj._foot_link_box(pts, self.PITCH_ANKLE, self.PITCH_BALL,
                                            flatten=False)
        flat = export_obj._foot_link_box(pts, self.PITCH_ANKLE, self.PITCH_BALL,
                                         flatten=True)
        self.assertTrue(export_obj.FOOT_BOX_FLAT,
                        "#41 之后默认必须是平轴：厚盒子 + 足盒接趾 同时满足 #39 和 #40")
        self.assertIsNotNone(pitched)
        self.assertIsNotNone(flat)
        self.assertAlmostEqual(flat[1], 7.5 / export_obj.FOOT_BOX_DOWN_FRAC, places=3,
                               msg=f"默认这一档必须逐字留在第十五轮的盒子：{flat}")
        self.assertLess(pitched[1], flat[1],
                        f"斜轴还在用「全窗最低点」定高 = 盒角会戳到地下：{pitched} vs {flat}")

    def test_pitched_box_bottom_stays_inside_the_shoe_in_every_band(self):
        """斜轴（回滚位）+ 逐带取紧：盒底面这一张斜平面必须在**每一带**都
        落在网格上下沿之间 —— 一个盒角都不许钻到鞋底下面。

        平轴那种「拿全窗最低点定高」在水平轴上是对的（盒底就是那张水平面，
        贴到鞋底最低点正是要的效果），搬到斜轴上就错：斜着的面会往**前**继续下探，
        香菱实跑盒前角 z=-2.94（地面以下）。这条用例钉的是回滚分支自己的算法，
        默认已经走平轴，所以两处调用都得把 flatten=False 写出来（别靠默认值）。
        """
        pts = self._pitched_shoe()
        A, B = self.PITCH_ANKLE, self.PITCH_BALL
        box = export_obj._foot_link_box(pts, A, B, flatten=False)
        self.assertIsNotNone(box)
        w, h = box
        frame = self._frame(A, B, False)
        rows = self._proj(pts, A, frame)
        seg = frame[3]
        t_lo = export_obj.FOOT_BOX_BACK_FRAC * seg
        t_hi = export_obj.FOOT_BOX_FWD_FRAC * seg
        deepest = -min(r[2] for r in rows if t_lo - 0.5 <= r[0] <= t_hi + 0.5)
        self.assertLess(h, min(deepest / export_obj.FOOT_BOX_DOWN_FRAC, seg) - 1e-6,
                        msg=f"高还按「全窗最低点」量 = 没逐带取紧（h={h}, 全窗={deepest}）")
        zs = [c[2] for c in self._corners(box, A, B, False)]
        self.assertGreaterEqual(min(zs), -0.5,
                                msg=f"盒角戳到鞋底下面去了：{box} 角 z={zs}")
        # 反向对照：把同一只鞋按平轴那个「全窗最低点」的高来摆到**斜**轴上，盒前角必然穿地
        bad = (w, min(deepest / export_obj.FOOT_BOX_DOWN_FRAC, seg))
        self.assertLess(min(c[2] for c in self._corners(bad, A, B, False)), -1.0,
                        msg="这条对照必须真的会戳到地下，否则上面那个门槛没测到东西")

    def test_flat_default_frames_the_heel_and_reaches_ground(self):
        """默认（平轴 + 全窗最低点）：盒底面正好压在鞋底上，后端（脚后跟那侧）
        也一起压到底 —— 用户在 #39 画的那个框、#41 右边那张图的厚盒子。
        水平轴不过球部那 29.93 度不再当断链看：沿轴最后一段由 足盒接趾 把前端面
        拉到 PMX 球部（Max 端实测判据 = 盒隙），所以这一档不再是「回滚位」。"""
        pts = self._pitched_shoe()
        box = export_obj._foot_link_box(pts, self.PITCH_ANKLE, self.PITCH_BALL,
                                        flatten=True)
        self.assertIsNotNone(box)
        corners = self._corners(box, self.PITCH_ANKLE, self.PITCH_BALL, True)
        self.assertAlmostEqual(min(c[2] for c in corners), 0.0, places=3,
                               msg="平轴 + 全窗最低点 = 盒底正好落在鞋底上")
        pitched = export_obj._foot_link_box(pts, self.PITCH_ANKLE, self.PITCH_BALL,
                                            flatten=False)
        # 斜轴的盒底是斜的：前端贴着鞋底，后端（脚后跟那侧）必然抬起来 —— 这一条就是
        # #39「框住脚跟」在斜轴上单独做不到的证据（#41 补的是长度，不是这张斜底面）。
        z_back = min(c[2] for c in self._corners(pitched, self.PITCH_ANKLE,
                                                 self.PITCH_BALL, False)
                     if c[1] > self.PITCH_ANKLE[1])
        self.assertGreater(z_back, 0.5,
                           msg=f"斜轴盒底在脚跟那侧抬起来了（预期，不是回归）：{z_back}")


    def test_degenerate_geometry_returns_none(self):
        """踝和脚掌重合、或者轴接近竖直：宽高判据退化，宁可不量也不猜。"""
        self.assertIsNone(export_obj._foot_link_box(
            [(-1.0, -2.0, 5.0), (1.0, -2.0, 5.0)], self.ANKLE, self.ANKLE))
        upright = self._slab(-12.0, -2.0, (-3.0, 0.0, 3.0), (2.0, 5.0, 8.0))
        self.assertIsNone(export_obj._foot_link_box(
            upright, self.ANKLE, (0.0, -2.0, 15.0)), "竖直轴：水平 side 退化")

    def test_mesh_above_the_box_does_not_widen_it(self):
        """盒顶以上的小腿/裙摆不许给「宽」投票 —— 香菱实测：轴一压平，量带从斜轴那
        11.2cm 挪成一个更宽的横条，裙摆顶点（离轴 o=+136cm，横向 s=+4.2）也进了带，
        把宽从 3.71 撑到 8.44 —— 比整只鞋（6.41）还宽，左右还差 2.2cm（#26 修的就是
        左右不一致）。骨盒在 (t, o) 平面里只占它自己那一段，高出盒顶的面碰不到它。"""
        shoe = self._slab(-12.0, -2.0, (-2.4, 0.0, 1.86), (2.0, 4.0, 5.0))
        skirt = self._slab(-12.0, -2.0, (-4.2, 0.0, 6.9), (20.0, 25.0, 30.0))
        box = self._box(shoe + skirt)
        self.assertIsNotNone(box)
        self.assertAlmostEqual(box[0], 2.0 * 1.86, places=3,
                               msg=f"裙摆把盒子撑宽了：{box}")
        self.assertAlmostEqual(box[1], 3.0 / export_obj.FOOT_BOX_DOWN_FRAC, places=3,
                               msg="高仍按「窗内最低点」量，裙摆在轴上方不参与")
        # 回滚位没有这道闸：斜轴那条窗口本来就窄到装不下裙摆，钉住它别被顺手改掉
        self.assertAlmostEqual(self._box(shoe + skirt, heelbox=False)[0], 2.0 * 4.2, places=3)

    def test_foot_profile_falls_back_to_a_capped_section(self):
        """量不到内切盒子时退回「脚掌那一片的截面」，并按段长 60% 封顶 —— 只可能偏细。

        y 下界取 -13 而不是 -12：BALL 在 y=-12，一片只到 -12 的网格等于「脚掌前面
        一个点也没有」，`_foot_profile` 在那一步就退回 None（那是另一条用例，见
        `test_mesh_without_a_foot_falls_back_to_a_plain_row`），量不到内切盒子的兜底
        口径根本走不到。往前多给 1cm，测的才是本意。
        """
        pts_max = self._slab(-13.0, -2.0, (-3.0, 0.0, 3.0), (2.0, 3.0, 4.0))
        # Max(x,y,z) -> PMX(x,z,y)，scale=1 时 convert_vec 正好换回来
        pmx = [(x, z, y) for (x, y, z) in pts_max]
        shoe = export_obj._foot_profile(pmx, 1.0, self.ANKLE, self.BALL, None)
        self.assertIsNotNone(shoe)
        self.assertIn("foot_w", shoe)
        self.assertIn("foot_h", shoe)
        self.assertGreater(shoe["foot_w"], 0.0)
        cap = 10.0 * 0.6      # 兜底上界 = 0.6 × 踝→脚掌段长
        self.assertLessEqual(shoe["foot_w"], cap + 1e-6)
        self.assertLessEqual(shoe["foot_h"], cap + 1e-6,
                             msg="封顶是防「整根 8cm 粗的骨盒」重演，量不到也不许超过它")


class FootHeelBoxHandoffTests(unittest.TestCase):
    """#39/#41：量截面的轴（Python）和转骨盒的轴（Max）必须是**同一根**。

    第十四轮回读：Biped 脚盒竖直方向 15.2% 在轴上方、84.8% 在轴下方，长度方向
    [-21.1%, +70.7%] 段长 —— 这四个数是量出来的，不是设计值，所以钉成用例：
    哪天换了 Max 版本比例变了，这几条会先响。
    压平之后 Python 若还按斜轴量带，Max 就把「斜轴量出来的细截面」写进压平的盒子
    —— 第十四轮两端就是各量各的，只是当时没人发现。

    M1b 之后这条同源链只剩一跳：`FOOT_BOX_FLAT` → meta 的 `foot_box_flat`
    → MMD2MAX.CFG 的 `MMD2MAX_FOOTBOXFLAT`。中间既不反转也不写 0/1，所以
    「翻一个词」到底翻成哪根轴，看字面就知道。
    """

    # 装配必需的最小输入目录：build_cfg 会校验必需路径非空，缺一个就抛。
    REQUIRED_INPUTS = ("t.obj", "t_bones.tsv", "t_weights.tsv",
                       "t_biped_map.tsv", "t_limbs.tsv")
    META_BASE = ["key\tvalue", "scale\t1.0", "spine_links\t3",
                 "fingers\t5", "finger_links\t3",
                 # #70/#71 的两张特写取景框：build_cfg 必需这十二行（值允许空 = 导出量不出
                 # subject）。三轴各一对，就是香菱那一组实测值 × scale 1.0。
                 "chest_box_x_lo\t-19.3487", "chest_box_x_hi\t19.3487",
                 "chest_box_y_lo\t-26.1781", "chest_box_y_hi\t7.1932",
                 "chest_box_z_lo\t96.5655", "chest_box_z_hi\t114.3923",
                 "neck_box_x_lo\t-18.2763", "neck_box_x_hi\t14.4334",
                 "neck_box_y_lo\t-21.2002", "neck_box_y_hi\t14.3967",
                 "neck_box_z_lo\t92.1007", "neck_box_z_hi\t136.9463"]

    def _cfg(self, tmp, meta_lines):
        """写出 meta + 必需输入文件，返回 obj_run.build_cfg 的键值表。"""
        sys.path.insert(0, str(PROJECT_ROOT / "tools"))
        import obj_run
        base = Path(tmp)
        for name in self.REQUIRED_INPUTS:
            (base / name).write_text("x\n", encoding="utf-8")
        (base / "t_meta.tsv").write_text("\n".join(meta_lines) + "\n", encoding="utf-8")
        cfg, _ = obj_run.build_cfg(base, "t", 100)
        return cfg

    def test_measured_placement_fractions_are_pinned(self):
        self.assertTrue(export_obj.FOOT_HEELBOX,
                        "默认必须走新判据；这条挂了说明整根开关被翻回去了")
        self.assertTrue(export_obj.FOOT_BOX_FLAT,
                        "#41：默认必须是平轴（厚盒子落地 + 足盒接趾 补长度）；"
                        "斜轴那一档只当第十六轮牙签的回滚位")
        self.assertAlmostEqual(export_obj.FOOT_BOX_DOWN_FRAC, 0.848, places=3)
        self.assertAlmostEqual(export_obj.FOOT_BOX_UP_FRAC, 0.152, places=3)
        self.assertAlmostEqual(export_obj.FOOT_BOX_UP_FRAC
                               + export_obj.FOOT_BOX_DOWN_FRAC, 1.0, places=6,
                               msg="上下比例必须互补，否则高会被算成两个口径")
        self.assertAlmostEqual(export_obj.FOOT_BOX_BACK_FRAC, -0.2111, places=4)
        self.assertAlmostEqual(export_obj.FOOT_BOX_FWD_FRAC, 0.7067, places=4)
        self.assertLess(export_obj.FOOT_BOX_BACK_FRAC, 0.0,
                        "往后探出是负数 —— 脚后跟就靠这一段框住")

    def test_meta_records_the_axis_as_a_literal(self):
        """meta 里这一行写的是 FOOT_BOX_FLAT 的**字面值**（true/false，不是 0/1、不反转）。

        这行是两端唯一的同源通道：Python 按哪根轴量的盒子，Max 就得把盒子转到哪根轴，
        否则「平轴量出来的 8.66 高」被写进斜着的骨盒（或反过来）。第十五轮那次压平
        换来的是 #40（链断 29.93 度），第十六轮翻回斜轴换来的是 #41（牙签 + 3.29cm
        空气）；现在两个都由 足盒接趾 收尾，所以默认压平，这一行必须是 true。
        """
        line = "foot_box_flat\t%s" % ("true" if export_obj.FOOT_BOX_FLAT else "false")
        self.assertEqual(line, "foot_box_flat\ttrue")

    def test_cfg_carries_the_axis_value_verbatim(self):
        """CFG 里的 MMD2MAX_FOOTBOXFLAT 必须逐字等于 meta 里那一行 —— 中间不许有翻译。"""
        import tempfile
        for value in ("true", "false"):
            with tempfile.TemporaryDirectory() as tmp:
                cfg = self._cfg(tmp, self.META_BASE + ["foot_box_flat\t" + value])
                self.assertEqual(cfg["MMD2MAX_FOOTBOXFLAT"], value,
                                 f"轴口径在 {value} 这一档被改了：两端从此各摆各的")

    def test_other_meta_keys_also_arrive_verbatim(self):
        """脊柱节数 / 手指布局同理：原样搬进 CFG，不做 0/1 转换、不做默认值补齐。"""
        import tempfile
        sys.path.insert(0, str(PROJECT_ROOT / "tools"))
        import obj_run
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._cfg(tmp, ["key\tvalue", "scale\t1.0", "spine_links\t2",
                                  "fingers\t3", "finger_links\t2",
                                  "foot_box_flat\tfalse",
                                  *[f"{key}\t"
                                    for prefix in obj_run.FRAMINGS
                                    for key in obj_run.box_keys(prefix)]])
            self.assertEqual(cfg["MMD2MAX_SPINE_LINKS"], "2")
            self.assertEqual(cfg["MMD2MAX_FINGERS"], "3")
            self.assertEqual(cfg["MMD2MAX_FINGER_LINKS"], "2")
            self.assertEqual(cfg["MMD2MAX_FOOTBOXFLAT"], "false")
            # 框为空 = 这具模型量不出 subject（没有叫「胸 / bust」的骨、或没有映射到
            # 颈链三节的骨）。这十二格必须**原样**是空串：补成 0 会让 Max 把那只框当成
            # 原点（框到脚底），补成任何数都取消了 .ms 里那条「退回场景包围盒 + 高度比例带」
            # 的路径 —— 而那条路径正是换一具模型时唯一还有的那张特写。
            for prefix in obj_run.FRAMINGS:
                for key in obj_run.framing_cfg_keys(prefix):
                    self.assertEqual(cfg[key], "", f"{key} 被补了默认值")

    def test_missing_meta_key_is_fatal_not_defaulted(self):
        """旧导出目录（缺 foot_box_flat）以前会退回「第十四轮口径」的硬编码默认值 ——
        那正是「日志全绿、几何另一套」的源头。M1b：缺键直接 SystemExit，让人重跑导出。"""
        import tempfile
        sys.path.insert(0, str(PROJECT_ROOT / "tools"))
        import obj_run
        for drop in ("foot_box_flat", "spine_links", "fingers", "finger_links",
                     "chest_box_y_lo", "neck_box_z_hi"):
            with tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                for name in self.REQUIRED_INPUTS:
                    (base / name).write_text("x\n", encoding="utf-8")
                lines = [ln for ln in self.META_BASE + ["foot_box_flat\ttrue"]
                         if not ln.startswith(drop + "\t")]
                (base / "t_meta.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")
                with self.assertRaises(SystemExit) as got:
                    obj_run.build_cfg(base, "t", 100)
                self.assertIn(drop, str(got.exception),
                              f"缺 {drop} 时报的不是这个键，错误信息帮不上重跑导出的人")


class FingerLayoutTests(unittest.TestCase):
    """手指数量自适应：模型可能不是 5 指（本模型只有 3 指）。

    Biped 的 createNew 按 fingers:N 建指头，编号固定 Finger0..Finger(N-1)。
    若模型缺中指/小指，原始映射会落在 0/1/3；fingers:3 时 Finger3 根本不存在，
    所以必须把编号压紧成 0/1/2，否则药指那两节骨骼会永远摆不到位。
    """

    def test_three_finger_model_gets_compacted(self):
        mapping = {
            22: "Bip001 L Finger0", 23: "Bip001 L Finger01",
            24: "Bip001 L Finger1", 25: "Bip001 L Finger11",
            26: "Bip001 L Finger3", 27: "Bip001 L Finger31",
        }
        out = semantic._compact_fingers(mapping)
        self.assertEqual(out[26], "Bip001 L Finger2")
        self.assertEqual(out[27], "Bip001 L Finger21")
        # 其余不受影响
        self.assertEqual(out[22], "Bip001 L Finger0")
        self.assertEqual(semantic.finger_layout(out), (3, 2))

    def test_five_finger_model_is_untouched(self):
        # 标准 5 指 × 3 节：Biped 默认命名（Finger0 / Finger01 / Finger02）
        mapping = {}
        n = 0
        for digit in range(5):
            for link in range(3):
                n += 1
                mapping[n] = (f"Bip001 L Finger{digit}"
                              + ("" if link == 0 else str(link)))
        self.assertEqual(semantic._compact_fingers(mapping), mapping)
        self.assertEqual(semantic.finger_layout(mapping), (5, 3))

    def test_non_finger_targets_pass_through(self):
        mapping = {1: "Bip001 L Hand", 2: "Bip001 L Finger3"}
        out = semantic._compact_fingers(mapping)
        self.assertEqual(out[1], "Bip001 L Hand")
        self.assertEqual(out[2], "Bip001 L Finger0")


class NeckAxisTests(unittest.TestCase):
    """#17「脖子位置没对上」：Neck 必须落在**颈部网格实测中线**上。

    上一版的规则是「把 首 挪到 上半身 与 頭 的中点」。MMD 的躯干骨在**体腔中心**、
    颈部骨在**颈轴**上，两者本来就不一条线 —— 取中点等于把脖子往前拽到喉咙外面
    （香菱实测 3.03cm），侧视就是「脖子从胸口里长出来」。
    现在改成量网格切片：只有明显偏离中线才动，胸腔（Spine2）保持 PMX 原位，
    用户要的「带一点挺胸」是颈轴归位后的自然结果，不需要额外加转角。
    """

    SCALE = 10.0
    NECK_Z = 116.0        # 颈部骨在 Max 里的高度（cm）

    def _column(self, y_center=0.0, hair=True):
        """颈部网格：x ±1.5、前后以 y_center 为中心、跨 z=113~118 的方柱（Max 坐标）。"""
        pts = []
        for z in range(113, 119):
            for x in (-1.5, -0.5, 0.5, 1.5):
                for dy in (-2.0, 0.0, 2.0):
                    pts.append((x, y_center + dy, float(z)))
            if hair:      # 长发/袖子：离颈轴很远，必须被「半宽过滤」排除
                for x in (-8.0, 8.0):
                    pts.append((x, y_center - 6.0, float(z)))
        # Max -> PMX：convert_vec 交换了 y/z，所以这里也交换回去
        return [(mx / self.SCALE, mz / self.SCALE, my / self.SCALE)
                for mx, my, mz in pts]

    def _apply(self, bone_y=0.0, positions=None):
        heads = [(0.0, 0.0, 100.0), (0.0, bone_y, self.NECK_Z), (0.0, 0.0, 130.0)]
        moved = export_obj._center_neck(
            heads, {1: "Bip001 Neck"}, {1: 1},
            self._column() if positions is None else positions, self.SCALE)
        return heads, moved

    def test_axis_is_measured_from_the_neck_column_not_the_hair(self):
        got = export_obj._neck_axis_y(self._column(), self.SCALE,
                                      (0.0, 0.0, self.NECK_Z))
        self.assertIsNotNone(got)
        self.assertAlmostEqual(got, 0.0, places=3, msg="颈柱中线在 y=0")
        ys = [convert_vec(p, self.SCALE)[1] for p in self._column()]
        self.assertAlmostEqual((min(ys) + max(ys)) / 2.0, -2.0, places=3,
                               msg="前后极值取中点会被长发拽到 -2，正是要避免的旧错法")

    def test_off_axis_neck_moves_onto_the_measured_axis(self):
        heads, moved = self._apply(bone_y=0.0, positions=self._column(-2.0))
        self.assertAlmostEqual(moved, 2.0, places=3)
        self.assertAlmostEqual(heads[1][1], -2.0, places=3,
                               msg="只动前后（y）：骨必须落到实测颈轴上")
        self.assertAlmostEqual(heads[1][0], 0.0, places=6, msg="左右不许动")
        self.assertAlmostEqual(heads[1][2], self.NECK_Z, places=6, msg="高度不许动")
        self.assertEqual(heads[2], (0.0, 0.0, 130.0), "頭 永远不许动")

    def test_small_deviation_is_left_alone(self):
        """MMD 本来就多半把 首 放在颈轴上（香菱实测差 0.12cm）：噪声不该引发搬运。"""
        heads, moved = self._apply(bone_y=0.0, positions=self._column(-1.0))
        self.assertEqual(moved, 0.0)
        self.assertEqual(heads[1], (0.0, 0.0, self.NECK_Z))

    def test_shift_is_capped(self):
        """偏离再大也最多挪 3cm：网格量错了也不至于把脖子搬到脸外面。"""
        heads, moved = self._apply(bone_y=0.0, positions=self._column(-5.0))
        self.assertAlmostEqual(moved, 3.0, places=3)
        self.assertAlmostEqual(heads[1][1], -3.0, places=3)

    def test_without_mesh_evidence_the_neck_is_untouched(self):
        for empty in ([], None):
            heads, moved = self._apply(bone_y=0.0, positions=empty)
            self.assertEqual(moved, 0.0, f"量不到颈轴就不能动（{empty!r}）")
            self.assertEqual(heads[1], (0.0, 0.0, self.NECK_Z))

    def test_model_without_a_neck_mapping_is_skipped(self):
        heads = [(0.0, 0.0, 100.0), (0.0, 0.0, self.NECK_Z)]
        self.assertEqual(export_obj._center_neck(
            heads, {}, {}, self._column(), self.SCALE), 0.0)

    def test_spine_chain_midpoint_balancer_is_removed(self):
        """旧 _balance_center_chain 把 上半身2 往回拉，会正好抹掉挺胸曲线（#17 后半段）。"""
        self.assertFalse(hasattr(export_obj, "_balance_center_chain"),
                         "躯干链必须由 PMX 原位决定，不再做二次搬运")


if __name__ == "__main__":
    unittest.main(verbosity=2)

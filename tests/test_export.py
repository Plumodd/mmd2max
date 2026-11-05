"""export_obj v2 改进落地测试：无用骨剔除 / 表情导出 / 物理导出。

验证 Task 17 的全部产出：
  - filter_useless 下 IK / 物理哑骨不进入 bones.tsv（被剔除）；
  - 被剔除骨的权重已归并到保留骨架（无空权重顶点）；
  - _morphs.tsv 含顶点 morph 且偏移按坐标变换正确；
  - _physics.tsv 含刚体与关节，刚体引用的骨序号已换算成导出后新序号；
  - _biped_map.tsv 用新序号映射。
"""
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from mmd2max import export_obj               # noqa: E402
from mmd2max import semantic                 # noqa: E402
from tests import spec_table                 # noqa: E402
from tests.make_sample import create_sample  # noqa: E402


def _export(source, outdir, *args, **kw):
    """两段式管线的测试快捷入口：build_plan（纯计算）-> export（落盘）。"""
    return export_obj.export(source, outdir, export_obj.build_plan(source, *args, **kw))


def _rows(text):
    """取 spec 文件的**数据行**：第 1 行是表头（M1 起每个文件都有，tab 分隔的列名），
    `#` 行是给人看的图例，`@KIND` 行是第二套记录的列名，三者都不算数据。
    解析规则只有一份（tests/spec_table.py），这里不再自己数首行 —— 否则两处对
    「什么算数据行」的理解会各自漂移，而 Max 端 mmd2maxReadTable 是第三处。"""
    return spec_table.data_lines(text)


class ExportV2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.d = Path(self.temp.name)
        self.pmx = create_sample(self.d)

    def tearDown(self):
        self.temp.cleanup()

    def _read(self, name):
        return (self.d / f"{self.pmx.stem}_{name}").read_text(encoding="utf-8")

    def test_every_spec_file_starts_with_a_header(self):
        """每个 spec 文件的第 1 行都是列名行：不以 # 开头、列名非空且不重复。

        Max 端（mmd2maxReadTable）只认第 1 行，写歪一位就是整份文件读不到列 ——
        这条把「表头必须机器可解析」这个约定钉死。
        列名数 >= 最短数据行的列数：表头不许给「最短行都没有的列」起名字。
        （limbs 的 10~18 列只有 mode=foot 的行才有、weights 的第 2 列往右是变长的
        `骨序号:权重` 对、physics/morphs 的第二套记录由 @JOINT/@V 语法行命名 ——
        这三类各自的宽度判据在 test_limbs / test_secondary_kind_header 里验。）
        """
        _export(self.pmx, self.d, scale=8.763, filter_useless=True)
        for name in ("bones.tsv", "biped_map.tsv", "weights.tsv", "morphs.tsv",
                     "physics.tsv", "limbs.tsv", "widths.tsv", "meta.tsv"):
            text = self._read(name)
            head = text.splitlines()[0]
            self.assertFalse(head.startswith("#"), f"{name} 第 1 行必须是列名行")
            cols = head.split("\t")
            self.assertGreaterEqual(len(cols), 2, f"{name} 列名太少：{head}")
            self.assertEqual(len(set(cols)), len(cols), f"{name} 列名重复：{head}")
            for col in cols:
                self.assertTrue(col and col.strip() == col,
                                f"{name} 列名不干净：{head}")
            if name == "weights.tsv":
                # 第 2 列往右是变长的 `骨序号:权重` 对，一列都命名不了（Max 端从
                # bone_weight_pairs 的列号一路取到行尾），所以只验表头本身。
                continue
            data = _rows(text)
            if not data:
                continue    # 只有表头（本模型没有这一类数据，如 widths）：宽度无从比对
            shortest = min(len(ln.split("\t")) for ln in data)
            self.assertGreaterEqual(len(cols), shortest,
                                    f"{name} 表头只有 {len(cols)} 列，数据行却有 {shortest} 列")

    def test_secondary_kind_header_names_the_other_record(self):
        """morphs/physics 一个文件两种记录，列含义不同的那一套用 `@<记录名>` 语法行。

        主表头装不下两套（同一列号在 RIGID 是 rigid_name_en、在 JOINT 是 joint_type），
        所以 Python 在表头后面再写一行 `@V\t…` / `@JOINT\t…`；Max 端把这些名字并进
        同一张列名表，各自带 rigid_/joint_ 前缀，不会和主记录撞名。
        """
        _export(self.pmx, self.d, scale=8.763, filter_useless=True)
        phys = self._read("physics.tsv").splitlines()
        self.assertTrue(phys[1].startswith("@JOINT\t"), f"physics 第 2 行应是 @JOINT 语法行：{phys[1]}")
        morphs = self._read("morphs.tsv").splitlines()
        self.assertTrue(morphs[1].startswith("@V\t"), f"morphs 第 2 行应是 @V 语法行：{morphs[1]}")
        # 语法行的字段数要和对应记录的字段数一一对上（第 1 格是记录名本身），
        # 否则 Max 端并进来的列名会整体错位一格。
        phys_rows = _rows(self._read("physics.tsv"))
        joint = [ln for ln in phys_rows if ln.startswith("JOINT\t")][0].split("\t")
        self.assertEqual(len(phys[1].split("\t")), len(joint),
                         "@JOINT 的列名要和 JOINT 行的字段一一对上")
        morph_rows = _rows(self._read("morphs.tsv"))
        vrow = [ln for ln in morph_rows if ln.startswith("V\t")][0].split("\t")
        self.assertEqual(len(morphs[1].split("\t")), len(vrow),
                         "@V 的列名要和 V 行的字段一一对上")

    def test_useless_bones_filtered_out(self):
        info = _export(self.pmx, self.d, scale=8.763, filter_useless=True)
        bones = _rows(self._read("bones.tsv"))
        names = [ln.split("\t")[1] for ln in bones]
        self.assertNotIn("左足IK", names)
        self.assertNotIn("物理用ダミー", names)
        # 原 25 根 -> 剔除 2 根 control 骨，剩 23
        self.assertEqual(len(bones), 23)
        self.assertEqual(info["kept_bone_count"], 23)
        self.assertEqual(info["bone_count"], 25)

    def test_kept_bones_have_role_and_biped_columns(self):
        _export(self.pmx, self.d, scale=8.763, filter_useless=True)
        bones = _rows(self._read("bones.tsv"))
        for ln in bones:
            cols = ln.split("\t")
            # idx name parent role biped_target hx hy hz tx ty tz skin [max_parent]
            self.assertEqual(len(cols), 13, f"列数应为 13，实际 {len(cols)}: {ln}")
            self.assertEqual(cols[3], "skeleton")
            self.assertIn(cols[11], ("0", "1"))
            int(cols[12])   # max_parent 必须是整数（-1 = 沿用默认父级）

    def test_bones_header_names_the_columns(self):
        """表头行必须是机器可解析的第一行（不以 # 开头），且列名与数据行的取法一致：
        Max 端 mmd2maxReadTable 就按这份名字取列，改了列序不改名字就会在这里先红。"""
        _export(self.pmx, self.d, scale=8.763, filter_useless=True)
        header = self._read("bones.tsv").splitlines()[0]
        self.assertFalse(header.startswith("#"))
        cols = header.split("\t")
        self.assertEqual(cols, ["idx", "bone_name", "parent", "role", "biped_target",
                                "head_x", "head_y", "head_z",
                                "tail_x", "tail_y", "tail_z",
                                "skin_flag", "max_parent"])
        # 表头列数 == 数据行列数，否则就是「写了 13 列只命名了 12 列」
        self.assertEqual(len(cols), len(_rows(self._read("bones.tsv"))[0].split("\t")))

    def test_accessory_parent_hierarchy_keeps_default(self):
        """hierarchy 策略不动父级：max_parent 列全为 -1。"""
        _export(self.pmx, self.d, scale=8.763, filter_useless=True,
                          accessory_parent="hierarchy")
        cols = [ln.split("\t") for ln in _rows(self._read("bones.tsv"))]
        self.assertTrue(all(c[12] == "-1" for c in cols))

    def test_accessory_parent_nearest_picks_geometrically_closest(self):
        """nearest 策略：语义父级很远时，改挂到几何最近的 Biped 节点。

        构造：骨 3 是附属骨，语义父级是 Biped 节点 0（在原点），但骨 3 自己
        贴在 Biped 节点 1（x=100）旁边 —— 应改挂到 1。
        """
        kept = [0, 1, 2]
        old_to_new = {0: 0, 1: 1, 2: 2}
        parents = [-1, -1, 0]           # 骨2 的父是 骨0
        heads = [(0.0, 0.0, 0.0), (100.0, 0.0, 0.0), (99.0, 0.0, 0.0)]
        mapping = {0: "Bip001 Pelvis", 1: "Bip001 Spine2"}
        col = export_obj._accessory_parent_column(
            kept, old_to_new, parents, heads, mapping, "nearest")
        self.assertEqual(col[2], 1, "骨2 应改挂到几何最近的 Bip001 Spine2")
        self.assertEqual(col[0], -1)
        self.assertEqual(col[1], -1)

    def test_weights_not_empty_after_filtering(self):
        info = _export(self.pmx, self.d, scale=8.763, filter_useless=True)
        # 无空权重顶点（export 内部会抛错，能跑到这里说明通过）
        self.assertTrue((self.d / f"{self.pmx.stem}_weights.tsv").is_file())

    def test_weight_that_prints_as_zero_is_not_written_as_a_link(self):
        """#76：`%.6f` 印成 `0.000000` 的权重不写成链路。

        Max 的 Skin 收到权重 0.0 会把这条链路整条丢掉、回读连骨号都不留（海灯节实测：
        332 条 PMX 权重 <5e-7 的对写进去，`links_missing` / `links_read` /
        `link_diff_max` / `link_mismatch_bones` 四行一起为「对形变没有任何影响」的数变红）。
        不写 ≠ 丢掉判据：`skin_rb/zero_links` 专门盯着「表里又冒出这种对」。
        """
        from mmd2max.export_obj import _worker_weights
        start, out = _worker_weights((0, [[(1, 1.0), (2, 3e-7)], [(3, 0.0)],
                                          [(1, 0.5), (2, 0.5)]]))
        self.assertEqual(start, 0)
        self.assertEqual(out[0], "1:1.000000", "亚微克的权重印出来就是 0.000000：不配当链路")
        self.assertEqual(out[1], "", "权重正好 0.0 的不写")
        self.assertEqual(out[2], "1:0.500000\t2:0.500000", "正常权重一对都不许少")
        _export(self.pmx, self.d, scale=8.763, filter_useless=True)
        text = (self.d / f"{self.pmx.stem}_weights.tsv").read_text(encoding="utf-8")
        self.assertNotIn(":0.000000", text,
                         "导出的权重表里出现了 Max 持不住的链路：期望链路数与场景一定会分叉")

    def test_morph_export_converts_offset(self):
        _export(self.pmx, self.d, scale=8.763, filter_useless=True)
        morphs = self._read("morphs.tsv")
        self.assertIn("MORPH\t0\tあ\ta\t2\t1\t1", morphs)
        # 偏移 (0.1,0.2,0.3)*8.763 期望值由 convert_vec 现算，不在测试里抄一遍映射，
        # 否则改了 coords.py 而忘了改测试，这里照样绿。
        from mmd2max.coords import convert_vec
        dx, dy, dz = convert_vec((0.1, 0.2, 0.3), 8.763)
        expect = "V\t0\t%.6f\t%.6f\t%.6f" % (dx, dy, dz)
        self.assertIn(expect, morphs)

    def test_face_winding_is_reversed(self):
        """绕序必须翻：MMD→Max 的映射含一次镜像（det=-1），不翻就正面变背面。"""
        start, lines = export_obj._worker_faces((0, [(0, 1, 2)]))
        self.assertEqual(lines[0], "f 3/3/3 2/2/2 1/1/1")

    def test_physics_export_maps_bone_indices(self):
        _export(self.pmx, self.d, scale=8.763, filter_useless=True)
        phys = self._read("physics.tsv")
        rigid_lines = [l for l in phys.splitlines() if l.startswith("RIGID")]
        joint_lines = [l for l in phys.splitlines() if l.startswith("JOINT")]
        self.assertEqual(len(rigid_lines), 2)
        self.assertEqual(len(joint_lines), 1)
        # 刚体0 引用骨17(左つま先)，在保留骨中仍是 17；刚体1 引用骨5(頭) -> 5
        self.assertTrue(rigid_lines[0].startswith("RIGID\t0\t物理剛体_足\t物理剛体_足_en\t17\t"))
        self.assertTrue(rigid_lines[1].startswith("RIGID\t1\t物理剛体_髪\t物理剛体_髪_en\t5\t"))

    def test_physics_size_scaled_like_position(self):
        """刚体尺寸必须和位置用同一个 scale（早期版本漏乘，代理会大 1/scale 倍）。"""
        _export(self.pmx, self.d, scale=8.763, filter_useless=True)
        phys = self._read("physics.tsv")
        rigid = [l for l in phys.splitlines() if l.startswith("RIGID")][0].split("\t")
        # 列序（0-based）：0=RIGID 1=idx 2=name 3=name_en 4=bone 5=group 6=mask
        #                 7=shape 8..10=size 11..13=pos 14..16=rot(弧度原值)
        #                 17..19=rot_max(Max 度数) 20=mass 21..22=damping
        #                 23=repulsion 24=friction 25=physics_mode
        self.assertEqual(len(rigid), 26)
        self.assertAlmostEqual(float(rigid[8]), 0.5 * 8.763, places=4)
        self.assertAlmostEqual(float(rigid[9]), 0.3 * 8.763, places=4)
        self.assertAlmostEqual(float(rigid[10]), 0.5 * 8.763, places=4)

    def test_physics_rigid_ships_max_degrees_next_to_raw_radians(self):
        """`rigid_rot_max_*` 必须紧跟在 `rigid_rot_*` 后面，且真的等于 Python 算出的度数。

        Max 端不再自己算矩阵（`transpose`/`eu.order`/`eulerAngles … order:` 在本机
        全部实测抛错，见 coords.py 的「旋转」一节），所以这三个格子是代理朝向的唯一
        来源：错位一格 = 把 mass 当角度写进 `node.rotation`，一根都不抛错但每根都歪。
        样例模型的刚体欧拉全 0（make_sample 没写角度），零对零抓不到错位，
        所以这里喂一个非零朝向进去。
        """
        import math
        from types import SimpleNamespace
        from mmd2max.coords import rigid_rotation_max

        rot = (0.3, -1.1, 0.75)      # PMX 弧度：三根轴都非零
        rb = SimpleNamespace(bone_index=0, name="刚体", name_en="rigid", group=1,
                             nocollide_mask=0, shape=1, size=(0.5, 0.3, 0.2),
                             position=(1.0, 2.0, 3.0), rotation=rot, mass=12.5,
                             move_damping=0.9, rotation_damping=0.8, repulsion=0.5,
                             friction=0.6, physics_mode=0)
        model = SimpleNamespace(rigid_bodies=[rb], joints=[])
        lines = export_obj._physics_rows(model, 2.0, {0: 0}, {0})
        cols = lines[0].split("\t")
        row = lines[2].split("\t")
        self.assertEqual(len(cols), len(row), "表头与 RIGID 行的宽度必须一致")
        d = dict(zip(cols, row))
        for i, key in enumerate(("rigid_rot_x", "rigid_rot_y", "rigid_rot_z")):
            self.assertAlmostEqual(float(d[key]), rot[i], places=6)
        want = rigid_rotation_max(rot)
        for i, key in enumerate(("rigid_rot_max_x", "rigid_rot_max_y", "rigid_rot_max_z")):
            self.assertAlmostEqual(float(d[key]), want[i], places=6)
        # 错位守卫：度数不是弧度（π/2 那档），且相邻的 mass 仍在自己那一格
        self.assertGreater(max(abs(v) for v in want), 20.0)
        self.assertAlmostEqual(float(d["rigid_mass"]), 12.5, places=6)
        self.assertEqual(cols[cols.index("rigid_rot_z") + 1], "rigid_rot_max_x")

    def test_joint_columns_count_and_order(self):
        """JOINT 必须满 30 列，且 rigidA/B 在 4/5 位（MaxScript 端按此解析）。"""
        _export(self.pmx, self.d, scale=8.763, filter_useless=True)
        joint = [l for l in self._read("physics.tsv").splitlines()
                 if l.startswith("JOINT")][0].split("\t")
        self.assertEqual(len(joint), 30)
        self.assertEqual(joint[3], "0")   # joint_type
        self.assertEqual(joint[4], "0")   # rigidA
        self.assertEqual(joint[5], "1")   # rigidB

    def test_biped_map_uses_new_indices(self):
        _export(self.pmx, self.d, scale=8.763, filter_useless=True)
        bmap = self._read("biped_map.tsv")
        # 新序号 0 -> Bip001（センター），且 IK(原23)/哑骨(原24) 不应出现在映射
        self.assertIn("0\tBip001\tセンター", bmap)
        self.assertNotIn("左足IK", bmap)
        self.assertNotIn("物理用ダミー", bmap)

    def test_full_mode_keeps_all_bones(self):
        info = _export(self.pmx, self.d, scale=8.763,
                                 filter_useless=False)
        bones = _rows(self._read("bones.tsv"))
        self.assertEqual(len(bones), 25)
        self.assertEqual(info["kept_bone_count"], 25)


class ToeSkinTests(unittest.TestCase):
    """#35 脚尖改绑：源文件把整只鞋绑在足首上时，按足轴把鞋头那一截划给 Toe0。

    全部用合成数据（不碰真实模型，换机器也跑得动）：骨骼链 0=Calf 1=Foot 2=Toe0，
    顶点先按 Max 坐标摆在足轴上，再用 convert_vec 的逆映射换回 PMX 坐标喂进函数。
    这一组里最有价值的是 test_band_is_relative —— 第一版把过渡带写成 2.0「厘米」，
    而 heads 是已乘 scale 的导出坐标，实跑量出来 2.0 导出单位 = 200cm，整只脚全落进
    过渡带、改绑一根没动（日志里只留下一句「足轴只有 0.12cm」，差点被当成模型问题）。
    """

    CALF, FOOT, TOE = 0, 1, 2
    # Max 坐标（Z-up）：踝在 z=5，趾关节往前 10、往下 4，膝在踝正上方 35
    HEADS = [(0.0, 0.0, 40.0), (0.0, 0.0, 5.0), (0.0, -10.0, 1.0)]
    MAPPING = {CALF: "Bip001 L Calf", FOOT: "Bip001 L Foot", TOE: "Bip001 L Toe0"}
    PARENTS = [-1, CALF, FOOT]
    OLD_TO_NEW = {0: 0, 1: 1, 2: 2}

    @staticmethod
    def _to_pmx(max_point, scale: float = 1.0):
        """convert_vec 的逆：max (a,b,c) -> pmx (a, c, b)，再除回 scale。"""
        return (max_point[0] / scale, max_point[2] / scale, max_point[1] / scale)

    def _axis(self, heads):
        fh, th = heads[self.FOOT], heads[self.TOE]
        raw = [th[i] - fh[i] for i in range(3)]
        span = sum(v * v for v in raw) ** 0.5
        return th, [v / span for v in raw], span

    def _split(self, offsets, scale: float = 1.0, heads=None, mapping=None,
               parents=None, weights=None):
        """offsets 是「沿足轴离趾关节多远」的列表（+ = 朝鞋尖）。"""
        heads = heads if heads is not None else self.HEADS
        toe_head, axis, span = self._axis(heads)
        points = [tuple(toe_head[i] + t * axis[i] for i in range(3)) for t in offsets]
        positions = [self._to_pmx(p, scale) for p in points]
        deform = list(weights) if weights is not None else [[(self.FOOT, 1.0)] for _ in offsets]
        before = [list(w) for w in deform]
        out, notes = export_obj._split_toe_weights(
            deform, positions, scale, heads,
            parents if parents is not None else self.PARENTS,
            mapping if mapping is not None else self.MAPPING, self.OLD_TO_NEW)
        return out, notes, span, before

    @staticmethod
    def _w(weights, bone):
        return next((w for b, w in weights if b == bone), 0.0)

    def test_split_follows_the_toe_plane(self):
        span = self._axis(self.HEADS)[2]
        band = export_obj.TOE_SPLIT_BAND
        out, _notes, _s, _before = self._split([3 * band * span, band * span, 0.0,
                                                -band * span, -3 * band * span])
        self.assertAlmostEqual(self._w(out[0], self.TOE), 1.0, places=9)
        self.assertEqual(self._w(out[0], self.FOOT), 0.0, "整段划过去的顶点不该再留足首一项")
        self.assertAlmostEqual(self._w(out[1], self.TOE), 1.0, places=9,
                               msg="刚好落在过渡带前缘 = 全给 Toe0")
        self.assertAlmostEqual(self._w(out[2], self.TOE), 0.5, places=9,
                               msg="分界 0.5/0.5 必须正好在趾关节平面上")
        self.assertAlmostEqual(self._w(out[3], self.TOE), 0.0, places=9)
        self.assertEqual(out[4], [(self.FOOT, 1.0)], "带后缘之外一根顶点都不许动")
        self.assertEqual(self._w(out[4], self.TOE), 0.0, "脚踝那一截必须留在足首")

    def test_weight_total_is_conserved(self):
        span = self._axis(self.HEADS)[2]
        band = export_obj.TOE_SPLIT_BAND
        out, _notes, _s, before = self._split(
            [t * band * span for t in (3, 1, 0.5, 0, -0.5, -1, -3)])
        self.assertEqual(sum(self._w(o, self.FOOT) + self._w(o, self.TOE) for o in out),
                         sum(sum(w for _b, w in o) for o in before),
                         "改绑只是把足首的一项分给趾骨，总量必须守恒")
        for o in out:
            self.assertAlmostEqual(sum(w for _b, w in o), 1.0, places=9,
                                   msg="Skin 不接受合计不为 1 的顶点")

    def test_switch_off_is_identical_to_source(self):
        original = export_obj.TOE_SPLIT
        try:
            export_obj.TOE_SPLIT = False
            out, notes, _span, before = self._split([1.0, 0.0, -1.0])
            self.assertEqual(out, before)
            self.assertEqual(notes, [])
        finally:
            export_obj.TOE_SPLIT = original

    def test_band_is_relative(self):
        """同一只脚放大 10 倍（scale 与 heads 一起放大）必须给出同样的分配。

        这就是「带用比例不用厘米」的可执行版本：绝对数一旦回到函数里，这条立刻红。
        """
        band = export_obj.TOE_SPLIT_BAND
        heads10 = [tuple(v * 10.0 for v in h) for h in self.HEADS]
        offsets = [t * band * self._axis(self.HEADS)[2] for t in (3, 1, 0, -1, -3)]
        out1, _n1, _s1, _b1 = self._split(offsets)
        out10, _n2, _s2, _b2 = self._split([o * 10.0 for o in offsets],
                                           scale=10.0, heads=heads10)
        for a, b in zip(out1, out10):
            self.assertAlmostEqual(self._w(a, self.TOE), self._w(b, self.TOE), places=9)

    def test_skips_when_author_already_bound_the_toe(self):
        """源文件自己给过 Toe0 顶点 ⇒ 作者的权重优先，不插手（换模型验收要的是这条）。"""
        n = 10
        weights = [[(self.FOOT, 0.6), (self.TOE, 0.4)] for _ in range(n)]
        out, notes, _span, before = self._split([1.0] * n, weights=weights)
        self.assertEqual(out, before)
        self.assertEqual(len(notes), 1)
        self.assertIn("源文件已给它", notes[0])

    def test_skips_when_foot_axis_cannot_be_trusted(self):
        """趾关节贴着踝 ⇒ 足轴量不出方向，跳过而不是拿噪声当方向分权重。"""
        heads = [self.HEADS[0], self.HEADS[1], (0.0, -0.02, 5.0)]
        out, notes, _span, before = self._split([1.0, 0.0, -1.0], heads=heads)
        self.assertEqual(out, before)
        self.assertIn("方向不可信", notes[0])

    def test_skips_when_sides_do_not_pair(self):
        """Biped 目标前缀对不上（左趾挂右足）宁可不改，也不要把权重搬到另一侧。"""
        mapping = dict(self.MAPPING)
        mapping[self.TOE] = "Bip001 R Toe0"
        out, notes, _span, before = self._split([1.0, 0.0, -1.0], mapping=mapping)
        self.assertEqual(out, before)
        self.assertEqual(notes, [])


class BreastAimTests(unittest.TestCase):
    """胸部骨的 tail 必须指向网格里量出来的乳头，不是 PMX 那根竖直牙签。

    判据来自用户 2026-11-03 的截图：「胸部的骨骼应该表现胸部的指向方向，应该指向乳头」。
    PMX 里 左胸/右胸 的 tail_offset 是默认值（head→tail 沿 +Z 一整根 scale），画出来
    就是胸腔上两根向上的牙签。这里用合成小样本把三条分支钉死：
    取的是**鼓面顶**（最外 25% 的均值）而不是质心、有后代的骨不动、点数不够不动。
    """

    # scale=1 时 convert_vec((x,y,z)) = (x,z,y)：PMX 的 -Z（屏幕外）= Max 的 -y（角色前）。
    HEAD_PMX = (1.0, 10.0, 0.0)
    HEAD_MAX = (1.0, 0.0, 10.0)
    STUB_MAX = (1.0, 0.0, 17.6563)      # PMX 默认尾：竖直向上，正是用户看到的牙签

    def _patch(self):
        """12 个「鼓面周围皮肤」点 + 4 个「乳头」点（PMX 坐标）。

        皮肤的 z ∈ {-0.5, 0} 靠后，乳头的 z = -2.0/-2.1 明显更靠前 ⇒ 顶点在 Max 里
        y = -2.05，比质心（-0.7）靠前 1.35：两个数字差得足够远，任何「拿质心当乳头」
        的改动都会被这条断言抓到。
        """
        pts = []
        for x in (0.5, 1.0, 1.5):
            for y in (9.5, 10.5):
                for z in (-0.5, 0.0):
                    pts.append((x, y, z))
        pts += [(0.8, 10.0, -2.0), (1.2, 10.0, -2.0),
                (1.0, 9.7, -2.1), (1.0, 10.3, -2.1)]
        return pts

    def _run(self, scale=1.0, parents=None, deform=None, min_region=6,
             names=("右胸",), heads=None, tails=None):
        heads = list(heads or [self.HEAD_MAX])
        tails = list(tails or [self.STUB_MAX])
        notes = export_obj._aim_breast_bones(
            list(names), ["" for _ in names],
            list(parents if parents is not None else [-1]),
            deform if deform is not None else [[(0, 1.0)] for _ in self._patch()],
            self._patch(), scale, heads, tails, min_region=min_region)
        return tails[0], notes

    def test_aim_is_the_apex_not_the_centroid(self):
        tail, notes = self._run()
        self.assertAlmostEqual(tail[0], 1.0, places=9)
        self.assertAlmostEqual(tail[1], -2.05, places=9)   # 鼓面顶，不是 -0.7 的质心
        self.assertAlmostEqual(tail[2], 10.0, places=9)    # 与 head 同高：水平指向
        self.assertNotAlmostEqual(tail[2], self.STUB_MAX[2], places=1)  # 牙签已消失
        self.assertEqual(len(notes), 1)
        self.assertIn("右胸", notes[0])
        self.assertIn("16点", notes[0])

    def test_scale_is_applied_to_region_points(self):
        """区域点必须乘 scale 再和 heads 比：PMX 坐标与导出坐标差 scale 倍。

        这里 scale=10，同一个合成胸要落在 (10, -20.5, 100)。漏乘 scale 会得到
        (1, -2.05, 10) —— 差 10 倍，正是 _remap_humanoid 注释里记过的那类旧错。
        """
        tail, _ = self._run(scale=10.0, heads=[(10.0, 0.0, 100.0)],
                            tails=[(10.0, 0.0, 176.563)])
        self.assertAlmostEqual(tail[0], 10.0, places=8)
        self.assertAlmostEqual(tail[1], -20.5, places=7)
        self.assertAlmostEqual(tail[2], 100.0, places=8)

    def test_bone_with_kept_child_is_left_alone(self):
        """有保留后代 ⇒ tail 已经指向子关节（真链），覆盖它就是把骨骼链画错。"""
        # 1 号骨（胸先）的父是 0 号 ⇒ 0 号有后代
        tail, notes = self._run(parents=[-1, 0], names=("右胸", "胸先"),
                                heads=[self.HEAD_MAX, (1.0, 0.0, 12.0)],
                                tails=[self.STUB_MAX, (1.0, 0.0, 13.0)])
        self.assertEqual(tail, self.STUB_MAX)
        self.assertEqual(notes, [])

    def test_tiny_region_is_left_alone(self):
        """点数不足（<min_region）宁可不改：三五个点编不出可信方向。"""
        pts = self._patch()[:4]
        tail, notes = self._run(deform=[[(0, 1.0)] for _ in pts], min_region=6)
        self.assertEqual(tail, self.STUB_MAX)
        self.assertEqual(notes, [])

    def test_weak_weights_do_not_count_as_the_region(self):
        """主导骨权重 <0.5 的点不算这块皮肤：否则整件衣服的顶点会把方向拽偏。"""
        deform = [[(0, 0.4), (1, 0.6)] for _ in self._patch()]
        tail, notes = self._run(deform=deform)
        self.assertEqual(tail, self.STUB_MAX)
        self.assertEqual(notes, [])

    def test_torso_bones_are_never_candidates(self):
        """「胸」字也出现在躯干名里，那些骨的 tail 由 Biped 链决定，绝不能改。"""
        for name in ("上半身", "下半身", "胸腔", "胸郭", "胸骨"):
            self.assertFalse(export_obj._is_breast_bone(name, ""), name)
        for name in ("左胸", "右胸", "胸", "バスト"):
            self.assertTrue(export_obj._is_breast_bone(name, ""), name)
        self.assertTrue(export_obj._is_breast_bone("Bust", "bust"))


class FramingBandTests(unittest.TestCase):
    """两张特写的取景框必须由**量出来的 subject 骨**决定，三根轴都算（#70 胸、#71 颈）。

    第 35 轮的账：z 写在 .ms 里，是「场景高度 × 0.58~0.72」，那两个比例是对着 PMX 标称
    身高 160 定的，而分母是实测场景高 140.513 ⇒ 整只带低了 4cm，chest_front 里一根胸骨
    都没有，而 fill_pct 63% 全绿（填充率量的是框自己，框里没骨也照样满）。
    第 36 轮的账：z 改成量的了，x/y 还拿「场景包围盒 ±10cm」凑 —— 框的前后中心被身后的
    裙摆拽到 y=+9.8，而 #69 的收框是朝框中心砍的，砍完只剩 y -0.4..20.0，骨在
    -11.75..-7.24：侧视那张里根本没有骨，而当时那两行 z 判据全绿。
    现在三根轴一起量、余量都取**网格实测高**的比例（比例对比例，换模型换 scale 不必改数，
    正是那条「阈值一律用比例、不许用绝对厘米」的老规矩）。
    #71 的账：颈部那两张图用的是同一套机制，但量的是另一组骨（颈链三节），坏法一模一样 ——
    旧的 `h × 0.55..0.98` 在香菱上是 60.4cm 高的一条带，比颈链实际跨度 27.5cm 宽一倍多，
    下沿低到骨盆，于是「颈部特写」里大半张图是躯干。两张都测才叫共用机制。
    """

    # PMX 坐标：convert_vec 的 Max z = PMX y，所以网格高 = (y 跨度) × scale。
    PTS = [(0.0, 0.0, 0.0), (0.0, 80.0, 0.0), (0.0, 160.0, 0.0)]
    HEADS = [(4.555, -7.2398, 105.7325), (-4.555, -7.2398, 105.7325)]
    TAILS = [(4.9157, -11.7451, 105.2253), (-4.9157, -11.7451, 105.2253)]
    NAMES = ["右胸", "左胸"]
    # 颈链三节的端点 = 香菱真导出（bones.tsv 的 Max cm），第 4 行是**陷阱**：
    # `左手首` 名字里也有「首」，但它映射到 Bip001 L Hand，不属于颈部。
    NECK_HEADS = [(0.000030, -4.921643, 100.760711),
                  (0.000069, -0.036633, 116.328685),
                  (0.000088, -1.038409, 122.964599),
                  (18.0, -6.0, 92.0)]
    NECK_TAILS = [(0.000069, -0.036633, 116.328685),
                  (0.000088, -1.038409, 122.964599),
                  (-3.842957, -6.766848, 128.286294),
                  (22.0, -6.0, 92.0)]
    NECK_NAMES = ["上半身2", "首", "頭", "左手首"]
    NECK_MAP = {0: "Bip001 Spine2", 1: "Bip001 Neck", 2: "Bip001 Head",
                3: "Bip001 L Hand"}
    # 颈骨极值：三节全进，`頭` 的 tail 把 x 拉到 -3.84、y 拉到 -6.77、z 拉到 128.29。
    NECK_MIN = (-3.842957, -6.766848, 100.760711)
    NECK_MAX = (0.000088, -0.036633, 128.286294)

    def _band(self, scale=1.0, names=None, pts=None):
        names = list(self.NAMES) if names is None else names
        return export_obj._chest_band(
            names, ["" for _ in names],
            [tuple(c * scale for c in h) for h in self.HEADS],
            [tuple(c * scale for c in t) for t in self.TAILS],
            pts if pts is not None else self.PTS, scale)

    def _neck(self, scale=1.0, mapping=None, heads=None):
        """颈链按 scale 摆好（骨端点与网格都用同一具模型的单位），返回量出来的带。"""
        src = list(self.NECK_HEADS) if heads is None else heads
        return export_obj._neck_band(
            [tuple(c * scale for c in h) for h in src],
            [tuple(c * scale for c in t) for t in self.NECK_TAILS],
            self.NECK_MAP if mapping is None else mapping,
            {i: i for i in range(len(src))},
            self.PTS, scale)

    def test_box_wraps_the_bones_on_all_three_axes(self):
        band = self._band()
        self.assertEqual(band.b_min, (-4.9157, -11.7451, 105.2253))
        self.assertEqual(band.b_max, (4.9157, -7.2398, 105.7325))
        pad_xy = 160.0 * export_obj.BOX_PAD_XY
        pad_z = 160.0 * export_obj.BAND_PAD_Z
        # 每一轴都两头各外扩一段（不是只给上沿留余量）
        self.assertAlmostEqual(band.lo[0], -4.9157 - pad_xy, places=6)
        self.assertAlmostEqual(band.hi[0], 4.9157 + pad_xy, places=6)
        self.assertAlmostEqual(band.lo[1], -11.7451 - pad_xy, places=6)
        self.assertAlmostEqual(band.hi[1], -7.2398 + pad_xy, places=6)
        self.assertAlmostEqual(band.lo[2], 105.2253 - pad_z, places=6)
        self.assertAlmostEqual(band.hi[2], 105.7325 + pad_z, places=6)
        for i in range(3):
            self.assertLessEqual(band.lo[i], band.b_min[i])
            self.assertGreaterEqual(band.hi[i], band.b_max[i])

    def test_the_neck_box_wraps_the_neck_chain_on_all_three_axes(self):
        """#71：颈链三节的 head+tail 全进极值，框按同一对比例外扩。

        极值取的是**三节合起来**的范围：`頭` 的 tail（-3.84/-6.77/128.29）三根轴都在边缘，
        只按 head 量就会把头顶裁掉 —— 那正是胸部 `test_both_endpoints_count` 测过的错。
        """
        band = self._neck()
        self.assertEqual(band.b_min, self.NECK_MIN)
        self.assertEqual(band.b_max, self.NECK_MAX)
        pad_xy = 160.0 * export_obj.BOX_PAD_XY
        pad_z = 160.0 * export_obj.BAND_PAD_Z
        for i in range(3):
            self.assertAlmostEqual(band.lo[i], self.NECK_MIN[i] - (pad_z if i == 2 else pad_xy),
                                   places=6)
            self.assertAlmostEqual(band.hi[i], self.NECK_MAX[i] + (pad_z if i == 2 else pad_xy),
                                   places=6)

    def test_the_neck_is_recognised_by_the_biped_target_not_by_name(self):
        """`左手首`（腕）名字里也有「首」：按名认骨会把手腕拉进颈部，框被拽到 x=+22。

        香菱实测 左手首/右手首/左足首/右足首 四根都含「首」。颈部只认 `NECK_TARGETS`
        那三个 Biped 目标 —— 与 `_center_neck` 挪骨用的是同一张表，所以「颈骨摆到哪」
        与「颈部框在哪」不可能分叉。
        """
        band = self._neck()
        self.assertEqual(band.b_max[0], self.NECK_MAX[0],
                         "x 上沿被手腕拽正了：颈部特写会变成半个人")
        self.assertLess(band.b_max[0], 1.0)
        self.assertEqual(band.b_min[2], self.NECK_MIN[2],
                         "z 下沿被手腕拽低：特写下沿该是 上半身2 的头，不是手腕")

    def test_a_model_without_a_neck_chain_has_no_band(self):
        """映射表里没有那三节（非 Biped 骨架 / 只有 首 没有 頭）⇒ None ⇒ meta 写空串。"""
        self.assertIsNone(self._neck(mapping={3: "Bip001 L Hand"}))
        self.assertIsNone(self._neck(mapping={}))

    def test_the_band_follows_a_bone_moved_earlier_in_the_same_pass(self):
        """`_neck_band` 读的是**当下**的 heads —— 所以它必须排在 `_center_neck` 之后。

        先框后挪就等于框里那节骨被自己推出去（#70 那一类分叉）。_center_neck 需要网格才能
        算颈轴，这里直接把某一节挪到新的 y/z 再量：极值必须跟着新位置走，否则
        build_plan 里那句「排在 _center_neck 之后」就只是在注释里排了个序。
        """
        moved = list(self.NECK_HEADS)
        moved[2] = (0.0, -30.0, 130.0)          # 頭 的头挪到 y=-30、z=130（模拟被改过之后）
        band = self._neck(heads=moved)
        self.assertEqual(band.b_min[1], -30.0,
                         "颈部极值没跟着 heads 走：取景段量的是挪之前的骨")
        self.assertEqual(band.b_max[2], 130.0)
        # 没被挪的那一节仍按原值参与（挪一节不许把别节的读数也算错）
        self.assertEqual(band.b_min[2], self.NECK_MIN[2])
        self.assertEqual(band.b_min[0], self.NECK_MIN[0])

    def test_the_frame_is_centered_on_the_bones_not_on_the_scene(self):
        """#69 的收框朝**框中心**砍水平跨度，所以框必须以骨为中，否则砍完就把骨裁出去。

        第 36 轮就是这么坏的：那只框的中心是场景包围盒的中心（被身后的裙摆拽到 y=+9.8），
        骨在 -11.75..-7.24 全部偏在一侧，收完就在框外。这条断言钉的是「中心 = 骨中心」，
        与余量给多少无关 —— 余量可以调，中心不能跑。两张特写共用 `_framing_band`，各钉一次。
        """
        for band in (self._band(), self._neck()):
            for i in range(3):
                self.assertAlmostEqual((band.lo[i] + band.hi[i]) / 2.0,
                                       (band.b_min[i] + band.b_max[i]) / 2.0, places=9)

    def test_pad_follows_the_measured_height_not_an_absolute_cm(self):
        """scale ×10 时框也恰好 ×10：余量是网格高的比例，写死厘米会在大模型上不够用。"""
        for small, big in ((self._band(scale=1.0), self._band(scale=10.0)),
                           (self._neck(scale=1.0), self._neck(scale=10.0))):
            for i in range(3):
                self.assertAlmostEqual(big.lo[i], small.lo[i] * 10.0, places=6)
                self.assertAlmostEqual(big.hi[i], small.hi[i] * 10.0, places=6)

    def test_no_breast_bone_means_no_band(self):
        """认不出胸骨的模型不许编一个框出来：写空串，让 Max 退回场景带并**报明**。"""
        self.assertIsNone(self._band(names=["センター", "腰"]))

    def test_no_mesh_means_no_band(self):
        """没有顶点就量不出网格高，比例余量没有分母 —— 同样退成空而不是猜一个数。"""
        self.assertIsNone(self._band(pts=[]))
        self.assertIsNone(export_obj._neck_band(self.NECK_HEADS, self.NECK_TAILS,
                                                self.NECK_MAP, {0: 0, 1: 1, 2: 2}, [], 1.0))

    def test_both_endpoints_count_not_only_the_head(self):
        """tail 是量出来的乳头点，比 head 更靠外也更低：min 必须取到它，只按 head 会漏。"""
        band = self._band()
        self.assertEqual(band.b_min[2], self.TAILS[0][2])
        self.assertEqual(band.b_min[1], self.TAILS[0][1])
        self.assertNotEqual(band.b_min[2], self.HEADS[0][2])

    def test_english_names_are_recognised_too(self):
        """PMX 的胸骨名从「右胸」到 bustR 都有：认名的规则与 _aim_breast_bones 同一条，
        所以「被改成指向乳头的骨」和「取景框框住的骨」不可能分叉。"""
        band = export_obj._chest_band(
            ["bustR", "腰"], ["", ""], self.HEADS[:1], self.TAILS[:1], self.PTS, 1.0)
        self.assertIsNotNone(band)
        self.assertAlmostEqual(band.b_max[2], self.HEADS[0][2], places=6)

    def test_meta_lines_are_the_box_then_the_bones_in_axis_order(self):
        """meta 那 12 行的**顺序**就是契约：tools/obj_run._ruler 按「同一轴的两端
        挨着」切片，写成先三根轴的 lo 再三根轴的 hi 就会把 y 的极值当成 x 的尺。
        两张特写共用 `band_meta_pairs`，所以前缀以外必须逐字同形 —— 键名由 obj_run 的
        `box_keys/bone_keys` 一处生成，这里拿它当尺对撞，防的是两边各拼一套词。"""
        sys.path.insert(0, str(PROJECT_ROOT / "tools"))
        import obj_run
        for prefix in ("chest", "neck"):
            with self.subTest(prefix=prefix):
                keys = [line.split("\t")[0]
                        for line in export_obj.band_meta_pairs(prefix, self._band())]
                self.assertEqual(keys, [
                    f"{prefix}_box_x_lo", f"{prefix}_box_x_hi",
                    f"{prefix}_box_y_lo", f"{prefix}_box_y_hi",
                    f"{prefix}_box_z_lo", f"{prefix}_box_z_hi",
                    f"{prefix}_bone_x_min", f"{prefix}_bone_x_max",
                    f"{prefix}_bone_y_min", f"{prefix}_bone_y_max",
                    f"{prefix}_bone_z_min", f"{prefix}_bone_z_max"])
                self.assertEqual(keys[:6], list(obj_run.box_keys(prefix)))
                self.assertEqual(keys[6:], list(obj_run.bone_keys(prefix)))

    def test_meta_lines_are_all_present_and_empty_without_a_band(self):
        """量不出 subject 时 12 行**照样要在**：键缺了 read_meta 会中止（那是好事），
        但导出侧的规矩是「写空串」，Max 与判据表都按空串走退回路径。"""
        for prefix in ("chest", "neck"):
            lines = export_obj.band_meta_pairs(prefix, None)
            self.assertEqual(len(lines), 12)
            for line in lines:
                self.assertTrue(line.endswith("\t"), line)


class _FakeVertex:
    def __init__(self, weights):
        self.weights = weights


class _FakeBone:
    def __init__(self, name, flags=0x0002):
        self.name = name
        self.flags = flags


class _FakeRigid:
    def __init__(self, bone_index):
        self.bone_index = bone_index


class _FakeJoint:
    def __init__(self, rigid_a, rigid_b):
        self.rigid_a = rigid_a
        self.rigid_b = rigid_b


class _FakeModel:
    def __init__(self, bones, weights_per_bone=(), rigid_bones=(), joints=()):
        self.bones = bones
        # 每根骨给一个顶点：权重 >0 才写出这对 (骨序号, 权重)
        self.vertices = [_FakeVertex([(i, w)] if w > 0 else [])
                         for i, w in enumerate(weights_per_bone)]
        self.rigid_bodies = [_FakeRigid(b) for b in rigid_bones]
        self.joints = list(joints)


class GuideBoneRuleTests(unittest.TestCase):
    """「辅助骨骼」判据：四者皆无才算纯辅助（Biped 目标 / 蒙皮 / 刚体 / IK）。

    用户 2026-11-03 点名要能关掉它们：「加一个按钮开启或者关闭保留这些辅助骨骼
    （センター2 WeaponR 右親指先 右小指先 右人指先 等，左边的也一样）」。
    这条判据是在香菱 356 根骨上量出来的（可丢 63 / 保留 293），
    名单与用户点名的骨逐根对得上。
    """

    def _model(self):
        bones = [_FakeBone("センター"), _FakeBone("右腕"), _FakeBone("センター2"),
                 _FakeBone("足IK親", 0x0020), _FakeBone("頭"), _FakeBone("前摆_8_0")]
        return _FakeModel(bones, weights_per_bone=(0.0, 1.0, 0.0, 0.0, 0.0, 0.0),
                          rigid_bones=(4,))

    def test_only_inert_bones_are_guides(self):
        guides = semantic.guide_bone_indices(self._model(),
                                             {0: "Bip001"})
        self.assertEqual(guides, {2, 5})

    def test_each_of_the_four_reasons_keeps_a_bone(self):
        mapping = {0: "Bip001"}
        guides = semantic.guide_bone_indices(self._model(), mapping)
        self.assertNotIn(0, guides)   # Biped 目标
        self.assertNotIn(1, guides)   # 有蒙皮权重
        self.assertNotIn(3, guides)   # IK 骨
        self.assertNotIn(4, guides)   # 被刚体引用

    def test_joint_only_bone_still_counts_as_physics(self):
        """刚体挂在关节上、骨本身没直接挂刚体，也算「物理要用」。"""
        model = self._model()
        model.rigid_bodies = [_FakeRigid(5)]
        model.joints = [_FakeJoint(0, 0)]
        self.assertNotIn(5, semantic.guide_bone_indices(model, {}))

    def test_no_biped_mapping_argument_is_not_a_blanket_drop(self):
        """不传 biped_mapping 时按「无 Biped 目标」判：仍须靠权重/刚体/IK 拦住别的骨。"""
        guides = semantic.guide_bone_indices(self._model())
        self.assertIn(0, guides)      # 这次没给映射，センター 落进辅助（调用方必须传）
        self.assertNotIn(1, guides)
        self.assertNotIn(3, guides)
        self.assertNotIn(4, guides)


class GuideSwitchTests(unittest.TestCase):
    """开关本身：默认开 = 逐字不变；关掉只丢辅助骨，权重一根不丢。

    build_plan 在真模型上各要跑几秒，两个 plan 在这一组测试里是固定的，
    所以缓存在类上跑一次（同一模型的两种开关状态，不存在跨用例的可变性）。
    """

    MODEL = Path(r"C:\Users\Administrator\Desktop\香菱\香菱.pmx")
    SCALE = 7.6563
    _shared = None

    def setUp(self):
        if not self.MODEL.is_file():
            self.skipTest(f"真实模型不在：{self.MODEL}")

    @classmethod
    def _plans(cls):
        if cls._shared is None:
            cls._shared = (
                export_obj.build_plan(cls.MODEL, scale=cls.SCALE, keep_guides=True),
                export_obj.build_plan(cls.MODEL, scale=cls.SCALE, keep_guides=False))
        return cls._shared

    def _weights(self, plan):
        """{骨名: 该骨总权重}，用来证明「丢的是没权重的骨」。"""
        return {name: plan.totals[i] for i, name in enumerate(plan.bone_names)}

    def test_default_argument_is_keep(self):
        """不传参 = 保留辅助骨：历轮基线与 GUI 默认都是这个行为。"""
        on, _ = self._plans()
        plain = export_obj.build_plan(self.MODEL, scale=self.SCALE)
        self.assertTrue(plain.keep_guides)
        self.assertEqual(plain.bone_names, on.bone_names)
        self.assertEqual(plain.bones_lines, on.bones_lines)

    def test_off_is_a_subset_in_the_same_order(self):
        on, off = self._plans()
        kept_off = set(off.bone_names)
        self.assertEqual([n for n in on.bone_names if n in kept_off], off.bone_names)
        self.assertLess(len(off.bone_names), len(on.bone_names))

    def test_dropped_bones_are_exactly_the_guides(self):
        on, off = self._plans()
        lost = [n for n in on.bone_names if n not in set(off.bone_names)]
        self.assertTrue(lost, "关掉开关必须真的少几根")
        off_names = set(off.bone_names)
        for name in ("センター2", "WeaponL", "WeaponR", "右親指先", "左人指先"):
            self.assertIn(name, lost, name)
        # 这两根在 useful 模式里早已被 control 角色剔掉（不是这个开关的功劳），
        # 但用户的判据是「场景里别有我不动的东西」，所以断言它们在关掉后确实不在。
        for name in ("全ての親", "操作中心"):
            self.assertNotIn(name, off_names, name)
        # 用户会动的骨一根都不能少
        for name in ("センター", "左胸", "右胸", "上半身", "左腕", "右腕", "頭"):
            self.assertNotIn(name, lost, name)
            self.assertIn(name, off_names, name)

    def test_no_weighted_bone_is_dropped(self):
        on, off = self._plans()
        before = self._weights(on)
        lost = {n for n in before if n not in set(off.bone_names)}
        self.assertTrue(all(before[n] == 0.0 for n in lost),
                        f"有权重的骨被丢了：{[n for n in lost if before[n] > 0]}")

    def test_vertex_weight_sum_is_conserved(self):
        """权重归并到保留祖先：每个顶点的权重合计不能变（绝不产生空权重）。"""
        on, off = self._plans()
        self.assertEqual(len(on.deform), len(off.deform))
        for i, (a, b) in enumerate(zip(on.deform, off.deform)):
            self.assertAlmostEqual(sum(w for _, w in a), sum(w for _, w in b),
                                   places=6, msg=f"顶点 {i} 权重合计变了")
            self.assertTrue(b, f"顶点 {i} 变成空权重")

    def test_indices_stay_in_range_after_renumbering(self):
        _on, off = self._plans()
        n = len(off.kept)
        for weights in off.deform:
            for bone_index, _w in weights:
                self.assertTrue(0 <= bone_index < n)


if __name__ == "__main__":
    unittest.main(verbosity=2)

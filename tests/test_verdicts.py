"""判据通道（M2b）的契约测试：规格表怎么装配、Max 那一侧能报哪些行。

这一层是「尺」，所以它自己必须先被证过。三类事故各有一组钉子：

1. **表本身**：线格式 v2 的读写（行可以比列名短 —— limbs.tsv 就靠这条）、字段里混进
   制表符、同一个 (phase, check, subject) 出现两次（拼表时两条规则抢一个读数，
   另一条形同不存在）。
2. **两侧通道对撞**：`.ms` 里少一个 `mmd2maxReport` 就是「这条判据永远没人填」，
   多一个就是「报告里出现规格表不认识的行」。两种都必须红 —— 这正是本项目那条
   老教训的反例做法：判据的持有方与被验收的方不能共用一份事实。
3. **Max 不许判定**：报告文件只许有 `phase/check/subject/actual` 四列。Max 一旦写出
   `PASS`/`FAIL`/`成功`，Python 这边就变成「读 Max 的结论」，而结论是它自己该算的。
4. **锁必须有来历**：每个 `lock` 数字都要能在被定价那一轮的基线日志里逐字找到，
   并且只在那具模型上生效 —— 换模型退化成 `report`，否则就是拿香菱的读数判克雷薇。
"""
import math
import re
import sys
import unittest
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from mmd2max import passes, targets, verdicts              # noqa: E402
from tests import required_files                           # noqa: E402

SCRIPT = (PROJECT_ROOT / "scripts" / "max_import_obj.ms").read_text(encoding="utf-8")
EXPORT_DIR = PROJECT_ROOT / "test_runs" / "香菱"
# 锁的数字全来自这一轮实跑；源码注释只能说明「为什么」，不能证明「数是抄来的还是
# 量来的」。基线日志才是这两个数字来源的凭据 —— 它现在在仓库里，所以缺件走
# `required_files.require()` 报红（以前只能 skip，见那个模块的 docstring）。
PRICED_LOG = PROJECT_ROOT / "baselines" / "round20_objrun.log"
# 第二具模型的定价轮（#76-B 的颈段）：锁的数字必须能在**它自己那具模型**的冻结日志里读到。
# 用第 59 轮（方向容差=3.0，即出厂设置下第一轮全绿）而不是第 57 轮：57 轮的 Max 侧日志被
# 58 轮的 DIR_TOL 实验覆盖了，冻结件里存的是实验内容 —— 名字对、内容错，拿它当凭据就是
# 把「实验轮读数」写成「出厂轮读数」。
SEGMENT_PRICED_LOG = PROJECT_ROOT / "baselines" / "round59_香菱-海灯节_objrun.log"
# 第 58 轮 = 把 DIR_TOL 压到 0.2 去试那一轮：它是「转角真的试过并且没用」的唯一凭据。
SEGMENT_FORCED_LOG = PROJECT_ROOT / "baselines" / "round58_香菱-海灯节_objrun.log"
# 第 57 轮的 obj_run 输出（出厂容差下量到同一个数的第一轮）。
SEGMENT_FIRST_STDOUT = PROJECT_ROOT / "baselines" / "round57_haideng_zerolinks.log"

# fixture 的肢段表：三种 mode 各一行，覆盖 build_spec 里全部「按 mode 派生主体」的分支。
# 脚段必须带齐骨盒回读（M4c）要的六列：少一列 `box_targets` 就抛，fixture 也就装不出
# 全套通道 —— 那正是要它在这里说话的原因（CHANNELS 缺类 = .ms 多报没人判的行不被发现）。
LIMBS = [
    {"parent_target": "Bip001 Spine", "child_target": "Bip001 Spine1",
     "desired_len": "8.325677", "mode": "link"},
    {"parent_target": "Bip001 L Finger0", "child_target": "Bip001 L Finger01",
     "desired_len": "2.344292", "mode": "link"},
    {"parent_target": "Bip001 L Thigh", "child_target": "Bip001 L Calf",
     "desired_len": "36.382676", "mode": "leg"},
    {"parent_target": "Bip001 L Foot", "child_target": "Bip001 L Toe0",
     "desired_len": "11.228109", "mode": "foot",
     "foot_len": "14.097240", "toe_len": "3.569191", "tip_x": "7.049082",
     "tip_y": "-11.587054", "tip_z": "1.459004",
     "toe_w": "5.317024", "toe_h": "1.860916",
     "foot_w": "3.714245", "foot_h": "8.656011"},
]

# fixture 的手部粗细表：两根（一只左手一只右手）就够长出通道，多写不会多通道。
WIDTHS = [{"biped_target": "Bip001 L Hand", "width": "3.6286"},
          {"biped_target": "Bip001 R Hand", "width": "3.6953"}]

# 附属骨方向回读（#66）的两份 fixture： bones 决定「哪些行是 BoneGeometry 的尺」，
# biped_map 决定「哪些行被 Biped 节点接管、因而不归这个通道」。两者都得走真解析
# （同 verdicts.CHANNELS 的理由）：手搭一份的话，这组通道只反映我对格式的想象。
BONES = verdicts.parse_table(verdicts.FIXTURE_BONES)[1]
BIPED_MAP = verdicts.parse_table(verdicts.FIXTURE_BIPED_MAP)[1]


def spec(**kw):
    args = dict(model=verdicts.PRICED_MODEL, vertex_count=18892, limbs=LIMBS,
                skin_expect=verdicts.FIXTURE_SKIN_EXPECT, widths=WIDTHS,
                bones=BONES, biped_map=BIPED_MAP,
                morph_count=61, skin=True, foot_reach=True)
    args.update(kw)
    return verdicts.build_spec(**args)


def key(rows):
    return {(r[0], r[1], r[2]): r for r in rows}


def slice_between(text, start_marker, end_marker):
    """从 start_marker 所在处切到 end_marker 之前（比手数字符可靠，改动后会跟着动）。"""
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    return text[start:end]


def ms_code(text):
    """剥掉 MAXScript 注释，只留代码。

    「这一段里不许出现判定词 / 不许有 catch」这类钉子必须只看代码：本项目 .ms 的注释
    写的是踩过的事故（里面满是「失败」「try/catch」的引述），拿全文去比对就成了
    注释越详细越判红 —— 那会逼人删注释，正是最不想要的那个结果。
    """
    out = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    out = re.sub(r"--[^\n]*", " ", out)
    return re.sub(r"//[^\n]*", " ", out)


class WireFormatTests(unittest.TestCase):
    def test_spec_text_starts_with_the_column_names(self):
        lines = verdicts.spec_text(spec()).splitlines()
        self.assertEqual(lines[0], "\t".join(verdicts.SPEC_HEADER))
        self.assertEqual(len(lines), len(spec()) + 1, "表头 + 数据行，一行不多一行不少")
        self.assertTrue(lines[-1].count("\t") == 6, "每行必须正好七个字段")

    def test_duplicate_channel_is_rejected_not_silently_dropped(self):
        rows = list(spec())
        rows.append(rows[0])
        with self.assertRaises(ValueError) as got:
            verdicts.spec_text(rows)
        self.assertIn("重复", str(got.exception))

    def test_tab_inside_a_field_is_rejected(self):
        # 骨名里真出现制表符的话，整行的列就错位了 —— 报错比「读成别的列」好
        rows = [r[:2] + ("Bip\t001",) + r[3:] for r in spec()]
        with self.assertRaises(ValueError):
            verdicts.spec_text(rows)

    def test_gate_row_without_a_number_is_rejected(self):
        with self.assertRaises(ValueError):
            verdicts.row("fit", "failed_segments", "-", None, None, "gate")
        # report 行反过来：给了数字也不许留下（它没有「该等于几」）
        got = verdicts.row("struct_dir", "measurable", "-", 51, 0.5, "report")
        self.assertEqual(got[3:5], ("", ""))

    def test_unknown_kind_or_when_is_rejected(self):
        with self.assertRaises(ValueError):
            verdicts.row("fit", "x", "-", 0, 0, "must_pass")
        with self.assertRaises(ValueError):
            verdicts.row("fit", "x", "-", 0, 0, "gate", when="maybe")

    def test_parse_table_accepts_short_rows_but_not_long_ones(self):
        """limbs.tsv 的行可以比列名短（只有 mode=foot 的行才带第 10~18 列）。"""
        text = ("a\tb\tc\nx\ty\tz\np\tq\n")
        header, rows = verdicts.parse_table(text)
        self.assertEqual(header, ("a", "b", "c"))
        self.assertEqual(rows[1], {"a": "p", "b": "q"}, "缺的尾部 = 这个键没有")
        self.assertNotIn("c", rows[1], "补空串会被下游当成「真有这个值」")
        with self.assertRaises(ValueError):
            verdicts.parse_table("a\tb\nx\ty\tz\n")

    def test_parse_table_survives_the_borders_max_writes(self):
        """BOM、CRLF、结尾少一个换行、注释行 —— 这些都不许让报告读成 0 行。"""
        body = "\n".join(["\t".join(verdicts.REPORT_HEADER),
                          "fit\tsegment_count\t-\t39"])
        for text in (body, body + "\n", body.replace("\n", "\r\n"),
                     "# 人读注释\n" + body, "﻿" + body):
            header, rows = verdicts.parse_table(text)
            self.assertEqual(header, verdicts.REPORT_HEADER)
            self.assertEqual(len(rows), 1, f"这份文本被读成 {len(rows)} 行：{text[:20]!r}")

    def test_empty_table_raises_instead_of_reading_as_green(self):
        """「报告是空的」必须报错。读成 0 行的话，校验器看到的是「一行都没量」。"""
        with self.assertRaises(ValueError):
            verdicts.parse_table("")


class SubjectDerivationTests(unittest.TestCase):
    """判据行「量谁」由导出的 limbs.mode 决定，不是由硬编码名单决定。"""

    def test_foot_and_leg_modes_drive_their_own_rows(self):
        rows = key(spec())
        for want in (("toe_snap", "residual_after_roll_cm", "Bip001 L Toe0"),
                     ("foot_chain", "chain_angle_deg", "Bip001 L Foot -> Bip001 L Toe0"),
                     ("foot_chain", "gap_cm", "Bip001 L Foot -> Bip001 L Toe0"),
                     ("thigh_roll", "knee_err_cm", "Bip001 L Thigh"),
                     ("segment", "len_cm", "Bip001 Spine -> Bip001 Spine1")):
            self.assertIn(want, rows)
        # 恒等式不许借道回来：偏航比的就是转角 pass 写进骨轴的那个方向（同源），
        # 它只能当日志。第十五轮压平之后它读 0.0 全绿，而截图里骨盒照旧是歪的。
        for gone in ("bone_yaw_deg", "toe_yaw_deg"):
            self.assertFalse([k for k in rows if k[1] == gone],
                             f"foot_yaw/{gone} 又变回判据了（那条行不可能变红）")

    def test_export_without_feet_produces_no_per_pair_foot_verdicts(self):
        """把脚链从导出里去掉，**逐对**的脚链判据必须一条不剩。

        反过来（留着）就是拿上一具模型的判据判这一具，缺的那一侧永远报不出来，整轮判红。
        两条全场景汇总（断开根数 / 没接上根数）不在其中：.ms 在脚链循环**外面**报它们，
        没有脚时读到 0 —— 那一行仍然必须出现，否则「这段没跑」和「跑了但没问题」同形。
        """
        legs_only = [x for x in LIMBS if x["mode"] != "foot"]
        rows = key(spec(limbs=legs_only))
        per_pair = {k for k in rows
                    if k[0] == "toe_snap"
                    or (k[0] == "foot_chain" and k[2] != verdicts.GLOBAL)}
        self.assertEqual(per_pair, set())
        for check in ("broken_roots", "gap_unconnected"):
            self.assertEqual(rows["foot_chain", check, verdicts.GLOBAL][6], "always")
        self.assertIn(("thigh_roll", "knee_err_cm", "Bip001 L Thigh"), rows,
                      "腿还在，腿的判据不许跟着消失")

    def test_segment_expected_is_the_exported_length_verbatim(self):
        """报的是「量到的长度」，尺是 limbs 的 desired_len 原文。

        报「Max 自己算的误差」的话，Max 解析目标值错一位（比如把列读串）就永远
        读成 0 误差 —— 那是恒等式。用原文比对，连 float 往返都不参与。
        """
        rows = key(spec())
        self.assertEqual(rows["segment", "len_cm",
                           "Bip001 L Foot -> Bip001 L Toe0"][3], "11.228109")

    def test_finger_segments_are_optional_but_still_graded(self):
        rows = key(spec())
        got = rows["segment", "len_cm", "Bip001 L Finger0 -> Bip001 L Finger01"]
        self.assertEqual(got[6], "if_present", "Biped 指槽位数由模型决定，缺了不算失败")
        self.assertEqual(got[4], str(verdicts.SEGMENT_TOL_CM),
                         "但只要报了，尺还是 0.05 厘米")

    def test_converge_pass_one_is_mandatory_and_later_passes_optional(self):
        rows = key(spec())
        self.assertEqual(rows["converge", "raised", "1"][6], "always")
        for check in ("raised", "not_improved", "dir_over", "place_sum_cm",
                      "place_max_cm"):
            self.assertEqual(rows["converge", check, "3"][6], "if_present",
                             "提前收敛的那几遍本来就不该有读数")

    def test_switches_downgrade_coverage_but_never_delete_rows(self):
        """--no-skin / FOOTREACH=false 只把「必须出现」松成「有就判」，行一行不少。

        删行的写法会把「整段跳过」和「跑挂了没报」合成同一个样子。
        被关掉那一段的**收工边界**（`pass/ran`）必须和它的读数一起松 —— 否则冒烟轮
        会因为「没跑蒙皮」而被判成「时间轴缺一段」，红得没有信息量。
        """
        on, off = spec(), spec(skin=False, foot_reach=False)
        self.assertEqual({r[:3] for r in on}, {r[:3] for r in off})
        before, after = key(on), key(off)
        changed = {k for k in before if before[k][6] != after[k][6]}
        # M4b 的整段回读（20 条聚合 + fixture 里两根脚链骨各 1 条 = 22）与写入计数一起松：
        # 蒙皮没跑就没有 Skin 修改器，这些行一条都量不出来。条数在这里额外钉一次，是为了
        # 「少一类回读判据」不会被「少一批降级」这种自洽的说法蒙过去。
        readback = {k for k in before if k[0] == "skin_rb"}
        self.assertEqual(len(readback), 22, "回读判据的条数变了")
        self.assertEqual(changed,
                         {("mesh", "vertex_count", "-"),
                          ("skin", "bones", "-"),
                          ("skin", "failed_vertices", "-"),
                          ("skin", "written_vertices", "-"),
                          ("toe_snap", "residual_after_reach_cm", "Bip001 L Toe0"),
                          ("pass", "ran", "skin_apply"),
                          ("pass", "ran", "skin_verify"),
                          ("pass", "ran", "foot_reach"),
                          ("pass", "ran", "toe_snap_reach")} | readback)
        self.assertEqual(after["morpher", "built", "-"][6], "if_present",
                         "表情表允许整段为空（CFG_EMPTY_OK），所以这几行永远是人有权不报")
        for k in changed:
            self.assertEqual((before[k][6], after[k][6]), ("always", "if_present"),
                             f"{k} 的降级方向不对：开关只能放宽要求，不能反过来加严")


class FootDegradationGateTests(unittest.TestCase):
    """脚段退化成普通段必须响，而「响不响」由**权重表**回答，不由 mode 列回答。

    这一族用例是 #98 那次审查的直接产物：`export_obj._foot_profile` 量不到鞋时旧写法
    直接写 `mode=link`，而 `box_targets` / 脚链 / 接趾 / 复摆全都从 `mode == "foot"` 的
    行取尺 —— 于是「导出成功 + 日志全绿 + 脚骨戳穿鞋面」三件事同时成立。修法是两层：
    写侧出声（limbs.tsv 里一行 `#`），判据侧把「这双脚该不该有尺」的来源换成
    weights/bones（导出的输入，规则 I 要的就是这个「独立」）。
    """

    @staticmethod
    def _degraded():
        """把 fixture 的脚段退回普通段：mode=link，10~18 列整体不写（写侧就这么落表）。"""
        rows = []
        for limb in LIMBS:
            if limb.get("mode") != "foot":
                rows.append(dict(limb))
                continue
            rows.append({"parent_target": limb["parent_target"],
                         "child_target": limb["child_target"],
                         "desired_len": limb["desired_len"],
                         "mode": "link"})
        return rows

    def test_a_skinned_foot_pair_that_degraded_raises(self):
        """这双脚蒙着顶点（fixture 的权重表这么说的）⇒ 退化 = 事故，当场抛。"""
        measured = verdicts.foot_pairs_with_mesh(verdicts.FIXTURE_SKIN_EXPECT)
        self.assertIn("Bip001 L Foot -> Bip001 L Toe0", measured)
        with self.assertRaises(ValueError) as caught:
            verdicts.limb_rows_of_mode(self._degraded(), "foot", measured)
        message = str(caught.exception)
        self.assertIn("Bip001 L Foot -> Bip001 L Toe0", message)
        self.assertIn("'link'", message)

    def test_the_whole_spec_build_refuses_a_degraded_foot(self):
        """不只是那个函数会抛：拼整张规格表的路径（obj_run 实跑走的就是它）也抛。

        只测叶子函数是不够的 —— 以前 `build_spec` 里那句 `x.get("mode") == "foot"`
        就是「谁也没抛、只是安静地少了几行」的那个位置。
        """
        with self.assertRaises(ValueError):
            spec(limbs=self._degraded())

    def test_a_foot_pair_without_mesh_is_allowed_to_degrade(self):
        """脚上没有网格（样例夹具那一支）：不抛，脚盒那一族跟着没有尺。

        这条是给「一刀切都抛」划界线的：样例模型只有 4 个躯干顶点，硬要它有骨盒目标
        等于要求凭空捏一个数。判据必须是**权重表说这双脚有网格**才抛，而不是
        「导出写了 link」就抛。
        """
        rows = verdicts.limb_rows_of_mode(self._degraded(), "foot", frozenset())
        self.assertEqual(rows, [])
        full = key(spec())
        built = key(spec(limbs=self._degraded(),
                         skin_expect=_skin_without_feet()))
        for family, subject in (("box_rb", "Bip001 L Foot"),
                                ("box_rb", "Bip001 L Toe0"),
                                ("toe_snap", "Bip001 L Toe0")):
            gone = {k for k in full if k[0] == family and k[2] == subject}
            self.assertTrue(gone, "fixture 本来有这一族，否则这条界线什么也没证")
            for k in gone:
                self.assertNotIn(k, built, f"没有鞋就没有尺：{k} 不该再出现")
        self.assertEqual(built[("box_rb", "nodes_measured", "-")][3], "2",
                         "骨盒只量两只手 —— 计数跟着少，不是留着一个没人填的 4")
        self.assertIn(("segment", "len_cm", "Bip001 L Foot -> Bip001 L Toe0"), built,
                      "段长来自踝与脚掌的骨位，不需要网格：没有鞋也照样判")
        self.assertIn(("fit", "segment_count", verdicts.GLOBAL), built,
                      "但表本身还是要拼得出来 —— 这条界线不是把导出打死")

    def test_the_leg_mode_column_is_checked_without_asking_the_mesh(self):
        """大腿段没有「量不到」这回事：mode 与目标名不符就直接抛，不问权重表。

        `mode=leg` 是纯命名（子关节位置就是 PMX 的膝），所以这一类的分叉一定是表被
        改坏 / 两份产物不同步，没有「这具模型合法地没有腿」的解释余地。
        """
        rows = [dict(x) if x["mode"] != "leg" else
                dict(x, mode="link") for x in LIMBS]
        with self.assertRaises(ValueError) as caught:
            verdicts.limb_rows_of_mode(rows, "leg", frozenset())
        self.assertIn("Bip001 L Thigh -> Bip001 L Calf", str(caught.exception))

    def test_a_row_without_target_names_raises_instead_of_becoming_invisible(self):
        """第 1~2 列空着 = 主键没了：按名字认类别的所有判断都会把它当成不存在，所以抛。"""
        for broken in ({"parent_target": "", "child_target": "Bip001 L Toe0", "mode": "foot"},
                       {"child_target": "Bip001 L Toe0", "mode": "foot"}):
            with self.assertRaises(ValueError) as caught:
                verdicts.limb_rows_of_mode([broken], "foot", frozenset())
            self.assertIn("parent_target", str(caught.exception))

    def test_foot_pairs_with_mesh_needs_both_bones_to_carry_weight(self):
        """口径是「两节都至少挂着一个顶点」：只有 Foot 有、Toe0 没有 ⇒ 不算可量。

        脚段一行同时给两根骨的尺（Foot 的 17/18 列、Toe0 的 15/16 列），缺一节就没有
        完整的一对目标，退回普通段才是对的。
        """
        def expect(links):
            return verdicts.FIXTURE_SKIN_EXPECT._replace(
                links_expected=sum(n for _n, n in links),
                links_by_bone=tuple(links))
        self.assertEqual(
            verdicts.foot_pairs_with_mesh(
                expect([("Bip001 L Foot", 363), ("Bip001 L Toe0", 74)])),
            frozenset({"Bip001 L Foot -> Bip001 L Toe0"}))
        for links in ((("Bip001 L Foot", 363), ("Bip001 L Toe0", 0)),
                      (("Bip001 L Foot", 0), ("Bip001 L Toe0", 74)),
                      (("Bip001 L Foot", 0), ("Bip001 L Toe0", 0))):
            self.assertEqual(verdicts.foot_pairs_with_mesh(expect(links)), frozenset(),
                             f"缺一节的脚链不算「有网格可量」：{links}")


def _skin_without_feet():
    """fixture 的期望链路表，但把脚上两节的顶点全挪走（= 样例模型那种「脚没有网格」）。"""
    base = verdicts.FIXTURE_SKIN_EXPECT
    links = [(name, 0 if name.endswith(verdicts.LINK_SUBJECT_SUFFIXES) else n)
             for name, n in base.links_by_bone]
    return base._replace(links_expected=sum(n for _name, n in links),
                         links_by_bone=tuple(links))


class RollbackSpecTests(unittest.TestCase):
    """M4d 快照回滚的规格行：开关改变的是**判定强度**，不是合同的大小。

    这组和 skin/foot_reach 那两组同构：`ROLLBACK=false` 的轮子里 Max 一段回滚代码都不跑，
    那 19 行照样在表上，只是退成 `if_present`（没读数算 SKIP，不算 FAIL）。哪天有人把
    「关掉」实现成「删行」，规格表就会和 Max 的报告同时少一类东西 —— 少的正是
    「退路到底存不存在」这件事本身，而那件事只能靠表上留着的行来说话。
    """

    # 只有真退回时才存在的五条：开关打开也不许要求它们必出现。
    RESTORE = ("foot_restored", "foot_restore_pos_cm", "foot_restore_box_cm",
               "foot_restore_scale_cm", "foot_restore_angle_deg")

    def test_switch_never_deletes_rows(self):
        off, on = spec(), spec(rollback=True)
        self.assertEqual({r[:3] for r in off}, {r[:3] for r in on})
        before, after = key(off), key(on)
        rb = {k for k in before if k[0] == "rollback"}
        self.assertEqual(len(rb), 19, "回滚判据的条数变了：先确认是不是少了一类退路")
        changed = {k for k in before if before[k][6] != after[k][6]}
        self.assertEqual(changed,
                         (rb - {("rollback", c, "-") for c in self.RESTORE})
                         | {("pass", "ran", "rollback_selftest"),
                            ("pass", "ran", "foot_reach_guard")})
        for k in changed:
            self.assertEqual((before[k][6], after[k][6]), ("if_present", "always"),
                             f"{k}：开关只能把要求收紧到「必须报到」，反过来就是拿开关藏缺陷")
        for check in self.RESTORE:
            self.assertEqual(after[("rollback", check, "-")][6], "if_present")

    def test_guard_coverage_follows_foot_reach_too(self):
        """守卫整段住在接趾里：回滚开着但 FOOTREACH 关着 ⇒ 自检仍然强制，守卫行退成可选。

        少这一条对撞的话，`--no-footreach` 那类冒烟轮会被自己的开关组合判红 —— 而红得
        没有信息量的验收，最后的下场和没有验收一样。
        """
        rows = key(spec(rollback=True, foot_reach=False))
        for check in ("saved", "loaded", "pos_cm", "box_cm", "scale_cm", "angle_deg"):
            self.assertEqual(rows["rollback", check, "-"][6], "always")
        for check in ("guard_saved", "foot_before_cm", "foot_after_cm",
                      "guard_fired", "foot_worse_bones"):
            self.assertEqual(rows["rollback", check, "-"][6], "if_present")

    def test_a_fired_guard_reddens_the_round_by_construction(self):
        """`foot_worse_bones` 期望 0、容差 0，而它数到的就是触发器本身。

        所以「走过回滚的轮子」一定判红。这不是苛刻，是 M4d 的全部意义：退路在走过的那
        一轮必须大声响，否则它会以「静默救场」的样子把一个真缺陷交付成一次成功。
        """
        rows = key(spec(rollback=True))
        self.assertEqual(rows["rollback", "foot_worse_bones", "-"][3:6], ("0", "0", "gate"))
        self.assertEqual(rows["rollback", "guard_fired", "-"][5], "report",
                         "guard_fired 只许当证据：判据只有 worse_bones 那一条，"
                         "两条都判就是两个来源")
        # 载体能不能用，看的是「存下来没有 / 读回来没有」这两个布尔，不是某个厘米数。
        for check in ("saved", "loaded", "guard_saved"):
            self.assertEqual(rows["rollback", check, "-"][3:6], ("true", "0", "gate"))
        # 扰动的量级、裸存图的代价、被撑的那根骨：三行都是「证明这局真动过手」的证据，
        # 没有它们，上面那三个 0 就可能是「压根没扰动」的假绿。
        for check in ("perturb_cm", "snap_cost_cm", "perturbed_bone"):
            self.assertEqual(rows["rollback", check, "-"][5], "report")

    def test_the_rollback_ruler_is_shared_and_stays_python_side(self):
        """六列 0 ± ROLLBACK_TOL_CM、两列 0 ± ROLLBACK_TOL_DEG：数都来自 targets，
        而且**都不许进 CFG**。

        Max 在写的那一刻还不知道误差，这条尺在它那边做不了任何门控；递过去就等于把
        判据交给被量的一方 —— M4b 的权重容差、这里的容差走的是同一条规矩。
        朝向那两列是第 27 轮加的四维：那一轮位置/盒/scale 全绿，锁骨却转了 89.5 度，
        所以「回干净没有」从此必须连轴朝向一起判（尺的定价见 targets.ROLLBACK_TOL_DEG）。
        """
        rows = key(spec(rollback=True))
        for check in ("pos_cm", "box_cm", "scale_cm",
                      "foot_restore_pos_cm", "foot_restore_box_cm",
                      "foot_restore_scale_cm"):
            self.assertEqual(rows["rollback", check, "-"][4], str(targets.ROLLBACK_TOL_CM),
                             f"rollback/{check} 的容差不再引用 targets.ROLLBACK_TOL_CM")
        carried = [k for k, v in targets.TARGETS.items()
                   if v[0] == targets.ROLLBACK_TOL_CM]
        self.assertEqual(carried, [],
                         f"回滚容差被放进 CFG（{carried}）：尺只能住在判的一方")
        for check in ("angle_deg", "foot_restore_angle_deg"):
            self.assertEqual(rows["rollback", check, "-"][4], str(targets.ROLLBACK_TOL_DEG),
                             f"rollback/{check} 的容差不再引用 targets.ROLLBACK_TOL_DEG")
            self.assertEqual(rows["rollback", check, "-"][3:5], ("0", str(targets.ROLLBACK_TOL_DEG)),
                             "朝向那一列期望必须是 0：它比的是退回前后同一根骨的轴")
        deg_carried = [k for k, v in targets.TARGETS.items()
                       if v[0] == targets.ROLLBACK_TOL_DEG]
        self.assertEqual(deg_carried, [],
                         f"回滚朝向尺被放进 CFG（{deg_carried}）：尺只能住在判的一方")


class HipAnchorGradingTests(unittest.TestCase):
    """骨盆三段的尺是「离髋中点的距离」，不是 PMX 腰→子骨长度。

    第二十一轮第一次把两张表拼起来就红了 3 行：`Pelvis -> L/R Thigh` 期望 9.0278、
    `Pelvis -> Spine` 期望 6.4912，Max 实报 6.95583 / 3.50962。查下来不是几何退步
    （第十九、二十轮的 `段校验` 一直是这两个读数），而是 .ms 在 3487 行把这三段的
    **目标**改写成髋中点距离 —— Biped 的 Pelvis pivot 只跟 COM 走，骨盆闭环又把 COM
    抬到两髋中点，照 PMX 腰的长度缩会把髋撑到 ±8.97（PMX 只要 ±7.00）。

    这条改写当时只有 Max 一侧有，Python 的尺不知道，于是产出一条**永远不可能通过**
    的 gate。永红和恒绿一样坏，所以这边把同一个量法实现一遍，并用下面的契约钉住
    两边输入相同（同一批 limbs 坐标列、同一个 0.05 门槛、同一对中点）。
    """

    HIP_LIMBS = [
        {"parent_target": "Bip001 Pelvis", "child_target": "Bip001 L Thigh",
         "desired_len": "9.027836", "mode": "link",
         "child_x": "10.0", "child_y": "0.0", "child_z": "100.0"},
        {"parent_target": "Bip001 Pelvis", "child_target": "Bip001 R Thigh",
         "desired_len": "9.027834", "mode": "link",
         "child_x": "-10.0", "child_y": "0.0", "child_z": "100.0"},
        {"parent_target": "Bip001 Pelvis", "child_target": "Bip001 Spine",
         "desired_len": "6.491230", "mode": "link",
         "child_x": "0.0", "child_y": "5.0", "child_z": "103.0"},
        {"parent_target": "Bip001 Spine", "child_target": "Bip001 Spine1",
         "desired_len": "8.325677", "mode": "link",
         "child_x": "0.0", "child_y": "13.5", "child_z": "103.0"},
    ]

    def rows(self, limbs=None):
        return key(verdicts.build_spec(model=verdicts.PRICED_MODEL, vertex_count=1,
                                       limbs=limbs or self.HIP_LIMBS,
                                       skin_expect=verdicts.FIXTURE_SKIN_EXPECT,
                                       widths=[],
                                       bones=BONES, biped_map=BIPED_MAP,
                                       morph_count=1))

    def test_pelvis_children_are_graded_against_the_anchor_midpoint(self):
        """锚点 (0,0,100)：腿根该量到 10，脊柱该量到 sqrt(34)=5.830952。"""
        rows = self.rows()
        self.assertEqual(verdicts.hip_anchor(self.HIP_LIMBS), (0.0, 0.0, 100.0))
        for child, want in (("Bip001 L Thigh", "10.000000"),
                            ("Bip001 R Thigh", "10.000000"),
                            ("Bip001 Spine", "5.830952")):
            got = rows["segment", "len_cm", f"Bip001 Pelvis -> {child}"][3]
            self.assertEqual(got, want, f"{child} 的尺没按髋中点算")
            self.assertNotEqual(got, next(x["desired_len"] for x in self.HIP_LIMBS
                                          if x["child_target"] == child),
                                "改写没发生 = 这条 gate 永远红")

    def test_non_pelvis_segments_keep_the_exported_length_verbatim(self):
        rows = self.rows()
        self.assertEqual(rows["segment", "len_cm",
                           "Bip001 Spine -> Bip001 Spine1"][3], "8.325677")

    def test_anchor_only_applies_when_both_hips_exist(self):
        """只有一条腿根、或表里根本没有坐标列 → 锚点算不出来 → 沿用 PMX 长度。

        「算不出来就退回收 PMX 的尺」而不是退收成 0 或缺行：换一具没有腿根的骨架
        （飘带、纯装饰骨）时这三行仍然要有尺，红也好、绿也好，都是**同一把尺**的结果。
        """
        one_hip = [x for x in self.HIP_LIMBS if x["child_target"] != "Bip001 R Thigh"]
        self.assertIsNone(verdicts.hip_anchor(one_hip))
        no_coords = [{k: v for k, v in x.items() if k not in verdicts.POS_KEYS}
                     for x in self.HIP_LIMBS]
        self.assertIsNone(verdicts.hip_anchor(no_coords))
        rows = self.rows(no_coords)
        self.assertEqual(rows["segment", "len_cm",
                           "Bip001 Pelvis -> Bip001 L Thigh"][3], "9.027836")

    def test_the_ms_side_uses_the_same_inputs(self):
        """两边算的是同一个数：同一批坐标（item[4]）、同一个门槛、同一对中点。"""
        block = ms_code(slice_between(SCRIPT, "local hipAnchor = mmd2maxHipAnchor limbs",
                                      "local scaleAxis = 0"))
        self.assertIn('if item[1] == "Bip001 Pelvis"', block)
        self.assertIn("local want = distance hipAnchor item[4]", block,
                      "子骨目标位必须是 item[4]（limbs 的 child_x/y/z），不是节点位置")
        self.assertIn(f"if want > {verdicts.HIP_ANCHOR_MIN_CM} do", block,
                      "门槛两边必须同一个数：这边改了那边不改 = 又分叉")
        self.assertIn("item[3] = want", block,
                      "改写要落到 item[3]，拟合与段校验才读同一个值")
        anchor = ms_code(slice_between(SCRIPT, "fn mmd2maxHipAnchor limbs",
                                       "fn mmd2maxFitPelvis"))
        for side in verdicts.HIP_JOINTS:
            self.assertIn(f'if item[2] == "{side}" do', anchor)
        self.assertIn("item[4]", anchor)
        self.assertIn("/ 2.0", anchor, "锚点是**中点**，不是某一条腿")


class FramingSpecTests(unittest.TestCase):
    """两张特写的取景判据（#70 胸、#71 颈）：Max 报「真去 zoom 的那只框 / 那条跨度」，
    Python 拿**骨自己**判。

    第 35 轮那框住不了任何东西的 chest_front 之所以全绿，是因为整条链上只有一条
    「填充率」在量框自己。第 36 轮补了 z 一根轴，侧视照样把骨裁在框的**前后**之外 ——
    所以现在三根轴各一对，另外还查 #69 收框**之后**那条水平跨度（收框是朝框中心砍的，
    砍之前的框罩得住不代表画面上有骨）。这些行的期望值全部来自导出侧量出来的骨坐标，
    与框是怎么算出来的无关 —— 拿框验框只能证明 CFG 没丢数，证明不了框里有东西。
    """

    # ((x,y,z)min, (x,y,z)max)：香菱实测的两根胸骨端点极值（tail 是乳头，比 head 更低）
    RULER = ((-4.9157, -11.7451, 105.2253), (4.9157, -7.2398, 105.7325))
    # 颈链三节（上半身2 / 首 / 頭）的端点极值：`頭` 的 tail 是三根轴的上/外侧边缘
    NECK = ((-3.842957, -6.766848, 100.760711), (8.8e-05, -0.036633, 128.286294))

    def rows(self, chest, neck=None):
        return {(r[0], r[1], r[2]): r
                for r in verdicts.shot_fill_rows(chest, neck)}

    def test_a_measured_box_is_graded_one_sided_on_every_axis(self):
        rows = self.rows(self.RULER, self.NECK)
        for prefix, ruler in (("chest", self.RULER), ("neck", self.NECK)):
            for i, axis in enumerate(verdicts.FRAMING_AXES):
                lo = rows[("shot_rb", f"{prefix}_box_lo", axis)]
                hi = rows[("shot_rb", f"{prefix}_box_hi", axis)]
                # 下沿「不得高于」骨的下缘、上沿「不得低于」骨的上缘：
                # 框比要求的更宽不是缺陷。
                self.assertEqual((lo[5], hi[5]), ("max", "min"), axis)
                self.assertEqual((lo[3], hi[3]),
                                 (str(ruler[0][i]), str(ruler[1][i])),
                                 f"{axis} 轴的尺拿错了：三轴各用各的极值，不许顺序切片串味")
                # 单向 kind 的容差没有意义，必须写 0 —— 留一个非零数会让人以为它的双向余量。
                self.assertEqual((lo[4], hi[4]), ("0", "0"), axis)
                for row in (lo, hi):
                    self.assertEqual(row[6], "if_present")

    def test_each_framing_uses_its_own_ruler(self):
        """两把尺同时给：颈部那六行拿颈骨的数，胸部那六行拿胸骨的数，互不串门。

        串了的形状是「颈部行复用了胸骨极值」—— 两个数都贴着中线、都在 100~130 之间，
        看上去毫无异常，但颈部特写永远判不出「框偏低到骨盆」这件事。
        """
        rows = self.rows(self.RULER, self.NECK)
        for axis_i, axis in enumerate(verdicts.FRAMING_AXES):
            self.assertEqual(rows[("shot_rb", "neck_box_lo", axis)][3],
                             str(self.NECK[0][axis_i]))
            self.assertEqual(rows[("shot_rb", "chest_box_lo", axis)][3],
                             str(self.RULER[0][axis_i]))
            self.assertNotEqual(rows[("shot_rb", "neck_box_lo", axis)][3],
                                rows[("shot_rb", "chest_box_lo", axis)][3],
                                f"{axis}：颈与胸共用一把尺，那颈部判据就是装饰")

    def test_the_tight_span_is_graded_for_every_tight_shot(self):
        """收框后的水平跨度：六张 tight 图各按自己视图的水平轴、自己的尺判。

        轴来自 SHOT_H_AXIS（正视→x、左视→y），因为 .ms 的 mmd2maxAspectBox 砍的正是
        这一根。#71 之前颈部那两张只能 report（导出侧没有颈链的尺）；现在两边的尺都在，
        一张特写也不许只报数不被判。
        """
        rows = self.rows(self.RULER, self.NECK)
        for tag, axis in verdicts.SHOT_H_AXIS.items():
            i = verdicts.FRAMING_AXES.index(axis)
            ruler = self.NECK if "neck" in tag else self.RULER
            lo = rows[("shot_rb", "tight_h_lo", tag)]
            hi = rows[("shot_rb", "tight_h_hi", tag)]
            self.assertEqual((lo[5], hi[5]), ("max", "min"), tag)
            self.assertEqual((lo[3], hi[3]),
                             (str(ruler[0][i]), str(ruler[1][i])), tag)

    def test_the_axis_table_covers_exactly_the_tight_shots(self):
        """SHOT_H_AXIS 的键集必须 == SHOT_TIGHT_TAGS：多一个键没人读，少一个键少两行判据。

        这两份名单一个是「.ms 收哪几张图」、一个是「这几张的水平轴是谁」，分家之后就会
        悄悄不一致 —— 不一致的形状是新加一张 tight 图而规格表里没有它的跨度行。
        """
        self.assertEqual(set(verdicts.SHOT_H_AXIS), set(verdicts.SHOT_TIGHT_TAGS))

    def test_an_unmeasurable_model_still_reports_every_number(self):
        """量不出 subject 的模型：那些行必须**照样存在**，只是退成 report（不卡数）。

        把行删掉就等于「这一轮没人问框里有没有骨」，而那正是第 35 轮的处境 ——
        少一行与少一条判据是两件事，前者必须是可察觉的。
        """
        rows = self.rows(None, None)
        prefixes = ("chest", "neck")
        checks = [(f"{prefix}_box_{edge}", axis) for prefix in prefixes
                  for edge in ("lo", "hi") for axis in verdicts.FRAMING_AXES]
        checks += [(c, t) for c in ("tight_h_lo", "tight_h_hi")
                   for t in verdicts.SHOT_TIGHT_TAGS]
        # 条数按名单算，不抄死数字：#73 加一张 tight 图时抄死的 24 会让这条红在
        # 「名单变了」这种根本不是缺陷的地方上，而它真正要守的是「每张都有两行跨度」。
        self.assertEqual(len(checks),
                         len(prefixes) * 2 * len(verdicts.FRAMING_AXES)
                         + 2 * len(verdicts.SHOT_TIGHT_TAGS))
        self.assertEqual(len({t for _, t in checks if t in verdicts.SHOT_TIGHT_TAGS}),
                         len(verdicts.SHOT_TIGHT_TAGS),
                         "每张 tight 图都得有自己的跨度行，少一张就是那张没人判")
        for check, subject in checks:
            row = rows[("shot_rb", check, subject)]
            self.assertEqual(row[5], "report", f"{check}/{subject}")
            self.assertEqual((row[3], row[4]), ("", ""),
                             "report 行不许留数字：那是把香菱的尺带去判别的模型")
            self.assertEqual(row[6], "if_present")

    def test_row_count_does_not_depend_on_the_ruler(self):
        """框量得出与量不出，行数一样：合同不随模型变，只有「卡不卡数」变。"""
        counts = {len(verdicts.shot_fill_rows(chest, neck))
                  for chest in (self.RULER, None) for neck in (self.NECK, None)}
        self.assertEqual(len(counts), 1, "行数随尺在不在变了：换模型会整类判据消失")

    def test_the_box_rows_are_part_of_the_full_spec(self):
        """这 24 行必须真出现在 build_spec 的输出里（走 shot_fill_rows 那一站）。"""
        rows = key(spec())
        for prefix in ("chest", "neck"):
            for check in (f"{prefix}_box_lo", f"{prefix}_box_hi"):
                for axis in verdicts.FRAMING_AXES:
                    self.assertIn(("shot_rb", check, axis), rows)
        for check in ("tight_h_lo", "tight_h_hi"):
            for tag in verdicts.SHOT_TIGHT_TAGS:
                self.assertIn(("shot_rb", check, tag), rows)


class PricedLockTests(unittest.TestCase):
    def test_locks_only_apply_to_the_model_they_were_measured_on(self):
        priced = key(spec())
        other = key(spec(model="克雷薇"))
        self.assertIn("lock", {r[5] for r in priced.values()})
        self.assertNotIn("lock", {r[5] for r in other.values()})
        # 键集合必须**完全**相同（含 placement）：换模型只许改「卡不卡数字」，
        # 不许改「量哪些东西」。少一行 = 那一类缺陷在新模型上没人看。
        self.assertEqual(set(priced), set(other))
        self.assertEqual(len(priced), len(other))
        for k, row in other.items():
            if priced[k][5] == "lock":
                self.assertEqual(row[5], "report", f"{k} 退化了但没保留「必须出现」")
                self.assertEqual(row[6], priced[k][6])
                self.assertEqual((row[3], row[4]), ("", ""),
                                 f"{k} 换模型后不许留香菱的数字")

    def test_every_lock_number_is_a_reading_from_the_priced_round(self):
        """锁不许是没来历的数字：每个都得能在被定价那一轮的实跑日志里逐字找到。

        这一条拦的是「抄一个新数字进来就直接跑」——那种数字下一轮永远读起来对，
        因为它就是从这一轮抄的。注释只能说明「为什么」，日志才证明「量到过」。
        """
        required_files.require(PRICED_LOG)
        log = PRICED_LOG.read_text(encoding="utf-8")
        numbers = []
        for check, values in verdicts.CONVERGE_LOCKS.items():
            numbers += [(f"converge {check}", v) for v in values]
        numbers += [(f"thigh {b}", v) for b, v in verdicts.THIGH_KNEE_LOCKS.items()]
        numbers += [(f"chain {b}", v) for b, v in verdicts.CHAIN_ANGLE_LOCKS.items()]
        numbers += [(f"placement {b}", v[0]) for b, v in verdicts.PLACEMENT_LOCKS.items()]
        for label, value in numbers:
            text = repr(float(value)) if isinstance(value, float) else str(value)
            self.assertIn(text, log, f"{label} 的 {text} 不在 {PRICED_LOG.name} 里：无来历")
        # 整数型锁单看数字太弱（"2" 谁都在日志里），所以把它那句话一起钉住：
        # 改了 .ms 的措辞 = 这条锁的来源读不到了，得同时改这里。
        self.assertIn(f"链向结论 断开={verdicts.CHAIN_BROKEN_LOCK} 根", log)
        self.assertIn(f"夹角>{verdicts.DIR_TOL_DEG}度={verdicts.STRUCT_OVER_LOCK}", log)

    def test_locks_point_at_baselines_not_the_live_run(self):
        """锁的来源必须是 baselines/ 里那一份冻结的日志，不是会被下一轮覆盖的 test_runs/。"""
        self.assertEqual(PRICED_LOG.parent.name, "baselines",
                         "指向可变日志的回归锁会在下一轮实跑后自己作废自己")
        self.assertEqual(SEGMENT_PRICED_LOG.parent.name, "baselines",
                         "段长锁同理：指向 live 日志 = 下一轮自己给自己发尺")

    def test_segment_lock_is_a_reading_from_its_own_frozen_round(self):
        """#76-B：换模型定价的那一段必须能在**那一具模型**的冻结日志里逐字读到。

        和香菱那批锁同一套规矩：数字不许是没来历的。这里读的还不是结论文字，是
        `段校验 … 实际=` 那一行的读数本身 —— 定价的是量到的长度，不是谁写的解释。
        并且同一个数要在**两份**出厂容差的冻结件里都出现：单轮读数不叫定价，叫抄噪声。
        """
        required_files.require(SEGMENT_PRICED_LOG, SEGMENT_FIRST_STDOUT)
        log = SEGMENT_PRICED_LOG.read_text(encoding="utf-8")
        # 定价轮必须跑在出厂容差下：拿实验轮（DIR_TOL 被压小）的读数当「正常读数」，
        # 锁钉的就是一个只有把容差改小才会出现的场景。
        cfg = [l for l in log.splitlines() if "配置来自" in l]
        self.assertEqual(len(cfg), 1, f"{SEGMENT_PRICED_LOG.name} 里读不出一行配置")
        self.assertIn(f"方向容差={targets.DIR_TOL_DEG}", cfg[0],
                      "定价轮的 DIR_TOL 和源码里的不是同一个数：这一轮的读数不是出厂配置下的读数")
        self.assertTrue(verdicts.SEGMENT_LOCKS,
                        "定价表空了：#76-B 那条够不着的段又变回一条永远红下去的 gate")
        earlier = SEGMENT_FIRST_STDOUT.read_text(encoding="utf-8")
        # 两份凭据都得是出厂容差下的轮：拿实验轮的读数当「独立复现」，那是两个同源数字
        # 互相印证，不是复现。
        self.assertIn(f"方向容差={targets.DIR_TOL_DEG}", earlier,
                      f"{SEGMENT_FIRST_STDOUT.name} 不是出厂容差那一轮：跨轮印证不成立")
        for model, table in verdicts.SEGMENT_LOCKS.items():
            for subject, (value, tol) in table.items():
                lines = [l for l in log.splitlines() if f"段校验 {subject} " in l]
                self.assertEqual(len(lines), 1,
                                 f"{model} 的 {subject} 在 {SEGMENT_PRICED_LOG.name} 里"
                                 "不是唯一一行：来历读不出")
                self.assertIn(f"实际={value}", lines[0],
                              f"{subject} 定价的 {value} 不是那一轮量到的长度：{lines[0]}")
                # 同一个数在两轮（不同代码、同一容差）里都量到：锁钉的是稳定读数，
                # 不是把一次偶发写死。
                self.assertIn(f"实报={value}", earlier,
                              f"{value} 只在 {SEGMENT_PRICED_LOG.name} 里出现过一次："
                              "拿单轮读数定价 = 把噪声钉成尺")
                # 锁取代的是 gate，容差只许更严：放宽尺去迁就读数就是「悄悄改容差」。
                self.assertLess(tol, verdicts.SEGMENT_TOL_CM,
                                f"{subject} 的锁容差 {tol} 比它取代的 gate"
                                f"（{verdicts.SEGMENT_TOL_CM}）还松")

    def test_segment_lock_provenance_chain_is_verbatim_in_the_cited_logs(self):
        """源码注释里那条「为什么够不着」的因果链，每一环都要能在被点名的日志里逐字 grep 到。

        定价说明一旦写进注释就没人再回去核；这条测试把注释变成可执行的了：注释里点的
        文件名写错（例如把实验轮写成正常轮）、或那一行的读数后来漂了，都直接红。
        每个键是一行的定位串，值是那一行里必须同时出现的字段 —— 分开 grep 会放行
        「两个数来自不同行」的拼接，那和抄错数没区别。
        """
        required_files.require(SEGMENT_PRICED_LOG, SEGMENT_FORCED_LOG)
        normal = SEGMENT_PRICED_LOG.read_text(encoding="utf-8")
        forced = SEGMENT_FORCED_LOG.read_text(encoding="utf-8")
        # ①③④：出厂容差那一轮的三条读数 —— 0.899 度的结构向、它造成的 pivot 偏差、
        # Neck 自己落位是准的（所以超差来自弧，不来自落位）、弧的另一半落在上一段。
        chain = {
            "结构向 Bip001 Spine 轴1": ["差倾=-0.899286", "夹角=0.898964"],
            "结构向 Bip001 Spine1": ["pivot偏差=0.176007"],
            "校验 Bip001 Neck 目标": ["偏差=7.6296e-06"],
            "段校验 Bip001 Spine -> Bip001 Spine1": ["目标=12.079", "实际=12.0747"],
        }
        bad = []
        for anchor, fields in chain.items():
            lines = [l.strip() for l in normal.splitlines() if anchor in l]
            if len(lines) != 1 or any(f not in lines[0] for f in fields):
                bad.append((anchor, len(lines), fields))
        self.assertEqual(bad, [], f"#76-B 因果链的某一环在 {SEGMENT_PRICED_LOG.name} 里读不通：{bad}")
        # 正常轮按设计不去转 0.899 度：日志里不许出现 Spine 的转角行。
        self.assertTrue("转角1:Bip001 Spine " not in normal,
                        "出厂容差那轮竟然转了 Spine：DIR_TOL 门控失效，①的推理不成立")
        # ②：只有把 DIR_TOL 压到 0.2 的实验轮才去试，试了读数纹丝不动 —— 这是
        # 「够不着不是门控太松」的唯一凭据，必须留在另一份冻结件里。
        self.assertTrue("方向容差=0.2" in forced and
                        "转角1:Bip001 Spine 夹角=0.899181->0.899181" in forced,
                        f"② 那行「转了但没动」不在 {SEGMENT_FORCED_LOG.name}（或它不是 "
                        "DIR_TOL=0.2 那一轮）：实验凭据丢了")

    def test_segment_lock_is_keyed_per_model_and_touches_only_its_own_row(self):
        """段长锁换模型必须仍是 gate：它的尺（limbs 的 desired_len）每具模型都数得出来。

        香菱那批锁换模型退成 report 是对的 —— 那些数字是香菱的身体；段长不是。退化成
        report 等于「换一具模型就没人量骨骼长度了」，而这一行明明一直有独立的尺。
        """
        subject = "Bip001 Spine1 -> Bip001 Neck"
        value, tol = verdicts.SEGMENT_LOCKS["香菱-海灯节"][subject]
        limbs = LIMBS + [{"parent_target": "Bip001 Spine1", "child_target": "Bip001 Neck",
                          "desired_len": "17.898357", "mode": "link"}]
        priced = key(spec(model="香菱-海灯节", limbs=limbs))
        gates = key(spec(model="香菱", limbs=limbs))
        self.assertEqual(priced["segment", "len_cm", subject][5], "lock",
                         "被定价的那一段没换成锁：它下一轮还会红，而红的是已知够不着的数")
        self.assertEqual(priced["segment", "len_cm", subject][3:5], (str(value), str(tol)))
        self.assertEqual(gates["segment", "len_cm", subject][5], "gate",
                         "香菱跟着换成了锁：定价泄漏到了别的模型上")
        self.assertEqual(gates["segment", "len_cm", subject][3], "17.898357",
                         "gate 的尺必须是 limbs 的原文长度")
        for k, row in priced.items():
            if k[0] == "segment" and k[2] != subject:
                self.assertEqual(row, gates[k],
                                 f"{k} 跟着定价一起变了：定价只许管它自己那一段")

    def test_source_explains_every_lock_table(self):
        """每个锁表在源码里必须带一段人话说明（数字为什么是这个数、治的是哪个缺陷）。"""
        source = (PROJECT_ROOT / "mmd2max" / "verdicts.py").read_text(encoding="utf-8")
        doc = source.split('"""')[1]
        for name in ("CONVERGE_LOCKS", "THIGH_KNEE_LOCKS", "CHAIN_ANGLE_LOCKS",
                     "PLACEMENT_LOCKS", "CHAIN_BROKEN_LOCK", "STRUCT_OVER_LOCK",
                     "SEGMENT_LOCKS"):
            self.assertIn(name, source, f"锁表 {name} 不见了")
            self.assertGreater(len(source[source.index(name):source.index(name) + 900]
                               .strip()), 40, f"{name} 附近没有说明")
        self.assertIn("定价", doc, "模块文档必须解释锁是「已定价的取舍」而不是目标")


    def test_report_rows_carry_no_number(self):
        for row in spec():
            if row[5] == "report":
                self.assertEqual((row[3], row[4]), ("", ""))


class MaxChannelContractTests(unittest.TestCase):
    """.ms 里出现的通道必须与规格表的通道**逐字相同**，两边都不许多。"""

    @staticmethod
    def reported_channels():
        """从 .ms 里抠出所有 (phase, check)。

        `mmd2maxReport "toe_snap" checkNm …` 的 check 是形参，字面量在调用点
        （`mmd2maxToeSnapBack root limbs "residual_after_roll_cm"`），所以两处都要收。
        """
        found = set(re.findall(r'mmd2maxReport\s+"([^"]+)"\s+"([^"]+)"', SCRIPT))
        for check in re.findall(r'mmd2maxToeSnapBack\s+root\s+limbs\s+"([^"]+)"', SCRIPT):
            found.add(("toe_snap", check))
        return found

    def test_channels_match_without_dropping_a_verdict(self):
        ms = self.reported_channels()
        spec_channels = verdicts.CHANNELS
        self.assertEqual(ms - spec_channels, set(),
                         "报告里有规格表不认识的通道：这些读数永远不会被判")
        self.assertEqual(spec_channels - ms, set(),
                         "规格表里有 .ms 不报的通道：这条判据是装饰，看着绿其实没量")

    def test_both_sides_write_the_same_header(self):
        # .ms 源码里是字面的 \t（反斜杠 + t），所以比对的是转义后的文本
        self.assertIn('fn mmd2maxReportHeader = "phase\\tcheck\\tsubject\\tactual"',
                      SCRIPT)
        self.assertEqual("phase\tcheck\tsubject\tactual",
                         "\t".join(verdicts.REPORT_HEADER))
        # 前三列是两张表的**拼接键**，必须逐字相同；第四列不同是设计：规格表那列叫
        # expected（尺），报告那列叫 actual（读数）。把它们写成同名反而会让拼表时
        # 「拿 expected 去填 actual」。
        self.assertEqual(verdicts.REPORT_HEADER[:3], verdicts.SPEC_HEADER[:3])
        self.assertEqual(verdicts.REPORT_HEADER[3], "actual")
        self.assertEqual(verdicts.SPEC_HEADER[3], "expected")

    def test_report_header_is_written_by_calling_the_function(self):
        """`format "%\n" (mmd2maxReportHeader)` 打出的是**函数本身**，不是它的返回值。

        第二十一轮实跑就栽在这里：报告第一行落地成字面量 `mmd2maxReportHeader()`，
        列名行变成 1 列，后面 121 行读数每行都「比列名多 3 列」，parse_table 直接抛
        —— 整轮判据作废。日志里没有任何异常（写文件的调用自己成功了），所以这条
        只能靠源码钉：**必须带 `()`**。比对用 ms_code()，因为注释里留了那个错写法。
        """
        init = ms_code(slice_between(SCRIPT, "fn mmd2maxReportInit",
                                     "fn mmd2maxReport phase"))
        self.assertIn('format "%\\n" (mmd2maxReportHeader()) to:f', init)
        self.assertNotIn("(mmd2maxReportHeader)", init,
                         "少了 () 就把函数名当字符串写进表头，报告整轮解析不出来")

    def test_the_chain_threshold_is_not_carved_twice(self):
        """`链向 断开` 的根数由 .ms 数出来，阈值必须是 MMD2MAX_DIRTOL 而不是字面量。

        M2a 之后容差只有 Python 一份；这里留一个 `> 3.0` 就意味着「把 DIRTOL 扳到 999
        回滚」时 Python 侧的尺变了、Max 侧的计数没变 —— broken_roots 会停在旧读数上。
        """
        body = slice_between(SCRIPT, "local minAng = 1e9", 'mmd2maxReport "foot_chain" "broken_roots"')
        self.assertIn("if minAng > MMD2MAX_DIRTOL do", body)
        self.assertIn('" 根（门槛 " + (MMD2MAX_DIRTOL as string) + " 度）"', body)
        for literal in re.findall(r"minAng\s*>\s*([0-9.]+)", ms_code(body)):
            self.fail(f"链向 阈值又刻了一份字面量：{literal}（应读 MMD2MAX_DIRTOL）")

    def test_max_writes_no_verdict_words(self):
        """报告通道只许出现四个字段，一个 PASS/FAIL 都不许写。

        Max 一旦下结论，Python 侧就变成「读 Max 的结论」—— 那正是 v3 的写法，
        也是 `成功=39 失败=0` 全绿而几何是错的根源（#24 的 100 通道假成功）。
        """
        body = ms_code(slice_between(SCRIPT, "fn mmd2maxReport phase",
                                     "fn mmd2maxSceneBox"))
        self.assertIn('format "%\\t%\\t%\\t%\\n"', body)
        for word in ("PASS", "FAIL", "OK", "成功", "失败", "达标"):
            self.assertNotIn(word, body, f"报数函数里不许出现判定词：{word}")
        # 判定词整份 .ms 里出现的位置只能是注释：剥掉注释后，写报告的函数之外
        # 也不许有「verdict / PASS / FAIL」这种 Max 侧自己算结论的标识符。
        stripped = ms_code(SCRIPT)
        for word in ("verdict", "PASS", "FAIL"):
            self.assertNotIn(word, stripped,
                             f"Max 只报数：{word} 不许出现在代码里（注释里可以）")

    def test_report_init_aborts_instead_of_skipping(self):
        init = ms_code(slice_between(SCRIPT, "fn mmd2maxReportInit",
                                     "fn mmd2maxReport phase"))
        self.assertIn("throw", init,
                      "报告建立失败必须中止：继续跑只剩一份没法验收的绿日志")
        self.assertNotIn("catch", init, "catch 等于把中止降级成「什么都没写」")

    def test_reporter_is_rebuilt_after_the_log_exists(self):
        """顺序必须是 建日志 → 日志落配置摘要 → 建报告 → 装配场景。

        报告通道写在日志之前 = 抛错时那份「配置摘要」还没落盘，事后看不出这轮跑的是
        哪份配置；写在装配之后 = 前面几步的读数没有文件可追加，第一行就丢。
        """
        main = slice_between(SCRIPT, "fn mmd2maxMain", "mmd2maxPrepareScene()")
        for marker in ("mmd2maxInitLog()", "mmd2maxLog MMD2MAX_CFGSUMMARY",
                       "mmd2maxReportInit()"):
            self.assertIn(marker, main, f"主流程里找不到 {marker}")
        self.assertLess(main.index("mmd2maxInitLog()"),
                        main.index("mmd2maxLog MMD2MAX_CFGSUMMARY"))
        self.assertLess(main.index("mmd2maxLog MMD2MAX_CFGSUMMARY"),
                        main.index("mmd2maxReportInit()"))

    def test_converge_pass_count_matches_the_loop(self):
        """遍数上限只有一个来源（注册表），另外两侧都是它的读者。

        v3 的写法是 `.ms` 里写死 `for passNo = 1 to 3`、Python 侧另有一个常量：分叉时
        多出来的遍永远没人填，少掉的遍没人判。M3 之后数值住在 `passes.py`，经 CFG 下行
        给 Max，规格表的遍数行由同一个数生成 —— 所以这里钉三段：.ms 用的是那个全局、
        全局的值来自 CFG、CFG/规格表的值等于注册表。
        """
        limit = passes.max_iters("converge")
        self.assertIn("for passNo = 1 to MMD2MAX_CONVERGE_MAX do", SCRIPT,
                      "循环上限又写死成字面量了：注册表改遍数时 .ms 不会跟着动")
        self.assertNotIn("for passNo = 1 to 3 do", ms_code(SCRIPT),
                         "注释里可以留历史，代码里再出现一个写死的 3 就是第二个来源")
        self.assertIn('mmd2maxCfgInt "MMD2MAX_CONVERGE_MAX"', SCRIPT,
                      "遍数必须从 CFG 读，缺键要中止而不是退一个默认值")
        self.assertEqual(verdicts.CONVERGE_MAX_PASSES, limit,
                         "规格表用的遍数不是注册表里那个")
        self.assertEqual(len([r for r in spec() if r[0] == "converge"
                              and r[1] in ("raised", "dir_over")]),
                         2 * limit, "converge 的读数组数与遍数分叉")
        self.assertEqual(len([r for r in spec() if r[0] == "pass" and r[1] == "ran"
                              and r[2] == "converge"]), 1,
                         "收敛这一段自己也要报收工边界，只有一条")

    def test_placement_subject_list_is_the_same_nineteen_nodes(self):
        """19 个落位校验点：.ms 的字面量清单必须等于规格表 placement 行的主体集合。

        少一个 = 那个节点永远不会被量（规格表里没有它的行）；多一个 = 报告里出现
        规格表不认识的行。两边都是「看上去全绿」。
        """
        chunk = re.search(r"for targetName in #\(([^)]*)\)", SCRIPT).group(1)
        listed = re.findall(r'"([^"]+)"', chunk)
        rows = [r for r in spec() if r[0] == "placement" and r[1] == "pos_err_cm"]
        self.assertEqual(sorted(set(listed)), sorted({r[2] for r in rows}),
                         "落位校验点清单与规格表分叉")
        self.assertEqual(len(listed), len(set(listed)), ".ms 的清单里有重复节点")
        self.assertEqual(len(listed),
                         len(verdicts.PLACEMENT_GATE_NODES)
                         + len(verdicts.PLACEMENT_LOCKS))

    def test_ray_targets_follow_the_same_rule_as_the_writer(self):
        """落位按骨盒射线派生的节点：规格表名单必须等于 .ms 的匹配模式。

        这条是 #29 的账：UpperArm 的判据是「离射线交点多远」，而不是「离 PMX 多远」。
        名单写死的两处，一处改了另一处不改，就是「用错尺」。
        """
        body = slice_between(SCRIPT, "fn mmd2maxRayFromParent", "fn mmd2maxShoulderRayTarget")
        patterns = re.findall(r'pattern:"([^"]+)"', body)
        self.assertEqual(len(patterns), 1, "射线派生的判据只许有一个模式，多了没人核对")
        suffix = patterns[0].lstrip("*").strip()
        for name in verdicts.PLACEMENT_RAY_NODES:
            self.assertTrue(name.endswith(suffix), f"{name} 不匹配 .ms 的 {patterns[0]}")
        rows = {r[2] for r in spec() if r[0] == "placement" and r[1] == "ray_err_cm"}
        self.assertEqual(rows, set(verdicts.PLACEMENT_RAY_NODES))

    def test_every_channel_phase_has_a_read_back_behind_it(self):
        """每个 phase 都必须真在 .ms 里被读回来（而不是把循环计数器报出去）。

        抽查方式：该 phase 的 report 调用必须落在某个 `distance` / `Get` /
        `nodeGetBoundingBox` 之后 —— 这条测不了全部，但它拦住「新加一个 phase 却
        只把现成的计数器抄一遍」这种最省事的写法。
        """
        for phase in sorted({c[0] for c in verdicts.CHANNELS}):
            self.assertIn(f'mmd2maxReport "{phase}"', SCRIPT, f"{phase} 没人报数")


class RealExportTests(unittest.TestCase):
    """真导出（香菱）上的金标准：行数与各 phase 的分布。

    这一条不是「钉住数字好看」：第十七轮那次「整份 413 行日志被读成全变了」就是
    解析层默默少读了一半。行数掉了 = 有人删了一类判据而没人察觉。
    """

    def setUp(self):
        if not (EXPORT_DIR / "limbs.tsv").is_file():
            self.skipTest(f"缺少真导出：{EXPORT_DIR}")
        from mmd2max import targets
        _, limbs = verdicts.parse_table(
            (EXPORT_DIR / "limbs.tsv").read_text(encoding="utf-8"))
        meta_text = (EXPORT_DIR / "香菱_meta.tsv").read_text(encoding="utf-8")
        _, meta = verdicts.parse_table(meta_text)
        look = {r["key"]: r["value"] for r in meta}
        self.limbs = limbs
        self.counts = {k: int(look[k]) for k in ("vertex_count", "kept_bones",
                                                 "morph_count")}
        # 蒙皮期望值走**真解析**：这两张表就是 Max 那一轮打开的同一份文件，而这里是
        # 另一个进程在算「输入说了什么」。手搭一个 SkinExpectation 的话，这组金标准
        # 数字量到的只是我对格式的想象。
        _, bones = verdicts.parse_table(
            (EXPORT_DIR / "香菱_bones.tsv").read_text(encoding="utf-8"))
        self.bones = bones
        weights = verdicts.parse_weights(
            (EXPORT_DIR / "香菱_weights.tsv").read_text(encoding="utf-8"))
        self.weights_rows = weights
        self.skin_expect = verdicts.skin_expectation(bones, weights)
        # 骨盒回读（M4c）的期望值同样走真解析：limbs.tsv 的脚段列 + widths.tsv 的行数
        # 就是那 42 条尺的唯一来历。fixture 里手抄一份 widths 的话，「12 行 × 2 通道」
        # 这条分布就变成我对表长度的想象，而不是导出文件说了什么。
        self.widths = verdicts.parse_table(
            (EXPORT_DIR / "香菱_widths.tsv").read_text(encoding="utf-8"))[1]
        # 附属骨方向回读（#66）的第二把尺：biped_map 与 bones 都是真导出的同一份文件。
        self.biped_map = verdicts.parse_table(
            (EXPORT_DIR / "香菱_biped_map.tsv").read_text(encoding="utf-8"))[1]
        self.targets = targets

    def rows(self, **kw):
        args = dict(model=verdicts.PRICED_MODEL, limbs=self.limbs, skin=True,
                    foot_reach=True, vertex_count=self.counts["vertex_count"],
                    morph_count=self.counts["morph_count"],
                    skin_expect=self.skin_expect, widths=self.widths,
                    bones=self.bones, biped_map=self.biped_map)
        args.update(kw)
        return verdicts.build_spec(**args)

    def test_the_declared_lock_list_equals_the_built_lock_rows(self):
        """`verdicts.LOCK_CHECKS` 必须与真规格表里 kind=lock 的 check 名**完全相等**。

        这张表不是文档，是给 `tools/check_ms.py` 的规则 I 读的：那条规则要靠它区分
        「已定价的锁（PASS 只等于没变得更差）」与「假验收（同源 gate）」。表与真表
        分叉的两个方向都坏：
          * 表里多出来 —— 一行同源 gate 被当成锁放过，规则当场变哑（这是最坏的一种，
            因为它看起来一切正常）；
          * 表里少了 —— 真锁被报成假验收，红几次之后有人去放宽规则。
        之所以不能让工具自己扫源码：`_lock("converge", check, …)` 里的 check 是循环
        变量，`dir_over` / `place_max_cm` / `place_sum_cm` 在源码文本里**根本不出现**。
        """
        rows = self.rows()
        built = {r[1] for r in rows if r[5] == "lock"}
        self.assertEqual(set(verdicts.LOCK_CHECKS), built,
                         "LOCK_CHECKS 与真规格表分叉了：加/删 lock 行要同步改这张表")
        self.assertEqual(sorted(verdicts.LOCK_CHECKS), list(verdicts.LOCK_CHECKS),
                         "这张表按字母序写：两轮 diff 才能看出改了哪一项")
        # 非定价模型一条 lock 都不该出（换模型时 _lock 退成 report），但声明表不许跟着空 ——
        # 它记的是「这套判据里哪些 check 有钉住读数的用法」，与这一具模型有没有被定价无关。
        other = {r[1] for r in self.rows(model="克雷薇") if r[5] == "lock"}
        self.assertEqual(other, set(), other)
        self.assertTrue(verdicts.LOCK_CHECKS)

    def test_shape_of_the_priced_spec(self):
        rows = self.rows()
        # −4（#98 规则 I 真的开始拦事之后删掉的两条恒等式：`foot_yaw/bone_yaw_deg` 与
        #   `foot_yaw/toe_yaw_deg`，左右脚各一条 = 4 行。它们的期望值就是转角 pass 写进
        #   骨轴的那个方向，第十五轮把脚盒压平之后它照读 0.0 全绿，而用户截图里骨盒照旧
        #   歪着 —— 同一件事第 40 轮又发生过一次。数字留在 `脚骨偏航结论` 那行日志里，
        #   和截图对时序照样能用；判据由 `foot_chain/chain_angle_deg`（骨轴 vs 回读到的
        #   子关节）与 `gap_cm`/`gap_covered`（两个盒子之间的空气）承担，那三条都不问写入侧）。
        # +1（#76 蒙皮链路的零权口径：新增 `skin_rb/zero_links`。权重印成 `0.000000` 的对
        #   Max 根本存不下（海灯节 332 条），期望链路不再数它，这一行专门盯着「表里又冒出
        #   这种对」= 陈旧文件/手改表。它是个新 check 名，所以通道数也 +1）。
        # +4（#79 沿指向的世界跨度：4 根脚链骨各一条 span_want_cm —— 趾骨那两条有
        #   limbs 的 toe_len 当独立的尺，所以 gate；足背两条两边都没有尺，只能 report。
        #   这一行是「薄边有没有沿着骨指向」唯一不问盒轴记录的读数，见 verdicts 那段）。
        # +1（#78 趾盒收尾：新增一道必经 pass，只带来它自己的收工边界这一行 ——
        #   它不新增读数通道，骨盒的尺子还是 box_rb 那一套）。
        # 537 = 46（#69 的 22 行：19 张图各一条 fill_pct + tags + projection_fail
        #   + shot_capture 的收工边界；#70 的 18 行：三轴取景框 6 + 收框后水平跨度 12，
        #   原来那两行「带」已被这 18 行取代；#71 的 6 行：颈部那三轴取景框，
        #   跨度 12 行里的颈部那 4 行也从 report 升级成参与判定（行数不变、尺到位））
        #   + 19（#72 的裁骨数：19 张图各一条 clip_hidden —— 全身那 5 张必须是 0，
        #   特写那 14 张必须 ≥1，两个方向一起绿才说明「取景框」与「裁剪」都没偷偷变）
        #   + 1（#72 的裁骨分母 clip_bones_total：report，让 clip_hidden 读得对）
        #   + 4（#73 的胸部顶视那一张：fill_pct + clip_hidden + tight_h_lo/hi = 4 行，
        #     通道名一个都不新增 —— 所以 len(CHANNELS) 不动，动的只有行数）
        #   + 4（#74 的同一张再拍一遍带网格的（chest_top）：还是那 4 行形状，
        #     与 bone_chest_top 的区别只在显不显网格，所以行数 +4、通道数照样不动）
        #   + 472（#66 及以前，见下）。
        # 472 = 235（#66 附属骨方向回读：227 根 BoneGeometry 各一条 dir_deg + 8 条聚合）
        #   + 236（M2~M4d 及 #66 以前，见下）+ 1（bone_dir_verify 的收工边界）。
        #   227 那一份不是手抄的：321 行 - 53 行被 Biped 接管 = 268 根附属骨，
        #   再剔掉 41 根 |tail-head| < 1cm 的显示 stub。
        # 236 = 215（M2+M3+M4b+M4c，见下）+ 19（M4d 快照回读：自检 9 条 + 守卫 10 条）
        #   + 2（rollback_selftest / foot_reach_guard 两个 pass 的收工边界）。
        # 215 = 172（M2+M3+M4b，见下）+ 42（M4c 骨盒回读：4 根脚骨 × 4 条 + 12 根手指
        #   × 2 条 + nodes_measured + read_fail）+ 1（box_verify 的收工边界）。
        # 172 = 121（M2 的读数行）+ 23（M3 每个 pass 的收工边界）+ 3（收敛闸门自己
        # 的三行：跑了几遍 / 为什么停 / 判据到没到）+ 25（M4b 的蒙皮回读：20 个聚合
        # 读数（#76 起多一条 zero_links）+ 4 根脚骨各一条链路 + skin_verify 自己的收工
        # 边界）。少一行 = 有人删了一类判据。
        # 19 里比第 27 轮以前多的那 2 条是 `angle_deg` 与 `foot_restore_angle_deg`：
        # 那一轮位置/盒/scale 三列全绿而锁骨转了 89.5 度，回滚的判据从此必须带朝向。
        # 注意：行数与 ROLLBACK 开不开**无关** —— 关着的轮子照样有这 19 行，只是当
        # when=if_present（没读数算 SKIP 不算 FAIL）。开关改变的是判定，不是合同。
        # 截图那 45 行同理：--shot 没开时它们是 SKIP，不是「合同里没有取景这件事」。
        self.assertEqual(len(rows), 547, "真导出的规格表行数变了：先确认是不是少了一类判据")
        self.assertEqual(dict(Counter(r[0] for r in rows)),
                         {"mesh": 1, "fit": 3, "pose": 4, "converge": 18,
                          "pose_end": 3, "rotate_end": 2, "thigh_roll": 4,
                          "toe_snap": 4, "rollback": 19, "foot_chain": 8,
                          "segment": 39, "placement": 21, "struct_dir": 3,
                          "box_rb": 46, "accessory": 3, "bone_rb": 235,
                          "shot_rb": 73,
                          "skin": 3, "skin_rb": 24,
                          "morpher": 4, "pass": 30})
        # 114 = 108（#66 及以前：96 个 M4d 及以前的读数通道 + 9 个 #66 附属骨方向回读
        #   + 3 个 #69 截图取景：fill_pct / tags / projection_fail）
        #   + 4（#70：chest_box_lo/hi 与 tight_h_lo/hi —— Max 真去 zoom 的那只框与收框后
        #     的那条水平跨度都得上报，才有「框里到底有没有骨」这条读数；
        #     第 35 轮全绿就是因为没有这一行，第 36 轮侧视全绿是因为只报了砍之前的框）
        #   + 2（#71：neck_box_lo/hi —— 颈部特写用的是同一套机制、另一组骨，
        #     tight_h_* 那两个通道已经存在，所以这里只多两个 check 名）
        #   + 1（#72：clip_hidden —— 每张图藏了几根框外的骨；19 行共用这一个通道名，
        #     所以通道数只 +1，而这一行是「裁骨到底有没有生效」唯一的读数）
        #   + 1（#79：span_want_cm —— 4 行共用一个通道名；它是 box_rb 里唯一**不读盒轴
        #     记录**的那一行，所以宽/高/长三行自洽地报错轴时，只有这行会红）
        #   + 1（#76：zero_links —— 权重正好 0.0 的链路条数。海灯节那 332 条把
        #     links_missing / link_diff_max / link_mismatch_bones / links_read 四行一起
        #     染红，而对形变毫无影响；单独一条通道名把它们记下来，真正的丢链路才会红）
        #   −2（#98：bone_yaw_deg / toe_yaw_deg —— 规则 I 补了数据流那一层之后抓出来的
        #     两条恒等式：期望值就是转角 pass 写进骨轴的方向，第十五轮压平之后它照读
        #     0.0 全绿，而截图里骨盒照旧歪着。判据由 chain_angle_deg（骨轴 vs 回读到的
        #     子关节）与 gap_cm/gap_covered 承担，那两个通道本来就是独立量）
        # 这条链记到 117 为止比上面加出来的数多 1：#72 以前有一轮的「+1 通道」漏记在
        # 注释里（不是判据丢了 —— 少一类判据会红上面的 len(rows) 断言）。要补就 diff
        # 两轮的 CHANNELS 集合，别动这里的断言迁就注释。
        self.assertEqual(len(verdicts.CHANNELS), 116)
        self.assertEqual(len(rows), len({r[:3] for r in rows}),
                         "规格表里有重复的 (phase, check, subject)：拼表时两条规则会抢一个读数")

    def test_breast_bones_are_inside_the_readback_subjects(self):
        """#66：那两根被改成指向乳头的骨必须出现在回读清单里。

        这条不多余：清单由「不在 biped_map」+「不是短骨」两条规则算出来。哪天有人把
        胸骨映射进 Biped、或导出侧把 tail 缩回 1cm 以内，这一组判据就会**安静地**不再
        覆盖 #66 —— 规格表照样全绿，而用户的「指着乳头」已经没人验了。
        """
        subjects = verdicts.accessory_dir_targets(self.bones, self.biped_map)
        names = {s.split("|", 1)[1] for s in subjects}
        for bone in ("右胸", "左胸"):
            self.assertIn(bone, names, f"{bone} 掉出了附属骨方向回读：#66 没人验收了")

    def test_skin_readback_expectations_come_from_the_input_tables(self):
        """M4b 的核心契约：蒙皮回读的每条尺都能在 bones.tsv / weights.tsv 里数出来。

        这几行是整套验收里第一批「Max 量场景、Python 量输入」的判据。期望值只要有一处
        其实是抄 Max 的读数（比如拿上一轮的 links_expected 当尺），这几条断言立刻红 ——
        因为它们比的是**这里重新数一遍**的数。
        """
        self.assertEqual(self.skin_expect.links_expected,
                         sum(len(pairs) for pairs in self.weights_rows),
                         "链路总数 = 权重对总数（香菱没有指向不蒙皮骨的权重，两者该相等）")
        self.assertEqual(self.skin_expect.bones_skinned, self.counts["kept_bones"],
                         "参与蒙皮的骨数：香菱每根都蒙，所以等于 meta 的 kept_bones —— "
                         "换一具有 skin_flag=0 的模型就不等了，那时这条尺只能来自骨骼表")
        self.assertEqual(self.skin_expect.verts_no_exp, 0)
        self.assertEqual(dict(self.skin_expect.links_by_bone),
                         {"Bip001 L Foot": 363, "Bip001 L Toe0": 74,
                          "Bip001 R Foot": 366, "Bip001 R Toe0": 74})
        rows = key(self.rows())
        for nm, want in self.skin_expect.links_by_bone:
            self.assertEqual((rows["skin_rb", "links", nm][3], rows["skin_rb", "links", nm][5]),
                             (str(want), "gate"), f"{nm} 的链路尺没落进规格表")
        self.assertEqual(rows["skin_rb", "links_expected", "-"][3], "26742")
        # 权重误差的容差来自 targets（尺的精度），期望值本身是 0（该完全对上）
        self.assertEqual((rows["skin_rb", "weight_err_max", "-"][3],
                          rows["skin_rb", "sum_err_max", "-"][3]), ("0", "0"))
        for check in ("zero_verts_pre", "repaired", "elapsed_s"):
            self.assertEqual(rows["skin_rb", check, "-"][5], "report",
                             f"{check} 是过程量：判红会把合法的修权当成缺陷")

    def test_zero_weight_pairs_get_a_gate_of_their_own(self):
        """#76：权重正好 0.0 的对不再冒充「丢掉的链路」，它有自己的尺。

        海灯节那 332 条（PMX 权重 <5e-7，`%.6f` 印成 `0.000000`）把 links_read /
        links_missing / link_diff_max / link_mismatch_bones 四行一起拖红，而对形变毫无
        影响 —— Max 的 Skin 收到 0.0 连骨号都不留。所以期望链路只数非零对，零权单列一行。
        这里钉的不是「那 332」这个数字（香菱一个都没有），是**这条尺的来历**：有人把
        过滤删回去，`links_expected` 立刻和这里重新数出来的非零对数不一致。
        """
        skinned = {i for i, r in enumerate(self.bones) if r.get("skin_flag") != "0"}
        zero = sum(1 for pairs in self.weights_rows for bone, w in pairs
                   if w <= 0.0 and bone in skinned)
        nonzero = sum(1 for pairs in self.weights_rows for bone, w in pairs
                      if w > 0.0 and bone in skinned)
        self.assertEqual(self.skin_expect.zero_links, zero, "零权链路数是数出来的，不是抄来的")
        self.assertEqual(self.skin_expect.links_expected, nonzero,
                         "期望链路里不许含零权对：含了就是要场景去挂一条挂不住的边")
        rows = key(self.rows())
        self.assertEqual((rows["skin_rb", "zero_links", "-"][3],
                          rows["skin_rb", "zero_links", "-"][5]),
                         (str(zero), "gate"),
                         "零权对没有自己的 gate：它下次只会伪装成一次链路缺失")

    def test_box_readback_expectations_come_from_limbs_and_widths(self):
        """M4c 的核心契约：骨盒回读的每条尺都能在 limbs.tsv 脚段列 / widths.tsv 里逐字找到。

        这三类尺以前只活在日志的**写入自述**里（`足背 … 宽=3.71424 高=8.65601`）；写完
        之后还有收尾转角与接趾 #scale 会改它。现在量的是最终场景，比的是输入文件 ——
        所以期望值一旦是从 Max 读数抄的，下面这几条断言立刻红。
        """
        foot, hands = verdicts.box_targets(self.limbs, self.widths)
        self.assertEqual([b.node for b in foot],
                         ["Bip001 L Foot", "Bip001 L Toe0",
                          "Bip001 R Foot", "Bip001 R Toe0"],
                         "脚链的回读顺序是「先足背后脚趾」，与 .ms 那一段一致")
        self.assertEqual([b.node for b in hands], [w["biped_target"] for w in self.widths],
                         "每行 widths 都该长出一条 hand_scale_cm，一根都不能漏")
        look = {r["parent_target"]: r for r in self.limbs if r.get("mode") == "foot"}
        look.update({r["child_target"]: r for r in self.limbs if r.get("mode") == "foot"})
        for box in foot:
            row = look[box.node]
            if box.length is None:                       # 足背：量踝处那一段
                self.assertEqual((box.width, box.height),
                                 (float(row["foot_w"]), float(row["foot_h"])))
            else:                                        # 脚趾：量脚尖处那一段 + 趾长
                self.assertEqual((box.width, box.height, box.length),
                                 (float(row["toe_w"]), float(row["toe_h"]),
                                  float(row["toe_len"])))

        rows = key(self.rows())
        # 期望值落进表里必须是同一个数（六位小数），kind 按「有没有输入尺」分岔：
        # 足背的长度不由任何输入列规定（接趾拉到的是压平投影），所以它是 report。
        self.assertEqual(rows["box_rb", "width_cm", "Bip001 L Foot"][3], "3.714245")
        self.assertEqual(rows["box_rb", "height_cm", "Bip001 L Foot"][3], "8.656011")
        self.assertEqual(rows["box_rb", "length_cm", "Bip001 L Foot"][3], "",
                         "足背的长度没有输入尺：填了期望值就是拿读数当判据")
        self.assertEqual(rows["box_rb", "length_cm", "Bip001 L Foot"][5], "report")
        self.assertEqual(rows["box_rb", "length_cm", "Bip001 L Toe0"][3], "3.569191")
        self.assertEqual(rows["box_rb", "length_cm", "Bip001 L Toe0"][5], "gate")
        # #79：沿指向的世界跨度用的是**同一把**独立的尺（limbs 的 toe_len），但它也只在
        # 有尺的那根骨上卡数 —— 足背没有尺，给它填期望值就等于把 Max 的读数当判据。
        self.assertEqual(rows["box_rb", "span_want_cm", "Bip001 L Toe0"][3], "3.569191")
        self.assertEqual(rows["box_rb", "span_want_cm", "Bip001 L Toe0"][5], "gate")
        self.assertEqual(rows["box_rb", "span_want_cm", "Bip001 L Foot"][3], "")
        self.assertEqual(rows["box_rb", "span_want_cm", "Bip001 L Foot"][5], "report")
        # 手部两把尺分开报：#scale 分量有来历（widths.tsv）所以是 gate；包围盒跨度
        # 与 #scale 的关系还没实测过，先让它说话（第二十一轮那条「永远不可能通过的
        # gate」就是这么来的）。
        self.assertEqual(rows["box_rb", "hand_scale_cm", "Bip001 L Hand"][3], "3.628600")
        self.assertEqual(rows["box_rb", "hand_scale_cm", "Bip001 L Hand"][5], "gate")
        self.assertEqual(rows["box_rb", "hand_box_cm", "Bip001 L Hand"][5], "report")
        self.assertEqual((rows["box_rb", "nodes_measured", "-"][3],
                          rows["box_rb", "read_fail", "-"][3]), ("16", "0"),
                         "量到 4 根脚骨 + 12 根手部骨，一个不许读不到")
        for check in ("nodes_measured", "read_fail"):
            self.assertEqual((rows["box_rb", check, "-"][4], rows["box_rb", check, "-"][5]),
                             ("0", "gate"), f"{check} 是计数，容差必须是 0")

    def test_widths_table_is_the_priced_twelve_rows_and_stays_in_centimeters(self):
        """widths.tsv 这一整轮**没人看守**：把 `_RADIUS_CAP_CM` / `_WIDTH_MIN_CM` 改成
        「PMX 单位 × scale」的那一次（香菱 auto scale = 7.6563），实测结果是
        行数 12→10、`Bip001 R Hand` 的粗细 3.6953→58.3399（采样窗把邻骨/手掌一起算了进来），
        而 tests/ 里没有一条比它 —— 只有 `tools/check_baseline.py` 这个脚本闸撞得上，
        我却在它之前把生产代码往错的方向改了一轮。

        所以钉三层：
          1. 行集（少一类手指就是少一条判据）；
          2. 两个已定价读数（`L Hand 3.6286` / `R Hand 3.6953`，来历 = 第二十六轮 M4c 从场景里
             读回来的 `box_rb/hand_scale_cm`，也是 `verdicts.FIXTURE_WIDTHS` 那两行）；
          3. 一条**不依赖具体数字**的量纲闸 —— 每个读数必须落在「手指/手掌能有的厘米宽」里。
             58.3399 那种数不该靠记住 3.6953 才抓得到：它一眼就是单位错了。
        """
        expect = {f"Bip001 {side} {part}"
                  for side in ("L", "R")
                  for part in ("Hand",) + tuple(f"Finger{i}" for i in range(5))}
        self.assertEqual({r["biped_target"] for r in self.widths}, expect,
                         "手部粗细表的行集变了：谁把一类手指剔掉了？")
        look = {r["biped_target"]: r["width"] for r in self.widths}
        self.assertEqual((look["Bip001 L Hand"], look["Bip001 R Hand"]),
                         ("3.6286", "3.6953"),
                         "手的粗细是 M4c（第二十六轮）回读过的读数，也是 FIXTURE_WIDTHS 的来历")
        for row in self.widths:
            w = float(row["width"])
            self.assertGreater(w, 0.5,
                               f"{row['biped_target']} 宽 {w}cm：比 5mm 还细的段量不出粗细，"
                               f"导出侧本就该把它丢掉（`_WIDTH_MIN_CM`），表里出现说明采样窗跑偏了")
            self.assertLess(w, 8.0,
                            f"{row['biped_target']} 宽 {w}cm —— 160cm 的角色上没有这么粗的手指，"
                            f"这是把邻骨/手掌算进来了（半径上限被乘上 scale 的老错）")

    def test_box_tolerance_mirrors_the_fitter_and_never_carries_a_float_tail(self):
        """容差 = 写入侧 `mmd2maxFitBoneExtent` 自己的退出条件（2%），并舍到 6 位。

        比它严 ⇒ 每轮红在浮点分辨率上；比它松 ⇒ 装歪了也绿。而 tol 要原样进
        VERDICTS 的列里，`0.17319268000000002` 这种尾巴会让两轮的同一行看起来像判据变了。
        """
        rows = [r for r in self.rows() if r[0] == "box_rb"
                and r[1] in ("width_cm", "height_cm", "length_cm",
                             "hand_scale_cm") and r[4]]
        self.assertTrue(rows)
        for row in rows:
            want = max(self.targets.BOX_TOL_REL * float(row[3]),
                       self.targets.BOX_TOL_MIN_CM)
            self.assertAlmostEqual(float(row[4]), round(want, 6), places=9,
                                   msg=f"{row[:3]} 的容差不是 2% 相对 + 0.02 地板")
            self.assertNotIn("e", row[4].lower(), f"容差写成了科学计数法：{row[4]}")
            self.assertLessEqual(len(row[4].split(".")[1]), 6,
                                 f"容差的小数位超过 6 位，两轮比对会有浮点尾巴：{row[4]}")
        # 这两个数本身也是定价过的，不许被顺手改成「更严一点」：2% 就是 .ms 那条
        # `abs(cur-target) <= 0.02*target` 的退出条件；0.02 厘米是 MAXScript
        # `as string` 的 6 位有效数字在最坏情况下的舍入量。
        self.assertEqual((self.targets.BOX_TOL_REL, self.targets.BOX_TOL_MIN_CM),
                         (0.02, 0.02))
        # 地板只在小于 1 厘米的目标上出场（脚趾高 1.86 的 2% = 0.037 > 0.02）
        self.assertEqual(self.targets.box_tol_cm(0.5), 0.02)
        self.assertEqual(self.targets.box_tol_cm(8.656011), 0.17312)
        self.assertEqual(self.targets.box_tol_cm(3.714245), 0.074285)

    def test_the_span_tolerance_adds_only_the_projection_bleed(self):
        """#79 那行的尺 = 2% 承诺 + 转角余量的投影溢量，一个都不多。

        `span_want_cm` 量的是角点投到指向方向上的跨度：盒子的长度轴偏 θ 度时，另外两根
        边按 `Σ 边长×|cos(轴,方向)|` 往这个方向里掺一份。转角那道闸承诺 θ ≤ DIR_TOL_DEG，
        所以不掺这一行就每轮红在自己允许的余量里（和上面那条 2% 的同款理由）；掺多了就
        不再是尺 —— 所以这里两头都钉：等式（公式独立重算一遍）+ 下限（必须仍比已知
        缺陷的差距小一个量级）。
        """
        foot, _ = verdicts.box_targets(self.limbs, self.widths)
        gated = [b for b in foot if b.length is not None]
        self.assertTrue(gated)
        rows = {r[2]: r for r in self.rows()
                if r[0] == "box_rb" and r[1] == "span_want_cm"}
        theta = math.radians(self.targets.DIR_TOL_DEG)
        for box in gated:
            row = rows[box.node]
            self.assertEqual(row[5], "gate", f"{box.node} 有独立尺却不判 = 白量")
            want = round(self.targets.box_tol_cm(box.length)
                         + math.hypot(box.width, box.height) * math.sin(theta)
                         + box.length * (1.0 - math.cos(theta)), 6)
            self.assertAlmostEqual(float(row[4]), want, places=9,
                                   msg=f"{box.node} 的溢量公式与 targets.span_tol_cm 分叉了")
            self.assertGreater(float(row[4]), self.targets.box_tol_cm(box.length),
                               "溢量项没进去：转角在 DIR_TOL 内合法，这一行会红在余量上")
            # 松到什么程度就不叫尺：已知错法里离期望**最近**的那一个也必须落在带外。
            # 趾盒那两根截面边（宽 / 高）谁朝前都是缺陷，它们到期望的距离必须 > 容差。
            closest = min(abs(box.length - box.width), abs(box.length - box.height))
            self.assertLess(float(row[4]), closest,
                            f"{box.node} 的容差吞掉了「截面边朝前」这种错（带宽 {row[4]}，"
                            f"已知错法只差 {closest:.3f}）")
        # 没有独立尺的那两根（足背）保持 report，容差列必须是空的
        for box in foot:
            if box.length is None:
                self.assertEqual((rows[box.node][3], rows[box.node][4],
                                  rows[box.node][5]), ("", "", "report"))

    def test_box_targets_refuse_a_foot_row_without_the_section_columns(self):
        """脚段少一列 = 中止，不许「这条判据不装」：删掉的判据不报错，只会让下一轮全绿。"""
        for key in ("foot_w", "foot_h", "toe_w", "toe_h", "toe_len"):
            limbs = [dict(r) for r in self.limbs]
            for row in limbs:
                if row.get("mode") == "foot":
                    row.pop(key)
            with self.assertRaises(ValueError) as got:
                verdicts.box_targets(limbs, self.widths)
            self.assertIn(key, str(got.exception))

    def test_box_targets_refuse_a_non_positive_section(self):
        """截面目标 0 或负数不是「这根骨没有粗细」，是导出侧没量到东西。"""
        widths = [dict(self.widths[0])]
        widths[0]["width"] = "0.0"
        with self.assertRaises(ValueError):
            verdicts.box_targets(self.limbs, widths)
        limbs = [dict(r) for r in self.limbs]
        for row in limbs:
            if row.get("mode") == "foot":
                row["foot_h"] = "-1"
        with self.assertRaises(ValueError):
            verdicts.box_targets(limbs, self.widths)

    def test_tolerance_rows_come_from_targets_not_from_literals(self):
        """脚趾容差/盒隙容差必须逐字等于 targets 里的那两个数。

        M2a 之后这五个数只有 Python 一份；规格表再从 .ms 或日志里读一遍就是第二份。
        """
        rows = key(self.rows())
        toe = rows["toe_snap", "residual_after_roll_cm", "Bip001 L Toe0"][4]
        gap = rows["foot_chain", "gap_cm", "Bip001 L Foot -> Bip001 L Toe0"][4]
        self.assertEqual((toe, gap), (str(self.targets.TOE_TOL_CM),
                                      str(self.targets.GAP_TOL_CM)))
        yaw = [r for k, r in rows.items()
               if k[0] == "bone_rb" and k[1] == "dir_deg"]
        self.assertTrue(yaw, "bone_rb/dir_deg 一行都没有：DIR_TOL_DEG 这把尺没人对撞了")
        self.assertEqual({r[4] for r in yaw}, {str(self.targets.DIR_TOL_DEG)})
        self.assertFalse([r for r in rows.values() if r[0] == "foot_yaw"],
                         "foot_yaw 是恒等式（期望值就是转角 pass 写进去的方向），已降级成日志")

    def test_hip_anchor_ruler_equals_the_priced_round_targets(self):
        """Python 算出来的三段尺，必须逐位对上第二十分钟那轮 Max **实际瞄准**的目标。

        这是「两边算的是同一个量法」唯一的实证形式：比的是 Max 日志里的 `目标=`，
        不是它自己的读数 —— 拿读数比读数就成了恒等式。对不上就说明两边的输入（坐标列
        /门槛/中点）已经分叉，那三行 gate 会从「永远红」变成「永远测不到真缺陷」。
        """
        required_files.require(PRICED_LOG)
        rows = key(self.rows())
        targets = dict(re.findall(
            r"段校验 Bip001 Pelvis -> (.+?) 目标=([0-9.eE+-]+)",
            PRICED_LOG.read_text(encoding="utf-8", errors="replace")))
        self.assertEqual(set(targets), set(verdicts.HIP_CHILDREN),
                         "日志里骨盆子段的目标行不齐：这条对照失去意义")
        for child, aimed in targets.items():
            got = rows["segment", "len_cm", f"Bip001 Pelvis -> {child}"][3]
            self.assertAlmostEqual(float(got), float(aimed), places=4,
                                   msg=f"{child}：Python={got} 但 Max 瞄准={aimed}")

    def test_no_verdict_row_is_graded_against_itself(self):
        """恒等式自查：expected 不许等于「Max 自己会报的那个数」的来源。

        这里能测的形式是：任何 gate 行的 expected 必须是导出侧的数（顶点数 / 肢段数 /
        desired_len / 0），不许是 `lock` 表里那种从日志抄来的读数。
        """
        locked = {str(v[0]) for v in verdicts.PLACEMENT_LOCKS.values()}
        locked |= {str(v) for v in verdicts.THIGH_KNEE_LOCKS.values()}
        locked |= {str(v) for v in verdicts.CHAIN_ANGLE_LOCKS.values()}
        locked |= {str(v) for row in verdicts.CONVERGE_LOCKS.values() for v in row}
        locked |= {str(verdicts.CHAIN_BROKEN_LOCK), str(verdicts.STRUCT_OVER_LOCK)}
        for row in self.rows():
            if row[5] != "gate":
                continue
            self.assertNotIn(row[3], locked,
                             f"{row[:3]} 的期望值是从上一轮读数抄的：那是恒等式，不是验收")


class AccessoryDirReadbackTests(unittest.TestCase):
    """#66 的验收半边：附属骨方向回读（bone_rb）的尺从哪来、谁能进这张表。

    为什么这条通道存在：日志里「附属骨父级统计：挂 Bone=214」只数了挂上谁，一根骨
    **指哪**一个字都没提。胸部骨那一刀改的是 tail（从网格量出来的乳头点），如果只在
    Python 侧量，那 #66 的全部证据就是「我自己算出来的那张表」—— 而 Max 那边改挂时
    `.parent` 一设就会把变换按父级重新解释（靠写回 worldTM 才没飞散），那个 try/catch
    静默失败的话，骨会跟着父级跑，日志照样全绿。
    """

    def test_mapped_rows_and_short_stubs_are_both_excluded(self):
        """两根排除规则一次测完：fixture 的前 3 行在 biped_map 里（被 Biped 接管），
        只有第 3 行 裙子A 是 BoneGeometry —— 所以期望的主体恰好一个。"""
        self.assertEqual(verdicts.accessory_dir_targets(BONES, BIPED_MAP),
                         ["3|裙子A"])

    def test_subject_carries_the_row_index_not_only_the_name(self):
        """`行号|骨名`：Max 那边重名会自动加后缀，只报骨名两边就对不上账。"""
        bones = [{"bone_name": "左胸", "head_x": "0", "head_y": "0", "head_z": "0",
                  "tail_x": "0", "tail_y": "0", "tail_z": "5"}]
        self.assertEqual(verdicts.accessory_dir_targets(bones, []), ["0|左胸"])

    def test_short_bone_is_skipped_on_the_same_threshold_as_max(self):
        """|tail-head| < ACCESSORY_STUB_CM 的是显示 stub（香菱 右手首 0.38cm），方向没意义。"""
        long_bone = {"bone_name": "长发", "head_x": "0", "head_y": "0", "head_z": "0",
                     "tail_x": "0", "tail_y": "0", "tail_z": "20"}
        stub = dict(long_bone, bone_name="右手首", tail_z="0.38")
        self.assertEqual(len(verdicts.accessory_dir_targets([long_bone, stub], [])), 1)

    def test_the_stub_threshold_reaches_max_through_the_cfg(self):
        """门限只有一个家（targets.STUB_CM），Max 那三处消费读的是 CFG 键，不是字面量。

        旧写法是「一份字面量抄两边 + 契约测试对撞」：改一边要等一整轮才以「规格表多出一条
        永远没人报的行」的形式暴露，而那个报错位置离根因很远。规则 IV 收的就是这类数 ——
        所以这条钉子现在反过来：`.ms` 里再出现 `< 1.0` 那种短骨比较就是违规。
        """
        self.assertEqual(verdicts.ACCESSORY_STUB_CM, targets.STUB_CM,
                         "verdicts 里那份必须真是别名，抄第二遍就又回到两份字面量")
        self.assertIn("MMD2MAX_STUBCM", targets.TARGETS,
                      "短骨门限没进 CFG = Max 那边只能自己发明这个数")
        block = SCRIPT[SCRIPT.index("fn mmd2maxVerifyAccessoryDirs"):
                       SCRIPT.index("fn mmd2maxCreateBones")]
        self.assertIn("if (length want) < MMD2MAX_STUBCM do", block,
                      ".ms 那一份门限没读 CFG：两边又会各判各的")
        self.assertNotIn("(length want) < 1.0", SCRIPT,
                         "字面量又回来了（本文件任何一处都算）")

    def test_missing_head_column_raises_instead_of_becoming_a_zero_bone(self):
        """缺列 = 两份导出产物分叉。当 (0,0,0) 会把真骨读成零长 stub，
        于是这一行从规格表里消失，而「这具模型的附属骨没被验收」不会有人注意到。"""
        broken = {"bone_name": "裙子A", "head_x": "0", "head_z": "0",
                  "tail_x": "0", "tail_y": "0", "tail_z": "5"}
        with self.assertRaises(ValueError):
            verdicts.accessory_dir_targets([broken], [])

    def test_unreadable_bone_index_raises(self):
        """biped_map 读不懂 = 这组判据没有尺，不许当成「没有骨被映射」。"""
        with self.assertRaises(ValueError):
            verdicts.accessory_dir_targets(
                BONES, [{"bone_index": "右足"}])

    def test_dir_deg_rows_are_gates_against_zero_with_the_priced_angle_tolerance(self):
        """期望 0 度（表里写的方向 == 场景里的局部 X 轴），容差用已经定价的 DIR_TOL_DEG，
        不新造一个数。"""
        rows = {(r[0], r[1], r[2]): r for r in spec()}
        row = rows[("bone_rb", "dir_deg", "3|裙子A")]
        self.assertEqual(row[3], "0")
        self.assertEqual(row[4], str(targets.DIR_TOL_DEG))
        self.assertEqual(row[5], "gate")
        self.assertEqual(row[6], "always",
                         "缺席必须是 FAIL：if_present 会让「一根也没量」变成绿灯")

    def test_measurable_expected_is_the_same_count_as_the_subject_rows(self):
        """聚合行与逐骨行同源：有人删了循环而留着聚合行，这里就会分叉。"""
        rows = spec()
        subjects = [r for r in rows if r[:2] == ("bone_rb", "dir_deg")]
        look = {(r[0], r[1], r[2]): r for r in rows}
        self.assertEqual(look[("bone_rb", "measurable", "-")][3], str(len(subjects)))
        self.assertEqual(look[("bone_rb", "measurable", "-")][5], "gate")
        self.assertEqual(look[("bone_rb", "over_tol", "-")][3], "0")
        self.assertEqual(look[("bone_rb", "read_fail", "-")][3], "0")

    def test_the_new_aggregates_are_reports_until_someone_pricies_them(self):
        """第一轮的新通道只许 report：没有来历的数字不许当尺（模块文档那条）。"""
        look = {(r[0], r[1], r[2]): r for r in spec()}
        for check in ("short_skipped", "worst_deg", "worst_bone",
                      "pivot_max_cm", "pivot_max_bone"):
            self.assertEqual(look[("bone_rb", check, "-")][5], "report",
                             f"{check} 还没被任何一轮定价过，不许卡数字")


if __name__ == "__main__":
    unittest.main()

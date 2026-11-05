"""M2c 判据校验器的契约：判据没过必须换成非 0 退出码，且不许把「没量到」读成通过。

这一层是整套 v4 验收的最后一道闸。它自己最容易长的两类毛病：

1. **静默通过**：缺一行、值不是数字、报告是上一轮的 —— 只要有一条被当成「没事」，
   全绿就又是假的（#24 的 100 通道假成功、#21 的旧截图蒙过判据，都是这一类）。
2. **判得太宽**：容差用 `<` 而不是 `<=`、锁行不判、报告里多出来的行没人管 ——
   表就退化成装饰。

所以这里的钉子全是「坏读数必须红」，只有 `when=if_present` 那一类允许安静缺席。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

import check_verdicts                                       # noqa: E402
import obj_run                                              # noqa: E402
from mmd2max import passes, verdicts                        # noqa: E402
from tests import required_files                            # noqa: E402

LIMBS = [{"parent_target": "Bip001 L Foot", "child_target": "Bip001 L Toe0",
          "desired_len": "11.228109", "mode": "foot",
          "foot_len": "14.097240", "toe_len": "3.569191", "tip_x": "7.049082",
          "tip_y": "-11.587054", "tip_z": "1.459004",
          "toe_w": "5.317024", "toe_h": "1.860916",
          "foot_w": "3.714245", "foot_h": "8.656011"},
         {"parent_target": "Bip001 L Thigh", "child_target": "Bip001 L Calf",
          "desired_len": "36.382676", "mode": "leg"},
         {"parent_target": "Bip001 Spine", "child_target": "Bip001 Spine1",
          "desired_len": "8.325677", "mode": "link"}]

# 骨盒回读（M4c）的手部那一批尺来自 widths.tsv：夹具给两根，正好长出
# `hand_scale_cm`（gate）与 `hand_box_cm`（report）各一对。
WIDTHS = [{"biped_target": "Bip001 L Hand", "width": "3.6286"},
          {"biped_target": "Bip001 R Hand", "width": "3.6953"}]

KEY = ("fit", "failed_segments", "-")
OPTIONAL = ("converge", "dir_over", "3")     # when=if_present 且是 lock
BOOL = ("foot_chain", "gap_covered", "Bip001 L Foot -> Bip001 L Toe0")
APPEAR = ("struct_dir", "measurable", "-")   # kind=report

# 闸门那三条不是「随便填个数」就行的：`passes.audit_gate` 会拿它们互相对账
# （遍数 ≤ 上限、ceiling 必须撞满、gate 必须达标位为 true）。夹具给的是第二十二轮
# 的真实出口 —— 撞上限、没达标。哪天夹具想演「提前收敛」，三条要一起改。
GATE_ACTUAL = {("converge", "iters", "-"): "3",
               ("converge", "exit_reason", "-"): "ceiling",
               ("converge", "gate_satisfied", "-"): "false"}


def spec_rows(rollback=False):
    return verdicts.build_spec(model=verdicts.PRICED_MODEL, vertex_count=18892,
                               limbs=LIMBS, morph_count=61, widths=WIDTHS,
                               bones=verdicts.parse_table(verdicts.FIXTURE_BONES)[1],
                               biped_map=verdicts.parse_table(
                                   verdicts.FIXTURE_BIPED_MAP)[1],
                               skin_expect=verdicts.FIXTURE_SKIN_EXPECT,
                               # #70：夹具必须带上胸骨三轴极值，否则规格表里一条 `max` 都没有，
                               # 判闸走过没走过那条分支测试看不出来 —— 第 36 轮的「未知 kind」
                               # 就是这样躲过整套测试、只在真跑里把整轮拒收的。
                               chest_bone=((-4.9157, -11.7451, 105.225345),
                                           (4.9157, -7.2398, 105.732513)),
                               rollback=rollback)


def keyed(rows):
    return {(r[0], r[1], r[2]): r for r in rows}


def report_text(spec, *, overrides=None, drop=(), extra=()):
    """按规格表造一份「全都报到期望值」的报告，再打补丁 / 删行 / 加行。

    行序按 **pass 注册表声明的时间轴** 排（`passes.report_order`），不是规格表的顺序：
    规格把 `pass/ran` 那一批行放在最后，照它写出来的报告是「所有读数都量在第一个 pass
    之前」，归因规则会一路报假红。夹具必须长得像 Max 真写出来的那份。
    """
    rows = []
    for row in spec:
        key = (row[0], row[1], row[2])
        if key in drop:
            continue
        actual = GATE_ACTUAL.get(key, "51" if row[5] == "report" else row[3])
        rows.append({"phase": key[0], "check": key[1], "subject": key[2],
                     "actual": overrides.get(key, actual) if overrides else actual})
    for key, actual in extra:
        rows.append({"phase": key[0], "check": key[1], "subject": key[2],
                     "actual": actual})
    lines = ["\t".join(verdicts.REPORT_HEADER)]
    lines += ["\t".join((r["phase"], r["check"], r["subject"], r["actual"]))
              for r in passes.report_order(rows)]
    return "\n".join(lines) + "\n"


def statuses(path):
    """把一份 CHECK 文件读成 {(phase, check, subject, kind): status}。"""
    _, rows = verdicts.parse_table(path.read_text(encoding="utf-8"))
    return {(r["phase"], r["check"], r["subject"], r["kind"]): r["status"]
            for r in rows}


class CheckerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.spec = spec_rows()
        (self.base / verdicts.SPEC_NAME).write_text(verdicts.spec_text(self.spec),
                                                    encoding="utf-8")
        self.by_key = keyed(self.spec)

    def tearDown(self):
        self.temp.cleanup()

    def write_report(self, **kw):
        path = self.base / verdicts.REPORT_NAME
        path.write_text(report_text(self.spec, **kw), encoding="utf-8")
        # 规格表必须不比报告新（校验器靠 mtime 挡「上一轮的报告」），测试里显式排序
        os.utime(path, (os.path.getmtime(path) + 5, os.path.getmtime(path) + 5))
        return path

    def run_check(self, **kw):
        self.write_report(**kw)
        code, lines = check_verdicts.check(self.base)
        return code, "\n".join(lines)

    # ── 好读数 ────────────────────────────────────────────────────
    def test_a_clean_round_passes(self):
        code, text = self.run_check()
        self.assertEqual(code, 0, text)
        self.assertIn("判据汇总", text)
        self.assertNotIn("[FAIL]", text)

    def test_check_file_carries_every_spec_row(self):
        """全量状态落盘：日志只留挂掉的行，事后要能回答「上一轮那条读数是多少」。"""
        self.run_check()
        header, written = verdicts.parse_table(
            (self.base / check_verdicts.CHECK_NAME).read_text(encoding="utf-8"))
        self.assertEqual(header[0], "status")
        self.assertEqual(len(written), len(self.spec))
        self.assertEqual({(r["phase"], r["check"], r["subject"]) for r in written},
                         set(self.by_key))

    def test_tolerance_boundary_is_inclusive(self):
        tol = float(self.by_key[("segment", "len_cm",
                               "Bip001 L Foot -> Bip001 L Toe0")][4])
        want = float(self.by_key[("segment", "len_cm",
                                "Bip001 L Foot -> Bip001 L Toe0")][3])
        # 正好压在容差上：过
        self.assertEqual(self.run_check(overrides={
            ("segment", "len_cm", "Bip001 L Foot -> Bip001 L Toe0"):
                repr(want + tol)})[0], 0)
        # 越过去一丝：红。容差写成严格小于的话，边界读数会一路骗人。
        self.assertEqual(self.run_check(overrides={
            ("segment", "len_cm", "Bip001 L Foot -> Bip001 L Toe0"):
                repr(want + tol * 1.001)})[0], 1)

    # ── 单向门槛（#69 的 min）─────────────────────────────────────
    def test_a_closeup_that_is_not_close_fails_the_round(self):
        """第 33 轮那张 18% 填充的胸部特写：图在、日志在、截图 pass 也 ran 了，
        而它能验收的东西是零。这一行必须把那一轮判红。
        """
        code, text = self.run_check(overrides={
            ("shot_rb", "fill_pct", "chest_front"): "18.0"})
        self.assertEqual(code, 1, text)
        self.assertIn("shot_rb/fill_pct chest_front", text)

    def test_a_shot_closer_than_the_floor_still_passes(self):
        """比门槛更近**不是缺陷**：这正是 min 而不是 gate 的理由 —— 拿 abs() 去比
        就得配容差，容差小了「拍得更清楚」会红，容差大了这条就是装饰。
        """
        code, text = self.run_check(overrides={
            ("shot_rb", "fill_pct", "chest_front"): "91.0"})
        self.assertEqual(code, 0, text)

    def test_the_aggregate_shot_counter_is_a_two_sided_gate(self):
        """`tags` 反过来必须是双向的：报多了（同一张被拍两遍）同样是这一轮不成立。
        与上面那条一起说明 min/gate 的分工不是随手选的。
        """
        # 比名单多一张 = 同一张被拍了两遍，这一轮不成立。写成 len+1 而不是抄一个数字：
        # #73 加 `bone_chest_top` 那天，抄死的 "20" 正好撞上新的期望值 20，这条会「绿着失效」。
        over = str(len(verdicts.SHOT_TAGS) + 1)
        code, text = self.run_check(overrides={("shot_rb", "tags", "-"): over})
        self.assertEqual(code, 1, text)
        self.assertIn("shot_rb/tags", text)

    # ── 自指的尺（#79）────────────────────────────────────────────
    SPAN = ("box_rb", "span_want_cm", "Bip001 L Toe0")
    FOOT_SPAN = ("box_rb", "span_want_cm", "Bip001 L Foot")

    def test_a_toe_box_that_points_its_thin_edge_forward_fails_the_round(self):
        """跨度行是 box_rb 里唯一「改标签也糊弄不过去」的那一把尺，所以它必须真的会红。

        夹具的趾盒三把尺：toe_len=3.569191（期望）/ toe_w=5.317024 / toe_h=1.860916。
        #75 之后 宽/高/长 三行都按盒账挑轴，于是「哪根边朝前」这句话在里面问不出来；
        这一行读的是角点投到指向方向上的跨度，谁朝前它就量谁。已知错法全在带外：
        薄边朝前（1.86）、宽边朝前（5.32）、第 48 轮那块板（0.5722）。
        """
        for bad in ("1.860916", "5.317024", "0.5722"):
            code, text = self.run_check(overrides={self.SPAN: bad})
            self.assertEqual(code, 1, f"薄边朝前（{bad}）居然绿了：{text}")
            self.assertIn("box_rb/span_want_cm", text)
        # 正常几何：长度轴与指向方向重合 ⇒ 跨度 = 棱长本身，溢量 0，绿。
        self.assertEqual(self.run_check(overrides={self.SPAN: "3.56919"})[0], 0)
        # 转角余量在带内（3 度时 5.32 那根边会掺 0.29cm）：这一行不许红在自己的承诺上。
        self.assertEqual(self.run_check(overrides={self.SPAN: "3.86"})[0], 0)
        # 足背那两根边没有独立的尺：填多离谱的数都不许改变退出码，否则期望值就是
        # 从读数抄来的（规则 II），而它会伪装成一条真判据。
        for wild in ("1.0", "999.0"):
            self.assertEqual(self.run_check(overrides={self.FOOT_SPAN: wild})[0], 0,
                             f"report 行 {wild} 居然参与判定：它没有尺")
        # 而它必须**被报到**：删掉这一行 = 少一把尺，规格里是 gate ⇒ 缺行就红。
        code, text = self.run_check(drop=(self.SPAN,))
        self.assertEqual(code, 1, text)
        self.assertIn("box_rb/span_want_cm", text)

    # ── 缺行 ──────────────────────────────────────────────────────
    def test_missing_mandatory_reading_is_a_failure(self):
        code, text = self.run_check(drop=(KEY,))
        self.assertEqual(code, 1)
        self.assertIn("fit/failed_segments", text)

    def test_missing_optional_reading_is_skipped_not_failed(self):
        """`when=if_present` 缺席只许记 SKIP：那一遍按设计可以整段不跑。

        但缺席**必须被说出来**（汇总里有「未跑 N」），否则「没跑」和「没这条判据」
        在日志里同形 —— 那是 v3 交付过的那种绿。
        """
        code, text = self.run_check(drop=(OPTIONAL,))
        self.assertEqual(code, 0, text)
        self.assertIn("未跑 1", text, "缺席必须被数出来：写成 0 就是「没跑」被读成「没这条判据」")

    def test_extra_report_line_fails_because_nobody_grades_it(self):
        code, text = self.run_check(extra=[(("fit", "mystery_count", "-"), "7")])
        self.assertEqual(code, 1)
        self.assertIn("没人判", text)

    def test_timeline_audit_participates_when_the_spec_has_pass_rows(self):
        """反证上面那条「不参与」不是逃生通道：今天的规格表有 pass/ran 行，
        报告整段不报收工边界就必须红，而且要说清是时间轴的问题。"""
        code, text = self.run_check(drop=tuple(
            (r[0], r[1], r[2]) for r in self.spec if r[0] == "pass"))
        self.assertEqual(code, 1)
        self.assertIn("时间轴问题", text)
        self.assertIn("没有 ran 行", text, "一条 pass 都没报，问题要说清是哪一段整段没跑")

    def test_pre_m3_spec_is_not_judged_by_todays_registry(self):
        """反过来：规格表里一条 `pass/ran` 都没有 = 这一轮的合同早于 M3。

        拿今天的注册表去数那一轮「少了 23 段」不是发现缺陷，是拿新合同判旧产物 ——
        冻结的 round22 会因此永远红，而一路红的审计等于没有审计。不参与要留一行
        说明（`未跑` 里能数到它），不能静默。
        """
        reduced = [r for r in self.spec if r[0] != "pass"]
        (self.base / verdicts.SPEC_NAME).write_text(verdicts.spec_text(reduced),
                                                    encoding="utf-8")
        path = self.base / verdicts.REPORT_NAME
        path.write_text(report_text(reduced), encoding="utf-8")
        os.utime(path, (os.path.getmtime(path) + 5, os.path.getmtime(path) + 5))
        code, lines = check_verdicts.check(self.base)
        text = "\n".join(lines)
        self.assertEqual(code, 0, text)
        self.assertIn("时间轴审计未参与", text)
        self.assertEqual(statuses(self.base / check_verdicts.CHECK_NAME)[
            ("pass", "timeline", "-", "timeline")], "SKIP",
            "不参与也要落进 CHECK 表：只写在日志里，事后查这张表看不出到底审过没有")

    # ── 坏读数 ────────────────────────────────────────────────────
    def test_over_tolerance_number_fails_and_names_the_row(self):
        code, text = self.run_check(overrides={KEY: "3"})
        self.assertEqual(code, 1)
        self.assertIn("fit/failed_segments", text)
        self.assertIn("[FAIL]", text)

    def test_non_numeric_actual_on_a_numeric_row_fails_instead_of_crashing(self):
        for junk in ("undefined", "", "-", "NaN-ish"):
            code, text = self.run_check(overrides={KEY: junk})
            self.assertEqual(code, 1, f"{junk!r} 居然过了：{text}")

    def test_bool_row_wants_the_word_not_a_number(self):
        self.assertEqual(self.run_check(overrides={BOOL: "true"})[0], 0)
        code, text = self.run_check(overrides={BOOL: "false"})
        self.assertEqual(code, 1, "端面没盖住 = 用户看得见的牙签，必须红")
        self.assertIn("foot_chain/gap_covered", text)
        # Max 那侧写 1/0 而不是 true/false 的话是格式错，不许被读成「盖住了」
        self.assertEqual(self.run_check(overrides={BOOL: "1"})[0], 1)

    def test_report_kind_row_with_empty_value_fails(self):
        """「必须出现」的行报了个空值 = 那一行是装饰，比缺席更坏，因为它看着像量过了。"""
        code, text = self.run_check(overrides={APPEAR: ""})
        self.assertEqual(code, 1, text)
        self.assertIn("struct_dir/measurable", text)

    def test_lock_row_is_graded_and_its_pass_says_no_improvement(self):
        """锁行照判超差，但 PASS 的说明必须写着「不等于达标」。

        `place_max_cm=5.6995` 这类读数钉的是**已定价的缺陷**；把它读成 6 项全过
        就是把债记成资产（README_HANDOVER 第 7 节反复提到这条）。
        """
        code, text = self.run_check(overrides={OPTIONAL: "9.9"})
        self.assertEqual(code, 1)
        self.assertIn("converge/dir_over", text)
        code, text = self.run_check()
        self.assertIn("没变得更差", text)

    def test_duplicate_report_lines_take_the_last_reading(self):
        """同一个键报两次：最后写的那行才是这一段的最终读数（复摆/接趾就是两次）。"""
        key = ("toe_snap", "residual_after_roll_cm", "Bip001 L Toe0")
        # 先坏后好 ⇒ 读最后那次，整轮仍然通过
        code, text = self.run_check(overrides={key: "99"}, extra=[(key, "0.02")])
        self.assertEqual(code, 0, text)
        # 先好后坏 ⇒ 同样读最后那次，必须红，并且说明里讲清「这键报了两次」
        code, text = self.run_check(extra=[(key, "99")])
        self.assertEqual(code, 1, text)
        self.assertIn("取最后一次", text)

    # ── 表本身不可用 ──────────────────────────────────────────────
    def test_missing_files_are_refused_not_read_as_green(self):
        code, lines = check_verdicts.check(self.base)   # 没有报告
        self.assertEqual(code, 2)
        self.assertIn("报告表不存在", "\n".join(lines))

    def test_header_drift_is_refused(self):
        path = self.write_report()
        path.write_text("phase\tcheck\tsubject\tvalue\n", encoding="utf-8")
        code, lines = check_verdicts.check(self.base)
        self.assertEqual(code, 2)
        self.assertIn("列名", "\n".join(lines))

    def test_stale_report_is_refused(self):
        """报告比规格表旧 = 这一轮的 Max 没写过报告，盘上的是上一轮的读数。

        obj_run 每轮开头删报告，所以正常跑碰不到这里；这条防的是「删报告那步没生效」
        或者有人手动复用旧目录 —— 那种情况下拿旧读数盖章，日志一样全绿。
        """
        path = self.write_report()
        old = os.path.getmtime(self.base / verdicts.SPEC_NAME) - 60
        os.utime(path, (old, old))
        code, lines = check_verdicts.check(self.base)
        self.assertEqual(code, 2)
        self.assertIn("上一轮", "\n".join(lines))

    def test_unknown_kind_in_spec_is_refused(self):
        spec = (self.base / verdicts.SPEC_NAME).read_text(encoding="utf-8")
        (self.base / verdicts.SPEC_NAME).write_text(
            spec.replace("\tgate\t", "\tmust_pass\t"), encoding="utf-8")
        self.write_report()
        code, lines = check_verdicts.check(self.base)
        self.assertEqual(code, 2)
        self.assertIn("未知 kind", "\n".join(lines))

    def test_the_kind_and_when_vocabulary_is_not_copied_into_the_checker(self):
        """判闸不许自己抄一份 kind/when 名单 —— 第 36 轮就栽在这上面。

        #70 给规格表加了 `max`，`verdicts.KINDS` 与 `grade()` 都改了，唯独这里那份
        字面量元组没改：真跑完的整轮被「规格表有未知 kind：max」拒收（退出码 2），
        而**测试全绿** —— 因为夹具没有胸骨极值，那两行退成 report，`max` 从来没经过
        这条校验路径。名单抄第二份的下场不是「多一条噪音」，是整轮验收没了。
        """
        src = (PROJECT_ROOT / "tools" / "check_verdicts.py").read_text(encoding="utf-8")
        self.assertIn("verdicts.KINDS", src, "kind 名单必须只有 verdicts 一处")
        self.assertIn("verdicts.WHENS", src, "when 名单必须只有 verdicts 一处")
        self.assertNotIn('"gate", "lock", "report"', src,
                         "这里又抄了一份 kind 名单：下一加一种判定就会复现第 36 轮")

    # ── 「表在、没人判」必须换成非 0 ──────────────────────────────
    def break_report(self, text):
        path = self.base / verdicts.REPORT_NAME
        path.write_text(text, encoding="utf-8")
        stamp = os.path.getmtime(path) + 5
        os.utime(path, (stamp, stamp))
        return path

    def test_a_header_only_spec_is_refused_not_read_as_green(self):
        """规格表只剩列名行 = 这一轮什么都还没判。

        以前它会一路走到汇总：循环零次、extras 空、时间轴不参与、`counts[FAIL]` 为 0
        ⇒ **退出码 0**，而 obj_run 只在产物齐之后才过这道闸 —— 于是「产物全在、判据表
        是空的」这种轮次在 GUI 里就是「完成」。M2c 的文件头写着「表是空的 ⇒ 2」，
        那一句在修之前只是承诺。
        """
        (self.base / verdicts.SPEC_NAME).write_text(verdicts.spec_text([]), encoding="utf-8")
        self.write_report()
        code, lines = check_verdicts.check(self.base)
        self.assertEqual(code, 2, "\n".join(lines))
        self.assertIn("一行判据都没有", "\n".join(lines))

    def test_a_header_only_report_is_refused(self):
        """报告表只剩列名行：Max 死在 ReportInit 之后、第一段读数之前。"""
        self.write_report()
        self.break_report("\t".join(verdicts.REPORT_HEADER) + "\n")
        code, lines = check_verdicts.check(self.base)
        self.assertEqual(code, 2, "\n".join(lines))
        self.assertIn("一行读数都没有", "\n".join(lines))

    def test_both_tables_empty_is_not_a_zero(self):
        """两张表都只剩列名 —— 这就是 M2c 立约时要堵的那个形状，两个都得报出来。"""
        (self.base / verdicts.SPEC_NAME).write_text(verdicts.spec_text([]), encoding="utf-8")
        self.break_report("\t".join(verdicts.REPORT_HEADER) + "\n")
        code, lines = check_verdicts.check(self.base)
        self.assertEqual(code, 2, "\n".join(lines))
        text = "\n".join(lines)
        self.assertIn("一行判据都没有", text)
        self.assertIn("一行读数都没有", text)

    def test_a_short_row_is_refused_instead_of_raising_keyerror(self):
        """被截断的读数行少一列，取 `row["actual"]` 会抛一条没有归属的 KeyError。"""
        self.break_report(report_text(self.spec) + "fit\tfailed_segments\n")
        code, lines = check_verdicts.check(self.base)
        self.assertEqual(code, 2, "\n".join(lines))
        self.assertIn("少", "\n".join(lines))

    def test_a_half_written_report_is_refused_instead_of_crashing(self):
        """超时兜底强杀 Max 时，报告最后那一行就是半个字符 —— 非法字节流。

        `UnicodeDecodeError` 是 `ValueError` 的子类，走同一条「表没法用」的路：
        日志里要看见「这一轮没判成」，而不是一条 traceback 把退出码顶成 1
        （1 号在 obj_run 里的意思是「判了、有 FAIL」，两种下一步完全不同）。
        """
        self.write_report()
        path = self.base / verdicts.REPORT_NAME
        path.write_bytes("\t".join(verdicts.REPORT_HEADER).encode("utf-8")
                         + b"\nfit\tfailed_segments\t-\t\xff\xfe")
        code, lines = check_verdicts.check(self.base)
        self.assertEqual(code, 2, "\n".join(lines))
        self.assertIn("读不出", "\n".join(lines))

    def test_a_zero_byte_report_is_refused(self):
        self.write_report()
        (self.base / verdicts.REPORT_NAME).write_bytes(b"")
        code, lines = check_verdicts.check(self.base)
        self.assertEqual(code, 2, "\n".join(lines))

    def test_an_unusable_table_exits_eight_from_the_runner(self):
        """空表要一路红到用户看得见的那只退出码 —— 8 = 两张表没法用。

        闸在 `check()` 里、退出码在 `obj_run.verdict_gate` 里，分两处才合得上一条链：
        只测前一处的话，映射写反（2 → 7，或者干脆丢掉）照样没人发现。
        """
        self.break_report("\t".join(verdicts.REPORT_HEADER) + "\n")
        self.assertEqual(obj_run.verdict_gate(self.base), 8)


class RollbackGradingTests(unittest.TestCase):
    """M4d：把 `ROLLBACK=true` 那张规格表真走一遍判闸。

    钉三件事，全都关于「退路会不会变成静默」：
      1. 一切合格的回滚轮照样是 0（回滚**没被触发**的那一轮不该因为开了开关就红）；
      2. **走过回滚的那一轮必须红** —— `foot_worse_bones` 数到的就是触发器本身，
         判闸不许因为「随后已经退回基准」把它洗成通过；否则 #38 那类回归会以
         「静默救场」的样子交付成一次成功；
      3. 开关开着却一条回滚读数都没报 = FAIL（不是 SKIP）：那说明 Max 那边的门控和
         Python 这边的合同分叉了；同一批行在关着的轮子里只许记 SKIP。
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp.cleanup()

    def grade(self, rollback, **kw):
        """在临时目录里造完整一轮（规格表 + 报告 + 判闸），返回 (码, 日志, 状态表)。"""
        base = Path(tempfile.mkdtemp(dir=self.temp.name))
        spec = spec_rows(rollback=rollback)
        (base / verdicts.SPEC_NAME).write_text(verdicts.spec_text(spec), encoding="utf-8")
        path = base / verdicts.REPORT_NAME
        path.write_text(report_text(spec, **kw), encoding="utf-8")
        os.utime(path, (os.path.getmtime(path) + 5, os.path.getmtime(path) + 5))
        code, lines = check_verdicts.check(base)
        return code, "\n".join(lines), statuses(base / check_verdicts.CHECK_NAME)

    @staticmethod
    def rb_keys(rollback):
        return tuple((r[0], r[1], r[2]) for r in spec_rows(rollback=rollback)
                     if r[0] == "rollback")

    # 「真退回时才存在」的那五条：夹具用「把它们从报告里删掉」来表示这一轮没触发回滚。
    RESTORE = ("foot_restored", "foot_restore_pos_cm", "foot_restore_box_cm",
               "foot_restore_scale_cm", "foot_restore_angle_deg")

    def test_a_clean_rollback_round_is_still_zero(self):
        drop = tuple(("rollback", c, "-") for c in self.RESTORE)
        code, text, got = self.grade(True, drop=drop)
        self.assertEqual(code, 0, text)
        self.assertEqual(got[("rollback", "foot_worse_bones", "-", "gate")], "PASS")
        # 没触发回滚 ⇒ 那五条本来就不存在：只许 SKIP，不许判红。
        for check in self.RESTORE:
            self.assertEqual(got[("rollback", check, "-", "gate")], "SKIP")

    def test_a_round_that_rolled_back_is_red_even_after_restoring(self):
        code, text, got = self.grade(
            True, overrides={("rollback", "foot_worse_bones", "-"): "2",
                             ("rollback", "guard_fired", "-"): "true",
                             ("rollback", "foot_restored", "-"): "true",
                             ("rollback", "foot_restore_pos_cm", "-"): "0",
                             ("rollback", "foot_restore_box_cm", "-"): "0",
                             ("rollback", "foot_restore_scale_cm", "-"): "0",
                             ("rollback", "foot_restore_angle_deg", "-"): "0"})
        self.assertEqual(code, 1, text)
        self.assertIn("rollback/foot_worse_bones", text)
        self.assertIn("本轮验收不通过", text)
        self.assertEqual(got[("rollback", "foot_restored", "-", "gate")], "PASS",
                         "退回本身合不合格是另一条判据；红的是「走过回滚」这件事")

    def test_a_carrier_that_swapped_the_axis_is_red(self):
        """合成判例：位置/盒/scale 三列全绿、锁骨却转了 89.5 度 ⇒ 必须判红。

        89.5225 这个数是第 27/28 轮**事后**量到的（`Bip001 L Clavicle` 世界长度轴
        -162.237 → -72.7144），但这两轮 `rollback/angle_deg` 自己读的都是 0.0：
        这一列量的是 load 那一刻，损伤是 load 之后才显形的，所以它抓不到那两轮
        （抓不到这件事写在 targets.py 与 max_import_obj.ms 的注释里，不在这儿掩盖）。
        那两轮的响是 placement 的 lock 行给的。这里要钉住的是尺本身的语义：
        一旦哪天真出现「load 完就换轴」，只靠 pos/box/scale 三把尺会把它放行 ——
        所以既断 angle_deg 红，也断那三条**仍然绿**。
        """
        code, text, got = self.grade(True, overrides={
            ("rollback", "angle_deg", "-"): "89.5225",
            ("rollback", "pos_cm", "-"): "5.08705e-05",
            ("rollback", "box_cm", "-"): "1.90735e-06",
            ("rollback", "scale_cm", "-"): "9.53674e-07"})
        self.assertEqual(code, 1, text)
        self.assertIn("rollback/angle_deg", text)
        for check in ("pos_cm", "box_cm", "scale_cm"):
            self.assertEqual(got[("rollback", check, "-", "gate")], "PASS",
                             f"{check} 在这一轮本来是合格的 —— 只靠它三样就会把缺陷放行")

    def test_carrier_that_failed_to_load_is_red(self):
        """载体自检读不回基准 ⇒ 三条 0 尺全红，这一轮不许交付。"""
        code, text, got = self.grade(True, overrides={
            ("rollback", "pos_cm", "-"): "3.7",
            ("rollback", "box_cm", "-"): "1.2",
            ("rollback", "scale_cm", "-"): "0.9"})
        self.assertEqual(code, 1, text)
        for check in ("pos_cm", "box_cm", "scale_cm"):
            self.assertEqual(got[("rollback", check, "-", "gate")], "FAIL")

    def test_switch_on_without_readings_fails_switch_on_with_absence_skips(self):
        """开关开着却没读数 ⇒ FAIL；开关关着没读数 ⇒ SKIP（未跑要说得出数）。"""
        code, text, got = self.grade(True, drop=self.rb_keys(True))
        self.assertEqual(code, 1, text)
        self.assertIn("rollback/saved", text)
        self.assertEqual(got[("rollback", "saved", "-", "gate")], "FAIL")

        code, text, got = self.grade(False, drop=self.rb_keys(False))
        self.assertEqual(code, 0, text)
        for check in ("saved", "loaded", "pos_cm", "foot_worse_bones"):
            self.assertEqual(got[("rollback", check, "-", "gate")], "SKIP")


class MinGradeTests(unittest.TestCase):
    """`grade()` 里 min 那一支的边界（不写文件，只判一行）。

    min 是第四种 kind（gate / lock / report 之外），#69 为它开的：取景填充率只有
    「不得低于」这一条边。这里的四条断言钉的是「超出没有代价、缺席与坏数照样红」。
    """

    ROW = dict(zip(verdicts.SPEC_HEADER,
                   ("shot_rb", "fill_pct", "chest_front", "45.0", "0", "min",
                    "if_present")))

    def grade(self, actual):
        return check_verdicts.grade(self.ROW, actual)

    def test_floor_and_above_pass(self):
        for actual in ("45", "45.0", "63.4", "100.0"):
            self.assertEqual(self.grade(actual)[0], check_verdicts.PASS, actual)

    def test_below_the_floor_fails(self):
        for actual in ("44.9", "18.0", "-1.0", "0.0"):
            self.assertEqual(self.grade(actual)[0], check_verdicts.FAIL, actual)

    def test_minus_one_is_the_broken_projection_sentinel_not_a_number_to_trust(self):
        status, note = self.grade("-1.0")
        self.assertEqual(status, check_verdicts.FAIL)
        self.assertIn("门槛", note)

    def test_a_non_number_never_passes(self):
        for actual in ("", "  ", "-", "true", "abc"):
            self.assertEqual(self.grade(actual)[0], check_verdicts.FAIL, actual)


class MaxGradeTests(unittest.TestCase):
    """`grade()` 里 max 那一支（#70 为它开的），与 min 互为镜像。

    取景框的下沿只需要「不得高于胸骨的实际下缘」：框比要求的更宽（更低）不是缺陷，
    画面上只是多带一点肚子 —— 拿 gate（abs 两边卡）去要求它，就会把「保守一点」判成
    失败，而那种失败没有任何人要修。这一支存在的唯一理由是第 35 轮那种框：带的上沿
    只到 101.17，而胸骨在 105.23 —— 那是**读数比要求的还高**，必须有方向地说出来。
    """

    ROW = dict(zip(verdicts.SPEC_HEADER,
                   ("shot_rb", "chest_box_lo", "y", "-11.7451", "0", "max",
                    "if_present")))

    def grade(self, actual):
        return check_verdicts.grade(self.ROW, actual)

    def test_ceiling_and_below_pass(self):
        for actual in ("-11.7451", "-26.1781", "-30.0", "-12.5"):
            self.assertEqual(self.grade(actual)[0], check_verdicts.PASS, actual)

    def test_above_the_ceiling_fails(self):
        # -5.0 是第 36 轮的真读数（框的下沿在骨头之上，整只骨被砍掉），-11.73 只高出
        # 0.02 —— 第 35 轮的 101.17 差得更远，但边界必须 inclusive：高一点点也是
        # 「框砍掉了骨头的下缘」。
        for actual in ("-11.73", "-5.0", "7.1932"):
            self.assertEqual(self.grade(actual)[0], check_verdicts.FAIL, actual)

    def test_the_failure_names_the_ceiling(self):
        status, note = self.grade("-5.0")
        self.assertEqual(status, check_verdicts.FAIL)
        self.assertIn("上限", note)

    def test_a_non_number_never_passes(self):
        """空串 / `-` 当「没量到」混过去 = 这一行退成装饰，正是本项目反复踩的那类。"""
        for actual in ("", "  ", "-", "true", "abc"):
            self.assertEqual(self.grade(actual)[0], check_verdicts.FAIL, actual)


class ObjRunWiringTests(unittest.TestCase):
    """obj_run 的退出码必须跟着判据走 —— 否则 GUI 照报「完成」。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.spec = spec_rows()
        (self.base / verdicts.SPEC_NAME).write_text(verdicts.spec_text(self.spec),
                                                    encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def report(self, **kw):
        path = self.base / verdicts.REPORT_NAME
        path.write_text(report_text(self.spec, **kw), encoding="utf-8")
        os.utime(path, (os.path.getmtime(path) + 5, os.path.getmtime(path) + 5))

    def test_clean_report_is_zero(self):
        self.report()
        self.assertEqual(obj_run.verdict_gate(self.base), 0)

    def test_failing_verdict_is_7_not_0(self):
        self.report(overrides={("fit", "failed_segments", "-"): "3"})
        self.assertEqual(obj_run.verdict_gate(self.base), 7)

    def test_unusable_channels_is_8_not_0(self):
        # 只有规格表、没有报告：Max 那侧一行都没报（本轮死在 ReportInit 之前）
        self.assertEqual(obj_run.verdict_gate(self.base), 8)


class FrozenRoundReplayTests(unittest.TestCase):
    """第二十二轮真跑出来的三张表冻结在 baselines/：判据层从此能离线回归。

    这是整套 M2 的落点。M2c 之前，「验收」只能在有 Max 的机器上跑一遍再看日志；
    现在把**真 Max 写的报告**和**真 Python 写的规格**钉成静态文件，改坏判据（把
    `<=` 写成 `<`、把 always 行当成可选、把 lock 当达标）不需要启动 3ds Max 就会红。
    比对的是**逐行状态**，不是汇总数字 —— 汇总只说「121 绿」，说不清哪一行原本是红。
    """

    FROZEN = PROJECT_ROOT / "baselines" / "round22_MMD2MAX.VERDICTS"
    FROZEN_REPORT = PROJECT_ROOT / "baselines" / "round22_MMD2MAX.REPORT"
    FROZEN_CHECK = PROJECT_ROOT / "baselines" / "round22_MMD2MAX.CHECK"

    def test_the_frozen_round_replays_exactly_the_frozen_verdicts(self):
        required_files.require(self.FROZEN, self.FROZEN_REPORT, self.FROZEN_CHECK)
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            # 复制而不是就地判：check() 会把 CHECK 写回 base，判据测试不许动基线文件
            spec = base / verdicts.SPEC_NAME
            report = base / verdicts.REPORT_NAME
            spec.write_bytes(self.FROZEN.read_bytes())
            report.write_bytes(self.FROZEN_REPORT.read_bytes())
            os.utime(report, (os.path.getmtime(spec) + 5, os.path.getmtime(spec) + 5))
            code, lines = check_verdicts.check(base)
            got = statuses(base / check_verdicts.CHECK_NAME)   # 出了 with 目录就没了

        self.assertEqual(code, 0, "\n".join(lines))
        self.assertEqual([ln for ln in lines if ln.lstrip().startswith("[FAIL]")], [])
        want = statuses(self.FROZEN_CHECK)
        # round22 的三张表里还没有 pass/ran 通道（M3 之后 Python 才生成那些行），
        # 所以时间轴审计对这一轮**不参与**，只落一行说明。允许多出来的只有这一行：
        # 别的任何差异都说明「离线判出的」和「实跑当时落盘的」不是同一件事。
        extra = {k: v for k, v in got.items() if k not in want}
        self.assertEqual(extra, {("pass", "timeline", "-", "timeline"): "SKIP"},
                         "冻结轮次唯一允许多出的是那行「审计未参与」的 SKIP")
        missing = {k for k in want if k not in got}
        self.assertEqual(missing, set(), "离线判出的行数比实跑当时少")
        differ = {k: (got[k], want[k]) for k in want if got[k] != want[k]}
        self.assertEqual(differ, {}, "离线判出的逐行状态和实跑当时落盘的不一致")

    def test_the_frozen_report_is_a_real_max_artifact(self):
        """冻结的报告必须是 **Max 写的**：UTF-8 BOM + CRLF 是它的指纹。

        `createFile … encoding:#utf8` 落 BOM，Python 侧永远不写。报告若哪天变成
        无 BOM，说明它被人在中间改过（或被 Python 覆盖写），那这张表的证据价值就没了。

        这个断言现在还得靠 `.gitattributes` 里 `baselines/round* -text` 才成立：仓库若按
        文本处理这份报告，检出就会按那台机的 `core.autocrlf` 改写行尾，于是「是不是 Max
        直接写的那份」变成看 git 配置脸色 —— 证据件的字节不该由克隆它的那台机决定。
        """
        required_files.require(self.FROZEN_REPORT)
        raw = self.FROZEN_REPORT.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"),
                        "报告没有 BOM：这不是 Max 直接写出来的那份")
        self.assertIn(b"\r\n", raw)


if __name__ == "__main__":
    unittest.main()

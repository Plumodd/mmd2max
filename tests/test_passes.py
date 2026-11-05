"""M3 pass 注册表的契约：声明的是**因果**，不是某一轮报告里发生过什么。

这个模块自己最容易长出的三类毛病，钉子按这三类摆：

1. **声明与脚本分叉**：注册表说有 23 段收工边界，`.ms` 里只写了 22 行、或者某道
   复核的 `mmd2maxReport` 落进了别人的段落。静态对撞（不用启动 Max）就能抓到，
   所以必须静态抓 —— 实跑一轮十几分钟，抓不到第二次的回归。
2. **审计被改宽**：STALE 写成 `<` 而不是 `<=`、覆盖只查 optional、归因不看 `reads`、
   闸门只查 `exit_reason` 不查 `iters`。每一条都要有「坏时间轴必须红」的钉子，
   否则这套东西就退化成注释。
3. **恒等式回归**：照着某轮报告的顺序把 `requires`/`invalidates` 抄出来 —— 那正是
   本项目反复踩的「拿被验收一方自己的输出当判据」。这里的夹具是**按声明造的**，
   真实时间轴只用冻结轮次对撞一次（`test_the_frozen_round_order_matches_the_registry`），
   两边不一致时必须讲清是谁错了，而不是把声明改成和实跑一样。
"""
import re
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

import obj_run                                              # noqa: E402
from mmd2max import passes, verdicts                          # noqa: E402

SCRIPT = (PROJECT_ROOT / "scripts" / "max_import_obj.ms").read_text(encoding="utf-8")

# 报告里 pass 边界与读数行的样子（.ms 侧的字面量写法）
RAN_RE = re.compile(r'mmd2maxReport\s+"pass"\s+"ran"\s+"(\w+)"')
READ_RE = re.compile(r'mmd2maxReport\s+"([^"]+)"\s+"([^"]+)"')


def strip_comments(text: str) -> str:
    """注释里满是「以前这里写的是 `for passNo = 1 to 3`」这类历史，静态对撞只看代码。"""
    out = re.sub(r"/\*.*?\*/", lambda m: " " * len(m.group(0)), text, flags=re.S)
    return re.sub(r"--[^\n]*", lambda m: " " * len(m.group(0)), out)


CODE = strip_comments(SCRIPT)

CLAIMED = {pair for p in passes.PASSES for pair in p.reads}
GATE_ACTUAL = {"iters": "3", "exit_reason": "ceiling", "gate_satisfied": "false"}


def row(phase, check, subject="-", actual="1"):
    return {"phase": phase, "check": check, "subject": subject, "actual": actual}


def segment_rows(pass_id: str) -> list[dict[str, str]]:
    """这一段真跑起来会写出的行：它认领的读数 + 它自己 phase 的读数 + 收工边界。"""
    p = passes.BY_ID[pass_id]
    rows = []
    if p.reads:                                   # 认领过的 (phase, check) 只报这些
        rows += [row(ph, ck) for ph, ck in p.reads]
    else:
        rows += [row(ph, ck) for ph, ck in sorted(verdicts.CHANNELS)
                 if ph in p.writes and (ph, ck) not in CLAIMED]
    if p.gate:                                    # 带闸门的段必须把三件事都报出来
        rows += [row(p.gate[0], "iters", "-", GATE_ACTUAL["iters"]),
                 row(p.gate[0], p.gate[1], "-", GATE_ACTUAL["exit_reason"]),
                 row(p.gate[0], "gate_satisfied", "-", GATE_ACTUAL["gate_satisfied"])]
    rows.append(row(passes.PASS_PHASE, passes.RAN_CHECK, pass_id))
    return rows


def clean_timeline() -> list[dict[str, str]]:
    """按注册表声明的时间轴造一份「Max 真会写出来的那种顺序」的报告行。"""
    return [r for p in passes.PASSES for r in segment_rows(p.id)]


def without(rows, **kw):
    """删行 / 换值：`without(rows, phase="mesh")` 或 `rows=..., subject="converge"`。"""
    return [r for r in rows if not all(r.get(k) == v for k, v in kw.items())]


def move(rows, predicate, before_pass_id):
    """把第一条匹配的行搬到 `before_pass_id` 那段收工边界之前（制造串段/陈旧）。"""
    out = list(rows)
    for i, r in enumerate(out):
        if predicate(r):
            moved = out.pop(i)
            break
    else:
        raise AssertionError("夹具里没有这条行")
    at = next(j for j, r in enumerate(out)
              if r.get("subject") == before_pass_id
              and r.get("check") == passes.RAN_CHECK)
    out.insert(at, moved)
    return out


class RegistryTests(unittest.TestCase):
    """注册表自己得先站得住：一张自相矛盾的声明比没有更糟。"""

    def test_validate_registry_is_clean(self):
        self.assertEqual(passes.validate_registry(), [])

    def test_every_pass_has_exactly_one_ran_boundary(self):
        ids = passes.pass_ids()
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(i.replace("_", "").isascii() and i.replace("_", "").isalnum()
                            for i in ids), "pass id 要进报告第三列，也得能在日志里 grep")

    def test_readings_are_attributable_to_some_pass(self):
        """每个会被报的 phase 都必须有作者：没作者的行只能靠 orphan 变红，
        而「这条读数归谁」就成了没人认领的问题。"""
        for phase in sorted({ph for ph, _ in verdicts.CHANNELS}):
            if phase == passes.PASS_PHASE:
                continue          # pass/ran 这一列的主体是 pass id 本身，不是作者
            self.assertIn(phase, passes.PHASE_AUTHORS, f"{phase} 没有声明的作者")

    def test_multi_author_phases_are_disambiguated_by_reads(self):
        """同一个 phase 有多个作者时，每个作者必须在 `reads` 里认领具体那条 check。

        不然归因规则只能判「这条读数属于其中任意一个」，于是「接趾那段把复摆的旧读数
        重报一遍」这种错就抓不到 —— 它看起来完全合法。
        """
        for phase, authors in passes.PHASE_AUTHORS.items():
            if len(authors) < 2:
                continue
            for pid in authors:
                self.assertTrue(passes.BY_ID[pid].reads,
                                f"{pid} 是 {phase} 的作者之一却没认领具体读数")

    def test_moves_expands_to_every_scene_reading(self):
        """`moves=True` 是「作废全部场景读数」的缩写，声明里不许出现第三种半截名单。

        手抄必漏一项，而漏掉那一项恰好是 STALE 唯一能抓到的回归；反过来 fit/pose/
        converge 是 pass 的**自述**（干了几件事），不存在重测一说，混进这张表会让每个
        早期 pass 都作废自己的读数，规则退化成噪音。只作废其中几条（比如某 pass 只把
        趾残差带跑）是允许的，但必须显式写出来并被这条测试拦住 —— 今天还没有这种段。
        """
        self.assertEqual(set(passes.POSE_FACTS),
                         {"bone_rb", "box_rb", "foot_chain", "placement",
                          "segment", "struct_dir", "toe_snap"})
        for p in passes.PASSES:
            self.assertIn(set(p.invalidates), (set(passes.POSE_FACTS), set()),
                          f"{p.id} 的作废名单是半截的：要么 moves=True 走全集，"
                          "要么显式写出这几条并给物理理由")
        for self_described in ("fit", "pose", "converge", "thigh_roll"):
            self.assertNotIn(self_described, passes.POSE_FACTS,
                             f"{self_described} 是自述，不该被算进场景读数")

    def test_declared_invalidations_have_a_remeasuring_author(self):
        """作废一条读数之后必须有人再量一次，否则这条声明在当前脚本里就不成立。

        这条是「写了个永远满足不了的规则」的防线：它之后没人量 ⇒ 每次实跑都 STALE
        ⇒ 于是没人再信 STALE 这条判据。
        """
        order = passes.ORDER_BY_ID
        for p in passes.PASSES:
            for phase in p.invalidates:
                if phase in p.writes:
                    continue
                authors = [order[a] for a in passes.PHASE_AUTHORS[phase]]
                self.assertGreater(max(authors), order[p.id],
                                   f"{p.id} 声明作废 {phase}，但它之后没人再量")

    def test_only_the_converge_loop_declares_iterations(self):
        """「跑几遍由读数决定」的只有收敛循环：别处若也开始自定遍数，闸门就得跟着扩。"""
        self.assertEqual([p.id for p in passes.PASSES if p.max_iters > 1], ["converge"])
        self.assertEqual(passes.max_iters("converge"), passes.CONVERGE_MAX_ITERS)
        self.assertEqual([p.id for p in passes.PASSES if p.gate], ["converge"])

    def test_spec_rows_declare_each_pass_once(self):
        rows = passes.spec_rows()
        self.assertEqual([r[2] for r in rows], list(passes.pass_ids()))
        for r in rows:
            self.assertEqual(r[:3], ("pass", "ran", r[2]))
            self.assertEqual(r[3:6], ("1", "0", "gate"),
                             "收工边界的尺是「恰好跑到 1」：跑到 2 是重做，跑到 0 是没跑")

    def test_optional_passes_are_the_only_if_present_ones(self):
        rows = {(r[2], r[6]) for r in passes.spec_rows()}
        self.assertEqual({pid for pid, when in rows if when == "if_present"},
                         {p.id for p in passes.PASSES if p.optional})
        self.assertIn(("foot_reach", "if_present"), rows)
        self.assertIn(("segment_check", "always"), rows,
                      "终局复核不许被标成可选：它没跑就等于这一轮没验收")

    def test_when_override_only_loosens_and_rejects_nonsense(self):
        got = {r[2]: r[6] for r in passes.spec_rows({"converge": "if_present"})}
        self.assertEqual(got["converge"], "if_present")
        with self.assertRaises(ValueError):
            passes.spec_rows({"converge": "maybe"})


class ScriptBoundaryContractTests(unittest.TestCase):
    """`.ms` 与注册表的静态对撞：不启动 Max 就能抓到的那部分，必须静态抓。

    能静态判的只有「边界在不在、读数目在哪条边界之前」。脚本里 pass 的**执行**顺序
    来自 `mmd2maxMain` 的调用序，而 `ran` 行散在各个被调函数的体内 —— 所以文本顺序
    不等于执行顺序（`fit` 的读数在 `fn mmd2maxFitSegments` 里，比 `biped_create` 的
    边界还早），跨段的归属只能拿真报告判（`audit` 的归因规则 + 冻结轮对撞）。
    """

    def bounds(self):
        return {m.group(1): m.start() for m in RAN_RE.finditer(CODE)}

    def test_ran_lines_in_the_script_are_exactly_the_declared_passes(self):
        found = RAN_RE.findall(CODE)
        self.assertEqual(sorted(found), sorted(passes.pass_ids()),
                         "注册表与脚本的 pass 边界分叉：一边多一段或少一段")
        self.assertEqual(len(found), len(set(found)),
                         "同一个 pass 报了两次收工边界（注册表说恰好一次）")

    def test_readings_are_emitted_before_their_own_boundary(self):
        """每条读数行必须排在**它自己作者**的收工边界之前（ran 是收工，不是开工）。

        抓的是「把读数挪到边界之后」这一类：那一行会被归因判给下一段，或者落在所有
        段之外 —— 数值全对，时间轴已经不描述这个 pass 了。
        """
        bounds = self.bounds()
        late = []
        for m in READ_RE.finditer(CODE):
            if m.group(1) == passes.PASS_PHASE:
                continue
            authors = passes.authors_of(m.group(1), m.group(2))
            if not authors:
                late.append(f'{m.group(1)}/{m.group(2)} 注册表里没有作者')
                continue
            spots = [bounds[a] for a in authors if a in bounds]
            if m.start() > min(spots):
                late.append(f'{m.group(1)}/{m.group(2)} 排在 {"、".join(authors)} '
                            "的收工边界之后")
        self.assertEqual(late, [])

    def test_optional_boundaries_are_emitted_inside_their_own_guard(self):
        """可以整段不发生的 pass，边界也得跟着门控走。

        无条件报 `ran physics_build` 等于宣称跑过：注册表里它是 optional，规格行退成
        `if_present`，于是「没跑却报了 ran」比「没报」难查得多 —— 它会给 STALE 和归因
        喂一个根本不存在的段。物理这一段就在 `mmd2maxMain` 里，直接看它的写法。
        """
        region = CODE[CODE.index("local physFile"):CODE.index("clearSelection()")]
        body = region[region.index('if physFile != "" do'):].split("do", 1)[1]
        self.assertTrue(body.lstrip().startswith("("),
                        "物理的收工边界又回到无条件执行了：if 后面必须是块")
        self.assertLess(body.index("mmd2maxBuildPhysics"),
                        body.index('mmd2maxReport "pass" "ran" "physics_build"'))

    def test_converge_bound_is_downlinked_from_the_registry(self):
        """遍数上限只有一个来源：注册表 → CFG → Max。任何一侧写死都是第二个事实。"""
        self.assertEqual(passes.max_iters("converge"), passes.CONVERGE_MAX_ITERS)
        self.assertIn('"MMD2MAX_CONVERGE_MAX"', CODE, "Max 侧又不从 CFG 读遍数了")


class AuditCleanTimelineTests(unittest.TestCase):
    """好时间轴必须**一条问题都没有** —— 否则上面那堆红全是噪音，没人会去看。"""

    def test_declared_timeline_produces_no_problems(self):
        self.assertEqual(passes.audit(clean_timeline()), [])

    def test_optional_passes_may_be_absent_entirely(self):
        rows = [r for r in clean_timeline()
                if not (r["phase"] == passes.PASS_PHASE and r["subject"] == "foot_reach")]
        self.assertEqual(passes.audit(rows), [],
                         "整段没跑（开关关掉）不该被覆盖规则判红")

    def test_mandatory_pass_may_not_be_absent(self):
        rows = [r for r in without(clean_timeline(), phase="segment")
                if not (r["phase"] == passes.PASS_PHASE
                        and r["subject"] == "segment_check")]
        problems = passes.audit(rows)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("没有 ran 行", problems[0])
        self.assertIn("必经步骤", problems[0])

    def test_report_order_is_a_stable_permutation(self):
        rows = clean_timeline()
        shuffled = list(reversed(rows))
        ordered = passes.report_order(shuffled)
        self.assertEqual(sorted(map(str, ordered)), sorted(map(str, rows)))
        self.assertEqual(passes.report_order(ordered), ordered, "排序不幂等")
        # 归因用的是**行序**，所以排序只许把段内相对顺序保持住（稳定排序）
        self.assertEqual([r["subject"] for r in ordered
                          if r["phase"] == passes.PASS_PHASE], list(passes.pass_ids()))


class AuditMutationTests(unittest.TestCase):
    """每条规则都要有一枚「坏时间轴必须红」的钉子。

    这套审计的价值全在「坏了会响」：任何一条规则被写宽（`<=` 改成 `<`、只查
    optional、归因不看 `reads`），这一组就会静默通过 —— 于是下一轮的绿又是假的。
    """

    def test_duplicate_boundary_is_a_problem(self):
        rows = clean_timeline()
        rows.insert(rows.index(row(passes.PASS_PHASE, passes.RAN_CHECK, "toe_box")),
                    row(passes.PASS_PHASE, passes.RAN_CHECK, "toe_box"))
        problems = passes.audit(rows)
        self.assertTrue(any("恰好跑一次" in p for p in problems), problems)

    def test_unknown_pass_id_is_a_problem(self):
        rows = clean_timeline()
        rows.insert(1, row(passes.PASS_PHASE, passes.RAN_CHECK, "sneaky_pass"))
        problems = passes.audit(rows)
        self.assertTrue(any("注册表不认识" in p for p in problems), problems)

    def test_requires_violation_is_a_problem(self):
        """把段校验挪到脚链复核之前：数值一样，但前提不成立。"""
        rows = move(clean_timeline(),
                    lambda r: r["phase"] == passes.PASS_PHASE
                    and r["subject"] == "segment_check",
                    "foot_chain_check")
        problems = passes.audit(rows)
        self.assertTrue(any("顺序错" in p and "foot_chain_check" in p for p in problems),
                        problems)

    def test_reading_moved_into_an_earlier_segment_is_stale(self):
        """段校验被搬到收尾转角之前 ⇒ 它量的已经不是最终场景。

        这条是 M3 的存在理由：逐行拼表看 `segment/len_cm` 完全正常，只有时间轴知道
        它在骨盒最后一次挪动之前量完了。
        """
        rows = move(clean_timeline(),
                    lambda r: r["phase"] == "segment" and r["check"] == "len_cm",
                    "thigh_roll")
        problems = passes.audit(rows)
        self.assertTrue(any(p.startswith("STALE") and "segment" in p for p in problems),
                        problems)
        self.assertTrue(any("是由" in p and "那一段量的" in p for p in problems),
                        "同一条读数还要报出归因问题：STALE 说时间轴，归因说谁量的")

    def test_attribution_names_the_wrong_owner(self):
        rows = move(clean_timeline(),
                    lambda r: r["phase"] == "mesh" and r["check"] == "vertex_count",
                    "pose_initial")
        problems = passes.audit(rows)
        wrong = [p for p in problems if "vertex_count" in p]
        self.assertEqual(len(wrong), 1, problems)
        self.assertIn("skin_apply", wrong[0], "问题文本要说清声明的作者是谁")

    def test_multi_author_phase_uses_reads_not_phase(self):
        """接趾那一段报「扭转后复摆」的读数：按 phase 看合法，按 `reads` 看串了门控。"""
        rows = move(clean_timeline(),
                    lambda r: r["phase"] == "toe_snap"
                    and r["check"] == "residual_after_roll_cm",
                    "figure_exit")
        problems = passes.audit(rows)
        self.assertTrue(any("residual_after_roll_cm" in p and "toe_snap_roll" in p
                            for p in problems), problems)

    # ── 闸门自相一致 ──────────────────────────────────────────────
    def converge_rows(self, iters="3", exit_reason="ceiling", satisfied="false"):
        """改收敛段那三条读数（默认值 = 第二十二轮的真实出口：撞上限、没达标）。"""
        actual = {"iters": iters, "exit_reason": exit_reason,
                  "gate_satisfied": satisfied}
        return [dict(r, actual=actual[r["check"]])
                if r["phase"] == "converge" and r["check"] in actual else r
                for r in clean_timeline()]

    def test_iters_above_the_declared_ceiling_is_a_problem(self):
        problems = passes.audit(self.converge_rows(iters="4"))
        self.assertTrue(any("超过注册表声明的上限" in p for p in problems), problems)

    def test_iters_below_one_is_a_problem(self):
        problems = passes.audit(self.converge_rows(iters="0"))
        self.assertTrue(any("而不是" in p and "循环一次都没跑" in p for p in problems),
                        problems)

    def test_non_numeric_iters_is_reported_not_crashed(self):
        problems = passes.audit(self.converge_rows(iters="undefined"))
        self.assertTrue(any("不是数字" in p for p in problems), problems)

    def test_ceiling_with_unused_iterations_is_a_problem(self):
        """报「撞上限」却只跑了两遍（上限三）：这句出口是编出来的。"""
        problems = passes.audit(self.converge_rows(iters="2"))
        self.assertTrue(any("ceiling" in p and "不是撞上限停的" in p for p in problems),
                        problems)

    def test_gate_exit_without_satisfied_flag_is_a_problem(self):
        """这正是被治掉的那类谎：循环结束了被当成收敛了。"""
        problems = passes.audit(self.converge_rows(iters="1", exit_reason="gate",
                                                  satisfied="false"))
        self.assertTrue(any("循环结束了被当成收敛了" in p for p in problems), problems)

    def test_gate_exit_with_the_flag_set_is_clean(self):
        self.assertEqual(passes.audit(self.converge_rows(
            iters="1", exit_reason="gate", satisfied="true")), [])

    def test_unknown_exit_reason_is_a_problem(self):
        problems = passes.audit(self.converge_rows(exit_reason="converged"))
        self.assertTrue(any("不是 gate/ceiling 之一" in p for p in problems), problems)

    def test_missing_exit_reason_is_a_problem(self):
        rows = without(clean_timeline(), phase="converge", check="exit_reason")
        problems = passes.audit(rows)
        self.assertTrue(any("必须报它到底是为什么停的" in p for p in problems), problems)

    def test_missing_iters_is_a_problem(self):
        rows = without(clean_timeline(), phase="converge", check="iters")
        problems = passes.audit(rows)
        self.assertTrue(any("没法核对" in p for p in problems), problems)

    def test_gate_is_only_checked_when_that_pass_ran(self):
        rows = [r for r in clean_timeline()
                if not (r["phase"] == passes.PASS_PHASE and r["subject"] == "converge")]
        rows = without(rows, phase="converge")
        problems = passes.audit(rows)
        self.assertTrue(any("converge" in p and "没有 ran 行" in p for p in problems))
        self.assertFalse(any("为什么停" in p for p in problems),
                         "整段没跑时闸门规则不该再追问出口 —— 那是重复报同一个错")


class CfgDownlinkTests(unittest.TestCase):
    """注册表 → CFG → Max 的下行链路：值只有一个来源，Max 只读不写。"""

    def test_cfg_keys_include_the_converge_bound(self):
        self.assertIn("MMD2MAX_CONVERGE_MAX", obj_run.CFG_KEYS)

    def test_cfg_summary_lines_up_with_the_declared_keys(self):
        """CFG 的键名单与 .ms 的必读名单必须同源（缺键在 Max 侧是中止）。"""
        text = (PROJECT_ROOT / "tools" / "obj_run.py").read_text(encoding="utf-8")
        self.assertIn('"MMD2MAX_CONVERGE_MAX": str(passes.max_iters("converge"))', text,
                      "遍数又写死在装配侧了")


class FrozenRoundCollisionTests(unittest.TestCase):
    """拿真 Max 写出的报告对撞声明：注册表与实跑分叉的唯一检测点。

    这一条是「不许拿报告抄声明」的另一半 —— 声明先立好，再用真实时间轴审它一次。
    对不上只有两种可能：注册表错了（要改声明并写清物理理由），或者脚本里那段真的
    挪了位置而没人更新注册表（要改脚本）。不许把这条测试删掉。
    """

    FROZEN_REPORT = PROJECT_ROOT / "baselines" / "round77_MMD2MAX.REPORT"

    @unittest.skipUnless(FROZEN_REPORT.is_file(), "需要 baselines/round77 的报告")
    def test_the_frozen_round_order_matches_the_registry(self):
        _, rows = verdicts.parse_table(
            self.FROZEN_REPORT.read_text(encoding="utf-8-sig"))
        rows = [r for r in rows if r.get("phase")]
        ordered = passes.report_order(rows)
        changed = [(a["phase"], a["check"], a["subject"], b["phase"], b["check"],
                    b["subject"])
                   for a, b in zip(rows, ordered) if a is not b]
        self.assertEqual(changed, [],
                         "真报告的行序与注册表声明的时间轴分叉：先讲清是谁错了")

    @unittest.skipUnless(FROZEN_REPORT.is_file(), "需要 baselines/round77 的报告")
    def test_the_frozen_round_timeline_is_clean(self):
        _, rows = verdicts.parse_table(
            self.FROZEN_REPORT.read_text(encoding="utf-8-sig"))
        problems = passes.audit([r for r in rows if r.get("phase")])
        # 冻结点从 round26 挪到 round33：bone_rb 是第 33 轮第一次有读数的通道，
        # 而拿**早于它**的报告做时间轴审计，等于让 audit 永远看不见这一段的归属 ——
        # 「必经 pass 缺 ran」那条规则会把新 pass 判红，于是只能给它开口子，口子一旦
        # 留着，bone_dir_verify 就永远不会因「某轮没跑」而红（box_verify 在 round23
        # 就是这么被放掉的，第 26 轮才收回）。round26 本身没有 rollback 行，所以这次
        # 挪冻点没有丢任何覆盖面。
        # 同一条理由把冻结点再往前挪到 round52（#78 的 toe_box_final 之后第一轮）：
        # 那个 pass 每轮必跑，冻结报告里没有它的 ran 行就等于「注册表列了必经步骤、
        # 实跑却没人证明它跑过」—— 这正是 audit 要抓的形状，不能反过来给它开 if_present
        # 口子。round52 的 phase 集合是 round33 的超集（多出 shot_rb），挪冻点仍然只赚
        # 覆盖面、不丢覆盖面。
        # 这次挪到 round77 的形状和前两次**不一样**，得说清：round52→round77 的 phase
        # 集合少了 foot_yaw，不是超集。原因是 #99 按规则 II（只报告不修的检查非法）把
        # 脚骨偏航那两行恒等式从判据表退回日志 —— 注册表现在声称 foot_chain_check
        # 只写 foot_chain，于是拿 round52 那份还带 foot_yaw 行的报告对撞，行序测试会
        # 在 470 行上分叉，而分叉的原因是「冻结件比注册表旧」，不是「脚本挪了位置没人
        # 改注册表」。这两种分叉对撞出来长得一模一样，只有把冻结点推到与当前脚本同源
        # 的那一轮才分辨得了；留在旧冻结件上，这条闸从此只会每轮重复报同一个已知陈旧。
        # 少掉的 foot_yaw 不算丢覆盖面：没有任何注册表条目声称写它，audit 要证的「必经
        # pass 都有 ran 行」在 round77 上仍然全部成立（本轮实跑：通过 526 / 失败 0）。
        self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()

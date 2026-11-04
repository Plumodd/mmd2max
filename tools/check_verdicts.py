"""判据校验器（M2c）：把规格表与 Max 报回来的读数拼起来，算出每一行的状态。

这一层是「判」的唯一持有者
--------------------------
v3 的验收写在 MAXScript 里，`成功=39 失败=0` 由脚本自己数、自己判、自己打日志，
于是出现过两类真实事故（都记在 README_HANDOVER 第 4 节）：

- `WM3_NumberOfChannels` 报 100 就以为建了 100 条通道，实测前 8 条全是 `- empty -`（#24）。
- 「撞 3 遍上限退出」和「判据真达标」在日志上长得一模一样，全绿交付。

所以 M2b 把「尺」搬到 Python（`MMD2MAX.VERDICTS`），Max 只许往 `MMD2MAX.REPORT`
追加 `phase/check/subject/actual` 四个字段。这个文件负责**判**：两张表来自不同进程，
任何一侧被改坏都会被另一侧抓到。

退出码只有三种，而且刻意保守
-----------------------------
- `0` 每一行都过（`lock` 行也算过 —— 见下面的「锁不是目标」）。
- `1` 至少一条 FAIL：超差、缺必须的读数、报告里有规格表不认识的行、格式读不出。
- `2` 这两张表本身没法用（文件缺失、列名不对、表是空的）。

绝不返回「警告算通过」。本项目反复踩的坑就是「绿日志 + 没有证据」：只要判据没被真读，
这一轮就不许报成成功。

锁（lock）不是目标
------------------
`kind=lock` 的行钉的是**已定价的缺陷**（Pelvis 落位 5.6995 之类），PASS 的意思是
「没变得更差」，不是「达标」。所以汇总里把 lock 单独一行列出，并且写明它不改进度：
把它读成「6 项全过」就是把债当资产。
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mmd2max import passes  # noqa: E402
from mmd2max.passes import PASS_PHASE  # noqa: E402
# 名单只从 verdicts 一处取（`from ... import` 会漏掉新加的判定，第 36 轮就是这么栽的）。
from mmd2max import verdicts  # noqa: E402
from mmd2max.verdicts import (  # noqa: E402
    GLOBAL,
    REPORT_HEADER,
    REPORT_NAME,
    SPEC_HEADER,
    SPEC_NAME,
    parse_table,
)

CHECK_NAME = "MMD2MAX.CHECK"

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"

# 状态行的顺序也是报告的阅读顺序：先看挂掉的，再看没量到的，最后才看通过的。
ORDER = {FAIL: 0, SKIP: 1, PASS: 2}


def is_bool_text(text: str) -> bool:
    return text.strip().lower() in ("true", "false")


def grade(row: dict[str, str], actual: str) -> tuple[str, str]:
    """一行判据 + Max 的读数 → (状态, 说明)。

    数值比对用 `abs(actual - expected) <= tol`；`min`/`max` 行是单向的
    （分别要求 `actual >= expected` / `actual <= expected`，超出没有代价）；
    容许的「读数不是数字」只有一种：布尔行（`端面覆盖=true`）。
    除此之外读不成数字就是 FAIL —— 让空串或 `-` 通过等于把那一行变成装饰。
    """
    kind = row["kind"]
    if kind == "report":
        if not actual.strip():
            # 行报了但值是空的：Max 那侧的读数没算出来。让它通过等于这条判据是装饰。
            return FAIL, "只要求出现的行，值是空的"
        return PASS, f"只要求出现，读数={actual}"
    if kind == "min":
        # 单向门槛：低于 expected 才是缺陷，高于它没有代价（见 verdicts 模块文档）。
        # 不许退化成 abs() 比较 —— 那样「拍得比要求更近」也会红。
        try:
            limit = float(row["expected"])
            got = float(actual)
        except ValueError:
            return FAIL, f"门槛 {row['expected']}，但读数不是数字：{actual!r}"
        if got >= limit:
            return PASS, f"{got} ≥ 门槛 {limit}"
        return FAIL, f"{got} < 门槛 {limit}：这一行的读数比要求的还低"
    if kind == "max":
        # `min` 的镜像：高于 expected 才是缺陷（例：取景带的下沿必须低过胸骨，
        # 带框得更宽不是缺陷）。同样不许退化成 abs()。
        try:
            limit = float(row["expected"])
            got = float(actual)
        except ValueError:
            return FAIL, f"上限 {row['expected']}，但读数不是数字：{actual!r}"
        if got <= limit:
            return PASS, f"{got} ≤ 上限 {limit}"
        return FAIL, f"{got} > 上限 {limit}：这一行的读数比要求的还高"
    if is_bool_text(row["expected"]):
        if actual.strip().lower() == row["expected"].strip().lower():
            return PASS, f"= {row['expected']}"
        return FAIL, f"期望 {row['expected']}，实报 {actual}"
    try:
        want = float(row["expected"])
        limit = float(row["tol"])
        got = float(actual)
    except ValueError:
        return FAIL, f"期望 {row['expected']}（容差 {row['tol']}），" \
                     f"但读数不是数字：{actual!r}"
    drift = abs(got - want)
    if drift <= limit + 1e-12:
        return PASS, f"|{got} - {want}| = {drift} ≤ {limit}"
    return FAIL, f"|{got} - {want}| = {drift} > {limit}"


def read_pair(spec_path: Path, report_path: Path) -> tuple[
        list[dict[str, str]], dict[tuple[str, str, str], tuple[str, int]],
        list[dict[str, str]], list[str]]:
    """读两张表，返回 (规格行, 报告索引, 报告原序行, 问题列表)。问题非空 ⇒ 调用方判 2 号退出码。

    报告索引按 (phase, check, subject) 收敛：Max 的复摆/接趾段可能对同一个主体报两次
    （#38 就是「转角后复测」+「接趾后复测」），**后写的赢** —— 那才是这一段的最终读数。
    原序行单独返回：M3 的 pass 时间轴审计要的正是「没被收敛掉的行序」，
    拿 `look` 去审等于把时间轴压成一个字典。
    """
    problems: list[str] = []
    for path, header, label in ((spec_path, SPEC_HEADER, "规格表"),
                                (report_path, REPORT_HEADER, "报告表")):
        if not path.is_file():
            problems.append(f"{label}不存在：{path}")
    if problems:
        return [], {}, [], problems

    spec_head, spec_rows = parse_table(spec_path.read_text(encoding="utf-8"))
    if spec_head != SPEC_HEADER:
        problems.append(f"规格表列名不是 {SPEC_HEADER}，而是 {spec_head}")
    report_head, report_rows = parse_table(report_path.read_text(encoding="utf-8"))
    if report_head != REPORT_HEADER:
        problems.append(f"报告表列名不是 {REPORT_HEADER}，而是 {report_head}")
    if problems:
        return [], {}, [], problems

    for row in spec_rows:
        # 名单只有 verdicts.KINDS/WHENS 一处：这两行以前各写一份字面量元组，#70 加 `max`
        # 时源码侧改了、这里没跟上，于是规格表里两条合法的行被判成「未知 kind」而整轮
        # 拒收 —— 同一件事写两处，其中一处必然滞后。
        if row["kind"] not in verdicts.KINDS:
            problems.append(f"规格表有未知 kind：{row['kind']}（{row['phase']}/{row['check']}）")
        if row["when"] not in verdicts.WHENS:
            problems.append(f"规格表有未知 when：{row['when']}（{row['phase']}/{row['check']}）")

    # 陈旧检查：报告必须比规格表**新**。obj_run 每轮开头删报告，所以正常跑不会命中；
    # 命中的是「上一轮的报告留着，这一轮 Max 死在 ReportInit 之前」那一种 —— 不挡住
    # 就是拿上一轮的绿给这一轮盖章（#21 那一类，只是这次骗的是验收本身）。
    if report_path.stat().st_mtime < spec_path.stat().st_mtime:
        problems.append(f"报告比规格表旧：报告 {report_path.stat().st_mtime:.0f} < "
                        f"规格 {spec_path.stat().st_mtime:.0f}，这是上一轮留下的读数")

    look: dict[tuple[str, str, str], tuple[str, int]] = {}
    for row in report_rows:
        key = (row["phase"], row["check"], row["subject"])
        # 计数：同一个键被报了几次，用来回答「Max 是不是把同一根骨测了两遍」
        look[key] = (row["actual"], look.get(key, ("", 0))[1] + 1)
    return spec_rows, look, report_rows, problems


def check(base: Path, *, spec_path: Path | None = None,
          report_path: Path | None = None) -> tuple[int, list[str]]:
    """跑完一轮后的判据汇总。返回 (退出码, 要打的日志行)。

    退出码：0 全过 / 1 有 FAIL / 2 两张表没法用（缺文件、列名分叉、陈旧）。

    除了逐行拼表，这里还跑一遍 M3 的**时间轴审计**（`mmd2max.passes.audit`）：
    读数对不对是一回事，读数**是在什么时候量的**是另一回事。撞上限退出被当成收敛、
    收尾之后又挪了骨却拿收尾前量的段长交付，这两种在逐行拼表里全是绿的 —— 一条
    STALE 的时间轴能把它们判红，所以它算 FAIL（这一轮不成立），不算「表没法用」。
    """
    spec_path = spec_path or base / SPEC_NAME
    report_path = report_path or base / REPORT_NAME
    spec_rows, look, report_rows, problems = read_pair(spec_path, report_path)
    if problems:
        return 2, [f"判据校验中止：{p}" for p in problems]

    keys = {(r["phase"], r["check"], r["subject"]) for r in spec_rows}
    lines: list[str] = []
    results: list[tuple[str, ...]] = []
    counts: Counter[str] = Counter()
    by_kind: Counter[tuple[str, str]] = Counter()

    for row in spec_rows:
        key = (row["phase"], row["check"], row["subject"])
        hit = look.get(key)
        if hit is None:
            if row["when"] == "always":
                status, note = FAIL, f"必须出现的读数没有报（expected {row['expected'] or '—'}）"
            else:
                status, note = SKIP, "本轮没有这一步（when=if_present）"
        else:
            actual, times = hit
            status, note = grade(row, actual)
            if times > 1:
                note += f"（同键报了 {times} 次，取最后一次）"
        counts[status] += 1
        by_kind[(row["kind"], status)] += 1
        results.append((status, row["phase"], row["check"], row["subject"],
                        row["expected"], row["tol"], row["kind"],
                        (hit[0] if hit else ""), note))

    # 报告里有、规格表没有的行：这些读数**永远不会被判**。宁可判红也不要静默忽略——
    # 静默会让「Max 新加了一段报数但没人给它定尺」看起来和通过一样。
    extras = sorted(set(look) - keys)
    if extras:
        counts[FAIL] += len(extras)
        for key in extras:
            results.append((FAIL, key[0], key[1], key[2], "", "", "orphan",
                            look[key][0], "规格表里没有这一行 = 这个读数没人判"))

    # 时间轴审计（M3）：覆盖 / 恰好一次 / requires 顺序 / STALE / 归因 / 闸门自相一致。
    # 每条问题单独占一行落进 CHECK，和逐行判据混在一起排 —— 只 FAIL 不 SKIP，
    # 因为「这一轮的时间轴不成立」没有部分通过可言。
    #
    # 但它只审**这份规格表声明过的合同**：注册表是现在的代码，规格表是那一轮 Python
    # 写下来的。表里一条 `pass/ran` 都没有 = 那一轮还早于 M3，拿今天的 23 个 pass 去
    # 数它「少了 23 行」不是发现缺陷，是拿新合同判旧产物（冻结的 round22 就会被这样
    # 判红，于是这条审计再也没人当回事）。不参与也要落一行 SKIP 说明为什么没审。
    if any(r["phase"] == PASS_PHASE for r in spec_rows):
        for problem in passes.audit(report_rows):
            counts[FAIL] += 1
            by_kind[("timeline", FAIL)] += 1
            results.append((FAIL, PASS_PHASE, "timeline", GLOBAL, "", "", "timeline",
                            "", problem))
    else:
        counts[SKIP] += 1
        by_kind[("timeline", SKIP)] += 1
        results.append((SKIP, PASS_PHASE, "timeline", GLOBAL, "", "", "timeline", "",
                        "规格表里没有 pass/ran 行：这一轮的合同早于 M3，时间轴审计不参与"))

    results.sort(key=lambda r: (ORDER[r[0]], r[1], r[2], r[3]))
    for status, phase, check_name, subject, expected, tol, kind, actual, note in results:
        if status == PASS:
            continue  # 通过的行不刷屏；全量落在 CHECK 文件里
        lines.append(f"  [{status}] {phase}/{check_name} {subject} "
                     f"({kind}) 期望={expected or '—'} 实报={actual or '—'} —— {note}")

    # 参与「硬失败」计数的判定种类。写成一个列表而不是把名字嵌进那句话里：#70 加 `max`
    # 时那句话还写着 gate/lock/min，读数已经按新种类在算了，账面上却看不见 —— 名单与
    # 文案各抄一份，必然有一份滞后。
    counted = ("gate", "lock", "min", "max")
    gate_lock = sum(by_kind.get((kind, FAIL), 0) for kind in counted)
    gate_lock += (by_kind.get(("orphan", FAIL), 0)
                  + by_kind.get(("timeline", FAIL), 0))
    lines.append(
        f"判据汇总：通过 {counts[PASS]}，失败 {counts[FAIL]}，未跑 {counts[SKIP]} "
        f"（{'/'.join(counted)} 失败 {gate_lock}）；"
        f"报告共 {len(look)} 行，规格 {len(spec_rows)} 行")
    # 时间轴问题单独说一句：它不是「某个数超差」，是「这一轮读数不描述最终场景」。
    # 混在 gate 里说，读的人会用「改一下容差」去处理一个顺序 bug。
    if by_kind.get(("timeline", FAIL), 0):
        lines.append(f"  其中 {by_kind[('timeline', FAIL)]} 条是时间轴问题"
                     "（pass 覆盖 / 顺序 / STALE / 归因 / 闸门自相一致）")
    elif by_kind.get(("timeline", SKIP), 0):
        lines.append("  时间轴审计未参与：本轮规格表里没有 pass/ran 行（那一轮早于 M3）")
    # 锁单独说一句：它 PASS 只保证没退步。汇总里不写这句就会被读成「6 条全达标」。
    locks = by_kind.get(("lock", PASS), 0)
    if locks:
        lines.append(f"  其中 {locks} 条是 lock（已定价的缺陷，PASS = 没变得更差，不等于达标）")
    if extras:
        lines.append(f"  报告里有 {len(extras)} 行规格表不认识：{', '.join(f'{a}/{b}' for a, b, _ in extras[:5])}")
    if counts[FAIL]:
        lines.append("⇒ 本轮验收不通过")

    write_check_file(base / CHECK_NAME, results)
    code = 1 if counts[FAIL] else 0
    return code, lines


def write_check_file(path: Path, results: list[tuple[str, ...]]) -> None:
    """把每一行的状态落盘（线格式 v2）。

    日志里只留挂掉的行，是为了让人看得下去；表全量落盘，是为了「这轮到底量到了什么」
    事后还能查 —— 只留绿日志的话，下一轮出问题就没人能回答「上一轮那条读数是多少」。
    """
    # 列名从规格表头派生，但**去掉 `when`**：when 是判定的输入（这行没读数算不算失败），
    # 不是判定结果。以前整份 SPEC_HEADER 直接拼进表头，行里少一列 ⇒ 从 actual 起整行
    # 错位一列，读它的人拿到的是「when=18892」。这里按行的真实字段数列，两边不许分叉。
    header = (("status",) + tuple(f for f in SPEC_HEADER if f != "when")
              + ("actual", "note"))
    if results and len(results[0]) != len(header):
        raise ValueError(f"CHECK 行有 {len(results[0])} 个字段，表头却有 {len(header)} 列")
    lines = ["\t".join(header)]
    # actual 是从 Max 那侧读来的自由文本，note 里也带它：一个制表符就能把这张表
    # 的列错位（下一轮读它的人看到的是「少一列」，不是「有人报了个带制表符的值」）。
    def safe(field: str) -> str:
        return field.replace("\t", " ").replace("\r", " ").replace("\n", " ")
    lines.extend("\t".join(safe(field) for field in row) for row in results)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="把 MMD2MAX.VERDICTS 与 MMD2MAX.REPORT 拼起来判（M2c）")
    ap.add_argument("base", help="导出目录（两张表都在里面）")
    ap.add_argument("--spec", default=None, help="规格表路径，默认 <目录>/MMD2MAX.VERDICTS")
    ap.add_argument("--report", default=None, help="报告路径，默认 <目录>/MMD2MAX.REPORT")
    args = ap.parse_args()

    base = Path(args.base).resolve()
    code, lines = check(base,
                        spec_path=Path(args.spec) if args.spec else None,
                        report_path=Path(args.report) if args.report else None)
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())

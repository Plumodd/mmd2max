"""判据通道（M2b）：Python 写「该量什么、允许差多少」，Max 只报数。

为什么验收不能留在 MAXScript 里
--------------------------------
v3 的验收长成这样：Max 侧自己数 `成功=39 失败=0`、自己判断「这轮算成功」，日志全绿
就是交付。这有两个洞（都是实际踩过的）：

1. **写的人给自己打分**。`WM3_NumberOfChannels` 报 100 时脚本以为建了 100 个通道，
   实测通道 1..8 全是 `- empty -`；`built=61` 照样打进日志（#24）。计数是写入侧的
   自述，不是回读。
2. **判据在只有人能看见的地方**。`落位最大=5.6995`、`结构向 夹角>3.0度=6` 这些数一直
   在日志里，但没有任何东西检查它们；「撞 3 遍上限退出」和「判据真达标」在日志上
   长得一样（README_V4 §4 M3 那一行说的就是这件事）。

所以这一层把「判」搬到 Python：`build_spec()` 从**导出数据**（limbs/meta/weights）加
`mmd2max.targets` 里的容差生成规格表，落 `MMD2MAX.VERDICTS`；Max 端只许往
`MMD2MAX.REPORT` 追加 `phase/check/subject/actual` 四个字段，**一个 PASS/FAIL 都不写**。
`tools/check_verdicts.py` 再把两张表拼起来算状态。这样「尺」和「量出来的数」来自
两个不同的进程，改坏任何一侧都会被另一侧发现。

三种 kind
---------
- `gate` 真正的要求，超差 = FAIL（例：失败段数必须 0、盒隙必须接上）。
- `lock` **已定价的缺陷**，钉住当前读数防止再退（例：`落位最大=5.6995`、`链向 断开=2 根`、
  Pelvis 落位偏差）。这类行不许被读成「达标」——它只保证「没变得更差」。
- `report` 只要求「这一行必须出现」，不卡数值（例：可测骨数，随模型变）。

`when=if_present` 的行（例：收尾重摆——没有任何骨被拽走时那一遍根本不该跑）缺失不算
失败，但如果报了，仍然按 expected/tol 判。

`always` 还是 `if_present`：这一列不是「我猜这轮会不会跑到」，而是「这一行没出现时，
我是不是愿意让它过去」。取向是**宁可让一具没量到的骨吵一句，也不要「整段静默跳过」
绿灯** —— v3 那 40 轮全部栽在后一种。只有三种情况用 `if_present`：① 开关明确关掉
（`--no-skin`、`FOOTREACH=false`、表情表为空）；② 那一步按设计可以合法不发生
（收敛循环提前收敛 → 第 2/3 遍没有；没有骨被拽走 → 收尾两道写没跑）；③ 目标节点
由模型决定、导出侧无法预先保证它存在（19 个落位校验点里的 Neck、Biped 指槽位数
不够时的指节）。

锁只在被定价的那具模型上生效
----------------------------
`lock` 的数字全部来自一具具体模型的一次实跑。5.6995 厘米是按香菱 158 厘米折出来的，
换一具 80 厘米的角色它必然超差 —— 那不是缺陷，是换了模型。所以每个锁都先问
`model == PRICED_MODEL`：不是那一具就退化成 `report`（仍然要求这一行出现，只是不卡
数字），等谁真跑了那具模型再把它定价。
"""
from __future__ import annotations

import math

from mmd2max import passes
from mmd2max.targets import DIR_TOL_DEG, GAP_TOL_CM, TOE_TOL_CM

# 两张表的名字（与 tools/obj_run.py 共用；改了这边不改那边 = 报告永远拼不上）
SPEC_NAME = "MMD2MAX.VERDICTS"
REPORT_NAME = "MMD2MAX.REPORT"

SPEC_HEADER = ("phase", "check", "subject", "expected", "tol", "kind", "when")
REPORT_HEADER = ("phase", "check", "subject", "actual")

KINDS = ("gate", "lock", "report")
WHENS = ("always", "if_present")

# 全模型范围的行为 "-"，Max 那边也写 "-"（两边同一个词，拼表才不需要映射）。
# 定义在 pass 注册表那边，因为归因/闸门规则也要用它去查报告行。
GLOBAL = passes.GLOBAL_SUBJECT

# 锁（lock）只对这一具模型生效；其余模型的相关行退化成 report。见模块文档。
PRICED_MODEL = "香菱"

# 收敛循环的遍数上限住在 `mmd2max/passes.py`（M3）：那边是注册表，它同时决定
# `MMD2MAX.CFG` 下发给 Max 的数字、以及校验器用来核对出口理由的上限。
# 以前这里是「`for passNo = 1 to 3` 的字面量 + 一份抄来的 3」，两边靠契约测试钉；
# 现在只有一份。
CONVERGE_MAX_PASSES = passes.max_iters("converge")

# ---------------------------------------------------------------------------
# 已定价的读数（lock）—— 来源：baselines/round20_objrun.log（第二十轮，香菱）
# 每一个都带「为什么是这个数」，没有来历的数字不许进这张表。
# ---------------------------------------------------------------------------

# 收敛三遍的读数。落位最大值 5.6995 是骨盆（PMX 腰 vs Biped COM 的固有差），
# 方向超标 4→5→5 是「靠上限退出」的证据（#53 要治的正是这个），本轮先钉住不许变差。
# 尝试/改善不单独锁：未改善=0 与 抛错=0 两条 gate 已经蕴含「改善数 == 尝试数」。
CONVERGE_LOCKS: dict[str, tuple[float, ...]] = {
    "dir_over": (4, 5, 5),
    "place_max_cm": (5.6995, 5.6995, 5.6995),
    "place_sum_cm": (146.513, 64.9124, 63.0287),
}
# 每个锁读数的允许漂移：落位是回读距离（噪声 ~1e-6），给 1e-4 足够；偏差和是 19 个点
# 的和，实测两轮差在 1e-4 量级，给 0.01。方向超标是**根数**，一根都不许动。
CONVERGE_TOL = {"dir_over": 0.0, "place_max_cm": 0.0001, "place_sum_cm": 0.01}

# 大腿扭转（#36）：左 1 遍收敛、右 2 遍未收敛就撞了迭代上限。
# 0.246829 厘米 ≈ 膝盖绕髋→踝轴还差 0.4 度 —— 定价为缺陷，不是目标。
THIGH_KNEE_LOCKS = {"Bip001 L Thigh": 0.158355, "Bip001 R Thigh": 0.246829}
THIGH_KNEE_TOL_CM = 0.05
# 踝是被「重摆」钉回 PMX 目标的，不是迭代逼近的，所以这条是真 gate 而不是锁。
THIGH_ANKLE_TOL_CM = 0.05

# 脚链（#40/#41）：压平水平轴的定义就是「骨轴与子关节夹角 29.9 度、断开 2 根」，
# 所以 链向 只能当锁；而「盒隙 没接上=0 根」是用户看得见的东西，是真判据。
CHAIN_BROKEN_LOCK = 2
CHAIN_ANGLE_LOCKS = {"Bip001 L Foot -> Bip001 L Toe0": 29.8725,
                     "Bip001 R Foot -> Bip001 R Toe0": 29.8929}
CHAIN_ANGLE_TOL_DEG = 0.5

# 结构向复核（退出 Figure 后的权威朝向读数）：>DIRTOL 的 6 根是已知集合
# （#27 的 Spine2/颈/手指 + 脚链定价项），本轮先钉住数量。
STRUCT_OVER_LOCK = 6

# 落位偏差：这些节点**必须**落在 PMX 目标上（回读实测 ~0），超 0.5cm 就是真缺陷。
PLACEMENT_GATE_NODES = (
    "Bip001 Head", "Bip001 Neck", "Bip001 Spine",
    "Bip001 L Clavicle", "Bip001 R Clavicle",
    "Bip001 L Foot", "Bip001 R Foot",
)
PLACEMENT_GATE_TOL_CM = 0.5

# 落位偏差：这些是**已定价的取舍**（用户看图后认可的代价），钉住不许变差。
#  - Pelvis 5.6995：骨盆 pivot 只跟 COM 走，闭环把髋中点对齐 PMX 腰之后 pivot 仍差这么多
#    （§7 第 12 条；这正是 place_max 那个数）。
#  - UpperArm/Forearm：#29 锁骨 0.955 取舍 —— 骨盒转不动手臂链，整条手臂按网格射线走，
#    PMX 目标位与它对不上是预期；Forearm 的 ~1.0 同源于此。
#  - Hand：跟着手臂链走，同为定价项。
#  - Thigh/Calf：0.1~0.25 量级来自 Biped 骨盒与 PMX 枢轴的姿态耦合。
PLACEMENT_LOCKS: dict[str, tuple[float, float]] = {
    "Bip001 Pelvis": (5.6995, 0.0001),
    "Bip001 Spine2": (0.0187461, 0.005),
    "Bip001 L UpperArm": (0.955353, 0.01),
    "Bip001 R UpperArm": (0.938523, 0.01),
    "Bip001 L Forearm": (1.01505, 0.01),
    "Bip001 R Forearm": (1.00992, 0.01),
    "Bip001 L Hand": (0.143507, 0.01),
    "Bip001 R Hand": (0.140112, 0.01),
    "Bip001 L Thigh": (0.105656, 0.01),
    "Bip001 R Thigh": (0.10566, 0.01),
    "Bip001 L Calf": (0.158318, 0.01),
    "Bip001 R Calf": (0.24703, 0.01),
}

# 落位目标按「父骨骨盒射线」派生的节点（#29 的 b 方案）。这些点离 PMX 远是定价项，
# 但离**射线交点**必须近 —— 那才是「肩点不再跟 Biped 抢」的证据。
# 名单与 .ms 的 mmd2maxRayFromParent 判据（pattern:"* UpperArm"）由契约测试钉在一起。
PLACEMENT_RAY_NODES = ("Bip001 L UpperArm", "Bip001 R UpperArm")
PLACEMENT_RAY_TOL_CM = 0.01

# 段校验的容差：0.05 厘米是第十二轮以来一直在用的口径（`段校验 … 误差 ≤0.05`），
# 比 Max 的 pivot 回读噪声（~4e-06）大三个数量级，比人眼能看出的错位（~0.3cm）小。
SEGMENT_TOL_CM = 0.05

# 骨盆子段的量法：Biped 的 Pelvis pivot 只跟 COM 走，而骨盆闭环把 COM 抬到**两髋中点**
# （不是 PMX 腰），所以 Pelvis→{L Thigh, R Thigh, Spine} 这三段能达到的长度是
# 「子骨目标位到髋中点的距离」，不是 limbs 的 PMX 腰→子骨长度（腿根 9.03 / 脊柱 6.49）。
# 拿后者去判 = 一条永远不可能通过的 gate，和恒等式绿灯一样坏。
# 门槛 0.05 与 .ms 的 `if want > 0.05` 同值（契约测试 test_segment_targets_use_the_hip_anchor 钉住）；
# 髋中点公式 = .ms 的 mmd2maxHipAnchor（L/R Thigh 的 child_x/y/z 中点）。
# 把这份量法写成 Python 代码而不是读 Max 的日志，是为了让「尺」始终只有一个来源：
# Max 报的是场景里量到的长度，期望值由这边算 —— 两边算错的方向互相独立。
PELVIS = "Bip001 Pelvis"
HIP_JOINTS = ("Bip001 L Thigh", "Bip001 R Thigh")
HIP_CHILDREN = HIP_JOINTS + ("Bip001 Spine",)
HIP_ANCHOR_MIN_CM = 0.05
POS_KEYS = ("child_x", "child_y", "child_z")


def bone_pos(limb: dict[str, str]) -> tuple[float, float, float]:
    """limbs 行里的子骨目标位（导出侧已经换算到 Max 场景坐标，单位厘米）。"""
    return tuple(float(limb[k]) for k in POS_KEYS)  # type: ignore[return-value]


def hip_anchor(limbs: list[dict[str, str]]) -> tuple[float, float, float] | None:
    """两髋中点；凑不齐左右腿根（或表里没有坐标列）时返回 None，骨盆子段沿用 PMX 长度。"""
    seen = {limb["child_target"]: bone_pos(limb) for limb in limbs
            if limb["child_target"] in HIP_JOINTS and all(k in limb for k in POS_KEYS)}
    if len(seen) != 2:
        return None
    left, right = seen[HIP_JOINTS[0]], seen[HIP_JOINTS[1]]
    return tuple((a + b) / 2.0 for a, b in zip(left, right))  # type: ignore[return-value]


def segment_expectation(limb: dict[str, str],
                        anchor: tuple[float, float, float] | None) -> str:
    """这一段该量到几厘米（文本，小数位与 limbs 的 desired_len 对齐）。"""
    if anchor is None or limb["parent_target"] != PELVIS or \
            limb["child_target"] not in HIP_CHILDREN or \
            not all(k in limb for k in POS_KEYS):
        return limb["desired_len"]
    want = math.dist(anchor, bone_pos(limb))
    if want <= HIP_ANCHOR_MIN_CM:
        return limb["desired_len"]
    return f"{want:.6f}"


# 盒隙的两个判据：沿轴距离（看得见的那段空气）与「子关节落在父盒端面矩形内」的布尔。
GAP_COVERED_TRUE = "true"


def row(phase: str, check: str, subject: str, expected, tol,
        kind: str, when: str = "always") -> tuple[str, str, str, str, str, str, str]:
    """规格行 → 七个**字符串**字段（wire format 只搬文本，判定时再 float()）。

    report 行的 expected/tol 是空串：它们没有「该等于几」，只要求这一行出现。
    """
    if kind not in KINDS:
        raise ValueError(f"未知的 kind：{kind}（只能是 {'、'.join(KINDS)}）")
    if when not in WHENS:
        raise ValueError(f"未知的 when：{when}（只能是 {'、'.join(WHENS)}）")
    if kind == "report":
        expected, tol = "", ""
    elif expected is None or tol is None:
        raise ValueError(f"{phase}/{check} 的 {kind} 行必须同时给出 expected 与 tol")
    return (phase, check, subject,
            "" if expected is None else str(expected),
            "" if tol is None else str(tol), kind, when)


def _lock(phase: str, check: str, subject: str, priced, tol,
          model: str, when: str = "always") -> tuple[str, ...]:
    """有定价读数就出 `lock`，否则出 `report`。

    「否则」有两种：这具模型没被定价过（换模型），以及这个主体不在定价表里（新增的
    肢段、改名的骨）。两种都不该拿香菱的数字去判红 —— 但那一行**必须出现**，
    否则「没量」和「量了没问题」在报告里长得一样。
    """
    if priced is None:
        return row(phase, check, subject, None, None, "report", when=when)
    return row(phase, check, subject, priced, tol, "lock", when=when)


def _priced(model: str, table: dict, key):
    """只有被定价的那具模型才拿到数字，其余模型返回 None（→ report）。"""
    return table.get(key) if model == PRICED_MODEL else None


def build_spec(*, model: str, vertex_count: int, limbs: list[dict[str, str]],
               kept_bones: int, morph_count: int,
               skin: bool = True, foot_reach: bool = True) -> list[tuple[str, ...]]:
    """装配一轮的全部判据规格。

    输入全部来自**导出结果**（weights 行数、limbs.tsv 的行、meta 的计数）与
    `mmd2max.targets` 的容差 —— 一个都不许从 Max 的日志里取，否则就成了「拿 Max
    自己报的数去要求 Max 报这个数」，那是恒等式，不是验收。

    哪些行出现也由导出结果决定：脚链、脚趾复摆、大腿扭转的主体都从 limbs 的 `mode`
    列推出来，所以换一具模型（指骨数不同、没有脚链）规格表自己就跟着变，不需要谁
    在实跑前手改这张表。
    """
    rows: list[tuple[str, ...]] = []
    foot_rows = [x for x in limbs if x.get("mode") == "foot"]
    leg_rows = [x for x in limbs if x.get("mode") == "leg"]

    def pair(limb: dict[str, str]) -> str:
        return f"{limb['parent_target']} -> {limb['child_target']}"

    # ── 网格 ──────────────────────────────────────────────────────
    # 期望顶点数来自 weights.tsv 的数据行；Max 报的是**网格对象**上的顶点数。
    # 两者不等 = 权重错位（#13 那个 bug 的守门人）。
    # 这一行在蒙皮函数里报（--no-skin 时整段不跑），所以开关关掉就退成 if_present。
    rows.append(row("mesh", "vertex_count", GLOBAL, vertex_count, 0, "gate",
                    when="always" if skin else "if_present"))

    # ── 肢段拟合 ──────────────────────────────────────────────────
    rows.append(row("fit", "segment_count", GLOBAL, len(limbs), 0, "gate"))
    rows.append(row("fit", "failed_segments", GLOBAL, 0, 0, "gate"))
    rows.append(row("fit", "cum_len_err_cm", GLOBAL, 0, 0.5, "gate"))

    # ── 摆位 ──────────────────────────────────────────────────────
    # placed_nodes 只要求出现：它是 passList 的长度，随模型的骨数变，没有「该等于几」。
    rows.append(row("pose", "placed_nodes", GLOBAL, None, None, "report"))
    rows.append(row("pose", "failed_nodes", GLOBAL, 0, 0, "gate"))
    rows.append(row("pose", "missing_nodes", GLOBAL, 0, 0, "gate"))
    rows.append(row("pose", "hip_residual_cm", GLOBAL, 0, GAP_TOL_CM, "gate"))

    # ── 收敛循环（每一遍一行，subject = 遍号）─────────────────────
    # 抛错与「未改善」是真要求（撞上限退出 = 没人判过），其余钉住本轮读数。
    # 第 1 遍必然发生；第 2、3 遍只在「上一遍还有骨要转」时才发生 → if_present。
    for pass_no in range(1, CONVERGE_MAX_PASSES + 1):
        when = "always" if pass_no == 1 else "if_present"
        subject = str(pass_no)
        rows.append(row("converge", "raised", subject, 0, 0, "gate", when=when))
        rows.append(row("converge", "not_improved", subject, 0, 0, "gate", when=when))
    for check in ("dir_over", "place_max_cm", "place_sum_cm"):
        values = CONVERGE_LOCKS[check]
        for pass_no in range(1, CONVERGE_MAX_PASSES + 1):
            when = "always" if pass_no == 1 else "if_present"
            value = _priced(model, {str(i + 1): v for i, v in enumerate(values)},
                            str(pass_no))
            rows.append(_lock("converge", check, str(pass_no), value,
                              CONVERGE_TOL[check], model, when=when))

    # ── 收敛闸门本身（M3）─────────────────────────────────────────
    # 这三行是「循环为什么停」的读数，全部由 Max 报，Python 只要求它们出现：
    # 数值门槛不该由这里再刻一遍（`dir_over` 那几条 lock 管的是达没达标），
    # 而三行之间的**自相一致**由 passes.audit_gate 判：报 ceiling 却只跑了 2 遍、
    # 报 gate 而 gate_satisfied=false —— 两种都是「循环结束了被当成收敛了」的谎。
    # 把它们定成 report 而不是 gate：换一具真收敛的模型时，出口=gate 不该被判红。
    rows.append(row("converge", "iters", GLOBAL, None, None, "report"))
    rows.append(row("converge", "exit_reason", GLOBAL, None, None, "report"))
    rows.append(row("converge", "gate_satisfied", GLOBAL, None, None, "report"))

    # ── 收尾重摆 / 收尾转角 ───────────────────────────────────────
    # 整段由 `lastAttempt > 0 or legRolled > 0` 门控：没有任何骨被转走或被拽走时它
    # 本来就不该跑，所以这几行是 if_present（跑没跑在报告里仍然看得见）。
    for check, kind in (("placed_nodes", "report"),
                        ("failed_nodes", "gate"),
                        ("missing_nodes", "gate")):
        expected = None if kind == "report" else 0
        rows.append(row("pose_end", check, GLOBAL, expected, expected, kind,
                        when="if_present"))
    for check in ("raised", "not_improved"):
        rows.append(row("rotate_end", check, GLOBAL, 0, 0, "gate",
                        when="if_present"))

    # ── 大腿扭转（#36）：主体 = limbs 里 mode=leg 的那些行 ────────
    for limb in leg_rows:
        subject = limb["parent_target"]
        value = _priced(model, THIGH_KNEE_LOCKS, subject)
        rows.append(_lock("thigh_roll", "knee_err_cm", subject, value,
                          THIGH_KNEE_TOL_CM, model))
        rows.append(row("thigh_roll", "ankle_err_cm", subject, 0,
                        THIGH_ANKLE_TOL_CM, "gate"))

    # ── 脚趾复摆（#38）+ 接趾后复摆：主体 = mode=foot 的行 ────────
    # 复摆残差的上限就是 CFG 里的 MMD2MAX_TOETOL：同一个数在两侧各写一遍的日子
    # 已经结束（M2a），规格表直接引用 targets。
    # residual_after_reach_cm 在 FOOTREACH=false 时整段不跑。
    for limb in foot_rows:
        subject = limb["child_target"]
        rows.append(row("toe_snap", "residual_after_roll_cm", subject, 0,
                        TOE_TOL_CM, "gate"))
        rows.append(row("toe_snap", "residual_after_reach_cm", subject, 0,
                        TOE_TOL_CM, "gate",
                        when="always" if foot_reach else "if_present"))

    # ── 脚链：偏航（#26）、骨轴连续（#40）、盒隙（#41）────────────
    # 偏航比的是 mmd2maxBoxWantDir 给的目标，而那个目标就是写进去的方向（同源，
    # 第十五轮它读 0.0 全绿而用户的截图里骨盒照旧是歪的）——所以真正的护栏是下面
    # 两条：**骨轴 vs 回读到的子关节**（链向）与 **两个盒子之间的空气**（盒隙）。
    for limb in foot_rows:
        subject = limb["parent_target"]
        rows.append(row("foot_yaw", "bone_yaw_deg", subject, 0, DIR_TOL_DEG, "gate"))
        rows.append(row("foot_yaw", "toe_yaw_deg", subject, 0, DIR_TOL_DEG, "gate"))
        pair_subject = pair(limb)
        value = _priced(model, CHAIN_ANGLE_LOCKS, pair_subject)
        rows.append(_lock("foot_chain", "chain_angle_deg", pair_subject, value,
                          CHAIN_ANGLE_TOL_DEG, model))
        rows.append(row("foot_chain", "gap_cm", pair_subject, 0, GAP_TOL_CM, "gate"))
        rows.append(row("foot_chain", "gap_covered", pair_subject,
                        GAP_COVERED_TRUE, 0, "gate"))
    rows.append(_lock("foot_chain", "broken_roots", GLOBAL,
                      _priced(model, {GLOBAL: CHAIN_BROKEN_LOCK}, GLOBAL),
                      0, model))
    rows.append(row("foot_chain", "gap_unconnected", GLOBAL, 0, 0, "gate"))

    # ── 段校验：limbs 每一对一行 ───────────────────────────────────
    # 期望值是**这一轮真能达到的长度**：骨盆三段按髋中点量（见 HIP_CHILDREN 的注释），
    # 其余段就是 limbs 的 desired_len。报的是 Max 从场景里**量到**的长度，所以 Max
    # 侧解析目标值错一位、或骨盆闭环没按同一个锚点走，都会在这里露出来。
    # 指节按 if_present：导出侧写了 5×3 而 Biped 的指槽建不出那么多是可能的。
    anchor = hip_anchor(limbs)
    for limb in limbs:
        when = "if_present" if "Finger" in limb["child_target"] else "always"
        rows.append(row("segment", "len_cm", pair(limb),
                        segment_expectation(limb, anchor),
                        SEGMENT_TOL_CM, "gate", when=when))

    # ── 落位偏差：每个测到的节点一行 ─────────────────────────────
    # 19 个点是 .ms 里的字面量清单（契约测试对撞），按模型可能缺 Neck 之类 → if_present。
    for bone in PLACEMENT_GATE_NODES:
        rows.append(row("placement", "pos_err_cm", bone, 0,
                        PLACEMENT_GATE_TOL_CM, "gate", when="if_present"))
    for bone in sorted(PLACEMENT_LOCKS):
        value = _priced(model, {k: v[0] for k, v in PLACEMENT_LOCKS.items()}, bone)
        tol = PLACEMENT_LOCKS[bone][1]
        rows.append(_lock("placement", "pos_err_cm", bone, value, tol, model,
                          when="if_present"))
    for bone in PLACEMENT_RAY_NODES:
        rows.append(row("placement", "ray_err_cm", bone, 0,
                        PLACEMENT_RAY_TOL_CM, "gate", when="if_present"))

    # ── 结构向复核 ────────────────────────────────────────────────
    rows.append(_lock("struct_dir", "over_tol", GLOBAL,
                      _priced(model, {GLOBAL: STRUCT_OVER_LOCK}, GLOBAL),
                      0, model))
    for check in ("measurable", "short_skipped"):
        rows.append(row("struct_dir", check, GLOBAL, None, None, "report"))

    # ── 附属骨父级 ────────────────────────────────────────────────
    rows.append(row("accessory", "no_parent", GLOBAL, 0, 0, "gate"))
    for check in ("on_biped", "on_bone"):
        rows.append(row("accessory", check, GLOBAL, None, None, "report"))

    # ── 蒙皮（--no-skin 时整段跳过）──────────────────────────────
    when = "always" if skin else "if_present"
    rows.append(row("skin", "bones", GLOBAL, kept_bones, 0, "gate", when=when))
    rows.append(row("skin", "failed_vertices", GLOBAL, 0, 0, "gate", when=when))
    rows.append(row("skin", "written_vertices", GLOBAL, vertex_count, 0, "gate",
                    when=when))

    # ── 表情通道（#24/#25：容量 ≠ 已用，必须逐条回读名字与目标）──
    # 表情表为空（CFG 允许 MMD2MAX_MORPHS 空）时整段跳过 → if_present。
    # 通道容量故意不设判据：WM3_NumberOfChannels 是「已分配」，报它等于给自己打分。
    for check in ("blocks", "built", "with_target", "with_name"):
        rows.append(row("morpher", check, GLOBAL, morph_count, 0, "gate",
                        when="if_present"))

    # ── pass 时间轴（M3）──────────────────────────────────────────
    # 每个 pass 一行 `pass/ran/<id>=1`：恰好一次。注册表还多干了三件事（顺序、作废、
    # 归因），都靠这些行当时间轴骨架，见 mmd2max/passes.py。
    # 开关明确打开的那些从 if_present 提回 always：`FOOTREACH=true` 时足盒接趾**必须**
    # 跑到，没跑是故障，不该被当成合法跳过。
    rows.extend(passes.spec_rows({
        "skin_apply": "always" if skin else "if_present",
        "foot_reach": "always" if foot_reach else "if_present",
        "toe_snap_reach": "always" if foot_reach else "if_present",
        "morpher_build": "always" if morph_count > 0 else "if_present",
    }))

    return rows


def spec_text(rows: list[tuple[str, ...]]) -> str:
    """规格表正文：线格式 v2（第 1 行是列名），无 BOM。"""
    for r in rows:
        if len(r) != len(SPEC_HEADER):
            raise ValueError(f"规格行字段数应为 {len(SPEC_HEADER)}：{r}")
        for field in r:
            if "\t" in field or "\n" in field:
                raise ValueError(f"规格行的字段里不许出现制表符或换行：{r}")
    keys = [(r[0], r[1], r[2]) for r in rows]
    dup = {k for k in keys if keys.count(k) > 1}
    if dup:
        # 同一个 (phase, check, subject) 出现两次 = 拼表时两条规则抢同一个读数，
        # 校验器只能认其中一条，另一条形同不存在。在这里就拦掉。
        raise ValueError(f"规格表有重复的 (phase, check, subject)：{sorted(dup)}")
    lines = ["\t".join(SPEC_HEADER)] + ["\t".join(r) for r in rows]
    return "\n".join(lines) + "\n"


def parse_table(text: str) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    """线格式 v2 读取（Python 侧）：第 1 行列名，`#` 行是人读注释，空行跳过。

    规格表与报告表都走这一个函数 —— 两张表必须是同一套解析，否则「Max 写出来的
    报告」和「Python 以为的报告」会在某个边界（BOM、CRLF、结尾无换行）上分叉，
    而分叉的表现是「报告读成 0 行」，看上去像这轮什么都没量。

    **行可以比列名短**（limbs.tsv 就靠这条：只有 `mode=foot` 的行才带第 10~18 列），
    短的尾部按「这个键没有」处理，取值一律用 `row.get(key)`；不许补空串 —— 补出来的
    空串会被下游当成「真有这个值」。反过来**比列名长**必须报错：那是多写了列，
    静默截断就是拿错位的数据当判据。
    """
    body = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln for ln in body.split("\n") if ln.strip() and not ln.startswith("#")]
    if not lines:
        raise ValueError("表是空的（连列名行都没有）")
    header = tuple(lines[0].split("\t"))
    rows: list[dict[str, str]] = []
    for number, line in enumerate(lines[1:], start=2):
        cells = line.split("\t")
        if len(cells) > len(header):
            raise ValueError(f"第 {number} 行有 {len(cells)} 列，比列名行的 "
                             f"{len(header)} 列还多：{line}")
        rows.append(dict(zip(header, cells)))
    return header, rows


# Max 侧必须出现的通道：(phase, check)。契约测试拿它和 .ms 里的
# `mmd2maxReport "…" "…"` 字面量对撞 —— 少一个就是「这条判据永远不会有人填」，
# 多一个就是「报告里出现规格表不认识的行」。两侧都由测试逼着同步。
# fixture 必须覆盖三种 mode（foot / leg / 其余）与指节，否则通道集合会缺类。
CHANNELS: frozenset[tuple[str, str]] = frozenset(
    (r[0], r[1]) for r in build_spec(
        model=PRICED_MODEL, vertex_count=1,
        limbs=[{"parent_target": "Bip001 L Foot", "child_target": "Bip001 L Toe0",
                "desired_len": "11.228109", "mode": "foot"},
               {"parent_target": "Bip001 L Thigh", "child_target": "Bip001 L Calf",
                "desired_len": "36.382676", "mode": "leg"},
               {"parent_target": "Bip001 L Finger0", "child_target": "Bip001 L Finger01",
                "desired_len": "2.344292", "mode": "link"},
               {"parent_target": "Bip001 Spine", "child_target": "Bip001 Spine1",
                "desired_len": "8.325677", "mode": "link"}],
        kept_bones=1, morph_count=1, skin=True, foot_reach=True)
)

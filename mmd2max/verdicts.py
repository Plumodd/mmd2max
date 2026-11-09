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

四种 kind
---------
- `gate` 真正的要求，超差 = FAIL（例：失败段数必须 0、盒隙必须接上）。
- `lock` **已定价的缺陷**，钉住当前读数防止再退（例：`落位最大=5.6995`、`链向 断开=2 根`、
  Pelvis 落位偏差）。这类行不许被读成「达标」——它只保证「没变得更差」。
- `report` 只要求「这一行必须出现」，不卡数值（例：可测骨数，随模型变）。
- `min` 单向门槛：读数**至少**要到 expected 才算过（例：特写截图的取景填充率、
  取景带的上沿必须高过胸骨）。为什么现有三种装不下它：gate 比的是
  `abs(实报 - 期望) <= tol`，是**双向**的，把填充率写成 gate + 大容差就等于没写
  （越大越能漂），写成 gate + 小容差又要求它不许超过某个数 —— 而「拍得更近」从来不是
  缺陷。expected 之下才是缺陷。tol 对 `min` 行没有意义，固定写 0。
- `max` `min` 的镜像：读数**最多**只能到 expected（例：取景带的下沿必须低过胸骨）。
  同样是单向 —— 带框得比要求的更宽不是缺陷，只是那张图更空。tol 固定写 0。

`when=if_present` 的行（例：收尾重摆——没有任何骨被拽走时那一遍根本不该跑）缺失不算
失败，但如果报了，仍然按 expected/tol 判。

`always` 还是 `if_present`：这一列不是「我猜这轮会不会跑到」，而是「这一行没出现时，
我是不是愿意让它过去」。取向是**宁可让一具没量到的骨吵一句，也不要「整段静默跳过」
绿灯** —— v3 那 40 轮全部栽在后一种。只有三种情况用 `if_present`：① 开关明确关掉
（`--no-skin`、`FOOTREACH=false`、表情表为空）；② 那一步按设计可以合法不发生
（收敛循环提前收敛 → 第 2/3 遍没有；没有骨被拽走 → 收尾两道写没跑）；③ 目标节点
由模型决定、导出侧无法预先保证它存在（19 个落位校验点里的 Neck、Biped 指槽位数
不够时的指节）。

锁按「哪一具模型 + 哪一组导出配置」生效
--------------------------------------
`lock` 的数字全部来自一具具体模型的一次实跑。5.6995 厘米是按香菱 158 厘米折出来的，
换一具 80 厘米的角色它必然超差 —— 那不是缺陷，是换了模型。所以每个锁都先问这一轮
是不是被定价过的那一具：不是就退化成 `report`（仍然要求这一行出现，只是不卡数字），
等谁真跑了那具模型再把它定价。

「哪一具」不认文件名，认**导出的事实**（`PRICED_RUNS` 的 `verts`/`morphs`）。这一条是
#106 修的东西：`run_model.py` 用 `outdir = test_runs/<pmx 名>`，所以第 76 轮为了让交付件
不覆盖金标准夹具而把 PMX 改名 ⇒ `model` 变了 ⇒ 整片锁退成 `report` ⇒ 那一轮 512 全绿，
而它其实是「验收层没参与」。改名是流程逼出来的，判据强度却跟着文件名走，这才是缺陷。

第二维是**导出配置**（`export_config()`：身高、权重阈值、骨数、辅助骨开关、形变口径）。
同一个 `香菱.pmx` 换一组开关就是另一组读数 —— `--drop-guides` 少导出 50 根骨，收敛循环要转的
骨集合不同，实测 `方向超标` 从 4/5/5 变成 5/6/6、结构向超标从 6 根变成 7 根。所以：

- 模型没被定价过 → `report`（设计，换模型不许拿香菱的数判它）；
- 模型被定价过、这一组配置没有 → **中止**（`pricing_of` 抛）。这一支以前不存在：
  配置对判据层完全隐形，于是「换配置」要么被判红（错的是尺），要么靠改名判绿（错的
  是没人看）。两边都不许，先把这一组配置实跑一遍、逐个查过读数来历再进表。

第二具模型上「按设计够不着」的读数走另一条路：`SEGMENT_LOCKS` 按模型名分键，命中的
那一行从 `gate` 换成 `lock`（尺从输入表的目标值换成已定价的读数），没命中的模型**照旧
走 gate** —— 段长的尺本来就每具模型都能从 limbs.tsv 数出来，退化成 report 等于放弃
验收，而它并不需要放弃：要钉住的只有那一段。这条路仍按名字认，因为它一次只改**一行**
的尺（漏了只少一段），而不是整片锁；改名风险的其余部分由上面那条计数认模型堵住。
"""
from __future__ import annotations

import math
from typing import NamedTuple

from mmd2max import passes, semantic
from mmd2max.targets import (
    DIR_TOL_DEG,
    GAP_TOL_CM,
    SEGMENT_TOL_CM,
    TOE_TOL_CM,
    GROUND_TOL_CM,
    PENETRATION_TOL_CM,
    ROLLBACK_TOL_CM,
    ROLLBACK_TOL_DEG,
    STUB_CM,
    WEIGHT_ERR_TOL,
    WEIGHT_SUM_TOL,
    ORIENT_TOL,
    box_tol_cm,
    span_tol_cm,
)

# 两张表的名字（与 tools/obj_run.py 共用；改了这边不改那边 = 报告永远拼不上）
SPEC_NAME = "MMD2MAX.VERDICTS"
REPORT_NAME = "MMD2MAX.REPORT"

SPEC_HEADER = ("phase", "check", "subject", "expected", "tol", "kind", "when")
REPORT_HEADER = ("phase", "check", "subject", "actual")

KINDS = ("gate", "lock", "report", "min", "max")
WHENS = ("always", "if_present")

# 声明成 `lock` 的 check 名 —— 钉住「已定价的读数」，PASS 只等于「没变得更差」。
# 这张表是**给工具读的**，不是文档：`tools/check_ms.py` 的规则 I 必须认出「这一行是锁，
# 不是达标」，才不会把定价过的缺陷也判成假验收（判多了的下一步一定是有人来放宽规则，
# 那才是这条规则死掉的方式）。扫源码的文本办法在这里必然失灵，因为
# `_lock("converge", check, …)` 里的 check 是循环变量 —— 文本里根本没有 `dir_over`
# 这个字面量，而真规格表里它确确实实是 lock。
# 口径由 test_verdicts.LockCheckNameListTests 钉死：这张表必须与真导出规格表里
# kind=lock 的那些 check 名**完全相等**，多一个少一个都红。
LOCK_CHECKS = ("broken_roots", "chain_angle_deg", "dir_over", "knee_err_cm",
               "over_tol", "place_max_cm", "place_sum_cm", "pos_err_cm")

# 全模型范围的行为 "-"，Max 那边也写 "-"（两边同一个词，拼表才不需要映射）。
# 定义在 pass 注册表那边，因为归因/闸门规则也要用它去查报告行。
GLOBAL = passes.GLOBAL_SUBJECT

# 模型名现在只剩两处用处：`SEGMENT_LOCKS` 的分键、以及 `PricedRun.model`（只进日志与报错）。
# 「这一轮是不是被定价过的那一具」不再由它决定 —— 见模块文档与 `PRICED_RUNS`。
PRICED_MODEL = "香菱"

# 收敛循环的遍数上限住在 `mmd2max/passes.py`（M3）：那边是注册表，它同时决定
# `MMD2MAX.CFG` 下发给 Max 的数字、以及校验器用来核对出口理由的上限。
# 以前这里是「`for passNo = 1 to 3` 的字面量 + 一份抄来的 3」，两边靠契约测试钉；
# 现在只有一份。
CONVERGE_MAX_PASSES = passes.max_iters("converge")

# ---------------------------------------------------------------------------
# 已定价的读数（lock）—— 每一条都带「哪一轮、哪一组导出配置、数出自哪份冻结件」
# 没有来历的数字不许进这张表。
# ---------------------------------------------------------------------------

# 落位最大值 5.6995 是骨盆（PMX 腰 vs Biped COM 的固有差）；偏差和是 19 个校验点的
# 和。这两组在香菱的两组导出配置下**读数逐字相同**（2026-11-05 用同一个 exe 各跑一遍：
# 5.6995 三遍一样、146.513/64.9124/63.0287 一个末位都没动），所以它们不随配置走，
# 留在这一份共享表里；随配置走的那个数（`dir_over`）住在 `PricedRun` 里。
CONVERGE_LOCKS: dict[str, tuple[float, ...]] = {
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

# 脚链（#40/#41）：旧版压平脚盒几何时把 Foot 盒压得太窄、接不上 Toe0，
# 链向定义成「断开 2 根」，broken_roots 锁在 2。第 81 轮用户把脚盒几何改成
# 「包围建模」(foot_w 取脚掌半宽，和 Toe0 同款)，接趾之后盒隙接上、链向连续，
# broken_roots 实测 0 —— 这是修好的**目标态**而非缺陷。锁改成 0：链一旦再被
# 压断（任何 >0 根）立刻 FAIL，回归护栏一条不少；但已修好的连接态不再被误判。
CHAIN_BROKEN_LOCK = 0
CHAIN_ANGLE_LOCKS = {"Bip001 L Foot -> Bip001 L Toe0": 29.8725,
                     "Bip001 R Foot -> Bip001 R Toe0": 29.8929}
CHAIN_ANGLE_TOL_DEG = 0.5

# 结构向复核（退出 Figure 后的权威朝向读数）：>DIRTOL 的那几根是已知集合
# （#27 的 Spine2/颈/手指 + 脚链定价项），钉的是数量不许变差。数量随导出配置走，
# 所以它住在 `PricedRun.struct_over` 里而不是一个常数。

# ── 定价的身份：模型由导出计数认，配置由导出事实拼 ──────────────────
#
# 这六个值全部取自导出落下来的**文本**，不在这里重新 float()：`float("0.000088")`
# 打印成 `8.8e-05`，定价条目与 meta 就会写成两种字形而对不上（`_ruler` 那条教训）。
# 前五维取 CLI/GUI 上真能按的开关（`--height` → scale、`--min-weight`、`--drop-guides`），
# 加上 `bones`：骨数是这些开关与骨架模式共同的结果，而「收敛要转几根骨」正是它决定的。
# 第六维 `dq` 取的是**源文件声明的形变口径**（meta 的 SDEF/QDEF 两键组合成
# lin / sdef / qdef / sdef+qdef）。它不是开关，但和开关一样决定「这轮的读数该按哪组数卡」：
# 双四元数分支在 M1b 删掉之后 Max 端只会按线性混合装配，于是「源含 DQ + 装配线性」是一个
# **从没实跑过定价**的组合 —— 让它进指纹，这一组就命中「配置没定价 ⇒ 中止」，而不是拿
# 纯线性那两组的数冒充它的尺。
# 放配置这一边而不是「认模型」那一边，是因为要防的事只有一件：一具与香菱同网格同表情
# 但含 SDEF 的模型，不该悄悄继承线性混合那两组的锁（认模型那一维会把它判成「没定价的
# 模型 ⇒ 整片退 report」，太安静；配置这一维判成「这一组配置没定价 ⇒ 中止」，够响）。
# 反过来，认模型用 `vertex_count` / `morph_count`：它们由网格与表情表决定，改文件名、
# 换身高、丢辅助骨都不动它们 —— 所以「把 PMX 改个名」不会让验收层认不出这具模型。
def export_config(*, scale: str, min_weight: str, bones: str, guides: str,
                  sdef: str, qdef: str) -> str:
    """导出配置串：定价表的分键，也是中止信息里那句「这一轮到底是哪一组开关」。

    `guides` 收的是 meta 的字面文本（true/false），不是 Python bool —— 装配侧读的是
    文件，中间不许有翻译（`foot_box_flat` 同一条规矩）。认不出的写法一律抛：那说明
    meta 与代码分叉了，此时任何定价判断都建立在猜上。
    `sdef` / `qdef` 同理收 meta 的 "0"/"1" 原文（#119）：形变口径决定 Skin 怎么算，
    双四元数分支在 M1b 删掉之后，「源文件有没有 SDEF/QDEF」只剩 meta 里这两行，
    而它不进指纹就等于「DQ 模型继承线性混合那两组的锁」—— 同一具网格换一种形变，
    落位与朝向读数本来就该重量一遍。
    """
    if guides not in ("true", "false"):
        raise ValueError(
            f"meta 的 keep_guides 必须是 true/false，读到 {guides!r}：重跑 "
            "tools/run_model.py 生成新的 meta（这一轮的辅助骨开关读不出来，"
            "而收敛读数恰好跟着它变）")
    for key, value in (("has_sdef", sdef), ("has_qdef", qdef)):
        if value not in ("0", "1"):
            raise ValueError(
                f"meta 的 {key} 必须是 0/1，读到 {value!r}：重跑 tools/run_model.py "
                "生成新的 meta（形变口径读不出来，而 Max 端只会按线性混合装配）")
    # 引号不在 f-string 里套引号：那条写法要 Python 3.12+，而本项目的下限是 3.8
    keep = "keep" if guides == "true" else "drop"
    dq = {(0, 0): "lin", (1, 0): "sdef", (0, 1): "qdef",
          (1, 1): "sdef+qdef"}[(int(sdef), int(qdef))]
    return f"scale={scale} min_weight={min_weight} bones={bones} guides={keep} dq={dq}"


class PricedRun(NamedTuple):
    """一具被定价过的模型在某一组导出配置下的读数，加它的来历。"""

    model: str                      # 只进日志与报错：定价认的是下面的计数，不是名字
    verts: str                      # 认模型：meta 的 vertex_count 原文（装配核对它 == 权重表行数，#121）
    morphs: str                     # 认模型：meta 的 morph_count 原文
    config: str                     # 认配置：export_config() 的产物
    dir_over: tuple[int, ...]       # 三遍收敛各自还有几根骨转不过去
    struct_over: int                # 结构向 >DIRTOL 的根数
    log: str                        # 这两个数量出自 baselines/ 里哪一份冻结件


PRICED_RUNS: tuple[PricedRun, ...] = (
    # 第 20 轮起那一组（全骨 321 根）：`dir_over` 4→5→5 是「靠上限退出」的证据
    # （#53 要治的正是这个），钉住不许变差。尝试/改善不单独锁：未改善=0 与 抛错=0
    # 两条 gate 已经蕴含「改善数 == 尝试数」。
    # `struct_over` 的 6 根 = #27 的 Spine2 / 颈 / 手指 + 脚链那两条定价项。
    PricedRun(model=PRICED_MODEL, verts="18892", morphs="61",
              config=export_config(scale="7.6563", min_weight="0.0",
                                   bones="321", guides="true", sdef="0", qdef="0"),
              dir_over=(4, 5, 5), struct_over=6, log="round20_objrun.log"),
    # 第 77 轮 = 第 76 轮那个交付件形状（--drop-guides，271 根骨）在 2026-11-05 重跑一遍。
    # 两轮独立读数相同（`dir_over 5/6/6`、`struct_dir/over_tol 7`，见下面那份冻结件与
    # test_runs/香菱_无附属骨/MMD2MAX.REPORT），所以这不是抄当轮，是量到过的两组数。
    # 第 110/114 轮（2026-11-07，同一份 PMX 只差 keep_guides）把「多的那 1 根」点名了：
    # 是 **R Hand 从退化读数变成可测**，不是泛指「少一批骨就少一批转到位的机会」。
    #   guides=true ：R Hand 挑中**轴3**、目标倾=0.0、实测倾=-90.541、夹角=90.5409，
    #                 被判**短骨**跳过 ⇒ measurable=51 / short_skipped=1 / over_tol=6
    #   guides=false：R Hand 挑中**轴1**、目标倾=-137.284、实测倾=178.77、夹角=43.7781
    #                 ⇒ measurable=52 / short_skipped=0 / over_tol=7
    # L Hand 两轮**逐位相同**（轴1、夹角=43.7967、pivot偏差=0.143507），所以变的只有
    # R Hand 的长度轴挑选这一处。它那 43.78 度就是 #33 定价的「Hand 不转」，只不过
    # 现在左右对称地各报一根，而不是一根报 43.8、另一根躲在短骨豁免里。
    # ⇒ 拿 over_tol 跨这两个配置比大小会造出假回归；它按配置指纹各锁各的数。
    PricedRun(model=PRICED_MODEL, verts="18892", morphs="61",
              config=export_config(scale="7.6563", min_weight="0.0",
                                   bones="271", guides="false", sdef="0", qdef="0"),
              dir_over=(5, 6, 6), struct_over=7,
              log="round77_香菱_noguide_objrun.log"),
)


def pricing_of(*, vertex_count: int, morph_count: int, config: str) -> PricedRun | None:
    """这一轮是不是「被定价过的那一具模型 + 那一组配置」。

    - 计数对不上任何条目 → None：没被定价过的模型，那些行退化成 `report`（模块文档）。
    - 计数对得上、配置对不上 → 抛。这一支就是 #106：以前配置不存在，所以
      `--drop-guides` 那轮要么拿全骨的数判红、要么（改了名）整片不判。
    """
    same_model = [r for r in PRICED_RUNS
                  if r.verts == str(vertex_count) and r.morphs == str(morph_count)]
    if not same_model:
        return None
    for run in same_model:
        if run.config == config:
            return run
    raise ValueError(
        f"{same_model[0].model}（顶点 {vertex_count} / 表情 {morph_count}）被定价过，"
        f"但这一组导出配置没有定价：{config}。已定价的配置是 "
        f"{'、'.join(r.config for r in same_model)}。"
        "换配置 = 换读数（骨数决定收敛要转多少根、身高决定所有厘米数），"
        "拿别的配置的数判它是假绿或假红 —— 先按这一组配置实跑一遍，把每个读数逐个"
        "查过来历，再往 PRICED_RUNS 里加一条")


def pricing_note(*, vertex_count: int, morph_count: int, config: str,
                 rows: list[tuple[str, ...]]) -> str:
    """给日志的一行：这一轮的锁是拿谁的数在卡。

    必须**每轮**打印，而不是只在抛错时说话：#106 那轮的形状不是「判错了」，是「整片
    不判，而日志里看不见」。没被定价过的模型退化成 report 是设计，但设计也得在账面上
    留一行 —— 否则下一轮没人发现自己其实没量（读数写着 512 全绿，验收层根本没参与）。
    """
    locks = sum(1 for r in rows if r[5] == "lock")
    run = pricing_of(vertex_count=vertex_count, morph_count=morph_count, config=config)
    if run is None:
        return (f"定价：无（顶点 {vertex_count} / 表情 {morph_count} 不在定价表里 ⇒ "
                f"已定价的读数只报告、不卡数，本表 {locks} 条 lock）")
    return (f"定价：{run.model}｜{run.config}｜读数出自 baselines/{run.log}"
            f"（dir_over {'/'.join(str(v) for v in run.dir_over)}、"
            f"struct_over {run.struct_over}）⇒ 本表 {locks} 条 lock")

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

# 段校验的容差 SEGMENT_TOL_CM 在 mmd2max/targets.py（0.05 的定价与来历写在那边）。
# 这边只引用，不再抄一遍：`TOE_TOL_CM` 的门控值就是从这里等过去的（#127）。

# 肢段长度：换到第二具模型上**实测到够不着**的那一段，按模型定价钉住（#76-B）。
# 键 = 模型名，值 = {段主体: (定价读数, 容差)}。没被定价的模型/段仍走 gate，
# 因为拿香菱的数字去判别的模型 = 一条永远过不去的闸。
#
# 香菱-海灯节 `Bip001 Spine1 -> Bip001 Neck` = 17.9546（PMX 是 17.898357，差 0.0562，
# 比 gate 的 0.05 多出 0.0062 —— 就是这一条把那一轮判成唯一的 1 个 FAIL）。
# 来历：读数在 baselines/round57_haideng_zerolinks.log（`实报=17.9546`）和
# baselines/round59_香菱-海灯节_objrun.log（`实际=17.9546`）里各出现一次 —— 两轮的
# DIR_TOL 不同、代码不同，读数一样，所以这个数不是某一轮的偶发。链条每一环都是读数：
#   ① round59:254 `结构向 Bip001 Spine 轴1 … 差倾=-0.899286 … 夹角=0.898964` —— 0.899 度低于
#      DIR_TOL(3.0)，转角 pass 按设计不去试它（所以日志里连「转角不做」那行都没有）。
#   ② 第 58 轮把 DIR_TOL 压到 0.2 去试（baselines/round58_香菱-海灯节_objrun.log）：
#      `转角1:Bip001 Spine 夹角=0.899181->0.899181`（抛错=0，但读数纹丝不动），另外多出
#      13 条「转了没改善」的收敛 FAIL —— 够不到目标不是门控太松，是 Biped 不吃这根骨的转角。
#   ③ round59:255 `结构向 Bip001 Spine1 … pivot偏差=0.176007` —— 0.899 度 × 12.079cm 的弧。
#   ④ Neck 自己落位是准的（round59:236 `校验 Bip001 Neck … 偏差=7.6296e-06`），所以
#      Spine1→Neck 的世界长度就是 PMX 长度加上那 0.176 的弧在这段方向上的投影 = 17.9546
#      （+0.0562）。同一条弧的另一半落在上一段：round59:232 `Spine -> Spine1 实际=12.0747`
#      （短 0.0043），两个符号、一个来历。
# 两头只能取其一：要让这段长度等于 PMX，就得把 Spine1 的 pivot 从「沿父骨轴 12.079」挪到
# PMX 目标（即离开父骨轴 0.176）—— 那会连带弄坏 Spine1 自己的结构向（现在 夹角=0.0）和
# Biped 沿脊柱的弯曲。定价的是这条世界长度：容差 0.001 比原来的 gate(0.05) 严 50 倍，
# 钉的是「别漂」（同一段在 8 个轮次里都读 17.9546，漂过 1 个末位就该有人来看），
# 不是「达标」。
SEGMENT_LOCKS: dict[str, dict[str, tuple[float, float]]] = {
    "香菱-海灯节": {"Bip001 Spine1 -> Bip001 Neck": (17.9546, 0.001)},
    # 克雷薇同一段的对应物，来历与上面那条**是同一个机制**（不是同一堆数字）：
    #   `段校验 Bip001 Spine1 -> Bip001 Neck 目标=20.3506 实际=20.4092 误差=0.0585976`
    #   `结构向 Bip001 Spine1 … 夹角=0.0 pivot偏差=0.159074` —— pivot 停在父骨轴上，
    #   `校验 Bip001 Neck … 偏差=0.0` —— Neck 自己落位是准的，
    #   `段校验 Bip001 Spine -> Bip001 Spine1 目标=15.2164 实际=15.213` —— 同一根弧的
    #     另一半落在上一段（短 0.0034），两个符号、一个来历。
    # 所以这段的世界长度 = PMX 长度 + pivot 偏移在这段方向上的投影，按构造够不着 20.3506。
    # 来历：上面四行读数在 baselines/round79_克雷薇_objrun.log（185/186/189/208 行，
    # 2026-11-05 13:19）里各出现一次，`实报=20.4092` 在 baselines/round75_克雷薇_driver.log
    # （34 行，2026-11-04 21:47，那一轮这段还是 gate 所以才会被打出来）里出现一次。
    # 两轮之间隔着并回 portable 那批改动 —— 79 轮的 .ms 与规格装配都是并回后的，配置项
    # 也从 42 项变成 43 项（新增 `短骨门限=1.0`），四行读数一字没变 ⇒ 这个数不是某一轮的
    # 偶发，也和那批改动无关。
    # 容差照 海灯节 的 0.001 定（比原 gate 的 0.05 严 50 倍），钉「别漂」，不是「达标」。
    "克雷薇": {"Bip001 Spine1 -> Bip001 Neck": (20.4092, 0.001)},
}

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


# ---------------------------------------------------------------------------
# 蒙皮回读的期望值（M4b）：从 weights.tsv + bones.tsv 独立算一遍
# ---------------------------------------------------------------------------
#
# 为什么这份计算必须在 Python 再写一遍，而不是拿 Max 报的 `links_expected` 当尺：
# 23 轮以来的 `skin/written_vertices` 数的是「ReplaceVertexWeights 返回没抛」，而探针
# 实测「返回 OK 而权重是 0.0」（M4e）和「没写过也读得出某根骨 @ 1.0」（M4f A1）都真实
# 存在 —— 写入侧的自述证明不了场景。现在 Max 从**场景**读回一份数，Python 从**输入文件**
# 算出一份期望，两边来自不同进程，这才是验收。
#
# 三条规则必须与 .ms 逐字同语义，否则第一轮就会假 FAIL：
#  1. 参与蒙皮的骨 = `skin_flag != "0"` 的行，Skin 里的骨号按表里的行序从 1 开始累加
#     （mmd2maxApplySkin 的 globalToSkin）。
#  2. 链路按**骨号出现次数**计，不按权重大小计（M4f C2：≲1e-8 的权重会被存成 0.0，
#     骨号还在；按权重数就会少数链路）。
#  3. 但权重**正好 0.0** 的对不计入期望：那条链路 Max 根本不持有（海灯节实测 332 条
#     PMX 权重 <5e-7 的对，印成 `0.000000` 交给 Skin，回读连骨号都没有，于是
#     `links_missing` / `link_diff_max` / `link_mismatch_bones` / `links_read` 四行为
#     一批「对形变没有任何影响」的数变红）。规则 2 管的是「 tiny 但非零的权重还在骨号里」，
#     规则 3 管的是「零权重的骨号压根不在场景里」—— 两条不冲突，量的是不同的东西。
#     导出侧（export_obj._worker_weights）现在也不会再发这种对；`skin_rb/zero_links`
#     就是专门盯着「权重表里又冒出这种对」的那把尺。
LINK_SUBJECT_SUFFIXES = (" Toe0", " Foot")


class SkinExpectation(NamedTuple):
    """一次蒙皮的期望值，全部从输入文件算出，一个都不来自 Max。"""

    links_expected: int
    zero_links: int
    verts_no_exp: int
    bones_skinned: int
    # 看得见的那几根（脚尖/足首）：(Max 里的节点名, 期望链路数)，按骨号顺序。
    links_by_bone: tuple[tuple[str, int], ...]


class PhysicsExpectation(NamedTuple):
    """物理段「该建成什么样」的五把尺，全部从**导出的 physics.tsv** 数出来。

    这几把尺与 meta 的 `rigid_count` / `joint_count` 是两份不同的东西：后者是 PMX 侧的
    声明（`len(model.rigid_bodies)`），前者是真正落盘、Max 要逐行读的那张表的行数。
    装配时两边对撞（分叉 ⇒ 中止，#122），对上了才把表里这份交给 Max 当期望值 ——
    Max 报的是**它在场景里建出来的代理个数**，创建会抛错、会退化成圆柱、会进不了层，
    所以那不是一个恒等式。第 64/64b 轮那句 `物理刚体代理：253（失败 1）` 就是这条闸
    该响而当时没有闸：那根「失败」的代理其实建好并挂上了父骨，抛的是记账那一步
    （PMX 刚体索引 0 基、Max 数组 1 基），所以 `rigid_created` 会比场景里的真数少一根、
    而 `rigid_failed` 会多说一根 —— 两个数都得看，卡一个不够。

    `oriented_rows` 数的是 `rigid_rot_x/y/z` 里至少一根非零的 RIGID 行，口径与 .ms 的
    `if (rx != 0.0) or (ry != 0.0) or (rz != 0.0)` 逐字相同（同一批列、同一个非零判据）。
    它是「`旋转失败=0` 这句是真话还是空话」的那一格：没有它，254 根全没朝向与 254 根
    全转上了读起来一模一样（.ms 汇总行那段注释记的就是这件事）。

    `parented_rows` 数的是 `rigid_bone >= 0` 的 RIGID 行（#125）= 这些代理**该**挂在一根骨
    下面。PMX 允许一个刚体不绑骨（bone = -1，MMD 里那种自己挂在根上的挂件），那几行本来就
    不该挂，所以期望不是「刚体行数」而是「绑了骨的行数」。对上的语义是 .ms 那句回读
    `proxy.parent != undefined` —— 挂父那三层条件落空时创建计数完全不动，这一格是唯一的出口。

    `named_rows`（#126）数的是 `rigid_bone_node` 非空的 RIGID 行 = 这些行**认得出该挂哪根骨
    的名字**。它永远 ≤ `parented_rows`：差出来的那几行是「绑了骨，但这个名字当不了尺」——
    重名骨（Max 会给第二个副本加后缀，谁拿裸名没量过）。`rigid_parented` 只说「挂上骨了」，
    挂错一根照样绿；这一格比的是回读到的 `proxy.parent.name` 与表里那个名字，
    是「表里的骨号 → Max 的 boneNodes 下标 → 场景里那个节点」这条链上唯一两端不同源的量。
    """

    rigid_rows: int
    joint_rows: int
    oriented_rows: int
    parented_rows: int
    named_rows: int


def physics_expectation(rows: list[dict[str, str]], *, src: str) -> PhysicsExpectation:
    """physics.tsv 解析出来的行 → 物理段那几把尺（#122 三把、#125 第四把、#126 第五把）。

    分类只认**列名**：RIGID 行走第 1 行的列名（有 `rigid_index`），JOINT 行走 `@JOINT`
    那行的列名（有 `joint_index`）。之所以不许「按第一列 row_kind 分类再看列名」：
    如果 `@JOINT` 那行没被读到（旧产物、或被谁手改坏了），JOINT 行会被主表头错解成
    `row_kind=JOINT`、`rigid_index=0` —— 数出来是一份**看着正常**的刚体行数，而真正的
    关节数读成 0。所以这里把每一种读不出的形状都抛出去，而不是把偏小的尺交给下一轮。
    """
    rigid = [r for r in rows if "rigid_index" in r]
    joint = [r for r in rows if "joint_index" in r]
    if both := [r for r in rows if "rigid_index" in r and "joint_index" in r]:
        raise ValueError(f"{src}：{len(both)} 行同时有 rigid_index 与 joint_index，"
                         "两种记录的列名撞了（`@JOINT` 那行的列名前缀是 joint_，不该撞）")
    if weird := [r for r in rigid if r.get("row_kind") not in (None, "RIGID")]:
        raise ValueError(f"{src}：{len(weird)} 行按主表头读出来 row_kind 不是 RIGID "
                         f"（第一行是 {weird[0].get('row_kind')!r}）—— `@JOINT` 那行列名行"
                         "没读到，关节行被当成刚体行了，重跑 tools/run_model.py")
    if unknown := [r for r in rows if "rigid_index" not in r and "joint_index" not in r]:
        raise ValueError(f"{src}：{len(unknown)} 行既没有 rigid_index 也没有 joint_index："
                         f"第一行是 {unknown[0].get('row_kind', unknown[0])!r}，"
                         "这张表不是导出层写的那种记录，重跑 tools/run_model.py")
    oriented = 0
    for r in rigid:
        try:
            axes = [float(r[f"rigid_rot_{a}"]) for a in ("x", "y", "z")]
        except (KeyError, ValueError) as error:
            raise ValueError(f"{src}：刚体行 {r.get('rigid_name', '?')} 的 rigid_rot_* "
                             f"读不出三个数（{error}）—— 朝向这一维没有尺，重跑导出")
        if any(v != 0.0 for v in axes):
            oriented += 1
    parented = 0
    named = 0
    for r in rigid:
        try:
            bone = int(r["rigid_bone"])
        except (KeyError, ValueError) as error:
            raise ValueError(f"{src}：刚体行 {r.get('rigid_name', '?')} 的 rigid_bone "
                             f"读不出一个整数（{error}）—— 「该不该挂骨」这一维没有尺，重跑导出")
        if bone >= 0:
            parented += 1
        try:
            node = r["rigid_bone_node"]
        except KeyError:
            raise ValueError(f"{src}：刚体行 {r.get('rigid_name', '?')} 没有 rigid_bone_node "
                             "这一格 —— 「该挂哪根骨的名字」这一维没有尺，重跑导出")
        if (node or "").strip():
            named += 1
    return PhysicsExpectation(rigid_rows=len(rigid), joint_rows=len(joint),
                              oriented_rows=oriented, parented_rows=parented,
                              named_rows=named)


def parse_weights(text: str) -> list[list[tuple[int, float]]]:
    """weights.tsv → 每个顶点一串 `(全局骨号, 权重)`。

    这张表**不走** `parse_table`：第 2 列往后是变长的 `骨号:权重` 对（香菱每行 1~4 对），
    而 parse_table 遇到「列数比列名多」必须报错 —— 那是它该做的（多出来的列意味着有人
    改了格式却没改列名表）。所以这里单独一份解析，语义与 .ms 的
    `for j = cPairs to p.count` 一致。

    行序即顶点号：Max 那边既按行序落位、也拿 `vertex_index` 核对行序（`index_seq_bad`），
    这里同样要求第 i 行的 vertex_index == i（0-based）。不满足就抛 —— 权重按序号写入，
    行序错了就是整份权重错位（#13 那一类），不能让下一轮 Max 去「量出来再看」。
    """
    body = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln for ln in body.split("\n") if ln.strip() and not ln.startswith("#")]
    if not lines:
        raise ValueError("权重表是空的（连列名行都没有）")
    header = lines[0].split("\t")
    if tuple(header[:2]) != ("vertex_index", "bone_weight_pairs"):
        raise ValueError(f"权重表的前两列名不是 vertex_index/bone_weight_pairs，"
                         f"而是 {header[:2]}")
    verts: list[list[tuple[int, float]]] = []
    for number, line in enumerate(lines[1:], start=2):
        cells = line.split("\t")
        index = int(cells[0])
        if index != number - 2:
            raise ValueError(f"权重表第 {number} 行的 vertex_index 是 {index}，"
                             f"按行序应该是 {number - 2}：权重按序号写入，行序错位 = 全表错位")
        pairs: list[tuple[int, float]] = []
        for cell in cells[1:]:
            if not cell:
                continue          # 行尾多一个制表符：Max 那边同样当「没有这一对」跳过
            bone, sep, weight = cell.partition(":")
            if not sep:
                raise ValueError(f"权重表第 {number} 行有不含冒号的权重项 {cell!r}："
                                 "格式该是 `骨号:权重`，静跳过会少数链路")
            pairs.append((int(bone), float(weight)))
        verts.append(pairs)
    return verts


def skin_expectation(bones: list[dict[str, str]],
                     weights_rows: list[list[tuple[int, float]]]) -> SkinExpectation:
    """按上面那两条规则数一遍期望链路；骨号映射与节点名都在这里定，Max 那边不许自己猜。"""
    for number, row in enumerate(bones):
        if row.get("idx") is not None and int(row["idx"]) != number:
            raise ValueError(f"骨骼表第 {number} 行的 idx 是 {row['idx']}，与行序不符："
                             "权重表里的骨号就是这里的行序，错位会蒙错骨")
    skinned: list[str] = []
    for row in bones:
        if row.get("skin_flag") == "0":
            continue
        # 节点名 = Biped 目标名（人形骨复用 Biped 节点），否则 = 建出来的 BoneGeometry 名。
        skinned.append(semantic.node_name(row.get("biped_target"), row["bone_name"]))

    global_to_skin: dict[int, int] = {}
    skin_id = 0
    for i, row in enumerate(bones):
        if row.get("skin_flag") == "0":
            continue
        skin_id += 1
        global_to_skin[i + 1] = skin_id          # 权重表里的骨号是 0-based 全局行号

    counts = [0] * (skin_id + 1)
    links = 0
    zero_links = 0
    verts_no_exp = 0
    for pairs in weights_rows:
        touched = 0
        for bone, weight in pairs:
            sid = global_to_skin.get(bone + 1)
            if sid is None:
                continue          # 指向不蒙皮的骨/越界：由 pairs_dropped 那条 gate 兜
            if weight <= 0.0:
                # 权重 0.0 的链路 Max 存不下（写进去连骨号都不留，见 export_obj
                # 的 _worker_weights 注释）：它不进期望链路，单独计一条 `zero_links`。
                # 导出侧现在不会再发这种对，所以这一格读回非 0 就是「权重表里冒出了
                # 一条 Max 持不住的链路」—— 陈旧文件或有人手改表，红得有名有姓。
                zero_links += 1
                continue
            counts[sid] += 1
            links += 1
            touched += 1
        if touched == 0:
            verts_no_exp += 1
    by_bone = tuple((nm, counts[sid]) for sid, nm in enumerate(skinned, start=1)
                    if nm.endswith(LINK_SUBJECT_SUFFIXES))
    return SkinExpectation(links_expected=links, zero_links=zero_links,
                           verts_no_exp=verts_no_exp,
                           bones_skinned=skin_id, links_by_bone=by_bone)


# ---------------------------------------------------------------------------
# 骨盒回读的期望值（M4c）：从 limbs.tsv 脚段 + widths.tsv 算，一个数都不来自 Max
# ---------------------------------------------------------------------------
#
# 治的是哪一类谎：`足背 … -> 宽=3.71424 高=8.65601`、`脚趾 … -> 5.31702/1.86092`、
# `手部粗细 … -> (8.63193, 3.6286, 3.6286)` 这些行**看起来**就是验收，实际是写入那一刻
# 的自述：它们量完之后就再没人管过这根骨，而后面还有收尾转角（可能把「哪根轴离世界 Z
# 最近」翻过去）与足盒接趾（一次 #scale）。#37/#41 反复的正是「缩完又被谁改了 / 缩错了
# 轴」。现在把同一件事改成两方：Max 在最终场景里再量一次跨度，这边从**输入文件**拿着
# 网格量出来的截面要求它对撞。
#
# 两把尺分开报，因为写入侧本来就用的两把：
#  - 脚链四根：`mmd2maxFitCrossSection` 收的是**本地包围盒跨度** ⇒ 回读也用跨度，gate。
#  - 手掌/手指：`mmd2maxApplyWidths` 收的是 **#scale 的两个非长度分量** ⇒ gate 读 #scale。
#    同一根骨再报一份包围盒跨度（`hand_box_cm`，只要求出现）：`widths.tsv` 那个数是按
#    「网格里的世界直径」量的，Max 那边把它当成 scale 分量写进去，而 scale ≠ 跨度
#    （Taper、盒子不对称都会让它偏）。这一轮第一次真去量这个关系，所以先让它说话 ——
#    拿没证实的等式装闸 = 第二十一轮「骨盆那条 gate 永远过不去」的同一个错。
FOOT_WIDTH_KEYS = ("foot_w", "foot_h")
TOE_WIDTH_KEYS = ("toe_w", "toe_h")


class FootBox(NamedTuple):
    """脚链一根骨的骨盒期望：宽/高来自网格截面，长度只有趾骨有（见 build_spec 注释）。"""

    node: str
    width: float
    height: float
    length: float | None
    # 谁规定的：进日志/报错用，也决定 `length_cm` 是 gate 还是 report。
    source: str


class HandBox(NamedTuple):
    node: str
    section: float
    # 掌长目标（widths.tsv 第 3 列，只有 Hand 两行有）。None = 老产物没有这一列，
    # 此时不许凭空长出一条 Max 永远不会报的 hand_length_cm 尺。
    palm: float | None = None


def _box_float(limb: dict[str, str], key: str, row_label: str,
               minimum: float = 0.02) -> float:
    """把 limbs 的一列读成正厘米数；读不到/读成非正数一律抛。

    「这根骨没有截面目标」和「截面目标是 0」在判据表里都该是**没有这一行**，
    但脚段是导出侧必写的（export_obj 的脚段 9 列），所以缺列 = 两份产物分叉，
    静跳过会让整条脚链的骨盒验收凭空消失而日志全绿。
    """
    raw = limb.get(key)
    if raw is None or not str(raw).strip():
        raise ValueError(f"肢段表 {row_label} 的 {key} 列缺失：骨盒回读没有尺。"
                         "脚段的 10~18 列由导出侧必写，缺了就是 limbs.tsv 与判据分叉")
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"肢段表 {row_label} 的 {key} 不是数字：{raw!r}") from None
    if value <= minimum:
        raise ValueError(f"肢段表 {row_label} 的 {key} = {raw}，不是正厘米数："
                         "截面目标非正 = 网格那一段没量到东西，拿它当尺会要求骨头缩成 0")
    return value


def foot_pairs_with_mesh(skin_expect: SkinExpectation) -> frozenset[str]:
    """哪些脚链「有网格可量」由**权重表**回答，不由 limbs.tsv 的第 9 列回答。

    口径：`Foot` 与 `Toe0` 两节都至少挂着一个蒙皮顶点（`skin_expect.links_by_bone`
    的计数 > 0）。这两张表（bones/weights）是导出的**输入**，与 `export_obj` 写下的
    mode 列无关，所以「这双脚到底该不该有骨盒目标」第一次有了独立于写入侧的答案。

    为什么需要它：`export_obj._foot_profile` 有两种「量不到」。方向反了那种直接抛；
    而「脚掌上一个网格顶点也没有」是**合法**的退化 —— 样例夹具 `make_sample.py`
    只有 4 个躯干顶点，脚上从来没网格，硬要它有骨盒目标等于要求凭空捏一个数。
    真模型（香菱实测：Foot 363 条链路、Toe0 74 条）走到退化那一支就是事故，必须抛。
    区分这两者不许用 mode 列自己（那就是恒等式），所以问权重表。
    """
    counts = {name: n for name, n in skin_expect.links_by_bone}
    out = set()
    for (parent, child), mode in semantic.limb_mode_overrides().items():
        if mode == "foot" and counts.get(parent, 0) > 0 and counts.get(child, 0) > 0:
            out.add(f"{parent} -> {child}")
    return frozenset(out)


def limb_rows_of_mode(limbs: list[dict[str, str]], mode: str,
                      measured: frozenset[str] = frozenset()) -> list[dict[str, str]]:
    """取「这一类」的肢段行：类别由父子目标名决定（`semantic.limb_mode_overrides()`），
    第 9 列的 mode 只在这里被**核对**，不当来源。

    为什么不写 `if limb.get("mode") == "foot"`（上一版就是这么写的）：

    1. **规则 I**：mode 是导出侧写下去的那一列。拿它当「这具模型有没有脚盒验收」的唯一
       来源，等于让写入侧给自己发验收单 —— 它写什么，判据就量什么。
    2. **静默退化**：`export_obj._limb_rows` 在量不到鞋时曾经退回 `mode=link`。那一行
       照样是一条合法的肢段行，`fit/segment_count` 照样绿，但脚链的骨盒、接趾、复摆
       判据全部没有尺可拼 —— 一整族验收凭空消失，而没有任何一行有能力说「不」。
       现在：表里出现「名字上是脚段、mode 却不是 foot」，而这双脚在权重表里蒙着顶点
       （`measured`，见 `foot_pairs_with_mesh`）= 当场抛。`measured` 留空 = 只核对
       `leg` 那一类（腿段的 mode 是纯命名，不可能退化，所以无条件核对）。

    名字表是两张输入表（`semantic` 的 Biped 链）里的常量，与这一具模型的网格无关，
    所以「该有几行脚段」这件事的期望值不出自写入侧。

    收进行的标准是「名字与 mode 都说这一类」：名字说是、mode 说不是，而权重表证明这双
    脚没有网格（样例夹具那一支），就按写侧的 `mode` 认它普通段 —— 它没有 10~18 列，
    硬收进来 `_box_float` 只能抛一个「列缺失」，把一条**合法**的退化报成表格分叉。
    """
    overrides = semantic.limb_mode_overrides()
    out: list[dict[str, str]] = []
    for limb in limbs:
        # 名字缺一律抛，不「认不出就算普通段」：这两列是这张表的主键，空了以后
        # 下面所有按名字的判断都会把它当成不存在 —— 又是同一族静默丢失。
        if not limb.get("parent_target") or not limb.get("child_target"):
            raise ValueError(
                f"肢段表有一行缺 parent_target/child_target：{limb}"
                "（第 1~2 列是这张表的主键：Max 端 filterString 会丢掉空字段，"
                "索引整体错位，判据侧则认不出它是哪一段）")
        pair = (limb["parent_target"], limb["child_target"])
        want = overrides.get(pair)
        label = f"{pair[0]} -> {pair[1]}"
        got = limb.get("mode")
        if want is not None and got != want and (want != "foot" or label in measured):
            raise ValueError(
                f"肢段表 {label} 的第 9 列是 {got!r}，按目标名它必须是 {want!r}："
                f"{want} 段的 10~18 列（截面/长度目标）只有导出侧量到网格才写得出来，"
                "退化成普通段就等于把这一族的验收悄悄关掉 —— 宁可不拼表，也不拼一张"
                "自己不知道少了几行的表")
        if want != mode or got != mode:
            continue
        out.append(limb)
    return out


def box_targets(limbs: list[dict[str, str]],
                widths: list[dict[str, str]],
                measured: frozenset[str] = frozenset()
                ) -> tuple[list[FootBox], list[HandBox]]:
    """骨盒回读的期望值：脚链四根（含每根的宽/高/长度）+ 手掌手指各一根的粗细。

    脚链的顺序是「先足背后脚趾」，与 Max 那一段的写法一致，报告读起来才对得上日志。
    「这具模型该有几根脚链骨」由 `limb_rows_of_mode` 按目标名给出，不看第 9 列 ——
    见那里的规则 I 说明；`measured` 是 `foot_pairs_with_mesh` 给的答案，决定
    「脚段退化成普通段」在这里该不该抛。
    """
    foot: list[FootBox] = []
    for limb in limb_rows_of_mode(limbs, "foot", measured):
        label = f"{limb.get('parent_target')} -> {limb.get('child_target')}"
        parent = limb.get("parent_target")
        child = limb.get("child_target")
        foot.append(FootBox(parent,
                            _box_float(limb, FOOT_WIDTH_KEYS[0], label),
                            _box_float(limb, FOOT_WIDTH_KEYS[1], label),
                            None, "limbs 的 foot_w/foot_h（踝处足背截面）"))
        foot.append(FootBox(child,
                            _box_float(limb, TOE_WIDTH_KEYS[0], label),
                            _box_float(limb, TOE_WIDTH_KEYS[1], label),
                            _box_float(limb, "toe_len", label),
                            "limbs 的 toe_w/toe_h/toe_len（脚尖处截面与趾长）"))

    hands: list[HandBox] = []
    for item in widths:
        name = item.get("biped_target")
        raw = item.get("width")
        if not name or raw is None or not str(raw).strip():
            raise ValueError(f"粗细表有一行缺 biped_target/width：{item}")
        try:
            value = float(raw)
        except ValueError:
            raise ValueError(f"粗细表 {name} 的 width 不是数字：{raw!r}") from None
        if value <= 0.02:
            raise ValueError(f"粗细表 {name} 的 width = {raw}，不是正厘米数："
                             "截面目标非正 = 网格那一段没量到东西。"
                             ".ms 的 mmd2maxReadWidths 只收 > 0.001 的行，所以这边必须在它"
                             "之前拦住：否则规格表会多出一条永远没人报的尺")
        # 第 3 列「掌长」是 #136 加的可选列：有值才长出 `hand_length_cm` 那把尺。
        # 空串/缺键都按「没有目标」处理，绝不把 0 当成合法掌长（0cm 的手掌不存在）。
        palm = None
        raw_palm = item.get("palm_len")
        if raw_palm is not None and str(raw_palm).strip():
            try:
                palm = float(raw_palm)
            except ValueError:
                raise ValueError(f"粗细表 {name} 的 palm_len 不是数字：{raw_palm!r}") from None
            if palm <= 0.5:
                raise ValueError(f"粗细表 {name} 的 palm_len = {raw_palm}，不是正的掌长："
                                 "量不到手掌网格就别写这一格，Max 那边不会去缩")
        hands.append(HandBox(name, value, palm))
    return foot, hands


# 附属骨（BoneGeometry）方向回读的短骨门限：|tail-head| 小于它的骨在 PMX 里只是显示
# stub（香菱 右手首 0.38cm，而左手首 3.26cm），方向本身没意义，两侧一起不量。
# 数值住在 `targets.STUB_CM`，经 `MMD2MAX.CFG` 的 `MMD2MAX_STUBCM` 下发给 Max（规则 IV：
# 「应该是多少」只许有一个来源）。这里保留这个名字只作别名 —— 判据侧与 Max 侧从此读的
# 是同一个数，规格表不可能多出一条永远没人报的行。
ACCESSORY_STUB_CM = STUB_CM


def _bone_point(bone: dict[str, str], prefix: str, index: int) -> tuple[float, float, float]:
    """把 bones.tsv 一行的 head_x/y/z 或 tail_x/y/z 读成三个厘米数。

    缺列 = 两份导出产物分叉，必须抛：静默当 (0,0,0) 会把一根真骨读成零长 stub，
    于是这一行从规格表里消失，而没人会注意到「这具模型的附属骨没被验收」。
    """
    out: list[float] = []
    for axis in "xyz":
        key = f"{prefix}_{axis}"
        if key not in bone:
            raise ValueError(f"骨骼表第 {index} 行（{bone.get('bone_name')!r}）没有 {key} 列："
                             "附属骨方向回读没有尺。这六列是导出侧必写的")
        raw = bone[key]
        try:
            out.append(float(raw))
        except (TypeError, ValueError):
            raise ValueError(f"骨骼表第 {index} 行的 {key} 不是数字：{raw!r}") from None
    return out[0], out[1], out[2]


def accessory_dir_targets(bones: list[dict[str, str]],
                          biped_map: list[dict[str, str]]) -> list[str]:
    """附属骨方向回读的主体清单（`行号|骨名`），顺序 = bones.tsv 的行序。

    一根骨要被量，必须同时满足两条，两条都是**输入文件**里的事实，不是 Max 的自述
    （拿被验收的一方自己的输出当判据 = 本项目反复交底的那条老教训）：

    1. 它没有复用 Biped 节点：行号不在 biped_map 的 `bone_index` 里。
       .ms 的 mmd2maxCreateBones 对映射行 append 的是 Biped 节点本身，那批骨的朝向由
       struct_dir / box_rb 两组终局复核管（Biped 的「方向」是 #dir/#scale 的事，
       不是 transform.row1），不能混进这个通道。
    2. |tail-head| ≥ `ACCESSORY_STUB_CM`：短骨方向没意义。

    为什么主体是「行号|骨名」而不是骨名：Max 那边重名会自动加后缀（`.name = bone_name`
    失败/改名后 nodes[i].name 已经不是表里那个词），两边就对不上账；行号是表的位置，
    唯一且两边同源，而名字留在后面只为人读日志时认得出是哪根骨。
    """
    mapped: set[int] = set()
    for item in biped_map:
        raw = item.get("bone_index")
        try:
            mapped.add(int(str(raw).strip()))
        except (TypeError, ValueError):
            raise ValueError(
                f"Biped 映射表有一行的 bone_index 读成整数失败：{raw!r}。"
                "附属骨回读要靠它认出「哪些行被 Biped 节点接管」，这张表读不懂 = 这组判据没有尺") from None
    subjects: list[str] = []
    for index, bone in enumerate(bones):
        if index in mapped:
            continue
        if "bone_name" not in bone:
            raise ValueError(f"骨骼表第 {index} 行没有 bone_name 列：回读的主体拼不出来")
        head = _bone_point(bone, "head", index)
        tail = _bone_point(bone, "tail", index)
        if math.dist(head, tail) < ACCESSORY_STUB_CM:
            continue
        subjects.append(f"{index}|{bone['bone_name']}")
    return subjects


# ---------------------------------------------------------------------------
# 截图取景（#69）：特写够不够特写，是一个数，不是一句「图里有头」
# ---------------------------------------------------------------------------
#
# 名单 = .ms 的 mmd2maxScreenshot 里那些 `mmd2maxShotOne outDir baseName "<tag>"` 调用点，
# 一个不多一个不少。契约测试拿 .ms 的字面量对撞这三份名单：改了那边不改这边，
# 要么报告里冒出规格表不认识的行（orphan 判红），要么规格表多出一条永远没人报的行。
# 为什么要给截图建判据：这一组的用途是**视觉验收**（#12/#26/#27/#37/#66 都靠它），
# 而「取景有没有生效」原先没有任何读数 —— 第 33 轮实测胸带框纵向只占画面 18%，
# 那张图判不了任何东西，但日志、报告、CHECK 全绿，因为没人要求过它是特写。
SHOT_TAGS: tuple[str, ...] = (
    "front", "side", "top",
    "bone_front", "bone_side",
    "bone_foot_side", "bone_foot_front", "bone_foot_top",
    "bone_chest_front", "bone_chest_side", "bone_chest_top",
    "chest_front", "chest_side", "chest_top",
    "neck_side", "neck_front",
    "foot_side", "foot_front", "foot_top",
    "hand_front", "hand_side",
)

# tight = 取景前按视口宽高比把框的**水平**跨度收到与纵向同比例（.ms 的 mmd2maxAspectBox）。
# 只有「纵向自己定、横向只是余量」的两组带该紧：颈部与胸部 —— 它们的水平跨度是 ±10cm 的
# 余量 + 被张开的手臂撑出来的 119cm，跟要判的东西（那一截带里的高矮）没关系。
# 按节点算出来的框（脚/手）不紧：那种框的水平跨度就是内容本身，收紧会裁掉内容。
# 这份名单必须与 .ms 调用点里用 mmd2maxShotOneTight 的那几个逐字相同（契约测试对撞）。
SHOT_TIGHT_TAGS: tuple[str, ...] = (
    "bone_chest_front", "bone_chest_side", "bone_chest_top",
    "chest_front", "chest_side", "chest_top",
    "neck_side", "neck_front",
)

# 特写的最低纵向填充率（占视口高度的百分之几）。定价来自第 33 轮的实测：
# 胸带框 119cm 宽 × 19.7cm 高，两个角 `gw.transPoint` 投出来纵向 85px / 474px = **18%**，
# 4.55cm 的胸部骨在图上约 20px —— 「指着乳头」这句话在像素上不存在。
# 按宽高比收过之后框的长宽比 = 视口的，zoomToBounds 两维同时贴，实测那一轮的横向填充
# 是 517/820 = 63%，所以纵向也该到 63% 上下（4.55cm ≈ 69px）。45 这条线卡在
# 「坏的 18%」和「预期的 63%」中间，两头都留一倍以上余量 —— 它不要求正好 63，
# 只要求「别再悄悄退回去」。
SHOT_MIN_FILL_PCT = 45.0

# 非 tight 的那些张只要「投影成功」这个下限（.ms 投不出时报 -1.0，见 mmd2maxShotFill）。
# 全身图的填充率取决于模型的比例（裙子摊开的角色和瘦高角色不一样），给它定一个
# 「该占多少」没有物理理由，拿它当尺就是装饰。
SHOT_MIN_FILL_FLOOR = 0.0

# 取景框 = 整场景包围盒的那几张（.ms 里传的是 `b.min b.max`）。这一组的 `clip_hidden`
# 必须是 0，而且是**构造性**的 0：mmd2maxSceneBox 就是所有对象的 pivot ∪ bounds 的并，
# 所以没有一根骨能整个落在它外面。反过来，其余每一张都只框住身体的一截，框外必然有骨
# （俯视那几张最典型：头骨、手指、辫子全在框外），所以期望是「至少藏掉一根」。
# 为什么给这个数建判据：#72 的裁骨要是悄悄失效（`isHidden` 抛、类名判断写错、名单没还），
# 图上只是「又糊成一片」，跟以前一模一样，没人看得出来。这两条方向相反的 kind 让它变成
# 一个读数：特写报 0 = 裁骨没跑；全身报 >0 = 取景框被谁改小了。
SHOT_SCENE_TAGS: tuple[str, ...] = ("front", "side", "top", "bone_front", "bone_side")

# ---------------------------------------------------------------------------
# 截图**内容**（#115）：上面那些读数没有一个是像素
# ---------------------------------------------------------------------------
#
# fill_pct 拿的是骨的世界坐标经 `gw.transPoint` 投影，tags/clip_hidden 是计数 ——
# 于是「21 张全在、取景全对、每张打开都是纯黑」这个形状一路绿灯。第 83 轮 #112 就实拍
# 到过这个形状（desktop.capture 恒黑那时还没有这道闸）。#115 把话变成像素上的：
# 存完每张图立刻 `openBitMap` 回读盘上那个文件，按网格采样，报两个数
# —— 「有几种颜色」和「最多的那种占多少」。纯色（黑屏、白屏、没渲染出来）在这两行上
# 是 1 与 100%，一眼就红。
#
# 定价（先用探针量 API，再拿两轮真跑定数，两条路都走过才敢把数写死）：
# · 合成件（test_runs/probe115：make_parse_probe 的 direct 模式只剪掉主执行块，
#   所以探针里调的就是 mmd2maxSampleShot 本尊，不是探针复刻的一份）：
#   纯黑 / 纯白 → 采到 576、去重 **1**、最大同类 576（=100%）；左右各半 → 去重 2、
#   最大同类 288；随机噪声 → 去重 576、最大同类 1；24×24 逐点不同 → 去重 576
#   （边长恰好等于网格，不能被 `w < grid` 误杀）；8×8 / 0 字节 / 改名成 .png 的文本 /
#   根本没这个文件 → 三格全 **-1**。
# · 真图（第 84 轮香菱r80、第 85 轮香菱-海灯节，各 21 张全部采到 576 点）：
#   去重最少 **29**（85 轮 bone_front，只剩骨架的那一张），最多 275；
#   最大同类占比最低 3.47%、最高 **12.15%**（85 轮 front，全身图里背景那一格）。
# 两条线都卡在「坏的」与「预期的」中间，两头各留一倍以上余量：
# 10 ≤ 29（真图最差）而纯色只有 1；50% ≥ 12.15%（真图最差）而纯色是 100%。
# 和 SHOT_MIN_FILL_PCT 同一条定价纪律：不要求「正好多少」，只要求「别再悄悄退回去」。
# 两个边界记在这里，别以后当 bug 查：
# · 左右各半那张的最大同类是 288/576 = **50.0%**，`max` 判的是「≤」，所以它**过**
#   dominant 这条、挂在 distinct=2 上 —— 两行是两条独立的闸，不是同一条判据的备份。
# · 拿 PIL 独立重算同一批盘上 PNG：最大同类 21/21 逐张与 Max 报的数相同，去重色数
#   差 0~3（Max 的解码与色彩管理不是逐通道恒等映射）。这一位不改判闸方向，所以没把
#   「逐张相等」写成断言 —— 那会变成一条跟着 Max 版本走的假红。
SHOT_PIXEL_GRID = 24
SHOT_MIN_DISTINCT_COLORS = 10
SHOT_MAX_DOMINANT_PCT = 50.0

# 取景框的三根轴（与导出侧的 mmd2max.export_obj.FRAMING_AXES、tools/obj_run.FRAMING_AXES
# 同一套词）。胸与颈共用这一套，因为两张特写的行形状完全一样：三轴各一对框 + 一对紧边。
FRAMING_AXES = ("x", "y", "z")

# 每张 tight 截图的**水平**世界轴：.ms 的 mmd2maxAspectBox 收的就是这一根
# （正视→x、左视→y）。键集必须与 SHOT_TIGHT_TAGS 相同（测试对撞），轴选错则判据会拿
# y 的极值去要求 x 的读数 —— 那是一条永远绿、也永远看不见的错。
SHOT_H_AXIS: dict[str, str] = {
    "bone_chest_front": "x", "chest_front": "x", "neck_front": "x",
    "bone_chest_side": "y", "chest_side": "y", "neck_side": "y",
    # 顶视屏幕上横=世界 X、竖=世界 Y（与 .ms 的 mmd2maxHAxis / mmd2maxShotFill 同一口径），
    # 所以 bone_chest_top 收的是 x —— 与正视图同一根轴，不是 y。
    # #74 的 chest_top 与它同一只框、同一个视口，所以轴也一样：两张图的差别只有网格显不显。
    "bone_chest_top": "x", "chest_top": "x",
}


def shot_fill_rows(chest_bone: tuple[tuple, tuple] | None = None,
                   neck_bone: tuple[tuple, tuple] | None = None) -> list[tuple[str, ...]]:
    """截图段的规格行：每张图 填充率 + 裁骨数 + 去重色数 + 最大同类占比，四条聚合计数，
    加两张特写的「罩得住」判据。

    `when` 全是 `if_present`：截图段只在 `MMD2MAX_SHOT` 打开时跑，而且它排在保存与成功
    标记之后 —— 那段坏了不该把已经落盘的 .max 判成失败，但它一旦跑了，行数与填充率
    就必须按上面这两份名单对账（少一张图 = 少一行读数，靠 `tags` 那条 gate 响）。

    `chest_bone` / `neck_bone` 是导出侧量出来的 subject 端点极值 `((x,y,z)min, (x,y,z)max)`，
    None = 这具模型认不出那几根骨。
    """
    rows: list[tuple[str, ...]] = []
    for tag in SHOT_TAGS:
        floor = SHOT_MIN_FILL_PCT if tag in SHOT_TIGHT_TAGS else SHOT_MIN_FILL_FLOOR
        rows.append(row("shot_rb", "fill_pct", tag, floor, 0, "min",
                        when="if_present"))
    for tag in SHOT_TAGS:
        if tag in SHOT_SCENE_TAGS:
            rows.append(row("shot_rb", "clip_hidden", tag, 0, 0, "max",
                            when="if_present"))
        else:
            rows.append(row("shot_rb", "clip_hidden", tag, 1, 0, "min",
                            when="if_present"))
    rows.append(row("shot_rb", "tags", GLOBAL, len(SHOT_TAGS), 0, "gate",
                    when="if_present"))
    # 裁骨的分母（场景里有几根「骨」）。这一行是 report 不是 gate：拿它当闸就是猜
    # 「Biped 到底建了几根节点」，而那个数没有独立的真值源。它的用处是让 clip_hidden 能被
    # 读对 —— round39 实测 foot_top 藏掉 268 根，268 恰好等于附属骨总数，没有分母的话
    # 「藏了 268 根」与「321 根里只藏了附属骨那 268 根（53 根 Biped 骨盒一根没动）」
    # 是同一个读数。
    # round41 起 .ms 在这一格写的不再是一个数，而是逐类计数（objects 总数 + 每类几个 +
    # 认定为骨几个）：round40 证明光有分母只能说「认少了 53 根」，说不出**漏的是哪一类**，
    # 而那才是这一轮真正卡住的地方。report 行没有 expected，装得下这句话，不用新开支。
    rows.append(row("shot_rb", "clip_bones_total", GLOBAL, None, 0, "report",
                    when="if_present"))
    rows.append(row("shot_rb", "projection_fail", GLOBAL, 0, 0, "gate",
                    when="if_present"))
    # ---- 内容闸（#115）：每张两行 + 一条「采样到底跑了几张」的聚合 ----
    # 逐张那 42 行的 `when` 是 if_present，单靠它们防不住「采样代码一次都没被调到」：
    # 没有行 = 合法缺席 = 全绿。所以配一条 gate —— 与上面 tags / projection_fail 同一套路。
    # 用 row 而不是 _lock：「不是一张纯色图」与模型无关，是这一段的结构性要求，
    # 换一具模型不会让纯黑的图变成可以接受。
    for tag in SHOT_TAGS:
        rows.append(row("shot_rb", "distinct_colors", tag,
                        SHOT_MIN_DISTINCT_COLORS, 0, "min", when="if_present"))
    for tag in SHOT_TAGS:
        rows.append(row("shot_rb", "dominant_pct", tag,
                        SHOT_MAX_DOMINANT_PCT, 0, "max", when="if_present"))
    rows.append(row("shot_rb", "pixel_sampled", GLOBAL, len(SHOT_TAGS), 0, "gate",
                    when="if_present"))
    rulers = {"chest": chest_bone, "neck": neck_bone}
    for prefix in rulers:
        rows.extend(box_rows(prefix, rulers[prefix]))
    rows.extend(tight_box_rows(rulers))
    return rows


def box_rows(prefix: str, subject: tuple[tuple, tuple] | None) -> list[tuple[str, ...]]:
    """一张特写：Max 这一轮**实际用的**取景框必须罩住量出来的 subject 极值，三轴各一对。

    两把尺分得很清：读数是 Max 拿去 zoom 的框角，期望是导出侧从骨坐标量出来的 subject。
    方向各用一种单向 kind —— 框比要求的更宽不是缺陷（图只是更空），更窄、把骨裁在框外才是。
    第 35 轮的形状是「带低 4cm + 全绿」：当时唯一的尺是填充率，而填充率量的是框自己，
    框里一根骨都没有也照样 63%。第 36 轮补了 z 却漏了 x/y，于是换成侧视把骨裁在框的
    **前后**方向之外，照样全绿 —— 一根轴一根轴查，才不给「只缺一轴」留位置。
    认不出 subject 的模型（`subject=None`）六行退成 `report`：仍然要求 Max 报它用的框
    （否则「取景段没跑」和「跑了但框是空的」在报告里长得一样），只是没有尺。
    """
    rows: list[tuple[str, ...]] = []
    for i, axis in enumerate(FRAMING_AXES):
        for check, pick, kind in ((f"{prefix}_box_lo", 0, "max"),
                                  (f"{prefix}_box_hi", 1, "min")):
            expected = None if subject is None else subject[pick][i]
            rows.append(row("shot_rb", check, axis, expected, 0,
                            kind if expected is not None else "report", when="if_present"))
    return rows


def tight_box_rows(rulers: dict[str, tuple[tuple, tuple] | None]) -> list[tuple[str, ...]]:
    """每张 tight 截图**收完之后真正拿去 zoom 的那条水平跨度**必须罩得住骨（#70 的另一半）。

    为什么上面那六行不够：#69 的收框是在 zoom 之前**朝框中心**砍水平跨度的，那六行读的是
    砍之前的框。第 36 轮就坏在这儿 —— 框中心被身后的裙摆拽到 y=+9.8，砍完只剩
    y -0.4..20.0，而骨在 -11.75..-7.24：砍之前的框明明罩得住，图里却没有骨。
    这一对读的是砍之后的那条边，正好是「画面上有没有」的边界。
    尺按 tag 里的前缀挑（`chest` / `neck`）：#71 之前颈部那四行只能 report，因为导出侧
    没有颈链的尺；现在两边都有，四行升级为参与判定 —— 一张特写也不许只报数不被判。
    """
    rows: list[tuple[str, ...]] = []
    for tag in SHOT_TIGHT_TAGS:
        prefix = next((name for name in rulers if name in tag), None)
        subject = rulers.get(prefix) if prefix is not None else None
        i = FRAMING_AXES.index(SHOT_H_AXIS[tag])
        for check, pick, kind in (("tight_h_lo", 0, "max"), ("tight_h_hi", 1, "min")):
            expected = None if subject is None else subject[pick][i]
            rows.append(row("shot_rb", check, tag, expected, 0,
                            kind if expected is not None else "report", when="if_present"))
    return rows


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
          when: str = "always") -> tuple[str, ...]:
    """有定价读数就出 `lock`，否则出 `report`。

    「否则」有两种：这一轮没被定价过（换模型），以及这个主体不在定价表里（新增的
    肢段、改名的骨）。两种都不该拿香菱的数字去判红 —— 但那一行**必须出现**，
    否则「没量」和「量了没问题」在报告里长得一样。
    """
    if priced is None:
        return row(phase, check, subject, None, None, "report", when=when)
    return row(phase, check, subject, priced, tol, "lock", when=when)


def _priced(run: PricedRun | None, table: dict, key):
    """这一轮被定价过才从表里取数字，否则 None（→ report）。

    `run` 是 `pricing_of()` 的结果：它已经把「模型对不上（None）」和「模型对得上但
    配置没定价（抛）」两种情况分开了，所以这里只需要问「有没有」。
    """
    return table.get(key) if run is not None else None


def build_spec(*, model: str, config: str, vertex_count: int,
               limbs: list[dict[str, str]],
               skin_expect: SkinExpectation, widths: list[dict[str, str]],
               bones: list[dict[str, str]], biped_map: list[dict[str, str]],
               morph_count: int, physics: PhysicsExpectation | None,
               skin: bool = True,
               foot_reach: bool = True, rollback: bool = False,
               toe_ground: bool = False,
               chest_bone: tuple[tuple, tuple] | None = None,
               neck_bone: tuple[tuple, tuple] | None = None) -> list[tuple[str, ...]]:
    """装配一轮的全部判据规格。

    `config` 是必填的：它和 `vertex_count` / `morph_count` 一起决定「这一轮是不是被
    定价过的那个形状」（`pricing_of`）。不给默认值 —— 一个「默认按全骨」的形参会让
    `--drop-guides` 那一轮悄悄用上全骨的锁，而那正是 #106 要堵的形状。

    输入全部来自**导出结果**（weights 行数、limbs.tsv 的行、meta 的计数）与
    `mmd2max.targets` 的容差 —— 一个都不许从 Max 的日志里取，否则就成了「拿 Max
    自己报的数去要求 Max 报这个数」，那是恒等式，不是验收。

    哪些行出现也由导出结果决定：脚链、脚趾复摆、大腿扭转的主体都从 limbs 的 `mode`
    列推出来，所以换一具模型（指骨数不同、没有脚链）规格表自己就跟着变，不需要谁
    在实跑前手改这张表。

    `skin_expect` 是**必填**的：蒙皮那一组判据的期望值全部来自 weights.tsv / bones.tsv
    （`skin_expectation()`），没有它就没有尺。以前这里收的是 meta 的 `kept_bones`，
    而 `skin/bones` 报的是 `skinOps.GetNumberBones` —— 只有「每一根骨都参与蒙皮」的模型
    才碰巧相等（香菱正好如此，所以 23 轮都是绿的）；换一具有 skin_flag=0 的模型，
    那条 gate 会判红而错的是尺。现在两件事各归各位。

    `widths` 同样是**必填**的（可以是空表）：骨盒回读那一组（M4c）的尺来自这张表与
    limbs 的脚段。传空表 = 这具模型没有手部粗细表，Max 那边也读不到 widths.tsv，
    两边同时没有那些行；传了表却不给 Max（或反过来）由 `box_rb/nodes_measured` 判红。

    `bones` + `biped_map` 也**必填**（#66 的附属骨方向回读）：前者给每根附属骨的
    head/tail 当尺，后者说明「哪些行被 Biped 节点接管、因此不归这个通道量」。
    两张表都是导出的输入，不是 Max 的输出 —— 这条判据证的正是「表里写的方向有没有
    活着进到最后那个场景」，所以期望值不许来自场景自己。

    `rollback` 是快照回滚（M4d）的开关，值来自 `targets.ROLLBACK` → CFG 同一条路。
    它决定的是「有没有这条退路」，所以开与关之间规格表**整段**不同：关掉时那些行退成
    `if_present`（Max 一段都不跑，读数当然没有），打开时 `rollback/saved` 与三列差值
    必须出现 —— 「开关打开却什么都没报」正是「退路只是句注释」的样子，必须红。

    `toe_ground`（#134 加、#135 翻默认）值来自 `targets.TOE_GROUND` → CFG。产品默认 true
    （落地踩地，用户第 135 轮对着截图拍板）；这里的形参默认 false 只服务于直接调用/单测
    的贴鞋基线。它改的是 **kind** 不是行数：落地态把 Toe0 链的 `segment/len_cm` 与
    `foot_chain/chain_angle_deg` 降 report（搬 pivot 的已定价代价，数字照打印），并把
    `toe_shoe` 的闸从 penetration 换到 ground_gap；贴鞋态反过来。行数与开关无关。

    `physics`（#122）是**必填**的，值可以是 `None`：`None` = 这一轮没给 physics 文件，
    物理段整段不跑，那些行退成 `if_present`（合法缺席）；给了就是 `PhysicsExpectation`，
    四个数都从**导出的 physics.tsv** 数出来（`physics_expectation()`），而 meta 的
    `rigid_count` / `joint_count` 在装配时与它对撞、分叉就中止 —— 那两份是导出层里
    各算各的同一件事（PMX 声明 vs 落盘的表），Max 读的是表。Max 报回来的
    `physics_rb/*` 是它在场景里**真建出来**的代理与标记的计数：创建会抛错、代理会
    退化成圆柱、会进不了层、会离开 PMX 那个点、会一根骨也没挂上（#125）。第 64/64b 轮的
    `物理刚体代理：253（失败 1） 旋转失败=254` 与第 70/71 轮的
    `挂父后世界偏心=4 最大=94.6242` 当时全靠人读日志，这一段就是那段日志的闸化。

    `chest_bone` / `neck_bone`（#70、#71）是导出侧量的**取景主体骨端点极值**
    `((x,y,z)min, (x,y,z)max)`，None = 这具模型认不出那组骨（胸：叫「胸 / bust」的骨；
    颈：映射到 `Bip001 Spine2 / Neck / Head` 的那三节）。它们是两组特写判据的尺：Max
    只许报它用的框，框里到底有没有那几根骨得由这边问 —— 认不出时那些行退成 `report`
    （必须出现、不卡数）。两份尺来自同一具导出的骨坐标，但**各量各的骨**，所以一把尺
    丢了只有那一组降级，另一组照常卡数。
    """
    rows: list[tuple[str, ...]] = []
    # 这一轮是「被定价过的那个形状」吗？认模型用导出计数、认配置用导出开关，两者都不
    # 许看文件名（#106）。配置没定价过的被定价模型在这里就抛，装配不会往下走。
    run = pricing_of(vertex_count=vertex_count, morph_count=morph_count, config=config)
    # 脚段/腿段的类别由名字表定，mode 列只被核对（规则 I）；「这双脚有没有网格可量」
    # 问权重表，不问 mode 列 —— 见 `foot_pairs_with_mesh`。
    measured_feet = foot_pairs_with_mesh(skin_expect)
    foot_rows = limb_rows_of_mode(limbs, "foot", measured_feet)
    leg_rows = limb_rows_of_mode(limbs, "leg", measured_feet)

    def pair(limb: dict[str, str]) -> str:
        return f"{limb['parent_target']} -> {limb['child_target']}"

    # 落地态（#135）被搬 pivot 的那几根 Toe0 链：Foot -> Toe0。segment/foot_chain 对
    # 这几条链的读数在落地态是「用户选择的代价」而非缺陷，降 report（见下面两处分支）。
    toe_pairs = {pair(limb) for limb in foot_rows}

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
    # `dir_over` 随导出配置变（要转的骨集合不同），所以它来自定价条目而不是共享表；
    # 没被定价的那一具拿不到这个数，那三行退化成 report（`_priced` 返回 None）。
    converge_locks = dict(CONVERGE_LOCKS)
    if run is not None:
        converge_locks["dir_over"] = run.dir_over
    for check in ("dir_over", "place_max_cm", "place_sum_cm"):
        values = converge_locks.get(check, ())
        for pass_no in range(1, CONVERGE_MAX_PASSES + 1):
            when = "always" if pass_no == 1 else "if_present"
            value = _priced(run, {str(i + 1): v for i, v in enumerate(values)},
                            str(pass_no))
            rows.append(_lock("converge", check, str(pass_no), value,
                              CONVERGE_TOL[check], when=when))

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
        value = _priced(run, THIGH_KNEE_LOCKS, subject)
        rows.append(_lock("thigh_roll", "knee_err_cm", subject, value,
                          THIGH_KNEE_TOL_CM))
        rows.append(row("thigh_roll", "ankle_err_cm", subject, 0,
                        THIGH_ANKLE_TOL_CM, "gate"))

    # ── 脚趾复摆（#38）+ 接趾后复摆：主体 = mode=foot 的行 ────────
    # 复摆残差的上限就是 CFG 里的 MMD2MAX_TOETOL：同一个数在两侧各写一遍的日子
    # 已经结束（M2a），规格表直接引用 targets。
    # 这个上限**同时是 .ms 里「已在容差内就不动」的门控**，所以它一旦比下面 `segment /
    # len_cm` 的闸（SEGMENT_TOL_CM）松，就出现「这里放行、那里判红」的自相矛盾（#127，
    # 林尼实测 0.489997 被放行、段长红在 0.38872）。targets 里两个数已并成一个。
    # residual_after_reach_cm 在 FOOTREACH=false 时整段不跑。
    for limb in foot_rows:
        subject = limb["child_target"]
        rows.append(row("toe_snap", "residual_after_roll_cm", subject, 0,
                        TOE_TOL_CM, "gate"))
        rows.append(row("toe_snap", "residual_after_reach_cm", subject, 0,
                        TOE_TOL_CM, "gate",
                        when="always" if foot_reach else "if_present"))

    # ── 穿鞋检测（#134）：趾盒到底呆在鞋里没有 / 踩没踩在地面 ──────────
    # 这一族回答的是用户那句「Toe 趾盒也可以踩在地面吗？符合结构」—— 以前只有 #19 的
    # 截面尺（盒粗=鞋截面）与一句日志，没有任何一列读「盒底相对鞋底/地面的竖直位置」。
    # 两把尺分工：
    #   penetration_cm = max(0, 鞋底z − 盒底z)：盒底戳到鞋底下方多少（穿模量）。
    #   ground_gap_cm  = 盒底世界 z：盒底离地面（z=0）多远。
    # 开关 MMD2MAX_TOE_GROUND 决定哪一把是闸（与 foot_reach/rollback 同一条配置分支）：
    #   关（回滚，贴鞋尖）⇒ penetration 是 max 闸（盒必须在鞋里，穿模≈0），ground_gap 只 report
    #     （盒底悬在鞋底那么高是翘头鞋的几何必然，不是缺陷 —— 拿它当闸会把现状判红）；
    #   开（落地，#135 起默认）⇒ ground_gap 升 gate（盒底必须真落到 z≈0，证明落地这一刀落下去了），
    #     penetration 退 report（落地必然戳穿翘起的鞋底，诚实报穿模量，不拿它判红 ——
    #     搬 pivot 的代价另由 segment/foot_chain 的 Toe0 链 report 行摆出来，见下面两处分支）。
    # 行数与开关无关（同 rollback 那 19 行）：开关改的是 kind，不是合同有没有这一格。
    for limb in foot_rows:
        subject = limb["child_target"]
        if toe_ground:
            rows.append(row("toe_shoe", "penetration_cm", subject, None, None, "report"))
            rows.append(row("toe_shoe", "ground_gap_cm", subject, 0,
                            GROUND_TOL_CM, "gate"))
        else:
            rows.append(row("toe_shoe", "penetration_cm", subject, 0,
                            PENETRATION_TOL_CM, "max"))
            rows.append(row("toe_shoe", "ground_gap_cm", subject, None, None, "report"))

    # ── 快照回滚（M4d）：载体本身每轮验一次，守卫只在开关打开时判 ──
    # 这一组和上面所有行的区别：它判的不是「几何对不对」，而是「写坏了有没有退路」。
    # 所以它的期望值全来自**探针实测**（probes/figfile_m4h.txt）而不是导出文件 ——
    # 导出侧没有任何一列能告诉你 `.fig` 在这台 Max 上能不能存下来。
    #
    # 自检那三列（pos/box/scale）期望 0：扰动 30% 之后 load 回来，位置、骨盒跨度、
    # #scale 三样都该回到基准（探针 H2 量到盒差 5.7e-06、scale 差 4.8e-07，
    # H4 量到「load 刚存的那份」位置差 6.8e-05 —— 都在 ROLLBACK_TOL_CM 下面两个数量级）。
    # `perturb_cm` 故意只当 report：它是「这次扰动真的动了」的证据，非要它等于几没有意义，
    # 但没有它，上面三个 0 就可能是「压根没扰动」的假绿。
    # `snap_cost_cm` 同样是 report：它量的是「一次裸存图动不动场景」——探针里那 2.13cm
    # 到底算 saveFigFile 还是算进出 Figure 模式的翻转，只有产品里这条能拆开回答。
    # `angle_deg` 是第 27 轮之后补的第四维：那一轮上面三列全绿，事后锁骨的世界长度轴却
    # 转了 89.5 度，双臂落位误差跟着从 0.955 涨到 1.957。尺在 targets.ROLLBACK_TOL_DEG。
    # **它自己的读数同样是证据**：第 27、28 两轮它都读 0.0 —— load 那一刻帧是好的，坏账
    # 由后面的 #scale 写刀结。所以这四行合起来只证明「载体当场无损」，不证明「这一轮没被
    # 回滚改动过」；后者靠 placement 的 lock 行，开关一开它就红给你看。
    when_rb = "always" if rollback else "if_present"
    for check in ("saved", "loaded"):
        rows.append(row("rollback", check, GLOBAL, "true", 0, "gate", when=when_rb))
    for check in ("perturb_cm", "snap_cost_cm", "perturbed_bone"):
        rows.append(row("rollback", check, GLOBAL, None, None, "report", when=when_rb))
    for check in ("pos_cm", "box_cm", "scale_cm"):
        rows.append(row("rollback", check, GLOBAL, 0, ROLLBACK_TOL_CM, "gate",
                        when=when_rb))
    rows.append(row("rollback", "angle_deg", GLOBAL, 0, ROLLBACK_TOL_DEG, "gate",
                    when=when_rb))
    # 守卫：`foot_worse_bones` 就是触发器本身，期望 0 —— 也就是说「回滚发生了」这一条
    # 一定把这一轮判红。这不是苛刻，是 M4d 的全部意义：退路只在它被走过的那一轮必须
    # 大声响，否则它会以「静默救场」的样子把一个真缺陷藏成一次成功交付。
    # 退回之后的几列（restore_*）比的是「和基准差多少」，所以厘米那三列同样是
    # 0 ± ROLLBACK_TOL_CM；朝向那一列用 ROLLBACK_TOL_DEG —— 守卫退的正是接趾那些
    # #scale 写刀，而第 27 轮量到「位置/盒/scale/轴朝向当场全回来、事后锁骨仍转了
    # 89.5 度」：这四列是「当下」的尺，判「退回合格」用它，别拿它当「这一轮没被回滚
    # 碰过」的证明（那一件事由 placement 的 lock 行管）。
    # 它们只有真退回时才存在 → if_present。
    when_guard = "always" if (rollback and foot_reach) else "if_present"
    rows.append(row("rollback", "guard_saved", GLOBAL, "true", 0, "gate",
                    when=when_guard))
    for check in ("foot_before_cm", "foot_after_cm", "guard_fired"):
        rows.append(row("rollback", check, GLOBAL, None, None, "report",
                        when=when_guard))
    rows.append(row("rollback", "foot_worse_bones", GLOBAL, 0, 0, "gate",
                    when=when_guard))
    rows.append(row("rollback", "foot_restored", GLOBAL, "true", 0, "gate",
                    when="if_present"))
    for check, tol in (("foot_restore_pos_cm", ROLLBACK_TOL_CM),
                       ("foot_restore_box_cm", ROLLBACK_TOL_CM),
                       ("foot_restore_scale_cm", ROLLBACK_TOL_CM),
                       ("foot_restore_angle_deg", ROLLBACK_TOL_DEG)):
        rows.append(row("rollback", check, GLOBAL, 0, tol, "gate",
                        when="if_present"))

    # ── 脚链：骨轴连续（#40）、盒隙（#41）──────────────────────────
    # 偏航（bone_yaw / toe_yaw）以前在这里有两行 gate，删了：它比的是
    # `mmd2maxBoxWantDir` 给的目标，而那个目标就是转角 pass **写进骨轴的方向** ——
    # 同源即恒等式，第十五轮压平之后它照读 0.0 全绿，用户截图里骨盒仍是指歪的。
    # `.ms` 那边把同一个数降级成一行 `脚骨偏航结论` 日志（和截图对时序还有用），
    # 判据换成下面两条不问写入侧的：**骨轴 vs 回读到的子关节**（链向）与
    # **两个盒子之间的空气**（盒隙）。
    # 这一族（以及任何 phase）的**去重条数**由 `tests/test_verdicts.py::MaxChannelContractTests.GRADED_SURFACE`
    # 数着：删/加通道要连那张名单一起改。第 81 轮的教训是「`.ms` 与 Python **对称地**删掉一族 ⇒
    # 两侧键集照旧一致、每个通道都有 Max 报读数、驱动无 orphan，三道**一致性**闸全绿」。
    # 当场咬住的只有 `RealExportTests.test_shape_of_the_priced_spec` 那两道**计数**
    # （规格 551→547 行、`CHANNELS` 118→116），它钉的是**一具真导出夹具**的数 —— 换模型本来
    # 就得重数，一族删 4 行、另一族加 4 行还能把总行数抵回去。名单按族数，抵不掉。

    for limb in foot_rows:
        pair_subject = pair(limb)
        value = _priced(run, CHAIN_ANGLE_LOCKS, pair_subject)
        # 落地态（#135）搬了 Toe0 pivot，Foot->Toe0 的链向必然偏离 PMX 靶（实测 +6.5°）。
        # 那是用户选择的落地形态的代价、不是缺陷：降 report（数字照打印），贴鞋态仍 lock/gate。
        if toe_ground and pair_subject in toe_pairs:
            rows.append(row("foot_chain", "chain_angle_deg", pair_subject,
                            None, None, "report"))
        else:
            rows.append(_lock("foot_chain", "chain_angle_deg", pair_subject, value,
                              CHAIN_ANGLE_TOL_DEG))
        # #139：gap_cm 是**单向**尺。Max 侧报的是「子关节沿轴 − 父盒远端」：>0 =
        # 两块盒子之间有空气（#41 要防的断链），<0 = 父盒盖过子关节、更连贯。
        # 旧规格写 gate(0 ± 容差) 会把 #135 落地态下必然出现的负 gap 判成缺陷
        # （Toe0 前移后 Foot 长度轴翻到 -Z，落地搬的 z 直接落在长度轴投影上）。
        # 改成 max 上限：正向空气缝超过 GAP_TOL_CM 仍然判红，护栏一条不少；
        # 负向重叠放行，但数字照打印，且 `gap_covered` 与 `gap_unconnected` 仍 gate。
        rows.append(row("foot_chain", "gap_cm", pair_subject,
                        GAP_TOL_CM, 0, "max"))
        rows.append(row("foot_chain", "gap_covered", pair_subject,
                        GAP_COVERED_TRUE, 0, "gate"))
    rows.append(_lock("foot_chain", "broken_roots", GLOBAL,
                      _priced(run, {GLOBAL: CHAIN_BROKEN_LOCK}, GLOBAL),
                      0))
    rows.append(row("foot_chain", "gap_unconnected", GLOBAL, 0, 0, "gate"))

    # ── 段校验：limbs 每一对一行 ───────────────────────────────────
    # 期望值是**这一轮真能达到的长度**：骨盆三段按髋中点量（见 HIP_CHILDREN 的注释），
    # 其余段就是 limbs 的 desired_len。报的是 Max 从场景里**量到**的长度，所以 Max
    # 侧解析目标值错一位、或骨盆闭环没按同一个锚点走，都会在这里露出来。
    # 指节按 if_present：导出侧写了 5×3 而 Biped 的指槽建不出那么多是可能的。
    anchor = hip_anchor(limbs)
    priced_segments = SEGMENT_LOCKS.get(model, {})
    for limb in limbs:
        when = "if_present" if "Finger" in limb["child_target"] else "always"
        subject = pair(limb)
        price = priced_segments.get(subject)
        # 落地态（#135）Foot->Toe0 段长必然偏离 PMX 靶（搬 pivot 实测 +0.866cm）：
        # 代价降 report（数字照打印），贴鞋态仍 gate/lock。只 demote 被搬的那几条链。
        if toe_ground and subject in toe_pairs:
            rows.append(row("segment", "len_cm", subject, None, None, "report",
                            when=when))
        elif price is None:
            rows.append(row("segment", "len_cm", subject,
                            segment_expectation(limb, anchor),
                            SEGMENT_TOL_CM, "gate", when=when))
        else:
            # 这一段在这个模型上够不着 PMX 值（来历见 SEGMENT_LOCKS）：改成钉住读数，
            # 行还在、还是必经，只是尺从「输入表的目标长度」换成「已定价的读数」。
            rows.append(row("segment", "len_cm", subject, price[0], price[1],
                            "lock", when=when))

    # ── 落位偏差：每个测到的节点一行 ─────────────────────────────
    # 19 个点是 .ms 里的字面量清单（契约测试对撞），按模型可能缺 Neck 之类 → if_present。
    for bone in PLACEMENT_GATE_NODES:
        rows.append(row("placement", "pos_err_cm", bone, 0,
                        PLACEMENT_GATE_TOL_CM, "gate", when="if_present"))
    for bone in sorted(PLACEMENT_LOCKS):
        value = _priced(run, {k: v[0] for k, v in PLACEMENT_LOCKS.items()}, bone)
        tol = PLACEMENT_LOCKS[bone][1]
        rows.append(_lock("placement", "pos_err_cm", bone, value, tol,
                          when="if_present"))
    for bone in PLACEMENT_RAY_NODES:
        # `max` 不是笔误：b 方案里 UpperArm 的**写入目标**就是锁骨骨盒射线点
        # （`mmd2maxShoulderRayTarget`），所以「实际 vs 射线点」量的是「我写下去的东西
        # 有没有被 Biped 求解器挪走」，而不是「落位合不合 PMX」—— 后者是上面那组
        # `pos_err_cm`（want 直接来自 limbs.tsv 的列）的活。写成 gate（期望=0）就是把
        # 恒等式当达标证据（第十七轮 #26/#40/#41 那一类）；写成上界才是它本来的语义：
        # 这条只承诺「偏差不得超过这个上界」，超了照样红，写卡住 / 求解器把骨拖走都抓得住。
        # 不写成 lock：lock 的期望值是**这一具模型定价过的读数**，换模型就退化，
        # 而这个上界与模型无关（见 PricedLockTests 那条「非定价模型不许出现 lock」）。
        rows.append(row("placement", "ray_err_cm", bone, PLACEMENT_RAY_TOL_CM, 0,
                        "max", when="if_present"))

    # ── 结构向复核 ────────────────────────────────────────────────
    # 超标的根数随导出配置变（与 `dir_over` 同一条理：它数的正是「还有几根转不到位」，
    # 而辅助骨那 50 根本来就分在收敛循环的活里）。
    struct_over = {GLOBAL: run.struct_over} if run else {}
    rows.append(_lock("struct_dir", "over_tol", GLOBAL,
                      _priced(run, struct_over, GLOBAL), 0))
    for check in ("measurable", "short_skipped"):
        rows.append(row("struct_dir", check, GLOBAL, None, None, "report"))

    # ── 骨盒回读（M4c）：期望值全部来自 limbs 脚段与 widths 表 ────
    # 上面 `足背 / 脚趾 / 手部粗细` 那三行日志是**写入那一刻**的自述；这一组读的是
    # 最终场景（四道终局复核之后、附属骨之前）。尺的来源见 `box_targets` 的注释。
    # 容差不许写成绝对常数：`mmd2maxFitBoneExtent` 的退出条件就是 2% 相对误差，
    # 尺比承诺更严会每轮红在分辨率上（`targets.box_tol_cm`）。
    foot_boxes, hand_boxes = box_targets(limbs, widths, measured_feet)
    for box in foot_boxes:
        rows.append(row("box_rb", "width_cm", box.node, f"{box.width:.6f}",
                        box_tol_cm(box.width), "gate"))
        rows.append(row("box_rb", "height_cm", box.node, f"{box.height:.6f}",
                        box_tol_cm(box.height), "gate"))
        # 足背那根骨的长度不由任何输入列规定：接趾把盒远端拉到「PMX 脚掌沿压平轴的
        # 投影」（第二十轮实测 9.73629），既不是 desired_len（11.228，两 pivot 的间距，
        # 由 segment/len_cm 管）也不是 foot_len（14.097，踝→鞋尖）。所以它是 report。
        if box.length is None:
            rows.append(row("box_rb", "length_cm", box.node, None, None, "report"))
        else:
            rows.append(row("box_rb", "length_cm", box.node, f"{box.length:.6f}",
                            box_tol_cm(box.length), "gate"))
        # 这根骨最终用哪根局部轴当长度轴（1/2/3）。期望值不在输入文件里，但它决定
        # 上面三行报的是哪三根轴：宽/高对调的时候，这一行是唯一的现场证据。
        rows.append(row("box_rb", "len_axis", box.node, None, None, "report"))
        # #79：把盒角点投到「这根骨指向的方向」上直接量世界跨度，**不碰盒轴记录**。
        # 上面三行都按 #75 那笔记录挑轴，所以「长」报的到底是哪根边的长度由记录说了算
        # —— #78 之前那版海灯节把趾长 0.5722 收在**竖直**轴上（现场三维的 `鞋尖−脚掌`
        # 在这只鞋上几乎竖直），而 3.4674 那根边顺着趾方向戳出去；宽/高/长三行按记录
        # 各自对得上自己的尺，整组读数自洽地绿。这一行不问记录，谁朝着骨的指向就量谁：
        # 对不上期望的趾长就只有「薄边没朝前」一种解释（55 轮实测两具模型都是 0 溢量：
        # 香菱 3.56919 == 长 3.56919，海灯节 0.572203 == 长 0.572202）。
        # 足背仍然只能 report：它的盒长由接趾投影决定，两边都没有独立的尺（理由见上面
        # length_cm 那段），拿 Max 自己的读数当期望就是规则 II 禁的那种恒等式。
        # 容差比上面三行宽：投影跨度会把转角余量算进去（长度轴偏 3 度时，5.32cm 的宽边
        # 就往这个方向多掺 0.29cm），公式与定价见 `targets.span_tol_cm`。
        # 足背那两行的实跑读数顺便记下，免得下一轮被当成缺陷：它的参考方向是**三维**的
        # 「实测踝 pivot → PMX 脚掌」，而骨盒按压平轴写，两者之间那道俯仰全进了跨度 ——
        # 香菱 长=12.6445 / 沿指向=15.2758，海灯节 18.6818 / 20.6234。
        if box.length is None:
            rows.append(row("box_rb", "span_want_cm", box.node, None, None, "report"))
        else:
            rows.append(row("box_rb", "span_want_cm", box.node, f"{box.length:.6f}",
                            span_tol_cm(box.length, box.width, box.height), "gate"))
    for box in hand_boxes:
        # gate 读的是 #scale（写入侧的那把尺），report 读的是包围盒跨度（用户看得见的那把）。
        rows.append(row("box_rb", "hand_scale_cm", box.node, f"{box.section:.6f}",
                        box_tol_cm(box.section), "gate"))
        rows.append(row("box_rb", "hand_box_cm", box.node, None, None, "report"))
        # 掌长（#136）：期望来自 widths.tsv 的网格实测，Max 在最终场景里量本地盒跨度。
        # 与 hand_scale_cm 同一条口径（2% 相对 + 0.02cm 地板），但量的是长度轴，
        # 抓的正是「横截面缩到位了、桨形盒却还比手掌长一截」这一格。
        if box.palm is not None:
            rows.append(row("box_rb", "hand_length_cm", box.node,
                            f"{box.palm:.6f}", box_tol_cm(box.palm), "gate"))
    rows.append(row("box_rb", "nodes_measured", GLOBAL,
                    len(foot_boxes) + len(hand_boxes), 0, "gate"))
    rows.append(row("box_rb", "read_fail", GLOBAL, 0, 0, "gate"))

    # ── 趾盒终帧重收的覆盖（#129）：那一刀落没落下是一个数 ─────────
    # 上面 box_rb 那组读的是**最终场景**的盒子，但它的期望值全部来自 limbs.tsv，
    # 与「这一刀是在哪一帧落的」无关。所以 #78 那个缺陷（终帧没重收、盒子上留的还是
    # 早帧那次的轴）在旧表里只有 R Toe0 宽高互换那两行会红，而第 48 轮的 L 恰好早帧
    # 与终帧的 |z| 顺序一致 —— 同一件事整个被盖住。Max 在这一段的自述只有
    # `pass/ran/toe_box_final 1`：一双脚跳过一根它照样是 1。
    # 「该落几刀」由输入表回答（`box_targets` 里带趾长的那几根 = 每双脚的趾骨），
    # 「落下几刀」由 Max 报，两边不同源 ⇒ 不是恒等式。这一行也不会有**合法的**跳过：
    # 终帧那道守卫现在与早帧那道是同一批条件（书写顺序不同），而「截面算不算一个正数」
    # 只有 `_box_float` 一把尺（≤ 0.02 在写规格表这一步就抛，Max 根本不会被启动）
    # —— 原来终帧守卫里多出的 `toeW/toeH > 0.05` 是第二把尺，会把 0.02~0.05 那一段的
    # 合法跳过报成红，与 #127 同一类，已删（见 .ms 那段注释）。
    for box in foot_boxes:
        if box.length is None:
            continue        # 足背的盒长由接趾投影决定，#78 只重收趾骨那根
        rows.append(row("toe_box_final", "fit", box.node, "true", 0, "gate"))

    # ── 附属骨父级 ────────────────────────────────────────────────
    rows.append(row("accessory", "no_parent", GLOBAL, 0, 0, "gate"))
    for check in ("on_biped", "on_bone"):
        rows.append(row("accessory", check, GLOBAL, None, None, "report"))

    # ── 附属骨方向回读（#66）──────────────────────────────────────
    # 上面那三行只数「挂上了谁」，数量对得上但每根骨**指哪**一个也没看。胸部骨那一刀
    # 改的是 tail（从网格量出来的乳头点），所以这一组才是它的验收：期望 0 度 =
    # 场景里那根骨的局部 X 轴与 bones.tsv 的 tail-head 重合，容差用已经定价的
    # DIR_TOL_DEG（不新造一个数：`.ms` 那边超标的计数 MMD2MAX_DIRTOL 用的就是它）。
    # `measurable` 的期望值来自两张输入表（行序减去被 Biped 接管的行、再剔短骨），
    # 所以「Max 少走了一遍表」会红在这里，而不是变成一根也没量的绿灯。
    dir_subjects = accessory_dir_targets(bones, biped_map)
    for subject in dir_subjects:
        rows.append(row("bone_rb", "dir_deg", subject, 0, DIR_TOL_DEG, "gate"))
    rows.append(row("bone_rb", "measurable", GLOBAL, len(dir_subjects), 0, "gate"))
    rows.append(row("bone_rb", "over_tol", GLOBAL, 0, 0, "gate"))
    rows.append(row("bone_rb", "read_fail", GLOBAL, 0, 0, "gate"))
    # 这四行是第一轮的新通道：没有来历的数字不许当尺（模块文档那条），
    # 所以先 report —— 跑出读数、谁要卡它就拿那一轮的日志定价，再翻 gate。
    for check in ("short_skipped", "worst_deg", "worst_bone",
                  "pivot_max_cm", "pivot_max_bone"):
        rows.append(row("bone_rb", check, GLOBAL, None, None, "report"))

    # ── 截图取景（#69、#70、#71）：特写够不够特写、框里有没有那几根骨 ──
    rows.extend(shot_fill_rows(chest_bone, neck_bone))

    # ── 蒙皮（--no-skin 时整段跳过）──────────────────────────────
    when = "always" if skin else "if_present"
    rows.append(row("skin", "bones", GLOBAL, skin_expect.bones_skinned, 0, "gate",
                    when=when))
    rows.append(row("skin", "failed_vertices", GLOBAL, 0, 0, "gate", when=when))
    rows.append(row("skin", "written_vertices", GLOBAL, vertex_count, 0, "gate",
                    when=when))

    # ── 蒙皮回读（M4b）：期望值全部来自 weights.tsv / bones.tsv ────
    # 上面那三条 skin/* 全是**写入侧的自述**（boneTotal 是 AddBone 之后数一遍，
    # written_vertices 数的是「ReplaceVertexWeights 返回没抛」）。这一组读的是场景：
    # skinOps 从那个 Skin 修改器里把每个顶点的骨号与权重取回来，再和这边算的期望对撞。
    #
    # 三条分组，各自管一件事：
    #  ① 「量没量到」：read_fail / verts_read / unmodified_verts。
    #     unmodified_verts 的期望是 `verts_no_exp` 而不是 0 —— 权重表里本来就没有链路的
    #     顶点确实不该被写入，硬写 0 等于拿一条不成立的尺去判（#19 那类「永远过不去的
    #     gate」）。而一旦 Max 那边的 verts_no_exp 与这边不一致，这条就会跟着红。
    #  ② 「解析有没有分叉」：index_seq_bad / pairs_dropped / verts_no_exp / links_expected /
    #     zero_links。这几条量的都是 Max 那份 weights.tsv 解析器 —— 期望 0（前两）或这边的
    #     计数（后三）。权重指向 skin_flag=0 的骨会被丢掉（pairs_dropped），那是真缺陷：
    #     那一块网格从此不会跟着动，所以它是 gate 而不是 report。
    #     zero_links 期望同样是 0：导出侧已经把「印出来是 0.000000」的对挡在表外，
    #     表里再出现这种对 = 有人拿旧文件或手改的文件喂 Max，那条链路 Max 一定不持有。
    #  ③ 「场景里的权重对不对」：zero_verts / dom_mismatch / weight_err_max / sum_err_max /
    #     links_read / links_missing / link_diff_max / link_mismatch_bones，外加脚尖与足首
    #     各一根骨的链路数（#35 的可见读数：Toe0 从 0 变 74、Foot 从 377 降到 363）。
    rows.append(row("skin_rb", "verts_read", GLOBAL, vertex_count, 0, "gate", when=when))
    rows.append(row("skin_rb", "read_fail", GLOBAL, 0, 0, "gate", when=when))
    rows.append(row("skin_rb", "unmodified_verts", GLOBAL,
                    skin_expect.verts_no_exp, 0, "gate", when=when))
    rows.append(row("skin_rb", "index_seq_bad", GLOBAL, 0, 0, "gate", when=when))
    rows.append(row("skin_rb", "pairs_dropped", GLOBAL, 0, 0, "gate", when=when))
    rows.append(row("skin_rb", "zero_links", GLOBAL, skin_expect.zero_links, 0, "gate",
                    when=when))
    rows.append(row("skin_rb", "verts_no_exp", GLOBAL,
                    skin_expect.verts_no_exp, 0, "gate", when=when))
    rows.append(row("skin_rb", "links_expected", GLOBAL,
                    skin_expect.links_expected, 0, "gate", when=when))
    rows.append(row("skin_rb", "links_read", GLOBAL,
                    skin_expect.links_expected, 0, "gate", when=when))
    rows.append(row("skin_rb", "links_missing", GLOBAL, 0, 0, "gate", when=when))
    rows.append(row("skin_rb", "name_mismatch", GLOBAL, 0, 0, "gate", when=when))
    rows.append(row("skin_rb", "zero_verts", GLOBAL, 0, 0, "gate", when=when))
    rows.append(row("skin_rb", "dom_mismatch", GLOBAL, 0, 0, "gate", when=when))
    rows.append(row("skin_rb", "link_diff_max", GLOBAL, 0, 0, "gate", when=when))
    rows.append(row("skin_rb", "link_mismatch_bones", GLOBAL, 0, 0, "gate", when=when))
    rows.append(row("skin_rb", "weight_err_max", GLOBAL, 0, WEIGHT_ERR_TOL, "gate",
                    when=when))
    rows.append(row("skin_rb", "sum_err_max", GLOBAL, 0, WEIGHT_SUM_TOL, "gate",
                    when=when))
    # 这三条是「发生过什么」而不是「该等于几」：补写次数与补救前的坍缩数只要出现就够，
    # 定成 gate 反而会在某一轮真的救回顶点时把 good case 判成 FAIL。
    for check in ("zero_verts_pre", "repaired", "elapsed_s"):
        rows.append(row("skin_rb", check, GLOBAL, None, None, "report", when=when))
    for subject, count in skin_expect.links_by_bone:
        rows.append(row("skin_rb", "links", subject, count, 0, "gate", when=when))

    # ── 表情通道（#24/#25：容量 ≠ 已用，必须逐条回读名字与目标）──
    # 表情表为空（CFG 允许 MMD2MAX_MORPHS 空）时整段跳过 → if_present。
    # 通道容量故意不设判据：WM3_NumberOfChannels 是「已分配」，报它等于给自己打分。
    for check in ("blocks", "built", "with_target", "with_name"):
        rows.append(row("morpher", check, GLOBAL, morph_count, 0, "gate",
                        when="if_present"))

    # ── 物理代理与关节标记（#122）：这几行就是那句日志汇总的闸化 ──
    # `mmd2maxBuildPhysics` 末尾一直有一整串计数（created / rigidFail / rotFail /
    # capFalls / worldFail / layerFail …），可它**只走 mmd2maxLog**。于是
    # 第 64/64b 轮的「`物理刚体代理：253（失败 1） 旋转失败=254`」、
    # 第 70 轮的「`挂父后世界偏心=4 最大=94.6242`」、第 71 轮的
    # 「`循环后仍偏=4 最大=94.6242@RB_21_右ひざ`」全是人一行行读日志读出来的 ——
    # 判据表上那些轮次都是绿的。
    # 五条「该有几根」的尺来自 physics.tsv（rigid/joint 的行数、朝向非零的行数、
    # `rigid_bone >= 0` 因而**该挂上骨**的行数、`rigid_bone_node` 因而**认得出挂哪根**的行数）；
    # 其余每条卡的是 0，各自对应一种真实下场：抛错、没转上、中心没挪到形心、
    # 挂父把那一步搬走、离开 PMX 那个点、循环跑完仍在外面、胶囊退成圆柱、没进层、
    # 父骨带着非单位的缩放矩阵。
    # `rigid_parented`（#125）补的是这一族最后一个绿盲：挂父那三层条件
    # （`boneNew >= 0` / 在 `boneNodes` 范围内 / 那个位置真有节点）任何一层落空，
    # 代理就钉在场景根上，而 `rigid_created` 照样 +1、`off_center` / `world_failed` /
    # `after_failed` 照样读 0 —— 那三把尺量的是「形心在不在 PMX 那个点上」，
    # 而一个没挂父的代理在绑定姿态下**正好就在那儿**（位置是我们自己按 PMX 摆的）。
    # 于是「254 根全绿」与「254 根一根都不跟骨动」在旧表上逐字相同，差别只在时间轴上
    # 才看得见。Max 侧数的是**回读** `proxy.parent`（不是「我那条 `.parent=` 抛没抛」），
    # 期望来自表 —— 两份各算各的，和 `rigid_created` 同构。
    # 「没挂上」几根不进表：它恒等于 `rigid_created - rigid_parented`，多给一行就是把
    # 同一个数判两遍（#124 给 `world_worst_cm` 的那条理由同形）；它在日志里逐根点名。
    # `rigid_parent_named`（#126）管的是下一层：挂上了骨，但挂的是**不是这一根**。
    # `rigid_parented` 只回读 `proxy.parent != undefined`，所以「父级是隔壁那根骨」在它
    # 眼里一分不差；而 `.parent=` 那一行的下标就是表里的 `rigid_bone`，拿它自己验它自己
    # 是恒等式（`boneNodes[j] == parN` 永远成立，因为 parN 就是从那个槽取的）。
    # 唯一两端不同源的公共量是**名字**：期望名由导出侧从 bones.tsv 那一行算
    # （`semantic.node_name` → physics.tsv 的 `rigid_bone_node`），实读名是 Max 在场景里
    # 拿到的 `proxy.parent.name`。中间隔着「表行序 = boneNodes 数组序」这句从来没有读数
    # 在验的假设 —— 现有闸只核过两边的**个数**（`rows.count != boneNodes.count` 三处），
    # 顺序错了它们照样绿。香菱 254 根里有 8 根的父骨是短骨：`bone_rb` 按 STUB 跳过它们，
    # 那 8 根此前没有任何逐行回读碰过。
    # 名字为空的行不进期望（重名骨，见 `_bone_node_names`）：这一格 ≤ `rigid_parented`，
    # 差几根在汇总行 `名字不计=N` 里看得见（香菱与克雷薇都读 0，即两具都没有重名保留骨）。
    # `parent_scaled` 卡 0。#122 初稿把它留成 `report`，理由写的是「探针 87 那具量到
    # 202 根，父骨本来就可能有一批缩放非单位的」—— 那句是假的，而且假在第 57 条刚登记的
    # 那类病上（把一次读数当模型属性，没问是谁量的）：第 69 轮那把尺读 `parent.scale`，
    # Biped 骨没这个属性、逐根当场抛（就是同一行那 42 条「创建失败」），254 根里只有挂在
    # 附属骨/Dummy 上的进了分母；换成探针 90 的 F2 那把活的尺（缩放 = `parent.transform`
    # 三行的长度，任何节点都读得到）之后**同一轮读 0**。此后每一轮都是 0：
    # 71/74/75/79/80/84/89（香菱）、90（克雷薇），而这两具的分母里分别有 42 / 13 根的
    # 父骨就是 Biped 骨 —— 「Biped 骨自带非 1 缩放是常态」这句话从来没被量过。
    # 口径要说死，因为**另一种**「父骨缩放」确实非 1，而且是我们自己写的：Biped 的段缩放
    # （第 89 轮 `肢段 Bip001 L UpperArm -> L Forearm … scale: [20.8675,…] -> [19.5224,…]`，
    # 骨长就是这么凑出来的）不进 `node.transform` 的行长度 —— 同一轮里那两根代理的父骨
    # 缩放仍读 1.0。这一格量的是「父骨的世界矩阵带不带缩放」，也就是会不会把挂上去的代理
    # 整块放大；physics.tsv 的 size 是绝对长度，父骨一带缩放代理就变形，而其余三把位置闸
    # （off_center / world_failed / after_failed）只看形心在不在 PMX 那个点上，一根都
    # 抓不到「整块大一倍」，世界盒并集那一行又只进日志 —— 这条轴上只有这一把尺。
    when_phys = "always" if physics is not None else "if_present"
    # 没给 physics 文件 ⇒ 整段不跑：期望 0 根，同时退成 if_present（合法缺席）。
    want = physics if physics is not None else PhysicsExpectation(0, 0, 0, 0, 0)
    for check, expected in (("rigid_created", str(want.rigid_rows)),
                            ("rigid_parented", str(want.parented_rows)),
                            ("rigid_parent_named", str(want.named_rows)),
                            ("joint_created", str(want.joint_rows)),
                            ("rot_oriented", str(want.oriented_rows)),
                            ("rot_ctrl_native", str(want.rigid_rows)),
                            ("rigid_failed", "0"), ("joint_failed", "0"),
                            ("rot_failed", "0"), ("center_failed", "0"),
                            ("off_center", "0"), ("link_shifted", "0"),
                            ("world_failed", "0"), ("after_failed", "0"),
                            ("cap_cylinder", "0"), ("layer_failed", "0"),
                            ("parent_scaled", "0")):
        rows.append(row("physics_rb", check, GLOBAL, expected, 0, "gate",
                        when=when_phys))
    rows.append(row("physics_rb", "world_worst_cm", GLOBAL, None, None, "report",
                    when=when_phys))
    # #132：朝向这一半此前**一把尺都没有**。link_shifted 只量形心，而收尾那句
    # `proxy.center=` 把形心强行搬回 PMX 那个点 —— 第 106 轮林尼 link_shifted=0、
    # world_worst_cm=1.64975e-05 全绿，同一轮 RB_1_首 的朝向却歪了 1.049 度
    # （弦长 0.0183069）。根因是默认的 Euler_XYZ 旋转控制器存不下万向锁上的旋转：
    # 那根代理挂在摆过姿势的 Bip001 Neck 下，局部欧拉 Y = -89.9999（探针 97 诊断行），
    # 而探针 97 X2 把**正确的**局部 quat 直接写进它的 .value，读回来是另一个 quat
    # （弦长 0.0407014）—— 不是写法的问题，是表示能力不够，重发多少句都拽不回。
    # 修法是建出来就换 Bezier_Rotation（探针 97 X1/X3 每一步弦长 0.0，探针 99 B
    # 三种形状都活）。
    #
    # 两行各治一种绿盲，不重复：
    #   orient_worst   量**结果**（最终世界矩阵 vs 从 Python 度数造出的应当矩阵，
    #                  两端不同源；阈值 ORIENT_TOL 的十个噪声读数见 targets.py）。
    #                  它是 gate 而不是 report —— 与 world_worst_cm 相反，因为朝向
    #                  没有别的闸在卡，这个最大弦长本身就是唯一的判据。
    #   rot_ctrl_native 量**机制**（所有写做完之后回读控制器类）。为什么结果之外还要
    #                  机制：orient_worst 只在「这一具的父骨姿势正好把局部旋转顶到死点」
    #                  时才响，香菱/克雷薇/海灯节 不撞死点（探针 98 离线量了 6 具 1956 根
    #                  刚体，导出侧死点 0 根），控制器退回去它们照样全绿 —— 正是 #125/#126
    #                  那类「症状没出现就当成没病」。期望 = rigid_rows，与 rigid_created
    #                  同源不同数：那边数建出来几根，这边数几根真是四元数原生。
    # 日志里的「没换上」不给行：它恒等于 rigid_created - rot_ctrl_native。
    rows.append(row("physics_rb", "orient_worst", GLOBAL, 0, ORIENT_TOL, "gate",
                    when=when_phys))

    # ── pass 时间轴（M3）──────────────────────────────────────────
    # 每个 pass 一行 `pass/ran/<id>=1`：恰好一次。注册表还多干了三件事（顺序、作废、
    # 归因），都靠这些行当时间轴骨架，见 mmd2max/passes.py。
    # 开关明确打开的那些从 if_present 提回 always：`FOOTREACH=true` 时足盒接趾**必须**
    # 跑到，没跑是故障，不该被当成合法跳过。
    rows.extend(passes.spec_rows({
        "skin_apply": "always" if skin else "if_present",
        "skin_verify": "always" if skin else "if_present",
        "foot_reach": "always" if foot_reach else "if_present",
        "toe_snap_reach": "always" if foot_reach else "if_present",
        # 趾盒落地（#134）：开关打开 ⇒ 这一刀必须跑到（没跑就是「开关只是句注释」）；
        # 关着时整段不跑，退 if_present。穿鞋检测尺只要有脚链就必跑（量盒底 vs 鞋底）。
        "toe_ground": "always" if toe_ground else "if_present",
        "toe_shoe": "always" if foot_rows else "if_present",
        # 回滚开关打开 ⇒ 自检**必须**跑到（没跑就是「退路只是句注释」）。守卫那一遍
        # 只有「开关开 + 接趾这一刀真跑了 + 基准拍得出来」三样都成立才会出现，前两条
        # 在这里能判，第三条（目录写不出文件）只有实跑知道 —— 它由 `rollback/guard_saved`
        # 那行 always 的 gate 负责响，而不是靠把这一行也提成 always。
        "rollback_selftest": "always" if rollback else "if_present",
        "foot_reach_guard": "always" if (rollback and foot_reach) else "if_present",
        "morpher_build": "always" if morph_count > 0 else "if_present",
        # 物理段（#122）：装配侧知道「这一轮有没有 physics 表」，所以 `pass/ran/physics_build`
        # 与上面那 17 行走的是同一条 when —— 不许出现「ran 是 always、读数却全空」或反过来
        # 「读数 always、ran 允许缺席」，那两种都是把同一段代码的两种说法装进一张表。
        "physics_build": when_phys,
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

    `@<记录名>\\t列名…`（第 2 行起）是同文件里**另一种记录**的列名，口径逐字对齐
    Max 端的 `mmd2maxReadTable`（scripts/max_import_obj.ms:1253）：它的第 1 个字段是记录名
    本身，列名从第 2 个字段起对齐；第 1 个字段仍报成 `row_kind`，所以两边都能按
    `row_kind` 分流。为什么不把两套列名并进同一张表再逐行 zip（Max 那边是「一张列名表 +
    每行原始字段数组」，按下标取，天然不会串）：Python 这边一行是一个 dict，并进来就等于
    让 `rigid_index` 与 `joint_index` **同时绑在同一个字段上**（两者的对齐位置都是 2），
    于是每条 RIGID 行都自带一个值为刚体号的下标，数出来的关节数看着正常 —— 那正是
    #122 要在 `physics_expectation` 里抛出来的那种「看着正常的错数」。两套列名撞车
    同样直接抛，不许先到先得。
    """
    body = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln for ln in body.split("\n") if ln.strip() and not ln.startswith("#")]
    if not lines:
        raise ValueError("表是空的（连列名行都没有）")
    header = tuple(lines[0].split("\t"))
    # 记录名 -> 该记录自己的列名（从第 2 个字段起对齐）
    kinds: dict[str, tuple[str, ...]] = {}
    for line in lines[1:]:
        cells = line.split("\t")
        if not cells[0].startswith("@"):
            continue
        name = cells[0][1:]
        if not name:
            raise ValueError(f"`@` 行没有记录名：{line}")
        if name in kinds:
            raise ValueError(f"记录 {name} 的列名行出现两次，后者会整条盖掉前者")
        if dup := set(cells[1:]) & set(header):
            raise ValueError(f"记录 {name} 的列名 {sorted(dup)} 与主表头撞了："
                             "两种记录读同一格，取到的数没有来历")
        kinds[name] = tuple(cells[1:])
    rows: list[dict[str, str]] = []
    for number, line in enumerate(lines[1:], start=2):
        cells = line.split("\t")
        if cells[0].startswith("@"):
            continue
        if kind := kinds.get(cells[0]):
            names, offset = kind, 1
        else:
            names, offset = header, 0
        if len(cells) - offset > len(names):
            raise ValueError(f"第 {number} 行有 {len(cells) - offset} 列，比"
                             f"{'记录 ' + cells[0] + ' 的' if offset else '列名行的'}"
                             f"{len(names)} 列还多：{line}")
        row = dict(zip(names, cells[offset:]))
        if offset:
            row["row_kind"] = cells[0]
        rows.append(row)
    return header, rows


# Max 侧必须出现的通道：(phase, check)。契约测试拿它和 .ms 里的
# `mmd2maxReport "…" "…"` 字面量对撞 —— 少一个就是「这条判据永远不会有人填」，
# 多一个就是「报告里出现规格表不认识的行」。两侧都由测试逼着同步。
# fixture 必须覆盖三种 mode（foot / leg / 其余）与指节，否则通道集合会缺类。

# 蒙皮通道的 fixture 走**真解析**（这两小段就是导出的形状：变长权重行 + 一根不蒙皮的骨），
# 而不是手搭一个 SkinExpectation —— 手搭的话，`skin_rb` 的通道集合就只反映我当时的想象。
FIXTURE_BONES = "\t".join(("idx", "bone_name", "parent", "role", "biped_target",
                           "head_x", "head_y", "head_z", "tail_x", "tail_y", "tail_z",
                           "skin_flag", "max_parent")) + "\n" + "\n".join((
    "\t".join(("0", "全ての親", "-1", "control", "", "0", "0", "0", "0", "0", "1",
               "0", "-1")),
    "\t".join(("1", "左足", "0", "skeleton", "Bip001 L Foot", "0", "0", "0", "0", "0",
               "1", "1", "-1")),
    "\t".join(("2", "左つま先", "1", "skeleton", "Bip001 L Toe0", "0", "0", "0", "0",
               "0", "1", "1", "-1")),
    "\t".join(("3", "裙子A", "0", "accessory", "", "0", "0", "0", "0", "0", "1",
               "1", "-1")),
)) + "\n"

FIXTURE_WEIGHTS = "vertex_index\tbone_weight_pairs\n" + "\n".join((
    "0\t1:0.700000\t2:0.300000",
    "1\t2:1.000000",
    "2\t3:1.000000",
)) + "\n"

FIXTURE_SKIN_EXPECT = skin_expectation(parse_table(FIXTURE_BONES)[1],
                                       parse_weights(FIXTURE_WEIGHTS))

# 脚段那 9 个附加列 fixture 必须带齐（至少是 box_targets 会查的那 5 列 + toe_len）：
# 少了任何一列，CHANNELS 就少一类通道，而契约测试只会看见「.ms 多报了没人判的行」。
# 数字取自香菱的真导出（同一具模型、同一把尺），改它们要连着看 M4c 的金标准。
# 第 3 列 palm_len（#136）必须在这份夹具里出现：CHANNELS 是「.ms 里所有报告通道」
# 的对照表，少写这一格，`box_rb/hand_length_cm` 就会变成「报告有、规格没有」的孤儿。
FIXTURE_WIDTHS = "\t".join(("biped_target", "width", "palm_len")) + "\n" + "\n".join((
    "Bip001 L Hand\t3.6286\t6.5000",
    "Bip001 R Hand\t3.6953\t6.5000",
)) + "\n"

# 附属骨方向回读（#66）的第二份 fixture：biped_map 决定「哪些行被 Biped 节点接管」，
# 因此决定 bone_rb/dir_deg 该出几行。前 3 行按真导出那样映射，裙子A 不映射 ——
# 于是 `accessory_dir_targets` 只认出第 3 行，CHANNELS 里就有这个通道。
# 写空表的话这一组通道整体消失，契约测试只会看见「.ms 多报了没人判的行」。
FIXTURE_BIPED_MAP = "\t".join(("bone_index", "biped_node", "pmx_bone_name")) + "\n" \
    + "\n".join((
        "0\tBip001\t全ての親",
        "1\tBip001 L Foot\t左足",
        "2\tBip001 L Toe0\t左つま先",
    )) + "\n"

CHANNELS: frozenset[tuple[str, str]] = frozenset(
    (r[0], r[1]) for r in build_spec(
        model=PRICED_MODEL, vertex_count=1,
        # 这一份只要「有哪些通道」，不要任何定价读数：顶点/表情写成 1，
        # `pricing_of` 认不出这具「模型」→ 整片退成 report。config 同理是占位串，
        # 认不出模型时它连参与都不参与（所以不许有人在这里编一组真配置来「顺便测锁」，
        # 那会把这份通道清单和某一轮的锁绑死）。
        config="unpriced",
        limbs=[{"parent_target": "Bip001 L Foot", "child_target": "Bip001 L Toe0",
                "desired_len": "11.228109", "mode": "foot",
                "foot_len": "14.097240", "toe_len": "3.569191",
                "toe_w": "5.317024", "toe_h": "1.860916",
                "foot_w": "3.714245", "foot_h": "8.656011"},
               {"parent_target": "Bip001 L Thigh", "child_target": "Bip001 L Calf",
                "desired_len": "36.382676", "mode": "leg"},
               {"parent_target": "Bip001 L Finger0", "child_target": "Bip001 L Finger01",
                "desired_len": "2.344292", "mode": "link"},
               {"parent_target": "Bip001 Spine", "child_target": "Bip001 Spine1",
                "desired_len": "8.325677", "mode": "link"}],
        skin_expect=FIXTURE_SKIN_EXPECT, morph_count=1,
        # `physics=None` 不是「忘了喂物理表」，而是这份清单只要通道名：physics_rb 那 17 条
        # 的 check 名与表里有几根刚体无关（表为空时同样要报 0），所以不需要物理 fixture，
        # 只要 `physics` 是必填参数 —— 漏传会在下面这一句就抛，而不是让 obj_run 少装一段判据。
        physics=None, skin=True, foot_reach=True,
        rollback=True,
        bones=parse_table(FIXTURE_BONES)[1],
        biped_map=parse_table(FIXTURE_BIPED_MAP)[1],
        widths=parse_table(FIXTURE_WIDTHS)[1])
)

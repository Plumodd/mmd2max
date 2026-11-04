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

锁只在被定价的那具模型上生效
----------------------------
`lock` 的数字全部来自一具具体模型的一次实跑。5.6995 厘米是按香菱 158 厘米折出来的，
换一具 80 厘米的角色它必然超差 —— 那不是缺陷，是换了模型。所以每个锁都先问
`model == PRICED_MODEL`：不是那一具就退化成 `report`（仍然要求这一行出现，只是不卡
数字），等谁真跑了那具模型再把它定价。
"""
from __future__ import annotations

import math
from typing import NamedTuple

from mmd2max import passes
from mmd2max.targets import (
    DIR_TOL_DEG,
    GAP_TOL_CM,
    TOE_TOL_CM,
    ROLLBACK_TOL_CM,
    ROLLBACK_TOL_DEG,
    WEIGHT_ERR_TOL,
    WEIGHT_SUM_TOL,
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
# 两条规则必须与 .ms 逐字同语义，否则第一轮就会假 FAIL：
#  1. 参与蒙皮的骨 = `skin_flag != "0"` 的行，Skin 里的骨号按表里的行序从 1 开始累加
#     （mmd2maxApplySkin 的 globalToSkin）。
#  2. 链路按**骨号出现次数**计，不按权重大小计（M4f C2：≲1e-8 的权重会被存成 0.0，
#     骨号还在；按权重数就会少数链路）。
LINK_SUBJECT_SUFFIXES = (" Toe0", " Foot")


class SkinExpectation(NamedTuple):
    """一次蒙皮的期望值，全部从输入文件算出，一个都不来自 Max。"""

    links_expected: int
    verts_no_exp: int
    bones_skinned: int
    # 看得见的那几根（脚尖/足首）：(Max 里的节点名, 期望链路数)，按骨号顺序。
    links_by_bone: tuple[tuple[str, int], ...]


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
        skinned.append(row.get("biped_target") or row["bone_name"])

    global_to_skin: dict[int, int] = {}
    skin_id = 0
    for i, row in enumerate(bones):
        if row.get("skin_flag") == "0":
            continue
        skin_id += 1
        global_to_skin[i + 1] = skin_id          # 权重表里的骨号是 0-based 全局行号

    counts = [0] * (skin_id + 1)
    links = 0
    verts_no_exp = 0
    for pairs in weights_rows:
        touched = 0
        for bone, _weight in pairs:
            sid = global_to_skin.get(bone + 1)
            if sid is None:
                continue          # 指向不蒙皮的骨/越界：由 pairs_dropped 那条 gate 兜
            counts[sid] += 1
            links += 1
            touched += 1
        if touched == 0:
            verts_no_exp += 1
    by_bone = tuple((nm, counts[sid]) for sid, nm in enumerate(skinned, start=1)
                    if nm.endswith(LINK_SUBJECT_SUFFIXES))
    return SkinExpectation(links_expected=links, verts_no_exp=verts_no_exp,
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


def box_targets(limbs: list[dict[str, str]],
                widths: list[dict[str, str]]) -> tuple[list[FootBox], list[HandBox]]:
    """骨盒回读的期望值：脚链四根（含每根的宽/高/长度）+ 手掌手指各一根的粗细。

    脚链的顺序是「先足背后脚趾」，与 Max 那一段的写法一致，报告读起来才对得上日志。
    """
    foot: list[FootBox] = []
    for limb in limbs:
        if limb.get("mode") != "foot":
            continue
        label = f"{limb.get('parent_target')} -> {limb.get('child_target')}"
        parent = limb.get("parent_target")
        child = limb.get("child_target")
        if not parent or not child:
            raise ValueError(f"肢段表脚段 {label!r} 缺 target 名：回读要有节点名当主体")
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
        hands.append(HandBox(name, value))
    return foot, hands


# 附属骨（BoneGeometry）方向回读的短骨门限：|tail-head| 小于它的骨在 PMX 里只是显示
# stub（香菱 右手首 0.38cm，而左手首 3.26cm），方向本身没意义，两侧一起不量。
# **这个数在 .ms 里还有一份字面量**（mmd2maxVerifyAccessoryDirs 的 `length want < 1.0`）：
# 两边不是同一条门限，规格表就会多出一条永远没人报的行（或反过来，报告里冒出没判的行），
# 两种都是红的 —— 契约测试拿字面量对撞，改一边就报。
ACCESSORY_STUB_CM = 1.0


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
    """截图取景的规格行：每张图一行填充率 + 一行裁骨数 + 两条聚合计数 + 两张特写的「罩得住」判据。

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
               skin_expect: SkinExpectation, widths: list[dict[str, str]],
               bones: list[dict[str, str]], biped_map: list[dict[str, str]],
               morph_count: int, skin: bool = True,
               foot_reach: bool = True, rollback: bool = False,
               chest_bone: tuple[tuple, tuple] | None = None,
               neck_bone: tuple[tuple, tuple] | None = None) -> list[tuple[str, ...]]:
    """装配一轮的全部判据规格。

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

    `chest_bone` / `neck_bone`（#70、#71）是导出侧量的**取景主体骨端点极值**
    `((x,y,z)min, (x,y,z)max)`，None = 这具模型认不出那组骨（胸：叫「胸 / bust」的骨；
    颈：映射到 `Bip001 Spine2 / Neck / Head` 的那三节）。它们是两组特写判据的尺：Max
    只许报它用的框，框里到底有没有那几根骨得由这边问 —— 认不出时那些行退成 `report`
    （必须出现、不卡数）。两份尺来自同一具导出的骨坐标，但**各量各的骨**，所以一把尺
    丢了只有那一组降级，另一组照常卡数。
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

    # ── 骨盒回读（M4c）：期望值全部来自 limbs 脚段与 widths 表 ────
    # 上面 `足背 / 脚趾 / 手部粗细` 那三行日志是**写入那一刻**的自述；这一组读的是
    # 最终场景（四道终局复核之后、附属骨之前）。尺的来源见 `box_targets` 的注释。
    # 容差不许写成绝对常数：`mmd2maxFitBoneExtent` 的退出条件就是 2% 相对误差，
    # 尺比承诺更严会每轮红在分辨率上（`targets.box_tol_cm`）。
    foot_boxes, hand_boxes = box_targets(limbs, widths)
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
    rows.append(row("box_rb", "nodes_measured", GLOBAL,
                    len(foot_boxes) + len(hand_boxes), 0, "gate"))
    rows.append(row("box_rb", "read_fail", GLOBAL, 0, 0, "gate"))

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
    #  ② 「解析有没有分叉」：index_seq_bad / pairs_dropped / verts_no_exp / links_expected。
    #     这四条量的都是 Max 那份 weights.tsv 解析器 —— 期望 0（前两）或这边的计数（后两）。
    #     权重指向 skin_flag=0 的骨会被丢掉（pairs_dropped），那是真缺陷：那一块网格从此
    #     不会跟着动，所以它是 gate 而不是 report。
    #  ③ 「场景里的权重对不对」：zero_verts / dom_mismatch / weight_err_max / sum_err_max /
    #     links_read / links_missing / link_diff_max / link_mismatch_bones，外加脚尖与足首
    #     各一根骨的链路数（#35 的可见读数：Toe0 从 0 变 74、Foot 从 377 降到 363）。
    rows.append(row("skin_rb", "verts_read", GLOBAL, vertex_count, 0, "gate", when=when))
    rows.append(row("skin_rb", "read_fail", GLOBAL, 0, 0, "gate", when=when))
    rows.append(row("skin_rb", "unmodified_verts", GLOBAL,
                    skin_expect.verts_no_exp, 0, "gate", when=when))
    rows.append(row("skin_rb", "index_seq_bad", GLOBAL, 0, 0, "gate", when=when))
    rows.append(row("skin_rb", "pairs_dropped", GLOBAL, 0, 0, "gate", when=when))
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
        # 回滚开关打开 ⇒ 自检**必须**跑到（没跑就是「退路只是句注释」）。守卫那一遍
        # 只有「开关开 + 接趾这一刀真跑了 + 基准拍得出来」三样都成立才会出现，前两条
        # 在这里能判，第三条（目录写不出文件）只有实跑知道 —— 它由 `rollback/guard_saved`
        # 那行 always 的 gate 负责响，而不是靠把这一行也提成 always。
        "rollback_selftest": "always" if rollback else "if_present",
        "foot_reach_guard": "always" if (rollback and foot_reach) else "if_present",
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
FIXTURE_WIDTHS = "\t".join(("biped_target", "width")) + "\n" + "\n".join((
    "Bip001 L Hand\t3.6286",
    "Bip001 R Hand\t3.6953",
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
        skin_expect=FIXTURE_SKIN_EXPECT, morph_count=1, skin=True, foot_reach=True,
        rollback=True,
        bones=parse_table(FIXTURE_BONES)[1],
        biped_map=parse_table(FIXTURE_BIPED_MAP)[1],
        widths=parse_table(FIXTURE_WIDTHS)[1])
)

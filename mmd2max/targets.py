"""装配目标值（容差与开关）的唯一来源 —— M2。

这五个数原先写死在 `scripts/max_import_obj.ms` 里（`global MMD2MAX_DIRTOL = 3.0` 那一类）。
那意味着装配口径由「跑脚本的那一侧」决定：Python 既不知道这几个数存在，也不校验它们，
判据变了要等一整轮 Max 跑完才看得出来。现在写的一方是 Python（`tools/obj_run.py` 把它们
落进 `MMD2MAX.CFG`），Max 只读不写；M2b 的 verdicts 表再从同一份数算 PASS/FAIL。两边同源，
不可能分叉 —— 与 M1b 对路径/开关做的是同一件事。

每一个数都是**定价过的取舍**，不是随手拍的；动之前先读 README_HANDOVER.md 第 7 节对应轮次，
那里记着它治的是哪个可见缺陷。改动必须是几何中性的（`tools/diff_round.py` 归一对比两轮日志
⇒ 除「配置:」行外无实质差异），否则这一轮的落位/转角读数会跟着变。
"""
from __future__ import annotations

# 方向容差（度）。MAXScript 侧三处消费同一个数（转角尝试门控 / 收敛「方向超标」计数 /
# 退出 Figure 模式后的「结构向 复核」），所以它必须是一个值而不是三份写死的字面量。
# 5.0 → 3.0 的定价依据：4.618 度的脚骨偏航落在旧门控下面永远不被尝试；再往下（1~2 度）
# 就是拿测量噪声当缺陷去转 —— 回滚阈值才 0.5 度，转了也判不出改善。
DIR_TOL_DEG = 3.0

# 大腿扭转容差（度）。只有 mmd2maxLegRoll 消费；和 DIR_TOL 分开设，因为膝盖绕髋→踝轴的
# 方位噪声比骨盒朝向小得多（3 度在膝上等于 1.9cm 横向位移，用户一眼看得见）。
# 同时是「整段不转腿」的回滚开关：把它改成 999 ⇒ 每根都在容差内被跳过。
ROLL_TOL_DEG = 1.0

# 脚趾落位容差（厘米）：Toe0 的 pivot 到 PMX つま先 的距离。写完立刻复测，没靠近 0.01cm
# 就停手 —— 这条同时是门控（已在容差内就不动），所以写 999 等于整段复摆跳过。
TOE_TOL_CM = 0.5

# 盒隙容差（厘米）：父骨盒远端面到子关节沿长度轴的间距，只当判据用，不改变任何写法。
# 0.5cm 是「鞋尖骨盒有没有接上」的下限 —— #41 用户判据是「像框一样框住脚」，接不上就是牙签。
GAP_TOL_CM = 0.5

# 脚链骨盒远端拉到子关节：false ⇒ 整段跳过，逐字退回第十六轮那根牙签（空 3.29cm）。
FOOT_REACH = True

# ── 蒙皮回读（M4b）的两条容差 ────────────────────────────────────────────────
# 这两条**不进 TARGETS、不进 CFG**：Max 那边拿它们做不了任何门控（写权重时还不知道误差），
# 它们只在 Python 判表时出场。放进 CFG 反而会把「尺」递到被量的一方手里，正是 v4 要避免的。
#
# 单根权重误差 1e-4：weights.tsv 写的是 6 位小数（export_obj.fmt），Max 读成 float 后
# Skin 自己再归一化一次；两次舍入叠加的量级在 1e-6，1e-4 留两个数量级的余量，
# 同时又比「权重写错一根骨」那种故障小得多（错挂一条链路通常差 0.1 以上）。
WEIGHT_ERR_TOL = 1e-4
# 权重和误差 1e-3：Skin 保证归一化，所以这一条量的其实不是「加起来的和」，而是
# **有没有链路被丢掉/坍缩**（M4f C2 实测 ≲1e-8 的权重会被存成 0.0，和照样是 1.0；
# 而 M4e 的二次写坍缩会把和打到 0.0）。1e-3 比归一化的浮点噪声大，比半条链路小。
WEIGHT_SUM_TOL = 1e-3

# ── 骨盒回读（M4c）的容差：同样只当尺，不进 CFG ──────────────────────────────
# 为什么是「相对 2% + 0.02cm 下限」而不是一个绝对数：写入侧的闭环
# （`mmd2maxFitBoneExtent`）的退出条件就是 `abs(cur-target) <= 0.02*target`，
# 所以它**保证达到的**精度只有 2%。回读的尺如果比写入的承诺更严（比如统一 0.01cm），
# 每一轮都会红在收敛判据上而不是几何上 —— 那是拿尺子的分辨率当缺陷。
# 2% 同时也没有松到看不见回归：#37 那根撑破鞋面的骨盒是 8.00×6.61 对 3.93×1.53
# （差 2 倍以上），#41 的牙签是盒隙 3.29cm，任何一次「缩完又被谁改回去」都远超 2%。
BOX_TOL_REL = 0.02
# 下限管的是细骨：小指 1.11cm 的 2% 只有 0.022cm，而 MAXScript 把 float 转字符串
# 只给 6 位有效数字（`as string`），再加写入/回读两次舍入 —— floor 兜住噪声地板。
BOX_TOL_MIN_CM = 0.02


def box_tol_cm(target_cm: float) -> float:
    """骨盒回读某一行的允许偏差（厘米）：2% 相对 + 0.02 绝对地板，取大的那个。

    舍到 6 位小数：这个数要原样进 MMD2MAX.VERDICTS 的 tol 列，`0.17319268000000002`
    这种浮点尾巴会让两轮的行比对看起来像判据变了。
    """
    return round(max(BOX_TOL_REL * float(target_cm), BOX_TOL_MIN_CM), 6)

# MAXScript 全局名 = CFG 键名（M1b 约定：一个词两边同名）。值类型跟着一起声明，
# obj_run 落盘与 Max 侧校验都读这张表，省掉「加一个键要改四处」。
#
# 上下限里 9999 不是笔误：DIRTOL / ROLLTOL / TOETOL 三个数**同时充当门控**
# （「已经比它小就别动这根骨」），所以「把它写成 999 = 整段不动」是这三条各自唯一的
# 回滚开关（README 第 6 节）。上限若卡在「合理值」附近（90 度），扳回滚开关时 Max 会在
# 读 CFG 的第一步就抛「超出允许范围」——回滚位等于被删掉。盒隙容差没有这种用法，
# 保持 50 厘米，越界照抛。
TARGETS: dict[str, tuple[float | bool, str, float, float]] = {
    #                      (值,       类型,  校验下限, 校验上限)
    "MMD2MAX_DIRTOL":     (DIR_TOL_DEG,  "deg",  0.0, 9999.0),
    "MMD2MAX_ROLLTOL":    (ROLL_TOL_DEG, "deg",  0.0, 9999.0),
    "MMD2MAX_TOETOL":     (TOE_TOL_CM,   "cm",   0.0, 9999.0),
    "MMD2MAX_GAPTOL":     (GAP_TOL_CM,   "cm",   0.0,   50.0),
    "MMD2MAX_FOOTREACH":  (FOOT_REACH,   "bool", 0.0,    0.0),
}


def cfg_values() -> dict[str, str]:
    """CFG 里的字符串形态：float 写成 `3.0`，bool 写成字面 `true`/`false`。

    写成 Python 的 True/False 会在 Max 侧变成「值不是 true/false」那句抛错，
    而这正是 M1b 立零默认值后要防的第一类失败 —— 所以格式化也只在这一处做。
    """
    out: dict[str, str] = {}
    for key, (value, kind, _, _) in TARGETS.items():
        if kind == "bool":
            out[key] = "true" if value else "false"
        else:
            out[key] = repr(float(value))
    return out

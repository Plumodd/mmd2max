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

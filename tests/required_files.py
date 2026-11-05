"""仓库内证据件的「缺件即红」读取口。

写这条之前，`baselines/` 不在版本控制里，测试对它只能 `skipUnless` —— 那在当时是对的
（新克隆确实没有这些件），代价是「有人删了一件」和「这台机本来就没带基线」在结果里长得
一模一样，而后者还会伪装成前者一直绿下去。

2026-11-05 读数件进了仓库（`baselines/round*`），所以这里把两种情况分开：
**仓库里有的东西读不到 = fail**。真属于外部的输入（Desktop 上的 `.pmx`、金标准
`baselines/香菱_*.tsv`、`test_runs/` 里的活产物）仍然允许 skip，但每一处都要在
`test_skip_ledger` 那张表里挂号 —— 多一条没登记的静默 skip 就红。
"""
from __future__ import annotations

from pathlib import Path


def require(*paths: Path) -> list[Path]:
    """这些件随仓库分发，读不到就是故障，不许读成「这条判据没意见」。"""
    missing = [str(p) for p in paths if not p.is_file()]
    if missing:
        raise AssertionError(
            "仓库里的基线读数件不见了：" + "、".join(missing)
            + " —— 它们已进版本控制，读不到只有两种可能：被人删了，或者检出被破坏。"
              "这两种都得响，静默 skip 会把它们都读成「本轮无意见」")
    return list(paths)

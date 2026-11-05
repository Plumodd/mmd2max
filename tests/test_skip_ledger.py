"""静默 skip 的账：谁可以「因为缺东西而不跑」，缺的是什么东西。

这条测试管的不是几何，是**验收层的可见性**。本项目反复栽在同一形状上：判据在某种输入
下不执行，而结果里看不出来（第 39 条的 `box_verify` 口子、本轮刚清掉的 `auto_scale`
那条永不执行的断言 —— 它每轮都 skip，看起来像「这条被看着」）。把 skip 收进一张表之后，
新增一条静默 skip 必须**同时**改这张表，改表就得写下「凭什么它可以不跑」。

口径（2026-11-05 定）：
  · 仓库里的件（`baselines/round*`、`scripts/*.ms`、`mmd2max/`）**不许**当 skip 的条件 ——
    读不到就是被删了或检出坏了，走 `tests/required_files.require()` 报红。
  · 外部资产（Desktop 上的 `.pmx`、`test_runs/` 里的活产物、金标准 `baselines/香菱_*.tsv`）
    允许 skip，但必须挂号。
  · 数据驱动的 skip（这具模型天生没有这一通道）允许，同样挂号。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent

# (文件, 理由里必须出现的字) → 凭什么它可以不跑。新增一条 skip 就得在这里加一行，
# 加这行的人被迫写下理由 —— 这张表就是「谁批的」的记录。
SKIP_LEDGER: dict[tuple[str, str], str] = {
    ("test_export.py", "真实模型不在"):
        "外部资产：Desktop\\香菱\\香菱.pmx，不随仓库分发（金标准 tsv 同一个理由）",
    ("test_metamorphic.py", "真模型不在"):
        "外部资产：同上，蜕变测试要拿真 .pmx 重导一遍才有对撞对象",
    ("test_pmx_axes.py", "缺少真实模型"):
        "外部资产：同上，轴固定化/局部轴有没有数据只有真模型答得了",
    ("test_real_model.py", "该模型没有骨变形"):
        "数据驱动：这具模型本来没有骨变形通道，不是缺件",
    ("test_verdicts.py", "缺少真导出"):
        "外部产物：test_runs/ 里的 limbs.tsv/meta.tsv 要一轮真跑才生成，不进仓库",
}

# 匹配三条语句形态；理由用「整条语句文本里出现登记的字」来认，跨行也算。
SKIP_TOKENS = ("self.skipTest(", "@unittest.skipUnless(", "@unittest.skipIf(")

# 仓库内路径一旦出现在 skip 条件里就是违规（这些件已进版本控制，缺 = 故障 = 该红）。
IN_REPO_PATH = re.compile(r'"baselines"\s*/\s*"round|"baselines/"\s*\+\s*"round|baselines/round')


class SkipLedgerTests(unittest.TestCase):
    def sites(self) -> list[tuple[str, str]]:
        """返回 tests/ 里每一处静默 skip 的 (文件, 语句文本)。"""
        found = []
        for path in sorted(TESTS_DIR.glob("test_*.py")):
            text = path.read_text(encoding="utf-8")
            for token in SKIP_TOKENS:
                for match in re.finditer(re.escape(token), text):
                    # 本文件 SKIP_TOKENS 元组里那三个字面量也是「出现」，但它们被引号包住
                    # （前一个字符是 " 且后一个是 "），是**定义处**不是**站点**；真的 skip
                    # 站点要么以 @ 开头、要么前面是空白，绝不会紧贴一个引号。
                    if text[match.start() - 1] == '"' and text[match.end()] == '"':
                        continue
                    # 从 token 起截到本条语句结束：装饰器一般两行内，skipTest 一行的多。
                    chunk = text[match.start():match.start() + 300]
                    found.append((path.name, chunk.split("\n\n")[0]))
        return found

    def test_every_skip_site_is_registered(self):
        sites = self.sites()
        unregistered = [(name, re.sub(r"\s+", " ", chunk)[:110])
                        for name, chunk in sites
                        if not any(key[0] == name and key[1] in chunk for key in SKIP_LEDGER)]
        self.assertEqual(unregistered, [],
                         "新增了没挂号的静默 skip：先写下「凭什么它可以不跑」再加分支"
                         "（仓库里的件不许当条件，用 tests/required_files.require()）")

    def test_the_ledger_is_not_a_graveyard(self):
        """挂号的每条都必须还在：skip 被删了、理由换了地方，表就得跟着瘦。

        一张只会变长的表没有意义 —— 它会把「当年批过一个口子」一直算成现在还有。
        """
        sites = self.sites()
        stale = [key for key in SKIP_LEDGER
                 if not any(key[0] == name and key[1] in chunk for name, chunk in sites)]
        self.assertEqual(stale, [], "账上有、代码里没有的 skip 口子：删掉这一行登记")

    def test_no_skip_is_keyed_on_a_repo_baseline(self):
        """仓库内的基线读数件**不许**成为 skip 的条件。"""
        offenders = [(name, re.sub(r"\s+", " ", chunk)[:110])
                     for name, chunk in self.sites() if IN_REPO_PATH.search(chunk)]
        self.assertEqual(offenders, [],
                         "这条 skip 拿仓库里的件当条件：那些件已随仓库分发，"
                         "读不到是故障，必须红（见 tests/required_files.py）")

    def test_baselines_readings_are_in_the_repository(self):
        """账里那些「仓库内的件」确实在仓库里 —— 判据的前提是输入存在。

        这一条专门防「.gitignore 又把它们排掉了」：`baselines/round*` 一旦回到 ignored，
        上面那两条闸会因为「本来就没跑」而看不见，整套离线回归就只剩注释。
        """
        import subprocess
        out = subprocess.run(["git", "ls-files", "baselines"],
                             cwd=TESTS_DIR.parent, capture_output=True, text=True)
        self.assertEqual(out.returncode, 0,
                         f"`git ls-files` 没跑起来（{out.stderr.strip()[:80]}）—— "
                         "这条闸的前提是它在这台机上量得到仓库里有什么")
        tracked = [l.strip() for l in out.stdout.splitlines() if l.strip()]
        self.assertGreaterEqual(len(tracked), 100,
                                f"仓库里只剩 {len(tracked)} 份基线读数件（应为 127 份 round*）："
                                "`baselines/*` + `!baselines/round*` 那条负向白名单失效了")
        banned = [p for p in tracked
                  if p.startswith("baselines/香菱_") or p.startswith("baselines/gen17/")
                  or p.startswith("baselines/shots_round")]
        self.assertEqual(banned, [],
                         "金标准 tsv / 上一代基线 / 渲染截图进了仓库：这些是从别人有版权的"
                         "游戏模型导出来的素材，口径里明确留在外面")


if __name__ == "__main__":
    unittest.main()

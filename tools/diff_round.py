"""把两轮 objrun 日志归一后对比，只报**实质**差异。

判「重构有没有动几何」靠的就是这个对比，所以它必须是代码，不能是每次现敲的 sed。
第十九轮核对 M1b 时踩到的两条都写进了规则里：

① **日期与时间都不补零**。同一份日志里时间戳的字符数会变（晚 11 点那轮比下午那轮多一个
   字符），按固定宽度裁时间戳一定裂。
② **别照终端回显选分隔符**。这份日志的日期分隔符字节是 `0x2F`（斜杠），显示出来却像短横线
   （这个环境的回显会改写日期串的分隔符），于是照着写的 `^[0-9]{4}-` 开头**静默不匹配**：
   不报错，只是什么都没剥，整份 413 行日志被读成「全变了」。要确认字节用
   `list(Path(f).read_bytes()[i:i+20])`，别看回显。

所以剥离规则只认「两个不含空格的 token」，与分隔符是斜杠还是短横线、补不补零完全无关。

只剥**负载**，不剥**读数**：时间戳、`X.XXX 秒` 耗时、源码目录名、紧跟「字节」的整数
（`.max` 与截图那一类）。除此之外每一个数字（`累计误差=0.224741`、`转角尝试=45`、
`顶点 18892`）都原样留着比 —— 几何读数本来就只有量，把量剥掉再比等于没有验收。
`--allow` 放行的是**结构性的行增删**（如 M1b 新增的「配置:」行），不是数字变化；名单外
一律 exit 1。这条区分以前只在文案里：`--allow "^蒙皮回读"` 白掉的是**整行**，所以那一族
行里的 `width_cm` 从 3.714 变成 9.999 也算「无实质差异」。现在 `permitted_moves()` 专门
把「放行的行里数字变了」那几处讲出来（默认提示、`--strict` 计入退出码）。

用法：
    python tools/diff_round.py baselines/round18_objrun.log test_runs/香菱/香菱_objrun.log
    python tools/diff_round.py --allow "^配置:" <基线> <本轮>   # 放行预期增删，可重复
    python tools/diff_round.py --allow "^骨盒回读" --strict <基线> <本轮>   # 读数一动就红

退出码 0 = 没有实质差异（只有被 --allow 认掉的行）；1 = 有实质差异（`--strict` 时含
「放行的行里数字变了」）；2 = 用法或文件错。
"""
from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path

# 时间戳：必须**认得出是日期+时间**才剥。早先写成 `^\S+ \S+\s+`，于是
# `香菱.max 191111168 字节` 这种不以时间戳开头的行也被当时间戳吃掉行头（信息丢失后
# 两行可能塌成同一个，反而漏判）。分隔符用 `[^0-9]`、月/日/时不补零都兼容，见文件头 ① ②。
TIMESTAMP = re.compile(r"^\d{4}[^0-9]\d{1,2}[^0-9]\d{1,2}\s+\d{1,2}:\d{2}:\d{2}\s*")
# 耗时是机器负载，不是几何读数：`导入耗时：1.118 秒` / `保存耗时：0.656 秒`。
SECONDS = re.compile(r"\d+(?:\.\d+)?\s*秒")
# 源码目录换了代（v3 → v4）不代表几何变了；日志里满是这两类路径。目录名是补零的短横线
# 格式（`mmd2max_v4_src_2026-11-03`），与日志时间戳不是一回事，这里可以写死。
PROJECT_DIR = re.compile(r"mmd2max_v\d+_src_[0-9-]+")
# 截图与 .max 的字节数随压缩抖动，只有「落没落盘」才是判据。**只遮字节数这一种语境**：
# 前后都不许挨着数字或小数点（`累计误差=0.224741` 的小数部分有 6 位，早先
# `(?<!)\b\d{6,}\b` 把它一起遮掉，正好遮掉了「证明重构没动几何」所依赖的那个读数），
# 而且**后面必须真的跟着「字节」**。
# 原来那条「任何 6 位以上整数」在 35 份冻结基线日志里命中 **0 次** —— 它今天什么也没遮，
# 却是一颗地雷：换一具密一点的模型，`已写入顶点 1000000`、`links_read 267420` 这类
# **读数**就会被当成字节数白掉，工具从此永远报「没变化」。
BYTE_SIZE = re.compile(r"(?<![\d.])\d{6,}(?=\s*字节)")


def normalize(line: str) -> str:
    """把一行日志压成「与时间和路径无关」的形态。"""
    line = TIMESTAMP.sub("", line, count=1)
    line = SECONDS.sub("T", line)
    line = PROJECT_DIR.sub("<PROJ>", line)
    line = BYTE_SIZE.sub("<N>", line)
    return line.rstrip()


def normalized_lines(text: str) -> list[str]:
    """逐行归一，丢掉归一后为空的行（多行报错的续行本身没有信息）。"""
    return [out for raw in text.splitlines() if (out := normalize(raw))]


def normalized(log: Path) -> list[str]:
    """读文件必须用 `utf-8-sig`：这份日志第 1 行带 BOM，用 `utf-8` 读会把 `\\ufeff`
    粘在表头上，那行就再也匹配不上任何前缀规则。"""
    return normalized_lines(log.read_text(encoding="utf-8-sig", errors="replace"))


def substantive(before: list[str], after: list[str],
                allows: list[re.Pattern]) -> list[str]:
    """返回「不能被 --allow 解释掉」的差异行，带 +/- 前缀。

    用 difflib 而不是整份文件 diff：重构加的是一行、删的可能是三行，需要按行对齐才能
    判断每一处增删是不是都落在允许名单里。`? ` 行是 ndiff 附带的字符级提示，不承载内容，
    计进来会让「增删都被 --allow 认掉」的一对被误判成实质差异。
    """
    problems: list[str] = []
    for tag in difflib.ndiff(before, after):
        if not (tag.startswith("+ ") or tag.startswith("- ")):
            continue
        line = tag[2:]
        if any(p.search(line) for p in allows):
            continue
        problems.append(tag[:1] + " " + line)
    return problems


def skeleton(line: str) -> str:
    """把行里所有数字换成 `#` —— 用来判断两行是不是「同一行换了值」而不是「一行换成了另一行」。"""
    return re.sub(r"\d+(?:\.\d+)?", "#", line)


def numbers(line: str) -> tuple[str, ...]:
    return tuple(re.findall(r"\d+(?:\.\d+)?", line))


def permitted_moves(before: list[str], after: list[str],
                    allows: list[re.Pattern]) -> list[tuple[str, str]]:
    """被 `--allow` 放掉的行里，**数字有没有变**。返回 (旧行, 新行) 清单。

    `--allow "^蒙皮回读"` 的语义本来只是「这一族行是预期新增的」，但它把整行白掉了 ——
    于是 `width_cm 3.714245 → 9.999999` 也算「无实质差异」。README 里 M4 那几轮
    「几何一个字节没动」的结论，有一部分是靠这个口子说出来的：它证的是**行数**没动，
    不是**读数**没动。这里把两者分开说，不改 `substantive` 的既有语义（那套用法已经
    在 35 份基线上跑过），只把漏的部分讲出来。

    配对很保守：只有非数字部分逐字符相同的两行才认作「同一行换了值」。认错了会把一次
    删除说成一次数字漂移，那比不报更坏。
    """
    moved: list[tuple[str, str]] = []
    pending: list[str] = []
    for tag in difflib.ndiff(before, after):
        if tag.startswith("? "):
            continue
        if tag.startswith("- "):
            pending.append(tag[2:])
            continue
        if tag.startswith("+ "):
            new = tag[2:]
            if not any(p.search(new) for p in allows):
                continue
            for index, old in enumerate(pending):
                if skeleton(old) == skeleton(new) and numbers(old) != numbers(new):
                    moved.append((old, new))
                    del pending[index]
                    break
            continue
        pending = []      # 中间出现未变行：上下文断了，剩下的删除行不再参与配对
    return moved


def main() -> int:
    parser = argparse.ArgumentParser(description="归一对比两轮 objrun 日志")
    parser.add_argument("baseline", type=Path)
    parser.add_argument("current", type=Path)
    parser.add_argument("--allow", action="append", default=[],
                        metavar="正则", help="放行预期的增/删行，可重复")
    parser.add_argument("--strict", action="store_true",
                        help="被 --allow 放行的行里数字一旦变了也算实质差异（默认只提示）")
    args = parser.parse_args()
    for path in (args.baseline, args.current):
        if not path.is_file():
            print(f"日志不存在：{path}", file=sys.stderr)
            return 2
    try:
        allows = [re.compile(p) for p in args.allow]
    except re.error as error:
        print(f"--allow 正则不合法：{error}", file=sys.stderr)
        return 2

    before, after = normalized(args.baseline), normalized(args.current)
    problems = substantive(before, after, allows)
    moves = permitted_moves(before, after, allows)
    print(f"归一后行数：基线 {len(before)} 本轮 {len(after)}")
    if moves:
        print(f"放行的行里有 {len(moves)} 处数字变了"
              f"{'（--strict：算实质差异）' if args.strict else '（默认只提示，不影响退出码）'}：")
        for old, new in moves[:20]:
            print(f"  - {old}")
            print(f"  + {new}")
        if len(moves) > 20:
            print(f"  …另有 {len(moves) - 20} 处")
    if args.strict:
        problems += [f"放行的行数字变了：{old} → {new}" for old, new in moves]
    if not problems:
        if moves:
            print(f"行数与文字没变，但放行的行里换了 {len(moves)} 个数字 —— "
                  "「无实质差异」这句话现在是有水分的。")
        else:
            print("无实质差异：本轮相对基线只动了时间、耗时与路径。")
        return 0
    print(f"实质差异 {len(problems)} 行：")
    for line in problems[:80]:
        print("  " + line)
    if len(problems) > 80:
        print(f"  …另有 {len(problems) - 80} 行")
    return 1


if __name__ == "__main__":
    sys.exit(main())

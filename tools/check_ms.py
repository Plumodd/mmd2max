"""MAXScript 静态体检：保留字 / 括号平衡 / 字符串 / 常见换行陷阱。

为什么需要这个工具：MAXScript 的报错只在「编译到那一行」时才弹，
前面的问题会把后面的全挡住 —— 我们已经在这上面栽过两次：
  - `local off = ...`  → Syntax error: at off, expected name
  - `local ok  = ...`  → Syntax error: at ok, expected name
  - `local mapped = 0` → Syntax error: at mapped, expected name
每次都是生成脚本 → 用户在 Max 里跑 → 弹窗 → 才能发现。这个脚本在生成前就拦下来。

v3 起本文件是唯一的静态体检工具（v2 的 tools/check_ms_syntax.py 已合并进来并删除），
除上面的规则外还查这些实跑踩过的坑：
  - `if <cond> do ( ... ) else ...`：if...do 不允许接 else，必须写 if...then；
  - `localTime()`：localTime 是全局变量不是函数，加括号报 Type error；
  - 行首以 `+ - * /` 开头的续行：MAXScript 换行即语句结束，报 expected <factor>；
  - 顶格 `fn` 必须处在括号深度 0：括号总数配平不代表嵌套正确；
  - **前向引用**：函数体里调用了定义在它后面的顶层函数，运行期报
    「Call needs function or class, got: undefined」（解析期绑定，实跑炸在截图段）；
  - `f(a, b)` 逗号实参表：MAXScript 没有这种语法，编译期就炸
    （-- Syntax error: at ,, expected <factor>）；
  - 裸调 `max a b` / `min a b`：会被当成 MAX 命令解析，同样整份脚本编译不过；
  - 顶层 `fn` 参数表里的 `[name:value]`：MAXScript 的可选参数写法，编译期就炸
    （13:06 那轮 `[skipDir:false]` → `Syntax error: at [, expected =`，日志一行没写）。

用法：python tools/check_ms.py [file...]
     默认检查 scripts/ 下的全部 .ms
退出码：0 = 通过；1 = 有问题（obj_run 会据此拒绝生成）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# MAXScript 保留字（与变量命名冲突的那些；不追求完整，覆盖实际踩过的坑）
RESERVED = {
    "on", "off", "in", "of", "to", "by", "do", "then", "else", "if", "for",
    "while", "break", "continue", "return", "exit", "with", "try", "catch",
    "throw", "fn", "function", "local", "global", "where", "when", "and", "or",
    "not", "true", "false", "undefined", "ok", "mapped", "build", "at", "time",
    "coordsys", "about", "animate", "redraw", "undo", "print", "format", "case",
    "default", "struct", "macroScript", "utility", "rollout", "plugin", "tool",
    "rcmenu", "silent", "quiet", "none", "all", "as", "of",
}


def strip_comments(src: str) -> str:
    """把注释替换成等长空白，保持行号不变。"""
    src = re.sub(r"/\*.*?\*/", lambda m: " " * len(m.group(0)), src, flags=re.S)
    src = re.sub(r"--[^\n]*", lambda m: " " * len(m.group(0)), src)
    return src


def check_reserved(src: str) -> list[str]:
    problems = []
    clean = strip_comments(src)
    for m in re.finditer(r"\blocal\s+([A-Za-z_]\w*)", clean):
        name = m.group(1)
        if name in RESERVED:
            line = clean[:m.start()].count("\n") + 1
            problems.append(f"行 {line}: `local {name}` —— `{name}` 是 MAXScript 保留字")
    for m in re.finditer(r"\bfn\s+\w+\s+([^=]*?)=", clean):
        for param in m.group(1).split():
            if param in RESERVED:
                line = clean[:m.start()].count("\n") + 1
                problems.append(f"行 {line}: 函数参数 `{param}` 是 MAXScript 保留字")
    return problems


def check_balance(src: str) -> list[str]:
    clean = strip_comments(src)
    pairs = {"(": ")", "[": "]", "{": "}"}
    stack: list[tuple[str, int]] = []
    problems = []
    for ch_index, ch in enumerate(clean):
        if ch in pairs:
            stack.append((ch, clean[:ch_index].count("\n") + 1))
        elif ch in pairs.values():
            if not stack:
                problems.append(f"行 {clean[:ch_index].count(chr(10)) + 1}: 多余的 `{ch}`")
                continue
            opener, line = stack.pop()
            if pairs[opener] != ch:
                problems.append(f"行 {line} 的 `{opener}` 与行 "
                                f"{clean[:ch_index].count(chr(10)) + 1} 的 `{ch}` 不匹配")
    for opener, line in stack:
        problems.append(f"行 {line}: `{opener}` 未闭合")
    return problems


def check_strings(src: str) -> list[str]:
    """字符串里的 `\\` 续行可能把下一行吞掉；这里只查未闭合的引号。"""
    problems = []
    in_str = False
    line = 1
    i = 0
    while i < len(src):
        ch = src[i]
        if ch == "\n":
            if in_str:
                problems.append(f"行 {line}: 字符串没有闭合")
                in_str = False
            line += 1
        elif in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_str = False
        else:
            if src.startswith("--", i):
                j = src.find("\n", i)
                i = len(src) if j == -1 else j
                continue
            if src.startswith("/*", i):
                j = src.find("*/", i + 2)
                i = len(src) if j == -1 else j + 2
                continue
            if ch == '"':
                in_str = True
        i += 1
    if in_str:
        problems.append("文件末尾字符串未闭合")
    return problems


def _strip_lines(src: str) -> list[str]:
    """逐行去掉注释，字符串原样保留，行号与列位置不变（给按行规则用）。"""
    lines = src.splitlines()
    out = []
    in_block = False
    for raw in lines:
        keep = []
        i = 0
        while i < len(raw):
            if in_block:
                end = raw.find("*/", i)
                if end == -1:
                    i = len(raw)
                else:
                    in_block = False
                    i = end + 2
                continue
            if raw.startswith("/*", i):
                in_block = True
                i += 2
                continue
            if raw.startswith("--", i):
                break
            if raw.startswith('"', i):
                j = i + 1
                while j < len(raw):
                    if raw[j] == "\\":
                        j += 2
                        continue
                    if raw[j] == '"':
                        j += 1
                        break
                    j += 1
                keep.append(raw[i:j])
                i = j
                continue
            keep.append(raw[i])
            i += 1
        out.append("".join(keep))
    return out


def _blank_strings(lines: list[str]) -> list[str]:
    """把字符串**内容**替换成等长空格（引号本身保留），位置不变。

    给「按括号/逗号形状匹配」的规则用：`format "%,%"` 里的逗号不是实参分隔符。
    """
    out = []
    for raw in lines:
        keep = []
        i = 0
        while i < len(raw):
            ch = raw[i]
            if ch == '"':
                keep.append('"')
                i += 1
                while i < len(raw) and raw[i] != '"':
                    if raw[i] == "\\" and i + 1 < len(raw):
                        keep.append("  ")
                        i += 2
                        continue
                    keep.append(" ")
                    i += 1
                if i < len(raw):
                    keep.append('"')
                    i += 1
                continue
            keep.append(ch)
            i += 1
        out.append("".join(keep))
    return out


def _paren_delta(code: str) -> int:
    """一行里圆括号的净增量，忽略字符串内的括号。"""
    delta = 0
    in_str = False
    for ch in code:
        if ch == '"':
            in_str = not in_str
        elif not in_str:
            if ch == "(":
                delta += 1
            elif ch == ")":
                delta -= 1
    return delta


def check_if_do_else(src: str) -> list[str]:
    """if...do 没有 else 分支：要写 else 必须用 if...then。本项目真实踩过这个坑。"""
    problems = []
    lines = _strip_lines(src)
    for index, code in enumerate(lines, start=1):
        if re.search(r"\bif\b.*\bdo\b.*\belse\b", code):
            problems.append(f"行 {index}: if...do 不允许接 else，请把 do 改成 then。")
    pending = []  # 每项 [记录行号, 记录时的括号深度, 是否已进入其代码块]
    depth = 0
    for index, code in enumerate(lines, start=1):
        stripped = code.strip()
        if re.search(r"\bif\b.*\bdo\b\s*\(?\s*$", stripped):
            pending.append([index, depth, False])
        depth += _paren_delta(code)
        for item in list(pending):
            start_line, start_depth, entered = item
            if depth > start_depth:
                item[2] = True
                continue
            if not entered:
                continue
            following = ""
            for nxt in lines[index:]:
                if nxt.strip():
                    following = nxt.strip()
                    break
            if following.startswith("else"):
                problems.append(
                    f"行 {start_line}: if...do 不允许接 else（下一非空行是 else），"
                    f"请把 do 改成 then。")
            pending.remove(item)
    return problems


def check_local_time(src: str) -> list[str]:
    lines = _strip_lines(src)
    problems = []
    for index, code in enumerate(lines, start=1):
        if re.search(r"\blocalTime\s*\(\s*\)", code):
            problems.append(
                f"行 {index}: localTime 是系统全局变量而不是函数，"
                f"写 localTime() 会报「Call needs function or class」，应直接用 localTime。")
    return problems


def check_continuation(src: str) -> list[str]:
    """行首以二元运算符开头的续行：MAXScript 换行即语句结束，会报 expected <factor>。"""
    problems = []
    lines = _strip_lines(src)
    for index in range(1, len(lines)):
        prev = lines[index - 1].strip()
        cur = lines[index].strip()
        if not prev or not cur:
            continue
        if cur[0] in "+-*/" and not prev.endswith("\\"):
            problems.append(
                f"行 {index + 1}: 行首以 '{cur[0]}' 开头的续行在 MAXScript 中不合法；"
                f"请在上一行末尾加 \\ 续行，或改用局部变量分步拼接。")
    return problems


def check_fn_depth(src: str) -> list[str]:
    """顶格写的 `fn` 必须处在括号深度 0。

    为什么需要这条规则：`fn` 一旦被卷进别的函数体，就成了局部函数，顶层调不到，
    报「Call needs function or class, got: undefined」。括号总数平衡
    （check_balance 过了）不代表嵌套正确：前面少一个 `)`、后面多一个 `)` 也能配平。

    注意：同样的报错文字还有第二个成因 —— 前向引用（见 check_forward_reference）。
    截图段那轮报错的真正原因是后者，不是深度；两条规则都要留着才能各拦住一半。
    """
    problems = []
    depth = 0
    for index, code in enumerate(_strip_lines(src), start=1):
        if code.startswith("fn ") or code.startswith("function "):
            if depth != 0:
                head = code.split("=")[0].strip()[:60]
                problems.append(
                    f"行 {index}: 顶格的 `{head}` 处在括号深度 {depth}，"
                    f"会被定义成局部函数、顶层调用不到（报 Call needs function or class）。")
        depth += _paren_delta(code)
    if depth != 0:
        problems.append(f"文件结束时括号深度为 {depth}，应为 0。")
    return problems


EMPTY_FN_RE = re.compile(r"\bfn\s+\w*\s*\(\s*\)")
CHAINED_CALL_PROP_RE = re.compile(r"\.\s*\w+\s*\(\s*\)\s*\.\s*[A-Za-z_]")


def check_empty_fn_params(src: str) -> list[str]:
    """`fn name () = ...` / `fn () = ...`：MAXScript 的零参函数**不写**空参数表。

    实跑证据（Max 2022，-U MAXScript 载入探针 viewport_probe2.ms）：
        p2Run "10_x" ( fn () ( ... ) )
        -- Syntax error: at (), expected name   （第 78 行）
    弹窗标题只有「MAXScript Auto-load Script Error Exception」，整份脚本一行不跑。
    零参函数写成 `fn name = ( ... )`，调用时裸写名字或再套一层分组括号。
    """
    problems = []
    for index, code in enumerate(_blank_strings(_strip_lines(src)), start=1):
        m = EMPTY_FN_RE.search(code)
        if m:
            problems.append(
                f"行 {index}: `{m.group(0).strip()}` 用了空参数表 —— MAXScript 的零参函数"
                f"不写 ()（Syntax error: at (), expected name，整份脚本编译不过）；"
                f"改成 fn name = ( ... )。")
    return problems


def check_chained_call_property(src: str) -> list[str]:
    """`a.getB().c`：点号不能直接接在调用后面，MAXScript 解析不了这个链。

    实跑证据（Max 2022，探针 viewport_probe3.ms 第 35 行）：
        try ( r = viewport.getTM().translation as string ) catch ()
        -- Syntax error: at ., expected <factor>
    同一份脚本其余部分全没跑，日志一个字没写 —— 和 check_bare_min_max 一样是编译期错误。
    修法：先落一个 local（`local tm = viewport.getTM()`）再点属性，
    或把调用整体括起来 `(viewport.getTM()).translation`。
    """
    problems = []
    for index, code in enumerate(_blank_strings(_strip_lines(src)), start=1):
        m = CHAINED_CALL_PROP_RE.search(code)
        if m:
            problems.append(
                f"行 {index}: `{m.group(0).strip()}` 把点号直接接在调用后面 —— MAXScript 报"
                f"「Syntax error: at ., expected <factor>」，整份脚本编译不过；"
                f"先 `local x = 调用()`，再取 x.属性。")
    return problems


def check_bare_min_max(src: str) -> list[str]:
    """裸调 `max a b` / `min a b`：MAXScript 会把它当成 MAX 命令解析。

    实跑证据（Max 2022，fileIn 一份 2500 行装配脚本）：
        hi = [ max hi.x p.x, ... ]
        -- Compile error: Unrecognized MAX command:  hi
    而且这是**编译期**错误 —— 整份脚本一行都不会执行，弹窗标题只有
    「MAXScript Auto-load Script Error Exception」，读不到内容。
    改用逐分量 if 比较（或 amax/amin 数组版）。
    """
    problems = []
    for index, code in enumerate(_blank_strings(_strip_lines(src)), start=1):
        for m in re.finditer(r"(?<![.\w])(max|min)\s+[A-Za-z0-9_(\[\"-]", code):
            problems.append(
                f"行 {index}: 裸调 `{m.group(1)} <args>` 会被 MAXScript 当成 MAX 命令"
                f"（Unrecognized MAX command），整份脚本编译不过；改用 if 比较。")
    return problems


COMMA_CALL_RE = re.compile(r"\b(mmd2max\w*)\s*\(([^()]*,[^()]*)\)")


def check_comma_call(src: str) -> list[str]:
    """`f(a, b)` 这种逗号实参表在 MAXScript 里根本不存在。

    实测（Max 2022，直跑探针）：
        try ( pp4(1, 2, 3, 4) )
        -- Syntax error: at ,, expected <factor>
    而且是**编译期**错误：整份脚本一行都不会执行。MAXScript 的调用只有
    「空格分隔」形式 `f a b c`；`f(x)` 之所以合法，是因为那对括号只是分组表达式，
    它永远只带一个实参。只查我们自己 `mmd2max*` 前缀的函数，避免误伤内置写法。
    """
    problems = []
    for index, code in enumerate(_blank_strings(_strip_lines(src)), start=1):
        for m in COMMA_CALL_RE.finditer(code):
            problems.append(
                f"行 {index}: `{m.group(1)}(...)` 用了逗号实参表 —— MAXScript 没有这种语法"
                f"（Syntax error: at ,, expected <factor>，整份脚本编译不过）；"
                f"改成空格分隔的 {m.group(1)} {' '.join(a.strip() for a in m.group(2).split(','))}。")
    return problems


REFERENCE_RE = re.compile(r"(?<![.\w])(mmd2max\w*)\b")


def check_forward_reference(src: str) -> list[str]:
    """函数体里不许引用「定义在它后面」的顶层函数（前向引用）。

    为什么这是硬错误：MAXScript 的调用是**解析期**绑定的。解析器读到 `f a b`
    这种空格裸调时，如果那一刻 `f` 还没被求值成函数值，它就只能把 `f` 当成一个
    普通全局变量、把后面那串当成实参，运行期于是拿 undefined 去 apply ——
    `-- Type error: Call needs function or class, got: undefined`。
    实跑证据（Max 2022，直跑探针 + 19:45 香菱装配日志）：
      mmd2maxSaveDib / mmd2maxShotOne / mmd2maxScreenshot 定义在 mmd2maxLog 之前，
      函数体第一行 `mmd2maxLog ("截图开始 " + ...)` 就炸在这里：日志里
      「开始抓取线框视口截图」之后一行截图日志都没有，而探针实测
      `classof mmd2maxLog = MAXScriptFunction`（值早就在，只是解析期看不见）。
      同一个 mmd2maxLog 排在它之后的几百个函数调用则完全正常。
    修法：把被调用的函数挪到调用者前面（本项目把日志三兄弟提到了文件开头）。

    判定按「引用的行号 < 定义的行号」逐行扫，函数体内和顶层语句都覆盖；
    注释与字符串已由 _strip_lines / _blank_strings 去掉，所以提到名字不算引用。
    """
    lines = _blank_strings(_strip_lines(src))
    depth = 0
    defined_at: dict[str, int] = {}       # 函数名 -> 定义行号（1-based，只看深度 0 的顶格 fn）
    for index, code in enumerate(lines, start=1):
        if depth == 0:
            m = re.match(r"(?:fn|function)\s+(\w+)", code)
            if m:
                defined_at.setdefault(m.group(1), index)
        depth += _paren_delta(code)

    problems = []
    for index, code in enumerate(lines, start=1):
        for m in REFERENCE_RE.finditer(code):
            name = m.group(1)
            line = defined_at.get(name)
            if line is not None and line > index:
                problems.append(
                    f"行 {index} 引用了行 {line} 才定义的 `{name}`：MAXScript 在解析期"
                    f"绑定裸调，运行期会报「Call needs function or class, got: undefined」；"
                    f"把 `{name}` 的定义提到前面。")
    return problems


def check_optional_params(src: str) -> list[str]:
    """顶层 `fn` 的参数表里不许出现 `[...]`（MAXScript 的可选参数写法）。

    实跑证据（Max 2022，13:06 那轮，-U MAXScript 载入 2900 行装配脚本）：
        fn mmd2maxPlaceBipedNodes root lines rowIds targetNames passList [skipDir:false] =
        -- Syntax error: at [, expected =      （弹窗标题：Auto-load Script Error Exception）
    解析器把 `false` 读成**类型名**、然后等 `=`，于是整份脚本一行不跑、日志一个字没写
    —— 和上面几条一样是编译期错误，只能靠静态拦。带类型的 `[x:boolean=false]` 按文档
    应当合法，但这条写法本项目从没验过，不值得再烧一轮实跑去试：
    要开关就加一个**位置参数**，调用点各写 true/false。
    """
    problems = []
    depth = 0
    for index, code in enumerate(_blank_strings(_strip_lines(src)), start=1):
        if depth == 0 and (code.startswith("fn ") or code.startswith("function ")):
            m = re.search(r"\[[^\]]*\]", code)
            if m:
                problems.append(
                    f"行 {index}: 函数参数表里写了可选参数 `{m.group(0)}` —— "
                    f"MAXScript 报「Syntax error: at [, expected =」，整份脚本编译不过"
                    f"（13:06 那轮就是这么烧掉的）；改成位置参数，调用点显式传值。")
        depth += _paren_delta(code)
    return problems


def check_source(src: str) -> list[str]:
    """全部规则跑一遍，返回问题列表（obj_run 生成脚本后直接调它）。"""
    return (check_reserved(src) + check_balance(src) + check_strings(src)
            + check_if_do_else(src) + check_local_time(src)
            + check_continuation(src) + check_fn_depth(src)
            + check_empty_fn_params(src) + check_chained_call_property(src)
            + check_bare_min_max(src) + check_comma_call(src)
            + check_optional_params(src) + check_forward_reference(src))


def check_file(path: Path) -> list[str]:
    if not path.is_file():
        return [f"文件不存在：{path}"]
    return check_source(path.read_text(encoding="utf-8"))


def main(argv: list[str]) -> int:
    targets = [Path(a) for a in argv[1:]] or sorted((ROOT / "scripts").glob("*.ms"))
    total = 0
    for path in targets:
        problems = check_file(path)
        label = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
        if problems:
            total += len(problems)
            print(f"[FAIL] {label}：{len(problems)} 个问题")
            for item in problems[:15]:
                print("   ", item)
        else:
            print(f"[OK]   {label}")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

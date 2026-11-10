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

# MAXScript 保留字（与变量命名冲突的那些；不追求完整，覆盖实际踩过的坑。
# 新增只认实测：`shape` 是 M4 探针第一次跑被「Cannot assign to read-only variable: shape」
# 打死之后加进来的，`snapshot` 是 M4g 探针同一句死法（probes/figfile_m4g.txt 第一行
# 「Cannot assign to read-only variable: snapshot」）—— 不是照抄文档，
# 抄一份来路不明的名单只会让人以为查得全。
RESERVED = {
    "on", "off", "in", "of", "to", "by", "do", "then", "else", "if", "for",
    "while", "break", "continue", "return", "exit", "with", "try", "catch",
    "throw", "fn", "function", "local", "global", "where", "when", "and", "or",
    "not", "true", "false", "undefined", "ok", "mapped", "build", "at", "time",
    "coordsys", "about", "animate", "redraw", "undo", "print", "format", "case",
    "default", "struct", "macroScript", "utility", "rollout", "plugin", "tool",
    "rcmenu", "silent", "quiet", "none", "all", "as", "of", "shape", "snapshot",
    # `box` 是 M4g v2 实测：`local box = try ( Box name:"M4GBox" ... )` 里的 `Box`
    # 被解析成**正在定义的那个 local**（此刻还没值）→「Call needs function or class,
    # got: undefined」，而且不中止、只让 Box=false。改名 wbox 之后同一句就建出来了
    # （probes/figfile_m4g.txt：Box=true 修改器=1）。这一类比 shape 更阴：不报错，只给假读数。
    "box",
}


def _blank_out(match: "re.Match") -> str:
    """注释整段变空白，但**换行必须留着**：早先用 `" " * len(...)` 把块注释里的
    换行也吃成空格，于是报出来的行号从第一处块注释起一路往前偏 —— 实测真实第 720
    行报成「行 553」。这个工具的用处就是让人照着行号去找代码，号不对等于没有。"""
    return "".join(ch if ch == "\n" else " " for ch in match.group(0))


def strip_comments(src: str) -> str:
    """把注释替换成等长空白，保持行号不变。"""
    src = re.sub(r"/\*.*?\*/", _blank_out, src, flags=re.S)
    src = re.sub(r"--[^\n]*", _blank_out, src)
    return src


def check_reserved(src: str) -> list[str]:
    """保留字撞名检查：`local` / `global` / `fn 名` / 函数参数，四种声明形式全覆盖。

    为什么 `fn` 的名字也要查：MAXScript 的 `fn name params = body` 编译成「给 name 赋值」，
    而保留字是**只读**的，所以 `fn shape x = ...` 不在编译期报语法错，而是在执行到那一行时抛
    「Cannot assign to read-only variable: shape」—— 整份脚本中止，而且 MMD2MAX.REPORT 里
    一行读数都还没有。这一条是 M4 探针（probes/readback_m4.txt）第一次跑就是这么死的：
    工具原先只看 `local`，`fn shape` 直接从它眼皮底下过去了。
    保留字不区分大小写：Max 的内建类叫 `Shape`，写 `shape` 一样撞。
    """
    problems = []
    clean = strip_comments(src)

    def report(name: str, form: str, position: int) -> None:
        if name.lower() not in RESERVED:
            return
        line = clean[:position].count("\n") + 1
        problems.append(f"行 {line}: `{form} {name}` —— `{name}` 是 MAXScript 保留字"
                        f"（保留字不区分大小写，且执行到那一行才抛，编译期看不出来）")

    for pattern, form in ((r"\blocal\s+([A-Za-z_]\w*)", "local"),
                          (r"\bglobal\s+([A-Za-z_]\w*)", "global"),
                          (r"\bfn\s+([A-Za-z_]\w*)", "fn")):
        for m in re.finditer(pattern, clean):
            report(m.group(1), form, m.start())
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


IF_HEAD_DO = re.compile(r"\bif\b.*\bdo\b\s*\(?\s*$")
# 整条 if...do 连体都写完在同一行的形状（do 后面还有内容）。行尾版（上面那条）看不到它。
IF_DO_INLINE = re.compile(r"\bif\b.*\bdo\b\s+\S")


def _next_code(lines, start):
    """start（0-based）之后第一条非空行：返回 (内容, 下标)；到末尾返回 ("", len)。"""
    i = start
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines):
        return "", i
    return lines[i].strip(), i


def _block_end(depths, open_at, start_depth):
    """括号块从 open_at 行开始，返回** closes 它的那一行**下标（找不到就到文件尾）。"""
    for j in range(open_at + 1, len(depths)):
        if depths[j] <= start_depth:
            return j - 1
    return len(depths) - 1


def check_if_do_else(src: str) -> list[str]:
    """if...do 不许接 else（要接必须写 if...then）——本项目真踩过这个 Syntax error。

    判断「这个 if...do 的块体到哪结束」：
      - if 行以 `(` 结尾，或紧跟的非空行以 `(` 开头 → 是括号块，跟着括号深度回到
        if 行所在深度的那一行就是块尾；
      - 否则块体就是紧跟的那**一条语句**（行尾 `\\` 续行要一起吃掉）。
    然后只看块尾之后的第一条非空行是不是 `else`。

    为什么重写：以前是一条全局 pending 队列，靠「深度变深」认定进入了块体。
    单语句体不进括号，那些项永远等不到变深，于是漏到几百行之外的第一个 else 上 ——
    把 `if (mod i 10000) == 0.0 do` 这种**已经实跑过 23 轮**的行报成缺陷。
    假阳性的代价不是多改一行，是没人再信这个检查器（M2b 文档里写的就是这件事）。
    """
    problems = []
    lines = _strip_lines(src)
    depths = []
    depth = 0
    for code in lines:
        depths.append(depth)
        depth += _paren_delta(code)

    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if re.search(r"\bif\b.*\bdo\b.*\belse\b", stripped):
            problems.append(f"行 {index + 1}: if...do 不允许接 else，请把 do 改成 then。")
        if not IF_HEAD_DO.search(stripped):
            if not IF_DO_INLINE.search(stripped) or _paren_delta(stripped) != 0 \
                    or stripped.endswith("\\"):
                index += 1
                continue
            # 整条 if...do 在这一行就写完了（体也在同一行、括号配平）：MAXScript 的
            # 块体到此结束，下一行若是 else 就是「do 接 else」那个 Syntax error。
            # 这一支是补的漏：探针 figfile_m4h 第一发就是死在这个形状上
            # （`if n == undefined do ( say (...) )` 换行 `else`），而当时的规则
            # 只认行尾的 do（IF_HEAD_DO），整行写完的形状一律不进判断。
            after, _ = _next_code(lines, index + 1)
            if after.startswith("else"):
                problems.append(
                    f"行 {index + 1}: if...do 在这一行已经写完，下一行的 else 接不上"
                    f"（MAXScript 要 if...then），请把 do 改成 then。")
            index += 1
            continue
        start_depth = depths[index]
        if stripped.endswith("("):
            end = _block_end(depths, index, start_depth)
        else:
            following, next_at = _next_code(lines, index + 1)
            if following.startswith("("):
                end = _block_end(depths, next_at, start_depth)
            else:
                end = next_at
                while end < len(lines) and lines[end].strip().endswith("\\"):
                    end += 1
        after, _ = _next_code(lines, end + 1)
        if after.startswith("else"):
            problems.append(
                f"行 {index + 1}: if...do 不允许接 else（块体结束后的第一条非空行是 "
                f"else），请把 do 改成 then。")
        index += 1
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
    """顶层 `fn` 的参数表里不许出现 `[...]` 或 `/name:`（MAXScript 的两种可选参数写法）。

    实跑证据（Max 2022，13:06 那轮，-U MAXScript 载入 2900 行装配脚本）：
        fn mmd2maxPlaceBipedNodes root lines rowIds targetNames passList [skipDir:false] =
        -- Syntax error: at [, expected =      （弹窗标题：Auto-load Script Error Exception）
    解析器把 `false` 读成**类型名**、然后等 `=`，于是整份脚本一行不跑、日志一个字没写
    —— 和上面几条一样是编译期错误，只能靠静态拦。带类型的 `[x:boolean=false]` 按文档
    应当合法，但这条写法本项目从没验过，不值得再烧一轮实跑去试：
    要开关就加一个**位置参数**，调用点各写 true/false。

    斜杠写法是同一类错误的第二个拼法（第 34 轮实跑，解析探针 probe_parse.txt）：
        fn mmd2maxShotOne outDir baseName tag viewKind lo hi /tight:false =
        -- Syntax error: at /, expected =
    代价和 13:06 那轮一模一样：Auto-load 弹窗、日志与 error.txt 全空、整轮白跑。
    文档里那个写法在 Max 2022 的解析器上就是不认，所以这里一并拦掉 —— 顺带说明
    **调用点**的 `fileName:shotFile` / `pattern:"*"`（关键字实参，没有斜杠）是另一回事，
    那些是本仓库实测跑通过的写法，不在本规则的射程里。
    """
    problems = []
    depth = 0
    for index, code in enumerate(_blank_strings(_strip_lines(src)), start=1):
        if depth == 0 and (code.startswith("fn ") or code.startswith("function ")):
            # 只看参数表：`=` 之后的 `[...]` 是数组下标，不是可选参数。
            # （`fn mmd2maxRows tbl = tbl[3]` 曾被这条误判成 Syntax error。）
            head = code.split("=", 1)[0]
            m = re.search(r"\[[^\]]*\]", head)
            if m:
                problems.append(
                    f"行 {index}: 函数参数表里写了可选参数 `{m.group(0)}` —— "
                    f"MAXScript 报「Syntax error: at [, expected =」，整份脚本编译不过"
                    f"（13:06 那轮就是这么烧掉的）；改成位置参数，调用点显式传值。")
            slash = re.search(r"/\S*", head)
            if slash:
                problems.append(
                    f"行 {index}: 函数参数表里写了斜杠可选参数 `{slash.group(0)}` —— "
                    f"MAXScript 报「Syntax error: at /, expected =」，整份脚本编译不过"
                    f"（第 34 轮 `/tight:false` 就是这么烧掉的）；改成位置参数，"
                    f"或者为那个开关单开一个函数。")
        depth += _paren_delta(code)
    return problems


CATCH_EXEMPT_MARK = "允许失败"
# `-- 允许失败` 后面必须真的跟一个理由：以前只判「这一行含不含这五个字」，于是
# `catch ( ) -- 允许失败` 也算放行 —— 豁免词本身变成空 catch 的通行证，
# 而 grep 得到的是「这里声明过允许失败」，声明里什么都没有。
CATCH_EXEMPT_RE = re.compile(r"允许失败\s*[:：]\s*\S+")


def _catch_body(code_lines: list[str], index: int, pos: int) -> tuple[str, int]:
    """读出 `catch` 关键字之后的**体**（可以跨行），返回 (体的文本, 体结束的行号)。

    MAXScript 有两种写法：`catch ( ... )` 与 `catch <表达式>`。只查同一种就把跨行写的
    空体全漏了 —— 而跨行恰恰是它长出来的样子：体里那句读数一旦删掉，剩下那对括号
    还在，括号内空无一物，`catch\\s*\\(\\s*\\)` 因为隔了换行就不匹配了。
    """
    line, col = index, pos
    while line < len(code_lines):
        while col < len(code_lines[line]) and code_lines[line][col] in " \t":
            col += 1
        if col < len(code_lines[line]):
            break
        line += 1
        col = 0
    else:
        return "", index + 1
    if code_lines[line][col] != "(":
        # 无括号体：`try 表达式 catch 表达式` 的体就是这一行剩下的部分
        return code_lines[line][col:], line + 1
    depth = 0
    parts: list[str] = []
    while line < len(code_lines):
        for ch in code_lines[line][col:]:
            if ch == "(":
                depth += 1
                if depth == 1:
                    continue
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return "".join(parts), line + 1
            parts.append(ch)
        line += 1
        col = 0
    return "".join(parts), index + 1


def check_empty_catch(src: str) -> list[str]:
    """规则 V：不许有空 catch —— 空 catch 的语义是「失败了我不想知道」。

    为什么值得变成静态规则（第十七轮之后的探针实测）：
    v3 的 `max_import_obj.ms:3982` 写着 `try ( skinOps.forceUpdate skinMod ) catch ()`，
    而探针 probes/skin_readback.txt 把 Max 2022 的 skinOps 成员表整个吐出来过
    （报错文字 `Unknown property ... in #Struct:skinOps(...)` 会带出 143 个成员）——
    **forceUpdate 不在里面**。那句从来就没生效过，我们却一直以为「force 更新之后再读
    GetNumberBones」是可信的。空 catch 把一个死调用藏了十几轮。
    同一文件里这样的 catch 曾有 64 个 —— #81 把 66 条（含后来新增的）清到 0，
    并把这条规则从 `--strict` 改成默认拦路。

    确实允许失败的地方（可选 API、探测某个属性存不存在）必须显式写
    `-- 允许失败：<理由>`，让"这里会静默"变成一条能 grep、且带着理由的声明。
    """
    problems = []
    raw_lines = src.splitlines()
    code_lines = _blank_strings(_strip_lines(src))
    for index, code in enumerate(code_lines, start=1):
        for match in re.finditer(r"\bcatch\b", code):
            body, end_line = _catch_body(code_lines, index - 1, match.end())
            if body.strip():
                continue
            # 豁免声明可以写在 catch 那一行，也可以写在闭合括号那一行（跨行体的常见写法）
            raw = " ".join(raw_lines[i] for i in range(index - 1, min(end_line, len(raw_lines))))
            if CATCH_EXEMPT_RE.search(raw):
                continue
            where = f"行 {index}" + (f"~{end_line}" if end_line > index else "")
            problems.append(
                f"{where}: `catch` 的体是空的 —— 失败会被完全吞掉"
                f"（v3 的死调用 skinOps.forceUpdate 就是这么藏了十几轮的）。"
                f"要么在体里记下失败（写 verdict / 打印 getCurrentException()），"
                f"要么显式声明 `-- {CATCH_EXEMPT_MARK}：<理由>`（冒号后面得有内容）。")
    return problems


# 判据名的识别**不锚在行首**：这条规则的用处是「凡是长得像在验收的函数，都不许拿
# 写入侧的求值器当期望值」，而 `mmd2maxFitScore`（打分→收敛闸门）这种名字把语素放在
# 中间，锚定写法看不见它 —— 看不见的那一条正是 #26/#40/#41 那类恒等式。
# 中文语素一起放着：这份文件现在的函数名全是 ASCII（实测 132 个顶格 fn 零个非 ASCII），
# 但 `_fn_spans` 已按 Unicode 认标识符，哪天有人写 `fn mmd2max复核脚盒` 就得被这条规则看见。
CHECKER_NAME_RE = re.compile(
    r"(?i)(mmd2max)?(Check|Verify|Audit|Score|Valid|LogStruct|Dump|校验|核对|判据|复核)")

# 求值器 = 产出「这里应该朝哪 / 应该落在哪」的函数。判据拿它的返回值当期望值就是恒等式。
ORACLE_NAME_RE = re.compile(r"(?i)(Want|Target|Desired|Expect|BoxDir)")

# `mmd2maxSegLen` 以前也被当成求值器（名字里有 SegLen）。它是尺：
# `distance a.transform.translationpart b.transform.translationpart` —— 只回读场景，
# 不产出任何期望值。把它列进求值器，`mmd2maxFitSegments` 那种「期望值来自 limbs.tsv 的
# desired_len、读数来自回读」的正常判据也会一起报成违规；而**误报的闸和漏报的闸一样危险**
# —— 它教会大家忽略这条规则。真正的同源判据是拿 `Want`/`Target` 那类函数的返回值当期望值，
# 那是下面按数据流查的事，不靠名字猜。
TOKEN = r"[\w]+"


def _fn_params(code_lines: list[str], decl_line: str) -> list[str]:
    """从 `fn 名字 参数… =` 声明行取出位置参数名（MAXScript 没有逗号实参表）。"""
    match = re.match(r"(?:fn|function)\s+" + TOKEN + r"\s*(.*)", decl_line.strip())
    if not match:
        return []
    head = match.group(1)
    cut = head.find("=")
    if cut >= 0:
        head = head[:cut]
    return [t for t in re.findall(TOKEN, head) if t not in RESERVED]


def _split_args(text: str) -> list[str]:
    """按顶层空白切一个调用的实参；括号、方括号与**字符串**内部都不切。

    字符串这一维不是凑数：喂进来的行已经被 `_blank_strings` 把引号里的内容抹成等长
    空格（引号本身留着），于是 `mmd2maxReportDir "Bip001 L Foot" want` 切成
    `['\"', '', '\"', 'want']` —— 实参的下标整体往前挪，`want` 落到了第 3 位，
    而它其实是第 1 个形参。`paramN` 就此接不上 arrivals，跨函数的污点在半路自己断掉：
    一种「规则看起来在跑、其实看不见」的漏法，而且只往「放过」的方向漏。
    """
    args: list[str] = []
    depth = 0
    in_string = False
    current = ""
    for ch in text:
        if ch == '"':
            in_string = not in_string
        elif ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
            if depth < 0:
                break
        if ch in " \t" and depth <= 0 and not in_string:
            if current:
                args.append(current)
                current = ""
            continue
        current += ch
    if current:
        args.append(current)
    return args


def _lock_check_names() -> set[str]:
    """`verdicts.py` 里被声明成 `lock`（已定价的缺陷）的 check 名。

    为什么要跨文件知道这个：同一句「拿写入侧的目标核对写入结果」，出现在 **gate** 上是
    规则 I 禁止的假验收，出现在 **lock** 上是明码标价的「只求不比上一轮差」。
    不区分这两种，要么把定价过的锁也判违规（于是大家去放宽规则），要么对恒等式 gate
    睁一只眼（于是规则失去用处）。读不到 verdicts.py 时返回空集 = 一律按 gate 处理，
    漏报的方向永远是「报多了」而不是「放过了」。

    首选 `LOCK_CHECKS` 那张表（verdicts.py 里显式列出来的）。文本扫两种写法只能兜底：
    `_lock("converge", check, …)` 里 check 是循环变量时，扫出来的名单里就是**没有**
    `dir_over`，而那一行在产品里真的落报告 —— 差的就是这一类「表上看得到、扫不出来」。
    """
    path = ROOT / "mmd2max" / "verdicts.py"
    if not path.is_file():
        return set()
    text = path.read_text(encoding="utf-8")
    declared = re.search(r"\bLOCK_CHECKS\s*=\s*\(([^)]*)\)", text)
    if declared:
        return set(re.findall(r"\"([^\"]+)\"", declared.group(1)))
    names = set(re.findall(r"_lock\(\s*\"[^\"]*\"\s*,\s*\"([^\"]+)\"", text))
    # 组必须圈在 check 名上：整条 `row(...)` 里唯一的引号对就是 phase/check 那两个，
    # 少了括号这个调用文本自己就成了名单里的一项（findall 无组返回整个匹配），于是
    # 「已定价的锁」一个都认不出来，规则 I 会把 `converge/dir_over` 这类真锁也判成假验收。
    # `[^)]*?` 不越过右括号 ⇒ 期望值现算（`row(..., f(x), tol, "lock")`）的行认不出，
    # 漏的方向是「多报」，与读不到文件时返回空集同一个口径。
    names |= set(re.findall(r"row\(\s*\"[^\"]*\"\s*,\s*\"([^\"]+)\"[^)]*?\"lock\"", text))
    return names


def _reported_check(raw: str) -> str | None:
    """从 `mmd2maxReport "phase" "check" …` 的**原始行**取出 check 名。

    只能在原文上取：字符串内容已经被 `_blank_strings` 抹成空格（那是按形状匹配的规则
    需要的），而这里要的恰恰是引号里的字。取不到（跨行没拼全、或走的是包装函数）就返回
    None，调用侧按「不是已定价的锁」处理 —— 宁可多报。
    """
    quoted = re.findall(r"\"([^\"]*)\"", raw)
    return quoted[1] if len(quoted) >= 2 else None


# 已定价的例外：键 = (**落报告的那个函数**, **污点来源的那个求值器**)。
# 加进来必须带理由，空着最理想。键落在「落报告的函数」而不是「调求值器的函数」：
# 数据流那一层是顺着实参位置把 want 传进包装器的，豁免必须钉在写这一行的地方，
# 否则钉在外层等于没钉（包装器照样替别人写恒等式）。
#
# (mmd2maxVerifyBoxes, mmd2maxBoxWantDir)：脚趾盒的「参考方向」与写入同一条。
# 这一条在 _fn_spans 修好之前根本查不出来（每个 fn 跨度只有 1 行，规则 I 只看
# 得到声明行），修好后它立刻命中 —— 命中是对的方向，但这条是定价过的：
#   * gate 的期望值不来自写入侧（脚链 limbs.tsv 第 15~18 列 / 趾长第 11 列 /
#     widths.tsv，Python 沿骨轴量网格算出来的），`Want` 在这里只当「沿哪根方向量」的轴选择器；
#   * #79 加的 `span_want_cm` 把盒角点投到这个方向上直接量，绕开盒账：骨盒要是没沿
#     它被指着的那根轴长，这一行就短于趾长目标，判据变红 —— 换模型冒烟（#75）实测
#     就是这么红过 15 行又收到 10 行的，所以这条判据「有能力说不」不是推测。
# 保留：如果哪天 span_want_cm 被删，这个例外就必须跟着删（它成立完全靠那把独立尺）。
#
# (mmd2maxReportBoxDims, mmd2maxBoxWantDir)：上面那条定价的**另一半天** —— 骨盒回读的
# 五行读数都从这里的 `dir` 参数拿方向（`mmd2maxBoxDims node dir` / `mmd2maxWorldSpanAlong`），
# 期望值全部来自 widths.tsv。判据与它的报告包装器必须一起列，只列外层则内层照样红。
# 保留：哪天这组读数的期望值改由 Max 自己算，例外立刻作废。
#
# (mmd2maxFitScore, mmd2maxBoxWantDir)：收敛循环的**打分函数**。搜索必须拿写入侧自己的
# 目标函数当分数，否则它优化的就不是它写下的东西。这条契约记在 `mmd2maxConvergeGate` 的注释里
# （原文两句：「门槛仍是 MMD2MAX_DIRTOL」+「打分与尝试门控同源那条契约测试没动」）；同一份注释
# 还记着 M3 为什么要换掉旧闸门：旧条件是转角 pass 自报的「这一遍没有骨要转」，那是「我打算干什么」
# 不是「场景现在是什么样」，第十七轮实测方向超标 4→5→5 而落位三遍一动不动，撞上限退出与真达标
# 在日志里长得一模一样。它的验收身份是 `converge/dir_over`，而那一行在 `verdicts.CONVERGE_LOCKS`
# + `_lock` 那组行里是 **lock**（只求不比上一轮的 4/5/5 差），不装作曲径。
# 保留条件：dir_over 哪天从 lock 升成 gate，这个例外必须跟着删 —— 那时候它就是在自己判自己。
#
# 第三元素（可选）= 这一条例外**只放过哪一行报告**。长函数必须用它：
# `mmd2maxBuildBiped` 是一千四百行的写入主体，按 (函数, 求值器) 豁免它等于给将来任何一条
# 同源 gate 开了后门；钉到 check 名才只放过已经定价的那一行。短函数（上面三条）不用它，
# 因为整支函数就是这一件事，钉行反而多一份要对齐的表。
ORACLE_EXEMPT: set[tuple[str, ...]] = {
    ("mmd2maxVerifyBoxes", "mmd2maxBoxWantDir"),
    ("mmd2maxReportBoxDims", "mmd2maxBoxWantDir"),
    ("mmd2maxFitScore", "mmd2maxBoxWantDir"),
    # ("mmd2maxBuildBiped", "mmd2maxShoulderRayTarget", "ray_err_cm")：b 方案（#29）把
    # UpperArm 的落位目标**定义**成锁骨骨盒射线点，所以 `distance got rayT` 问不出
    # 「目标选得对吗」，它问的是「写下去的值有没有留在原处」—— 落的那行因此是**上界**
    # （`placement/ray_err_cm` 的 kind=max，期望值是 PLACEMENT_RAY_TOL_CM 而不是 0），
    # 「落位合不合 PMX」由同一批 `pos_err_cm` 行负责，那批的 want 直接来自 limbs.tsv。
    # 保留条件：这一行哪天升成 gate（期望值改成 spec 数），或者 ray_err_cm 不再由
    # BuildBiped 自己报，这条例外就必须跟着删。
    ("mmd2maxBuildBiped", "mmd2maxShoulderRayTarget", "ray_err_cm"),
}


def _exempted(fn: str, oracle: str, check: str | None) -> bool:
    """这条 (落报告的函数, 同源求值器, 这一行的 check) 是否已定价。"""
    if (fn, oracle) in ORACLE_EXEMPT:
        return True
    return check is not None and (fn, oracle, check) in ORACLE_EXEMPT


def _fn_spans(code_lines: list[str]) -> list[tuple[str, int, int]]:
    """按括号深度切出顶层 `fn 名字` 的行范围（1-based，含定义行）。

    只认顶格写的 fn —— check_fn_depth 已经保证顶格 fn 一定在深度 0，
    嵌套进去的是局部函数，不归这条规则管。

    关键：声明行自己没有括号 **不算函数结束**。这份文件里绝大多数函数写成
        fn mmd2maxLog message =
        (
            ...
        )
    括号在下一行才开。早期版本按「深度回到 0 就关段」处理，于是每个 fn 的跨度
    都只有声明那 1 行，规则 I 只看得到 `fn 名字 …` 这行，永远查不出调用 —— 一条
    看着在跑、实际是死的闸（比没有闸更坏，因为它让人以为有人在管）。
    """
    spans: list[tuple[str, int, int]] = []
    depth = 0
    open_name: str | None = None
    open_line = 0
    opened = False

    def close(end_line: int) -> None:
        nonlocal open_name, opened
        if open_name is not None:
            spans.append((open_name, open_line, end_line))
            open_name = None
            opened = False

    for index, code in enumerate(code_lines, start=1):
        stripped = code.strip()
        delta = _paren_delta(code)
        head = None
        if depth == 0:
            # 标识符按 Unicode 认：MAXScript 允许非 ASCII 名字，只认 ASCII 的话
            # 「名字里带中文的判据函数」根本进不了 spans，规则 I 对它是盲区。
            match = re.match(r"(?:fn|function)\s+(" + TOKEN + r")", stripped)
            if match:
                head = match.group(1)
        if head is not None:
            close(index - 1)
            open_name, open_line, opened = head, index, False
            depth = max(depth + delta, 0)
            opened = depth > 0
            continue
        if open_name is None:
            depth = max(depth + delta, 0)
            continue
        if not opened:
            # 还没见着括号：要么这是「一行体」（本行没有开括号，下一段就是新语句），
            # 要么括号在后面的行上等。
            if depth == 0 and delta <= 0 and stripped and index > open_line:
                close(index - 1)
                depth = max(depth + delta, 0)
                continue
            if depth + delta > 0:
                opened = True
            depth = max(depth + delta, 0)
            continue
        depth += delta
        if depth <= 0:
            close(index)
            depth = 0
    close(len(code_lines))
    return spans


def _logical_lines(code_lines: list[str]) -> list[tuple[int, str]]:
    """把行尾 `\\` 的续行接成一条逻辑行，返回 [(起始行号 1-based, 文本)]。

    为什么必须接：MAXScript 的续行把**一次调用**劈成两三行，而「实参在第几个位置」
    只有在整条调用上才谈得上。按物理行做污点，`(mmd2maxBoxWantDir …)` 那个写在下一行的
    实参就成了不相干的语句，污点在那里断掉 —— 而 `foot_yaw` 那两行恒等式（5355~5356）
    恰恰就是这么排的。
    """
    out: list[tuple[int, str]] = []
    buf, start = "", 0
    for index, code in enumerate(code_lines, start=1):
        stripped = code.rstrip()
        if stripped.endswith("\\"):
            if not buf:
                start = index
            buf += stripped[:-1]
            continue
        if buf:
            out.append((start, buf + code))
            buf = ""
        else:
            out.append((index, code))
    if buf:
        out.append((start, buf))
    return out


def _expr_tags(expr: str, oracles: set[str], tainted: dict[str, set[str]],
               return_oracles: dict[str, set[str]],
               leak: dict[str, set[int]], known: dict[str, list[str]]) -> set[str]:
    """一个表达式里的污点来源：直接调求值器，或调了「参数/内部求值器会流到返回值」的函数。

    标签有两种：求值器名（源头在 Max 的写入侧）与 `paramN`（源头在**本函数的第 N 个实参**，
    这里还不知道传进来的是什么）。`paramN` 只在函数内部相对成立，跨函数时按调用点
    的实参重新取值 —— 这样 `mmd2maxVecAngleDeg` 这种纯几何尺就不会因为「某个调用点
    拿 want 喂过它」而永久变成污点源：那是这条规则最容易长出的假阳性，误报会把
    整条规则变成噪音，而噪音的规则没人看。
    """
    tags: set[str] = set()
    tokens = set(re.findall(TOKEN, expr))
    tags |= tokens & oracles
    for name in tokens & set(tainted):
        tags |= tainted[name]
    for match in re.finditer(TOKEN, expr):
        callee = match.group(0)
        if callee not in known:
            continue
        own = return_oracles.get(callee)
        positions = leak.get(callee)
        if not own and not positions:
            continue
        tags |= own or set()
        if not positions:
            continue
        args = _split_args(expr[match.end():])
        for position in positions:
            if position < len(args):
                tags |= _expr_tags(args[position], oracles, tainted,
                                   return_oracles, leak, known)
    return tags


def check_oracle_independence(src: str) -> list[str]:
    """规则 I：判据不许拿写入侧的求值器当期望值（判据独立于写入）。

    这条规则是 v4 存在的全部理由。#26（偏航）/#40（链向）/#41（脚盒）三次缺陷
    是同一个 bug：`mmd2maxBoxWantDir` 既用来决定骨盒朝哪，又用来核对骨盒朝哪 ——
    恒等式，永远绿；`段校验` 拿两个刚被钉到同一对 PMX 点上的 pivot 互相量 —— 也永远绿。
    日志里几百个"成功"计数，没有一行有能力说"不"。

    两层查法，因为单靠名字查会漏两类极便宜的规避：

    1. **名字**：函数名像判据（Check/Verify/Audit/Score/Valid/LogStruct/Dump + 中文语素，
       **不锚行首**）而体内出现像求值器的调用（Want/Target/Desired/Expect/BoxDir）。
    2. **数据流**：求值器的返回值 → 局部量 → （跨函数：实参按位置污染被调方的形参，
       返回值污染调用方）→ 流进 `mmd2maxReport` 或任何最终会写报告的函数。
       这一层才管得住「把 want 当参数传进一个叫 LogStruct 的函数」——
       `mmd2maxLogStructDir` 的名字里没有一个求值器语素，函数体里也没有求值器调用，
       按名字查它永远是干净的，而它的第三个参数就是 `mmd2maxBoxWantDir` 给的方向。

    命中了就两选一：把期望值改成从 spec 文件读（规则 IV 的数值单源），或者把这一行
    降级成日志。`verdicts.py` 里声明成 **lock** 的那几行例外 —— 锁的语义本来就是
    「只求不比上一轮差」，拿它冒充曲径才是违规。
    """
    problems = []
    code_lines = _blank_strings(_strip_lines(src))
    logical = _logical_lines(code_lines)
    spans = _fn_spans(code_lines)
    oracles = {name for name, _s, _e in spans if ORACLE_NAME_RE.search(name)}
    known = {name: _fn_params(code_lines, code_lines[start - 1].strip())
             for name, start, _e in spans}
    # 汇点 = 直接写报告的函数 + 调用它们的函数（一跳闭包；包装层是这条规则最常见的藏身处）
    sinks = {"mmd2maxReport"}
    for _ in range(3):
        grown = set(sinks)
        for name, start, end in spans:
            body = "\n".join(code_lines[start - 1:end])
            if any(re.search(r"\b" + re.escape(s) + r"\b", body) for s in sinks):
                grown.add(name)
        if grown == sinks:
            break
        sinks = grown
    lock_checks = _lock_check_names()

    # 第 1 层：名字。判据名 × 求值器名，同在一个函数体内。
    for name, start, end in spans:
        if not CHECKER_NAME_RE.search(name):
            continue
        for line in range(start, end + 1):
            for callee in re.findall(TOKEN, code_lines[line - 1]):
                if callee == name or callee not in oracles:
                    continue
                if (name, callee) in ORACLE_EXEMPT:
                    continue
                problems.append(
                    f"行 {line}: 判据 `{name}` 调用了求值器 `{callee}` —— 期望值和写入值"
                    f"同源，这条判据不可能变红（#26/#40/#41 的成因）。"
                    f"改成从 spec 读期望值，或把调用移出判据。")

    # 第 2 层：数据流。标签有两种 —— 求值器名（源头在写入侧）与 `paramN`（源头在本函数
    # 第 N 个实参，进函数时还不知道传进来的是什么）。跨函数时按**调用点的实参**重新取值，
    # 所以 `mmd2maxVecAngleDeg` 这类纯几何的尺不会因为「某个调用点拿 want 喂过它」而永久
    # 变成污点源 —— 那会把它每一个正常调用都报成违规，误报的规则没人看。
    # 不动点迭代：A 的返回值污点要等 A 自己扫过才知道，B 把 want 传给 C 的形参也要等 B 扫过。
    raw_logical = dict(_logical_lines(_strip_lines(src)))
    body_lines: dict[str, list[tuple[int, str]]] = {}
    for name, start, end in spans:
        statements = [(no, text) for no, text in logical
                      if start < no <= end and text.strip() not in ("", "(", ")")]
        body_lines[name] = statements

    return_oracles: dict[str, set[str]] = {}
    leak: dict[str, set[int]] = {}
    arrivals: dict[tuple[str, int], list[tuple[str, set[str]]]] = {}
    hits: list[tuple[str, int, str | None, set[str]]] = []
    for _round in range(6):
        new_return: dict[str, set[str]] = {}
        new_leak: dict[str, set[int]] = {}
        new_arrivals: dict[tuple[str, int], list[tuple[str, set[str]]]] = {}
        new_hits: list[tuple[str, int, str | None, set[str]]] = []
        for name, _start, end in spans:
            tainted = {param: {f"param{pos}"} for pos, param in enumerate(known[name])}
            statements = body_lines[name]
            for position, (no, text) in enumerate(statements):
                tags = _expr_tags(text, oracles, tainted, return_oracles, leak, known)
                target = re.match(r"\s*(?:local|global)?\s*(" + TOKEN + r")\s*(?:=|\+=|-=|\*=|/=)",
                                  text)
                if target and tags:
                    tainted.setdefault(target.group(1), set()).update(tags)
                # 返回值：显式 `return`，或函数的最后一个表达式（MAXScript 没有隐式 return
                # 这件事是反过来的 —— 收尾表达式**就是**结果）
                if tags and (re.search(r"\breturn\b", text) or position == len(statements) - 1):
                    for tag in tags:
                        if tag.startswith("param"):
                            new_leak.setdefault(name, set()).add(int(tag[5:]))
                        else:
                            new_return.setdefault(name, set()).add(tag)
                sink = next((s for s in sinks if s != name
                             and re.search(r"\b" + re.escape(s) + r"\b", text)), None)
                if sink is not None and tags:
                    check = _reported_check(raw_logical.get(no, "")) if sink == "mmd2maxReport" else None
                    new_hits.append((name, no, check, set(tags)))
                for match in re.finditer(TOKEN, text):
                    callee = match.group(0)
                    if callee not in known or callee == name:
                        continue
                    args = _split_args(text[match.end():])
                    for pos, arg in enumerate(args[:len(known[callee])]):
                        arg_tags = _expr_tags(arg, oracles, tainted, return_oracles, leak, known)
                        if arg_tags:
                            new_arrivals.setdefault((callee, pos), []).append((name, arg_tags))
        stable = (new_return == return_oracles and new_leak == leak
                  and new_arrivals == arrivals)
        return_oracles, leak, arrivals, hits = new_return, new_leak, new_arrivals, new_hits
        if stable:
            break

    def origins(fn: str, tag: str, seen: set[tuple[str, str]]) -> set[str]:
        """把 `paramN` 还原成真正的求值器名：看所有调用点在的那个位置传进来的是什么。

        报告行常常在包装器里（`mmd2maxReportBoxDims` 落五行读数），want 是从外面传进来的
        第三个参数 —— 只看包装器自己，它身上一个求值器都没有。所以豁免的键必须是
        **(落报告的函数, 还原出来的求值器)**：钉在外层调用方等于没钉。
        """
        if not tag.startswith("param"):
            return {tag}
        found: set[str] = set()
        for caller, caller_tags in arrivals.get((fn, int(tag[5:])), ()):
            for inner in caller_tags:
                if (caller, inner) in seen:
                    continue
                seen.add((caller, inner))
                found |= origins(caller, inner, seen)
        return found

    reported: set[tuple[str, int]] = set()
    for fn, line, check, tags in hits:
        if check is not None and check in lock_checks:
            continue        # 已定价的锁：PASS 只说「不比上一轮差」，不装作曲径
        sources: set[str] = set()
        for tag in tags:
            sources |= origins(fn, tag, {(fn, tag)})
        sources = {o for o in sources if not _exempted(fn, o, check)}
        if not sources or (fn, line) in reported:
            continue
        reported.add((fn, line))
        problems.append(
            f"行 {line}: 判据 `{fn}` 把写入侧求值器的结果落进了报告"
            f"（同源量：{'、'.join(sorted(sources))}）—— 期望值和写入值同一条，"
            f"这一行不可能变红（#26/#40/#41 的成因）。改成从 spec 读期望值，"
            f"或把这一行降级成日志。")
    return problems


# 记账用的全局变量：值不来自 CFG，而是 Max 在跑的过程中自己攒的状态。
# 列在这里必须说得出「谁写它」，因为状态变量被第二次赋值是脏读数的来源，
# 而 CFG 变量被第二次赋值意味着「Python 说的数不算了」—— 那正是规则 IV 要防的。
CFG_STATE: dict[str, str] = {
    "MMD2MAX_CFG": "生成脚本注入的唯一外部变量（CFG 路径）",
    "MMD2MAX_CFG_TBL": "mmd2maxLoadCfg 读进来的整张表",
    "MMD2MAX_CFGSUMMARY": "LoadCfg 拼好的摘要，InitLog 之后才打印",
    "MMD2MAX_REFRESHCALL": "redrawViews 第几次的计数",
    "MMD2MAX_LENAXIS_MAP": "肢段拟合逐段记的长度轴账",
    "MMD2MAX_BOXAXIS_MAP": "骨盒轴账（只 append）",
    "MMD2MAX_BOXWH_MAP": "骨盒宽/高轴账（写入侧逐根记，回读侧认账；同骨后写覆盖）",
    "MMD2MAX_CENTER_TARGET": "mmd2maxReadLimbs 从 limbs.tsv 记下的中轴均匀三节父骨名单（段校验按骨盒长度报数用）",
    "MMD2MAX_LEG_NATIVE": "mmd2maxMain 按 sdef_vertices.tsv 是否有数据行设置的 Calf 原生膝铰链开关",
    "MMD2MAX_SHOTMESH": "截图段暂存的网格名单",
    "MMD2MAX_SHOTFILL": "截图段暂存的填充设置",
    "MMD2MAX_SHOTPIXEL": "截图段暂存的像素读数（#115）",
    "MMD2MAX_SHOTCLIP": "截图段暂存的裁剪设置",
}


def check_numeric_single_source(src: str, cfg_keys: set[str] | None = None,
                                target_keys: set[str] | None = None) -> list[str]:
    """规则 IV：「这个数应该是多少」只许有一个来源 —— Python 写进 CFG 的那一份。

    M2a 把五个容差搬进 `mmd2max/targets.py`，M2b 又把判据拆成两张表；这两步的成果
    全靠一条纪律撑着：**Max 侧只读、不发明数**。这条纪律一旦破了，日志里的读数就
    不再是「按 Python 那张表跑出来的」，下一轮 diff_round 对的是两个不同的口径。
    四种破法按危害排：

    1. 字面量回来：`MMD2MAX_DIRTOL = 3.0` 出现在 .ms 里 = 容差有了第二个家，
       改 CFG 不再等于改行为（第十九轮之前它就是这样的）；
    2. 第二次赋值：CFG 读进来之后又被别处覆盖，读到哪个值取决于执行顺序；
    3. 名字与键分叉：`MMD2MAX_DIRTOL = mmd2maxCfgNum "MMD2MAX_DIRTOL_DEG"` ——
       M1b 的约定是「一个词两边同名」，分叉之后 CFG 里那个键没人读、.ms 里这个数没人写；
    4. 读一个 CFG 里没有的键：Max 在 `mmd2maxCfg*` 那一步就抛，整轮白跑
       （有了 cfg_keys 才查得了，所以 obj_run 生成脚本时把真的那份 CFG 传进来）。
    """
    problems: list[str] = []
    lines = _strip_lines(src)
    text = "\n".join(lines)
    # 键名在字符串里，只能用没抹掉字符串的那份文本取
    cfg_reads = re.findall(r"(MMD2MAX_\w+)\s*=\s*mmd2maxCfg\w*\s+\"(MMD2MAX_\w+)\"", text)
    read_names = {name for name, _key in cfg_reads}
    declared = set(re.findall(r"\bglobal\s+(MMD2MAX_\w+)", text))
    assigned: dict[str, list[tuple[int, str]]] = {}
    for index, code in enumerate(_blank_strings(lines), start=1):
        match = re.match(r"\s*(?:global\s+)?(MMD2MAX_\w+)\s*(?:=|\+=)", code)
        if match:
            assigned.setdefault(match.group(1), []).append((index, code.strip()))
    for name, key in cfg_reads:
        if name != key:
            problems.append(
                f"CFG 键名与全局名分叉：`{name} = mmd2maxCfg* \"{key}\"` —— M1b 的约定是"
                f"一个词两边同名，分叉之后「改 CFG 就是改这个数」不再成立。")
    for name in sorted(declared | set(assigned)):
        if name in CFG_STATE:
            continue
        sites = assigned.get(name, [])
        if name not in read_names:
            problems.append(
                f"`global {name}` 的值不来自 CFG（也不在记账名单 CFG_STATE 里）："
                f"规则 IV 要求「应该是多少」只有一个来源，请把它做成 targets.TARGETS 的一项"
                f"并由 mmd2maxLoadCfg 读进来。")
            continue
        if not sites:
            problems.append(f"`{name}` 声明了却从没被赋值 —— mmd2maxLoadCfg 里没有这一行？")
            continue
        hardcoded = [(index, code) for index, code in sites if "mmd2maxCfg" not in code]
        for index, code in hardcoded:
            problems.append(
                f"行 {index}: `{code[:60]}` —— CFG 变量 `{name}` 被第二次赋值/写死。"
                f"容差回到 .ms 里就等于有两个家：改 MMD2MAX.CFG 不再等于改行为，"
                f"而下一轮 diff_round 对的是两个口径。")
        reads = [index for index, code in sites if "mmd2maxCfg" in code]
        if len(reads) > 1:
            problems.append(
                f"行 {reads}: `{name}` 从 CFG 读了 {len(reads)} 次 —— 同一个键读两遍读出的是"
                f"同一个值，但它让「哪一行最后说了算」没法回答。")
    for name, key in cfg_reads:
        if cfg_keys is not None and key not in cfg_keys:
            problems.append(
                f"Max 读的 CFG 键 `{key}` 不在 Python 写出的那份 CFG 里 —— "
                f"mmd2maxCfg* 读不到就抛，这一轮会白跑（obj_run 生成时把 CFG 的键集传进来看见的）。")
    if target_keys is not None:
        for key in sorted(target_keys - read_names):
            problems.append(
                f"targets.TARGETS 里有 `{key}`，但 .ms 从没读它 —— 这个数发出去没人消费，"
                f"改它不影响任何行为，却会让人以为改了它就越过了某道闸。")
    return problems


def check_v4_debt(src: str) -> list[str]:
    """规则 V + 规则 I 的违规清单（v3 继承债，M1b~M4 期间会一路变少）。"""
    return check_empty_catch(src) + check_oracle_independence(src)

def check_bare_fn_reference(src: str) -> list[str]:
    """零参 `fn` 的名字单独出现（后面没跟 `(`）= 拿到的是**函数对象**，不是它的返回值。

    MAXScript 不像 Python：`f` 是值，`f()` 才是调用。所以 `if (f) then (...)` 编译得过、
    运行时报 `-- Type error: if-test requires BooleanClass, got: f()` —— 第 37 轮就是这句，
    整轮在读 CFG 那一步就炸，一句读数都没写。这一类错误静态上极好认，所以拦住。
    （真要传函数本身的地方 —— `map f list`、回调注册 —— 本文件里没有：Max 的回调走
    `installUI`/事件表，不靠把 `fn` 当值传。）
    """
    # 注释与字符串都要剥掉：这两处出现函数名是人话（「mmd2maxLoadCfg 必须排在……」），
    # 不是引用。
    code = "\n".join(_blank_strings(strip_comments(src).split("\n")))
    # 只认「零参」定义：`fn name =` 或 `fn name() =`；带参数的名字被裸引用是另一回事
    # （那本来就是语法错误，Max 会在编译期直接报，不需要这条规则）。
    zero_arg = {m.group(1)
                for m in re.finditer(r"\bfn\s+([A-Za-z_][A-Za-z0-9_]*)\s*(\(\s*\))?\s*=", code)
                if m.group(2) is None or m.group(2).strip("() \t") == ""}
    problems = []
    for name in sorted(zero_arg):
        for m in re.finditer(r"\b" + re.escape(name) + r"\b", code):
            after = code[m.end():]
            if after.lstrip().startswith("("):
                continue                      # 正常调用
            if re.match(r"\s*=", after):
                continue                      # 定义那一行本身（`fn name =`）
            line = code[:m.start()].count("\n") + 1
            problems.append(f"行 {line}: 裸引用零参函数 `{name}` —— 这里是函数对象而不是"
                            f"返回值，要写 `{name}()`")
    return problems


def _target_key_names() -> set[str] | None:
    """`targets.TARGETS` 里的键名 —— 用文本取，不 import。

    为什么不 import：check_ms 住在 tools/，`mmd2max` 包要靠项目根在 sys.path 上才导得到，
    而这份文件的调用方（obj_run、测试、CLI）各自的路径准备不一样。导不动就返回 None，
    调用侧**跳过这一向**并在报告里说明，而不是当成「没有键」——把「没量到」读成通过
    是这套验收最不能犯的那类错。
    """
    path = ROOT / "mmd2max" / "targets.py"
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    start = text.find("TARGETS")
    if start < 0:
        return None
    block = text[start:]
    end = block.find("\n}")
    return set(re.findall(r"\"(MMD2MAX_\w+)\"", block if end < 0 else block[:end]))


def cfg_keys_from_script(script_text: str) -> set[str] | None:
    """生成脚本自己指向的那份 MMD2MAX.CFG 里的键名（`key<TAB>value`，第 1 行是列名）。

    为什么要从脚本里读路径而不是让调用方传：obj_run 生成的那份 .ms 头部就写着
    `global MMD2MAX_CFG = "…"`，它已经知道自己会读哪张表 —— 检查这一份脚本就该用
    这一份真 CFG，而不是靠调用方记得把键集传对。模板（scripts/max_import_obj.ms）没有
    注入行，返回 None，「Max 读了一个 CFG 里没有的键」这一向就不查（不是查过说没问题）。
    """
    match = re.search(r'global\s+MMD2MAX_CFG\s*=\s*"((?:[^"\\]|\\.)*)"', script_text)
    if not match:
        return None
    path = Path(match.group(1).replace("\\\\", "\\").replace('\\"', '"'))
    if not path.is_file():
        return None
    keys: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines()[1:]:
        if line.strip():
            keys.add(line.split("\t", 1)[0])
    return keys


def check_source(src: str, cfg_keys: set[str] | None = None) -> list[str]:
    """全部规则跑一遍，返回问题列表（obj_run 生成脚本后直接调它）。

    14 条是「编译期会炸」的老规则；规则 V/I/IV 是 v4 新加的结构性约束。以前 V/I 走
    `--strict` 开关，因为 v3 继承了 60+ 条空 catch，一次性拦会把每一轮实跑卡住。
    #81 把这些债清到 0（每条要么换成记下了失败的读数，要么写成 `-- 允许失败：<理由>`），
    所以开关删掉，V/I 从此默认拦路。**注意**：不拦路从来不等于不重要 —— 空 catch 把
    `skinOps.forceUpdate` 这个死调用藏了十几轮，就是"先记账、以后再说"的下场。

    `cfg_keys` 是 Python 真的写进 MMD2MAX.CFG 的那组键名：不给的时候自己去脚本头部的
    `global MMD2MAX_CFG = "…"` 后面读那份文件（见 `cfg_keys_from_script`）。
    """
    if cfg_keys is None:
        cfg_keys = cfg_keys_from_script(src)
    problems = (check_reserved(src) + check_balance(src) + check_strings(src)
                + check_if_do_else(src) + check_local_time(src)
                + check_continuation(src) + check_fn_depth(src)
                + check_empty_fn_params(src) + check_chained_call_property(src)
                + check_bare_min_max(src) + check_comma_call(src)
                + check_optional_params(src) + check_forward_reference(src)
                + check_bare_fn_reference(src) + check_v4_debt(src)
                + check_numeric_single_source(src, cfg_keys=cfg_keys,
                                              target_keys=_target_key_names()))
    return problems


def check_file(path: Path) -> list[str]:
    if not path.is_file():
        return [f"文件不存在：{path}"]
    return check_source(path.read_text(encoding="utf-8"))


def main(argv: list[str]) -> int:
    targets = [Path(a) for a in argv[1:] if not a.startswith("--")] or sorted(
        (ROOT / "scripts").glob("*.ms"))
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

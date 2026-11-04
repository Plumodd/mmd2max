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


EMPTY_CATCH_RE = re.compile(r"\bcatch\s*\(\s*\)")
CATCH_EXEMPT_MARK = "允许失败"


def check_empty_catch(src: str) -> list[str]:
    """规则 V：不许有 `catch ()` —— 空 catch 的语义是「失败了我不想知道」。

    为什么值得变成静态规则（第十七轮之后的探针实测）：
    v3 的 `max_import_obj.ms:3982` 写着 `try ( skinOps.forceUpdate skinMod ) catch ()`，
    而探针 probes/skin_readback.txt 把 Max 2022 的 skinOps 成员表整个吐出来过
    （报错文字 `Unknown property ... in #Struct:skinOps(...)` 会带出 143 个成员）——
    **forceUpdate 不在里面**。那句从来就没生效过，我们却一直以为「force 更新之后再读
    GetNumberBones」是可信的。空 catch 把一个死调用藏了十几轮。
    同一文件里这样的 catch 有 64 个。

    确实允许失败的地方（可选 API、探测某个属性存不存在）必须显式写
    `-- 允许失败：<理由>` 在同一行，让"这里会静默"变成一条能 grep 的声明。
    """
    problems = []
    raw_lines = src.splitlines()
    for index, code in enumerate(_blank_strings(_strip_lines(src)), start=1):
        if EMPTY_CATCH_RE.search(code):
            raw = raw_lines[index - 1] if index - 1 < len(raw_lines) else ""
            if CATCH_EXEMPT_MARK in raw:
                continue
            problems.append(
                f"行 {index}: `catch ()` 是空 catch —— 失败会被完全吞掉"
                f"（v3 的死调用 skinOps.forceUpdate 就是这么藏了十几轮的）。"
                f"要么记下失败（写 verdict / 打印 getCurrentException()），"
                f"要么在同一行显式声明 `-- {CATCH_EXEMPT_MARK}：<理由>`。")
    return problems


CHECKER_NAME_RE = re.compile(r"(?i)^(mmd2max)?(Check|Verify|Audit|Score|Valid|LogStruct|Dump)")
ORACLE_NAME_RE = re.compile(r"(?i)(Want|Target|Desired|Expect|SegLen|BoxDir)")

# 已定价的例外：(判据函数, 它允许调用的求值器)。加进来必须带理由，空着最理想。
ORACLE_EXEMPT: set[tuple[str, str]] = set()


def _fn_spans(code_lines: list[str]) -> list[tuple[str, int, int]]:
    """按括号深度切出顶层 `fn 名字` 的行范围（1-based，含定义行）。

    只认顶格写的 fn —— check_fn_depth 已经保证顶格 fn 一定在深度 0，
    嵌套进去的是局部函数，不归这条规则管。
    """
    spans: list[tuple[str, int, int]] = []
    depth = 0
    open_name = None
    open_line = 0
    body_depth = 0
    for index, code in enumerate(code_lines, start=1):
        stripped = code.strip()
        if depth == 0 and open_name is None:
            m = re.match(r"(?:fn|function)\s+([A-Za-z_][A-Za-z0-9_]*)", stripped)
            if m:
                open_name = m.group(1)
                open_line = index
                body_depth = depth
        if open_name is not None:
            new_depth = depth + _paren_delta(code)
            if new_depth <= 0:
                spans.append((open_name, open_line, index))
                open_name = None
            depth = new_depth
            if depth < 0:
                depth = 0
        else:
            depth += _paren_delta(code)
            if depth < 0:
                depth = 0
    if open_name is not None:
        spans.append((open_name, open_line, len(code_lines)))
    return spans


def check_oracle_independence(src: str) -> list[str]:
    """规则 I：判据函数不许调用写路径的求值器（判据独立于写入）。

    这条规则是 v4 存在的全部理由。#26（偏航）/#40（链向）/#41（脚盒）三次缺陷
    是同一个 bug：`mmd2maxBoxWantDir` 既用来决定骨盒朝哪，又用来核对骨盒朝哪 ——
    恒等式，永远绿；`段校验` 拿两个刚被钉到同一对 PMX 点上的 pivot 互相量 —— 也永远绿。
    日志里几百个"成功"计数，没有一行有能力说"不"。

    静态近似（不是完备证明，但能拦住复发）：名字像判据的函数（Check/Verify/Audit/
    Score/Valid/LogStruct/Dump 开头）体内不许出现名字像求值器的调用
    （Want/Target/Desired/Expect/SegLen/BoxDir）。命中了就两选一：把期望值改成从
    spec 文件读（规则 IV 的数值单源），或者从判据里把这个调用拿掉。
    """
    problems = []
    code_lines = _blank_strings(_strip_lines(src))
    spans = _fn_spans(code_lines)
    oracles = {name for name, _s, _e in spans if ORACLE_NAME_RE.search(name)}
    by_line: dict[int, str] = {}
    for name, start, end in spans:
        for line in range(start, end + 1):
            by_line.setdefault(line, name)
    for name, start, end in spans:
        if not CHECKER_NAME_RE.search(name):
            continue
        for line in range(start, end + 1):
            for callee in re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\b", code_lines[line - 1]):
                if callee == name or callee not in oracles:
                    continue
                if (name, callee) in ORACLE_EXEMPT:
                    continue
                problems.append(
                    f"行 {line}: 判据 `{name}` 调用了求值器 `{callee}` —— 期望值和写入值"
                    f"同源，这条判据不可能变红（#26/#40/#41 的成因）。"
                    f"改成从 spec 读期望值，或把调用移出判据。")
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


def check_source(src: str, strict: bool = False) -> list[str]:
    """全部规则跑一遍，返回问题列表（obj_run 生成脚本后直接调它）。

    strict=False（默认）只跑 14 条「编译期会炸」的老规则；规则 V/I 是 v4 新加的
    结构性约束，v3 继承下来的违规还有 60+ 条，一次性拦会把每一轮实跑都卡住。
    所以先记账不拦路，main() 会把债的条数打出来；清完那天把 strict 打开（M4 的验收
    项之一）。**注意**：不拦路不等于不重要 —— 空 catch 把 skinOps.forceUpdate 这个
    死调用藏了十几轮，就是"先记账、以后再说"的下场。
    """
    problems = (check_reserved(src) + check_balance(src) + check_strings(src)
                + check_if_do_else(src) + check_local_time(src)
                + check_continuation(src) + check_fn_depth(src)
                + check_empty_fn_params(src) + check_chained_call_property(src)
                + check_bare_min_max(src) + check_comma_call(src)
                + check_optional_params(src) + check_forward_reference(src)
                + check_bare_fn_reference(src))
    if strict:
        problems = problems + check_v4_debt(src)
    return problems


def check_file(path: Path, strict: bool = False) -> list[str]:
    if not path.is_file():
        return [f"文件不存在：{path}"]
    return check_source(path.read_text(encoding="utf-8"), strict=strict)


def main(argv: list[str]) -> int:
    strict = "--strict" in argv[1:]
    targets = [Path(a) for a in argv[1:] if not a.startswith("--")] or sorted(
        (ROOT / "scripts").glob("*.ms"))
    total = 0
    for path in targets:
        problems = check_file(path, strict=strict)
        label = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
        if problems:
            total += len(problems)
            print(f"[FAIL] {label}：{len(problems)} 个问题")
            for item in problems[:15]:
                print("   ", item)
        else:
            print(f"[OK]   {label}")
        if not strict:
            # 结构性债（规则 V/I）默认只记账不拦路 —— 拦路会让每一轮实跑都卡在这
            # 60+ 条继承债上。清到 0 就把 --strict 打开，让 check_ms 真正咬人。
            debt = len(check_v4_debt(path.read_text(encoding="utf-8")
                                     if path.is_file() else ""))
            if debt:
                print(f"       （另有 {debt} 条 v4 结构性债：空 catch / 判据同源；"
                       f"--strict 才拦路）")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

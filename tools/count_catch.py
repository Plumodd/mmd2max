#!/usr/bin/env python3.12
"""数一份 .ms 里的 catch：多少条静默、多少条记了失败、记下来的失败有没有人读。

规则 V（check_ms 的空 catch 闸）管的是 `catch ()` —— 体是空的，语义就是「失败了我不
想知道」。#81 把 max_import_obj.ms 的 66 条债清到 0 之后，剩下的形状变成另一种：
`catch ( err = (getCurrentException() as string) )`。它把异常文字存进了变量，
于是躲过了空 catch 的正则；但如果那个变量后面没人读，失败照样一个字都不会出现在
日志或 verdicts 里 —— 对读者来说和 `catch ()` 没有区别，只是更难看穿。

这条债不能做成拦路规则：判「有没有人读」要把作用域算对，误报的闸三天后就被关了
（见交接文档里那条「误报的规则会被关掉」）。所以这里只报数、只列嫌疑，人来判。

用法：
    tools/count_catch.py [scripts/max_import_obj.ms]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import check_ms  # noqa: E402

EMPTY_RE = re.compile(r"\bcatch\s*\(\s*\)")
# catch ( name = ... ) / catch ( name += ... )：把失败存进一个具名的东西
CAPTURE_RE = re.compile(r"\bcatch\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*[+]?=")


def catch_rows(src: str) -> list[tuple[int, str, str]]:
    """返回 (行号, 原始行, 剥掉注释与字符串字面量之后的代码)。"""
    raw_lines = src.splitlines()
    rows = []
    for index, code in enumerate(check_ms._blank_strings(check_ms._strip_lines(src)),
                                start=1):
        if "catch" not in code:
            continue
        raw = raw_lines[index - 1] if index - 1 < len(raw_lines) else ""
        rows.append((index, raw, code))
    return rows


def dead_captures(src: str) -> list[tuple[int, str]]:
    """记下失败、但那份记录在这个函数里从没被读过的 catch —— 写了等于没写。

    判据：把变量所在的那个顶层 `fn` 整段拿出来，数它出现了几次（occ）和其中几次是
    「写」（`V =` / `V +=`，含 `local V = ""` 那一次声明）。occ == writes 就说明这个
    函数里没人读它，异常文字进去就死了。日志、verdicts、返回值都不可能出现，因为
    那些写法都会让 occ 大于 writes。
    顶格 fn 之外（pass 顶层代码）的站点按整个文件算，同一条判据。
    注释与字符串已经被 check_ms 的 blankers 抹掉，所以「注释里提了一句 err」
    不会被当成读了 err。
    """
    code_lines = check_ms._blank_strings(check_ms._strip_lines(src))
    spans = check_ms._fn_spans(code_lines)

    def span_of(line: int) -> tuple[int, int]:
        for _name, start, end in spans:
            if start <= line <= end:
                return (start, end)
        return (1, len(code_lines))

    write_re_cache: dict[str, re.Pattern] = {}

    def writes_in(text: str, var: str) -> int:
        pat = write_re_cache.get(var)
        if pat is None:
            pat = re.compile(rf"\b{re.escape(var)}\s*\+?=")
            write_re_cache[var] = pat
        return len(pat.findall(text))

    dead = []
    for index, _raw, code in catch_rows(src):
        if EMPTY_RE.search(code):
            continue
        match = CAPTURE_RE.search(code)
        if not match:
            continue
        var = match.group(1)
        start, end = span_of(index)
        text = "\n".join(code_lines[start - 1:end])
        occ = len(re.findall(rf"\b{re.escape(var)}\b", text))
        if occ == writes_in(text, var):
            dead.append((index, var))
    return dead


def main(argv: list[str]) -> int:
    targets = [Path(a) for a in argv[1:] if not a.startswith("--")]
    if not targets:
        targets = [ROOT / "scripts" / "max_import_obj.ms"]
    for path in targets:
        src = path.read_text(encoding="utf-8")
        rows = catch_rows(src)
        empty = [(i, raw) for i, raw, code in rows if EMPTY_RE.search(code)]
        marked = [(i, raw) for i, raw in empty if check_ms.CATCH_EXEMPT_MARK in raw]
        unmarked = [(i, raw) for i, raw in empty if check_ms.CATCH_EXEMPT_MARK not in raw]
        nonempty = len(rows) - len(empty)
        dead = dead_captures(src)
        print(f"{path.name}：catch 行 {len(rows)}")
        print(f"  空体 catch ()     ：{len(empty)}")
        print(f"    其中带「{check_ms.CATCH_EXEMPT_MARK}」声明：{len(marked)}")
        print(f"    其中无声明（债，check_ms 拦路）：{len(unmarked)}")
        print(f"  非空体 catch (…)  ：{nonempty}")
        print(f"  记了变量但全文没人读：{len(dead)}")
        for index, var in dead[:40]:
            print(f"    行 {index}: {var}")
        for index, raw in unmarked[:40]:
            print(f"    行 {index}: {raw.strip()[:90]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

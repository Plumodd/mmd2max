"""把 stdout/stderr 定成 UTF-8 —— 判据消息里有 `⇒`，它在 GBK 里编不出来。

为什么要有这个文件（三条都是量出来的，不是推的）：

1. **GBK 里有 `→ ≤ · × ——`，唯独没有 `⇒`（U+21D2）**：`'⇒'.encode('gbk')` 当场抛。
   而运行时唯一会打 `⇒` 的那句是 `check_verdicts` 的「⇒ 本轮验收不通过」。
2. **不指定编码时，Windows 上 Python 的 stdout 按本地代码页走**（这台机是 cp936），
   实测 `PYTHONIOENCODING=gbk python tools/check_verdicts.py <挂掉的目录>`：前面每一行
   都正常打完，然后**在 `⇒` 那一行崩**（UnicodeEncodeError + traceback）。
   也就是说：轮子挂了，而工具在说出「这一轮挂了」那句话之前自己先死了。
   （退出码碰巧还是 1，所以只看退出码的人不会发现日志少了一行。）
3. **冻结的 exe 命中同一件事**：实测 `mmd2max.exe --help` 重定向到文件时字节是 GBK
   （`→` = `a1 fa`），父环境里写着 `PYTHONUTF8=1` 也不认。源码侧之所以一直没露出来，
   是因为 `启动GUI.bat` 里 `chcp 65001` + `PYTHONIOENCODING=utf-8` 替它兜着。

`obj_run.log()` 那条 except 分支在同样条件下不崩，代价是 `⇒` 变成 `?`（实测
`[08:27:34] 定价 锁 ? 已冻结`）：读数还在，判据里的因果符号没了 —— 这正是本项目
「计数绿、内容坏」那一族的第 N 个变体。

所以入口第一件事把两个流重定成 UTF-8，之后的字节口径与盘上所有产物（CFG / REPORT /
CHECK / VERDICTS / 日志全是 UTF-8）、与 GUI 那侧 `encoding="utf-8"` 的子进程解码器一致。

**不动控制台代码页**（不调 `SetConsoleOutputCP`）：那是进程共享的状态，改了会把同一个
控制台里别的程序的输出搞乱；而 stdout 真连着控制台时 Python 走 `WriteConsoleW`
（UTF-16），本来就不经过代码页，`⇒` 显示得好好的。这里要救的是**被重定向 / 走管道**
的那条路 —— 也就是 exe 用户和自动化真正在走的那条。
"""
from __future__ import annotations

import sys

ENCODING = "utf-8"
# UTF-8 编不出来的只剩孤立代理对（多半来自操作系统错误信息里的半截路径）。
# 选 replace 而不是 strict：strict 会为了一个代理字符把整轮判据打死的同一种崩法重演一遍。
ERRORS = "replace"

# 每个流**第一次见到**时的编码。为什么要记住它：入口是嵌套的 —— `mmd2max.exe --obj-run …`
# 先在 exe 入口采纳一次，obj_run 的 main 再采纳一次，第二次如果不记住原值就会读出
# 「utf-8→utf-8」，看着像「本来就没问题」。实测（r3 exe，PYTHONIOENCODING=gbk 的子进程）
# 确实是这个假象，而它写出去的字节是 UTF-8 —— 读数说的和做的事不一致，就得改读数。
_ORIGINAL: dict[str, str] = {}


def adopt_utf8_console() -> str:
    """把 sys.stdout / sys.stderr 重定成 UTF-8，返回一句可以原样打进日志的读数。

    读数不是装饰：这一轮的输出到底是按什么编码写的，事后只有从日志里能查。
    重定向过的（TextIOWrapper）与不可重定的（测试里换成 StringIO、或别的 harness 塞进来的
    流）分开报，后者不许静默 —— 「没改」和「改成了 utf-8」在账上必须是两种字。
    """
    parts: list[str] = []
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        current = getattr(stream, "encoding", None) or "无"
        before = _ORIGINAL.setdefault(name, current)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            parts.append(f"{name}={before}(不可重定)")
            continue
        reconfigure(encoding=ENCODING, errors=ERRORS)
        parts.append(f"{name}={before}→{stream.encoding}")
    return " ".join(parts)

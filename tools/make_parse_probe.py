"""生成两份 Max 诊断脚本，用来回答「读代码答不了、又不该拿正式产物去试」的问题。

为什么需要：Max 2022 的报错弹窗是 Qt 自绘的，截图拿到 4bpp 花屏、Win32 也读不到
子控件文字，所以「脚本到底哪一步炸了」只能靠我们自己把异常写进文本文件里。

两份脚本各有分工：

1) parse（probe_parse.ms）：把正式脚本末尾真正干活的
   `try ( mmd2maxMain() ) catch (...)` 剪掉，用 fileIn 载入。
   语法错误会以可捕获异常的形式落到文本里 —— 拿到的是「Compile error: ...」+ 行内容。
   注意：fileIn 会为被载入的文件新建作用域，所以**它里面定义的 fn 在外面看不见**
   （实测 classof 全是 UndefinedClass）。这条限制决定了下面第 2 份的存在。

2) direct（probe_direct.ms）：不经过 fileIn，直接把剪掉主执行块、并在末尾追加
   诊断代码的脚本交给 `3dsmax.exe -U MAXScript` 执行 —— 作用域与正式跑完全一致，
   才能判「某个 fn 在正式跑时到底存不存在」。
   为避免污染正式产物，MMD2MAX_LOG / MARKER / OUTPUT 三个路径全部改写。

用法：python tools/make_parse_probe.py <run.ms> <out_dir> <post.ms>
     post.ms 里的代码会被包进一个 ( ) 块追加到 direct 脚本末尾（可用 mmd2maxLog）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

MAIN_BLOCK = "try\n(\n    mmd2maxMain()\n)"
LOG_LINE = re.compile(r'^MMD2MAX_LOG = ".*"$', re.M)
MARKER_LINE = re.compile(r'^MMD2MAX_MARKER = ".*"$', re.M)
OUTPUT_LINE = re.compile(r'^MMD2MAX_OUTPUT = ".*"$', re.M)
SHOTDIR_LINE = re.compile(r'^MMD2MAX_SHOTDIR = ".*"$', re.M)


def ms_path(p: Path) -> str:
    return str(p.resolve()).replace("\\", "\\\\")


def _cut(text: str) -> str:
    index = text.find(MAIN_BLOCK)
    if index == -1:
        raise SystemExit(f"找不到主执行块 {MAIN_BLOCK!r}，不敢生成探针（怕探针真去装配场景）")
    return text[:index]


def build(run_ms: Path, out_dir: Path, post_ms: Path | None) -> int:
    text = run_ms.read_text(encoding="utf-8")
    body = _cut(text) + "\n/* 探针：主执行块已剪掉，只留定义 */\n"
    out_dir.mkdir(parents=True, exist_ok=True)

    target = out_dir / "probe_parse_target.ms"
    target.write_text(body, encoding="utf-8")
    log_txt = out_dir / "probe_parse.txt"
    (out_dir / "probe_parse.ms").write_text(
        "(\n"
        "    fn ppLog s =\n"
        "    (\n"
        f"        local f = openFile \"{ms_path(log_txt)}\" mode:\"a\" encoding:#utf8\n"
        "        if f != undefined do ( format \"%\\n\" (s as string) to:f; close f )\n"
        "    )\n"
        f"    try ( deleteFile \"{ms_path(log_txt)}\" ) catch ()\n"
        "    ppLog (\"BEGIN \" + (localTime as string))\n"
        "    local parsed = false\n"
        f"    try ( fileIn \"{ms_path(target)}\"; parsed = true )\n"
        "    catch ( ppLog (\"PARSE FAIL: \" + (getCurrentException() as string)) )\n"
        "    if parsed do ppLog \"PARSE OK\"\n"
        "    ppLog (\"END \" + (localTime as string))\n"
        "    try ( quitMax quiet:true ) catch ()\n"
        "    parsed\n"
        ")\n", encoding="utf-8")

    direct = out_dir / "probe_direct.ms"
    if post_ms is not None:
        # 三个产物路径全部改写，探针绝不碰正式的 .max / 成功标记 / 日志。
        probe_text = LOG_LINE.sub(f'MMD2MAX_LOG = "{ms_path(out_dir / "probe_direct.log")}"',
                                  body, count=1)
        probe_text = MARKER_LINE.sub(f'MMD2MAX_MARKER = "{ms_path(out_dir / "probe.marker")}"',
                                     probe_text, count=1)
        probe_text = OUTPUT_LINE.sub(f'MMD2MAX_OUTPUT = "{ms_path(out_dir / "probe.max")}"',
                                     probe_text, count=1)
        probe_text = SHOTDIR_LINE.sub(
            f'MMD2MAX_SHOTDIR = "{ms_path(out_dir / "shots")}"', probe_text, count=1)
        # 诊断代码包在自己的 ( ) 块里：自动加载的文件里写顶层 local 会直接弹
        # Auto-load Script Error。mmd2maxInitLog 负责重建（截断）探针日志。
        probe_text += "\n(\n    try ( mmd2maxInitLog() ) catch ()\n"
        probe_text += post_ms.read_text(encoding="utf-8")
        # 探针跑完让 Max 自己安静退出：外面用 taskkill /F 强杀会被 Max 记成
        # 「异常终止」，攒够次数下次启动就弹 Startup Failure Detection，
        # 那个 Qt 弹窗读不到文字、还挡住 -U MAXScript 自动执行，整轮探针白跑。
        probe_text += ('\n    mmd2maxLog "PROBE END"\n'
                       '    try ( quitMax quiet:true ) catch ()\n)\n')
        direct.write_text(probe_text, encoding="utf-8")
    print(f"解析探针：{out_dir / 'probe_parse.ms'} → {log_txt}")
    if direct.is_file():
        print(f"直跑探针：{direct} → {out_dir / 'probe_direct.log'}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        print(__doc__)
        sys.exit(2)
    extra = Path(sys.argv[3]) if len(sys.argv) == 4 else None
    sys.exit(build(Path(sys.argv[1]), Path(sys.argv[2]), extra))

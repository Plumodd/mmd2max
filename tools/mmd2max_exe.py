"""单个 exe 的入口：三种跑法都走进同一份代码。

    mmd2max.exe                          图形界面（与双击 启动GUI.bat 同一个窗口）
    mmd2max.exe --model <目录|pmx> [...]  导出段，等价于 `python tools/run_model.py`
    mmd2max.exe --obj-run <obj目录> <名> [...]  装配段，等价于 `python tools/obj_run.py`

`--obj-run` 不是给人手打的，是给冻结模式下的**自己**调用的：`app_paths.obj_run_command()`
在冻结分支里只能拼 `exe + 子命令`，因为那时盘上没有 `tools/obj_run.py` 这个文件。
所以这个入口的三条分支必须与三个源文件的位置参数**逐字一致**，否则「源码跑」与「exe 跑」
会分成两套命令行口径 —— 那正是本项目最贵的分叉形状。

三个模块都在模块层静态 import：PyInstaller 只顺着静态 import 收依赖，函数里的 `import gui`
会被漏收，漏了的那条要到用户点那个按钮时才炸。
"""
from __future__ import annotations

import multiprocessing
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(TOOLS.parent))

import gui                                             # noqa: E402
import obj_run                                         # noqa: E402
import run_model                                       # noqa: E402
from mmd2max import app_paths, console                 # noqa: E402

USAGE = "\n".join([
    "mmd2max —— PMX → 3ds Max 2022",
    "",
    "  (无参数) / --gui [pmx]                  图形界面",
    "  --model <目录|.pmx> [--drop-guides] [--height N] [--min-weight N]",
    "                                        导出 OBJ 与数据表（+ 生成装配脚本）",
    "  --obj-run <obj目录> <模型名> [--no-skin] [--physics] [--shot] [--manual] [--gen-only] [--quit]",
    "                                        驱动 3ds Max 装配",
    "",
    f"  产物根：{app_paths.app_dir()}",
    f"  只读数据：{app_paths.bundle_dir()}",
])


def main(argv: list[str]) -> int:
    # 冻结的 exe 就是这条路上「编码不对」的那台机器：实测 `mmd2max.exe --help` 重定向时
    # 字节按 cp936 写（父环境写 PYTHONUTF8=1 也不认），而判据消息里的 ⇒ cp936 编不出来。
    # 分支还没分就先定口径，三条子命令与 USAGE 都走 UTF-8。
    console.adopt_utf8_console()
    if len(argv) < 2:
        return gui.main(["gui.py"])
    mode = argv[1]
    if mode in ("--gui", "gui"):
        return gui.main(["gui.py", *argv[2:]])
    if mode == "--model":
        return run_model.main(["run_model.py", *argv[2:]])
    if mode == "--obj-run":
        # obj_run.main() 直接读 sys.argv，所以这里改的是它看见的那份参数
        sys.argv = ["obj_run.py", *argv[2:]]
        return obj_run.main()
    if mode in ("--help", "-h", "help"):
        print(USAGE)
        return 0
    print(f"不认识的子命令 {mode}\n")
    print(USAGE)
    return 2


if __name__ == "__main__":
    # 进程池的子进程**就是这个 exe 自己**（`export_obj.export()` 用 ProcessPoolExecutor，
    # Windows 上只有 spawn；冻结后子进程的 argv 是 `--multiprocessing-fork key=val …`）。
    # 不先在这里认下来，子进程就会走到 main 的「不认识的子命令」分支退出 2，
    # 父进程那边表现为 BrokenProcessPool —— 2026-11-05 首跑实测就是这么炸的。
    multiprocessing.freeze_support()
    raise SystemExit(main(sys.argv))

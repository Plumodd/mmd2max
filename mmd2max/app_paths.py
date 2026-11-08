"""「代码在哪」与「产物写哪」，打包成单个 exe 之后是两件事。

源码跑（第 76 轮以来的全部行为）：两者都是仓库根，本模块不改变任何东西。
PyInstaller onefile：解释器与只读数据（`scripts/max_import_obj.ms`、`mmd2max/configs/*.json`）
解压进 `sys._MEIPASS` 那个**退出即删**的临时目录 —— 产物写在那里等于没写，截图、`.tsv`、
`MMD2MAX.*` 全都会跟着临时目录一起消失，而这套流水线的验收口径恰恰是「回读落盘的数」。
所以可写根必须是 exe 自己旁边。

`MMD2MAX_APP_DIR` 是同一套解析的显式覆盖：测试拿它模拟「产物根不在这」，用户拿它把导出
目录挪到别的盘，不需要改代码，也不需要真的打一份 exe 才能验证这条路径。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_ENV = "MMD2MAX_APP_DIR"


def is_frozen() -> bool:
    """PyInstaller 会给冻结进程挂上 `sys.frozen`；源码跑永远没有这个属性。"""
    return bool(getattr(sys, "frozen", False))


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def bundle_dir() -> Path:
    """只读数据根：exe 的解压目录 / 仓库根。"""
    if is_frozen():
        # `_MEIPASS` 缺失时退回 exe 所在目录：宁可读到「同目录那份脚本」，也不要抛 AttributeError
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return repo_root()


def app_dir() -> Path:
    """可写产物根：冻结时是 exe 旁边，源码时是仓库根，`MMD2MAX_APP_DIR` 优先。"""
    override = os.environ.get(APP_DIR_ENV, "").strip()
    if override:
        return Path(override).resolve()
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return repo_root()


def test_runs_dir() -> Path:
    return app_dir() / "test_runs"


def shots_dir() -> Path:
    return app_dir() / "outputs" / "shots"


def template_ms() -> Path:
    return bundle_dir() / "scripts" / "max_import_obj.ms"


def config_dir() -> Path:
    """骨骼定义 JSON 在哪：先认产物根下那份（用户改得动），没有才用只读包里那份。

    源码模式两个根是同一个目录，这条顺序不产生任何行为差异；冻结模式下它是
    「JSON 是唯一事实来源、改 JSON 不用改代码」这条设计在 exe 里还成立的唯一办法。
    判据只取 `pmx_definition.json` 在不在：三份 JSON 由 `export_configs()` 一起写，
    摆一半的形态不存在，不必为它加状态。
    """
    writable = app_dir() / "mmd2max" / "configs"
    if (writable / "pmx_definition.json").is_file():
        return writable
    return bundle_dir() / "mmd2max" / "configs"


def obj_run_command(out_dir: Path, name: str, *flags: str) -> list[str]:
    """重新调用装配段。

    冻结模式里没有 `tools/obj_run.py` 这个文件，只有 exe 自己 —— 所以子命令名是入口
    `tools/mmd2max_exe.py` 认的那几个。返回值一律 `str`：`subprocess` 虽然吃 `Path`，
    但冻结分支里拼的是 exe 路径 + 字面子命令，两种模式给同一种类型才对得上。
    """
    if is_frozen():
        return [sys.executable, "--obj-run", str(out_dir), name, *flags]
    return [sys.executable, str(bundle_dir() / "tools" / "obj_run.py"),
            str(out_dir), name, *flags]

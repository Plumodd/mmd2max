"""读 3ds Max 当前打开的报错弹窗文字（obj_run 只截图，截图是 4bpp 花屏读不出来）。

用法：python tools/read_dialog.py [窗口标题关键字]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from desktop import child_windows, find_windows  # noqa: E402


def dump(needle: str) -> int:
    windows = find_windows(needle)
    if not windows:
        print(f"没找到标题含 {needle!r} 的窗口")
        return 1
    for win in windows:
        print(f"=== hwnd={win.get('hwnd')} title={win.get('title')!r} rect={win.get('rect')}")
        for child in child_windows(int(win["hwnd"])):
            text = (child.get("text") or "").strip()
            if text:
                print(f"  [{child.get('class')}] {text}")
    return 0


if __name__ == "__main__":
    sys.exit(dump(sys.argv[1] if len(sys.argv) > 1 else "MAXScript"))

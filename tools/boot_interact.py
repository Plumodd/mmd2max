"""启动 3ds Max、自动处理启动弹窗，并验证主窗口是否就绪。

3ds Max 2022 用 Qt 绘制界面，按钮不是原生 HWND 子窗口，EnumChildWindows 枚举不到，
COM(Add-Type/ProgID) 也不可用。因此这里改为：
    截屏 -> 像素分析定位按钮矩形 -> 点击最右侧按钮 -> 验证结果。
"""
from __future__ import annotations

import struct
import subprocess
import sys
import time
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))

from desktop import (  # noqa: E402
    capture_region,
    capture_screen,
    click,
    find_windows,
    list_windows,
)

MAX_EXE = r"C:\Program Files\Autodesk\3ds Max 2022\3dsmax.exe"
SHOTS = TOOLS.parent / "outputs" / "shots"

# 弹窗按钮自左至右的顺序（依据用户截图确认）
BUTTON_ORDER = ["Restore Factory Settings...", "Go to Settings Folder",
                "Continue Without Restore"]
# 选择非破坏性选项，避免改动用户偏好设置
SAFE_BUTTON = "Continue Without Restore"


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def read_bmp(path: Path) -> tuple[int, int, bytes]:
    raw = path.read_bytes()
    offset = struct.unpack_from("<I", raw, 10)[0]
    width, height = struct.unpack_from("<ii", raw, 18)
    bit_count = struct.unpack_from("<H", raw, 28)[0]
    if bit_count != 32:
        raise ValueError(f"仅支持 32 位 BMP，实际 {bit_count}")
    pixels = raw[offset:]
    return width, height, pixels


def button_map(width: int, height: int, pixels: bytes) -> list[list[bool]]:
    """生成按钮底色掩码。

    3ds Max 2022 为深色主题：按钮底色约 (100,100,100)，窗口底色约 (68,68,68)，
    文字为更暗(62)或更亮(220)的像素。按"各通道接近且中等亮度"判定按钮底色。
    """
    row_bytes = width * 4
    grid: list[list[bool]] = []
    for y in range(height):
        base = (height - 1 - y) * row_bytes  # BMP 自下而上存储
        row = pixels[base:base + row_bytes]
        flags = []
        for x in range(width):
            b, g, r = row[x * 4], row[x * 4 + 1], row[x * 4 + 2]
            spread = max(r, g, b) - min(r, g, b)
            flags.append(spread <= 14 and 82 <= (r + g + b) / 3 <= 132)
        grid.append(flags)
    return grid


def _close_gaps(grid: list[list[bool]], horizontal: int, vertical: int) -> list[list[bool]]:
    """先横向补齐文字造成的细缝，再纵向补齐抗锯齿造成的断行。"""
    height = len(grid)
    width = len(grid[0]) if height else 0
    result = [row[:] for row in grid]
    for y in range(height):
        last = -1
        for x in range(width):
            if result[y][x]:
                if last >= 0 and x - last - 1 <= horizontal:
                    for fill in range(last, x):
                        result[y][fill] = True
                last = x
    for x in range(width):
        last = -1
        for y in range(height):
            if result[y][x]:
                if last >= 0 and y - last - 1 <= vertical:
                    for fill in range(last, y):
                        result[fill][x] = True
                last = y
    return result


def find_buttons(width: int, height: int, pixels: bytes,
                 y_from: float = 0.55, y_to: float = 0.99,
                 min_width: int = 60, max_width: int = 320) -> list[tuple[int, int, int, int]]:
    """在弹窗下半部分定位按钮：二维掩码 + 连通域求外接矩形。"""
    top = int(height * y_from)
    bottom = int(height * y_to)
    grid = button_map(width, height, pixels)[top:bottom]
    if not grid:
        return []
    grid = _close_gaps(grid, horizontal=7, vertical=2)
    rows = len(grid)
    cols = len(grid[0])

    seen = [[False] * cols for _ in range(rows)]
    boxes: list[tuple[int, int, int, int]] = []
    for y in range(rows):
        for x in range(cols):
            if not grid[y][x] or seen[y][x]:
                continue
            stack = [(y, x)]
            seen[y][x] = True
            x0 = x1 = x
            y0 = y1 = y
            count = 0
            while stack:
                cy, cx = stack.pop()
                count += 1
                x0 = min(x0, cx); x1 = max(x1, cx)
                y0 = min(y0, cy); y1 = max(y1, cy)
                for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                    if 0 <= ny < rows and 0 <= nx < cols and grid[ny][nx] and not seen[ny][nx]:
                        seen[ny][nx] = True
                        stack.append((ny, nx))
            w = x1 - x0 + 1
            h = y1 - y0 + 1
            if min_width <= w <= max_width and 16 <= h <= 48 and count >= 300:
                boxes.append((x0, y0 + top, w, h))

    boxes.sort(key=lambda b: b[0])
    return boxes


def main(argv: list[str]) -> int:
    SHOTS.mkdir(parents=True, exist_ok=True)
    log(f"启动 {MAX_EXE}")
    subprocess.Popen([MAX_EXE],
                     creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                     close_fds=True)

    dialog = None
    for _ in range(90):
        time.sleep(1)
        hits = find_windows("Startup Failure")
        if hits:
            dialog = hits[0]
            break
    if dialog is None:
        log("未在 90 秒内等到启动弹窗")
        capture_screen(str(SHOTS / "no_dialog.bmp"))
        return 1

    # 窗口句柄会先于实际绘制出现，必须等到内容真正画到屏幕上再截图
    log("弹窗句柄已出现，等待其绘制…")
    time.sleep(10)
    if not find_windows("Startup Failure"):
        log("等待期间弹窗消失")
        return 1

    left, top, right, bottom = dialog["rect"]
    width, height = right - left, bottom - top
    log(f"弹窗 hwnd={dialog['hwnd']} rect=({left},{top})-({right},{bottom}) {width}x{height}")
    shot = SHOTS / "dialog_live.bmp"
    capture_region(str(shot), left, top, width, height)

    try:
        w, h, pixels = read_bmp(shot)
        buttons = find_buttons(w, h, pixels)
    except Exception as error:  # noqa: BLE001
        log(f"按钮分析失败：{error}")
        return 2

    log(f"识别到按钮数：{len(buttons)} -> {buttons}")
    if not buttons:
        return 2

    # 按钮自左至右对应 BUTTON_ORDER；取最右侧 = Continue Without Restore
    chosen = buttons[-1]
    if len(buttons) > 1 and buttons[0][0] < buttons[-1][0]:
        pass  # 已按 x 排序
    cx = left + chosen[0] + chosen[2] // 2
    cy = top + chosen[1] + chosen[3] // 2
    log(f"点击最右侧按钮 目标={SAFE_BUTTON} 坐标=({cx},{cy}) 局部={chosen}")
    click(cx, cy)
    time.sleep(3)

    capture_region(str(SHOTS / "after_click.bmp"), left, top, width, height)
    # 轮询主窗口
    for i in range(60):
        time.sleep(2)
        windows = [w for w in list_windows() if "3ds Max" in w["title"]
                   and "Startup Failure" not in w["title"]]
        if windows:
            big = [w for w in windows if w["rect"][2] - w["rect"][0] > 900]
            if big:
                w0 = big[0]
                log(f"主窗口就绪 hwnd={w0['hwnd']} "
                    f"{w0['rect'][2] - w0['rect'][0]}x{w0['rect'][3] - w0['rect'][1]} "
                    f"{w0['title']!r}")
                capture_screen(str(SHOTS / "max_loaded.bmp"))
                return 0
        if i % 5 == 4:
            log(f"等待主窗口… 现有 Max 窗口 {len(windows)}")

    log("弹窗已关闭但未等到主窗口")
    capture_screen(str(SHOTS / "after_click_full.bmp"))
    return 3


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

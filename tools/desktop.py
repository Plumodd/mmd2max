"""Windows 桌面操作工具：枚举窗口 / 截屏 / 模拟鼠标键盘。

仅用标准库 ctypes 调用 Win32 API，不编译 .NET，避免运行时代码生成。
截图输出为 24/32 位 BMP，便于直接查看。

用法:
    python tools/desktop.py list
    python tools/desktop.py screen out.bmp
    python tools/desktop.py window <hwnd> out.bmp
    python tools/desktop.py click <x> <y>
    python tools/desktop.py focus <hwnd>
    python tools/desktop.py type "text"
    python tools/desktop.py keys "F11"
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import struct
import sys
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# 未声明 DPI 感知时，Windows 会对 GetWindowRect / 屏幕尺寸返回虚拟化坐标，
# 导致非主屏（缩放比例不同）上的窗口坐标与实际像素位置不符。必须先开启。
try:
    ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
except Exception:  # noqa: BLE001
    try:
        user32.SetProcessDPIAware()
    except Exception:  # noqa: BLE001
        pass

HWND = wt.HWND
RECT = wt.RECT
BOOL = wt.BOOL
LPARAM = wt.LPARAM

WNDENUMPROC = ctypes.WINFUNCTYPE(BOOL, HWND, LPARAM)

EnumWindows = user32.EnumWindows
EnumWindows.argtypes = [WNDENUMPROC, LPARAM]
EnumChildWindows = user32.EnumChildWindows
EnumChildWindows.argtypes = [HWND, WNDENUMPROC, LPARAM]
GetClassNameW = user32.GetClassNameW
GetClassNameW.argtypes = [HWND, ctypes.c_wchar_p, ctypes.c_int]
SendMessageW = user32.SendMessageW
SendMessageW.argtypes = [HWND, ctypes.c_uint, ctypes.c_ulonglong, ctypes.c_longlong]
SendMessageW.restype = ctypes.c_longlong
GetParent = user32.GetParent
GetParent.argtypes = [HWND]
GetParent.restype = HWND
GetWindowTextW = user32.GetWindowTextW
GetWindowTextW.argtypes = [HWND, ctypes.c_wchar_p, ctypes.c_int]
GetWindowTextLengthW = user32.GetWindowTextLengthW
GetWindowTextLengthW.argtypes = [HWND]
IsWindowVisible = user32.IsWindowVisible
IsWindowVisible.argtypes = [HWND]
GetWindowRect = user32.GetWindowRect
GetWindowRect.argtypes = [HWND, ctypes.POINTER(RECT)]
GetWindowThreadProcessId = user32.GetWindowThreadProcessId
GetWindowThreadProcessId.argtypes = [HWND, ctypes.POINTER(wt.DWORD)]
PrintWindow = user32.PrintWindow
PrintWindow.argtypes = [HWND, wt.HDC, ctypes.c_uint]
SetForegroundWindow = user32.SetForegroundWindow
SetForegroundWindow.argtypes = [HWND]
ShowWindow = user32.ShowWindow
ShowWindow.argtypes = [HWND, ctypes.c_int]
GetForegroundWindow = user32.GetForegroundWindow
GetForegroundWindow.restype = HWND
GetDC = user32.GetDC
GetDC.argtypes = [HWND]
GetDC.restype = wt.HDC
ReleaseDC = user32.ReleaseDC
ReleaseDC.argtypes = [HWND, wt.HDC]
mouse_event = user32.mouse_event
mouse_event.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_ulong]
keybd_event = user32.keybd_event
keybd_event.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte, ctypes.c_uint, ctypes.c_ulong]
SetCursorPos = user32.SetCursorPos
SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
VkKeyScanA = user32.VkKeyScanA
VkKeyScanA.argtypes = [ctypes.c_char]
VkKeyScanA.restype = ctypes.c_short

CreateCompatibleDC = gdi32.CreateCompatibleDC
CreateCompatibleDC.argtypes = [wt.HDC]
CreateCompatibleDC.restype = wt.HDC
CreateDIBSection = gdi32.CreateDIBSection
CreateDIBSection.restype = wt.HBITMAP
CreateCompatibleBitmap = gdi32.CreateCompatibleBitmap
CreateCompatibleBitmap.restype = wt.HBITMAP
SelectObject = gdi32.SelectObject
SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
BitBlt = gdi32.BitBlt
BitBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                   wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
GetDIBits = gdi32.GetDIBits
GetSystemMetrics = user32.GetSystemMetrics
GetSystemMetrics.argtypes = [ctypes.c_int]
DeleteObject = gdi32.DeleteObject
DeleteObject.argtypes = [wt.HGDIOBJ]
DeleteDC = gdi32.DeleteDC
DeleteDC.argtypes = [wt.HDC]

SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
BM_CLICK = 0x00F5
WM_SETTEXT = 0x000C
WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E
CF_UNICODETEXT = 13
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
KEYEVENTF_KEYUP = 0x0002
SW_RESTORE = 9


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32),
        ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32),
        ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16),
        ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32),
        ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", ctypes.c_uint32 * 3)]


CreateDIBSection.argtypes = [wt.HDC, ctypes.POINTER(BITMAPINFO), ctypes.c_uint,
                             ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.c_uint]
GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, ctypes.c_uint, ctypes.c_uint,
                      ctypes.c_void_p, ctypes.POINTER(BITMAPINFO), ctypes.c_uint]


def _window_title(hwnd: int) -> str:
    length = GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def _window_pid(hwnd: int) -> int | None:
    """窗口所属进程 id。用来把「别的 Max 实例留下的窗口」和本次启动的那个子进程区分开。"""
    pid = wt.DWORD()
    GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value or None


def list_windows() -> list[dict]:
    found: list[dict] = []

    def callback(hwnd, _lparam):
        if IsWindowVisible(hwnd):
            rect = RECT()
            GetWindowRect(hwnd, ctypes.byref(rect))
            found.append({
                "hwnd": hwnd,
                "title": _window_title(hwnd),
                "cls": _class_name(hwnd),
                "pid": _window_pid(hwnd),
                "rect": (rect.left, rect.top, rect.right, rect.bottom),
            })
        return True

    EnumWindows(WNDENUMPROC(callback), 0)
    return found


def write_bmp(path: str, width: int, height: int, pixels: bytes, bit_count: int = 32) -> None:
    row_size = ((width * bit_count + 31) // 32) * 4
    file_header = struct.pack("<2sIHHI", b"BM", 54 + row_size * height, 0, 0, 54)
    info_header = struct.pack(
        "<IiiHHIIiiII", 40, width, height, 1, bit_count, 0, row_size * height, 0, 0, 0, 0
    )
    with open(path, "wb") as stream:
        stream.write(file_header)
        stream.write(info_header)
        stream.write(pixels)


def _capture(hdc_mem, width: int, height: int, draw) -> bytes:
    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = width
    bmi.bmiHeader.biHeight = height
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = 0
    bits = ctypes.c_void_p()
    dib = CreateDIBSection(hdc_mem, ctypes.byref(bmi), DIB_RGB_COLORS, ctypes.byref(bits), None, 0)
    if not dib:
        raise OSError("CreateDIBSection failed")
    old = SelectObject(hdc_mem, dib)
    draw(hdc_mem)
    row_bytes = width * 4
    buffer = (ctypes.c_char * (row_bytes * height))()
    bmi.bmiHeader.biHeight = height
    GetDIBits(hdc_mem, dib, 0, height, buffer, ctypes.byref(bmi), DIB_RGB_COLORS)
    SelectObject(hdc_mem, old)
    DeleteObject(dib)
    return bytes(buffer)


def capture_screen(path: str, all_monitors: bool = True) -> tuple[int, int]:
    """截取屏幕。all_monitors=True 时截取跨所有显示器的虚拟桌面。"""
    hdc_screen = GetDC(0)
    hdc_mem = CreateCompatibleDC(hdc_screen)
    if all_monitors:
        left = GetSystemMetrics(SM_XVIRTUALSCREEN)
        top = GetSystemMetrics(SM_YVIRTUALSCREEN)
        width = GetSystemMetrics(SM_CXVIRTUALSCREEN)
        height = GetSystemMetrics(SM_CYVIRTUALSCREEN)
        origin = (left, top)
    else:
        origin = (0, 0)
        width = GetSystemMetrics(0)
        height = GetSystemMetrics(1)

    def draw(target):
        BitBlt(target, 0, 0, width, height, hdc_screen, origin[0], origin[1], SRCCOPY)

    pixels = _capture(hdc_mem, width, height, draw)
    DeleteDC(hdc_mem)
    ReleaseDC(0, hdc_screen)
    write_bmp(path, width, height, pixels)
    return width, height


def capture_region(path: str, left: int, top: int, width: int, height: int) -> tuple[int, int]:
    """截取虚拟桌面上的任意矩形区域（坐标可用负值，覆盖副屏）。"""
    hdc_screen = GetDC(0)
    hdc_mem = CreateCompatibleDC(hdc_screen)

    def draw(target):
        BitBlt(target, 0, 0, width, height, hdc_screen, left, top, SRCCOPY)

    pixels = _capture(hdc_mem, width, height, draw)
    DeleteDC(hdc_mem)
    ReleaseDC(0, hdc_screen)
    write_bmp(path, width, height, pixels)
    return width, height


def capture_window(hwnd: int, path: str) -> tuple[int, int, bool]:
    rect = RECT()
    GetWindowRect(hwnd, ctypes.byref(rect))
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width <= 0 or height <= 0:
        raise ValueError("窗口尺寸无效")
    hdc_screen = GetDC(0)
    hdc_mem = CreateCompatibleDC(hdc_screen)
    painted: list[int] = []

    def draw(target):
        # 必须画进 _capture 即将读的那张 DIB：mem DC 刚建时选中的是 1x1 单色位图，
        # 先 PrintWindow 再另起 DIB 的话，PrintWindow 的内容被丢掉、回读恒为全黑。
        painted.append(PrintWindow(hwnd, target, 2))

    pixels = _capture(hdc_mem, width, height, draw)
    DeleteDC(hdc_mem)
    ReleaseDC(0, hdc_screen)
    write_bmp(path, width, height, pixels)
    return width, height, bool(painted and painted[0])


def focus(hwnd: int) -> None:
    ShowWindow(hwnd, SW_RESTORE)
    SetForegroundWindow(hwnd)


def click(x: int, y: int) -> None:
    SetCursorPos(int(x), int(y))
    time.sleep(0.12)
    mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.06)
    mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    time.sleep(0.12)


def type_text(text: str) -> None:
    for char in text:
        result = VkKeyScanA(char.encode("ascii"))
        vk = result & 0xFF
        shift = (result >> 8) & 0x01
        if vk == 0xFF:
            continue
        if shift:
            keybd_event(0x10, 0, 0, 0)
        keybd_event(vk, 0, 0, 0)
        keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
        if shift:
            keybd_event(0x10, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.02)


SPECIAL_KEYS = {
    "F11": 0x7A, "F1": 0x70, "ENTER": 0x0D, "ESC": 0x1B, "TAB": 0x09,
    "CTRL": 0x11, "ALT": 0x12, "SHIFT": 0x10, "SPACE": 0x20,
}


def press_keys(names: str) -> None:
    for name in names.split("+"):
        key = name.strip().upper()
        vk = SPECIAL_KEYS.get(key)
        if vk is None and len(key) == 1:
            vk = ord(key)
        if vk is None:
            continue
        keybd_event(vk, 0, 0, 0)
        keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.05)


def _class_name(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    GetClassNameW(hwnd, buffer, 256)
    return buffer.value


def _window_text(hwnd: int) -> str:
    length = SendMessageW(hwnd, WM_GETTEXTLENGTH, 0, 0)
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    SendMessageW(hwnd, WM_GETTEXT, length + 1, ctypes.cast(buffer, ctypes.c_void_p).value)
    return buffer.value


def child_windows(hwnd: int) -> list[dict]:
    found: list[dict] = []

    def callback(child, _lparam):
        if IsWindowVisible(child):
            rect = RECT()
            GetWindowRect(child, ctypes.byref(rect))
            found.append({
                "hwnd": child,
                "class": _class_name(child),
                "title": _window_title(child),
                "text": _window_text(child),
                "rect": (rect.left, rect.top, rect.right, rect.bottom),
            })
        return True

    EnumChildWindows(hwnd, WNDENUMPROC(callback), 0)
    return found


def find_windows(needle: str) -> list[dict]:
    """按标题/类名子串查找顶层窗口（不区分大小写）。"""
    key = needle.lower()
    return [w for w in list_windows()
            if key in w["title"].lower() or key in _class_name(w["hwnd"]).lower()]


def find_child(hwnd: int, needle: str) -> list[dict]:
    key = needle.lower()
    return [c for c in child_windows(hwnd) if key in c["text"].lower()]


def click_control(hwnd: int) -> bool:
    """用 BM_CLICK 消息触发按钮，无需把窗口提到前台。"""
    return bool(SendMessageW(hwnd, BM_CLICK, 0, 0) == 0)


def set_clipboard(text: str) -> bool:
    """把文本放入剪贴板。必须显式声明句柄参数类型，否则 64 位句柄会被截断。"""
    global_alloc = ctypes.windll.kernel32.GlobalAlloc
    global_alloc.restype = ctypes.c_void_p
    global_alloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    global_lock = ctypes.windll.kernel32.GlobalLock
    global_lock.restype = ctypes.c_void_p
    global_lock.argtypes = [ctypes.c_void_p]
    global_unlock = ctypes.windll.kernel32.GlobalUnlock
    global_unlock.restype = ctypes.c_int
    global_unlock.argtypes = [ctypes.c_void_p]
    set_clipboard_data = ctypes.windll.user32.SetClipboardData
    set_clipboard_data.restype = ctypes.c_void_p
    set_clipboard_data.argtypes = [ctypes.c_uint, ctypes.c_void_p]

    if not user32.OpenClipboard(0):
        return False
    try:
        user32.EmptyClipboard()
        encoded = text.encode("utf-16-le") + b"\x00\x00"
        handle = global_alloc(0x0042, len(encoded))
        if not handle:
            return False
        pointer = global_lock(handle)
        if not pointer:
            return False
        ctypes.memmove(pointer, encoded, len(encoded))
        global_unlock(handle)
        if not set_clipboard_data(CF_UNICODETEXT, handle):
            return False
    finally:
        user32.CloseClipboard()
    return True


def paste() -> None:
    keybd_event(0x11, 0, 0, 0)
    keybd_event(0x56, 0, 0, 0)
    keybd_event(0x56, 0, KEYEVENTF_KEYUP, 0)
    keybd_event(0x11, 0, KEYEVENTF_KEYUP, 0)


def paste_text(text: str) -> None:
    """把文本放到剪贴板后按 Ctrl+V 输入，避免逐字符敲击导致的编码与输入法问题。"""
    set_clipboard(text)
    time.sleep(0.08)
    paste()


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1
    command = argv[1].lower()
    if command == "list":
        for item in list_windows():
            if item["title"]:
                left, top, right, bottom = item["rect"]
                print(f"hwnd={item['hwnd']} {left},{top} {right - left}x{bottom - top}  {item['title']}")
        return 0
    if command == "screen":
        width, height = capture_screen(argv[2])
        print(f"screen {width}x{height} -> {argv[2]}")
        return 0
    if command == "region":
        left, top, width, height = (int(v) for v in argv[2:6])
        capture_region(argv[6], left, top, width, height)
        print(f"region {left},{top} {width}x{height} -> {argv[6]}")
        return 0
    if command == "monitors":
        print(f"virtual origin=({GetSystemMetrics(SM_XVIRTUALSCREEN)},"
              f"{GetSystemMetrics(SM_YVIRTUALSCREEN)})")
        print(f"virtual size={GetSystemMetrics(SM_CXVIRTUALSCREEN)}"
              f"x{GetSystemMetrics(SM_CYVIRTUALSCREEN)}")
        print(f"primary={GetSystemMetrics(0)}x{GetSystemMetrics(1)}")
        return 0
    if command == "find":
        for item in find_windows(argv[2]):
            left, top, right, bottom = item["rect"]
            print(f"hwnd={item['hwnd']} {left},{top} {right - left}x{bottom - top}  {item['title']}")
        return 0
    if command == "children":
        for item in child_windows(int(argv[2])):
            left, top, right, bottom = item["rect"]
            label = item["text"] or item["title"]
            print(f"hwnd={item['hwnd']} class={item['class']} {left},{top} "
                  f"{right - left}x{bottom - top}  {label!r}")
        return 0
    if command == "clickbutton":
        hits = find_child(int(argv[2]), argv[3])
        if not hits:
            print(f"no control matching {argv[3]!r}")
            return 2
        target = hits[0]
        ok = click_control(target["hwnd"])
        print(f"BM_CLICK hwnd={target['hwnd']} label={target['text']!r} result={ok}")
        return 0 if ok else 3
    if command == "paste":
        paste_text(argv[2])
        return 0
    if command == "window":
        width, height, ok = capture_window(int(argv[2]), argv[3])
        print(f"window {width}x{height} printWindow={ok} -> {argv[3]}")
        return 0
    if command == "click":
        click(int(argv[2]), int(argv[3]))
        print(f"clicked {argv[2]},{argv[3]}")
        return 0
    if command == "focus":
        focus(int(argv[2]))
        print(f"focused {argv[2]}")
        return 0
    if command == "type":
        type_text(argv[2])
        return 0
    if command == "keys":
        press_keys(argv[2])
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

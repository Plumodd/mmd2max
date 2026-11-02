"""用 UI Automation 读写 3ds Max 的启动弹窗（只点白名单里的安全按钮）。

为什么不用现成的 ctypes 那套（tools/desktop.py）：
Max 2022 的 "Startup Failure Detection" 是 Qt 窗口，实测 EnumChildWindows 返回
0 个子控件，BM_CLICK 无从下手；只能退到「截图 → 找按钮色块 → 按坐标点」，
而坐标猜测踩到过两件事：
  1. 窗口不在前台/不在屏幕上时点空（探针里那个弹窗 BoundingRectangle 在屏幕外，
     Win32 坐标法完全够不着，UIA 照样能拿到元素）；
  2. 「点最右侧按钮」并不等于安全按钮 —— 这个对话框右边那个是
     "Restore Factory Settings…"，点下去真会把用户的 Max 设置清零。
UIA 能按**按钮名**精确 Invoke，所以这里把它做成唯一通路，并且只允许调用方
传白名单里的名字；名字对不上就只回报观测到的按钮清单，什么都不点。

用法：
    from uia_dialog import dialog_buttons, invoke_safe_button
    buttons = dialog_buttons("Startup Failure")        # -> ["Continue Without Restore", ...]
    ok = invoke_safe_button("Startup Failure", "Continue Without Restore")
"""
from __future__ import annotations

import os
import subprocess

# PowerShell 侧的脚本常量：所有外部输入都走环境变量，绝不拼进脚本正文，
# 免得标题/按钮名里的引号变成命令注入点。
_PS = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$auto = [System.Windows.Automation.AutomationElement]
$root = $auto::RootElement
$winLike = '*' + $env:MMD2MAX_WIN + '*'
$want = $env:MMD2MAX_BUTTON
$doClick = $env:MMD2MAX_CLICK
$btnCond = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
    [System.Windows.Automation.ControlType]::Button)
$invokeType = [System.Windows.Automation.InvokePattern]::Pattern
$found = 0
foreach ($w in $root.FindAll([System.Windows.Automation.TreeScope]::Children,
                             [System.Windows.Automation.Condition]::TrueCondition)) {
    $name = $w.Current.Name
    if ($name -notlike $winLike) { continue }
    $found = $found + 1
    Write-Output ("WIN|" + $name + "|" + $w.Current.ClassName)
    foreach ($b in $w.FindAll([System.Windows.Automation.TreeScope]::Descendants, $btnCond)) {
        $bn = $b.Current.Name
        $en = $b.Current.IsEnabled
        Write-Output ("BTN|" + $bn + "|enabled=" + $en)
        if ($doClick -eq '1' -and $bn -eq $want -and $en) {
            try {
                ($b.GetCurrentPattern($invokeType)).Invoke()
                Write-Output ("INVOKED|" + $bn)
            } catch {
                Write-Output ("INVOKE_FAILED|" + $bn + "|" + $_.Exception.Message)
            }
        }
    }
}
if ($found -eq 0) { Write-Output 'NO_WINDOW' }
"""


def _call(win: str, button: str, click: bool) -> list[str]:
    env = dict(os.environ)
    env.update({
        "MMD2MAX_WIN": win,
        "MMD2MAX_BUTTON": button,
        "MMD2MAX_CLICK": "1" if click else "0",
    })
    try:
        done = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS],
            env=env, capture_output=True, text=True, errors="replace", timeout=60,
        )
    except Exception as error:  # noqa: BLE001
        return [f"ERROR|{error}"]
    lines = [ln for ln in (done.stdout or "").splitlines() if ln.strip()]
    if not lines and done.returncode != 0:
        lines = [f"ERROR|{(done.stderr or '').strip()[:300]}"]
    return lines


def parse_lines(lines: list[str]) -> dict:
    """把 PowerShell 的输出整理成 {windows, buttons, invoked, error}。"""
    result = {"windows": [], "buttons": [], "invoked": None, "error": None}
    for line in lines:
        parts = line.split("|")
        head = parts[0]
        if head == "WIN" and len(parts) >= 3:
            result["windows"].append({"name": parts[1], "class": parts[2]})
        elif head == "BTN" and len(parts) >= 3:
            result["buttons"].append({"name": parts[1], "enabled": "true" in parts[2].lower()})
        elif head == "INVOKED" and len(parts) >= 2:
            result["invoked"] = parts[1]
        elif head == "INVOKE_FAILED" and len(parts) >= 3:
            result["error"] = f"Invoke 失败：{parts[1]} {parts[2]}"
        elif head == "ERROR" and len(parts) >= 2:
            result["error"] = parts[1]
    return result


def dialog_buttons(win: str) -> list[str]:
    """列出标题含 win 的顶层窗口里所有按钮名（只读，不点任何东西）。"""
    buttons = parse_lines(_call(win, "", False))["buttons"]
    return [b["name"] for b in buttons]


def invoke_safe_button(win: str, button: str) -> tuple[bool, dict]:
    """按**确切按钮名**Invoke。名字不在窗口里就什么都不做。

    返回 (是否点成功, 解析后的观测结果)，调用方拿 buttons 去写日志，
    这样「为什么没点到」是可查的，而不是又一次静默失败。
    """
    info = parse_lines(_call(win, button, True))
    return info["invoked"] == button, info

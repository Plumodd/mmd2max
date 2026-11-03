@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

rem 找 Python：python.org 的 py 启动器 -> PATH 里的 python -> 开发机的私有路径。
rem 顺序不能反：Windows 的 `python` 可能是应用商店的占位别名，跑它会跳商店而不是报错。
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>&1
if not errorlevel 1 (
    py -3 tools\gui.py %*
    goto :eof
)

python -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>&1
if not errorlevel 1 (
    python tools\gui.py %*
    goto :eof
)

if exist "%USERPROFILE%\.local\bin\python3.12.exe" (
    "%USERPROFILE%\.local\bin\python3.12.exe" tools\gui.py %*
    goto :eof
)

echo 没找到 Python 3.8+（试过 py -3、python、%%USERPROFILE%%\.local\bin）。
echo 装一个 https://www.python.org/downloads/windows/ ，安装时勾 Add python.exe to PATH，再双击本文件。
pause

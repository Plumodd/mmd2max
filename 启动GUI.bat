@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

rem Launcher for the v4 test package. Keep this file ASCII-only with CRLF line
rem endings: cmd.exe re-reads batch files by byte offset, so LF-only endings or a
rem non-ASCII comment can split one line into two commands and the double-click
rem then either does nothing or prints "is not recognized as an internal or
rem external command" (that is how the 2026-11-05 package failed). The Chinese
rem rationale lives in README_V4 section 7 instead.
rem Python lookup order matters: try the py launcher first, because the plain
rem "python" name on Windows may be the Microsoft Store alias, which opens the
rem Store instead of returning an error.
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

echo Python 3.8 or newer was not found. Tried: py -3, python, and %USERPROFILE%\.local\bin.
echo Install it from https://www.python.org/downloads/windows/ and tick
echo "Add python.exe to PATH", then double-click this file again.
pause

@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set "PY=%USERPROFILE%\.local\bin\python3.12.exe"
if not exist "%PY%" set "PY=python"
"%PY%" tools\gui.py %*

@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
python verify_expected.py
pause

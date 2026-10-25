@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_taxiops.ps1"
if errorlevel 1 echo.
if errorlevel 1 echo Failed. Read the messages above.
pause

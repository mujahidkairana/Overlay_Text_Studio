@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
title Overlay Text Studio v3 - Shared Setup
if not exist logs mkdir logs
echo ============================================================
echo Overlay Text Studio v3 - Install or Repair
echo ============================================================
echo Installed components are reused first.
echo Cached downloads are used second.
echo Internet is used only when neither is available.
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0shared_setup.ps1" -SharedRoot "%~1" > "%~dp0logs\setup_console.log" 2>&1
set "SETUP_EXIT=%ERRORLEVEL%"
type "%~dp0logs\setup_console.log"
if not "%SETUP_EXIT%"=="0" (
    echo.
    echo [ERROR] Setup did not complete. Review logs\setup_console.log.
    pause
    exit /b %SETUP_EXIT%
)
echo.
echo Setup completed. Use START_APP.bat from now on.
pause
exit /b 0

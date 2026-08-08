@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
title Overlay Text Studio v2
if not exist logs mkdir logs
if not exist runtime_path.txt (
    echo Setup has not completed yet. Starting SETUP_ONCE.bat...
    call SETUP_ONCE.bat
    if errorlevel 1 exit /b 1
)
set /p "RUNTIME_ROOT=" < runtime_path.txt
if not exist "%RUNTIME_ROOT%\Scripts\python.exe" (
    echo Shared runtime is missing or moved. Starting repair...
    call SETUP_ONCE.bat
    if errorlevel 1 exit /b 1
    set /p "RUNTIME_ROOT=" < runtime_path.txt
)
"%RUNTIME_ROOT%\Scripts\python.exe" start_overlay.py
set "APP_EXIT=%ERRORLEVEL%"
if not "%APP_EXIT%"=="0" (
    echo The app could not start. Review logs\streamlit.log.
    pause
)
exit /b %APP_EXIT%

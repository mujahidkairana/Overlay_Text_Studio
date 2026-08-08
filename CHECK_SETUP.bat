@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist runtime_path.txt (
  echo Setup has not completed. Run SETUP_ONCE.bat first.
  pause
  exit /b 1
)
set /p "RUNTIME_ROOT=" < runtime_path.txt
"%RUNTIME_ROOT%\Scripts\python.exe" system_check.py
set "CHECK_EXIT=%ERRORLEVEL%"
pause
exit /b %CHECK_EXIT%

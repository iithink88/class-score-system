@echo off
chcp 936 >nul 2>&1
title Class Score System v2.0
echo.
echo  ========================================
echo    Class Score System Starting...
echo  ========================================
echo.

set "PY_EXE="

REM Try WorkBuddy managed Python first
if exist "C:\Users\lenovo\.workbuddy\binaries\python\envs\default\Scripts\python.exe" (
    set "PY_EXE=C:\Users\lenovo\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
    goto :FOUND
)

REM Try system python
python --version >nul 2>&1
if %errorlevel% == 0 (
    set "PY_EXE=python"
    goto :FOUND
)

REM Try python3
python3 --version >nul 2>&1
if %errorlevel% == 0 (
    set "PY_EXE=python3"
    goto :FOUND
)

echo  [ERROR] Python not found!
echo  Please install Python 3.8+
echo.
pause
exit /b 1

:FOUND
echo  Using Python: %PY_EXE%
echo  Starting server, please wait...
echo  Browser will open automatically
echo  If not, open http://127.0.0.1:8099 manually
echo.
echo  Press Ctrl+C to stop server
echo  ----------------------------------------

cd /d "%~dp0"
"%PY_EXE%" server.py

pause

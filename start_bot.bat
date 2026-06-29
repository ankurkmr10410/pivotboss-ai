@echo off
title PivotBoss AI Trading Bot
cd /d D:\pivotboss-ai
chcp 65001 > nul

echo.
echo  ================================
echo   PivotBoss AI - Starting up...
echo  ================================
echo.

:: Kill any existing instance on port 8000
echo Checking for existing instance...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8000 2^>nul') do (
    echo Stopping previous instance PID %%a
    taskkill /PID %%a /F >nul 2>&1
)
timeout /t 2 /nobreak >nul

:: Show Python version to confirm it works
echo Checking Python...
python --version
if errorlevel 1 (
    echo ERROR: Python not found. Make sure Python is installed and in PATH.
    pause
    exit /b 1
)

:: Check if run.py exists
if not exist "run.py" (
    echo ERROR: run.py not found in D:\pivotboss-ai
    echo Make sure you are running from the correct folder.
    pause
    exit /b 1
)

echo.
echo Starting PivotBoss AI...
echo.

:: Run the bot - window stays open on error
python run.py --login
if errorlevel 1 (
    echo.
    echo ERROR: Bot stopped with an error. Check the output above.
)

echo.
echo Bot has stopped. Press any key to close...
pause

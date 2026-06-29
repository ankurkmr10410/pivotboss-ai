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
    echo Stopping previous instance (PID %%a)...
    taskkill /PID %%a /F >nul 2>&1
)
timeout /t 2 /nobreak >nul

:: Now start fresh
echo Starting PivotBoss AI...
python run.py --login

pause

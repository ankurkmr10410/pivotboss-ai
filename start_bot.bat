@echo off
title PivotBoss AI Trading Bot
cd /d D:\pivotboss-ai

:: Force UTF-8 output
chcp 65001 > nul

echo.
echo  ================================
echo   PivotBoss AI - Starting up...
echo  ================================
echo.

python run.py --login
pause

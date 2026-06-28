@echo off
title PivotBoss AI - Auto Start
cd /d D:\pivotboss-ai
chcp 65001 > nul

echo [%date% %time%] PivotBoss AI starting... >> logs\startup.log

:: If KOTAK_TOTP_SEED is set in .env, this runs fully unattended
python run.py --live >> logs\bot.log 2>&1

echo [%date% %time%] PivotBoss AI stopped. >> logs\startup.log

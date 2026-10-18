@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  python app\launcher.py
) else (
  py -3 app\launcher.py
)
if errorlevel 1 pause

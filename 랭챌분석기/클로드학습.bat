@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -3 app\claude_ui.py
if errorlevel 1 python app\claude_ui.py
if errorlevel 1 pause

@echo off
chcp 65001 >nul
cd /d "%~dp0"
set FORMAT_HUB_OFFLINE=1
format-hub.exe --offline --host 127.0.0.1
if errorlevel 1 pause

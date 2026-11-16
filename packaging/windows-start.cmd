@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在检查并准备 Office 引擎，首次需联网下载官方安装包……
format-hub.exe --install-engine windows-office
if errorlevel 1 (
  echo Office 引擎准备失败，图片、PDF、音视频、CAD 工具仍可使用。
  echo 可稍后重试，或按 Ctrl+C 退出。
  pause
)
format-hub.exe --host 0.0.0.0
pause

@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3.12 -m venv .venv
  if errorlevel 1 goto error
  .venv\Scripts\python.exe -m pip install -r requirements.txt
  if errorlevel 1 goto error
)
.venv\Scripts\python.exe run.py %*
if errorlevel 1 goto error
exit /b 0
:error
echo Startup failed. Install Python 3.12 and check the error above.
pause
exit /b 1

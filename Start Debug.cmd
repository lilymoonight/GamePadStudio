@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Please create the .venv environment and install requirements.txt first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" "%~dp0scripts\start_debug.py" %*

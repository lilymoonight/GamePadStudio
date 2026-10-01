@echo off
cd /d "%~dp0"
if exist "%LOCALAPPDATA%\Programs\GamePadStudio\GamePadStudio.exe" (
  start "" "%LOCALAPPDATA%\Programs\GamePadStudio\GamePadStudio.exe"
  exit /b
)
if exist "dist\GamePadStudio\GamePadStudio.exe" (
  start "" "dist\GamePadStudio\GamePadStudio.exe" --data-dir "%~dp0studio-data"
) else (
  start "" ".venv\Scripts\pythonw.exe" "%~dp0main.py" --data-dir "%~dp0studio-data"
)

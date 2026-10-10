@echo off
cd /d "%~dp0"
if exist "dist\CS2Music\CS2Music.exe" (
  start "" "dist\CS2Music\CS2Music.exe"
  exit /b
)
if not exist ".venv\Scripts\python.exe" (
  echo Please run setup.ps1 first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" main.py
pause

@echo off
setlocal
cd /d "%~dp0"

set "PYTHON=python"
if exist "%LocalAppData%\Python\pythoncore-3.14-64\python.exe" (
    set "PYTHON=%LocalAppData%\Python\pythoncore-3.14-64\python.exe"
)

"%PYTHON%" "%~dp0cs2_result_music.py" --config "%~dp0config.json" --capture-gsi "%~dp0work\gsi-capture.jsonl" --verbose 1>>"%~dp0work\listener-service.stdout.log" 2>>"%~dp0work\listener-service.stderr.log"
endlocal

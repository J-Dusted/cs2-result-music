@echo off
setlocal
cd /d "%~dp0"
python "%~dp0cs2_result_music.py" --config "%~dp0config.json" --capture-gsi "%~dp0work\gsi-capture.jsonl" --verbose
endlocal

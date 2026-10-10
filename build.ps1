$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
& .\.venv\Scripts\python.exe scripts\build.py
if ($LASTEXITCODE -ne 0) { throw "Build failed." }

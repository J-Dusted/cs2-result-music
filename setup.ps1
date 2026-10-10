param([string]$Python = "python", [string]$IndexUrl = "https://pypi.org/simple")
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe")) {
    & $Python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Python venv failed. Install Python 3.12 x64 from python.org." }
}
& .\.venv\Scripts\python.exe -m pip install --index-url $IndexUrl -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { throw "Dependency install failed." }
& .\.venv\Scripts\python.exe scripts\vendor_rnnoise.py
if ($LASTEXITCODE -ne 0) { throw "RNNoise download failed." }
Write-Host "Ready: .\start.cmd"

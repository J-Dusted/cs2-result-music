$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$startup = [Environment]::GetFolderPath("Startup")
$launcher = Join-Path $startup "CS2 Result Music.cmd"
$content = @"
@echo off
cd /d "$root"
start "" /min "$root\start.bat"
"@
[IO.File]::WriteAllText(
    $launcher,
    $content,
    (New-Object Text.UTF8Encoding($false)))
Write-Host "Autostart installed: $launcher"
Write-Host "The controller will start after the next user logon."

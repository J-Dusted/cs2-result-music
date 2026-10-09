$ErrorActionPreference = "Stop"
$startup = [Environment]::GetFolderPath("Startup")
$launcher = Join-Path $startup "CS2 Result Music.cmd"
if (Test-Path -LiteralPath $launcher) {
    Remove-Item -LiteralPath $launcher -Force
    Write-Host "Autostart removed: $launcher"
} else {
    Write-Host "Autostart was not installed."
}

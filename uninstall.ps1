param(
    [switch]$RemoveState,
    [string]$CaptureDeviceGuid = "{0be43164-1b92-4f6d-8c30-29243a0040ed}"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$apoRoot = "C:\Program Files\EqualizerAPO"
$apoConfig = Join-Path $apoRoot "config\config.txt"
$includeFile = Join-Path $apoRoot "config\cs2_result_music.txt"
$pluginFile = Join-Path $apoRoot "VSTPlugins\CS2MicInjector.dll"
$stateDirectory = "C:\ProgramData\CS2ResultAudio"
$beginMarker = "# CS2 Result Music begin"
$endMarker = "# CS2 Result Music end"

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this uninstaller from an elevated PowerShell window."
}

& (Join-Path $root "unregister-apo.ps1") -DeviceGuid $CaptureDeviceGuid

if (Test-Path -LiteralPath $apoConfig) {
    $text = [IO.File]::ReadAllText($apoConfig)
    $pattern = "(?ms)^" + [regex]::Escape($beginMarker) +
        "\r?\n.*?^" + [regex]::Escape($endMarker) + "\r?\n?"
    $updated = [regex]::Replace($text, $pattern, "")
    [IO.File]::WriteAllText(
        $apoConfig,
        $updated,
        (New-Object Text.UTF8Encoding($false)))
}

foreach ($path in @($includeFile, $pluginFile)) {
    if (Test-Path -LiteralPath $path) {
        Remove-Item -LiteralPath $path -Force
    }
}
if ($RemoveState -and (Test-Path -LiteralPath $stateDirectory)) {
    Remove-Item -LiteralPath $stateDirectory -Recurse -Force
}
Write-Host "CS2 Result Music APO files removed."

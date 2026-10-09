param(
    [string]$DeviceGuid = "{0be43164-1b92-4f6d-8c30-29243a0040ed}"
)

$ErrorActionPreference = "Stop"

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this unregistration script from an elevated PowerShell window."
}

$equalizerPreMix = "{EACD2258-FCAC-4FF4-B36D-419E924A6D79}"
$fxKey =
    "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Capture" +
    "\$DeviceGuid\FxProperties"
$childRoot = "HKLM:\SOFTWARE\EqualizerAPO\Child APOs"
$childKey = Join-Path $childRoot $DeviceGuid
$stateDirectory = "C:\ProgramData\CS2ResultAudio"
$backupPath = Join-Path $stateDirectory "apo-registration-backup.json"
$valueNames = [ordered]@{
    Lfx = "{d04e05a6-594b-4fb6-a80d-01af5eed7d1d},1"
    Gfx = "{d04e05a6-594b-4fb6-a80d-01af5eed7d1d},2"
    Sfx = "{d04e05a6-594b-4fb6-a80d-01af5eed7d1d},5"
    Mfx = "{d04e05a6-594b-4fb6-a80d-01af5eed7d1d},6"
    Efx = "{d04e05a6-594b-4fb6-a80d-01af5eed7d1d},7"
}

if (-not (Test-Path -LiteralPath $fxKey)) {
    Write-Host "Capture endpoint FxProperties not found; nothing to remove."
    exit 0
}

$originalValues = $null
if (Test-Path -LiteralPath $childKey) {
    $child = Get-ItemProperty -LiteralPath $childKey
    $originalValues = [ordered]@{}
    foreach ($entry in $valueNames.GetEnumerator()) {
        if ($null -ne $child.PSObject.Properties[$entry.Value]) {
            $originalValues[$entry.Key] = [string]$child.($entry.Value)
        }
    }
}

if ($originalValues -eq $null -and (Test-Path -LiteralPath $backupPath)) {
    $backup = Get-Content -LiteralPath $backupPath -Raw | ConvertFrom-Json
    $originalValues = [ordered]@{}
    foreach ($entry in $valueNames.GetEnumerator()) {
        $originalValues[$entry.Key] =
            [string]$backup.original_values.($entry.Key)
    }
}

if ($originalValues -ne $null) {
    foreach ($entry in $valueNames.GetEnumerator()) {
        $original = [string]$originalValues[$entry.Key]
        if ($original -eq "!KEY") {
            continue
        }
        if ($original -eq "!VALUE" -or [string]::IsNullOrEmpty($original)) {
            Remove-ItemProperty `
                -LiteralPath $fxKey `
                -Name $entry.Value `
                -ErrorAction SilentlyContinue
        } else {
            New-ItemProperty `
                -LiteralPath $fxKey `
                -Name $entry.Value `
                -Value $original `
                -PropertyType String `
                -Force | Out-Null
        }
    }
} else {
    $current = Get-ItemProperty -LiteralPath $fxKey
    foreach ($entry in $valueNames.GetEnumerator()) {
        if ($null -ne $current.PSObject.Properties[$entry.Value] -and
            [string]$current.($entry.Value) -eq $equalizerPreMix) {
            Remove-ItemProperty `
                -LiteralPath $fxKey `
                -Name $entry.Value `
                -ErrorAction SilentlyContinue
        }
    }
}

if (Test-Path -LiteralPath $childKey) {
    Remove-Item -LiteralPath $childKey -Recurse -Force
}
if ((Test-Path -LiteralPath $childRoot) -and
    -not (Get-ChildItem -LiteralPath $childRoot -ErrorAction SilentlyContinue)) {
    Remove-Item -LiteralPath $childRoot -Force
}
if (Test-Path -LiteralPath $backupPath) {
    Remove-Item -LiteralPath $backupPath -Force
}

Write-Host "Equalizer APO capture registration removed from $DeviceGuid."
Write-Host "Reboot Windows to fully unload the APO."

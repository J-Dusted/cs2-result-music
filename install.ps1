param(
    [switch]$SkipGsi,
    [switch]$SkipApoDeviceRegistration,
    [string]$CsgoCfgDirectory,
    [string]$CaptureDeviceGuid = "{0be43164-1b92-4f6d-8c30-29243a0040ed}"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$pluginSource = Join-Path $root "bin\CS2MicInjector.dll"
$playerSource = Join-Path $root "bin\CS2LocalPlayback.exe"
$configPath = Join-Path $root "config.json"
$apoRoot = "C:\Program Files\EqualizerAPO"
$apoConfig = Join-Path $apoRoot "config\config.txt"
$includeSource = Join-Path $root "deploy\cs2_result_music.txt"
$includeDestination = Join-Path $apoRoot "config\cs2_result_music.txt"
$vstDirectory = Join-Path $apoRoot "VSTPlugins"
$stateDirectory = "C:\ProgramData\CS2ResultAudio"
$musicRoot = Join-Path $root "music"
$beginMarker = "# CS2 Result Music begin"
$endMarker = "# CS2 Result Music end"

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this installer from an elevated PowerShell window."
}

if (-not (Test-Path -LiteralPath $pluginSource)) {
    throw "Missing $pluginSource. Run build.ps1 first."
}
if (-not (Test-Path -LiteralPath $playerSource)) {
    throw "Missing $playerSource. Run build.ps1 first."
}
if (-not (Test-Path -LiteralPath $apoRoot)) {
    throw "Equalizer APO is not installed at $apoRoot."
}

New-Item -ItemType Directory -Force -Path $vstDirectory | Out-Null
New-Item -ItemType Directory -Force -Path $stateDirectory | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $includeDestination) |
    Out-Null
foreach ($eventDirectory in @("win", "lose", "start", "mvp", "bomb10")) {
    New-Item -ItemType Directory -Force -Path (Join-Path $musicRoot $eventDirectory) |
        Out-Null
}

Copy-Item -LiteralPath $pluginSource -Destination (Join-Path $vstDirectory "CS2MicInjector.dll") -Force
Copy-Item -LiteralPath $includeSource -Destination $includeDestination -Force

$stateAcl = Get-Acl -LiteralPath $stateDirectory
$localService = New-Object Security.Principal.SecurityIdentifier("S-1-5-19")
$currentUser = $identity.User
foreach ($account in @($localService, $currentUser)) {
    $rule = New-Object Security.AccessControl.FileSystemAccessRule(
        $account,
        "Modify",
        "ContainerInherit,ObjectInherit",
        "None",
        "Allow")
    $stateAcl.SetAccessRule($rule)
}
Set-Acl -LiteralPath $stateDirectory -AclObject $stateAcl

$configText = if (Test-Path -LiteralPath $apoConfig) {
    [IO.File]::ReadAllText($apoConfig)
} else {
    ""
}
$backup = "$apoConfig.cs2-result-music.bak"
if ((Test-Path -LiteralPath $apoConfig) -and -not (Test-Path -LiteralPath $backup)) {
    Copy-Item -LiteralPath $apoConfig -Destination $backup -Force
}
if ($configText -notmatch [regex]::Escape($beginMarker)) {
    $suffix = if ($configText.Length -gt 0 -and -not $configText.EndsWith("`n")) {
        "`r`n"
    } else {
        ""
    }
    $configText += "$suffix$beginMarker`r`nInclude: cs2_result_music.txt`r`n$endMarker`r`n"
    [IO.File]::WriteAllText(
        $apoConfig,
        $configText,
        (New-Object Text.UTF8Encoding($false)))
}

if (-not $SkipGsi) {
    $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
    if (-not $config.gsi.token -or $config.gsi.token -eq "TOKEN") {
        $bytes = New-Object byte[] 16
        $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
        try {
            $rng.GetBytes($bytes)
        } finally {
            $rng.Dispose()
        }
        $config.gsi.token = -join ($bytes | ForEach-Object { $_.ToString("x2") })
        [IO.File]::WriteAllText(
            $configPath,
            ($config | ConvertTo-Json -Depth 20),
            (New-Object Text.UTF8Encoding($false)))
    }
    $python = (Get-Command python -ErrorAction Stop).Source
    $arguments = @(
        (Join-Path $root "cs2_result_music.py"),
        "--config",
        $configPath,
        "--install-gsi"
    )
    if ($CsgoCfgDirectory) {
        $arguments += @("--csgo-cfg", $CsgoCfgDirectory)
    }
    & $python @arguments
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "The GSI file was not installed automatically."
    }
}

if (-not $SkipApoDeviceRegistration) {
    & (Join-Path $root "register-apo.ps1") `
        -DeviceGuid $CaptureDeviceGuid `
        -ExpectedDeviceName "HECATE G2 GAMING HEADSET"
}

Write-Host ""
Write-Host "Installation files copied."
Write-Host "The HECATE microphone capture endpoint is registered."
Write-Host "Reboot Windows, then start with start.bat."
Write-Host ""
Write-Host "TeamSpeak remains set to the HECATE microphone. Do not select a virtual cable."

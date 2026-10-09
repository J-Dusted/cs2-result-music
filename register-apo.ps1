param(
    [string]$DeviceGuid = "{0be43164-1b92-4f6d-8c30-29243a0040ed}",
    [string]$ExpectedDeviceName = "HECATE G2 GAMING HEADSET"
)

$ErrorActionPreference = "Stop"

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this registration script from an elevated PowerShell window."
}

$equalizerPreMix = "{EACD2258-FCAC-4FF4-B36D-419E924A6D79}"
$defaultProcessingMode = "{C18E2F7E-933D-4965-B7D1-1EEF228D2AF3}"
$captureRoot = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Capture"
$deviceKey = Join-Path $captureRoot $DeviceGuid
$propertiesKey = Join-Path $deviceKey "Properties"
$fxKey = Join-Path $deviceKey "FxProperties"
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
$multiValueNames = @(
    "{d04e05a6-594b-4fb6-a80d-01af5eed7d1d},13",
    "{d04e05a6-594b-4fb6-a80d-01af5eed7d1d},14",
    "{d04e05a6-594b-4fb6-a80d-01af5eed7d1d},15"
)
$sfxProcessingModes = "{d3993a3f-99c2-4402-b5ec-a92a0367664b},5"
$disableEnhancements = "{1da5d803-d492-4edd-8c23-e0c0ffee7f0e},5"

if (-not ("Cs2RegistryPrivilege" -as [type])) {
    Add-Type @"
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;

public static class Cs2RegistryPrivilege
{
    [StructLayout(LayoutKind.Sequential)]
    private struct Luid
    {
        public uint LowPart;
        public int HighPart;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct TokenPrivileges
    {
        public uint PrivilegeCount;
        public Luid Luid;
        public uint Attributes;
    }

    [DllImport("advapi32.dll", SetLastError = true)]
    private static extern bool OpenProcessToken(
        IntPtr processHandle,
        uint desiredAccess,
        out IntPtr tokenHandle);

    [DllImport("advapi32.dll", SetLastError = true)]
    private static extern bool LookupPrivilegeValue(
        string systemName,
        string name,
        out Luid luid);

    [DllImport("advapi32.dll", SetLastError = true)]
    private static extern bool AdjustTokenPrivileges(
        IntPtr tokenHandle,
        bool disableAllPrivileges,
        ref TokenPrivileges newState,
        uint bufferLength,
        IntPtr previousState,
        IntPtr returnLength);

    [DllImport("kernel32.dll")]
    private static extern bool CloseHandle(IntPtr handle);

    public static void Enable(string name)
    {
        IntPtr token;
        if (!OpenProcessToken(
                System.Diagnostics.Process.GetCurrentProcess().Handle,
                0x0020 | 0x0008,
                out token))
        {
            throw new Win32Exception(Marshal.GetLastWin32Error());
        }
        try
        {
            Luid luid;
            if (!LookupPrivilegeValue(null, name, out luid))
            {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            TokenPrivileges privileges = new TokenPrivileges();
            privileges.PrivilegeCount = 1;
            privileges.Luid = luid;
            privileges.Attributes = 0x00000002;
            if (!AdjustTokenPrivileges(
                    token,
                    false,
                    ref privileges,
                    0,
                    IntPtr.Zero,
                    IntPtr.Zero))
            {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            if (Marshal.GetLastWin32Error() == 1300)
            {
                throw new Win32Exception(1300);
            }
        }
        finally
        {
            CloseHandle(token);
        }
    }
}
"@
}

function Grant-AdministratorFullControl {
    param([string]$ProviderPath)

    $subKey = $ProviderPath -replace "^HKLM:\\", ""
    [Cs2RegistryPrivilege]::Enable("SeTakeOwnershipPrivilege")

    $takeOwnership =
        [Security.AccessControl.RegistryRights]::TakeOwnership
    $key = [Microsoft.Win32.Registry]::LocalMachine.OpenSubKey(
        $subKey,
        [Microsoft.Win32.RegistryKeyPermissionCheck]::ReadWriteSubTree,
        $takeOwnership)
    if ($null -eq $key) {
        throw "Could not open registry key for ownership: $ProviderPath"
    }
    try {
        $acl = $key.GetAccessControl(
            [Security.AccessControl.AccessControlSections]::Owner)
        $administrators =
            New-Object Security.Principal.NTAccount("BUILTIN\Administrators")
        $acl.SetOwner($administrators)
        $key.SetAccessControl($acl)
    } finally {
        $key.Close()
    }

    $changePermissions =
        [Security.AccessControl.RegistryRights]::ChangePermissions
    $key = [Microsoft.Win32.Registry]::LocalMachine.OpenSubKey(
        $subKey,
        [Microsoft.Win32.RegistryKeyPermissionCheck]::ReadWriteSubTree,
        $changePermissions)
    if ($null -eq $key) {
        throw "Could not open registry key for permissions: $ProviderPath"
    }
    try {
        $acl = $key.GetAccessControl()
        $rule = New-Object Security.AccessControl.RegistryAccessRule(
            $administrators,
            [Security.AccessControl.RegistryRights]::FullControl,
            [Security.AccessControl.InheritanceFlags]::ContainerInherit,
            [Security.AccessControl.PropagationFlags]::None,
            [Security.AccessControl.AccessControlType]::Allow)
        $acl.SetAccessRule($rule)
        $key.SetAccessControl($acl)
    } finally {
        $key.Close()
    }
}

if (-not (Test-Path -LiteralPath $fxKey)) {
    throw "The capture endpoint has no FxProperties key: $DeviceGuid"
}

$properties = Get-ItemProperty -LiteralPath $propertiesKey
$connectionName = [string]$properties."{a45c254e-df1c-4efd-8020-67d146a850e0},2"
$deviceName = [string]$properties."{b3f8fa53-0004-438e-9003-51a46e139bfc},6"
if ($deviceName -notlike "*$ExpectedDeviceName*") {
    throw "Endpoint $DeviceGuid is '$connectionName / $deviceName', not '$ExpectedDeviceName'."
}

function Test-RegistryValue {
    param(
        [string]$Path,
        [string]$Name
    )
    $item = Get-ItemProperty -LiteralPath $Path
    return $null -ne $item.PSObject.Properties[$Name]
}

$currentFx = Get-ItemProperty -LiteralPath $fxKey
$originalValues = [ordered]@{}
foreach ($entry in $valueNames.GetEnumerator()) {
    if (Test-RegistryValue -Path $fxKey -Name $entry.Value) {
        $originalValues[$entry.Key] = [string]$currentFx.($entry.Value)
    } else {
        $originalValues[$entry.Key] = "!VALUE"
    }
}

foreach ($entry in $valueNames.GetEnumerator()) {
    if ($originalValues[$entry.Key] -eq $equalizerPreMix) {
        Write-Host "Equalizer APO is already registered on the HECATE microphone."
        exit 0
    }
}

$hasLfxOrGfx =
    $originalValues.Lfx -ne "!VALUE" -or $originalValues.Gfx -ne "!VALUE"
$hasSfxMfxEfx =
    $originalValues.Sfx -ne "!VALUE" -or
    $originalValues.Mfx -ne "!VALUE" -or
    $originalValues.Efx -ne "!VALUE"
$hasMulti = $false
foreach ($name in $multiValueNames) {
    if (Test-RegistryValue -Path $fxKey -Name $name) {
        $hasMulti = $true
        break
    }
}

# This mirrors Equalizer APO DeviceSelector's capture-device selection:
# use LFX only when the driver exposes LFX/GFX without newer stages.
$installMode = if (
    $hasLfxOrGfx -and -not $hasSfxMfxEfx -and -not $hasMulti
) {
    "LFX"
} else {
    "SFX"
}

New-Item -ItemType Directory -Force -Path $stateDirectory | Out-Null
New-Item -Path $childRoot -Force | Out-Null
New-Item -Path $childKey -Force | Out-Null
Grant-AdministratorFullControl -ProviderPath $fxKey

foreach ($entry in $valueNames.GetEnumerator()) {
    New-ItemProperty `
        -LiteralPath $childKey `
        -Name $entry.Value `
        -Value ([string]$originalValues[$entry.Key]) `
        -PropertyType String `
        -Force | Out-Null
}

if ($installMode -eq "LFX") {
    $preMixName = $valueNames.Lfx
    $preMixChild = if ($originalValues.Lfx -ne "!VALUE") {
        [string]$originalValues.Lfx
    } elseif ($originalValues.Sfx -ne "!VALUE") {
        [string]$originalValues.Sfx
    } else {
        ""
    }
} else {
    $preMixName = $valueNames.Sfx
    $preMixChild = if ($originalValues.Sfx -ne "!VALUE") {
        [string]$originalValues.Sfx
    } elseif ($originalValues.Lfx -ne "!VALUE") {
        [string]$originalValues.Lfx
    } else {
        ""
    }
}

New-ItemProperty `
    -LiteralPath $childKey `
    -Name "PreMixChild" `
    -Value $preMixChild `
    -PropertyType String `
    -Force | Out-Null
New-ItemProperty `
    -LiteralPath $childKey `
    -Name "PostMixChild" `
    -Value "" `
    -PropertyType String `
    -Force | Out-Null
New-ItemProperty `
    -LiteralPath $childKey `
    -Name "AllowSilentBufferModification" `
    -Value "false" `
    -PropertyType String `
    -Force | Out-Null
New-ItemProperty `
    -LiteralPath $childKey `
    -Name "Version" `
    -Value "2" `
    -PropertyType String `
    -Force | Out-Null
Remove-ItemProperty `
    -LiteralPath $childKey `
    -Name "DisableAutomaticAdjustment" `
    -ErrorAction SilentlyContinue

if ($installMode -eq "LFX") {
    New-ItemProperty `
        -LiteralPath $fxKey `
        -Name $valueNames.Lfx `
        -Value $equalizerPreMix `
        -PropertyType String `
        -Force | Out-Null
    foreach ($name in @($valueNames.Sfx, $valueNames.Mfx, $valueNames.Efx)) {
        Remove-ItemProperty -LiteralPath $fxKey -Name $name -ErrorAction SilentlyContinue
    }
} else {
    foreach ($name in @($valueNames.Lfx, $valueNames.Gfx)) {
        Remove-ItemProperty -LiteralPath $fxKey -Name $name -ErrorAction SilentlyContinue
    }
    New-ItemProperty `
        -LiteralPath $fxKey `
        -Name $valueNames.Sfx `
        -Value $equalizerPreMix `
        -PropertyType String `
        -Force | Out-Null
    if (-not (Test-RegistryValue -Path $fxKey -Name $sfxProcessingModes)) {
        New-ItemProperty `
            -LiteralPath $fxKey `
            -Name $sfxProcessingModes `
            -Value @($defaultProcessingMode) `
            -PropertyType MultiString `
            -Force | Out-Null
    }
}

Remove-ItemProperty `
    -LiteralPath $fxKey `
    -Name $disableEnhancements `
    -ErrorAction SilentlyContinue

$backup = [ordered]@{
    device_guid = $DeviceGuid
    connection_name = $connectionName
    device_name = $deviceName
    install_mode = $installMode
    pre_mix_value_name = $preMixName
    original_values = $originalValues
    created_at = (Get-Date).ToString("o")
}
[IO.File]::WriteAllText(
    $backupPath,
    ($backup | ConvertTo-Json -Depth 10),
    (New-Object Text.UTF8Encoding($false)))

Write-Host "Registered Equalizer APO on the capture endpoint:"
Write-Host "  $connectionName / $deviceName"
Write-Host "  stage=$installMode pre-mix, GUID=$DeviceGuid"
Write-Host "  previous driver APO references are preserved for uninstall."
Write-Host "Reboot Windows before checking the live microphone chain."

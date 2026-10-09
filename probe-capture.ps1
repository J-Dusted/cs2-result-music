param(
    [int]$Seconds = 5
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech
$engine = $null
try {
    $engine = New-Object System.Speech.Recognition.SpeechRecognitionEngine
    $engine.SetInputToDefaultAudioDevice()
    $engine.RecognizeAsync()
    Write-Host "Default capture endpoint is open for $Seconds seconds."
    Write-Host "Speak or tap the HECATE microphone now."
    Start-Sleep -Seconds $Seconds
    $engine.RecognizeAsyncCancel()
} finally {
    if ($null -ne $engine) {
        $engine.Dispose()
    }
}

Start-Sleep -Seconds 2
$statusPath = "C:\ProgramData\CS2ResultAudio\status.txt"
if (Test-Path -LiteralPath $statusPath) {
    Get-Content -LiteralPath $statusPath
} else {
    Write-Warning "Plugin heartbeat was not created at $statusPath."
    Write-Warning "Open TeamSpeak, speak for a few seconds, then run --check-apo."
}

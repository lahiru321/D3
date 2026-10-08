# Generate 16 kHz mono WAVs of every line in bench/commands.txt using Windows SAPI voices.
# Synthetic speech is only for timing and a rough accuracy baseline; real accuracy
# comes from recordings of your own voice (scripts/record_commands.py).
#
# Usage: powershell -ExecutionPolicy Bypass -File scripts/make_test_audio.ps1

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech

$root = Split-Path -Parent $PSScriptRoot
$commandsFile = Join-Path $root "bench\commands.txt"
$outDir = Join-Path $root "bench\out\synthetic"
New-Item -ItemType Directory -Force $outDir | Out-Null

$format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$voices = $synth.GetInstalledVoices() | Where-Object { $_.Enabled } | ForEach-Object { $_.VoiceInfo.Name }
Write-Host "Voices: $($voices -join ', ')"

$commands = Get-Content $commandsFile | Where-Object { $_ -and -not $_.StartsWith("#") }
$i = 0
foreach ($voice in $voices) {
    $synth.SelectVoice($voice)
    $tag = ($voice -replace '[^A-Za-z]', '').ToLower()
    foreach ($cmd in $commands) {
        $i++
        $slug = ($cmd -replace "[^A-Za-z0-9]+", "_").Trim("_").ToLower()
        $path = Join-Path $outDir ("{0}__{1}.wav" -f $tag, $slug)
        $synth.SetOutputToWaveFile($path, $format)
        $synth.Speak($cmd)
    }
}
$synth.SetOutputToNull()
$synth.Dispose()
Write-Host "Wrote $i files to $outDir"

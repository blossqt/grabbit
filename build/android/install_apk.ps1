<#
    Installs the APK on a plugged-in phone and shows what the app says.

        powershell -File build/android/install_apk.ps1
        powershell -File build/android/install_apk.ps1 -Seconds 90

    adb runs on Windows even though the APK is built inside WSL, because that
    is where the phone is plugged in. -Seconds is how long to watch the log
    after launching; Ctrl+C stops it early.
#>
param(
    [string]$Apk = '',
    [int]$Seconds = 45,
    [switch]$KeepData
)

$ErrorActionPreference = 'Stop'
$adb = "$env:LOCALAPPDATA\GrabbitBuild\platform-tools-win\adb.exe"
$package = 'com.grabbit.downloader'
if (-not (Test-Path $adb)) { throw "adb is missing at $adb" }

if (-not $Apk) {
    $root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
    $Apk = (Get-ChildItem "$root\dist\android\*.apk" | Sort-Object LastWriteTime | Select-Object -Last 1).FullName
}
if (-not $Apk -or -not (Test-Path $Apk)) { throw 'no APK found - run build/android/build_apk.sh first' }
"APK: $Apk ({0:N1} MB)" -f ((Get-Item $Apk).Length / 1MB)

& $adb devices | Select-Object -Skip 1 | Where-Object { $_ -match '\S' } | ForEach-Object { "device: $_" }

if (-not $KeepData) {
    "`n=== removing any previous install ==="
    & $adb uninstall $package 2>&1 | ForEach-Object { "  $_" }
}

"`n=== installing ==="
& $adb install -r $Apk 2>&1 | ForEach-Object { "  $_" }

"`n=== the tools it will run (these have to be executable) ==="
& $adb shell "ls -l `$(pm path $package | head -1 | sed 's/package://' | xargs dirname)/lib/arm64/ 2>/dev/null || echo '(none unpacked)'"

"`n=== launching ==="
& $adb shell "am start -n $package/org.kivy.android.PythonActivity" | ForEach-Object { "  $_" }
& $adb logcat -c

"`n=== log for $Seconds seconds (python, and anything that crashes) ==="
$job = Start-Job -ScriptBlock {
    param($adb)
    & $adb logcat -v brief python:D PythonActivity:D AndroidRuntime:E DEBUG:E *:S
} -ArgumentList $adb
Start-Sleep -Seconds $Seconds
Receive-Job $job | ForEach-Object { $_ }
Stop-Job $job; Remove-Job $job

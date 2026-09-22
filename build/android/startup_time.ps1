<#
    Times opening the installed app from cold - nothing of it running - to
    its first frame on screen, which is the wait someone touching its icon
    sits through.

        powershell -File build\android\startup_time.ps1 [-Runs 5]

    Each run stops the app, starts it the way the launcher does, and reads
    back from the phone's log the moments it passed: Python starting, Kivy
    starting, the window opening, the interface built, and the first frame,
    which the app reports itself ("[Start-up] ..." - a build from before that
    line stops at the interface being built). Everything is timed from the
    moment Android was asked to start it.

    The phone has to be unlocked and awake: Kivy waits for its window.
#>
param([int]$Runs = 5)

$adb = "$env:LOCALAPPDATA\GrabbitBuild\platform-tools-win\adb.exe"
$package = 'com.grabbit.downloader'
$activity = "$package/org.kivy.android.PythonActivity"

# What each moment looks like in the log, in the order they come.
$marks = [ordered]@{
    'Python'      = 'Initializing Python for Android'
    'Kivy'        = '\[Kivy\s*\] v\d'
    'window'      = '\[Window\s*\] Provider:'
    'built'       = 'Start application main loop'
    'first frame' = '\[Start-up\s*\]'
    'engine'      = 'aria2 \S+ started on port'
    'yt-dlp'      = '\[Warm-up\s*\] yt-dlp loaded'
}

function Stamp($line) {
    # "09-21 23:40:01.123 ..." as seconds into the day.
    if ("$line" -match '^\d\d-\d\d (\d\d):(\d\d):(\d\d\.\d+)') {
        return [int]$Matches[1] * 3600 + [int]$Matches[2] * 60 + [double]$Matches[3]
    }
    return $null
}

function Median($values) {
    $sorted = @($values | Sort-Object)
    if ($sorted.Count -eq 0) { return $null }
    return $sorted[[int][math]::Floor(($sorted.Count - 1) / 2)]
}

"=== device ==="
& $adb shell "getprop ro.product.model; getprop ro.build.version.release"
if (-not "$(& $adb shell "pm path $package")") { throw "$package is not installed" }
if ((& $adb shell "dumpsys window | grep -c mDreamingLockscreen=true").Trim() -ne '0') {
    throw 'the phone is locked - unlock it and run this again (Kivy waits for its window)'
}
& $adb shell "input keyevent KEYCODE_WAKEUP" | Out-Null

$all = @()
for ($run = 1; $run -le $Runs; $run++) {
    & $adb shell "am force-stop $package" | Out-Null
    Start-Sleep -Seconds 2
    & $adb logcat -c
    $am = (& $adb shell "am start -W -n $activity") -join "`n"

    # Until the first frame is reported, or - on a build that does not report
    # it - a moment after the interface is built.
    $deadline = (Get-Date).AddSeconds(45)
    $built = $null
    $log = @()
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 700
        $log = @(& $adb logcat -d -v threadtime)
        if ($log | Select-String -Pattern $marks['first frame'] -Quiet) {
            if ($log | Select-String -Pattern $marks['yt-dlp'] -Quiet) { break }
            if ($null -eq $built) { $built = Get-Date }
        } elseif ($log | Select-String -Pattern $marks['built'] -Quiet) {
            if ($null -eq $built) { $built = Get-Date }
        }
        # Give the engine and yt-dlp a while to come up after the frame.
        if ($built -and ((Get-Date) - $built).TotalSeconds -gt 8) { break }
    }

    $start = $log | Select-String -Pattern "START u0 .*cmp=$([regex]::Escape($activity))" | Select-Object -First 1
    if (-not $start) { "run ${run}: Android's start request is not in the log"; continue }
    $zero = Stamp $start.Line
    $row = [ordered]@{ Run = $run }
    if ($am -match 'TotalTime: (\d+)') { $row['splash'] = [int]$Matches[1] / 1000.0 }
    foreach ($name in $marks.Keys) {
        $hit = $log | Select-String -Pattern $marks[$name] | Select-Object -First 1
        if ($hit) { $row[$name] = [math]::Round((Stamp $hit.Line) - $zero, 2) }
    }
    $asked = [bool]($log | Select-String -Pattern 'REQUEST_PERMISSIONS' -Quiet)
    $row['asked'] = $asked
    $all += [pscustomobject]$row
    $parts = @($row.Keys | Where-Object { $_ -notin 'Run', 'asked' } | ForEach-Object { "$_ $($row[$_])s" })
    $note = ''
    if ($asked) { $note = '  (+ a permission request, which pauses and resumes it)' }
    "run ${run}: " + ($parts -join ', ') + $note
}

"`n=== median of $($all.Count) runs, seconds after Android was asked to start it ==="
foreach ($name in @('splash') + @($marks.Keys)) {
    $values = @($all | ForEach-Object { $_.$name } | Where-Object { $null -ne $_ })
    if ($values.Count) { '  {0,-12} {1,5:N2}' -f $name, (Median $values) }
}
"  'splash' is Android's own count to the splash screen; 'first frame' is the app on screen."
& $adb shell "am force-stop $package" | Out-Null

<#
    Drives the installed app the way a person would - by sharing links to it -
    and reports what ends up in the downloads folder.

        powershell -File build\android\app_test.ps1

    This is the other half of device_test.ps1. That one checks everything up to
    the network without needing the screen; this one needs the phone unlocked
    and awake, because Kivy waits for a window before it starts, and it covers
    what only the real app can: DNS, the share sheet, and the whole chain from
    a link to a finished file.
#>
param(
    [int]$Wait = 75,
    [switch]$KeepFiles
)

$adb = "$env:LOCALAPPDATA\GrabbitBuild\platform-tools-win\adb.exe"
$package = 'com.grabbit.downloader'
$activity = "$package/org.kivy.android.PythonActivity"
$folder = '/storage/emulated/0/Download/Grabbit'
$results = @()

function Report($name, $ok, $detail) {
    $script:results += [pscustomobject]@{ Name = $name; Ok = $ok }
    $mark = if ($ok) { 'PASS' } else { 'FAIL' }
    if ($detail) { "  [$mark] $name - $detail" } else { "  [$mark] $name" }
}

function Saved {
    # Names only, so a file appearing is easy to spot.
    (& $adb shell "ls $folder 2>/dev/null") | Where-Object { $_ -match '\S' } | ForEach-Object { $_.Trim() }
}

function Share($url) {
    & $adb shell "am start -a android.intent.action.SEND -t text/plain --es android.intent.extra.TEXT '$url' -n $activity" | Out-Null
}

"=== device ==="
& $adb shell "getprop ro.product.model; getprop ro.build.version.release"
if (-not "$(& $adb shell "pm path $package")") { throw "$package is not installed" }

if ((& $adb shell "dumpsys window | grep -c mDreamingLockscreen=true").Trim() -ne '0') {
    throw 'the phone is locked - unlock it and run this again (Kivy waits for a window)'
}

if (-not $KeepFiles) { & $adb shell "rm -rf $folder" | Out-Null }

"`n=== starting the app ==="
& $adb shell "am force-stop $package" | Out-Null
& $adb logcat -c
& $adb shell "input keyevent KEYCODE_WAKEUP" | Out-Null
& $adb shell "am start -n $activity" | Out-Null
Start-Sleep -Seconds 30
$engine = & $adb logcat -d -s python | Select-String 'aria2 .* started on port'
Report 'the app starts and brings up aria2' ([bool]$engine) ("$engine" -replace '.*\] ', '')

foreach ($case in @(
    @{ Name = 'a YouTube video'; Url = 'https://www.youtube.com/watch?v=jNQXAC9IVRw'; Match = '\.mp4$' },
    @{ Name = 'a TikTok'; Url = 'https://www.tiktok.com/@jade.wood/video/7434103280294300960'; Match = '\.mp4$' },
    @{ Name = 'every slide of a photo post'; Url = 'https://www.instagram.com/nasa/p/DdWojaYFDf-/'; Match = '\.jpg$' }
)) {
    "`n=== $($case.Name) ==="
    $before = @(Saved)
    Share $case.Url
    Start-Sleep -Seconds $Wait
    $new = @(Saved) | Where-Object { $_ -notin $before -and $_ -match $case.Match }
    Report $case.Name ($new.Count -gt 0) ($new -join ', ')
}

"`n=== the same video as a GIF, and as an MP3 ==="
# The format buttons sit under the link box: Video, MP3, GIF.
$width = [int]((& $adb shell "wm size") -replace '.*?(\d+)x\d+.*', '$1')
foreach ($format in @(
    @{ Name = 'MP3'; X = [int]($width * 0.5); Match = '\.mp3$' },
    @{ Name = 'GIF'; X = [int]($width * 0.82); Match = '\.gif$' }
)) {
    $before = @(Saved)
    & $adb shell "am start -n $activity" | Out-Null
    Start-Sleep -Seconds 2
    & $adb shell "input tap $($format.X) 240" | Out-Null
    Start-Sleep -Seconds 2
    Share 'https://www.youtube.com/watch?v=jNQXAC9IVRw'
    Start-Sleep -Seconds ($Wait + 45)
    $new = @(Saved) | Where-Object { $_ -notin $before -and $_ -match $format.Match }
    Report "$($format.Name) from the same link" ($new.Count -gt 0) ($new -join ', ')
}

"`n=== everything it saved ==="
& $adb shell "ls -l $folder 2>/dev/null"

"`n=== anything it complained about ==="
& $adb logcat -d -s python | Select-String 'ERROR|Traceback|RuntimeError' | Select-Object -Last 6 | ForEach-Object { "$_" }

$failed = @($results | Where-Object { -not $_.Ok })
"`n{0}/{1} checks passed" -f ($results.Count - $failed.Count), $results.Count
$failed | ForEach-Object { "  failed: $($_.Name)" }

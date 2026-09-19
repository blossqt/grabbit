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
    [int]$Wait = 150,
    [switch]$KeepFiles
)

$adb = "$env:LOCALAPPDATA\GrabbitBuild\platform-tools-win\adb.exe"
$python = "$env:LOCALAPPDATA\GrabbitBuild\venv\Scripts\python.exe"
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

function Select-Format($x, $low, $high) {
    # Tap the format chip and check it actually took. While yt-dlp is
    # extracting, the interface thread can be starved for a few seconds and a
    # tap arrives late - after the link has already been shared with the old
    # format still selected, which used to look like a broken download.
    $probe = Join-Path $env:TEMP 'grabbit-chip-probe.png'
    for ($attempt = 1; $attempt -le 8; $attempt++) {
        & $adb shell "input tap $x 326" | Out-Null
        Start-Sleep -Seconds 6
        & $adb shell "screencap -p /sdcard/chip-probe.png" | Out-Null
        & $adb pull /sdcard/chip-probe.png $probe 2>&1 | Out-Null
        $centre = & $python -c "from PIL import Image
im = Image.open(r'$probe').convert('RGB')
run = [x for x in range(im.size[0]) if im.getpixel((x, 300)) == (42, 44, 49)]
print(sum(run) // len(run) if run else -1)"
        if ([int]$centre -ge $low -and [int]$centre -le $high) { return $true }
    }
    return $false
}

function Sizes {
    (& $adb shell "ls -l $folder 2>/dev/null") -join "`n"
}

function Wait-ForFile($before, $pattern, $seconds) {
    # Poll rather than sleep for a fixed time: a download that also has to be
    # re-encoded (MP3, GIF) takes much longer than one that does not, and
    # guessing a single number either wastes minutes or fails a working app.
    #
    # And once the name appears, wait for its size to stop moving. ffmpeg
    # creates the output file before it has written it, so a name on its own
    # can mean "still being made" - which once left a scratch palette in the
    # listing and looked like a leak.
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 5
        $new = @(Saved) | Where-Object { $_ -notin $before -and $_ -match $pattern }
        if ($new.Count -gt 0) {
            $last = ''
            for ($still = 0; $still -lt 24; $still++) {
                $now = Sizes
                if ($now -eq $last) { return $new }
                $last = $now
                Start-Sleep -Seconds 5
            }
            return $new
        }
    }
    return @()
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
    $new = Wait-ForFile $before $case.Match $Wait
    Report $case.Name ($new.Count -gt 0) ($new -join ', ')
}

"`n=== the same video as a GIF, and as an MP3 ==="
# The format buttons sit under the link box: Video, MP3, GIF.
$width = [int]((& $adb shell "wm size") -replace '.*?(\d+)x\d+.*', '$1')
foreach ($format in @(
    @{ Name = 'MP3'; X = [int]($width * 0.5); Low = $width * 0.35; High = $width * 0.65; Match = '\.mp3$' },
    @{ Name = 'GIF'; X = [int]($width * 0.82); Low = $width * 0.68; High = $width * 0.99; Match = '\.gif$' }
)) {
    $before = @(Saved)
    & $adb shell "am start -n $activity" | Out-Null
    Start-Sleep -Seconds 2
    if (-not (Select-Format $format.X $format.Low $format.High)) {
        Report "$($format.Name) from the same link" $false 'the format chip never took the tap'
        continue
    }
    Share 'https://www.youtube.com/watch?v=jNQXAC9IVRw'
    # These two download and then re-encode, so they get considerably longer.
    $new = Wait-ForFile $before $format.Match ($Wait + 180)
    Report "$($format.Name) from the same link" ($new.Count -gt 0) ($new -join ', ')
}

"`n=== everything it saved ==="
& $adb shell "ls -l $folder 2>/dev/null"

"`n=== anything it complained about ==="
& $adb logcat -d -s python | Select-String 'ERROR|Traceback|RuntimeError' | Select-Object -Last 6 | ForEach-Object { "$_" }

$failed = @($results | Where-Object { -not $_.Ok })
"`n{0}/{1} checks passed" -f ($results.Count - $failed.Count), $results.Count
$failed | ForEach-Object { "  failed: $($_.Name)" }

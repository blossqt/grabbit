<#
    Drives the installed app the way a person would - by sharing links to it
    and touching Download on the page that asks what to make of each - and
    reports what ends up in the downloads folder.

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

function Get-Screen {
    # The display's size and density, as Android reports them - an override
    # (set in developer options) wins, and comes last.
    $size = (& $adb shell "wm size") | Where-Object { $_ -match '\d+x\d+' } | Select-Object -Last 1
    $density = (& $adb shell "wm density") | Where-Object { $_ -match '\d' } | Select-Object -Last 1
    $w, $h = ($size -replace '.*?(\d+)x(\d+).*', '$1 $2') -split ' '
    return @{ Width = [int]$w; Height = [int]$h; Dp = [int]($density -replace '.*?(\d+)\s*$', '$1') / 160.0 }
}

function Confirm-Page($seconds) {
    # Sharing a link opens the page that asks what to make of it. Its Download
    # button sits at the bottom right, and turns blue once the link has been
    # read - so wait for the blue, then tap it.
    $screen = Get-Screen
    $x = [int]($screen.Width * 0.75)
    $y = [int]($screen.Height - (14 + 24) * $screen.Dp)
    $probe = Join-Path $env:TEMP 'grabbit-page-probe.png'
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        & $adb shell "screencap -p /sdcard/page-probe.png" | Out-Null
        & $adb pull /sdcard/page-probe.png $probe 2>&1 | Out-Null
        $blue = & $python -c "from PIL import Image
r, g, b = Image.open(r'$probe').convert('RGB').getpixel(($x, $y))
print(1 if b > 200 and r < 120 else 0)"
        if ([int]$blue -eq 1) {
            & $adb shell "input tap $x $y" | Out-Null
            return $true
        }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Set-Remembered($quality) {
    # The page opens on the last choice made in it, which the app keeps in its
    # settings: write that choice in while the app is stopped, and the page
    # comes up with it already chosen.
    & $adb shell "am force-stop $package" | Out-Null
    $file = 'files/grabbit/settings.json'
    $json = (& $adb exec-out "run-as $package cat $file") -join "`n"
    $local = Join-Path $env:TEMP 'grabbit-settings.json'
    $json | & $python -c "import json, sys
text = sys.stdin.read().strip()
data = json.loads(text) if text.startswith('{') else {}
data['video_quality'] = '$quality'
open(r'$local', 'w', encoding='utf-8').write(json.dumps(data, indent=2))"
    & $adb push $local /data/local/tmp/grabbit-settings.json 2>&1 | Out-Null
    & $adb shell "cat /data/local/tmp/grabbit-settings.json | run-as $package sh -c 'cat > $file'; rm -f /data/local/tmp/grabbit-settings.json"
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
# A video comes out as a video unless the page was last left on something else.
Set-Remembered 'best'
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
    if (-not (Confirm-Page 90)) {
        Report $case.Name $false 'the page never offered Download'
        continue
    }
    $new = Wait-ForFile $before $case.Match $Wait
    Report $case.Name ($new.Count -gt 0) ($new -join ', ')
}

"`n=== the same video as a GIF, and as an MP3 ==="
foreach ($format in @(
    @{ Name = 'MP3'; Quality = 'audio_mp3'; Match = '\.mp3$' },
    @{ Name = 'GIF'; Quality = 'gif'; Match = '\.gif$' }
)) {
    $before = @(Saved)
    Set-Remembered $format.Quality
    Share 'https://www.youtube.com/watch?v=jNQXAC9IVRw'
    if (-not (Confirm-Page 120)) {
        Report "$($format.Name) from the same link" $false 'the page never offered Download'
        continue
    }
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

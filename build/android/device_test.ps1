<#
    Exercises the installed APK on a plugged-in phone, the way selftest.py
    exercises the desktop build: real links, real downloads, and a look at what
    landed on the phone afterwards.

        powershell -File build\android\device_test.ps1

    Links are handed to the app through Android's share intent, which is both
    the quickest way to drive it from a cable and the path most people will
    actually use.
#>
param(
    [int]$Wait = 150,
    [switch]$SkipGrant
)

$adb = "$env:LOCALAPPDATA\GrabbitBuild\platform-tools-win\adb.exe"
$package = 'com.grabbit.downloader'
$activity = "$package/org.kivy.android.PythonActivity"
$results = @()

function Report($name, $ok, $detail) {
    $script:results += [pscustomobject]@{ Name = $name; Ok = $ok }
    $mark = if ($ok) { 'PASS' } else { 'FAIL' }
    if ($detail) { "  [$mark] $name - $detail" } else { "  [$mark] $name" }
}

function Adb { & $adb @args 2>&1 }

function Send-Link($url) {
    Adb shell am start -a android.intent.action.SEND -t text/plain `
        --es android.intent.extra.TEXT "'$url'" -n $activity | Out-Null
}

function Downloads-Listing {
    # Wherever the app was allowed to write.
    $shared = Adb shell "ls -l /storage/emulated/0/Download/Grabbit 2>/dev/null"
    $private = Adb shell "ls -l /storage/emulated/0/Android/data/$package/files/Download 2>/dev/null"
    return @($shared) + @($private)
}

"=== device ==="
Adb shell "getprop ro.product.model; getprop ro.build.version.release"

if (-not $SkipGrant) {
    "`n=== granting file access so downloads land in Downloads/Grabbit ==="
    Adb shell "appops set --uid $package MANAGE_EXTERNAL_STORAGE allow"
    Adb shell "pm grant $package android.permission.POST_NOTIFICATIONS" | Out-Null
}

"`n=== starting the app ==="
Adb shell "am force-stop $package" | Out-Null
Adb logcat -c | Out-Null
Adb shell "am start -n $activity" | Out-Null
Start-Sleep -Seconds 12

$pid_ = (Adb shell "pidof $package").Trim()
Report 'the app is running' ([bool]$pid_) "pid $pid_"

$tools = Adb shell "ls `$(pm path $package | head -1 | sed 's/package://' | xargs dirname)/lib/arm64/"
Report 'the native tools were unpacked' ("$tools" -match 'libaria2c\.so') ("$tools" -replace '\s+', ' ')

$aria = Adb shell "ps -A -o NAME | grep -i aria2"
Report 'aria2 is running inside the app' ([bool]("$aria".Trim())) "$aria".Trim()

foreach ($case in @(
    @{ Name = 'youtube'; Url = 'https://www.youtube.com/watch?v=jNQXAC9IVRw' },
    @{ Name = 'tiktok';  Url = 'https://www.tiktok.com/@jade.wood/video/7434103280294300960' },
    @{ Name = 'instagram photos'; Url = 'https://www.instagram.com/nasa/p/DdWojaYFDf-/' }
)) {
    "`n=== $($case.Name) ==="
    $before = (Downloads-Listing) -join "`n"
    Send-Link $case.Url
    Start-Sleep -Seconds $Wait
    $after = (Downloads-Listing) -join "`n"
    $new = Compare-Object ($before -split "`n") ($after -split "`n") |
           Where-Object { $_.SideIndicator -eq '=>' } | ForEach-Object { $_.InputObject }
    Report "$($case.Name): a file arrived" ([bool]$new) (($new | ForEach-Object { ($_ -split '\s+')[-1] }) -join ', ')
}

"`n=== what the app said ==="
Adb logcat -d -v brief python:D AndroidRuntime:E *:S | Select-Object -Last 40

"`n=== everything it saved ==="
Downloads-Listing

$failed = $results | Where-Object { -not $_.Ok }
"`n{0}/{1} checks passed" -f ($results.Count - $failed.Count), $results.Count
$failed | ForEach-Object { "  failed: $($_.Name)" }

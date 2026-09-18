<#
    Runs the engine on a plugged-in phone and reports what happened, the way
    build/selftest.py does for the desktop.

        powershell -File build\android\device_test.ps1

    The app's Python side is started through `run-as`, which works on a debug
    build and needs neither the screen nor the lock code: the tools, the engine
    and the storage rules can all be checked with the phone face down on the
    desk.

    What it cannot check is downloading. A run-as process is given sockets but
    not Android's DNS resolver, so every hostname fails there while working
    perfectly in the app itself. The test says so and stops rather than
    reporting failures that mean nothing; for the downloads, open the app and
    share a link to it.

    -Grant also gives the app "all files access" over adb, so downloads land in
    Downloads/Grabbit rather than the app's own folder. Uninstalling undoes it.
#>
param(
    [switch]$Grant,
    [string]$Script = "$PSScriptRoot\device_engine_test.py"
)

$ErrorActionPreference = 'Continue'
$adb = "$env:LOCALAPPDATA\GrabbitBuild\platform-tools-win\adb.exe"
$package = 'com.grabbit.downloader'
$app = "/data/user/0/$package/files/app"

"=== device ==="
& $adb shell "getprop ro.product.model; getprop ro.build.version.release; getprop ro.product.cpu.abi"

$installed = & $adb shell "pm path $package"
if (-not "$installed") { throw "$package is not installed - run build/android/install_apk.ps1 first" }
$libs = (& $adb shell "dirname `$(pm path $package | head -1 | sed 's/package://')").Trim() + '/lib/arm64'
"native libraries: $libs"

if ($Grant) {
    "`n=== granting all-files access so downloads land in Downloads/Grabbit ==="
    & $adb shell "appops set --uid $package MANAGE_EXTERNAL_STORAGE allow"
}

"`n=== sending the test into the app's sandbox ==="
# A freshly installed app has nothing in its private folder until the activity
# has started once and unpacked it. One launch is enough - the window it then
# waits for does not matter here, so a locked phone is fine.
# stdlib.zip is the last thing out of the APK, so waiting for it is waiting for
# the unpacking to finish. Stopping the app before that leaves a Python with no
# standard library, which fails in a way that looks like nothing to do with this.
function Test-Unpacked {
    # An explicit "no" matters: any error text from run-as would otherwise read
    # as success.
    $answer = (& $adb shell "run-as $package sh -c 'test -f files/app/_python_bundle/stdlib.zip && echo yes || echo no'") -join ''
    return $answer -match 'yes'
}

if (-not (Test-Unpacked)) {
    "`n=== first launch, so the app unpacks itself ==="
    & $adb shell "am start -n $package/org.kivy.android.PythonActivity" | Out-Null
    for ($i = 1; $i -le 60; $i++) {
        Start-Sleep -Seconds 2
        if (Test-Unpacked) { break }
    }
    & $adb shell "am force-stop $package"
    if (Test-Unpacked) { "  unpacked after about $($i * 2)s" }
    else { throw 'the app never finished unpacking itself - is it crashing on launch?' }
}

# The interpreter is normally linked into place by the app's own start-up, which
# only gets that far once it has a window. Doing it here instead is what lets
# this run without the screen.
& $adb shell "run-as $package sh -c 'mkdir -p files/app/.bin && ln -sf $libs/libpythonbin.so files/app/.bin/python'"

# adb push cannot reach an app's private folder, and the app cannot read
# /data/local/tmp. So the file is pushed as the shell user and poured into the
# sandbox through a pipe: cat reads it, run-as writes it.
& $adb push $Script /data/local/tmp/device_engine_test.py 2>&1 | Select-String 'pushed' | ForEach-Object { "  $_" }
& $adb shell "cat /data/local/tmp/device_engine_test.py | run-as $package sh -c 'cat > files/app/device_engine_test.py'"
& $adb shell "rm -f /data/local/tmp/device_engine_test.py"
& $adb shell "run-as $package wc -c files/app/device_engine_test.py"

$env_vars = @(
    "LD_LIBRARY_PATH=$libs",
    "PYTHONHOME=$app/_python_bundle",
    "PYTHONPATH=${app}:$app/_python_bundle/stdlib.zip:$app/_python_bundle/modules:$app/_python_bundle/site-packages:$app/shared",
    "ANDROID_PRIVATE=/data/user/0/$package/files",
    "ANDROID_APP_PATH=$app",
    "ANDROID_ARGUMENT=$app",
    "HOME=$app",
    "LANG=en_US.UTF-8"
) -join ' '

"`n=== running the engine on the phone ==="
& $adb shell "run-as $package sh -c 'cd files/app && $env_vars ./.bin/python -u device_engine_test.py 2>&1'"

"`n=== what is on the phone now ==="
& $adb shell "ls -l /storage/emulated/0/Download/Grabbit 2>/dev/null; ls -l /storage/emulated/0/Android/data/$package/files/Download 2>/dev/null"

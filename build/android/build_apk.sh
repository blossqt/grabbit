#!/usr/bin/env bash
# Builds the Grabbit APK with python-for-android.
#
#   bash build/android/build_apk.sh            (inside WSL)
#
# Everything the app needs has already been cross-compiled by build_aria2.sh
# and build_tools.sh and sits in vendor/android. This script assembles the
# Python side, hands the binaries to p4a as native libraries, and builds.
#
# The binaries travel as lib*.so on purpose: Android 10 and later refuse to
# execute anything from an app's data directory, but the native library
# directory stays executable. Dropping them into p4a's libs collection is all
# it takes - the bootstrap copies that folder into the APK.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
ANDROID_ROOT="${ANDROID_HOME:-$HOME/android}"
VENV="${P4A_VENV:-$ANDROID_ROOT/p4a-venv}"
STORAGE="$ANDROID_ROOT/p4a-build"
VENDOR="$ROOT/vendor/android"
APPDIR="$ANDROID_ROOT/apk-src"
OUTDIR="$ROOT/dist/android"

DIST=grabbit
PKG=com.grabbit.downloader
NAME=Grabbit
# One version for both platforms: the shared package is where it is written.
VERSION="${GRABBIT_VERSION:-$(sed -n "s/^APP_VERSION = '\(.*\)'/\1/p" "$ROOT/app/grabbit/__init__.py")}"
ARCH=arm64-v8a
API="${TARGET_API:-35}"
MINSDK="${MIN_SDK:-24}"
# yt-dlp-ejs carries the JavaScript that QuickJS runs for YouTube's challenges;
# without it a JS runtime on its own gets nowhere. mutagen is deliberately left
# out, as it is on the desktop: it is GPL, and yt-dlp only wants it to write
# cover art into audio files.
#
# build/android/recipes holds one local recipe, which takes requests out of
# Kivy's optional dependencies; the file explains why it has to.
REQUIREMENTS='python3,kivy,android,pyjnius,yt-dlp,yt-dlp-ejs,certifi,websockets'

say() { printf '\n>> %s\n' "$*"; }

# ------------------------------------------------------- what we ship
for tool in aria2c ffmpeg ffprobe qjs; do
  [ -f "$VENDOR/$tool" ] || {
    echo "error: vendor/android/$tool is missing - run build_aria2.sh and build_tools.sh first" >&2
    exit 1; }
done

[ -d "$VENV" ] || { echo "error: no p4a environment - run build/android/setup_p4a.sh first" >&2; exit 1; }
# shellcheck disable=SC1091
source "$VENV/bin/activate"

# p4a's own "android" recipe cythonises itself but never says so, and modern
# pip builds each wheel in an isolated environment that therefore has no
# Cython. Declaring the dependency is the whole fix; it is written next to the
# recipe so a p4a upgrade simply replaces it.
RECIPE_SRC="$(python -c 'import os, pythonforandroid as p; print(os.path.join(os.path.dirname(p.__file__), "recipes", "android", "src"))')"
if [ -d "$RECIPE_SRC" ] && [ ! -f "$RECIPE_SRC/pyproject.toml" ]; then
  say 'declaring Cython for p4a android recipe'
  cat > "$RECIPE_SRC/pyproject.toml" <<'TOML'
[build-system]
requires = ["setuptools>=58.0.0", "wheel", "Cython>=3.0"]
build-backend = "setuptools.build_meta"
TOML
fi

# ------------------------------------------------------- SDK and NDK
export ANDROID_HOME="$ANDROID_ROOT"
export ANDROID_SDK_ROOT="$ANDROID_ROOT"
SDKMANAGER="$ANDROID_ROOT/cmdline-tools/latest/bin/sdkmanager"
# p4a is tested against NDK 28; the newer one fetch_sdk.sh brings down builds
# our own binaries happily but is past what p4a supports, so install 28 for it.
P4A_NDK=28.2.13676358
if [ ! -d "$ANDROID_ROOT/platforms/android-$API" ] || [ ! -d "$ANDROID_ROOT/ndk/$P4A_NDK" ]; then
  say "installing SDK platform $API, build-tools and NDK $P4A_NDK"
  yes | "$SDKMANAGER" --licenses >/dev/null 2>&1 || true
  "$SDKMANAGER" "platforms;android-$API" "build-tools;35.0.0" "ndk;$P4A_NDK" >/dev/null
fi

NDK="$(ls -d "$ANDROID_ROOT"/ndk/28.* 2>/dev/null | sort -V | tail -1 || true)"
[ -n "$NDK" ] || NDK="${ANDROID_NDK_HOME:-$(ls -d "$ANDROID_ROOT"/android-ndk-r* | sort -V | tail -1)}"
say "NDK: $NDK"

# --------------------------------------------------- assemble the app
say 'assembling the Python side'
rm -rf "$APPDIR"
mkdir -p "$APPDIR/shared"
cp "$ROOT/android/main.py" "$APPDIR/main.py"
cp -r "$ROOT/android/grabbit_mobile" "$APPDIR/grabbit_mobile"
cp -r "$ROOT/app/grabbit" "$APPDIR/shared/grabbit"
# The desktop front-end has no place on a phone: it is Qt, and nothing here
# imports it. The engine module is the Qt orchestration; the phone has its own.
rm -rf "$APPDIR/shared/grabbit/ui" \
       "$APPDIR/shared/grabbit/app.py" \
       "$APPDIR/shared/grabbit/engine.py" \
       "$APPDIR/shared/grabbit/associations.py" \
       "$APPDIR/shared/grabbit/player.py" \
       "$APPDIR/shared/grabbit/selfupdate.py"
find "$APPDIR" -name '__pycache__' -type d -prune -exec rm -rf {} +
find "$APPDIR" -name '*.pyc' -delete

# gallery-dl, for the photo posts yt-dlp leaves behind - X's, for one. It is a
# separate program under the GPL and is kept at arm's length, as on Windows: a
# folder of its own, off the app's import path, run as its own process with the
# APK's Python (grabbit_mobile/bootstrap.py), never imported. It needs
# requests, which needs urllib3 and idna; certifi is in the APK already.
# requests does without a character-set detector, which is as well:
# charset-normalizer is the package p4a cannot install (see recipes/kivy).
say 'adding gallery-dl'
python -m pip install --quiet --no-deps --only-binary=:all: --target "$APPDIR/gallery-dl" \
  gallery-dl==1.32.13 requests==2.34.2 urllib3==2.8.0 idna==3.20
rm -rf "$APPDIR/gallery-dl/bin" "$APPDIR/gallery-dl/share"

python - "$APPDIR" <<'PY'
import compileall, sys
# Fail early and loudly on a syntax error rather than in the middle of gradle.
if not compileall.compile_dir(sys.argv[1], quiet=1, force=True):
    sys.exit('error: the app source does not compile')
PY
find "$APPDIR" -name '__pycache__' -type d -prune -exec rm -rf {} +

# ------------------------------------- hand the binaries to p4a as libs
# Two places, for the two reasons given at each of them below.
say 'placing native tools where p4a will package them'
place_tools() {   # place_tools <directory>
  mkdir -p "$1"
  cp "$VENDOR/aria2c"  "$1/libaria2c.so"
  cp "$VENDOR/ffmpeg"  "$1/libffmpeg.so"
  cp "$VENDOR/ffprobe" "$1/libffprobe.so"
  cp "$VENDOR/qjs"     "$1/libquickjs.so"
  chmod 755 "$1"/lib*.so
  echo "   $1"
}

# The collection is where p4a gathers libraries as it assembles a
# distribution, so it has to exist before the build - including on a machine
# that has never built this app, where nothing has created it yet.
place_tools "$STORAGE/build/libs_collections/$DIST/$ARCH"

# And the distribution's own libs directory, when there is one. p4a assembles
# a distribution once and every later build repackages what is already in it,
# so a rebuilt binary copied only into the collection would never reach the
# APK. There is no distribution to write to on a first build; the line above
# covers that one.
if [ -d "$STORAGE/dists/$DIST" ]; then
  place_tools "$STORAGE/dists/$DIST/libs/$ARCH"
fi

# ------------------------------------------------------------- build
# p4a installs the pure-Python requirements through a throwaway venv, but keeps
# it between runs and upgrades pip inside it every time. A run that fails
# mid-upgrade leaves a pip that cannot import itself, and every later build
# dies there instead of where the real problem was.
rm -rf "$STORAGE/build/venv"

mkdir -p "$OUTDIR"
cd "$OUTDIR"
# DownloadService (java/) keeps downloads going while the app is off screen. It
# is a foreground service in the app's own process, which p4a declares only by
# name (--native-service) - and Android 14 and later refuse a foreground
# service whose manifest entry has no foregroundServiceType. p4a writes the
# name into android:name="..." as it is, so the attributes ride in on it; the
# check at the end confirms they landed. It replaces --wakelock, which kept the
# screen on at full brightness while the app was open and did nothing once it
# was not.
#
# --display-cutout=shortEdges lets the app draw beside the camera cutout at all
# times. Without it Android may stop doing so whenever the system bars change -
# as they do when the keyboard opens - and shift everything down, leaving a
# black strip where the app was. The app keeps its own content clear of the
# cutout (safe_insets in grabbit_mobile/bootstrap.py).
say 'building the APK (first run compiles CPython, SDL and Kivy - expect a wait)'
p4a apk \
  --private "$APPDIR" \
  --package="$PKG" \
  --name="$NAME" \
  --version="$VERSION" \
  --bootstrap=sdl2 \
  --requirements="$REQUIREMENTS" \
  --local-recipes="$HERE/recipes" \
  --arch="$ARCH" \
  --dist-name="$DIST" \
  --storage-dir="$STORAGE" \
  --sdk-dir="$ANDROID_ROOT" \
  --ndk-dir="$NDK" \
  --android-api="$API" \
  --ndk-api="$MINSDK" \
  --minsdk="$MINSDK" \
  --orientation=portrait \
  --display-cutout=shortEdges \
  --activity-launch-mode=singleTask \
  --intent-filters="$HERE/intent_filters.xml" \
  --add-source="$HERE/java" \
  --native-service 'com.grabbit.downloader.DownloadService" android:exported="false" android:foregroundServiceType="dataSync' \
  --icon="$HERE/icon.png" \
  --presplash="$HERE/presplash.png" \
  --presplash-color='#17171c' \
  --enable-androidx \
  --permission=android.permission.INTERNET \
  --permission=android.permission.ACCESS_NETWORK_STATE \
  --permission=android.permission.READ_EXTERNAL_STORAGE \
  --permission=android.permission.WRITE_EXTERNAL_STORAGE \
  --permission=android.permission.MANAGE_EXTERNAL_STORAGE \
  --permission=android.permission.POST_NOTIFICATIONS \
  --permission=android.permission.FOREGROUND_SERVICE \
  --permission=android.permission.FOREGROUND_SERVICE_DATA_SYNC \
  --permission=android.permission.WAKE_LOCK \
  --permission=android.permission.REQUEST_INSTALL_PACKAGES

say 'result'
ls -lh "$OUTDIR"/*.apk

# Everything above is in aid of getting four files into one directory inside
# the APK, and when that quietly does not happen the app still installs and
# still starts - it just cannot download anything. Look inside and say so now.
APK="$(ls -t "$OUTDIR"/*.apk | head -1)"
say "checking $(basename "$APK") carries the engine"
for lib in libaria2c.so libffmpeg.so libffprobe.so libquickjs.so; do
  unzip -l "$APK" | grep -q "lib/$ARCH/$lib" \
    || { echo "error: $lib is missing from the APK" >&2; exit 1; }
  echo "   $lib"
done

# The same for the background service - compiled in, and declared with the
# type Android 14 insists on (see --native-service above) - and for gallery-dl.
TOOLS="$ANDROID_ROOT/build-tools/35.0.0"
"$TOOLS/dexdump" "$APK" 2>/dev/null | grep -q 'Lcom/grabbit/downloader/DownloadService;' \
  || { echo "error: DownloadService is not compiled into the APK" >&2; exit 1; }
"$TOOLS/aapt2" dump xmltree --file AndroidManifest.xml "$APK" \
  | grep -A4 'com.grabbit.downloader.DownloadService' | grep -q 'foregroundServiceType' \
  || { echo "error: the manifest does not declare DownloadService as a data-sync service" >&2; exit 1; }
echo "   DownloadService"
PRIVATE="$(mktemp)"
unzip -p "$APK" assets/private.tar > "$PRIVATE"
tar -tf "$PRIVATE" | grep -q 'gallery-dl/gallery_dl/__main__' \
  || { echo "error: gallery-dl is missing from the APK" >&2; exit 1; }
rm -f "$PRIVATE"
echo "   gallery-dl"

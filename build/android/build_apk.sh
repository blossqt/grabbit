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
VERSION="${GRABBIT_VERSION:-1.0.0}"
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
if [ ! -d "$ANDROID_ROOT/platforms/android-$API" ]; then
  say "installing SDK platform $API"
  yes | "$SDKMANAGER" --licenses >/dev/null 2>&1 || true
  "$SDKMANAGER" "platforms;android-$API" "build-tools;35.0.0" >/dev/null
fi

# p4a is tested against NDK 28; ours built the binaries with a newer one, which
# it only warns about. Prefer an SDK-installed 28 if it is there.
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
       "$APPDIR/shared/grabbit/player.py"
find "$APPDIR" -name '__pycache__' -type d -prune -exec rm -rf {} +
find "$APPDIR" -name '*.pyc' -delete
python - "$APPDIR" <<'PY'
import compileall, sys
# Fail early and loudly on a syntax error rather than in the middle of gradle.
if not compileall.compile_dir(sys.argv[1], quiet=1, force=True):
    sys.exit('error: the app source does not compile')
PY
find "$APPDIR" -name '__pycache__' -type d -prune -exec rm -rf {} +

# ------------------------------------- hand the binaries to p4a as libs
LIBS="$STORAGE/build/libs_collections/$DIST/$ARCH"
say "placing native tools in $LIBS"
mkdir -p "$LIBS"
cp "$VENDOR/aria2c"  "$LIBS/libaria2c.so"
cp "$VENDOR/ffmpeg"  "$LIBS/libffmpeg.so"
cp "$VENDOR/ffprobe" "$LIBS/libffprobe.so"
cp "$VENDOR/qjs"     "$LIBS/libquickjs.so"
chmod 755 "$LIBS"/lib*.so
ls -l "$LIBS"

# ------------------------------------------------------------- build
# p4a installs the pure-Python requirements through a throwaway venv, but keeps
# it between runs and upgrades pip inside it every time. A run that fails
# mid-upgrade leaves a pip that cannot import itself, and every later build
# dies there instead of where the real problem was.
rm -rf "$STORAGE/build/venv"

mkdir -p "$OUTDIR"
cd "$OUTDIR"
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
  --activity-launch-mode=singleTask \
  --intent-filters="$HERE/intent_filters.xml" \
  --icon="$HERE/icon.png" \
  --presplash="$HERE/presplash.png" \
  --presplash-color='#17171c' \
  --wakelock \
  --enable-androidx \
  --permission=android.permission.INTERNET \
  --permission=android.permission.ACCESS_NETWORK_STATE \
  --permission=android.permission.READ_EXTERNAL_STORAGE \
  --permission=android.permission.WRITE_EXTERNAL_STORAGE \
  --permission=android.permission.MANAGE_EXTERNAL_STORAGE \
  --permission=android.permission.POST_NOTIFICATIONS \
  --permission=android.permission.FOREGROUND_SERVICE

say 'result'
ls -lh "$OUTDIR"/*.apk

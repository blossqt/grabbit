#!/usr/bin/env bash
# Fetches the Android pieces that need no root: the NDK (to cross-compile
# aria2, QuickJS and FFmpeg for phones), platform-tools (adb) and the
# command-line tools (sdkmanager, which needs a JDK) - and, where there is a
# JDK, what python-for-android builds the APK against (versions.sh).
#
# Everything lands in ~/android. Run it inside WSL:
#   bash build/android/fetch_sdk.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${ANDROID_HOME:-$HOME/android}"
REPO=https://dl.google.com/android/repository
mkdir -p "$ROOT/downloads"
cd "$ROOT/downloads"

say() { printf '\n>> %s\n' "$*"; }

# Google's package index tells us today's file names rather than us guessing.
say 'reading the Android package index'
curl -fsSL "$REPO/repository2-3.xml" -o repository2-3.xml

newest() {   # newest() <regex> -> newest matching archive name
  grep -oE "$1" repository2-3.xml | sort -V | uniq | tail -1
}

NDK_ZIP=$(newest 'android-ndk-r[0-9]+[a-z]?-linux\.zip')
TOOLS_ZIP=$(newest 'commandlinetools-linux-[0-9]+_latest\.zip')
: "${NDK_ZIP:?could not find an NDK in the index}"
: "${TOOLS_ZIP:?could not find the command-line tools in the index}"

get() {      # get <file>
  if [ -s "$1" ]; then
    echo "   cached $1"
  else
    echo "   fetching $1"
    curl -fL --progress-bar "$REPO/$1" -o "$1.part"
    mv "$1.part" "$1"
  fi
}

# Only what is not already unpacked. The NDK zip alone is well over a
# gigabyte, and a CI runner that restored the unpacked one from a cache should
# not spend the run fetching it again.
say 'downloading what is missing'
[ -d "$ROOT/${NDK_ZIP%-linux.zip}" ] || get "$NDK_ZIP"
[ -d "$ROOT/cmdline-tools/latest" ]  || get "$TOOLS_ZIP"
[ -d "$ROOT/platform-tools" ]        || get platform-tools-latest-linux.zip

say 'unpacking'
cd "$ROOT"
# unzip when it exists, because the NDK ships symlinks (clang -> clang-21) and
# executable bits that a naive extraction destroys. The Python fallback below
# handles both explicitly: zipfile writes a symlink out as a normal file whose
# contents are the link target, which leaves a compiler that cannot run.
extract() {   # extract <zip> <destination>
  if command -v unzip >/dev/null 2>&1; then
    unzip -q "$1" -d "$2"
    return
  fi
  python3 - "$1" "$2" <<'PY'
import os, stat, sys, zipfile
archive, destination = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(archive) as zf:
    for entry in zf.infolist():
        path = zf.extract(entry, destination)
        mode = entry.external_attr >> 16
        if entry.is_dir() or not mode:
            continue
        if stat.S_ISLNK(mode):
            target = zf.read(entry).decode()
            os.remove(path)
            os.symlink(target, path)
        else:
            os.chmod(path, mode)
PY
}

[ -d "${NDK_ZIP%-linux.zip}" ] || extract "downloads/$NDK_ZIP" "$ROOT"
[ -d platform-tools ] || extract "downloads/platform-tools-latest-linux.zip" "$ROOT"
if [ ! -d cmdline-tools/latest ]; then
  rm -rf cmdline-tools-tmp
  extract "downloads/$TOOLS_ZIP" cmdline-tools-tmp
  mkdir -p cmdline-tools
  mv cmdline-tools-tmp/cmdline-tools cmdline-tools/latest
  rmdir cmdline-tools-tmp
fi

# Here rather than in build_apk.sh, so that a CI run keeps them with the rest
# of the toolchain: its cache is written between this script and that one.
# shellcheck source=versions.sh
source "$HERE/versions.sh"
if command -v java >/dev/null 2>&1; then
  install_p4a_sdk "$ROOT"
else
  echo "   no JDK yet: build_apk.sh installs the SDK platform and NDK it needs"
fi

NDK_DIR="$ROOT/$(ls -d android-ndk-r* 2>/dev/null | sort -V | tail -1 | xargs -n1 basename)"
cat > "$ROOT/env.sh" <<EOF
# source this before building anything for Android
export ANDROID_HOME="$ROOT"
export ANDROID_SDK_ROOT="$ROOT"
export ANDROID_NDK_HOME="$NDK_DIR"
export PATH="\$ANDROID_HOME/cmdline-tools/latest/bin:\$ANDROID_HOME/platform-tools:\$PATH"
EOF

say 'ready'
echo "   NDK          : $NDK_DIR"
echo "   adb          : $ROOT/platform-tools/adb"
echo "   sdkmanager   : $ROOT/cmdline-tools/latest/bin/sdkmanager (needs a JDK)"
echo "   environment  : source $ROOT/env.sh"
du -sh "$ROOT" 2>/dev/null | awk '{print "   size         : " $1}'

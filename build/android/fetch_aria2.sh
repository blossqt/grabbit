#!/usr/bin/env bash
# Fetches aria2's source, which is not kept in git.
#
#   bash build/android/fetch_aria2.sh
#
# On Windows bootstrap.ps1 does this; build_aria2.sh calls this when the tree
# is missing, so a Linux machine - or a CI runner - can build the phone engine
# without a Windows step first. Same release either way; the version below and
# the one in bootstrap.ps1 are meant to match.
set -euo pipefail

VERSION="${ARIA2_VERSION:-release-1.37.0}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
SRC="$ROOT/aria2"

if [ -f "$SRC/configure.ac" ]; then
  echo "aria2 source already here: $SRC"
  exit 0
fi

echo ">> fetching aria2 $VERSION"
mkdir -p "$SRC"
curl -fL --progress-bar \
  "https://github.com/aria2/aria2/archive/refs/tags/$VERSION.tar.gz" \
  -o "$SRC/../aria2-src.tar.gz"
tar -xf "$SRC/../aria2-src.tar.gz" -C "$SRC" --strip-components=1
rm -f "$SRC/../aria2-src.tar.gz"
[ -f "$SRC/configure.ac" ] || { echo 'error: aria2 source did not extract' >&2; exit 1; }
echo "   $SRC"

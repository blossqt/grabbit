#!/usr/bin/env bash
# Compile a self-contained (statically linked) aria2c.exe from ../aria2.
#
# Must run inside the MSYS2 UCRT64 environment created by bootstrap.ps1.
# build_all.ps1 calls it for you; to run it by hand:
#   set MSYSTEM=UCRT64 & set CHERE_INVOKING=1
#   %LOCALAPPDATA%\GrabbitBuild\msys64\usr\bin\bash.exe -l build/build_aria2.sh
#
# TLS uses Windows' native Schannel (wintls), so no OpenSSL CA bundle is
# needed. Linked in statically: c-ares, expat, sqlite3, zlib, gmp, libssh2.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/.." && pwd)"
src="$root/aria2"
obj="$(cygpath -u "$LOCALAPPDATA")/GrabbitBuild/aria2-build"
dest="$root/vendor"

if [ "${MSYSTEM:-}" != "UCRT64" ]; then
  echo "error: run inside MSYS2 UCRT64 (MSYSTEM=UCRT64)" >&2
  exit 1
fi

cd "$src"

# Local fixes to aria2 itself (see build/patches/*.patch for the reasoning).
for p in "$here"/patches/*.patch; do
  [ -e "$p" ] || continue
  name="$(basename "$p")"
  if patch -p1 -N -s --dry-run -d "$src" <"$p" >/dev/null 2>&1; then
    echo ">> applying $name"
    patch -p1 -N -d "$src" <"$p"
  elif patch -p1 -R -s --dry-run -d "$src" <"$p" >/dev/null 2>&1; then
    echo ">> $name already applied"
  else
    echo "error: $name does not apply to this aria2 source" >&2
    exit 1
  fi
done

if [ ! -f configure ] || [ configure.ac -nt configure ]; then
  echo ">> autoreconf"
  autoreconf -fi
fi

mkdir -p "$obj"
cd "$obj"
if [ ! -f Makefile ] || [ "$src/configure" -nt Makefile ]; then
  echo ">> configure"
  "$src/configure" \
    --disable-nls \
    --without-included-gettext \
    --without-gnutls \
    --without-openssl \
    --with-libcares \
    --with-sqlite3 \
    --without-libxml2 \
    --with-libexpat \
    --with-libz \
    --with-libgmp \
    --with-libssh2 \
    --without-libgcrypt \
    --without-libnettle \
    ARIA2_STATIC=yes \
    CFLAGS="-O2 -g0" \
    CXXFLAGS="-O2 -g0" \
    LDFLAGS="-static -static-libgcc -static-libstdc++"
fi

echo ">> make ($(nproc) jobs)"
# Only what aria2c.exe needs; doc/ and test/ want sphinx and cppunit.
for dir in lib deps src; do
  make -j"$(nproc)" -C "$dir"
done

mkdir -p "$dest"
strip -o "$dest/aria2c.exe" src/aria2c.exe
cp -f "$src/COPYING" "$dest/aria2-COPYING.txt"

echo ">> result"
"$dest/aria2c.exe" --version | sed -n '1,12p'
echo ">> DLL imports (should be Windows system DLLs only)"
objdump -p "$dest/aria2c.exe" | grep 'DLL Name' | sort -u
ls -l "$dest/aria2c.exe"

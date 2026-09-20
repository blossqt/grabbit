#!/usr/bin/env bash
# Cross-compiles aria2 and the libraries it needs for arm64 Android phones.
#
#   bash build/android/build_aria2.sh          (inside WSL, after fetch_sdk.sh)
#
# The result is a single static-ish PIE executable in vendor/android/aria2c that
# an Android app can ship as an asset and drive over its RPC interface, exactly
# as the Windows build does.
#
# Follows aria2's own android-config recipe: OpenSSL for TLS (Android has no
# system TLS an executable can link to), plus expat, zlib and c-ares. SFTP and
# sqlite are left out - neither matters on a phone and both add build surface.
#
# Our DHT patch from build/patches is applied here too, so magnets behave the
# same on Android as on Windows.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
ANDROID_ROOT="${ANDROID_HOME:-$HOME/android}"
API="${ANDROID_API:-24}"          # Android 7 and up
HOST=aarch64-linux-android
PREFIX="$ANDROID_ROOT/arm64-sysroot"
WORK="$ANDROID_ROOT/build-arm64"
OUT="$ROOT/vendor/android"

# Pinned dependency versions - bump here, the URLs follow.
OPENSSL_VERSION=3.5.4
EXPAT_VERSION=2.7.3
ZLIB_VERSION=1.3.1
CARES_VERSION=1.34.5

NDK="${ANDROID_NDK_HOME:-$(ls -d "$ANDROID_ROOT"/android-ndk-r* 2>/dev/null | sort -V | tail -1)}"
[ -d "$NDK" ] || { echo "error: no NDK found - run build/android/fetch_sdk.sh first" >&2; exit 1; }
TOOLCHAIN="$NDK/toolchains/llvm/prebuilt/linux-x86_64"
[ -d "$TOOLCHAIN" ] || { echo "error: NDK toolchain missing at $TOOLCHAIN" >&2; exit 1; }

export PATH="$TOOLCHAIN/bin:$PATH"
export AR="$TOOLCHAIN/bin/llvm-ar"
export RANLIB="$TOOLCHAIN/bin/llvm-ranlib"
export STRIP="$TOOLCHAIN/bin/llvm-strip"
export CC="$TOOLCHAIN/bin/$HOST$API-clang"
export CXX="$TOOLCHAIN/bin/$HOST$API-clang++"
export PKG_CONFIG_LIBDIR="$PREFIX/lib/pkgconfig"
BUILD_TRIPLE="$(uname -m)-pc-linux-gnu"
JOBS="$(nproc)"

mkdir -p "$PREFIX" "$WORK" "$OUT" "$PREFIX/lib"
cd "$WORK"

say() { printf '\n>> %s\n' "$*"; }

# Android's libc has pthread and rt built in, so there is no libpthread.so or
# librt.so to link against - but autotools checks and pkg-config files still
# ask for -lpthread and -lrt. Empty archives satisfy the linker harmlessly;
# the symbols themselves come from libc.
if [ ! -f "$PREFIX/lib/libpthread.a" ]; then
  say 'stub libpthread.a / librt.a (Android keeps both inside libc)'
  : > "$WORK/empty.c"
  "$CC" -c "$WORK/empty.c" -o "$WORK/empty.o"
  "$AR" rcs "$PREFIX/lib/libpthread.a" "$WORK/empty.o"
  "$AR" rcs "$PREFIX/lib/librt.a" "$WORK/empty.o"
fi

fetch() {    # fetch <url> <tarball>
  [ -s "$2" ] || curl -fL --progress-bar "$1" -o "$2"
}

unpack() {   # unpack <tarball> <directory>
  [ -d "$2" ] || tar xf "$1"
}

# ---------------------------------------------------------------- OpenSSL
if [ ! -f "$PREFIX/lib/libssl.a" ]; then
  say "OpenSSL $OPENSSL_VERSION"
  fetch "https://github.com/openssl/openssl/releases/download/openssl-$OPENSSL_VERSION/openssl-$OPENSSL_VERSION.tar.gz" "openssl-$OPENSSL_VERSION.tar.gz"
  # Always from a clean tree: OpenSSL refuses to reconfigure over itself, so a
  # failed attempt would otherwise poison every retry.
  rm -rf "openssl-$OPENSSL_VERSION"
  tar xf "openssl-$OPENSSL_VERSION.tar.gz"
  (
    cd "openssl-$OPENSSL_VERSION"
    # no-apps: we want libcrypto and libssl, not the openssl command line.
    # no-module: build the legacy provider *into* libcrypto instead of leaving
    # it as a loadable module. aria2 insists on loading "legacy" at start-up
    # for RC4, which BitTorrent peer encryption uses, and a statically linked
    # binary has no module file to find - it aborts on launch without this.
    ANDROID_NDK_ROOT="$NDK" ./Configure android-arm64 no-shared no-module no-tests no-apps no-docs \
      -D__ANDROID_API__="$API" --prefix="$PREFIX"
    make -j"$JOBS"
    make install_sw
  )
fi

# ------------------------------------------------------------------ zlib
if [ ! -f "$PREFIX/lib/libz.a" ]; then
  say "zlib $ZLIB_VERSION"
  fetch "https://github.com/madler/zlib/releases/download/v$ZLIB_VERSION/zlib-$ZLIB_VERSION.tar.gz" "zlib-$ZLIB_VERSION.tar.gz"
  unpack "zlib-$ZLIB_VERSION.tar.gz" "zlib-$ZLIB_VERSION"
  (
    cd "zlib-$ZLIB_VERSION"
    ./configure --prefix="$PREFIX" --libdir="$PREFIX/lib" --includedir="$PREFIX/include" --static
    make -j"$JOBS" install
  )
fi

# ----------------------------------------------------------------- expat
if [ ! -f "$PREFIX/lib/libexpat.a" ]; then
  say "expat $EXPAT_VERSION"
  tag="R_${EXPAT_VERSION//./_}"
  fetch "https://github.com/libexpat/libexpat/releases/download/$tag/expat-$EXPAT_VERSION.tar.bz2" "expat-$EXPAT_VERSION.tar.bz2"
  unpack "expat-$EXPAT_VERSION.tar.bz2" "expat-$EXPAT_VERSION"
  (
    cd "expat-$EXPAT_VERSION"
    ./configure --host="$HOST" --build="$BUILD_TRIPLE" --prefix="$PREFIX" \
      --disable-shared --without-docbook --without-examples --without-tests
    make -j"$JOBS" install
  )
fi

# ---------------------------------------------------------------- c-ares
if [ ! -f "$PREFIX/lib/libcares.a" ]; then
  say "c-ares $CARES_VERSION"
  fetch "https://github.com/c-ares/c-ares/releases/download/v$CARES_VERSION/c-ares-$CARES_VERSION.tar.gz" "c-ares-$CARES_VERSION.tar.gz"
  unpack "c-ares-$CARES_VERSION.tar.gz" "c-ares-$CARES_VERSION"
  (
    cd "c-ares-$CARES_VERSION"
    ./configure --host="$HOST" --build="$BUILD_TRIPLE" --prefix="$PREFIX" --disable-shared
    make -j"$JOBS" install
  )
fi

# ----------------------------------------------------------------- aria2
say 'aria2'
SRC="$ROOT/aria2"
# Not in git. On Windows bootstrap.ps1 has already put it there; anywhere else
# - a Linux machine, a CI runner - fetch it now rather than sending the reader
# to a PowerShell script they cannot run.
[ -f "$SRC/configure.ac" ] || bash "$HERE/fetch_aria2.sh"
[ -f "$SRC/configure.ac" ] || { echo 'error: aria2 source missing' >&2; exit 1; }

cd "$SRC"
for patch_file in "$ROOT"/build/patches/*.patch; do
  [ -e "$patch_file" ] || continue
  name="$(basename "$patch_file")"
  if patch -p1 -N -s --dry-run <"$patch_file" >/dev/null 2>&1; then
    echo "   applying $name"
    patch -p1 -N <"$patch_file"
  else
    echo "   $name already applied"
  fi
done
[ -f configure ] || autoreconf -i

OBJ="$WORK/aria2-arm64"
mkdir -p "$OBJ"
cd "$OBJ"
if [ ! -f Makefile ]; then
  "$SRC/configure" \
    --host="$HOST" \
    --build="$BUILD_TRIPLE" \
    --prefix="$PREFIX" \
    --disable-nls \
    --without-gnutls \
    --with-openssl \
    --without-sqlite3 \
    --without-libxml2 \
    --with-libexpat \
    --with-libcares \
    --with-libz \
    --without-libssh2 \
    --without-libgcrypt \
    --without-libnettle \
    --without-libgmp \
    ARIA2_STATIC=yes \
    CFLAGS="-Os -fPIE" \
    CXXFLAGS="-Os -fPIE" \
    CPPFLAGS="-I$PREFIX/include -fPIE" \
    LDFLAGS="-fPIE -pie -L$PREFIX/lib -static-libstdc++"
fi

for dir in lib deps src; do
  make -j"$JOBS" -C "$dir"
done

"$STRIP" -o "$OUT/aria2c" src/aria2c
say 'result'
ls -l "$OUT/aria2c"
file "$OUT/aria2c" 2>/dev/null || true
echo "   copy this into the app as an asset; it is driven over RPC like the desktop build"

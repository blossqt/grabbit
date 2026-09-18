#!/usr/bin/env bash
# Cross-compiles the remaining native tools for arm64 Android:
#
#   quickjs  - the JavaScript engine yt-dlp needs for YouTube's challenges.
#              Deno, which the desktop build uses, has no Android target at all.
#   lame     - the only MP3 encoder yt-dlp will ask for by name (libmp3lame).
#   dav1d    - AV1 decoding. FFmpeg has no software AV1 decoder of its own, and
#              AV1 is what YouTube hands out for much of its catalogue now.
#   ffmpeg   - merging separate video and audio streams, extracting audio,
#              embedding thumbnails and metadata, and making GIFs. ffprobe is
#              built alongside it: yt-dlp's metadata and chapter handling has no
#              fallback when it is missing.
#
#   bash build/android/build_tools.sh [quickjs|lame|dav1d|ffmpeg|all]
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
ANDROID_ROOT="${ANDROID_HOME:-$HOME/android}"
API="${ANDROID_API:-24}"
HOST=aarch64-linux-android
PREFIX="$ANDROID_ROOT/arm64-sysroot"   # shared with build_aria2.sh: OpenSSL lives here
WORK="$ANDROID_ROOT/build-arm64"
OUT="$ROOT/vendor/android"
WHAT="${1:-all}"

LAME_VERSION=3.100
DAV1D_VERSION=1.5.1
FFMPEG_TAG=n8.0

NDK="${ANDROID_NDK_HOME:-$(ls -d "$ANDROID_ROOT"/android-ndk-r* 2>/dev/null | sort -V | tail -1)}"
[ -d "$NDK" ] || { echo "error: no NDK found - run build/android/fetch_sdk.sh first" >&2; exit 1; }
TOOLCHAIN="$NDK/toolchains/llvm/prebuilt/linux-x86_64"
export PATH="$TOOLCHAIN/bin:$PATH"
export PKG_CONFIG_LIBDIR="$PREFIX/lib/pkgconfig"
BUILD_TRIPLE="$(uname -m)-pc-linux-gnu"
JOBS="$(nproc)"
mkdir -p "$WORK" "$OUT" "$PREFIX"

say() { printf '\n>> %s\n' "$*"; }
fetch() { [ -s "$2" ] || curl -fL --progress-bar "$1" -o "$2"; }

# ---------------------------------------------------------------- quickjs
build_quickjs() {
  say 'QuickJS (JavaScript engine for YouTube)'
  cd "$WORK"
  if [ ! -d quickjs-ng ]; then
    git clone --depth 1 https://github.com/quickjs-ng/quickjs.git quickjs-ng
  fi
  cd quickjs-ng
  rm -rf build-android
  cmake -B build-android \
    -DCMAKE_TOOLCHAIN_FILE="$NDK/build/cmake/android.toolchain.cmake" \
    -DANDROID_ABI=arm64-v8a \
    -DANDROID_PLATFORM="android-$API" \
    -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_SHARED_LIBS=OFF
  # qjs_exe is the interpreter; the plain "qjs" target is the library it links.
  cmake --build build-android --target qjs_exe -j "$JOBS"
  binary="$(find build-android -maxdepth 2 -type f -name 'qjs*' ! -name '*.a' ! -name '*.o' | head -1)"
  [ -n "$binary" ] || { echo 'error: qjs interpreter not found after build' >&2; exit 1; }
  cp "$binary" "$OUT/qjs"
  "$TOOLCHAIN/bin/llvm-strip" "$OUT/qjs"
  ls -l "$OUT/qjs"
}

# ------------------------------------------------------------------- lame
# yt-dlp hard-codes "-c:a libmp3lame" when asked for MP3, so FFmpeg's own
# encoders cannot stand in for this one.
build_lame() {
  [ -f "$PREFIX/lib/libmp3lame.a" ] && { echo 'lame already built'; return; }
  say "LAME $LAME_VERSION (MP3 encoder)"
  cd "$WORK"
  fetch "https://downloads.sourceforge.net/project/lame/lame/$LAME_VERSION/lame-$LAME_VERSION.tar.gz" "lame-$LAME_VERSION.tar.gz"
  [ -d "lame-$LAME_VERSION" ] || tar xf "lame-$LAME_VERSION.tar.gz"
  cd "lame-$LAME_VERSION"
  # --disable-frontend: the "lame" command line wants a terminal we do not have.
  CC="$TOOLCHAIN/bin/$HOST$API-clang" \
  AR="$TOOLCHAIN/bin/llvm-ar" \
  RANLIB="$TOOLCHAIN/bin/llvm-ranlib" \
  CFLAGS="-Os -fPIC" \
  ./configure --host="$HOST" --build="$BUILD_TRIPLE" --prefix="$PREFIX" \
    --disable-shared --enable-static --disable-frontend --disable-gtktest
  make -j"$JOBS"
  make install
  ls -l "$PREFIX/lib/libmp3lame.a"
}

# ------------------------------------------------------------------ dav1d
# FFmpeg's own "av1" decoder only drives hardware; there is no software AV1
# decoder in FFmpeg itself. Without dav1d, making a GIF out of an AV1 video -
# which is most of YouTube now - fails at the first frame.
build_dav1d() {
  [ -f "$PREFIX/lib/libdav1d.a" ] && { echo 'dav1d already built'; return; }
  say "dav1d $DAV1D_VERSION (AV1 decoding)"
  # meson and ninja come from a throwaway virtualenv rather than the system,
  # so this needs no root and cannot disturb anything else.
  TOOLS_VENV="$WORK/meson-venv"
  [ -x "$TOOLS_VENV/bin/meson" ] || {
    python3 -m venv "$TOOLS_VENV"
    "$TOOLS_VENV/bin/pip" install --quiet --upgrade pip meson ninja
  }
  cd "$WORK"
  [ -d dav1d ] || git clone --depth 1 --branch "$DAV1D_VERSION" \
    https://code.videolan.org/videolan/dav1d.git dav1d
  cat > "$WORK/dav1d-android.ini" <<EOF
[binaries]
c = '$TOOLCHAIN/bin/${HOST}${API}-clang'
cpp = '$TOOLCHAIN/bin/${HOST}${API}-clang++'
ar = '$TOOLCHAIN/bin/llvm-ar'
strip = '$TOOLCHAIN/bin/llvm-strip'
pkg-config = 'pkg-config'

[host_machine]
system = 'android'
cpu_family = 'aarch64'
cpu = 'aarch64'
endian = 'little'
EOF
  cd dav1d
  rm -rf build-android
  PATH="$TOOLS_VENV/bin:$PATH" meson setup build-android \
    --cross-file "$WORK/dav1d-android.ini" \
    --prefix="$PREFIX" --libdir=lib \
    --default-library=static --buildtype=release \
    -Denable_tools=false -Denable_tests=false
  PATH="$TOOLS_VENV/bin:$PATH" ninja -C build-android
  PATH="$TOOLS_VENV/bin:$PATH" ninja -C build-android install
  ls -l "$PREFIX/lib/libdav1d.a"
}

# ----------------------------------------------------------------- ffmpeg
build_ffmpeg() {
  build_lame
  build_dav1d
  say "FFmpeg $FFMPEG_TAG (merging, audio extraction, GIFs)"
  cd "$WORK"
  if [ ! -d ffmpeg ]; then
    git clone --depth 1 --branch "$FFMPEG_TAG" https://github.com/FFmpeg/FFmpeg.git ffmpeg
  fi
  cd ffmpeg
  # Containers, protocols, parsers and filters are left at their defaults: they
  # are what breaks on an unusual site, and they are cheap. The codec tables are
  # the bulk of the binary, so those are the curated part - everything a
  # downloader actually meets, plus what a GIF and an MP3 need.
  decoders=h264,hevc,vp8,vp9,av1,libdav1d,mpeg4,mpeg2video,mpeg1video,msmpeg4v1,msmpeg4v2,msmpeg4v3,wmv1,wmv2,wmv3,vc1,theora,flv,h263,prores,dvvideo,rawvideo,gif,png,apng,mjpeg,webp,bmp,tiff,aac,aac_latm,ac3,eac3,mp3,mp3float,mp2,opus,vorbis,flac,alac,wmav1,wmav2,dca,truehd,amrnb,amrwb,pcm_s16le,pcm_s16be,pcm_u8,pcm_f32le,pcm_mulaw,pcm_alaw,subrip,srt,webvtt,ass,movtext,text
  encoders=gif,png,apng,mjpeg,aac,flac,alac,libmp3lame,pcm_s16le,wrapped_avframe,srt,subrip,ass,webvtt,movtext
  # Always reconfigure: an existing config.mak is from whatever the options
  # were last time, and a stale one is a confusing thing to debug.
  ./configure \
    --prefix="$WORK/ffmpeg-install" \
    --target-os=android \
    --arch=aarch64 \
    --enable-cross-compile \
    --cc="$TOOLCHAIN/bin/${HOST}${API}-clang" \
    --cxx="$TOOLCHAIN/bin/${HOST}${API}-clang++" \
    --ar="$TOOLCHAIN/bin/llvm-ar" \
    --ranlib="$TOOLCHAIN/bin/llvm-ranlib" \
    --nm="$TOOLCHAIN/bin/llvm-nm" \
    --strip="$TOOLCHAIN/bin/llvm-strip" \
    --pkg-config=pkg-config --pkg-config-flags=--static \
    --disable-shared --enable-static --enable-pic \
    --enable-small --disable-debug --disable-doc \
    --disable-ffplay --disable-avdevice \
    --disable-vulkan --disable-jni --disable-mediacodec --disable-v4l2-m2m \
    --disable-encoders --disable-decoders \
    --enable-decoder="$decoders" \
    --enable-encoder="$encoders" \
    --enable-openssl \
    --enable-libmp3lame \
    --enable-libdav1d \
    --extra-cflags="-I$PREFIX/include -Os -fPIE" \
    --extra-ldflags="-L$PREFIX/lib -fPIE -pie"
  make -j"$JOBS" ffmpeg ffprobe
  for binary in ffmpeg ffprobe; do
    "$TOOLCHAIN/bin/llvm-strip" -o "$OUT/$binary" "$binary"
  done
  ls -l "$OUT/ffmpeg" "$OUT/ffprobe"
}

case "$WHAT" in
  quickjs) build_quickjs ;;
  lame)    build_lame ;;
  dav1d)   build_dav1d ;;
  ffmpeg)  build_ffmpeg ;;
  all)     build_quickjs; build_ffmpeg ;;
  *) echo "usage: $0 [quickjs|lame|dav1d|ffmpeg|all]" >&2; exit 2 ;;
esac

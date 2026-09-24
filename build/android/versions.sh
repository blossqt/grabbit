# What python-for-android builds the APK against. fetch_sdk.sh installs
# these and build_apk.sh uses them, so they are written down once, here.
# Sourced, not run.

P4A_API="${TARGET_API:-35}"
P4A_BUILD_TOOLS=35.0.0
# p4a is tested against NDK 28; the newer one fetch_sdk.sh brings down builds
# our own binaries happily but is past what p4a supports, so 28 is installed
# alongside it for p4a.
P4A_NDK=28.2.13676358

install_p4a_sdk() {   # install_p4a_sdk <android root>
  if [ -d "$1/platforms/android-$P4A_API" ] && [ -d "$1/build-tools/$P4A_BUILD_TOOLS" ] \
     && [ -d "$1/ndk/$P4A_NDK" ]; then
    return
  fi
  local sdkmanager="$1/cmdline-tools/latest/bin/sdkmanager"
  printf '\n>> %s\n' "installing SDK platform $P4A_API, build-tools $P4A_BUILD_TOOLS and NDK $P4A_NDK"
  yes | "$sdkmanager" --licenses >/dev/null 2>&1 || true
  "$sdkmanager" "platforms;android-$P4A_API" "build-tools;$P4A_BUILD_TOOLS" "ndk;$P4A_NDK" >/dev/null
}

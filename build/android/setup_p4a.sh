#!/usr/bin/env bash
# Sets up python-for-android, which turns Python code into an APK.
#
#   bash build/android/setup_p4a.sh     (inside WSL, after fetch_sdk.sh)
#
# p4a runs on the host Python. Ubuntu 26.04 ships 3.14, which is newer than
# most tooling expects, so this reports clearly if that combination is the
# problem rather than failing cryptically later.
set -euo pipefail

VENV="${P4A_VENV:-$HOME/android/p4a-venv}"
ANDROID_ROOT="${ANDROID_HOME:-$HOME/android}"

echo ">> host python: $(python3 --version)"

if [ ! -d "$VENV" ]; then
  echo ">> creating $VENV"
  python3 -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"

python -m pip install --quiet --upgrade pip wheel setuptools
echo ">> installing python-for-android"
python -m pip install --upgrade python-for-android cython

echo ">> versions"
python -c "import pythonforandroid, sys; print('   p4a     :', pythonforandroid.__version__)"
python -c "import cython, sys; print('   cython  :', cython.__version__)"
echo "   sdk     : $ANDROID_ROOT"
echo "   ndk     : $(ls -d "$ANDROID_ROOT"/android-ndk-r* | sort -V | tail -1)"

echo ">> checking p4a can start"
p4a --version || true

cat <<EOF

Next: build/android/build_apk.sh builds the APK itself.
Activate this environment first:
  source $VENV/bin/activate
  source $ANDROID_ROOT/env.sh
EOF

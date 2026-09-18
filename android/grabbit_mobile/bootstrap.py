"""Finding the bundled executables on a phone.

Android 10 and later refuse to execute anything from an app's data directory,
so a packaged binary cannot simply be copied somewhere and run. The way round
it is to ship it as a native library: anything named lib*.so inside the APK is
unpacked into the app's native library directory, which stays executable.

So aria2 travels as libaria2c.so and is run straight from there. The copy path
below is only for older phones and for running this code off-device.
"""

import logging
import os
import shutil
import stat
from pathlib import Path

from . import paths

log = logging.getLogger(__name__)

# Executables we ship, and the lib name each one travels under.
BUNDLED = {
    'aria2c': 'libaria2c.so',
    'ffmpeg': 'libffmpeg.so',
    'quickjs': 'libquickjs.so',
}


def native_library_dir() -> str | None:
    """Where Android unpacked our lib*.so files, if we are on a phone."""
    try:
        from jnius import autoclass
        activity = autoclass('org.kivy.android.PythonActivity').mActivity
        return activity.getApplicationInfo().nativeLibraryDir
    except Exception:
        return None


def locate(name: str) -> str | None:
    """Full path to a bundled executable, or None if this build lacks it."""
    lib_dir = native_library_dir()
    if lib_dir:
        candidate = os.path.join(lib_dir, BUNDLED.get(name, f'lib{name}.so'))
        if os.path.isfile(candidate):
            return candidate

    # Off-device, or an older Android where copying still works.
    copied = paths.tools_dir() / name
    if copied.is_file():
        return str(copied)

    source = Path(__file__).resolve().parent.parent / 'tools' / name
    if source.is_file():
        shutil.copy2(source, copied)
        copied.chmod(copied.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
        return str(copied)
    return shutil.which(name)


def unpack_tools() -> dict:
    """Resolve every bundled tool once, at start-up."""
    found = {}
    for name in BUNDLED:
        path = locate(name)
        if path:
            found[name] = path
            log.info('%s -> %s', name, path)
        else:
            log.info('%s is not in this build', name)
    paths.TOOL_PATHS.update(found)
    # The shared modules look for tools the desktop way, so tell them where
    # these actually are on a phone.
    from grabbit import paths as shared_paths
    shared_paths.TOOL_OVERRIDES.update(found)
    return found


def ca_bundle() -> str | None:
    """A certificate bundle for aria2: Android exposes none that OpenSSL finds."""
    try:
        import certifi
        return certifi.where()
    except Exception:
        local = paths.tools_dir() / 'cacert.pem'
        return str(local) if local.exists() else None

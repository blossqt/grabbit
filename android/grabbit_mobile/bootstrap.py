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
import sys
from pathlib import Path

from . import paths

log = logging.getLogger(__name__)

# Executables we ship, and the lib name each one travels under.
BUNDLED = {
    'aria2c': 'libaria2c.so',
    'ffmpeg': 'libffmpeg.so',
    'ffprobe': 'libffprobe.so',
    'quickjs': 'libquickjs.so',
}


def native_library_dir() -> str | None:
    """Where Android unpacked our lib*.so files, if we are on a phone."""
    try:
        from jnius import autoclass
        activity = autoclass('org.kivy.android.PythonActivity').mActivity
        return activity.getApplicationInfo().nativeLibraryDir
    except Exception:
        pass
    # No activity to ask: a headless run, or a background service. Python
    # itself is in that same directory - p4a ships the interpreter as
    # libpythonbin.so and links .bin/python to it - so follow the link.
    interpreter = os.path.realpath(sys.executable or '')
    if os.path.basename(interpreter).startswith('libpython'):
        return os.path.dirname(interpreter)
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


def prepare_environment() -> None:
    """Give the standard library a temp directory that exists.

    Android has no /tmp, and p4a sets no TMPDIR, so tempfile falls back to the
    working directory. yt-dlp writes the scripts it hands to QuickJS there, so
    point it somewhere we own and can clear out.
    """
    import tempfile
    scratch = paths.data_dir() / 'tmp'
    scratch.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault('TMPDIR', str(scratch))
    tempfile.tempdir = str(scratch)


def unpack_tools() -> dict:
    """Resolve every bundled tool once, at start-up."""
    prepare_environment()
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


def has_all_files_access() -> bool | None:
    """Whether Android will let us write outside our own folder.

    None means the question does not arise: not a phone, or Android 10 and
    older, where the ordinary storage permission covers it.
    """
    try:
        from jnius import autoclass
        if autoclass('android.os.Build$VERSION').SDK_INT < 30:
            return None
        return bool(autoclass('android.os.Environment').isExternalStorageManager())
    except Exception:
        return None


def open_all_files_settings() -> bool:
    """Open the one screen that can grant it. Nothing else can."""
    try:
        from jnius import autoclass
        activity = autoclass('org.kivy.android.PythonActivity').mActivity
        Intent = autoclass('android.content.Intent')
        AndroidSettings = autoclass('android.provider.Settings')
        Uri = autoclass('android.net.Uri')
        activity.startActivity(Intent(
            AndroidSettings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
            Uri.parse('package:' + activity.getPackageName())))
        return True
    except Exception:
        log.exception('could not open the storage settings screen')
        return False

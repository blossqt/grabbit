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


def public_class(name: str):
    """A Java class through pyjnius, with only its public side read.

    Before pyjnius can use a class it reads every method and field declared
    anywhere in its ancestry, private ones included: thousands, for Android's
    view classes, and at start-up that reading is time the app is not on
    screen. Nothing here uses anything but public members, so only those are
    read. (What a Java method returns is still wrapped by pyjnius itself, in
    full - which is why the code here avoids calls that return a View.)
    """
    from jnius import autoclass
    return autoclass(name, include_protected=False, include_private=False)


def native_library_dir() -> str | None:
    """Where Android unpacked our lib*.so files, if we are on a phone."""
    # Python itself is in that directory - p4a ships the interpreter as
    # libpythonbin.so and links .bin/python to it - so following the link
    # answers without asking Android, whose answer pyjnius would first have
    # to read a Java class for, just as the app is starting.
    interpreter = os.path.realpath(sys.executable or '')
    if os.path.basename(interpreter).startswith('libpython'):
        return os.path.dirname(interpreter)
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


def prepare_environment() -> None:
    """Give the standard library a temp directory, and yt-dlp a cache, that exist.

    Android has no /tmp, and p4a sets no TMPDIR, so tempfile falls back to the
    working directory. yt-dlp writes the scripts it hands to QuickJS there, so
    point it somewhere we own and can clear out.

    Nor is there a HOME, so yt-dlp's cache - what it has worked out about
    YouTube's player, among other things - would go to /data/.cache, which no
    app may write, and every attempt to keep it would fail.
    """
    import tempfile
    scratch = paths.data_dir() / 'tmp'
    scratch.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault('TMPDIR', str(scratch))
    tempfile.tempdir = str(scratch)
    os.environ.setdefault('XDG_CACHE_HOME', str(paths.cache_dir()))


def link_farm(found: dict) -> dict:
    """Give the tools their ordinary names, in one directory.

    yt-dlp looks for a file called ffmpeg, with ffprobe beside it, in whatever
    directory it is pointed at. What Android lets an app execute is
    libffmpeg.so, in a directory the app cannot add anything to. Symlinks
    answer both: the names are ordinary, and executing one follows it back to
    the native library directory, where the rule about executable files is
    satisfied.
    """
    tools = paths.tools_dir()
    linked = {}
    for name, target in found.items():
        link = tools / name
        if Path(target).parent == tools:
            linked[name] = target          # already a real file of ours
            continue
        try:
            if link.is_symlink() or link.exists():
                if os.path.realpath(link) == os.path.realpath(target):
                    linked[name] = str(link)
                    continue
                link.unlink()
            os.symlink(target, link)
            linked[name] = str(link)
        except OSError:
            log.warning('could not link %s into %s; using it where it is', name, tools)
            linked[name] = target
    return linked


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
    found = link_farm(found)
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


def open_url(url: str) -> bool:
    """Hand a link to whatever the phone opens links with.

    For an update that is the browser, which downloads the APK and offers to
    open it - and Android installs it over this copy, keeping its downloads
    and settings, because both are signed with the same key. Off a phone, the
    desktop's browser does the same job for the preview.
    """
    try:
        from jnius import autoclass
        activity = autoclass('org.kivy.android.PythonActivity').mActivity
        Intent = autoclass('android.content.Intent')
        Uri = autoclass('android.net.Uri')
        intent = Intent(Intent.ACTION_VIEW, Uri.parse(url))
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        activity.startActivity(intent)
        return True
    except ImportError:
        pass
    except Exception:
        log.exception('could not open %s', url)
        return False
    import webbrowser
    return webbrowser.open(url)


# What Android's installer sends back to this activity (see install_apk).
INSTALL_STATUS_ACTION = 'com.grabbit.downloader.INSTALL_STATUS'


def install_apk(path: str) -> None:
    """Hand a downloaded APK to Android's own installer.

    No app may install anything silently, this one included: it opens an
    installer session, writes the APK into it and commits it, and Android
    answers - with an intent back to this activity, read by install_status -
    by asking for the confirmation screen it wants the person to see. The
    update then goes over this copy, keeping its downloads and settings,
    because both are signed with the same key.

    Raises on anything that stops it, so the caller can fall back to letting
    the browser fetch the APK instead.
    """
    from jnius import autoclass
    activity = autoclass('org.kivy.android.PythonActivity').mActivity
    Intent = autoclass('android.content.Intent')
    PendingIntent = autoclass('android.app.PendingIntent')
    SessionParams = autoclass('android.content.pm.PackageInstaller$SessionParams')
    installer = activity.getPackageManager().getPackageInstaller()
    session_id = installer.createSession(SessionParams(SessionParams.MODE_FULL_INSTALL))
    session = installer.openSession(session_id)
    try:
        stream = session.openWrite('grabbit.apk', 0, os.path.getsize(path))
        with open(path, 'rb') as handle:
            for chunk in iter(lambda: handle.read(1 << 16), b''):
                stream.write(chunk)
        session.fsync(stream)
        stream.close()
        answer = Intent(activity, activity.getClass())
        answer.setAction(INSTALL_STATUS_ACTION)
        # UPDATE_CURRENT | MUTABLE: the installer writes its answer into this
        # intent, which Android 12 and later forbid unless it says so. The bit
        # is ignored where it has no name yet.
        pending = PendingIntent.getActivity(activity, session_id, answer, 0x08000000 | 0x02000000)
        session.commit(pending.getIntentSender())
    except Exception:
        session.abandon()
        raise
    finally:
        session.close()


def install_status(intent):
    """What Android's installer said, if this intent is its answer.

    None when it is not; ('confirm', None) once the confirmation screen has
    been opened; ('failed', message) when the installer gave up.
    """
    if intent is None:
        return None
    try:
        from jnius import autoclass, cast
    except ImportError:
        return None                    # off a phone, nothing installs anything
    try:
        if intent.getAction() != INSTALL_STATUS_ACTION:
            return None
        Intent = autoclass('android.content.Intent')
        PackageInstaller = autoclass('android.content.pm.PackageInstaller')
        status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, -999)
        if status == PackageInstaller.STATUS_PENDING_USER_ACTION:
            confirm = cast('android.content.Intent', intent.getParcelableExtra(Intent.EXTRA_INTENT))
            autoclass('org.kivy.android.PythonActivity').mActivity.startActivity(confirm)
            return 'confirm', None
        if status == PackageInstaller.STATUS_SUCCESS:
            return 'done', None
        if status == PackageInstaller.STATUS_FAILURE_ABORTED:
            return 'failed', 'The update was cancelled.'
        if status == PackageInstaller.STATUS_FAILURE_CONFLICT:
            return 'failed', ('This update is signed differently from the Grabbit installed here, '
                              'so Android refuses it. Uninstall Grabbit, then install the new one.')
        detail = intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE) or f'status {status}'
        return 'failed', f'Android could not install the update: {detail}'
    except Exception as error:
        log.exception('could not read the installer\'s answer')
        return 'failed', f'Android could not install the update: {error}'


def safe_insets(surface_height: int = 0) -> tuple:
    """How far the system's own furniture reaches into what the app draws, in
    pixels.

    The app draws edge to edge, which on this phone means the title sits under
    the camera cutout and the footer under the gesture bar. Android will say
    where those are; asking is better than picking a number that happens to
    suit one handset. They are measured from the edges of SDL's surface, not
    the screen's: when Android moves the surface - which some phones do while
    the keyboard is up - an inset it has already moved clear of must not be
    added again.

    surface_height is SDL's surface as Kivy has it (Window.height). From
    Android 11 the window manager answers, which costs almost nothing; asking
    a view instead, as older versions must, has pyjnius read the whole of
    View first - the slowest single step of starting up.

    Returns (top, bottom), both zero anywhere that cannot answer.
    """
    try:
        from jnius import autoclass
        activity = autoclass('org.kivy.android.PythonActivity').mActivity
        if surface_height and autoclass('android.os.Build$VERSION').SDK_INT >= 30:
            metrics = activity.getWindowManager().getCurrentWindowMetrics()
            insets = metrics.getWindowInsets()
            top, bottom = _window_insets(autoclass, insets)
            # The surface starts at the top of the window, so it is only
            # ever short of the bottom - by what it has been moved clear of.
            return int(top), int(max(0, bottom - (metrics.getBounds().height() - surface_height)))
        decor = activity.getWindow().getDecorView()
        insets = decor.getRootWindowInsets()
        if insets is None:
            return 0, 0
        top, bottom = _window_insets(autoclass, insets)
        try:
            surface = autoclass('org.libsdl.app.SDLActivity').getContentView().getChildAt(0)
            where = autoclass('android.graphics.Rect')()
            if surface is not None and surface.getGlobalVisibleRect(where):
                top = max(0, top - where.top)
                bottom = max(0, bottom - (decor.getHeight() - where.bottom))
        except Exception:
            pass          # no surface to measure: the window's edges will do
        return int(top), int(bottom)
    except Exception:
        return 0, 0


def _window_insets(autoclass, insets) -> tuple:
    """(top, bottom) that the cutout and the system bars take from a window."""
    top = bottom = 0
    cutout = insets.getDisplayCutout()
    if cutout is not None:
        top, bottom = cutout.getSafeInsetTop(), cutout.getSafeInsetBottom()
    try:
        types = autoclass('android.view.WindowInsets$Type')
        bars = insets.getInsets(types.statusBars() | types.navigationBars())
        top, bottom = max(top, bars.top), max(bottom, bars.bottom)
    except Exception:
        pass          # older Android: the cutout is the best we have
    return top, bottom


def paint_window(colour: str) -> None:
    """Give the window behind the app the app's own background colour.

    SDL draws the app on a surface, and anything of the window it does not
    cover - a strip Android leaves while it moves things for the keyboard,
    the space behind a system bar - shows the window's background, which is
    black unless something says otherwise.
    """
    try:
        from android.runnable import run_on_ui_thread
        from jnius import autoclass
    except ImportError:
        return        # not on a phone

    @run_on_ui_thread
    def paint():
        try:
            value = public_class('android.graphics.Color').parseColor(colour)
            window = autoclass('org.kivy.android.PythonActivity').mActivity.getWindow()
            # The window's background is what its decor view draws, so this
            # colours that too - without asking for the view, a View.
            window.setBackgroundDrawable(public_class('android.graphics.drawable.ColorDrawable')(value))
            window.setStatusBarColor(value)
            window.setNavigationBarColor(value)
        except Exception:
            log.exception('could not colour the window')

    paint()

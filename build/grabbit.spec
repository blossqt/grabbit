# PyInstaller spec for Grabbit - built by build_app.ps1
import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

project = os.path.abspath(os.path.join(SPECPATH, '..'))
app_dir = os.path.join(project, 'app')

# yt-dlp registers its own PyInstaller hook (extractors, lazy imports); the EJS
# JavaScript bundles YouTube needs are plain data files, so collect them here.
datas = collect_data_files('yt_dlp_ejs')

hiddenimports = [
    'grabbit', 'grabbit.app', 'grabbit.ui.main_window',
    'yt_dlp_ejs',
]
# gallery-dl is deliberately NOT bundled into this exe: it runs as its own
# program from tools\gallery-dl.exe (see app/grabbit/gallerydl.py).

# Qt's plugins, named here rather than left to PyInstaller's Qt hook. The hook
# drops any plugin whose Qt libraries it finds by a second path - and a build
# run from inside a packaged app, such as the Claude desktop app, sees AppData
# by two paths. It once dropped every plugin that way, qwindows.dll included,
# and the exe it made could not open a window. These are the kinds 1.1.0 had.
import PySide6
qt_plugins = os.path.join(os.path.dirname(PySide6.__file__), 'plugins')
qt_plugin_binaries = [
    (os.path.join(qt_plugins, kind, name), os.path.join('PySide6', 'plugins', kind))
    for kind in ('platforms', 'imageformats', 'iconengines', 'styles', 'tls', 'generic',
                 'networkinformation')
    if os.path.isdir(os.path.join(qt_plugins, kind))
    for name in sorted(os.listdir(os.path.join(qt_plugins, kind)))
    if name.lower().endswith('.dll')
]
if not any(dest.endswith('platforms') and src.endswith('qwindows.dll') for src, dest in qt_plugin_binaries):
    raise SystemExit('Qt\'s Windows platform plugin (qwindows.dll) was not found - the exe could not start')

a = Analysis(
    [os.path.join(app_dir, 'main.py')],
    pathex=[app_dir],
    binaries=qt_plugin_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    excludes=[
        'tkinter', 'unittest', 'pydoc_data', 'lib2to3', 'test',
        'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtQuickWidgets',
        'PySide6.Qt3DCore', 'PySide6.QtDesigner', 'PySide6.QtHelp',
        'PySide6.QtTest', 'PySide6.QtSql', 'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineWidgets', 'PySide6.QtCharts', 'PySide6.QtMultimedia',
        'PySide6.QtOpenGL', 'PySide6.QtPdf', 'PySide6.QtPdfWidgets',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Grabbit',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # Windowed by default; set GRABBIT_CONSOLE=1 to build a console variant
    # that prints tracebacks, which is the only way to debug startup failures.
    console=os.environ.get('GRABBIT_CONSOLE') == '1',
    disable_windowed_traceback=False,
    icon=os.path.join(SPECPATH, 'grabbit.ico'),
    version=os.path.join(SPECPATH, 'version_info.txt'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Grabbit',
)

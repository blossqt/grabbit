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

a = Analysis(
    [os.path.join(app_dir, 'main.py')],
    pathex=[app_dir],
    binaries=[],
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

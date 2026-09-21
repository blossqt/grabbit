"""Render Grabbit's icon everywhere it is baked into a file.

    python build/make_icon.py

Writes the multi-size .ico the packaged exe carries, and the phone's launcher
icon and splash image. All three come from the same drawing the running app
uses for its window and tray (grabbit.ui.icons.render_app_icon), so the icon a
person sees in the Start menu, on the taskbar and on a phone is one icon.

The phone's two used to be one-off images, which is how they came to be a
different blue from the desktop's: each was drawn in whatever the accent
colour was on the day, and then simply kept.
"""

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

from PySide6.QtCore import QBuffer, QIODevice  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication     # noqa: E402

HERE = Path(__file__).resolve().parent
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)

LAUNCHER_SIZE = 512
# The splash is the icon, centred on the app's background. The colour is the
# one build_apk.sh hands p4a as --presplash-color, which fills the rest of the
# screen around this image - the two must agree or the square shows.
SPLASH_SIZE, SPLASH_ICON, SPLASH_BACKGROUND = 768, 320, '#17171c'


def png_bytes(pixmap) -> bytes:
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    if not pixmap.save(buffer, 'PNG'):
        raise RuntimeError(f'could not encode the {pixmap.width()}px icon')
    data = bytes(buffer.data())
    buffer.close()
    return data


def write_ico(render, out: Path) -> None:
    # Each size drawn at that size, rather than one large image shrunk: the
    # small ones stay crisp.
    images = [(size, png_bytes(render(size))) for size in ICO_SIZES]
    header = struct.pack('<HHH', 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b'', b''
    for size, data in images:
        entries += struct.pack('<BBBBHHII', size % 256, size % 256, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    out.write_bytes(header + entries + blobs)
    print(f'wrote {out} ({out.stat().st_size} bytes, {len(images)} sizes)')


def write_splash(render, out: Path) -> None:
    image = QImage(SPLASH_SIZE, SPLASH_SIZE, QImage.Format_RGB32)
    image.fill(QColor(SPLASH_BACKGROUND))
    painter = QPainter(image)
    corner = (SPLASH_SIZE - SPLASH_ICON) // 2
    painter.drawPixmap(corner, corner, render(SPLASH_ICON))
    painter.end()
    if not image.save(str(out), 'PNG'):
        raise RuntimeError(f'could not write {out}')
    print(f'wrote {out} ({SPLASH_SIZE}px)')


def main():
    app = QApplication(sys.argv)
    from grabbit.ui.icons import render_app_icon

    write_ico(render_app_icon, HERE / 'grabbit.ico')

    launcher = HERE / 'android' / 'icon.png'
    if not render_app_icon(LAUNCHER_SIZE).save(str(launcher), 'PNG'):
        raise RuntimeError(f'could not write {launcher}')
    print(f'wrote {launcher} ({LAUNCHER_SIZE}px)')

    write_splash(render_app_icon, HERE / 'android' / 'presplash.png')
    app.quit()


if __name__ == '__main__':
    main()

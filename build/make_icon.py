"""Render Grabbit's icon to a multi-size .ico for the packaged exe."""

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

from PySide6.QtCore import QBuffer, QIODevice  # noqa: E402
from PySide6.QtWidgets import QApplication     # noqa: E402

SIZES = (16, 24, 32, 48, 64, 128, 256)


def main():
    app = QApplication(sys.argv)
    from grabbit.ui.icons import app_icon
    icon = app_icon()

    images = []
    for size in SIZES:
        pixmap = icon.pixmap(size, size)
        if pixmap.width() != size:
            pixmap = pixmap.scaled(size, size)
        buffer = QBuffer()
        buffer.open(QIODevice.WriteOnly)
        if not pixmap.save(buffer, 'PNG'):
            raise RuntimeError(f'could not encode the {size}px icon')
        images.append((size, bytes(buffer.data())))
        buffer.close()

    out = Path(__file__).resolve().parent / 'grabbit.ico'
    header = struct.pack('<HHH', 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b'', b''
    for size, data in images:
        entries += struct.pack('<BBBBHHII', size % 256, size % 256, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    out.write_bytes(header + entries + blobs)
    print(f'wrote {out} ({out.stat().st_size} bytes, {len(images)} sizes)')
    app.quit()


if __name__ == '__main__':
    main()

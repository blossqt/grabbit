"""Choosing one frame of a GIF to save as a picture - grabbit.gifs' half in the
add dialog: the frame above, a slider along the frames, and a step either way.

The GIF is fetched and read once, on a thread of its own. Each frame is kept as
a small JPEG to look at, so even a long GIF holds little.
"""

import threading

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QSlider, QToolButton, QVBoxLayout, QWidget

from .. import gifs
from . import icons, theme

PREVIEW_W, PREVIEW_H = 384, 216


class _Signals(QObject):
    """Has no parent on purpose: the GIF can still be on its way when the dialog
    closes, and a signal nobody listens to any more is harmless."""
    read = Signal(object)          # [jpeg bytes, ...]
    failed = Signal(str)


def _shown(image: QImage, scale: float) -> bytes:
    """One frame as it is looked at: fitted to the picture, over its black."""
    size = image.size().scaled(round(PREVIEW_W * scale), round(PREVIEW_H * scale),
                               Qt.KeepAspectRatio)
    flat = QImage(size, QImage.Format_RGB32)
    flat.fill(QColor('#000'))
    painter = QPainter(flat)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    painter.drawImage(flat.rect(), image)
    painter.end()
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    flat.save(buffer, 'JPG', 88)
    return bytes(data)


def read_frames(url: str, scale: float = 1.0, headers: dict | None = None) -> list:
    """Every frame of the GIF at `url`, ready to look at."""
    return [_shown(image, scale) for image in gifs.frames(gifs.fetch(url, headers))]


class GifPicker(QWidget):
    """The frame, the slider, and which frame of how many."""

    def __init__(self, url: str, headers: dict | None = None, parent=None):
        super().__init__(parent)
        self.url = url
        self.headers = dict(headers or {})
        self.shown: list = []          # each frame as a JPEG to look at
        self._asked = False
        self._signals = _Signals()
        self._signals.read.connect(self._on_read)
        self._signals.failed.connect(self._on_failed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.picture = theme.Label('Reading the GIF…')
        self.picture.setFixedSize(PREVIEW_W, PREVIEW_H)
        self.picture.setAlignment(Qt.AlignCenter)
        self.picture.setWordWrap(True)
        self.picture.setStyleSheet('background: #000; border-radius: 6px; color: #9aa0a6;')
        layout.addWidget(self.picture, 0, Qt.AlignLeft)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.back = QToolButton()
        self.back.setIcon(icons.icon('back', icons.theme_color('text'), 16))
        self.back.setToolTip('The frame before')
        self.back.clicked.connect(lambda: self.step(-1))
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.setPageStep(10)
        self.slider.valueChanged.connect(self._show)
        self.forward = QToolButton()
        self.forward.setIcon(icons.icon('next', icons.theme_color('text'), 16))
        self.forward.setToolTip('The frame after')
        self.forward.clicked.connect(lambda: self.step(1))
        self.count = theme.Label()
        self.count.setMinimumWidth(120)
        for button in (self.back, self.forward):
            button.setFixedSize(30, 26)
        for widget in (self.back, self.slider, self.forward, self.count):
            row.addWidget(widget, 1 if widget is self.slider else 0)
        layout.addLayout(row)
        self._enable(False)

    # --------------------------------------------------------------- answer
    @property
    def at(self) -> int:
        """The chosen frame's index, from 0."""
        return self.slider.value()

    # --------------------------------------------------------------- moving
    def step(self, direction: int):
        self.slider.setValue(self.slider.value() + direction)

    def _enable(self, on: bool):
        for widget in (self.back, self.slider, self.forward):
            widget.setEnabled(on)

    def _show(self, index: int):
        if not self.shown:
            return
        self.count.setText(f'Frame {index + 1} of {len(self.shown)}')
        pixmap = QPixmap()
        if pixmap.loadFromData(self.shown[index]):
            ratio = self.devicePixelRatioF()
            pixmap.setDevicePixelRatio(ratio)
            self.picture.setPixmap(pixmap)

    # -------------------------------------------------------------- reading
    def fetch(self):
        """Read the GIF, the first time it is wanted."""
        if self._asked:
            return
        self._asked = True
        signals, url, headers, scale = self._signals, self.url, self.headers, self.devicePixelRatioF()

        def work():
            try:
                signals.read.emit(read_frames(url, scale, headers))
            except gifs.GifError as exc:
                signals.failed.emit(str(exc))
            except Exception as exc:                  # never silently
                signals.failed.emit(f'Could not read the GIF: {exc}')

        threading.Thread(target=work, name='grabbit-gif', daemon=True).start()

    def _on_read(self, shown: list):
        self.shown = shown
        self.slider.setRange(0, len(shown) - 1)
        self._enable(len(shown) > 1)
        self._show(self.slider.value())

    def _on_failed(self, message: str):
        self.picture.setPixmap(QPixmap())
        self.picture.setText(message)

"""Choosing one frame of a video to save as a picture - the desktop's half of
grabbit.frames: a slider along the video, the frame under it above, and a
step either way for the frame just before or after."""

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QSlider, QToolButton,
                               QVBoxLayout, QWidget)

from .. import frames

PREVIEW_W, PREVIEW_H = 384, 216


class _Signals(QObject):
    """How the reading thread hands frames to the window.

    It has no parent on purpose: a frame can still be on its way when the
    dialog closes, and handing it to a signal nobody listens to any more is
    harmless, where handing it to a deleted widget is not.
    """
    frame = Signal(float, object)
    failed = Signal(str)
    stream = Signal(object)


class FramePicker(QWidget):
    """The slider, the frame, and PNG or JPG."""

    def __init__(self, url: str, settings, duration: float = 0.0, parent=None):
        super().__init__(parent)
        self.url = url
        self.settings = settings
        self.duration = float(duration or 0)
        self.fps = 0.0
        self._reader = None
        self._signals = _Signals()
        self._signals.frame.connect(self._on_frame)
        self._signals.failed.connect(self._on_failed)
        self._signals.stream.connect(self._on_stream)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(6)
        self.picture = QLabel('Finding the video…')
        self.picture.setFixedSize(PREVIEW_W, PREVIEW_H)
        self.picture.setAlignment(Qt.AlignCenter)
        self.picture.setWordWrap(True)
        self.picture.setStyleSheet('background: #000; border-radius: 6px; color: #9aa0a6;')
        layout.addWidget(self.picture, 0, Qt.AlignLeft)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.back = QToolButton()
        self.back.setText('‹')
        self.back.setToolTip('The frame before')
        self.back.clicked.connect(lambda: self.step(-1))
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, max(1, int(self.duration * 1000)))
        self.slider.setSingleStep(100)
        self.slider.setPageStep(5000)
        self.slider.valueChanged.connect(self._moved)
        self.slider.sliderReleased.connect(self.fetch)
        self.forward = QToolButton()
        self.forward.setText('›')
        self.forward.setToolTip('The frame after')
        self.forward.clicked.connect(lambda: self.step(1))
        self.clock = QLabel()
        self.clock.setMinimumWidth(120)
        for button in (self.back, self.forward):
            button.setFixedSize(30, 26)
            font = button.font()
            font.setPointSizeF(font.pointSizeF() * 1.4)
            button.setFont(font)
        for widget in (self.back, self.slider, self.forward, self.clock):
            row.addWidget(widget, 1 if widget is self.slider else 0)
        layout.addLayout(row)

        kinds = QHBoxLayout()
        kinds.addWidget(QLabel('Save the frame as'))
        self.kind = QComboBox()
        for value, label in frames.IMAGE_TYPES:
            self.kind.addItem(label, value)
        index = self.kind.findData(getattr(settings, 'frame_format', '') or 'png')
        self.kind.setCurrentIndex(max(0, index))
        kinds.addWidget(self.kind)
        kinds.addStretch(1)
        layout.addLayout(kinds)

        # Fetch where the slider stops, not everywhere it passes through.
        self._rest = QTimer(self)
        self._rest.setSingleShot(True)
        self._rest.setInterval(250)
        self._rest.timeout.connect(self.fetch)
        self._show_clock()

    # --------------------------------------------------------------- answer
    @property
    def at(self) -> float:
        return self.slider.value() / 1000.0

    @property
    def image_type(self) -> str:
        return self.kind.currentData()

    # --------------------------------------------------------------- moving
    def _moved(self, _value):
        self._show_clock()
        self._rest.start()

    def _show_clock(self):
        total = self.duration
        self.clock.setText(f'{frames.clock(self.at)} / {frames.clock(total)}' if total
                           else frames.clock(self.at))

    def step(self, direction: int):
        """One frame on or back - or a tenth of a second while the rate is unknown."""
        step = 1.0 / self.fps if self.fps else 0.1
        self.slider.setValue(self.slider.value() + round(direction * step * 1000))
        self.fetch()

    # -------------------------------------------------------------- reading
    def fetch(self):
        self._rest.stop()
        if self._reader is None:
            signals = self._signals
            self._reader = frames.FrameReader(
                self.url, self.settings, on_frame=signals.frame.emit,
                on_error=signals.failed.emit, on_stream=signals.stream.emit,
                width=PREVIEW_W * 2)
        self._reader.want(self.at)

    def stop(self):
        if self._reader is not None:
            self._reader.close()
            self._reader = None

    def _on_stream(self, stream):
        self.fps = stream.fps
        if stream.duration and not self.duration:
            self.duration = stream.duration
            self.slider.setRange(0, int(stream.duration * 1000))
            self._show_clock()

    def _on_frame(self, at, data):
        pixmap = QPixmap()
        if not pixmap.loadFromData(data):
            self._on_failed('That frame could not be shown')
            return
        self.picture.setPixmap(pixmap.scaled(self.picture.size(), Qt.KeepAspectRatio,
                                             Qt.SmoothTransformation))

    def _on_failed(self, message):
        self.picture.setPixmap(QPixmap())
        self.picture.setText(message)

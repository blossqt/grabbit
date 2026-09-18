"""Thumbnail grid for picking carousel slides / playlist items, and a preview window."""

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QPushButton, QSizePolicy, QStyle, QStyledItemDelegate, QVBoxLayout)

from ..util import human_duration, human_size
from . import thumbs

ITEM_ROLE = Qt.UserRole + 1
PIXMAP_ROLE = Qt.UserRole + 2
CHECKED_ROLE = Qt.UserRole + 3

TILE = 148
TILE_H = 148


class _TileDelegate(QStyledItemDelegate):
    def paint(self, painter: QPainter, option, index):
        item = index.data(ITEM_ROLE)
        pixmap = index.data(PIXMAP_ROLE)
        checked = bool(index.data(CHECKED_ROLE))
        palette = option.palette
        accent = palette.highlight().color()

        rect = QRectF(option.rect).adjusted(4, 4, -4, -4)
        path = QPainterPath()
        path.addRoundedRect(rect, 8, 8)

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        backdrop = palette.window().color()
        painter.fillPath(path, backdrop.darker(108) if backdrop.lightness() > 128 else backdrop.lighter(125))

        if isinstance(pixmap, QPixmap) and not pixmap.isNull():
            painter.save()
            painter.setClipPath(path)
            scaled = pixmap.scaled(rect.size().toSize(), Qt.KeepAspectRatioByExpanding,
                                   Qt.SmoothTransformation)
            x = rect.x() + (rect.width() - scaled.width()) / 2
            y = rect.y() + (rect.height() - scaled.height()) / 2
            painter.drawPixmap(int(x), int(y), scaled)
            painter.restore()
        else:
            painter.setPen(QPen(palette.color(palette.ColorGroup.Disabled, palette.ColorRole.Text)))
            has_thumbnail = bool(item and (item.thumbnail or item.preview))
            if has_thumbnail:
                painter.drawText(option.rect, Qt.AlignCenter, 'loading…')
            else:
                glyph = {'audio': '♪', 'video': '▶'}.get(getattr(item, 'kind', ''), '□')
                glyph_font = QFont(option.font)
                glyph_font.setPointSizeF(option.font.pointSizeF() + 16)
                painter.setFont(glyph_font)
                painter.drawText(option.rect, Qt.AlignCenter, glyph)
                painter.setFont(option.font)

        if not checked:  # dim what will not be downloaded
            painter.fillPath(path, QColor(0, 0, 0, 110))

        # badge: duration for video/audio, size for photos
        label = ''
        if item is not None:
            if item.kind in ('video', 'audio') and item.duration:
                label = human_duration(item.duration)
            elif item.kind == 'image' and item.width and item.height:
                label = f'{item.width}×{item.height}'
            elif item.filesize:
                label = human_size(item.filesize)
            if item.kind == 'audio':
                label = ('♪ ' + label).strip()
            elif item.kind == 'video':
                label = ('▶ ' + label).strip()
        if label:
            font = QFont(option.font)
            font.setPointSizeF(max(7.0, font.pointSizeF() - 1.0))
            painter.setFont(font)
            metrics = painter.fontMetrics()
            width = metrics.horizontalAdvance(label) + 10
            badge = QRectF(rect.left() + 6, rect.bottom() - metrics.height() - 10,
                           width, metrics.height() + 4)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(0, 0, 0, 165))
            painter.drawRoundedRect(badge, 5, 5)
            painter.setPen(QPen(QColor('#ffffff')))
            painter.drawText(badge, Qt.AlignCenter, label)

        # index pill
        number = str(index.row() + 1)
        font = QFont(option.font)
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1.0))
        painter.setFont(font)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 150))
        painter.drawEllipse(QRectF(rect.left() + 6, rect.top() + 6, 20, 20))
        painter.setPen(QPen(QColor('#ffffff')))
        painter.drawText(QRectF(rect.left() + 6, rect.top() + 6, 20, 20), Qt.AlignCenter, number)

        # check mark
        marker = QRectF(rect.right() - 27, rect.top() + 6, 21, 21)
        painter.setPen(QPen(QColor(255, 255, 255, 220), 1.4))
        painter.setBrush(accent if checked else QColor(0, 0, 0, 120))
        painter.drawEllipse(marker)
        if checked:
            painter.setPen(QPen(QColor('#ffffff'), 2.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            left, top, size = marker.left(), marker.top(), marker.width()
            painter.drawPolyline([
                QPointF(left + size * 0.27, top + size * 0.52),
                QPointF(left + size * 0.44, top + size * 0.70),
                QPointF(left + size * 0.74, top + size * 0.32),
            ])

        if checked or (option.state & QStyle.StateFlag.State_HasFocus):
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(accent, 2))
            painter.drawPath(path)
        painter.restore()

    def sizeHint(self, option, index):
        return QSize(TILE, TILE_H)


class GalleryGrid(QListWidget):
    """Grid of tiles; click toggles, double-click previews."""

    selection_changed = Signal()
    preview_requested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setViewMode(QListWidget.IconMode)
        self.setResizeMode(QListWidget.Adjust)
        self.setMovement(QListWidget.Static)
        self.setSelectionMode(QListWidget.NoSelection)
        self.setSpacing(2)
        self.setUniformItemSizes(True)
        self.setItemDelegate(_TileDelegate(self))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._loader = thumbs.loader()
        self._loader.ready.connect(self._on_thumb)
        self.itemClicked.connect(lambda item: self.toggle(self.row(item)))
        self.itemDoubleClicked.connect(lambda item: self.preview_requested.emit(self.row(item)))

    # ------------------------------------------------------------------ data
    def set_items(self, items):
        self.clear()
        for media_item in items:
            entry = QListWidgetItem()
            entry.setData(ITEM_ROLE, media_item)
            entry.setData(CHECKED_ROLE, not media_item.optional)
            entry.setSizeHint(QSize(TILE, TILE_H))
            entry.setToolTip(media_item.title)
            self.addItem(entry)
            url = media_item.thumbnail or media_item.preview
            if url:
                pixmap = self._loader.request(url, media_item.headers)
                if pixmap is not None:
                    entry.setData(PIXMAP_ROLE, pixmap)
        self._resize_to_contents()
        self.selection_changed.emit()

    def _on_thumb(self, url: str, pixmap: QPixmap):
        for row in range(self.count()):
            entry = self.item(row)
            media_item = entry.data(ITEM_ROLE)
            if media_item and (media_item.thumbnail == url or media_item.preview == url) \
                    and not entry.data(PIXMAP_ROLE):
                entry.setData(PIXMAP_ROLE, pixmap)
                self.update(self.indexFromItem(entry))

    def _resize_to_contents(self):
        """Show up to three rows of tiles without an outer scrollbar."""
        count = max(1, self.count())
        width = max(self.viewport().width(), 600)
        per_row = max(1, width // (TILE + 4))
        rows = min(3, (count + per_row - 1) // per_row)
        self.setFixedHeight(rows * (TILE_H + 6) + 8)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._resize_to_contents()

    # ------------------------------------------------------------ selection
    def toggle(self, row: int, value: bool | None = None):
        entry = self.item(row)
        if entry is None:
            return
        checked = bool(entry.data(CHECKED_ROLE)) if value is None else value
        entry.setData(CHECKED_ROLE, (not checked) if value is None else value)
        self.update(self.indexFromItem(entry))
        self.selection_changed.emit()

    def set_all(self, checked: bool):
        for row in range(self.count()):
            self.item(row).setData(CHECKED_ROLE, checked)
        self.viewport().update()
        self.selection_changed.emit()

    def items(self) -> list:
        return [self.item(row).data(ITEM_ROLE) for row in range(self.count())]

    def is_checked(self, row: int) -> bool:
        entry = self.item(row)
        return bool(entry and entry.data(CHECKED_ROLE))

    def selected_items(self) -> list:
        return [self.item(row).data(ITEM_ROLE) for row in range(self.count())
                if self.item(row).data(CHECKED_ROLE)]

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            index = self.currentIndex()
            if index.isValid():
                if event.key() == Qt.Key_Space:
                    self.toggle(index.row())
                else:
                    self.preview_requested.emit(index.row())
                return
        super().keyPressEvent(event)


class PreviewDialog(QDialog):
    """Full-size look at one slide, with arrows to step through the post."""

    def __init__(self, items, start: int, checked: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Preview')
        self.resize(900, 720)
        self.items = items
        self.checked = list(checked)
        self.index = start
        self._loader = thumbs.loader()
        self._loader.ready.connect(self._on_thumb)

        layout = QVBoxLayout(self)
        self.image = QLabel(alignment=Qt.AlignCenter)
        self.image.setMinimumHeight(420)
        self.image.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.image, 1)

        self.caption = QLabel(alignment=Qt.AlignCenter)
        self.caption.setObjectName('Muted')
        self.caption.setWordWrap(True)
        layout.addWidget(self.caption)

        bar = QHBoxLayout()
        self.previous_button = QPushButton('←  Previous')
        self.next_button = QPushButton('Next  →')
        self.include_button = QPushButton()
        self.include_button.setCheckable(True)
        close_button = QPushButton('Close')
        self.previous_button.clicked.connect(lambda: self.step(-1))
        self.next_button.clicked.connect(lambda: self.step(1))
        self.include_button.toggled.connect(self._set_included)
        close_button.clicked.connect(self.accept)
        bar.addWidget(self.previous_button)
        bar.addWidget(self.next_button)
        bar.addStretch(1)
        bar.addWidget(self.include_button)
        bar.addWidget(close_button)
        layout.addLayout(bar)

        self._show_current()

    def step(self, delta: int):
        if not self.items:
            return
        self.index = (self.index + delta) % len(self.items)
        self._show_current()

    def _set_included(self, value: bool):
        if 0 <= self.index < len(self.checked):
            self.checked[self.index] = value
        self._update_include_label()

    def _update_include_label(self):
        included = self.checked[self.index] if self.index < len(self.checked) else False
        self.include_button.setText('✓  Included' if included else 'Include this item')

    def _show_current(self):
        item = self.items[self.index]
        self.setWindowTitle(f'{item.title}  ({self.index + 1} of {len(self.items)})')
        parts = [f'{self.index + 1} of {len(self.items)}', item.title]
        if item.width and item.height:
            parts.append(f'{item.width}×{item.height}')
        if item.duration:
            parts.append(human_duration(item.duration))
        if item.filesize:
            parts.append(human_size(item.filesize))
        self.caption.setText('   ·   '.join(p for p in parts if p))
        self.previous_button.setEnabled(len(self.items) > 1)
        self.next_button.setEnabled(len(self.items) > 1)
        self.include_button.blockSignals(True)
        self.include_button.setChecked(bool(self.checked[self.index]) if self.index < len(self.checked) else False)
        self.include_button.blockSignals(False)
        self._update_include_label()

        url = item.preview or item.thumbnail
        self.image.setText('Loading preview…')
        pixmap = self._loader.request(url, item.headers) if url else None
        if pixmap is not None:
            self._set_pixmap(pixmap)

    def _on_thumb(self, url: str, pixmap: QPixmap):
        item = self.items[self.index]
        if url in (item.preview, item.thumbnail):
            self._set_pixmap(pixmap)

    def _set_pixmap(self, pixmap: QPixmap):
        if pixmap.isNull():
            self.image.setText('No preview available')
            return
        area = self.image.size()
        self.image.setPixmap(pixmap.scaled(area, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        item = self.items[self.index] if self.items else None
        if item:
            pixmap = self._loader.cached(item.preview or item.thumbnail)
            if pixmap is not None:
                self._set_pixmap(pixmap)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Left, Qt.Key_Up):
            self.step(-1)
        elif event.key() in (Qt.Key_Right, Qt.Key_Down):
            self.step(1)
        elif event.key() == Qt.Key_Space:
            self.include_button.toggle()
        else:
            super().keyPressEvent(event)

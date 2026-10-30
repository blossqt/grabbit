"""The suite's settings windows, laid out alike: the pages down the left under the app's logo, a
pill sliding to the one chosen; each page a title, then framed sections of settings, rising in as
it comes up. An app's own sidebar (sidebar()) takes the same look.

Written once, here in Hanami, and copied into each Qt app's ui\\ folder (tests\\check_copies.py
keeps them the same); it uses only suitetheme.py, which is shared the same way.

    frame = SettingsFrame(window, icons.render_app_icon(28))
    layout = frame.add('\\ue80f', 'General')       # a Segoe Fluent Icons glyph, the page's name
    box_frame, box = section('Starting up')
    layout.addWidget(box_frame)
"""

from __future__ import annotations

import math

from PySide6.QtCore import (QEasingCurve, QEvent, QModelIndex, QObject, QPersistentModelIndex, QRectF, QSize, Qt,
                            QVariantAnimation, Signal)
from PySide6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLayout,
                               QScrollArea, QStackedWidget, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget)

from . import suitetheme as theme


def glyph_font(point_size: float) -> QFont:
    """Windows 11's icon font (Windows 10's, if that is all there is)."""
    f = QFont('Segoe Fluent Icons')
    if not f.exactMatch():
        f = QFont('Segoe MDL2 Assets')
    f.setPointSizeF(point_size)
    return f


def label(text: str, size: float = 9.5, weight=QFont.Normal, role: str = '', wrap: bool = False) -> QLabel:
    """Text on a settings page: role 'dim' for a line about something, 'error' for a problem."""
    line = QLabel(text)
    line.setFont(theme.font(size, weight))
    if role:
        line.setProperty('role', role)
    line.setWordWrap(wrap)
    return line


def note(text: str) -> QLabel:
    """A line about the settings near it: smaller, dimmer, wrapped."""
    return label(text, 8.5, role='dim', wrap=True)


def section(title: str) -> tuple[QFrame, QVBoxLayout]:
    """A panel on a page, under its own heading."""
    frame = QFrame()
    frame.setObjectName('section')
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18, 14, 18, 16)
    layout.setSpacing(8)
    if title:                           # none for a page's first, when it would say the page's name again
        layout.addWidget(label(title, 10.5, QFont.DemiBold))
    return frame, layout


def form() -> QFormLayout:
    """Settings with a name before each, in a section: the names in a column on the left."""
    rows = QFormLayout()
    rows.setContentsMargins(0, 0, 0, 0)
    rows.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    rows.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
    rows.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
    rows.setHorizontalSpacing(16)
    rows.setVerticalSpacing(8)
    return rows


def framed(widget: QWidget) -> QWidget:
    """A list on a settings page, in a box of its own."""
    widget.setObjectName('list')
    return widget


def switch(text: str, about: str, on: bool) -> tuple[QWidget, QCheckBox]:
    """A setting: a check box (a switch, in the liquid look), and a line about it below."""
    box = QCheckBox(text)
    box.setChecked(on)
    box.setCursor(Qt.PointingHandCursor)
    holder = QWidget()
    layout = QVBoxLayout(holder)
    layout.setContentsMargins(0, 2, 0, 2)
    layout.setSpacing(1)
    layout.addWidget(box)
    if about:
        line = label(about, 8.5, role='dim', wrap=True)
        line.setObjectName('about')
        indent(line)
        layout.addWidget(line)
    return holder, box


def indent(line: QLabel) -> None:
    """A line about a setting starts under its words, past the box (or switch) beside them."""
    liquid = theme.look() == 'liquid' and theme.glass_on()
    line.setContentsMargins((theme.SuiteStyle.SWITCH[0] if liquid else theme.SuiteStyle.SIZE) + 8, 0, 0, 0)


def page(title: str, about: str = '') -> tuple[QWidget, QVBoxLayout]:
    """A page: its title, and a line about it if it needs one."""
    widget = QWidget()
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(4, 4, 4, 4)
    layout.setSpacing(12)
    layout.addWidget(label(title, 17, QFont.DemiBold))
    if about:
        layout.addWidget(label(about, 9, role='dim', wrap=True))
    return widget, layout


def scrolled(widget: QWidget) -> QScrollArea:
    """A page taller than the window, in a scroll area of glass."""
    widget.setObjectName('scrolled')
    area = QScrollArea()
    area.setObjectName('scrolled')
    area.setWidget(widget)
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    return area


class Nav(QWidget):
    """The pages down the left: a pill slides to the one chosen - springing into place, and
    stretching as it goes, in the liquid look."""

    chosen = Signal(int)
    ROW = 40

    def __init__(self, items: list[tuple[str, str]] | None = None):
        super().__init__()
        self._items = list(items or [])
        self._current = 0
        self._hover = -1
        self._at = 0.0                  # where the pill is, in rows
        self._moving = 0.0              # how far through a move it is, for its stretch
        self._slide = None
        self.setFixedWidth(196)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)

    def add(self, mark: str, title: str) -> None:
        self._items.append((mark, title))
        self.updateGeometry()
        self.update()

    def titles(self) -> list[str]:
        return [title for _, title in self._items]

    def sizeHint(self) -> QSize:
        return QSize(196, self.ROW * len(self._items) + 16)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def current(self) -> int:
        return self._current

    def choose(self, index: int) -> None:
        if not 0 <= index < len(self._items) or index == self._current:
            return
        start = self._at
        self._current = index
        self.chosen.emit(index)
        theme.stop_animation(self._slide)
        if not theme.motion_on():
            self._at = float(index)
            self.update()
            return
        slide = self._slide = QVariantAnimation(self)
        liquid = theme.look() == 'liquid' and theme.glass_on()
        curve = QEasingCurve(QEasingCurve.OutBack if liquid else QEasingCurve.OutCubic)
        if liquid:
            curve.setOvershoot(1.1)
        slide.setEasingCurve(curve)
        slide.setDuration(360 if liquid else 220)
        slide.setStartValue(start)
        slide.setEndValue(float(index))

        def step(value):
            self._at = float(value)
            span = abs(index - start) or 1
            self._moving = min(1.0, max(0.0, abs(self._at - start) / span))
            self.update()

        def done():
            self._moving = 0.0
            self.update()
        slide.valueChanged.connect(step)
        slide.finished.connect(done)
        theme.animate(slide)

    def _row_at(self, y: float) -> int:
        index = int((y - 8) // self.ROW)
        return index if 0 <= index < len(self._items) else -1

    def mousePressEvent(self, event):
        index = self._row_at(event.position().y())
        if index >= 0:
            self.choose(index)

    def mouseMoveEvent(self, event):
        index = self._row_at(event.position().y())
        if index != self._hover:
            self._hover = index
            self.update()

    def leaveEvent(self, event):
        self._hover = -1
        self.update()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Down, Qt.Key_Right):
            self.choose(self._current + 1)
        elif event.key() in (Qt.Key_Up, Qt.Key_Left):
            self.choose(self._current - 1)
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event):
        c = theme.colors()
        dark = theme.is_dark()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        width = self.width() - 16
        if 0 <= self._hover != self._current:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 14) if dark else QColor(0, 0, 0, 10))
            p.drawRoundedRect(QRectF(8, 8 + self._hover * self.ROW + 2, width, self.ROW - 4), 8, 8)
        pill = paint_pill(p, QRectF(8, 8 + self._at * self.ROW + 2, width, self.ROW - 4), self._moving)
        for index, (mark, title) in enumerate(self._items):
            row = QRectF(8, 8 + index * self.ROW, width, self.ROW)
            selected = index == self._current
            p.setPen(QColor(theme.ink()) if selected else c.dim)
            p.setFont(glyph_font(11))
            p.drawText(QRectF(row.left() + 14, row.top(), 24, row.height()), Qt.AlignCenter, mark)
            p.setPen(c.text)
            p.setFont(theme.font(9.5, QFont.DemiBold if selected else QFont.Normal))
            p.drawText(QRectF(row.left() + 46, row.top(), row.width() - 50, row.height()),
                       Qt.AlignVCenter | Qt.AlignLeft, title)
        if self.hasFocus() and self._current >= 0:
            p.setPen(QPen(QColor(theme.ink()), 1, Qt.DotLine))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(pill.adjusted(-2, -2, 2, 2), 10, 10)
        p.end()


def paint_pill(p: QPainter, pill: QRectF, moving: float = 0.0) -> QRectF:
    """The chosen row's pill: a soft chip with the pink bar at its left - in the liquid look, a
    capsule of glass, stretching as it moves (moving: how far through a move it is). Gives the
    rectangle it filled."""
    dark = theme.is_dark()
    if theme.look() == 'liquid' and theme.glass_on():
        stretch = 6 * math.sin(math.pi * moving)
        pill = pill.adjusted(0, -stretch / 2, 0, stretch / 2)
        round_ = pill.height() / 2
        body = QLinearGradient(pill.topLeft(), pill.bottomLeft())
        body.setColorAt(0, QColor(255, 255, 255, 52 if dark else 235))
        body.setColorAt(1, QColor(255, 255, 255, 18 if dark else 140))
        p.setPen(Qt.NoPen)
        p.setBrush(body)
        p.drawRoundedRect(pill, round_, round_)
        theme._edge_light(p, pill.adjusted(0.5, 0.5, -0.5, -0.5), round_, 120, 150 if dark else 255, 1.1)
        if not dark:
            p.setPen(QPen(QColor(0, 0, 0, 22), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(pill, round_, round_)
    else:
        p.setPen(Qt.NoPen)
        p.setBrush(theme.colors().chip)
        p.drawRoundedRect(pill, 8, 8)
        bar = QRectF(pill.left() + 1, pill.center().y() - 8, 3, 16)
        p.setBrush(QColor(theme.ink()))
        p.drawRoundedRect(bar, 1.5, 1.5)
    return pill


# --------------------------------------------------------------------------- sidebars

COUNT = Qt.UserRole + 70        # a sidebar row's count, shown at its right; none for no count


class _SideRows(QStyledItemDelegate):
    """A sidebar's rows drawn as the settings windows' pages are: a row of Nav's height, its icon
    in a column (pink while chosen), its name bold while chosen, a faint tint under the pointer;
    a row that can't be chosen is a heading over the rows below it (or, empty, a gap)."""

    HEADING = 32

    def sizeHint(self, option, index):
        if not index.flags() & Qt.ItemIsEnabled:
            return QSize(10, self.HEADING if index.data() else 8)
        return QSize(10, Nav.ROW)

    def paint(self, p, option, index):
        c = theme.colors()
        r = QRectF(option.rect)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        text = str(index.data() or '')
        if not index.flags() & Qt.ItemIsEnabled:
            p.setPen(c.dim)
            p.setFont(theme.font(8.5, QFont.DemiBold))
            p.drawText(r.adjusted(14, 0, -8, -6), Qt.AlignLeft | Qt.AlignBottom, text)
            p.restore()
            return
        view = option.widget
        chosen = bool(index.flags() & Qt.ItemIsSelectable) and view is not None and view.currentIndex() == index
        ticked = index.data(Qt.CheckStateRole) == Qt.Checked
        if option.state & QStyle.State_MouseOver and not chosen:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 14) if theme.is_dark() else QColor(0, 0, 0, 10))
            p.drawRoundedRect(r.adjusted(4, 2, -4, -2), 8, 8)
        icon = index.data(Qt.DecorationRole)
        if isinstance(icon, QIcon) and not icon.isNull():
            ratio = view.devicePixelRatioF() if view is not None else 1.0
            mark = icon.pixmap(QSize(16, 16), ratio)
            if chosen or ticked:
                mark = _tinted(mark, QColor(theme.ink()))
            p.drawPixmap(QRectF(r.left() + 14 + 4, r.center().y() - 8, 16, 16), mark, QRectF(mark.rect()))
        right = r.right() - 12
        count = index.data(COUNT)
        if ticked:
            p.setPen(QColor(theme.ink()))
            p.setFont(glyph_font(10))
            p.drawText(QRectF(right - 16, r.top(), 16, r.height()), Qt.AlignCenter, '')
            right -= 24
        elif count not in (None, ''):
            p.setPen(c.dim)
            p.setFont(theme.font(9))                # (Segoe UI's figures are all one width)
            shown = str(count)
            width = p.fontMetrics().horizontalAdvance(shown)
            p.drawText(QRectF(right - width, r.top(), width, r.height()), Qt.AlignVCenter | Qt.AlignRight, shown)
            right -= width + 10
        p.setPen(c.text)
        p.setFont(theme.font(9.5, QFont.DemiBold if chosen else QFont.Normal))
        room = QRectF(r.left() + 46, r.top(), max(0.0, right - r.left() - 46), r.height())
        p.drawText(room, Qt.AlignVCenter | Qt.AlignLeft,
                   p.fontMetrics().elidedText(text, Qt.ElideRight, int(room.width())))
        p.restore()


def _tinted(pixmap: QPixmap, color: QColor) -> QPixmap:
    """An icon in one colour, its shape kept."""
    out = QPixmap(pixmap.size())
    out.setDevicePixelRatio(pixmap.devicePixelRatio())
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.drawPixmap(0, 0, pixmap)
    p.setCompositionMode(QPainter.CompositionMode_SourceIn)
    p.fillRect(out.rect(), color)
    p.end()
    return out


class _SidePill(QObject):
    """The chosen row's pill, under the rows, sliding from the row chosen before to the new one
    (springing, in the liquid look) - drawn on the list's viewport before its rows are."""

    def __init__(self, view: QAbstractItemView):
        super().__init__(view)
        self.view = view
        self._from = QPersistentModelIndex()
        self._t = 1.0
        self._slide = None
        view.viewport().installEventFilter(self)
        view.selectionModel().currentChanged.connect(self._moved)

    def _moved(self, current, previous) -> None:
        theme.stop_animation(self._slide)
        self._slide = None
        selectable = previous.isValid() and bool(previous.flags() & Qt.ItemIsSelectable)
        if not selectable or not current.isValid() or not theme.motion_on() or not self.view.isVisible():
            self._from, self._t = QPersistentModelIndex(), 1.0
            self.view.viewport().update()
            return
        self._from, self._t = QPersistentModelIndex(previous), 0.0
        slide = self._slide = QVariantAnimation(self)
        liquid = theme.look() == 'liquid' and theme.glass_on()
        curve = QEasingCurve(QEasingCurve.OutBack if liquid else QEasingCurve.OutCubic)
        if liquid:
            curve.setOvershoot(1.1)
        slide.setEasingCurve(curve)
        slide.setDuration(360 if liquid else 220)
        slide.setStartValue(0.0)
        slide.setEndValue(1.0)

        def step(value):
            self._t = float(value)
            self.view.viewport().update()
        slide.valueChanged.connect(step)
        theme.animate(slide)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Paint:
            current = self.view.currentIndex()
            if current.isValid() and current.flags() & Qt.ItemIsSelectable:
                end = QRectF(self.view.visualRect(current)).adjusted(4, 2, -4, -2)
                pill, moving = end, 0.0
                if self._t < 1.0 and self._from.isValid():
                    start = QRectF(self.view.visualRect(QModelIndex(self._from))).adjusted(4, 2, -4, -2)
                    t = self._t
                    pill = QRectF(start.left() + (end.left() - start.left()) * t,
                                  start.top() + (end.top() - start.top()) * t, end.width(), end.height())
                    moving = min(1.0, max(0.0, t))
                p = QPainter(obj)
                p.setRenderHint(QPainter.Antialiasing)
                paint_pill(p, pill, moving)
                p.end()
        return False


def sidebar(view: QAbstractItemView) -> None:
    """Give a list down a window's side the settings windows' look: rows of the same height and
    type, headings over them (rows that can't be chosen), counts at the right (COUNT), and the
    pill sliding to the row chosen. A checkable row that can't be chosen shows a tick."""
    view.setItemDelegate(_SideRows(view))
    view.setMouseTracking(True)
    view.viewport().setAttribute(Qt.WA_Hover)
    view.setFrameShape(QFrame.NoFrame)
    _SidePill(view)


class SettingsFrame:
    """A settings window's layout: the app's logo and "Settings" above the pages down the left,
    the page chosen on the right - and, for a window whose settings are kept with OK, a row
    under the pages for its buttons (footer)."""

    def __init__(self, window: QWidget, logo: QPixmap, title: str = 'Settings'):
        self.window = window
        window.setObjectName('settings')
        window.setMinimumSize(800, 560)
        outer = QHBoxLayout(window)
        outer.setContentsMargins(12, 16, 20, 16)
        outer.setSpacing(12)
        side = QVBoxLayout()
        side.setSpacing(10)
        top = QHBoxLayout()
        top.setContentsMargins(14, 0, 0, 0)
        top.setSpacing(10)
        mark = QLabel()
        mark.setPixmap(logo)
        top.addWidget(mark)
        top.addWidget(label(title, 13, QFont.DemiBold), 1)
        side.addLayout(top)
        self.nav = Nav()
        side.addWidget(self.nav)
        side.addStretch(1)
        self.side = side
        outer.addLayout(side)
        right = QVBoxLayout()
        right.setSpacing(12)
        self.pages = QStackedWidget()
        right.addWidget(self.pages, 1)
        self.right = right
        outer.addLayout(right, 1)
        self.nav.chosen.connect(self.pages.setCurrentIndex)
        theme.restyle(window, style)
        theme.on_restyle(self._restyled)

    def add(self, mark: str, title: str, about: str = '', scroll: bool = True) -> QVBoxLayout:
        """A page, in the list and on the right: its layout, to fill with sections (and end with
        a stretch)."""
        widget, layout = page(title, about)
        self.nav.add(mark, title)
        self.pages.addWidget(scrolled(widget) if scroll else widget)
        return layout

    def add_widget(self, mark: str, title: str, widget: QWidget) -> None:
        """A page made elsewhere, as it is."""
        self.nav.add(mark, title)
        self.pages.addWidget(widget)

    def footer(self, row: QLayout) -> None:
        """A row under the pages: the version, OK and Cancel."""
        self.right.addLayout(row)

    def open_page(self, name: str) -> None:
        for index, title in enumerate(self.nav.titles()):
            if title.lower() == name.lower():
                self.nav.choose(index)

    def _restyled(self) -> None:
        """The look changed: the lines about settings start under their words again."""
        try:
            for line in self.window.findChildren(QLabel, 'about'):
                indent(line)
            self.nav.update()
        except RuntimeError:                                # the window has gone
            pass


def style() -> str:
    """A settings window's style sheet: set with theme.restyle, so it follows the look."""
    c = theme.colors()
    return theme.card_style() + theme.panel_css('QFrame#section', 12) + f"""
        QWidget#settings {{ background: {theme.css(c.card)}; }}
        QStackedWidget, QStackedWidget > QWidget {{ background: transparent; }}
        QScrollArea#scrolled, QScrollArea#scrolled > QWidget, QWidget#scrolled {{ background: transparent; }}
        QListWidget#list {{ background: {theme.css(c.chip)}; border: 1px solid {theme.css(c.border)};
                            border-radius: 8px; padding: 4px; }}
        QSlider {{ min-height: 22px; background: transparent; }}
        QSlider::groove:horizontal {{ height: 4px; border-radius: 2px; background: {theme.css(c.border)}; }}
        QSlider::sub-page:horizontal {{ height: 4px; border-radius: 2px; background: {theme.ACCENT}; }}
        QSlider::handle:horizontal {{ width: 16px; height: 16px; margin: -6px 0; border-radius: 8px;
                                      background: {theme.ACCENT}; border: 1px solid {theme.ink()}; }}
        QSlider::handle:horizontal:hover {{ background: {theme.ACCENT_HOVER}; }}
        QSlider::sub-page:horizontal:disabled, QSlider::handle:horizontal:disabled {{
            background: {theme.css(c.border)}; border-color: {theme.css(c.border)}; }}
    """

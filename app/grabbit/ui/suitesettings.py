"""The suite's settings windows, laid out alike: the pages down the left under the app's logo, a
pill sliding to the one chosen; each page a title, then framed sections of settings, rising in as
it comes up. An app's own sidebar (sidebar()) takes the same look, and the apps' switches,
choices (Segmented), empty places (Empty) and toasts (stack_toasts) are drawn here too, after
21st.dev's components (HeroUI's switch, a segmented control, shadcn's Empty, Sonner).

Written once, here in Hanami, and copied into each Qt app's ui\\ folder (tests\\check_copies.py
keeps them the same); it uses only suitetheme.py, which is shared the same way.

    frame = SettingsFrame(window, icons.render_app_icon(28))
    layout = frame.add('gear', 'General')          # an icon of the suite's (suiteicons), the page's name
    box_frame, box = section('Starting up')
    layout.addWidget(box_frame)
"""

from __future__ import annotations

import math

from PySide6.QtCore import (QEasingCurve, QEvent, QModelIndex, QObject, QPersistentModelIndex, QPoint,
                            QPropertyAnimation, QRectF, QSize, Qt, QVariantAnimation, Signal)
from PySide6.QtGui import QColor, QFont, QIcon, QKeySequence, QLinearGradient, QPainter, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QLayout,
                               QScrollArea, QSizePolicy, QStackedWidget, QStyle, QStyledItemDelegate, QVBoxLayout,
                               QWidget)

from . import suiteicons
from . import suitetheme as theme


def glyph_font(point_size: float) -> QFont:
    """Windows 11's icon font (Windows 10's, if that is all there is)."""
    f = QFont('Segoe Fluent Icons')
    if not f.exactMatch():
        f = QFont('Segoe MDL2 Assets')
    f.setPointSizeF(point_size)
    return f


def draw_mark(painter: QPainter, rect: QRectF, mark: str, color: QColor, size: int) -> None:
    """A page's, a tick's or an empty place's mark: one of the suite's icons (suiteicons) by name, or
    else a Segoe Fluent glyph, centred in rect at about size px."""
    if mark in suiteicons.SHAPES:
        suiteicons.draw(painter, rect, mark, color, size)
        return
    painter.setPen(color)
    painter.setFont(glyph_font(size * 0.62))
    painter.drawText(rect, Qt.AlignCenter, mark)


BODY, SMALL = theme.TYPE['body'], theme.TYPE['small']      # a setting's name; a line about it


def label(text: str, size: float = BODY, weight=QFont.Normal, role: str = '', wrap: bool = False) -> QLabel:
    """Text on a settings page: role 'dim' for a line about something, 'error' for a problem. Its box
    is its ink (theme.Label), so the gaps around it are what they say."""
    line = theme.Label(text)
    line.setFont(theme.font(size, weight))
    if role:
        line.setProperty('role', role)
    line.setWordWrap(wrap)
    return line


def note(text: str) -> QLabel:
    """A line about the settings near it: a step smaller, dimmer, wrapped."""
    return label(text, SMALL, role='dim', wrap=True)


# Spacing (2026-11-02, the user: "split all empty space evenly"): the gaps the eye sees, ink to
# ink, are even (Keytap\.dev\spacing\BRIEF.md): 16 between the items of a section, 16 padding,
# 4 inside a pair (a setting's name and the line under it). Text is theme.Label, whose box is its
# ink, so these numbers are the gaps themselves.
PAD = 16            # a section's padding, to the ink
ITEMS = 16          # between the items of a section: settings rows, a form's rows, a note, buttons
TITLE_GAP = ITEMS   # under a section's title, as between its items: even
PAIR = 4            # a setting's name and the line under it: one item


def group(text: str) -> tuple[QWidget, QVBoxLayout]:
    """A group of choices inside a section, under its name (like Discord's "If Streamer Mode is
    enabled..."): the name a setting's size and weight, its choices close under it. Add them to
    the layout given."""
    holder = QWidget()
    layout = QVBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(ITEMS)                     # its name to its choices, as between items
    layout.addWidget(label(text, BODY, QFont.Medium, wrap=True))
    return holder, layout


def section(title: str) -> tuple[QFrame, QVBoxLayout]:
    """A panel on a page, under its own heading (Segoe UI Semibold, as Windows' and most apps'
    settings have it): the layout given is for its items, ITEMS apart."""
    frame = QFrame()
    frame.setObjectName('section')
    outer = QVBoxLayout(frame)
    outer.setContentsMargins(PAD, PAD, PAD, PAD)
    outer.setSpacing(0)
    if title:                           # none for a page's first, when it would say the page's name again
        line = theme.Label(title)
        line.setObjectName('section_title')
        line.setFont(theme.text('heading'))
        outer.addWidget(line)
        outer.addSpacing(TITLE_GAP)
    layout = QVBoxLayout()
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(ITEMS)
    outer.addLayout(layout)
    return frame, layout


def form() -> QFormLayout:
    """Settings with a name before each, in a section: the names in a column on the left."""
    rows = QFormLayout()
    rows.setContentsMargins(0, 0, 0, 0)
    rows.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    rows.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
    rows.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
    rows.setHorizontalSpacing(16)
    rows.setVerticalSpacing(ITEMS)
    return rows


def framed(widget: QWidget) -> QWidget:
    """A list on a settings page, in a box of its own."""
    widget.setObjectName('list')
    return widget


class _Switch(QCheckBox):
    """A setting's switch, drawn without words: its name is a label beside it, which text() and
    setText() reach, and which dims with it."""

    def __init__(self, name: QLabel, row: QWidget):
        super().__init__()
        self._name, self._row = name, row
        self.setAccessibleName(name.text())

    def text(self) -> str:
        return self._name.text()

    def setText(self, text: str) -> None:
        self._name.setText(text)
        self.setAccessibleName(text)

    def changeEvent(self, event):
        if event.type() == QEvent.EnabledChange:
            for line in self._row.findChildren(QLabel):
                line.setEnabled(self.isEnabled())
        super().changeEvent(event)


class _SwitchRow(QWidget):
    """A setting's row: a click on its words flips its switch, as on the switch itself.

    It is as tall as its words, so rows are as evenly apart as any other text (the user: "split all
    empty space evenly"); the switch, taller than one line, is held by the row's parent and hung
    centred on the words at the row's right, reaching into the gaps around it."""

    box: QCheckBox

    def event(self, event):
        if event.type() in (QEvent.ParentChange, QEvent.Move, QEvent.Resize, QEvent.Show, QEvent.Hide):
            self._place()
        return super().event(event)

    def _place(self) -> None:
        box, parent = getattr(self, 'box', None), self.parentWidget()
        if box is None or parent is None:
            return
        if box.parentWidget() is not parent:
            box.setParent(parent)
            self.destroyed.connect(box.deleteLater)
        width, height = theme.SuiteStyle.SWITCH[0] + 2, max(theme.SuiteStyle.SWITCH[1], box.sizeHint().height())
        box.setGeometry(self.x() + self.width() - width, self.y() + (self.height() - height) // 2, width, height)
        box.raise_()
        box.setVisible(not self.isHidden())

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.box.isEnabled() and self.rect().contains(event.position().toPoint()):
            self.box.click()
        super().mouseReleaseEvent(event)


def switch(text: str, about: str, on: bool) -> tuple[QWidget, QCheckBox]:
    """A setting, laid out as Windows 11, Discord and most apps lay one out: its name (medium
    weight) and a line about it on the left, the switch (21st.dev's HeroUI switch) on the right,
    in the middle of the row's height."""
    holder = _SwitchRow()
    row = QHBoxLayout(holder)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(24)
    words = QVBoxLayout()
    words.setContentsMargins(0, 0, 0, 0)
    words.setSpacing(PAIR)
    name = label(text, BODY, QFont.Medium, wrap=True)
    name.setObjectName('name')
    words.addWidget(name)
    if about:
        line = label(about, SMALL, role='dim', wrap=True)
        line.setObjectName('about')
        words.addWidget(line)
    row.addLayout(words, 1)
    row.setAlignment(words, Qt.AlignVCenter)
    # A one-line row is at least the switch's height less 6, so in a run of them the switches
    # are at least 10 apart (16 + 14 - 20); its words sit in its middle
    holder.setMinimumHeight(theme.SuiteStyle.SWITCH[1] - 6)
    box = _Switch(name, holder)
    box.setProperty(theme.SWITCH, True)
    box.setChecked(on)
    box.setCursor(Qt.PointingHandCursor)
    box.hide()                                  # until the row has a parent to hang it in
    # its room at the right, with 24 between it and the words (a layout adds no spacing beside a
    # spacer); the switch itself is placed by the row
    row.addSpacing(24 + theme.SuiteStyle.SWITCH[0] + 2)
    holder.box = box
    holder.setCursor(Qt.PointingHandCursor)
    return holder, box


def heading(text: str, size: float) -> QLabel:
    """A page's, window's or empty place's title, in Geist Serif."""
    line = theme.Label(text)
    line.setFont(theme.serif(size))
    return line


def titled(title: str, text: str) -> QWidget:
    """A dialog's title in Geist Serif, and a line under it."""
    block = QWidget()
    column = QVBoxLayout(block)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(ITEMS)                    # a title is no pair with its line: 4 crammed it
    column.addWidget(heading(title, theme.TYPE['title']))
    column.addWidget(label(text, wrap=True))
    return block


def indent(line: QLabel) -> None:
    """A line about a setting starts under its words - since its switch went to the right of the
    row (2026-11-02), at the section's left edge, with no indent."""
    line.setContentsMargins(0, 0, 0, 0)


def page(title: str, about: str = '') -> tuple[QWidget, QVBoxLayout]:
    """A page: its title, and a line about it if it needs one."""
    widget = QWidget()
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(4, 4, 4, 4)
    layout.setSpacing(ITEMS)                    # its title, its sections: even
    layout.addWidget(heading(title, 21))
    if about:
        layout.addWidget(label(about, SMALL, role='dim', wrap=True))
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
        slide.setDuration(theme.GENTLE if liquid else theme.STEADY)
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
            draw_mark(p, QRectF(row.left() + 14, row.top(), 24, row.height()), mark,
                      QColor(theme.ink()) if selected else c.dim, 18)
            p.setPen(c.text)
            p.setFont(theme.font(BODY))       # one weight: the pill shows the page chosen, and words don't shift
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
        # On glass a row is kept as a picture once drawn (text on a see-through window is drawn
        # without ClearType either way): a window being resized drew every row again each frame.
        if not theme.glass_on():
            self._draw(p, QRectF(option.rect), option, index)
            return
        view = option.widget
        icon = index.data(Qt.DecorationRole)
        ratio = view.devicePixelRatioF() if view is not None else 1.0
        key = (str(index.data() or ''), index.flags().value, view is not None and view.currentIndex() == index,
               bool(option.state & QStyle.State_MouseOver), index.data(Qt.CheckStateRole) == Qt.Checked,
               icon.cacheKey() if isinstance(icon, QIcon) else None, str(index.data(COUNT)),
               option.rect.width(), option.rect.height(), ratio, id(theme.colors()), theme.ink())
        row = _rows.get(key)
        if row is None:
            if len(_rows) > 300:
                _rows.clear()
            row = QPixmap(max(1, round(option.rect.width() * ratio)), max(1, round(option.rect.height() * ratio)))
            row.setDevicePixelRatio(ratio)
            row.fill(Qt.transparent)
            painter = QPainter(row)
            self._draw(painter, QRectF(0, 0, option.rect.width(), option.rect.height()), option, index)
            painter.end()
            _rows[key] = row
        p.drawPixmap(option.rect.topLeft(), row)

    @staticmethod
    def _draw(p, r: QRectF, option, index) -> None:
        c = theme.colors()
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
            draw_mark(p, QRectF(right - 16, r.top(), 16, r.height()), 'check', QColor(theme.ink()), 16)
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


_rows: dict = {}        # sidebar rows as drawn, by all that goes into one (_SideRows.paint)


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
        slide.setDuration(theme.GENTLE if liquid else theme.STEADY)
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


class _Chip(QWidget):
    """An icon in a soft rounded square."""

    def __init__(self, mark: str, size: int = 40):
        super().__init__()
        self.mark = mark
        self.setFixedSize(size, size)

    def paintEvent(self, event):
        c = theme.colors()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(c.border, 1))
        p.setBrush(c.chip)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)
        p.setPen(c.text)
        draw_mark(p, QRectF(self.rect()), self.mark, p.pen().color(), round(self.width() * 0.5))
        p.end()


class Shimmer(QWidget):
    """Where something is on its way: the rows it will fill, as soft rounded bars with a light
    sweeping across them (21st.dev's skeletons), instead of a blank space or a spinner."""

    SWEEP_MS = 1400

    def __init__(self, rows: int = 6, row_height: int = 44, thumb: bool = True, parent=None):
        super().__init__(parent)
        self.rows, self.row_height, self.thumb = rows, row_height, thumb
        self.setMinimumHeight(row_height)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._phase = 0.0
        self._sweep = QVariantAnimation(self)
        self._sweep.setDuration(self.SWEEP_MS)
        self._sweep.setStartValue(0.0)
        self._sweep.setEndValue(1.0)
        self._sweep.setLoopCount(-1)
        self._sweep.valueChanged.connect(self._step)

    def sizeHint(self) -> QSize:
        return QSize(240, self.rows * self.row_height)

    def _step(self, value) -> None:
        self._phase = float(value)
        self.update()

    def showEvent(self, event):
        if theme.motion_on():
            self._sweep.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self._sweep.stop()
        super().hideEvent(event)

    def paintEvent(self, event):
        c = theme.colors()
        base = QColor(c.text)
        base.setAlpha(18 if theme.is_dark() else 14)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        width = self.width()
        light = QLinearGradient(-width + 2 * width * self._phase, 0, 2 * width * self._phase, 0)
        shine = QColor(255, 255, 255, 34 if theme.is_dark() else 120)
        light.setColorAt(0.0, QColor(255, 255, 255, 0))
        light.setColorAt(0.5, shine)
        light.setColorAt(1.0, QColor(255, 255, 255, 0))
        for row in range(min(self.rows, self.height() // self.row_height + 1)):
            top = row * self.row_height + 6
            height = self.row_height - 12
            left = 8
            bars = []
            if self.thumb and height >= 20:
                bars.append(QRectF(left, top, height, height))
                left += height + 12
            room = width - left - 8
            line = max(6, min(10, height // 3))
            widths = (0.7, 0.45) if height >= 2 * line + 6 else (0.6,)
            for k, share in enumerate(widths):
                y = top + (height - line) / 2 if len(widths) == 1 else top + 2 + k * (line + 6)
                bars.append(QRectF(left, y, room * (share - 0.08 * (row % 3)), line))
            for bar in bars:
                radius = min(6.0, bar.height() / 2)
                p.setBrush(base)
                p.drawRoundedRect(bar, radius, radius)
                p.setBrush(light)
                p.drawRoundedRect(bar, radius, radius)
        p.end()


class Empty(QWidget):
    """An empty place (21st.dev's shadcn Empty): an icon in a soft square, a title under it, and
    a line about what would be here - in the middle of the room it has."""

    def __init__(self, mark: str, title: str, text: str = '', parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)     # drops and clicks go on to what is under it
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(8)
        layout.addStretch(1)
        self.chip = _Chip(mark)
        layout.addWidget(self.chip, 0, Qt.AlignHCenter)
        layout.addSpacing(4)
        self.title = heading(title, 13)
        self.title.setAlignment(Qt.AlignHCenter)
        layout.addWidget(self.title)
        self.text = label(text, 9.5, role='dim', wrap=True)
        self.text.setAlignment(Qt.AlignHCenter)
        self.text.setVisible(bool(text))
        layout.addWidget(self.text)                 # the whole width, so it wraps rather than being cut
        layout.addStretch(1)
        theme.restyle(self, lambda: f'QLabel {{ color: {theme.colors().text.name()}; background: transparent; }}'
                                    f'QLabel[role="dim"] {{ color: {theme.colors().dim.name()}; }}')

    def set(self, mark: str, title: str, text: str = '') -> None:
        self.chip.mark = mark
        self.chip.update()
        self.title.setText(title)
        self.text.setText(text)
        self.text.setVisible(bool(text))


class Segmented(QWidget):
    """A choice of a few (21st.dev's segmented control): the buttons side by side on a soft
    track, and a pill sliding under the one chosen. The buttons stay the app's own - checkable
    QPushButtons, in a QButtonGroup if it likes - so whatever reads or sets them still does."""

    PAD = 3

    def __init__(self, buttons: list):
        super().__init__()
        self.buttons = list(buttons)
        self._pill: QRectF | None = None
        self._slide = None
        row = QHBoxLayout(self)
        row.setContentsMargins(self.PAD, self.PAD, self.PAD, self.PAD)
        row.setSpacing(0)
        for button in self.buttons:
            button.setObjectName('segment')
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.toggled.connect(lambda on: on and self._slide_to(animate=True))
            row.addWidget(button)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        theme.restyle(self, self._style)

    def _style(self) -> str:
        c = theme.colors()
        return f"""
            QPushButton#segment {{ background: transparent; border: none; border-radius: 8px; padding: 6px 14px;
                                   color: {c.dim.name()}; font-weight: 400; }}
            QPushButton#segment:checked {{ color: {c.text.name()}; font-weight: 500; }}
            QPushButton#segment:disabled {{ color: {theme.css(c.border)}; }}
        """

    def _target(self) -> QRectF | None:
        chosen = next((b for b in self.buttons if b.isChecked()), None)
        return QRectF(chosen.geometry()) if chosen is not None else None

    def _slide_to(self, animate: bool) -> None:
        end = self._target()
        theme.stop_animation(self._slide)
        self._slide = None
        if end is None or self._pill is None or not animate or not theme.motion_on() or not self.isVisible():
            self._pill = end
            self.update()
            return
        start = QRectF(self._pill)
        slide = self._slide = QVariantAnimation(self)
        slide.setDuration(theme.GENTLE)
        slide.setEasingCurve(QEasingCurve(QEasingCurve.OutBack) if theme.look() == 'liquid' and theme.glass_on()
                             else QEasingCurve(QEasingCurve.OutQuint))
        slide.setStartValue(0.0)
        slide.setEndValue(1.0)

        def step(t):
            t = float(t)
            self._pill = QRectF(start.left() + (end.left() - start.left()) * t, end.top(),
                                start.width() + (end.width() - start.width()) * t, end.height())
            self.update()
        slide.valueChanged.connect(step)
        theme.animate(slide)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._slide_to(animate=False)

    def showEvent(self, event):
        super().showEvent(event)
        self._slide_to(animate=False)

    def paintEvent(self, event):
        dark = theme.is_dark()
        liquid = theme.look() == 'liquid' and theme.glass_on()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        track = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        bend = track.height() / 2 if liquid else 11.0
        p.setPen(QPen(theme.colors().border, 1) if liquid else Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 16) if dark else QColor(0, 0, 0, 14))
        p.drawRoundedRect(track, bend, bend)
        if self._pill is None:
            self._pill = self._target()
        if self._pill is not None:
            pill = QRectF(self._pill)
            round_ = pill.height() / 2 if liquid else 8.0
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 0 if dark else 14))
            p.drawRoundedRect(pill.translated(0, 1), round_, round_)
            p.setBrush(QColor(255, 255, 255, 46 if dark else 245))
            p.drawRoundedRect(pill, round_, round_)
            if liquid:
                theme._edge_light(p, pill.adjusted(0.5, 0.5, -0.5, -0.5), round_, 120, 150 if dark else 255, 1.1)
        p.end()


# --------------------------------------------------------------------------- toasts

TOAST_GAP = 14          # 21st.dev's Sonner: between toasts spread out, and how far each one behind peeks out
TOAST_BEHIND = 2        # folded, how many show behind the newest
TOAST_SHRINK = 0.05     # each one behind is this much narrower than the one in front of it


def keycaps(keys: str) -> QWidget:
    """A shortcut as keycaps: Ctrl+Shift+A as three small keys side by side."""
    row = QWidget()
    line = QHBoxLayout(row)
    line.setContentsMargins(0, 0, 0, 0)
    line.setSpacing(4)
    line.addStretch(1)                  # flush right, so the keys of a sheet's sections line up
    for key in keys.replace('++', '+Plus').split('+'):
        cap = QLabel(key.replace('Plus', '+'))
        cap.setObjectName('keycap')
        cap.setFont(theme.text('caption', QFont.DemiBold))
        cap.setAlignment(Qt.AlignCenter)
        cap.setMinimumWidth(22)
        line.addWidget(cap)
    theme.restyle(row, lambda: f"""
        QLabel#keycap {{ color: {theme.css(theme.colors().text)}; background: {theme.css(theme.colors().chip)};
                         border: 1px solid {theme.css(theme.colors().border)}; border-bottom-width: 2px;
                         border-radius: 5px; padding: 1px 6px; }}""")
    return row


class ShortcutSheet(QDialog):
    """Every keyboard shortcut an app has, in groups, with its keys as keycaps - opened with Ctrl+/
    or F1 (shortcut_sheet)."""

    def __init__(self, parent: QWidget, title: str, groups: list[tuple[str, list[tuple[str, str]]]]):
        super().__init__(parent)
        self.setWindowTitle(title)
        theme.frost(self)
        theme.restyle(self, style)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(16)
        outer.addWidget(heading('Keyboard shortcuts', theme.TYPE['title']))
        for name, rows in groups:
            box, layout = section(name)
            grid = QGridLayout()
            grid.setHorizontalSpacing(24)
            grid.setVerticalSpacing(8)
            for n, (keys, what) in enumerate(rows):
                grid.addWidget(label(what), n, 0)
                grid.addWidget(keycaps(keys), n, 1)
            grid.setColumnStretch(0, 1)
            layout.addLayout(grid)
            outer.addWidget(box)
        outer.addStretch(1)
        self.resize(460, self.sizeHint().height())


def shortcut_sheet(window: QWidget, title: str, groups) -> None:
    """Ctrl+/ and F1 in window open its shortcut sheet. groups: [(name, [(keys, what), ...]), ...],
    or a function giving them, for shortcuts the person can change."""
    def show() -> None:
        sheet = ShortcutSheet(window, title, groups() if callable(groups) else groups)
        sheet.setAttribute(Qt.WA_DeleteOnClose)
        sheet.show()
    for keys in ('Ctrl+/', 'F1'):
        QShortcut(QKeySequence(keys), window, show)


def stack_toasts(toasts: list, corner: QPoint, shadow: int, spread: bool) -> None:
    """Toasts as Sonner stacks them: the newest at the corner (corner: where its card's bottom
    right goes), the next ones folded behind it - each peeking out above, a little narrower -
    or, spread (the pointer on them), one above another. They glide to their places; each one's
    depth (0 in front, or spread) says how its card is drawn: narrower, and without its words."""
    for depth, toast in enumerate(reversed(toasts)):
        card = toast.height() - 2 * shadow
        if spread:
            top = corner.y() - card - sum(t.height() - 2 * shadow + TOAST_GAP for t in list(reversed(toasts))[:depth])
        else:
            top = corner.y() - card - TOAST_GAP * min(depth, TOAST_BEHIND)
        toast.depth = 0 if spread else depth
        toast.setWindowOpacity(1.0 if spread or depth <= TOAST_BEHIND else 0.0)
        _glide(toast, QPoint(corner.x() - toast.width() + shadow, top - shadow))
        toast.update()
    for toast in toasts:                    # the newest on top
        toast.raise_()


def _glide(window: QWidget, to: QPoint) -> None:
    moving = getattr(window, '_suite_glide', None)
    theme.stop_animation(moving)
    if not window.isVisible() or not theme.motion_on() or window.pos() == to:
        window.move(to)
        return
    glide = window._suite_glide = QPropertyAnimation(window, b'pos', window)
    glide.setDuration(theme.GENTLE)
    glide.setEasingCurve(QEasingCurve.OutCubic)
    glide.setStartValue(window.pos())
    glide.setEndValue(to)
    theme.animate(glide)


def toast_card(card: QRectF, depth: int) -> QRectF:
    """A toast's card as it is drawn at its depth in the stack: narrower behind, its middle kept."""
    inset = card.width() * TOAST_SHRINK * depth / 2
    return card.adjusted(inset, 0, -inset, 0)


class SettingsFrame:
    """A settings window's layout: the app's logo and "Settings" above the pages down the left,
    the page chosen on the right - and, for a window whose settings are kept with OK, a row
    under the pages for its buttons (footer)."""

    def __init__(self, window: QWidget, logo: QPixmap, title: str = 'Settings'):
        self.window = window
        window.setObjectName('settings')
        window.setFont(theme.font(BODY))       # its buttons and boxes the size of its words (Windows 11's 14 px)
        window.setMinimumSize(800, 560)
        outer = QHBoxLayout(window)
        outer.setContentsMargins(12, 16, 20, 16)
        outer.setSpacing(12)
        # The logo and pages on a pane of their own, as macOS Tahoe floats its sidebar: on clear
        # glass the pages' names read over whatever is behind the window.
        self.sidebar = QFrame()
        self.sidebar.setObjectName('sidebar')
        side = QVBoxLayout(self.sidebar)
        side.setContentsMargins(0, 12, 0, 8)
        side.setSpacing(12)
        top = QHBoxLayout()
        top.setContentsMargins(16, 0, 0, 0)
        top.setSpacing(12)
        mark = QLabel()
        mark.setPixmap(logo)
        top.addWidget(mark)
        top.addWidget(heading(title, 17), 1)
        side.addLayout(top)
        self.nav = Nav()
        side.addWidget(self.nav)
        side.addStretch(1)
        self.side = side
        outer.addWidget(self.sidebar)
        right = QVBoxLayout()
        right.setSpacing(12)
        self.pages = theme.Pages()
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
    body = theme.font(BODY)
    # Windows gives buttons, boxes and lists a 9 pt font of their own, which a window's font
    # doesn't reach: here they take the words' size, as in Windows 11's own settings.
    controls = ', '.join(f'QWidget#settings {kind}' for kind in
                         ('QAbstractButton', 'QComboBox', 'QLineEdit', 'QAbstractSpinBox', 'QAbstractItemView'))
    return theme.card_style() + theme.panel_css('QFrame#section', 12) + theme.panel_css('QFrame#sidebar', 12) + f"""
        {controls} {{ font-family: "{body.family()}"; font-size: {BODY}pt; }}
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

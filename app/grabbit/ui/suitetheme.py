"""suitetheme: the suite's look in a Qt app - its pink, check boxes, colours, fonts and cards,
its glass and its animations.

SHARED FILE - the same file sits in the ui folder of the suite's Qt apps
(Keytap, Dropshift, Grabbit). This copy, in Hanami, is the original: change
it here and copy it over; Hanami's tests/check_copies.py says whether they match.

Each app's own ui/theme.py takes everything from here (`from .suitetheme
import *`) and adds what only that app needs - a few more colours, by
subclassing Colors - so the app's modules go on using `theme.<name>`.

The highlight is the suite's blossom pink, ACCENT - the colour of every suite
app's icon tile - rather than the Windows accent, which on this kind of
machine follows the wallpaper and could be any colour at all. It is too pale
for white text or for lines on white: text on it is ON_ACCENT, and the pink as
text or a line on the window is ink().

Glass: every window of the suite is a pane of glass, as Windows 11's own are.
An app calls frost(window) once, before the window is first shown. A framed
window then takes Windows' Acrylic under a veil of the suite's own; a
frameless one (a card, a menu, a toast, a shelf) draws its glass itself, from
the screen behind it (see "glass from the screen"), with Windows' rounded
corners and shadow - so it paints no shadow of its own (glass_on() says
which). colors() turns see-through to match, and paint_glass() draws the pane.
Glass needs Windows 11 22H2 and Windows' transparency effects on (Settings >
Personalisation > Colours); without them everything stays solid, as before.

Two looks, chosen in Hanami for the whole suite (look(), set_look()):

- Frosted, the default: clear glass that softens what is behind it, and
  gathers a little of what lies beyond a pop-up's edges in at its rim -
  lit as liquid glass's rim is.
- Liquid glass, after Apple's: what is behind stays sharp in the middle and
  bends at the edges and round the corners, as through a polished lens, under
  a tint that adapts to it. The bending moves with the glass as it rises into
  place, unfolds, is dragged or changes size. Buttons and boxes are capsules,
  check boxes are switches, and buttons and panels catch a light under the
  pointer that brightens as they are pressed.

frost() also brings the suite's animations, unless they are off (motion_on()):
pop-ups fade in (rising and unfolding into place in the liquid look), and out
again when closed with fade_out(); a window's parts rise into place one after
another as it opens (stagger_in), and a stacked page's as it comes up (it
slides in, too, in the liquid look); progress bars glide to each new value;
and check boxes tick (switches spring across) smoothly. A style sheet
made from this module's colours should be set with restyle(), so it follows
the look and Windows' light or dark as they change.
"""

from __future__ import annotations

import ctypes
import itertools
import math
import os
import queue
import sys
import threading
import time
import weakref
from ctypes import wintypes
from dataclasses import dataclass, replace

from PySide6.QtCore import (QEasingCurve, QEvent, QObject, QPoint, QPointF, QPropertyAnimation, QRect, QRectF, Qt,
                            QSequentialAnimationGroup, QTimer, QVariantAnimation, Signal)
from PySide6.QtGui import (QBrush, QColor, QConicalGradient, QFont, QGuiApplication, QImage, QLinearGradient,
                           QPainter, QPainterPath, QPalette, QPen, QPixmap, QRadialGradient, QRegion)
from PySide6.QtWidgets import (QAbstractSpinBox, QApplication, QCheckBox, QComboBox, QFrame, QGraphicsEffect,
                               QGraphicsOpacityEffect, QLineEdit, QMainWindow, QMenuBar, QProgressBar, QProxyStyle,
                               QPushButton, QStackedWidget, QStatusBar, QStyle, QToolBar, QToolButton, QWidget)

__all__ = ['ACCENT', 'ACCENT_HOVER', 'ON_ACCENT', 'ROSE', 'SuiteStyle', 'install', 'Colors', 'is_dark', 'veil',
           'colors', 'ink', 'font', 'paint_shadow', 'card_style', 'liquid_controls_css', 'panel_css', 'GLASS_RADIUS',
           'LIQUID_RADIUS', 'glass_on', 'frost', 'paint_glass', 'css', 'fade_in', 'LOOKS', 'look', 'set_look',
           'motion_on', 'motion_chosen', 'set_motion', 'MODES', 'mode', 'set_mode', 'app_mode', 'restyle',
           'on_restyle', 'light_up', 'BACKDROP_OPACITY', 'fade_out', 'stagger_in', 'GAME_DOWNLOADS',
           'game_quiet_chosen', 'set_game_quiet', 'game_downloads', 'set_game_downloads', 'game_in_front',
           'quiet_now', 'on_game']

ACCENT = '#ffd1dc'         # the suite's blossom pink: buttons, ticks, highlights
ACCENT_HOVER = '#ffe0e8'
ON_ACCENT = '#a61e4d'      # text and ticks drawn on ACCENT, which is too pale for white
ROSE = '#c2255c'           # the mark on the icons: the pink as text or a line on white
GLASS_RADIUS = 8           # the corners Windows 11 rounds a frameless glass window to
LIQUID_RADIUS = 14         # a liquid glass pop-up's corners, rounder (Windows' own rounding lies outside them)


class SuiteStyle(QProxyStyle):
    """Windows' own style, with check boxes drawn in the suite's pink - as switches in the liquid look.

    Windows 11's style loses a ticked box's fill once a style sheet is in
    play, leaving a bare tick that looks cut off. Drawing the box here keeps
    it whole, in any mode, whatever the accent colour of the day. A box that
    frost() wired up ticks smoothly (a switch springs across): its fill and
    tick follow _TICK, which its animation moves from 0 to 1.
    """

    SIZE = 18
    SWITCH = (34, 20)

    def pixelMetric(self, metric, option=None, widget=None):
        if metric == QStyle.PM_IndicatorWidth:
            return self.SWITCH[0] if _liquid_look() else self.SIZE
        if metric == QStyle.PM_IndicatorHeight:
            return self.SWITCH[1] if _liquid_look() else self.SIZE
        return super().pixelMetric(metric, option, widget)

    def drawPrimitive(self, element, option, painter, widget=None):
        if element != QStyle.PE_IndicatorCheckBox:
            super().drawPrimitive(element, option, painter, widget)
            return
        state = option.state
        partly = bool(state & QStyle.State_NoChange)
        step = widget.property(_TICK) if isinstance(widget, QCheckBox) else None
        moving = step is not None
        if not moving:
            step = 1.0 if state & QStyle.State_On else 0.5 if partly and _liquid_look() else 1.0 if partly else 0.0
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        if not state & QStyle.State_Enabled:
            painter.setOpacity(0.45)
        if _liquid_look():
            self._switch(option, painter, float(step), moving)
        else:
            self._box(option, painter, float(step), partly)
        painter.restore()

    def _box(self, option, painter, step, partly):
        c = colors()
        state = option.state
        base = painter.opacity()
        side = min(option.rect.width(), option.rect.height(), self.SIZE) - 1
        box = QRectF(0, 0, side, side)
        box.moveCenter(QRectF(option.rect).center())
        hover = state & QStyle.State_MouseOver and state & QStyle.State_Enabled
        painter.setPen(QPen(c.edge if hover or step > 0.5 else c.dim, 1))
        painter.setBrush(c.chip)
        painter.drawRoundedRect(box, side * 0.22, side * 0.22)
        if step <= 0:
            return
        # The pink fills in from the middle, then the tick draws itself.
        grow = 0.55 + 0.45 * min(1.0, step * 1.6)
        inner = QRectF(0, 0, side * grow, side * grow)
        inner.moveCenter(box.center())
        painter.setPen(QPen(c.edge, 1) if grow >= 1 else Qt.NoPen)
        painter.setBrush(QColor(ACCENT))
        painter.setOpacity(base * min(1.0, step * 2))
        painter.drawRoundedRect(inner, inner.width() * 0.22, inner.width() * 0.22)
        painter.setOpacity(base)
        pen = QPen(QColor(ON_ACCENT), max(1.6, side * 0.12), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        x, y = box.x(), box.y()
        if partly:
            painter.drawLine(QPointF(x + side * 0.28, y + side / 2), QPointF(x + side * 0.72, y + side / 2))
        elif step > 0.35:
            tick = QPainterPath(QPointF(x + side * 0.26, y + side * 0.52))
            tick.lineTo(x + side * 0.43, y + side * 0.69)
            tick.lineTo(x + side * 0.75, y + side * 0.33)
            drawn = (step - 0.35) / 0.65
            painter.drawPath(tick if drawn >= 1 else _part_of(tick, drawn))

    def _switch(self, option, painter, step, moving):
        """A switch, as Apple's: the pink fills the track as the knob crosses it, and the knob
        stretches as it travels - turning to glass while it does - and springs into place."""
        c = colors()
        state = option.state
        base = painter.opacity()
        width, height = self.SWITCH
        rect = QRectF(option.rect)
        track = QRectF(rect.x() + 0.5, rect.center().y() - height / 2 + 0.5, width - 1, height - 1)
        bend = track.height() / 2
        on = min(1.0, max(0.0, step))
        hover = state & QStyle.State_MouseOver and state & QStyle.State_Enabled
        painter.setPen(QPen(c.edge if hover else c.dim, 1))
        painter.setBrush(c.chip)
        painter.drawRoundedRect(track, bend, bend)
        if on > 0:
            painter.setOpacity(base * on)
            painter.setPen(QPen(c.edge, 1))
            painter.setBrush(QColor(ACCENT))
            painter.drawRoundedRect(track, bend, bend)
            painter.setOpacity(base)
        size = track.height() - 4
        stretch = 6 * math.sin(math.pi * on) if moving else 0.0
        x = track.x() + 2 + (track.width() - size - 4) * step - stretch / 2
        x = min(max(x, track.x() + 1.5), track.right() - 1.5 - size - stretch)
        knob = QRectF(x, track.center().y() - size / 2, size + stretch, size)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 45))
        painter.drawRoundedRect(knob.translated(0, 0.8), size / 2, size / 2)
        painter.setBrush(QColor(255, 255, 255, int(255 - 120 * stretch / 6)))
        painter.drawRoundedRect(knob, size / 2, size / 2)
        painter.setPen(QPen(QColor(255, 255, 255, 235), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(knob.adjusted(0.5, 0.5, -0.5, -0.5), size / 2 - 0.5, size / 2 - 0.5)


def _part_of(path: QPainterPath, share: float) -> QPainterPath:
    """The first share of a path's length, for a tick drawing itself."""
    out = QPainterPath(path.pointAtPercent(0))
    steps = 24
    for i in range(1, steps + 1):
        t = share * i / steps
        out.lineTo(path.pointAtPercent(path.percentAtLength(path.length() * t)))
    return out


_style = None
_watch = None


def install(app) -> None:
    """Give the app the suite's style, right after making the QApplication.

    Kept here so Python never frees it under Qt. The suite's light or dark is
    taken now; Windows going light or dark, and the look, the animations or
    light or dark changing in Hanami, are followed from here on.
    """
    global _style, _watch
    _style = SuiteStyle()
    app.setStyle(_style)
    _apply_mode(starting=True)
    app.styleHints().colorSchemeChanged.connect(lambda *_: _suite_changed())
    # The look is chosen in Hanami, for the whole suite: each app looks for a change now and then.
    _watch = QTimer(app)
    _watch.setInterval(2000)
    _watch.timeout.connect(_look_changed)
    _watch.start()


@dataclass(frozen=True)
class Colors:
    card: QColor
    border: QColor
    text: QColor
    dim: QColor
    chip: QColor
    selected: QColor
    selected_text: QColor
    shadow: QColor
    error: QColor
    edge: QColor        # ACCENT as an outline: deeper on white, where the pale pink would vanish
    tile: QColor        # a card or panel sitting on the window, faintly pink


def is_dark() -> bool:
    app = QGuiApplication.instance()
    return app is not None and app.styleHints().colorScheme() == Qt.ColorScheme.Dark


def colors() -> Colors:
    """The suite's colours for the mode Windows is in - see-through where they sit on glass."""
    if is_dark():
        solid = Colors(card=QColor('#202127'), border=QColor(255, 255, 255, 30), text=QColor('#ececf1'),
                       dim=QColor('#9a9aa6'), chip=QColor('#2c2d35'),
                       selected=QColor(ACCENT), selected_text=QColor(ON_ACCENT),
                       shadow=QColor(0, 0, 0, 120), error=QColor('#ff8a80'),
                       edge=QColor(ACCENT), tile=QColor('#2b2a31'))
        if glass_on():
            return replace(solid, card=QColor(24, 22, 28, 70), chip=QColor(255, 255, 255, 22),
                           tile=QColor(255, 255, 255, 16), border=QColor(255, 255, 255, 40),
                           dim=QColor('#a9a8b4'))
        return solid
    solid = Colors(card=QColor('#ffffff'), border=QColor(0, 0, 0, 28), text=QColor('#1d1d22'),
                   dim=QColor('#6e6e7a'), chip=QColor('#f1f2f6'),
                   selected=QColor(ACCENT), selected_text=QColor(ON_ACCENT),
                   shadow=QColor(20, 24, 40, 70), error=QColor('#c62828'),
                   edge=QColor('#ee9fb5'), tile=QColor('#fff6f8'))
    if glass_on():
        return replace(solid, card=QColor(255, 255, 255, 90), chip=QColor(255, 255, 255, 150),
                       tile=QColor(255, 250, 252, 120), border=QColor(0, 0, 0, 24), dim=QColor('#5f5f6b'))
    return solid


def veil() -> QColor:
    """A framed glass window's own tint over Windows' Acrylic: enough that text stays easy to read
    whatever is behind the window. (In the liquid look, the tint of its clear glass, as it is
    mostly - see _paint_clear.)"""
    if look() == 'liquid':
        return QColor(30, 28, 38, 150) if is_dark() else QColor(250, 249, 252, 140)
    return QColor(24, 22, 30, 150) if is_dark() else QColor(250, 247, 249, 150)


def css(color: QColor) -> str:
    """A colour as a style sheet takes it, keeping its see-through part."""
    return color.name(QColor.HexArgb) if color.alpha() < 255 else color.name()


def ink() -> str:
    """The pink as text, a line or an icon on the window: pale on dark, deep rose on white."""
    return ACCENT if is_dark() else ROSE


def font(point_size: float, weight: QFont.Weight = QFont.Normal) -> QFont:
    f = QFont('Segoe UI Variable Text')
    if not f.exactMatch():
        f = QFont('Segoe UI')
    f.setPointSizeF(point_size)
    f.setWeight(weight)
    return f


def paint_shadow(painter, rect, radius: float, depth: int, color: QColor) -> None:
    """A soft shadow, as rings of fading rounded rectangles around rect."""
    painter.save()
    painter.setPen(Qt.NoPen)
    for i in range(depth, 0, -1):
        shade = QColor(color)
        shade.setAlphaF(color.alphaF() * (1 - i / depth) ** 2 / depth * 2.2)
        painter.setBrush(shade)
        grown = QRectF(rect).adjusted(-i, -i + 2, i, i + 2)
        painter.drawRoundedRect(grown, radius + i, radius + i)
    painter.restore()


def paint_glass(painter, rect, radius: float = GLASS_RADIUS, fill: QColor | None = None) -> None:
    """A frameless window's pane of glass, made from the screen behind it (see "glass from the
    screen"): frosted, or liquid. Should that fail, a veil (fill, else the card colour) over
    Windows' blur, light along its top edge and a bright rim."""
    window = painter.device()
    lens = _lens_for(window) if isinstance(window, QWidget) and _screen_glass(window) else None
    if lens is not None:
        if look() == 'liquid':
            _paint_liquid(painter, rect, window, lens)
        else:
            _paint_frosted(painter, rect, radius, window, lens)
        return
    dark = is_dark()
    r = QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5)
    path = QPainterPath()
    path.addRoundedRect(r, radius, radius)
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    painter.fillPath(path, fill if fill is not None else colors().card)
    sheen = QLinearGradient(r.topLeft(), r.bottomLeft())
    sheen.setColorAt(0.0, QColor(255, 255, 255, 30 if dark else 110))
    sheen.setColorAt(min(0.5, 36 / max(r.height(), 1)), QColor(255, 255, 255, 0))
    painter.fillPath(path, sheen)
    rim = QLinearGradient(r.topLeft(), r.bottomLeft())
    rim.setColorAt(0.0, QColor(255, 255, 255, 80 if dark else 230))
    rim.setColorAt(1.0, QColor(255, 255, 255, 18 if dark else 90))
    painter.setPen(QPen(QBrush(rim), 1))
    painter.setBrush(Qt.NoBrush)
    painter.drawPath(path)
    painter.restore()


def card_style() -> str:
    """Stylesheet for the inputs and buttons that sit on a card: glassy, lit from above - and in
    the liquid look, capsules of glass. Set it with restyle(), so it follows the look."""
    c = colors()
    text, dim, chip, border = c.text.name(), c.dim.name(), css(c.chip), css(c.border)
    edge = c.edge.name()
    dark = is_dark()
    shine = css(QColor(255, 255, 255, 26 if dark else 170))
    rim = css(QColor(255, 255, 255, 34 if dark else 200))
    controls = liquid_controls_css() or f"""
        QLineEdit {{
            color: {text}; background: {chip}; border: 1px solid {border};
            border-radius: 8px; padding: 7px 10px; selection-background-color: {ACCENT};
            selection-color: {ON_ACCENT};
        }}
        QLineEdit:focus {{ border: 1px solid {edge}; }}
        QPushButton {{
            color: {text}; border: 1px solid {border}; border-top-color: {rim};
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {shine}, stop:0.5 {chip}, stop:1 {chip});
            border-radius: 8px; padding: 6px 16px;
        }}
        QPushButton:hover {{ border: 1px solid {edge}; }}
        QPushButton:pressed {{ background: {chip}; }}
        QPushButton[role="primary"] {{
            color: {ON_ACCENT}; border: 1px solid {edge}; border-top-color: #fff5f8; font-weight: 600;
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ffeef3, stop:0.5 {ACCENT}, stop:1 #ffc4d3);
        }}
        QPushButton[role="primary"]:hover {{
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #fff6f9, stop:0.5 {ACCENT_HOVER},
                                        stop:1 #ffd3de);
        }}
        QPushButton[role="primary"]:pressed {{ background: {ACCENT}; }}
        QPushButton:checked {{ color: {ON_ACCENT}; background: {ACCENT}; border: 1px solid {edge}; font-weight: 600; }}
        QPushButton:disabled {{ color: {dim}; }}
        QPushButton[role="primary"]:disabled {{ color: {ON_ACCENT}; background: {chip}; border: 1px solid {border}; }}
        QToolButton {{ color: {dim}; background: transparent; border: 1px solid transparent;
                       border-radius: 8px; padding: 0px 8px 2px 8px; }}
        QToolButton:hover {{ color: {text}; border: 1px solid {edge}; }}
    """
    return f"""
        QLabel {{ color: {text}; background: transparent; }}
        QLabel[role="dim"] {{ color: {dim}; }}
        QLabel[role="error"] {{ color: {c.error.name()}; }}
        {controls}
        QProgressBar {{ background: {chip}; border: none; border-radius: 3px; max-height: 6px; }}
        QProgressBar::chunk {{ background: {edge}; border-radius: 3px; }}
        QToolButton::menu-indicator {{ image: none; width: 0; }}
        QCheckBox {{ color: {text}; background: transparent; spacing: 8px; }}
        QListView {{ background: transparent; border: none; outline: none; }}
        QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
        QScrollBar::handle:vertical {{ background: {border}; border-radius: 3px; min-height: 24px; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    """


def liquid_controls_css() -> str:
    """In the liquid look, style sheet rules for buttons, tool buttons and text boxes as capsules
    of glass - lit along the top, catching light at the bottom, the main button glass tinted
    pink; '' in the other. card_style() has them; an app with a style sheet of its own adds them.
    (Each one's corners are set to half its height as it is shown: see _round.)"""
    if not _liquid_look():
        return ''
    c = colors()
    text, dim, border, edge = c.text.name(), c.dim.name(), css(c.border), c.edge.name()
    if is_dark():
        top, middle, low = (255, 255, 255, 50), (255, 255, 255, 22), (255, 255, 255, 14)
        rim, side, under = (255, 255, 255, 96), (255, 255, 255, 34), (255, 255, 255, 56)
        lit = (255, 255, 255, 64)
        field, field_top = (0, 0, 0, 60), (0, 0, 0, 90)
    else:
        top, middle, low = (255, 255, 255, 240), (255, 255, 255, 160), (255, 255, 255, 125)
        rim, side, under = (255, 255, 255, 255), (0, 0, 0, 26), (0, 0, 0, 40)
        lit = (255, 255, 255, 255)
        field, field_top = (255, 255, 255, 120), (0, 0, 0, 40)
    top, middle, low, rim, side, under, lit, field, field_top = (
        css(QColor(*v)) for v in (top, middle, low, rim, side, under, lit, field, field_top))
    return f"""
        QLineEdit {{
            color: {text}; background: {field}; border: 1px solid {side}; border-top-color: {field_top};
            border-bottom-color: {rim}; border-radius: 13px; padding: 7px 12px;
            selection-background-color: {ACCENT}; selection-color: {ON_ACCENT};
        }}
        QLineEdit:focus {{ border: 1px solid {edge}; }}
        QPushButton {{
            color: {text}; border: 1px solid {side}; border-top-color: {rim}; border-bottom-color: {under};
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {top}, stop:0.55 {middle}, stop:1 {low});
            border-radius: 13px; padding: 6px 18px;
        }}
        QPushButton:hover {{ border-top-color: {lit}; }}
        QPushButton:pressed {{ background: {middle}; }}
        QPushButton[role="primary"], QPushButton:checked {{
            color: {ON_ACCENT}; border: 1px solid #fff4f7; border-bottom-color: {edge}; font-weight: 600;
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #e6fff3f6, stop:0.5 #e6ffd6e1,
                                        stop:1 #e6ffc2d2);
        }}
        QPushButton[role="primary"]:pressed {{ background: {ACCENT}; }}
        QPushButton:disabled {{ color: {dim}; }}
        QPushButton[role="primary"]:disabled {{ color: {ON_ACCENT}; background: {middle}; border: 1px solid {border}; }}
        QToolButton {{ color: {dim}; background: transparent; border: 1px solid transparent;
                       border-radius: 13px; padding: 0px 8px 2px 8px; }}
        QToolButton:hover {{ color: {text}; border: 1px solid {side}; border-top-color: {rim};
                             background: {middle}; }}
    """


def panel_css(selector: str, radius: int = 12) -> str:
    """Style sheet for a panel sitting on a window (an app's card, a box of settings): faintly
    tinted - and in the liquid look, a slab of glass lit from above, with bright rims and rounder
    corners. light_up() gives it the light under the pointer too."""
    c = colors()
    if not _liquid_look():
        return (f'{selector} {{ background: {css(c.tile)}; border: 1px solid {css(c.border)}; '
                f'border-radius: {radius}px; }}')
    if is_dark():
        top, low, rim, side, under = (255, 255, 255, 30), (255, 255, 255, 12), (255, 255, 255, 84), \
            (255, 255, 255, 30), (255, 255, 255, 46)
    else:
        top, low, rim, side, under = (255, 255, 255, 200), (255, 255, 255, 110), (255, 255, 255, 255), \
            (0, 0, 0, 20), (0, 0, 0, 30)
    top, low, rim, side, under = (css(QColor(*v)) for v in (top, low, rim, side, under))
    return (f'{selector} {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {top}, stop:1 {low}); '
            f'border: 1px solid {side}; border-top-color: {rim}; border-bottom-color: {under}; '
            f'border-radius: {radius + 6}px; }}')


# --------------------------------------------------------------------------- glass

_glass: bool | None = None
_TICK = '_suite_tick'          # a check box's step through its animation, 0 to 1
_UNFOLD = '_suite_unfold'      # a liquid pop-up's step through its opening, 0 to 1
_FROSTED = '_suite_frosted'


def glass_on() -> bool:
    """Whether the suite's windows are glass here: Windows 11 22H2 or later, with transparency
    effects on, in a real window (not offscreen, as tests are). SUITE_GLASS=0 turns it off."""
    global _glass
    if _glass is None:
        _glass = _glass_possible()
    return _glass


def _glass_possible() -> bool:
    if sys.platform != 'win32' or os.environ.get('SUITE_GLASS') == '0':
        return False
    if os.environ.get('QT_QPA_PLATFORM', 'windows').split(':')[0] not in ('', 'windows'):
        return False
    if sys.getwindowsversion().build < 22621:           # DWM's system backdrops came with 22H2
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize') as key:
            return bool(winreg.QueryValueEx(key, 'EnableTransparency')[0])
    except OSError:
        return True


def _liquid_look() -> bool:
    return glass_on() and look() == 'liquid'


# --------------------------------------------------------------------------- the suite's choices

LOOKS = {'frosted': 'Frosted', 'liquid': 'Liquid glass'}
MODES = {'system': 'Follow Windows', 'light': 'Light', 'dark': 'Dark'}
_LOOK_KEY = r'Software\blossqt\Suite'
_look: str | None = None
_motion: bool | None = None
_mode: str | None = None
_app_mode = 'system'


def look() -> str:
    """The suite's glass, as chosen in Hanami: 'frosted' (the default) or 'liquid' (Apple's)."""
    global _look
    if _look is None:
        _look = _read_look()
    return _look


def _read_look() -> str:
    if os.environ.get('SUITE_LOOK') in LOOKS:             # tests and screenshots
        return os.environ['SUITE_LOOK']
    value = _suite_value('Look')
    return value if value in LOOKS else 'frosted'


def _suite_value(name: str):
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _LOOK_KEY) as key:
            return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def _set_suite_value(name: str, kind: int, value) -> None:
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _LOOK_KEY) as key:
        winreg.SetValueEx(key, name, 0, kind, value)


def set_look(name: str) -> None:
    """Choose the suite's glass (Hanami's settings do). Every suite app running takes it within a
    couple of seconds, without starting again."""
    import winreg
    _set_suite_value('Look', winreg.REG_SZ, name if name in LOOKS else 'frosted')
    _look_changed()


def motion_on() -> bool:
    """Whether the suite animates: unless turned off in Hanami (motion_chosen()), or in Windows
    (Settings > Accessibility > Visual effects > Animation effects)."""
    global _motion
    if _motion is None:
        _motion = _read_motion()
    return _motion


def motion_chosen() -> bool:
    """Animations as chosen in Hanami, whatever Windows says."""
    if os.environ.get('SUITE_MOTION') in ('0', '1'):
        return os.environ['SUITE_MOTION'] == '1'
    return _suite_value('Motion') != 0


def _read_motion() -> bool:
    if not motion_chosen():
        return False
    if sys.platform == 'win32':
        on = wintypes.BOOL(1)
        if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(on), 0) and not on.value:
            return False                                    # SPI_GETCLIENTAREAANIMATION
    return True


def set_motion(on: bool) -> None:
    """Turn the suite's animations on or off (Hanami's settings do), for every app within seconds."""
    import winreg
    _set_suite_value('Motion', winreg.REG_DWORD, 1 if on else 0)
    _look_changed()


def mode() -> str:
    """Light or dark for the whole suite, as chosen in Hanami: 'system' (as Windows is), 'light'
    or 'dark'."""
    global _mode
    if _mode is None:
        _mode = _read_mode()
    return _mode


def _read_mode() -> str:
    if os.environ.get('SUITE_MODE') in MODES:
        return os.environ['SUITE_MODE']
    value = _suite_value('Mode')
    return value if value in MODES else 'system'


def set_mode(name: str) -> None:
    """Choose light or dark for the whole suite (Hanami's settings do); every suite app running
    takes it within a couple of seconds."""
    import winreg
    _set_suite_value('Mode', winreg.REG_SZ, name if name in MODES else 'system')
    _look_changed()


def app_mode(name: str) -> None:
    """An app's own choice of light or dark, over the suite's ('system' leaves it to the suite)."""
    global _app_mode
    _app_mode = name if name in MODES else 'system'
    _apply_mode()


def _scheme() -> Qt.ColorScheme:
    """Light or dark as chosen - the app's own choice, else the suite's; Unknown: as Windows is."""
    chosen = _app_mode if _app_mode != 'system' else mode()
    return {'light': Qt.ColorScheme.Light, 'dark': Qt.ColorScheme.Dark}.get(chosen, Qt.ColorScheme.Unknown)


def _apply_mode(starting: bool = False) -> None:
    """Show the app light or dark, as chosen. Qt then says the scheme changed, and everything
    restyles (install). As the app starts, a choice of Windows' leaves Qt as it is."""
    app = QGuiApplication.instance()
    if app is None or (starting and _scheme() == Qt.ColorScheme.Unknown):
        return
    app.styleHints().setColorScheme(_scheme())


def _look_changed() -> None:
    """The look, the animations or light or dark may have changed (in Hanami, or Windows): if so,
    take them."""
    global _look, _motion, _mode
    now = (_read_look(), _read_motion(), _read_mode())
    if now != (_look, _motion, _mode):
        mode_changed = now[2] != _mode
        _look, _motion, _mode = now
        if mode_changed:
            _apply_mode()
        _suite_changed()


# --------------------------------------------------------------------------- games
#
# While a full-screen game is in front, the suite keeps out of its way, unless that is turned off
# in Hanami: the glass stops looking at the screen, Hanami holds notifications back until the game
# is left, and Grabbit slows or pauses its downloads (game_downloads()). A game is a program Windows
# knows as one - those Game Bar has recognised, listed under GameConfigStore - whose window fills
# its screen, borderless or exclusive.

GAME_DOWNLOADS = {'slow': 'Slow down', 'pause': 'Pause', 'keep': 'Keep going'}
_GAMES_KEY = r'System\GameConfigStore\Children'
_GAMES_FOR = 60.0           # s: how long the list of games is kept before it is read again
_GAME_EVERY = 1500          # ms: how often the window in front is looked at
_games: tuple[float, frozenset] = (0.0, frozenset())
_programs: dict[int, str] = {}
_game_quiet = False
_game_watch = None
_game_callbacks: list = []


def game_quiet_chosen() -> bool:
    """Whether the suite keeps quiet while a full-screen game is in front, as chosen in Hanami."""
    return _suite_value('GameQuiet') != 0


def set_game_quiet(on: bool) -> None:
    import winreg
    _set_suite_value('GameQuiet', winreg.REG_DWORD, 1 if on else 0)
    _check_game()


def game_downloads() -> str:
    """What Grabbit does with its downloads while a game is in front: 'slow', 'pause' or 'keep'."""
    value = _suite_value('GameDownloads')
    return value if value in GAME_DOWNLOADS else 'slow'


def set_game_downloads(name: str) -> None:
    import winreg
    _set_suite_value('GameDownloads', winreg.REG_SZ, name if name in GAME_DOWNLOADS else 'slow')


def _known_games() -> frozenset:
    """The programs Windows knows as games, as full paths (os.path.normcase)."""
    global _games
    at, games = _games
    if at and time.monotonic() - at < _GAMES_FOR:
        return games
    found = set()
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _GAMES_KEY) as key:
            for index in itertools.count():
                try:
                    name = winreg.EnumKey(key, index)
                except OSError:
                    break
                try:
                    with winreg.OpenKey(key, name) as child:
                        path = winreg.QueryValueEx(child, 'MatchedExeFullPath')[0]
                except OSError:
                    continue
                if isinstance(path, str) and path:
                    found.add(os.path.normcase(path))
    except OSError:
        pass
    _games = (time.monotonic(), frozenset(found))
    return _games[1]


class _MonitorInfo(ctypes.Structure):
    _fields_ = [('cbSize', wintypes.DWORD), ('rcMonitor', wintypes.RECT), ('rcWork', wintypes.RECT),
                ('dwFlags', wintypes.DWORD)]


_game_dlls = None


def _game_calls():
    """user32 and kernel32 for looking at the window in front - copies of their own, so the types
    set on them here touch no other code's."""
    global _game_dlls
    if _game_dlls is None:
        user32, kernel32 = ctypes.WinDLL('user32'), ctypes.WinDLL('kernel32')
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.MonitorFromWindow.restype = wintypes.HMONITOR
        user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(_MonitorInfo)]
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        kernel32.OpenProcess.argtypes, kernel32.OpenProcess.restype = [wintypes.DWORD, wintypes.BOOL,
                                                                       wintypes.DWORD], wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                        ctypes.POINTER(wintypes.DWORD)]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        _game_dlls = user32, kernel32
    return _game_dlls


def _program_of(pid: int) -> str:
    """The full path (normcase) of the program a process runs, or ''."""
    if pid in _programs:
        return _programs[pid]
    kernel32 = _game_calls()[1]
    path = ''
    handle = kernel32.OpenProcess(0x1000, False, pid)              # PROCESS_QUERY_LIMITED_INFORMATION
    if handle:
        buffer = ctypes.create_unicode_buffer(1024)
        length = wintypes.DWORD(len(buffer))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
            path = os.path.normcase(buffer.value)
        kernel32.CloseHandle(handle)
    if len(_programs) > 64:
        _programs.clear()
    _programs[pid] = path
    return path


def game_in_front() -> bool:
    """Whether the window in front is a game's, filling its screen."""
    if sys.platform != 'win32':
        return False
    u = _game_calls()[0]
    hwnd = u.GetForegroundWindow()
    if not hwnd:
        return False
    rect = wintypes.RECT()
    info = _MonitorInfo()
    info.cbSize = ctypes.sizeof(info)
    monitor = u.MonitorFromWindow(hwnd, 2)                          # MONITOR_DEFAULTTONEAREST
    if not u.GetWindowRect(hwnd, ctypes.byref(rect)) or not u.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return False
    screen = info.rcMonitor
    if rect.left > screen.left or rect.top > screen.top or rect.right < screen.right or rect.bottom < screen.bottom:
        return False
    pid = wintypes.DWORD()
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    program = _program_of(pid.value)
    return bool(program) and program in _known_games()


def quiet_now() -> bool:
    """Whether the suite keeps quiet now: a full-screen game is in front, and that is chosen."""
    _watch_games()
    return _game_quiet


def on_game(callback) -> None:
    """Call callback(quiet) whenever a full-screen game comes to the front, or leaves it (while
    the suite is to keep quiet for games)."""
    _game_callbacks.append(callback)
    _watch_games()


def _watch_games() -> None:
    global _game_watch
    app = QApplication.instance()
    if _game_watch is None and app is not None:
        _game_watch = QTimer(app)
        _game_watch.setInterval(_GAME_EVERY)
        _game_watch.timeout.connect(_check_game)
        _game_watch.start()
        _check_game()


def _check_game() -> None:
    global _game_quiet
    try:
        quiet = game_quiet_chosen() and game_in_front()
    except OSError:
        quiet = False
    if quiet == _game_quiet:
        return
    _game_quiet = quiet
    for callback in list(_game_callbacks):
        try:
            callback(quiet)
        except Exception:                               # one app's part failing leaves the rest be
            import traceback
            traceback.print_exc()


# --------------------------------------------------------------------------- following changes

_restyled: weakref.WeakSet = weakref.WeakSet()
_callbacks: list = []


def restyle(widget: QWidget, make) -> None:
    """Give a widget the style sheet make() returns - now, and again whenever the look, the
    animations or Windows' light or dark change."""
    widget._suite_restyle = make
    _restyled.add(widget)
    widget.setStyleSheet(make())


def on_restyle(callback) -> None:
    """Call callback() whenever the look, the animations or Windows' light or dark change - for an
    app that styles itself (a style sheet for the whole app, say)."""
    _callbacks.append(callback)


def _suite_changed() -> None:
    """The look, the animations or Windows' light or dark changed: style sheets are made again,
    check boxes become switches (or back), and glass windows take the new glass."""
    for widget in list(_restyled):
        try:
            widget.setStyleSheet(widget._suite_restyle())
        except RuntimeError:                                # gone
            _restyled.discard(widget)
    for callback in list(_callbacks):
        callback()
    app = QApplication.instance()
    if app is not None:
        for widget in app.allWidgets():
            if isinstance(widget, QCheckBox):
                widget.updateGeometry()
                widget.update()
            elif getattr(widget, '_suite_lit', False):
                _round(widget)
    _refrost_all()


# --------------------------------------------------------------------------- windows

class _Margins(ctypes.Structure):
    _fields_ = [('left', ctypes.c_int), ('right', ctypes.c_int), ('top', ctypes.c_int), ('bottom', ctypes.c_int)]


class _Accent(ctypes.Structure):
    _fields_ = [('state', ctypes.c_int), ('flags', ctypes.c_int), ('color', ctypes.c_uint), ('animation', ctypes.c_int)]


class _CompositionData(ctypes.Structure):
    _fields_ = [('attribute', ctypes.c_int), ('data', ctypes.c_void_p), ('size', ctypes.c_size_t)]


_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWA_SYSTEMBACKDROP_TYPE = 38
_DWMWCP_ROUND, _DWMSBT_NONE, _DWMSBT_TRANSIENTWINDOW = 2, 1, 3     # rounded corners; no backdrop; Acrylic
_WCA_ACCENT_POLICY = 19
_ACCENT_DISABLED, _ACCENT_ENABLE_TRANSPARENTGRADIENT, _ACCENT_ENABLE_ACRYLICBLURBEHIND = 0, 2, 4


def _dwm(hwnd: int, attribute: int, value: int) -> None:
    v = ctypes.c_int(value)
    ctypes.windll.dwmapi.DwmSetWindowAttribute(wintypes.HWND(hwnd), attribute, ctypes.byref(v), ctypes.sizeof(v))


def _frameless(window: QWidget) -> bool:
    return bool(window.windowFlags() & Qt.FramelessWindowHint)


def _tint() -> int:
    """The frameless glass's tint, as Windows takes it (alpha, blue, green, red), should it fall
    back to Windows' blur: a deep plum on dark, a pinkish white on light."""
    color, alpha = (QColor('#221e27'), 0xA6) if is_dark() else (QColor('#fbf5f8'), 0xB4)
    return (alpha << 24) | (color.blue() << 16) | (color.green() << 8) | color.red()


def _accent(hwnd: int, state: int, color: int = 0) -> None:
    accent = _Accent(state, 2, color, 0)
    data = _CompositionData(_WCA_ACCENT_POLICY, ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p),
                            ctypes.sizeof(accent))
    ctypes.windll.user32.SetWindowCompositionAttribute(wintypes.HWND(hwnd), ctypes.byref(data))


def _apply_glass(window: QWidget) -> None:
    hwnd = int(window.winId())
    _dwm(hwnd, _DWMWA_USE_IMMERSIVE_DARK_MODE, 1 if is_dark() else 0)
    if _frameless(window):
        # A frameless window draws its glass itself, from the screen behind it; should it have none
        # (the shot failed), Windows' older blur behind (its Acrylic stays solid on these).
        _dwm(hwnd, _DWMWA_WINDOW_CORNER_PREFERENCE, _DWMWCP_ROUND)
        drawn = getattr(window, '_suite_shot', None) is not None
        _accent(hwnd, _ACCENT_DISABLED if drawn else _ACCENT_ENABLE_ACRYLICBLURBEHIND, _tint())
    elif _clear(window):
        # Liquid glass: what is behind shows through as it is, live - Windows' "transparent
        # gradient" in a colour of nothing, under the window's own frame - with the tint, the
        # bending rim and the light painted over it (_paint_clear).
        _dwm(hwnd, _DWMWA_SYSTEMBACKDROP_TYPE, _DWMSBT_NONE)
        ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(wintypes.HWND(hwnd), ctypes.byref(_Margins(0, 0, 0, 0)))
        _accent(hwnd, _ACCENT_ENABLE_TRANSPARENTGRADIENT, 0)
    else:
        _accent(hwnd, _ACCENT_DISABLED)
        _dwm(hwnd, _DWMWA_SYSTEMBACKDROP_TYPE, _DWMSBT_TRANSIENTWINDOW)
        ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(wintypes.HWND(hwnd), ctypes.byref(_Margins(-1, -1, -1, -1)))


def _glass_palette(palette: QPalette) -> QPalette:
    """A framed window's palette on glass: the veil for its background, see-through boxes and lists."""
    c = colors()
    out = QPalette(palette)
    for group in (QPalette.Active, QPalette.Inactive, QPalette.Disabled):
        out.setColor(group, QPalette.Window, veil())
        out.setColor(group, QPalette.Base, c.chip)
        out.setColor(group, QPalette.AlternateBase, c.tile)
        out.setColor(group, QPalette.Button, c.chip)
    return out


def frost(window: QWidget, fade: bool = True, backdrop: str | None = None) -> None:
    """Make a window glass, with the suite's animations - call before it is first shown. A
    frameless window then paints itself with paint_glass() and no shadow when glass_on(), and
    opens with an animation - unless not fade, for one that sees to its own opacity. A framed one
    can have a backdrop: a picture (a file) laid faintly over it, under its contents."""
    window.installEventFilter(_frosting())
    window.setProperty(_FROSTED, True)
    window.setProperty('_suite_fade', fade)
    window._suite_backdrop = backdrop
    if glass_on():
        window.setAttribute(Qt.WA_TranslucentBackground)
        if not _frameless(window):
            window.setPalette(_glass_palette(window.palette()))


def _refrost_all() -> None:
    """Windows went light or dark, or the look changed: the glass windows take the new tint,
    title bar, palette and glass, and their parts the new shapes."""
    if not glass_on():
        return
    for window in QApplication.topLevelWidgets():
        if window.property(_FROSTED) and window.isVisible():
            if not _frameless(window):
                window.setPalette(_glass_palette(window.palette()))
            if _screen_glass(window):
                _follow(window)
                _watch_around(window, start=True)
            else:
                _stop_live(window)
                _watch_around(window, stop=True)
            _apply_glass(window)
            _animate_parts(window)
            window.update()


class _Frosting(QObject):
    """Watches the glass windows: glass as each is shown, then its animations - and, under a framed
    one's contents, the veil, which Qt leaves unpainted on a see-through window, and its rim, its
    bend and its light. A frameless one's glass, and a framed one's rim, is taken from the screen
    as it opens, and follows it as it moves or changes size."""

    def eventFilter(self, obj, event):
        kind = event.type()
        if not isinstance(obj, QWidget) or not obj.isWindow():
            return False
        if kind == QEvent.Paint and not _frameless(obj):
            if _clear(obj):
                _paint_clear(obj, event)
            elif glass_on():
                painter = QPainter(obj)
                painter.setClipRect(event.rect())
                _paint_bridge(painter, obj)
                _paint_rim_lens(painter, obj)
                painter.fillRect(event.rect(), veil())
                _paint_framed_rim(painter, obj)
                painter.end()
            if getattr(obj, '_suite_backdrop', None):
                return _paint_backdrop(obj, event)
        elif kind == QEvent.Show:
            if _screen_glass(obj):
                _capture(obj)                       # not on screen yet: what is behind it, as it is
                _watch_around(obj, start=True)
            if glass_on() and not _frameless(obj) and not _clear(obj):
                _bridge(obj)
            if glass_on():
                _apply_glass(obj)
            _animate_parts(obj)
            if _frameless(obj) and obj.property('_suite_fade'):
                _open(obj)
            elif not _frameless(obj):
                _settle_in(obj)
        elif kind == QEvent.Hide:
            _stop_live(obj)
            _watch_around(obj, stop=True)
        elif kind in (QEvent.Move, QEvent.Resize) and obj.isVisible() and _screen_glass(obj):
            if not getattr(obj, '_suite_gliding', False):
                _follow(obj)
            if kind == QEvent.Move and not _frameless(obj):
                _update_rim(obj)                    # only its rim changes as it is dragged
            else:
                obj.update()
        elif kind == QEvent.ChildAdded and obj.isVisible():
            QTimer.singleShot(0, obj, lambda: _animate_parts(obj))
        return False


_BRIDGE_HOLD, _BRIDGE_FADE = 1200, 350    # ms: how long a frosted window's own glass stays, then fades


def _bridge(window: QWidget) -> None:
    """Windows takes about a second to bring a newly shown frosted window's Acrylic up, and until
    then the window is only its veil over what is behind - far too clear. So it opens over frosted
    glass of its own, made from the shot of the screen behind it its rim took as it opened (before
    it was on screen), which fades away as the Acrylic comes."""
    shot = getattr(window, '_suite_shot', None)
    if shot is None:
        return
    g, at, dpr = window.geometry(), window._suite_shot_at, shot.devicePixelRatio()
    part = shot.copy(QRect(round((g.x() - at.x()) * dpr), round((g.y() - at.y()) * dpr),
                           round(g.width() * dpr), round(g.height() * dpr)))
    if not part.isNull():
        _bridge_from(window, part)


def _bridge_from(window: QWidget, shot: QPixmap) -> None:
    """_bridge, from a shot of what is behind the window: blurred and tinted as Acrylic is (mostly
    its tint, a little of what is behind showing through), kept while Windows brings its own up,
    then faded out."""
    glass = _soften(shot, 16)
    painter = QPainter(glass)
    painter.fillRect(QRectF(0, 0, glass.width(), glass.height()),
                     QColor(44, 44, 44, 205) if is_dark() else QColor(250, 250, 250, 190))
    painter.end()
    window._suite_bridge = glass
    window._suite_bridge_opacity = 1.0
    ref = weakref.ref(window)

    def fade() -> None:
        w = ref()
        if w is None or getattr(w, '_suite_bridge', None) is not glass:
            return
        if not motion_on():
            done(w)
            return
        animation = QVariantAnimation(w)
        animation.setDuration(_BRIDGE_FADE)
        animation.setStartValue(1.0)
        animation.setEndValue(0.0)
        animation.setEasingCurve(QEasingCurve.InOutQuad)
        animation.valueChanged.connect(lambda value: (setattr(w, '_suite_bridge_opacity', float(value)),
                                                      w.update()))
        animation.finished.connect(lambda: done(w))
        _keep(animation)
        animation.start()

    def done(w) -> None:
        if getattr(w, '_suite_bridge', None) is glass:
            w._suite_bridge = None
            w.update()

    QTimer.singleShot(_BRIDGE_HOLD, fade)


def _paint_bridge(painter, window: QWidget) -> None:
    """A frosted window's own glass, while it opens (_bridge), under its veil."""
    glass = getattr(window, '_suite_bridge', None)
    if glass is not None:
        painter.setOpacity(window._suite_bridge_opacity)
        painter.drawPixmap(0, 0, glass)
        painter.setOpacity(1.0)


BACKDROP_OPACITY = 0.10       # a backdrop picture is this faint, so text over it reads as well as ever


def _paint_backdrop(window: QWidget, event) -> bool:
    """A window as it paints itself, then its backdrop over that, faintly - its contents paint
    after, on top. The picture covers the window, cropped to fit, and is kept scaled to its size."""
    try:
        type(window).paintEvent(window, event)
    except (AttributeError, RuntimeError, TypeError):
        return False
    dpr = window.devicePixelRatioF()
    key = (window.width(), window.height(), dpr)
    if getattr(window, '_suite_backdrop_key', None) != key:
        source = QPixmap(window._suite_backdrop)
        if source.isNull():
            window._suite_backdrop = None
            return True
        width, height = round(window.width() * dpr), round(window.height() * dpr)
        scaled = source.scaled(width, height, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        picture = scaled.copy((scaled.width() - width) // 2, (scaled.height() - height) // 2, width, height)
        picture.setDevicePixelRatio(dpr)
        window._suite_backdrop_pixmap = picture
        window._suite_backdrop_key = key
    painter = QPainter(window)
    painter.setClipRect(event.rect())
    painter.setOpacity(BACKDROP_OPACITY)
    painter.drawPixmap(0, 0, window._suite_backdrop_pixmap)
    painter.end()
    return True


_frosting_filter = None


def _frosting() -> _Frosting:
    global _frosting_filter
    if _frosting_filter is None:
        _frosting_filter = _Frosting()
    return _frosting_filter


# --------------------------------------------------------------------------- glass from the screen
#
# Windows' own materials only frost, and not frameless windows at all. So a frameless window draws
# its glass from the screen behind it: taken just before it opens, a little beyond its edges, then
# softened and bent - each ring in from the rim gathering light from a little further out, as a
# lens's edge does, so what lies just beyond each edge shows inside it.
#
# The glass follows the window: while it moves or changes size, the screen behind is taken again
# and again (Windows leaving the window itself out of the shots, SetWindowDisplayAffinity), the
# glass made from the last shot, shifted, in between; and a still window looks now and then at
# what is around it, and takes the screen again when that changed. While it is left out, the
# window is missing from screenshots and screen sharing too - only for those moments.
#
# Those later shots are taken on a thread of the glass's own (_Grabber): Windows takes several
# milliseconds over each, which a window being dragged would otherwise spend waiting, frame after
# frame, and its dragging would stutter. A framed window repaints only its rim as it is dragged
# (_update_rim) - the rest of it does not change - and gauges its tint again once it settles.
#
# Something being dragged (a file, say) has its image drawn by Windows under the cursor, above
# everything, and a shot catches it: the glass would show a copy of it frozen, then trailing the
# real one. So shots have it taken out (_heal), and a still window's glass waits while a mouse
# button is held (_button_down).

_WDA_NONE, _WDA_EXCLUDEFROMCAPTURE = 0, 0x11
_MARGIN = 56              # how far beyond a window's edges its shot of the screen reaches
_MARGIN_FRAMELESS = 160   # a pop-up's, further: one dragged fast stays within its last shot until the next comes
# band: how far in from the rim the glass bends light; reach: how far beyond the edge the light
# shown at the rim comes from, falling off towards the flat middle - by power over the band, or
# (rate) as exp(-rate x distance in), a thin rim bending hard, as liquid-glass-js's "rim" does;
# blur: how much the glass softens what is behind; steps: the rings it is drawn in; corner,
# shrink: the rings' corners.
_LENSES = {
    'frosted': dict(band=24, reach=24, power=1.5, blur=7, steps=8, corner=GLASS_RADIUS * 2, shrink=1.0),
    'liquid': dict(band=9, reach=48, rate=0.55, blur=2, steps=10, corner=LIQUID_RADIUS, shrink=1.0),
    # a framed window's rim, in either look: only the rings that bend in light from beyond its
    # edges (the window itself fills the rest of its shot)
    'rim': dict(band=5, reach=56, rate=0.55, blur=2, steps=6, corner=GLASS_RADIUS, shrink=1.0),
}
_LIVE_MS = 33             # how often a moving window takes the screen behind it again
_SETTLE = 0.35            # and for how long after it stops
_HIDDEN_FOR = 0.06        # how long Windows takes to leave a window out of shots
_WATCH_MS = 600           # how often a still window looks at what is around it


def _screen_glass(window: QWidget) -> bool:
    """Whether a window's glass is made from the screen: a frameless one's, and a framed one's rim,
    which bends in what lies beyond its edges, in either look."""
    return glass_on() and bool(window.property(_FROSTED))


def _clear(window: QWidget) -> bool:
    """A framed window of clear glass: the liquid look's."""
    return glass_on() and look() == 'liquid' and not _frameless(window) and bool(window.property(_FROSTED))


def _where(window: QWidget):
    """Where a window's shot of the screen is taken from: its screen, the part of the screen wanted
    (the window and a margin around it), the part of that on the screen - None if none is - and
    the margin."""
    g = window.frameGeometry()
    screen = QGuiApplication.screenAt(g.center()) or window.screen() or QGuiApplication.primaryScreen()
    margin = _MARGIN_FRAMELESS if _frameless(window) else _MARGIN
    want = g.adjusted(-margin, -margin, margin, margin)
    got = want.intersected(screen.geometry())
    return None if got.isEmpty() else (screen, got, want, margin)


def _capture(window: QWidget) -> None:
    """Take what is behind a window, a little beyond its edges, for its glass - now, as it opens.
    (Later shots are taken on the glass's own thread: _capture_later.) None while a game is in
    front: the glass is then plain."""
    where = None if quiet_now() else _where(window)
    if where is None:
        return
    screen, got, want, margin = where
    area = screen.geometry()
    shot = screen.grabWindow(0, got.x() - area.x(), got.y() - area.y(), got.width(), got.height())
    if not shot.isNull():
        _took(window, *_made(shot, got, want, _LENSES[look()]['blur'], not _frameless(window), True, margin,
                             _drag_images(_native(screen, got))))


def _made(shot, got: QRect, want: QRect, blur: int, framed: bool, tone: bool, margin: int = _MARGIN,
          dragged: list = ()) -> tuple:
    """A shot of the screen made into glass, on any thread (a QImage away from the windows' own):
    the images of anything being dragged taken out of it (dragged, in its pixels: _heal), made up
    to the size wanted, softened, and - if tone - with how bright and busy what is behind the window
    is. What is behind a frameless window is in the shot; a framed one's isn't (the window itself
    is there), so its tint goes by what is around it."""
    if dragged:
        shot = _heal(shot, dragged)
    if got != want:
        shot = _fill_out(shot, got, want)
    told = None
    if tone:
        inside = QRectF(margin, margin, want.width() - 2 * margin, want.height() - 2 * margin)
        told = _tone_around(shot, inside) if framed else _tone(shot, inside)
    return _soften(shot, blur), want.topLeft(), told


def _drag_images(native: QRect) -> list:
    """Where the images of things being dragged lie in a part of the screen (in Windows' own
    pixels), relative to it. Windows draws each in a window of its own, which follows the cursor
    above everything."""
    user32 = _gdi()[0]
    found, hwnd = [], None
    for _ in range(8):
        hwnd = user32.FindWindowExW(None, hwnd, 'SysDragImage', None)
        if not hwnd:
            break
        box = wintypes.RECT()
        if user32.IsWindowVisible(hwnd) and user32.GetWindowRect(hwnd, ctypes.byref(box)):
            part = QRect(box.left, box.top, box.right - box.left, box.bottom - box.top).intersected(native)
            if not part.isEmpty():
                found.append(part.translated(-native.topLeft()))
    return found


def _heal(shot, parts: list) -> QImage:
    """A shot with parts of it (in its own pixels) painted over from just around each, as though
    nothing were there: the image of something being dragged, which caught in a shot would sit
    frozen in the glass, then trail the real one about. Each part takes its edges blended across
    it, side to side and top to bottom. (Of a QPixmap, or on any thread a QImage.)"""
    image = (shot.toImage() if isinstance(shot, QPixmap) else QImage(shot)).convertToFormat(QImage.Format_RGB32)
    dpr = image.devicePixelRatio()
    image.setDevicePixelRatio(1.0)
    whole = image.rect()
    painter = QPainter(image)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    for part in parts:
        part = part.adjusted(-2, -2, 2, 2).intersected(whole)              # and its soft edge
        if part.isEmpty():
            continue
        xs = [x for x in (part.left() - 1, part.right() + 1) if whole.left() <= x <= whole.right()]
        ys = [y for y in (part.top() - 1, part.bottom() + 1) if whole.top() <= y <= whole.bottom()]
        blends = []
        if xs:
            across = QImage(2, part.height(), QImage.Format_RGB32)
            edges = QPainter(across)
            edges.drawImage(0, 0, image.copy(xs[0], part.top(), 1, part.height()))
            edges.drawImage(1, 0, image.copy(xs[-1], part.top(), 1, part.height()))
            edges.end()
            blends.append(across)
        if ys:
            down = QImage(part.width(), 2, QImage.Format_RGB32)
            edges = QPainter(down)
            edges.drawImage(0, 0, image.copy(part.left(), ys[0], part.width(), 1))
            edges.drawImage(0, 1, image.copy(part.left(), ys[-1], part.width(), 1))
            edges.end()
            blends.append(down)
        for n, blend in enumerate(blends):
            painter.setOpacity(1.0 / (n + 1))                               # the two, evenly
            painter.drawImage(QRectF(part), blend)
        painter.setOpacity(1.0)
    painter.end()
    image.setDevicePixelRatio(dpr)
    return image


def _took(window: QWidget, shot, at: QPoint, tone: tuple | None) -> None:
    """A window has a new shot for its glass: shown - for a framed window only along its rim,
    unless the tint changed with it."""
    if isinstance(shot, QImage):
        shot = QPixmap.fromImage(shot)
    window._suite_shot = shot
    window._suite_shot_at = at
    framed = not _frameless(window)
    tint = _clear_tint(window) if framed else None
    if tone is not None:
        old = getattr(window, '_suite_tone', None)
        if old is not None and (_live(window) or framed):
            tone = tuple(0.6 * a + 0.4 * b for a, b in zip(old, tone))
        window._suite_tone = tone
    if framed and (not _clear(window) or _clear_tint(window) == tint):
        _update_rim(window)
    else:
        window.update()


def _capture_later(window: QWidget, final: bool) -> bool:
    """Take what is behind a window again, on the glass's own thread, and show it when it comes. A
    framed window's tint is gauged again only from the last one (final), once it has settled, so
    as it is dragged only its rim repaints; a frameless window repaints whole anyway. False if a
    shot for the window is on its way already."""
    where = None if quiet_now() else _where(window)
    if where is None:
        if final:
            _stop_live(window)
        return True
    screen, got, want, margin = where
    blur, framed = _LENSES[look()]['blur'], not _frameless(window)
    tone = final or not framed
    native = _native(screen, got)
    ref = weakref.ref(window)

    def then(made):
        w = ref()
        if w is None or not w.isVisible():
            return
        if made is not None:
            _took(w, *made)
        if final and not _live(w):
            _stop_live(w)                   # back in shots, now the last one is taken

    return _grabber().take(('shot', id(window)), native, screen.devicePixelRatio(),
                           lambda image: _made(image, got, want, blur, framed, tone, margin, _drag_images(native)),
                           then)


def _native(screen, rect: QRect) -> QRect:
    """A part of a screen in Windows' own pixels: Qt's are scaled by the screen's DPI from its corner."""
    dpr = screen.devicePixelRatio()
    origin = screen.geometry().topLeft()
    return QRect(origin.x() + round((rect.x() - origin.x()) * dpr), origin.y() + round((rect.y() - origin.y()) * dpr),
                 round(rect.width() * dpr), round(rect.height() * dpr))


class _BitmapHeader(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('width', wintypes.LONG), ('height', wintypes.LONG),
                ('planes', wintypes.WORD), ('bit_count', wintypes.WORD), ('compression', wintypes.DWORD),
                ('image_size', wintypes.DWORD), ('x_ppm', wintypes.LONG), ('y_ppm', wintypes.LONG),
                ('used', wintypes.DWORD), ('important', wintypes.DWORD)]


_SRCCOPY, _CAPTUREBLT = 0x00CC0020, 0x40000000
_gdi_calls = None


def _gdi():
    """user32 and gdi32 for taking shots of the screen - copies of their own, so the types set on
    them here touch no other code's."""
    global _gdi_calls
    if _gdi_calls is None:
        user32, gdi32 = ctypes.WinDLL('user32'), ctypes.WinDLL('gdi32')
        user32.GetDC.argtypes, user32.GetDC.restype = [wintypes.HWND], wintypes.HDC
        user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
        user32.FindWindowExW.restype = wintypes.HWND
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetAsyncKeyState.argtypes, user32.GetAsyncKeyState.restype = [ctypes.c_int], ctypes.c_short
        gdi32.CreateCompatibleDC.argtypes, gdi32.CreateCompatibleDC.restype = [wintypes.HDC], wintypes.HDC
        gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                           ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
        gdi32.CreateDIBSection.restype = wintypes.HBITMAP
        gdi32.SelectObject.argtypes, gdi32.SelectObject.restype = [wintypes.HDC, wintypes.HGDIOBJ], wintypes.HGDIOBJ
        gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HDC,
                                 ctypes.c_int, ctypes.c_int, wintypes.DWORD]
        gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        gdi32.DeleteDC.argtypes = [wintypes.HDC]
        _gdi_calls = user32, gdi32
    return _gdi_calls


def _screen_image(rect: QRect) -> QImage | None:
    """A part of the screen (in Windows' own pixels) as it is shown, as Qt's grabWindow takes it -
    on any thread."""
    width, height = rect.width(), rect.height()
    if width <= 0 or height <= 0:
        return None
    user32, gdi32 = _gdi()
    screen = user32.GetDC(None)
    if not screen:
        return None
    memory = gdi32.CreateCompatibleDC(screen)
    header = _BitmapHeader(ctypes.sizeof(_BitmapHeader), width, -height, 1, 32)     # top row first
    bits = ctypes.c_void_p()
    bitmap = gdi32.CreateDIBSection(screen, ctypes.byref(header), 0, ctypes.byref(bits), None, 0)
    image = None
    if bitmap and bits.value:
        old = gdi32.SelectObject(memory, bitmap)
        if gdi32.BitBlt(memory, 0, 0, width, height, screen, rect.x(), rect.y(), _SRCCOPY | _CAPTUREBLT):
            image = QImage(ctypes.string_at(bits, width * height * 4), width, height, width * 4,
                           QImage.Format_RGB32).copy()
        gdi32.SelectObject(memory, old)
    if bitmap:
        gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(memory)
    user32.ReleaseDC(None, screen)
    return image


class _Grabber(QObject):
    """The glass's own thread: it takes shots of the screen, makes them into glass (make, given
    the shot as a QImage) and hands that back to the windows' thread (then) - one shot at a time
    for each key."""

    _done = Signal(object, object)

    def __init__(self):
        super().__init__()
        self._jobs = queue.SimpleQueue()
        self._waiting: dict = {}            # token: (key, then)
        self._busy: set = set()
        self._tokens = itertools.count()
        self._done.connect(self._finish)
        threading.Thread(target=self._work, name='suite-glass', daemon=True).start()

    def take(self, key, rect: QRect, dpr: float, make, then) -> bool:
        if key in self._busy:
            return False
        token = next(self._tokens)
        self._busy.add(key)
        self._waiting[token] = (key, then)
        self._jobs.put((token, rect, dpr, make))
        return True

    def _work(self) -> None:
        while True:
            token, rect, dpr, make = self._jobs.get()
            try:
                made = _screen_image(rect)
                if made is not None:
                    made.setDevicePixelRatio(dpr)
                    made = make(made)
            except Exception:                   # noqa: BLE001 - no shot this time: the glass keeps the last
                made = None
            try:
                self._done.emit(token, made)
            except RuntimeError:                # the app is closing
                return

    def _finish(self, token, made) -> None:
        key, then = self._waiting.pop(token, (None, None))
        self._busy.discard(key)
        if then is not None:
            then(made)


_grabber_thread = None


def _grabber() -> _Grabber:
    global _grabber_thread
    if _grabber_thread is None:
        _grabber_thread = _Grabber()
    return _grabber_thread


def _fill_out(shot, got: QRect, want: QRect):
    """A shot cut short by the screen's edge, made up to the size wanted: beyond the edge, roughly
    what is at it. (A QPixmap, or on any thread a QImage.)"""
    dpr = shot.devicePixelRatio()
    size = round(want.width() * dpr), round(want.height() * dpr)
    image = isinstance(shot, QImage)
    full = QImage(*size, QImage.Format_RGB32) if image else QPixmap(*size)
    full.setDevicePixelRatio(dpr)
    painter = QPainter(full)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    draw = painter.drawImage if image else painter.drawPixmap
    draw(QRectF(0, 0, want.width(), want.height()), shot, QRectF(shot.rect()))
    draw(QPointF(got.x() - want.x(), got.y() - want.y()), shot)
    painter.end()
    return full


def _soften(shot, factor: int):
    if factor <= 1:
        return shot
    small = shot.scaled(max(1, shot.width() // factor), max(1, shot.height() // factor), Qt.IgnoreAspectRatio,
                        Qt.SmoothTransformation)
    soft = small.scaled(shot.width(), shot.height(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    soft.setDevicePixelRatio(shot.devicePixelRatio())
    return soft


def _tone(shot, inner: QRectF) -> tuple[float, float]:
    """How bright what is behind the window is (0 to 1), and how busy (how much it changes from
    one spot to the next) - so the liquid glass's tint can keep text on it easy to read. (Of a
    QPixmap, or on any thread a QImage.)"""
    dpr = shot.devicePixelRatio()
    part = shot.copy(QRect(round(inner.x() * dpr), round(inner.y() * dpr), max(1, round(inner.width() * dpr)),
                           max(1, round(inner.height() * dpr))))
    n = 32
    image = (part.toImage() if isinstance(part, QPixmap) else part).scaled(
        n, n, Qt.IgnoreAspectRatio, Qt.SmoothTransformation).convertToFormat(QImage.Format_RGB32)
    raw, line = image.constBits(), image.bytesPerLine()          # blue, green, red, - for each spot
    lum = [[(0.0722 * raw[i] + 0.7152 * raw[i + 1] + 0.2126 * raw[i + 2]) / 255
            for i in range(y * line, y * line + 4 * n, 4)] for y in range(n)]
    mean = sum(map(sum, lum)) / (n * n)
    busy = sum(abs(lum[y][x] - lum[y][x + 1]) + abs(lum[y][x] - lum[y + 1][x])
               for y in range(n - 1) for x in range(n - 1)) / ((n - 1) ** 2)
    return mean, busy


def _tone_around(shot: QPixmap, inside: QRectF) -> tuple[float, float]:
    """_tone of the strips of a shot around the window in it, as a guess at what is behind it."""
    width, height = shot.width() / shot.devicePixelRatio(), shot.height() / shot.devicePixelRatio()
    strips = [QRectF(0, 0, width, inside.top()), QRectF(0, inside.bottom(), width, height - inside.bottom()),
              QRectF(0, inside.top(), inside.left(), inside.height()),
              QRectF(inside.right(), inside.top(), width - inside.right(), inside.height())]
    parts = [(strip.width() * strip.height(), _tone(shot, strip)) for strip in strips
             if strip.width() >= 2 and strip.height() >= 2]
    total = sum(area for area, _ in parts) or 1
    return (sum(area * tone[0] for area, tone in parts) / total,
            sum(area * tone[1] for area, tone in parts) / total)


def _lens_for(window: QWidget) -> QPixmap | None:
    """The window's glass for where it is now, made again only when it moved, changed size or
    took a new shot. A framed window's is only its rim, which bends in what lies beyond its edges;
    within that, what is behind it shows through as it is."""
    shot = getattr(window, '_suite_shot', None)
    if shot is None:
        return None
    at = window._suite_shot_at
    corner = window.mapToGlobal(QPoint(0, 0))
    framed = not _frameless(window)
    spec = _LENSES['rim' if framed else look()]
    above = _ABOVE if framed else 0
    key = (look(), framed, window.width(), window.height(), corner.x() - at.x(), corner.y() - at.y(),
           shot.cacheKey())
    if getattr(window, '_suite_lens_key', None) != key:
        window._suite_lens = _lens(shot, key[4], key[5] - above, window.width(), window.height() + above, spec,
                                   rim_only=framed)
        window._suite_lens_key = key
    return window._suite_lens


def _lens(shot: QPixmap, x: float, y: float, width: int, height: int, spec: dict,
          rim_only: bool = False) -> QPixmap:
    """Glass width x height, for a window at (x, y) in the shot: each ring in from the rim drawn
    from a little more of what lies beyond the window's edges, the middle as it is (or, rim_only,
    left out)."""
    dpr = shot.devicePixelRatio()
    out = QPixmap(max(1, round(width * dpr)), max(1, round(height * dpr)))
    out.setDevicePixelRatio(dpr)
    out.fill(Qt.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    full = QRectF(0, 0, width, height)
    room_x, room_y = shot.width() / dpr, shot.height() / dpr
    rings = _rings(width, height, spec)
    for ring, reach in rings[:-1] if rim_only else rings:
        painter.setClipPath(ring)
        w, h = width + 2 * reach, height + 2 * reach
        left = min(max(x - reach, 0.0), max(0.0, room_x - w))     # kept inside the shot
        top = min(max(y - reach, 0.0), max(0.0, room_y - h))
        painter.drawPixmap(full, shot, QRectF(left * dpr, top * dpr, w * dpr, h * dpr))
    painter.end()
    return out


_ring_cache: dict = {}


def _rings(width: int, height: int, spec: dict) -> list:
    """The lens's rings for a window's size, each with how far beyond the edges it reaches - from
    the rim in to the flat middle, each ring only its own band (the middle whole)."""
    key = (width, height, tuple(sorted(spec.items())))
    rings = _ring_cache.get(key)
    if rings is None:
        if len(_ring_cache) > 24:
            _ring_cache.clear()
        full = QRectF(0, 0, width, height)
        shapes = []
        for i in range(spec['steps']):
            t = i / (spec['steps'] - 1)                     # 0 at the rim, 1 where the glass is flat
            inset = spec['band'] * t
            corner = max(2.0, spec['corner'] - inset * spec['shrink'])
            shape = QPainterPath()
            shape.addRoundedRect(full.adjusted(inset, inset, -inset, -inset), corner, corner)
            if t >= 1:
                reach = 0.0
            elif 'rate' in spec:
                reach = spec['reach'] * math.exp(-spec['rate'] * inset)
            else:
                reach = spec['reach'] * (1 - t) ** spec['power']
            shapes.append((shape, reach))
        rings = []
        for i, (shape, reach) in enumerate(shapes):
            ring = QPainterPath(shape)
            if i + 1 < len(shapes):
                ring.addPath(shapes[i + 1][0])
                ring.setFillRule(Qt.OddEvenFill)
            rings.append((ring, reach))
        _ring_cache[key] = rings
    return rings


def _live(window: QWidget) -> bool:
    timer = getattr(window, '_suite_live', None)
    return timer is not None and timer.isActive()


def _follow(window: QWidget) -> None:
    """The window moved, changed size, or what is behind it changed: take the screen behind it
    again and again until it settles, Windows leaving a frameless window out of the shots
    meanwhile. (A framed one's glass only takes what is around it, so it stays in them.)"""
    now = time.monotonic()
    window._suite_settle = now + _SETTLE
    if not _frameless(window):
        window._suite_hidden_at = None
    elif getattr(window, '_suite_hidden_at', None) is None:
        ctypes.windll.user32.SetWindowDisplayAffinity(wintypes.HWND(int(window.winId())), _WDA_EXCLUDEFROMCAPTURE)
        window._suite_hidden_at = now
    timer = getattr(window, '_suite_live', None)
    if timer is None:
        timer = window._suite_live = QTimer(window)
        timer.setInterval(_LIVE_MS)
        ref = weakref.ref(window)
        timer.timeout.connect(lambda: (w := ref()) is not None and _live_frame(w))
    if not timer.isActive():
        timer.start()


def _live_frame(window: QWidget) -> None:
    if not window.isVisible():
        _stop_live(window)
        return
    now = time.monotonic()
    hidden_at = window._suite_hidden_at
    if hidden_at is not None and now - hidden_at < _HIDDEN_FOR:
        return
    final = now >= window._suite_settle
    if _capture_later(window, final) and final:
        window._suite_live.stop()                   # (left out of shots until that last one comes)


def _stop_live(window: QWidget) -> None:
    timer = getattr(window, '_suite_live', None)
    if timer is not None:
        timer.stop()
    if getattr(window, '_suite_hidden_at', None) is not None and _frameless(window):
        ctypes.windll.user32.SetWindowDisplayAffinity(wintypes.HWND(int(window.winId())), _WDA_NONE)
        window._suite_hidden_at = None
    window._suite_around = None


def _watch_around(window: QWidget, start: bool = False, stop: bool = False) -> None:
    """Now and then a still window looks at the strips of screen around it; when they change,
    what is behind it most likely did too (another window came up, a page scrolled), and it takes
    the screen behind it again."""
    timer = getattr(window, '_suite_watch', None)
    if stop:
        if timer is not None:
            timer.stop()
        return
    if start:
        if timer is None:
            timer = window._suite_watch = QTimer(window)
            timer.setInterval(_WATCH_MS)
            ref = weakref.ref(window)
            timer.timeout.connect(lambda: (w := ref()) is not None and _watch_around(w))
        window._suite_around = None
        timer.start()
        return
    if not window.isVisible() or _live(window) or not _screen_glass(window) or _button_down() or quiet_now():
        return
    g = window.frameGeometry()
    screen = QGuiApplication.screenAt(g.center()) or window.screen() or QGuiApplication.primaryScreen()
    gap, strip = _AROUND
    outer = g.adjusted(-gap - strip, -gap - strip, gap + strip, gap + strip).intersected(screen.geometry())
    if outer.isEmpty():
        return
    ref = weakref.ref(window)

    def then(now):
        w = ref()
        if w is None or not w.isVisible() or _live(w):
            return
        last = getattr(w, '_suite_around', None)
        w._suite_around = now
        if now is not None and last is not None and len(now) == len(last) \
                and sum(abs(a - b) for a, b in zip(now, last)) / len(now) > 0.025:
            _follow(w)

    _grabber().take(('around', id(window)), _native(screen, outer), screen.devicePixelRatio(),
                    lambda shot: _around(shot, outer, g), then)


_AROUND = 4, 12         # the strips a still window watches: how far out from its edges they start, how wide


def _button_down() -> bool:
    """Whether a mouse button is held: something being dragged about, most likely, near the
    window - which a still window's glass leaves out, rather than chase it with shots that would
    each show it a moment late. Once the button is let go, the glass catches up."""
    keys = _gdi()[0].GetAsyncKeyState
    return any(keys(key) & 0x8000 for key in (0x01, 0x02, 0x04))      # left, right, middle


def _around(shot, outer: QRect, g: QRect) -> list:
    """The colours of the strips of screen just around a window (g), a few of each, from a shot of
    outer - on any thread."""
    gap = _AROUND[0]
    dpr = shot.devicePixelRatio()
    x0, y0 = g.x() - outer.x(), g.y() - outer.y()
    parts = [QRect(0, 0, outer.width(), y0 - gap),                                     # above
             QRect(0, y0 + g.height() + gap, outer.width(), outer.height() - y0 - g.height() - gap),
             QRect(0, 0, x0 - gap, outer.height()),                                    # left
             QRect(x0 + g.width() + gap, 0, outer.width() - x0 - g.width() - gap, outer.height())]
    values = []
    for part in parts:
        if part.width() < 2 or part.height() < 2:
            values += [0.0] * 24
            continue
        piece = shot.copy(QRect(round(part.x() * dpr), round(part.y() * dpr), round(part.width() * dpr),
                                round(part.height() * dpr)))
        wide = part.width() >= part.height()
        image = (piece.toImage() if isinstance(piece, QPixmap) else piece).scaled(
            8 if wide else 1, 1 if wide else 8, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        for i in range(8):
            color = image.pixelColor(i if wide else 0, 0 if wide else i)
            values += [color.redF(), color.greenF(), color.blueF()]
    return values


def _lean(window: QWidget) -> float:
    """Where the window sits across its screen, -0.5 (left) to 0.5 (right): the light on liquid
    glass shifts with it, so the highlights move as the window does."""
    screen = window.screen() or QGuiApplication.primaryScreen()
    area = screen.geometry()
    middle = window.frameGeometry().center().x()
    return max(-0.5, min(0.5, (middle - area.center().x()) / max(1, area.width())))


def _light_of(window: QWidget) -> int:
    """The angle the light on a window's glass comes from, in whole degrees, from above-left - a
    little more from the left the further right it is."""
    return round(120 - 50 * _lean(window))


_ABOVE = 40      # a framed window's glass runs on up behind its title bar: no rim or corners along its top
_RIM_SPAN = 12   # how far in from a framed window's edges its rim, its light and the bending reach


def _update_rim(window: QWidget) -> None:
    """Repaint only a framed window's rim - its sides and bottom (the top is under the title
    bar) - where its glass changes as it is dragged: not the rest of it, nor the parts on it."""
    r = window.rect()
    window.update(QRegion(r).subtracted(QRegion(r.adjusted(_RIM_SPAN, 0, -_RIM_SPAN, -_RIM_SPAN))))


def _rim_light(window: QWidget, r: QRectF, radius: float, light: int, alphas: tuple[int, int]) -> QPixmap:
    """The light on a glass rim - its soft glow inside the edge (_rim_glow, alphas[0]) and the
    bright edge (_edge_light, alphas[1]) - drawn once into a picture the window's size and kept:
    it takes a few milliseconds to draw, and a dragged window repaints its rim frame after frame.
    The light kept to whole degrees, it is drawn again only now and then as the window goes."""
    dpr = window.devicePixelRatioF()
    key = (window.width(), window.height(), dpr, r.getRect(), radius, light, alphas)
    if getattr(window, '_suite_rim_key', None) != key:
        layer = QPixmap(max(1, round(window.width() * dpr)), max(1, round(window.height() * dpr)))
        layer.setDevicePixelRatio(dpr)
        layer.fill(Qt.transparent)
        painter = QPainter(layer)
        painter.setRenderHint(QPainter.Antialiasing)
        _rim_glow(painter, r, radius, light, alphas[0])
        _edge_light(painter, r, radius, light, alphas[1], 1.3)
        painter.end()
        window._suite_rim, window._suite_rim_key = layer, key
    window._suite_rim_light = light
    return window._suite_rim


def _paint_rim_lens(painter, window: QWidget) -> None:
    """A framed window's rim, bending in what lies beyond its edges (_lens_for), in either look.
    Its glass runs on up behind the title bar, so its top edge, rim and corners are out of sight."""
    lens = _lens_for(window)
    if lens is not None:
        dpr = lens.devicePixelRatio()
        painter.drawPixmap(QRectF(0, -_ABOVE, lens.width() / dpr, lens.height() / dpr), lens, QRectF(lens.rect()))


def _paint_framed_rim(painter, window: QWidget) -> None:
    """The light on a framed glass window's rim, in either look - none along its top, as its glass
    runs on up behind the title bar."""
    edge = QRectF(window.rect()).adjusted(0.5, 0.5 - _ABOVE, -0.5, -0.5)
    alphas = (46, 175) if is_dark() else (80, 245)
    painter.drawPixmap(0, 0, _rim_light(window, edge, GLASS_RADIUS, _light_of(window), alphas))


def _clear_tint(window: QWidget) -> int:
    """How much a framed window's clear glass tints what is behind it: clear where that is calm,
    more where its text would be hard to read - as _liquid_tint, from a little more, as a window
    holds more text than a pop-up."""
    mean, busy = getattr(window, '_suite_tone', None) or (0.5, 0.05)
    if is_dark():
        return int(min(220, 110 + 160 * max(0.0, mean - 0.25) + 600 * busy))
    return int(min(215, 95 + 150 * max(0.0, 0.7 - mean) + 600 * busy))


def _paint_clear(window: QWidget, event) -> None:
    """Under a framed window's contents, in the liquid look: its clear glass - a light tint, lit
    from above, adapting to what is around it - the rim bending in what lies beyond the window's
    edges, and the light catching the edges."""
    dark = is_dark()
    painter = QPainter(window)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    painter.setClipRect(event.rect())
    r = QRectF(window.rect())
    _paint_rim_lens(painter, window)            # the rim first, then the tint over all of it
    alpha = _clear_tint(window)
    tint = QLinearGradient(r.topLeft(), r.bottomLeft())
    tint.setColorAt(0.0, QColor(46, 43, 54, alpha) if dark else QColor(255, 255, 255, alpha))
    tint.setColorAt(1.0, QColor(20, 18, 26, alpha) if dark else QColor(232, 232, 238, alpha))
    painter.fillRect(r, tint)
    _paint_framed_rim(painter, window)
    painter.end()


def _rim_glow(painter, r: QRectF, radius: float, light: float, alpha: int, depth: int = 7) -> None:
    """The rim's own light, inside the edge: brightest at the edge and fading smoothly inwards over
    depth pixels, by which way each bit of the edge faces - the glass's thickness catching the
    light, without a hard line where the rim meets the rest."""
    for k in range(depth):
        inset = 1.0 + k
        fade = (1 - k / depth) ** 2
        _edge_light(painter, r.adjusted(inset, inset, -inset, -inset), max(0.5, radius - inset), light,
                    int(alpha * fade), 1.0)


def _paint_frosted(painter, rect, radius: float, window: QWidget, lens: QPixmap) -> None:
    """Frosted glass: what is behind, softened, through the lens's rim, under a light veil for
    reading, a glow where the light falls, and the liquid glass's rim catching the light by which
    way each bit of it faces."""
    dark = is_dark()
    r = QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5)
    path = QPainterPath()
    path.addRoundedRect(r, radius, radius)
    dpr = lens.devicePixelRatio()
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    painter.setClipPath(path)
    painter.drawPixmap(QRectF(0, 0, lens.width() / dpr, lens.height() / dpr), lens, QRectF(lens.rect()))
    painter.setClipping(False)
    painter.fillPath(path, QColor(18, 16, 24, 118) if dark else QColor(255, 255, 255, 128))
    glow = QRadialGradient(QPointF(r.left() + r.width() * 0.2, r.top() + r.height() * 0.08),
                           max(r.width(), r.height()) * 0.9)
    glow.setColorAt(0, QColor(255, 255, 255, 38 if dark else 70))
    glow.setColorAt(1, QColor(255, 255, 255, 0))
    painter.fillPath(path, glow)
    painter.drawPixmap(0, 0, _rim_light(window, r, radius, _light_of(window), (40, 180) if dark else (85, 245)))
    painter.restore()


def _liquid_tint(window: QWidget) -> int:
    """How much liquid glass tints what is behind it (0 to 255): a fifth, as liquid-glass-js's
    0.2 - clear glass - and more only where text on it would be hard to read: over what is bright
    (under light text) or dark (under dark text), or busy, as a page of text is."""
    mean, busy = getattr(window, '_suite_tone', None) or (0.5, 0.05)
    if is_dark():
        return int(min(200, 64 + 160 * max(0.0, mean - 0.25) + 600 * busy))
    return int(min(200, 51 + 150 * max(0.0, 0.7 - mean) + 600 * busy))


def _paint_liquid(painter, rect, window: QWidget, lens: QPixmap) -> None:
    """Liquid glass: what is behind, through the lens - clear in the middle, the rim bending in
    what lies just beyond the edges - under a light tint, lit from above, with a fine rim catching
    the light by which way each bit of it faces; the light shifts a little with where the window
    is, so the highlights move as it does. While it opens, the glass grows into place."""
    dark = is_dark()
    radius = LIQUID_RADIUS
    r = QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5)
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    grown = window.property(_UNFOLD)
    if grown is not None and float(grown) < 1:
        scale = 0.93 + 0.07 * float(grown)
        middle = r.center()
        painter.translate(middle)
        painter.scale(scale, scale)
        painter.translate(-middle)
    path = QPainterPath()
    path.addRoundedRect(r, radius, radius)
    dpr = lens.devicePixelRatio()
    painter.setClipPath(path)
    painter.drawPixmap(QRectF(0, 0, lens.width() / dpr, lens.height() / dpr), lens, QRectF(lens.rect()))
    painter.setClipping(False)
    # The tint: toward white at the top and a light grey at the bottom (a dark grey to near black
    # on dark), so the glass looks lit from above.
    alpha = _liquid_tint(window)
    tint = QLinearGradient(r.topLeft(), r.bottomLeft())
    tint.setColorAt(0.0, QColor(62, 58, 70, alpha) if dark else QColor(255, 255, 255, alpha))
    tint.setColorAt(1.0, QColor(12, 10, 16, alpha) if dark else QColor(178, 178, 184, alpha))
    painter.fillPath(path, tint)
    shade = QLinearGradient(r.topLeft(), r.bottomLeft())
    shade.setColorAt(0.0, QColor(255, 255, 255, 22))
    shade.setColorAt(0.5, QColor(255, 255, 255, 0))
    shade.setColorAt(1.0, QColor(0, 0, 0, 0))
    painter.fillPath(path, shade)
    painter.drawPixmap(0, 0, _rim_light(window, r, radius, _light_of(window), (40, 180) if dark else (85, 245)))
    painter.restore()


def _edge_light(painter, r: QRectF, radius: float, light: float, alpha: int, width: float) -> None:
    """Light caught along a rounded rectangle's edge by which way each bit of it faces (light, the
    angle the light comes from, in degrees, counter-clockwise from the right): brightest facing
    the light, nearly as bright facing straight away (light passing through the glass), dim side
    on - as liquid glass's rim is. The straight sides each take one brightness, the corners sweep
    through theirs."""
    def lit(angle: float) -> float:
        facing = math.cos(math.radians(angle - light))
        return (max(0.0, facing) + 0.8 * max(0.0, -facing)) ** 1.5

    def color(angle: float) -> QColor:
        return QColor(255, 255, 255, int(alpha * min(1.0, lit(angle))))

    radius = max(0.5, min(radius, r.width() / 2, r.height() / 2))
    left, top, right, bottom = r.left(), r.top(), r.right(), r.bottom()
    painter.setBrush(Qt.NoBrush)
    for start, end, facing in ((QPointF(left + radius, top), QPointF(right - radius, top), 90),
                               (QPointF(right, top + radius), QPointF(right, bottom - radius), 0),
                               (QPointF(right - radius, bottom), QPointF(left + radius, bottom), 270),
                               (QPointF(left, bottom - radius), QPointF(left, top + radius), 180)):
        painter.setPen(QPen(color(facing), width, Qt.SolidLine, Qt.FlatCap))
        painter.drawLine(start, end)
    for middle, first in ((QPointF(right - radius, top + radius), 0), (QPointF(left + radius, top + radius), 90),
                          (QPointF(left + radius, bottom - radius), 180), (QPointF(right - radius, bottom - radius), 270)):
        sweep = QConicalGradient(middle, 0)
        for step in range(13):
            angle = first + 90 * step / 12
            sweep.setColorAt((angle % 360) / 360 if angle < 360 else 1.0, color(angle))
        corner = QPainterPath()
        box = QRectF(middle.x() - radius, middle.y() - radius, 2 * radius, 2 * radius)
        corner.arcMoveTo(box, first)
        corner.arcTo(box, first, 90)
        painter.setPen(QPen(QBrush(sweep), width, Qt.SolidLine, Qt.FlatCap))
        painter.drawPath(corner)


# --------------------------------------------------------------------------- light and shape

_LIGHT = '_suite_light'


def light_up(widget: QWidget, radius: float | None = None) -> None:
    """Give a widget (a panel, say) the liquid look's light under the pointer, which brightens as
    it is pressed; its corners, radius, are rounded as its style sheet's are (panel_css: 18)."""
    widget._suite_light_radius = radius
    _light(widget)


def _light(widget: QWidget) -> None:
    if getattr(widget, '_suite_lit', False):
        return
    if widget.metaObject().className() != type(widget).__name__:
        return              # one Qt made of its own kind (a text box's clear button, say): it paints itself
    widget._suite_lit = True
    widget.setAttribute(Qt.WA_Hover)
    widget.installEventFilter(_lights())
    _round(widget)


def _round(widget: QWidget) -> None:
    """In the liquid look, a button or text box is a capsule: rounded to half its height - which a
    style sheet can't say (and it drops a radius larger than that) - so each one has its own."""
    if not isinstance(widget, (QPushButton, QToolButton, QLineEdit)) \
            or isinstance(widget.parent(), (QAbstractSpinBox, QComboBox)):
        return
    own = widget.styleSheet()
    if own != getattr(widget, '_suite_round', ''):
        return                                              # the app styles this one itself
    want = f'border-radius: {max(2, widget.height() // 2)}px;' if _liquid_look() else ''
    if want != own:
        widget._suite_round = want
        widget.setStyleSheet(want)


class _Light(QObject):
    """The liquid look's light: a soft glow under the pointer on a button or panel, swelling and
    brightening its rim as it is pressed, and fading away after."""

    def eventFilter(self, obj, event):
        kind = event.type()
        if kind == QEvent.Resize:
            _round(obj)
        elif kind == QEvent.Paint:
            return self._paint(obj, event)
        elif not (_liquid_look() and motion_on()) or isinstance(obj, QLineEdit):
            return False
        elif kind in (QEvent.HoverEnter, QEvent.HoverMove):
            self._state(obj)['at'] = event.position()
            if kind == QEvent.HoverEnter:
                self._to(obj, 'hover', 1.0, 160)
            obj.update()
        elif kind == QEvent.HoverLeave:
            self._to(obj, 'hover', 0.0, 380)
        elif kind == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            self._state(obj)['at'] = event.position()
            self._to(obj, 'press', 1.0, 110)
        elif kind == QEvent.MouseButtonRelease:
            self._to(obj, 'press', 0.0, 480)
        return False

    @staticmethod
    def _state(obj) -> dict:
        state = getattr(obj, _LIGHT, None)
        if state is None:
            state = {'hover': 0.0, 'press': 0.0, 'at': QPointF(), 'moving': {}}
            setattr(obj, _LIGHT, state)
        return state

    def _to(self, obj, which: str, value: float, ms: int) -> None:
        state = self._state(obj)
        old = state['moving'].get(which)
        if old is not None:
            old.stop()
        animation = QVariantAnimation(obj)
        animation.setDuration(ms)
        animation.setStartValue(float(state[which]))
        animation.setEndValue(value)
        animation.setEasingCurve(QEasingCurve.OutCubic)

        def step(v):
            state[which] = float(v)
            obj.update()
        animation.valueChanged.connect(step)
        state['moving'][which] = animation
        _keep(animation)
        animation.start()

    def _paint(self, obj, event) -> bool:
        state = getattr(obj, _LIGHT, None)
        if not state or (state['hover'] < 0.01 and state['press'] < 0.01) or not _liquid_look():
            return False
        try:
            type(obj).paintEvent(obj, event)                # the widget as it paints itself, then the light
        except (AttributeError, RuntimeError, TypeError):
            return False
        dark = is_dark()
        painter = QPainter(obj)
        painter.setRenderHint(QPainter.Antialiasing)
        r = QRectF(obj.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = getattr(obj, '_suite_light_radius', None)
        radius = min(r.height() / 2, r.width() / 2) if radius is None else radius
        path = QPainterPath()
        path.addRoundedRect(r, radius, radius)
        hover, press, at = state['hover'], state['press'], state['at']
        strength = (24 if dark else 46) * hover + (58 if dark else 84) * press
        glow = QRadialGradient(at, math.hypot(r.width(), r.height()) * (0.45 + 0.35 * press))
        glow.setColorAt(0.0, QColor(255, 255, 255, int(strength)))
        glow.setColorAt(0.55, QColor(255, 255, 255, int(strength * 0.3)))
        glow.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.fillPath(path, glow)
        if press > 0.01:
            painter.setPen(QPen(QColor(255, 255, 255, int(160 * press)), 1.2))
            painter.drawPath(path)
        painter.end()
        return True


_lights_filter = None


def _lights() -> _Light:
    global _lights_filter
    if _lights_filter is None:
        _lights_filter = _Light()
    return _lights_filter


# --------------------------------------------------------------------------- animations

_running: set = set()          # animations under way, kept from Python's collector until they finish


def _keep(animation) -> None:
    _running.add(animation)
    animation.finished.connect(lambda: _running.discard(animation))


def fade_in(widget: QWidget, ms: int = 170, window: bool = False) -> None:
    """Fade a widget in: a window by its opacity, a page inside one by an opacity effect (left
    alone if it holds video or OpenGL, which such effects break). Not when animations are off."""
    if not motion_on():
        return
    if window:
        widget.setWindowOpacity(0.0)
        animation = QPropertyAnimation(widget, b'windowOpacity', widget)
    else:
        if widget.graphicsEffect() is not None or _holds_video(widget):
            return
        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(0.0)
        widget.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b'opacity', widget)
        animation.finished.connect(lambda: widget.setGraphicsEffect(None))
    animation.setDuration(ms)
    animation.setStartValue(0.0)
    animation.setEndValue(1.0)
    animation.setEasingCurve(QEasingCurve.OutCubic)
    _keep(animation)
    animation.start()


def _open(window: QWidget) -> None:
    """A frameless window opening: it fades in - and in the liquid look, rises the last few pixels
    into place as its glass unfolds, what is behind bending through it as it goes."""
    if not motion_on():
        return
    liquid = _liquid_look() and _screen_glass(window)
    fade_in(window, 200 if liquid else 170, window=True)
    if not liquid:
        return
    window.setProperty(_UNFOLD, 0.0)
    unfold = QVariantAnimation(window)
    unfold.setDuration(380)
    unfold.setStartValue(0.0)
    unfold.setEndValue(1.0)
    unfold.setEasingCurve(QEasingCurve.OutCubic)

    def grow(value):
        window.setProperty(_UNFOLD, float(value))
        window.update()

    def grown():
        window.setProperty(_UNFOLD, None)
        window.update()
    unfold.valueChanged.connect(grow)
    unfold.finished.connect(grown)
    _keep(unfold)
    unfold.start()
    end = window.pos()
    _glide(window, end + QPoint(0, 10), end)


def _glide(window: QWidget, start: QPoint, end: QPoint) -> None:
    """Move a window from start to end, easing in - stopping if the app moves it itself."""
    def place(point: QPoint) -> None:
        window._suite_gliding = True
        window.move(point)
        window._suite_gliding = False
        window._suite_glide_to = point

    place(start)
    glide = QVariantAnimation(window)
    glide.setDuration(380)
    glide.setStartValue(QPointF(start))
    glide.setEndValue(QPointF(end))
    glide.setEasingCurve(QEasingCurve.OutCubic)

    def step(value):
        if window.pos() != window._suite_glide_to:
            glide.stop()                                    # moved elsewhere meanwhile
            return
        place(value.toPoint())
    glide.valueChanged.connect(step)
    _keep(glide)
    glide.start()


def _animate_parts(window: QWidget) -> None:
    """Once per window part: its stacked pages fade in as they change, its check boxes tick
    smoothly, and its buttons and text boxes take the liquid look's shape and light."""
    for stack in window.findChildren(QStackedWidget):
        if not stack.property('_suite_fades'):
            stack.setProperty('_suite_fades', True)
            stack.currentChanged.connect(lambda _, s=stack: s.isVisible() and s.currentWidget() is not None
                                         and _page_in(s.currentWidget()))
    for box in window.findChildren(QCheckBox):
        if not box.property('_suite_ticks'):
            box.setProperty('_suite_ticks', True)
            box.toggled.connect(lambda on, b=box: _tick(b, on))
    for kind in (QPushButton, QToolButton, QLineEdit):
        for widget in window.findChildren(kind):
            _light(widget)
    for bar in window.findChildren(QProgressBar):
        if not bar.property('_suite_glides'):
            bar.setProperty('_suite_glides', True)
            _glide_progress(bar)


def _page_in(page: QWidget) -> None:
    """A page coming up in a stack: its sections rise into place one after another (or, without
    any, it fades in) - and in the liquid look, it slides the last few pixels into place."""
    if not motion_on():
        return
    sections = _sections(page)
    if sections:
        stagger_in(sections)
    else:
        fade_in(page)
    if _liquid_look():
        end = page.pos()
        slide = QPropertyAnimation(page, b'pos', page)
        slide.setDuration(260)
        slide.setStartValue(end + QPoint(18, 0))
        slide.setEndValue(end)
        slide.setEasingCurve(QEasingCurve.OutCubic)
        _keep(slide)
        slide.start()


def _tick(box: QCheckBox, on: bool) -> None:
    if not box.isVisible() or not motion_on():
        return
    animation = QVariantAnimation(box)
    start = box.property(_TICK)
    animation.setStartValue(float(start) if start is not None else (0.0 if on else 1.0))
    animation.setEndValue(1.0 if on else 0.0)
    if _liquid_look():
        spring = QEasingCurve(QEasingCurve.OutBack)
        spring.setOvershoot(1.3)
        animation.setDuration(320)
        animation.setEasingCurve(spring)
    else:
        animation.setDuration(180)
        animation.setEasingCurve(QEasingCurve.OutCubic)

    def step(value):
        box.setProperty(_TICK, float(value))
        box.update()

    def done():
        box.setProperty(_TICK, None)
        box.update()
    animation.valueChanged.connect(step)
    animation.finished.connect(done)
    _keep(animation)
    animation.start()


def _holds_video(widget: QWidget) -> bool:
    """Whether a widget is, or holds, video or OpenGL, which graphics effects break."""
    kinds = ('QOpenGLWidget', 'QVideoWidget', 'QQuickWidget')
    return any(w.inherits(kind) for w in (widget, *widget.findChildren(QWidget)) for kind in kinds)


class _Rise(QGraphicsEffect):
    """A part of a window coming in: drawn faint, and a little below where it is, nearer and
    clearer as step goes from 0 to 1. Only drawn so: the part itself never moves, so its layout
    and the pointer's aim are left alone."""

    def __init__(self, parent, by: int):
        super().__init__(parent)
        self.step = 0.0
        self.by = by

    def boundingRectFor(self, rect):
        return rect.adjusted(0, 0, 0, self.by)

    def draw(self, painter):
        if self.step >= 1.0:
            self.drawSource(painter)
            return
        pixmap = self.sourcePixmap(Qt.LogicalCoordinates, QPoint(), QGraphicsEffect.NoPad)
        painter.save()
        painter.setOpacity(painter.opacity() * self.step)
        painter.drawPixmap(QPointF(0, self.by * (1.0 - self.step)), pixmap)
        painter.restore()


_STAGGER_GAP = 45       # ms between one part's rising and the next's
_STAGGER_MOST = 8       # parts after this many come in with the last


def stagger_in(widgets, by: int = 10, ms: int = 300) -> None:
    """Parts of a window coming in one after another: each fades in as it rises the last few
    pixels into place. Not when animations are off, nor for a part with video or an effect of
    its own."""
    if not motion_on():
        return
    for index, widget in enumerate(widgets):
        if widget.graphicsEffect() is not None or _holds_video(widget):
            continue
        effect = _Rise(widget, by)
        widget.setGraphicsEffect(effect)
        rise = QVariantAnimation(widget)
        rise.setDuration(ms)
        rise.setStartValue(0.0)
        rise.setEndValue(1.0)
        rise.setEasingCurve(QEasingCurve.OutCubic)

        def step(value, e=effect):
            e.step = float(value)
            e.update()

        def done(w=widget, e=effect):
            try:
                if w.graphicsEffect() is e:
                    w.setGraphicsEffect(None)
            except RuntimeError:                    # gone meanwhile
                pass
        rise.valueChanged.connect(step)
        rise.finished.connect(done)
        sequence = QSequentialAnimationGroup(widget)
        sequence.addPause(_STAGGER_GAP * min(index, _STAGGER_MOST))
        sequence.addAnimation(rise)
        _keep(sequence)
        sequence.start()


def _sections(root: QWidget) -> list:
    """The panels a page is made of (QFrame#section), as they show."""
    return [w for w in root.findChildren(QFrame, 'section') if w.isVisibleTo(root)][:12]


def _settle_in(window: QWidget) -> None:
    """A framed window opening: its sections - or else the parts it is made of - rise into place
    one after another."""
    if not motion_on():
        return
    root = window.centralWidget() if isinstance(window, QMainWindow) else window
    if root is None:
        return
    parts = _sections(root) or [w for w in root.findChildren(QWidget, options=Qt.FindDirectChildrenOnly)
                                if w.isVisibleTo(window) and not w.isWindow()
                                and not isinstance(w, (QStatusBar, QMenuBar, QToolBar))]
    if 0 < len(parts) <= 12:
        stagger_in(parts)


def fade_out(window: QWidget, then=None, ms: int = 160) -> QPropertyAnimation | None:
    """Close a frameless window softly: it fades away, then then() (else it hides). At once when
    animations are off. Returns the fading, which stopping (as the window is wanted again) leaves
    the window up - its opacity is then for the caller to put back - or None."""
    finish = then or window.hide
    if getattr(window, '_suite_closing', False):
        return None
    if not motion_on() or not window.isVisible():
        finish()
        return None
    window._suite_closing = True
    fade = QPropertyAnimation(window, b'windowOpacity', window)
    fade.setDuration(ms)
    fade.setStartValue(window.windowOpacity())
    fade.setEndValue(0.0)
    fade.setEasingCurve(QEasingCurve.InCubic)

    def done():
        window._suite_closing = False
        finish()
        try:
            window.setWindowOpacity(1.0)            # ready to be shown again
        except RuntimeError:
            pass
    fade.finished.connect(done)
    _keep(fade)
    fade.start()
    return fade


def _glide_progress(bar: QProgressBar) -> None:
    """A progress bar's fill glides to each new value rather than jumping - forward, that is: one
    going back, or busy (no range), or hidden, goes at once. Meanwhile value() says where the fill
    is, on its way."""
    state = {'shown': bar.value(), 'own': False, 'moving': None}

    def put(value: int) -> None:
        state['own'] = True
        bar.setValue(value)
        state['own'] = False
        state['shown'] = value

    def changed(value: int) -> None:
        if state['own']:
            return
        moving = state['moving']
        if moving is not None:
            moving.stop()
        start = state['shown']
        if not motion_on() or not bar.isVisible() or bar.maximum() <= bar.minimum() or value <= start \
                or start < bar.minimum():
            state['shown'] = value
            return
        put(start)
        glide = QVariantAnimation(bar)
        glide.setDuration(320)
        glide.setStartValue(float(start))
        glide.setEndValue(float(value))
        glide.setEasingCurve(QEasingCurve.OutCubic)
        glide.valueChanged.connect(lambda v: put(int(round(v))))
        state['moving'] = glide
        _keep(glide)
        glide.start()
    bar.valueChanged.connect(changed)

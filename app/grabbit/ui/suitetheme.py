"""suitetheme: the suite's look in a Qt app - its pink, check boxes, colours, fonts and cards,
its frosted glass and its animations.

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

Frosted glass: every window of the suite is a pane of glass - whatever is
behind it shows through, blurred - as Windows 11's own are. An app calls
frost(window) once, before the window is first shown. A framed window then
takes Windows' Acrylic; a frameless one (a card, a menu, a toast), which
Windows leaves solid, takes the older acrylic blur behind, tinted, with
Windows' own rounded corners and shadow - so it paints no shadow of its own
(glass_on() says which). colors() turns see-through to match, and
paint_glass() draws a pane on it: a veil, light along its top edge and a
bright rim. Glass needs Windows 11 22H2 and Windows' transparency effects on
(Settings > Personalisation > Colours); without them everything stays solid,
as before. frost() also brings the suite's animations: frameless windows fade
in, a window's stacked pages fade in as they change, and check boxes tick
smoothly.

Two looks, chosen in Hanami for the whole suite (look(), set_look()): frosted,
as above, and liquid glass, after Apple's - clearer, and bending what is
behind a pop-up near its edges, as a lens does. Windows has no such material,
so a liquid pop-up draws its glass from the screen behind it (see the liquid
glass section).
"""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from dataclasses import dataclass, replace

from PySide6.QtCore import (QEasingCurve, QEvent, QObject, QPointF, QPropertyAnimation, QRectF, Qt, QTimer,
                            QVariantAnimation)
from PySide6.QtGui import (QBrush, QColor, QFont, QGuiApplication, QLinearGradient, QPainter, QPainterPath, QPalette,
                           QPen, QPixmap, QRadialGradient)
from PySide6.QtWidgets import (QApplication, QCheckBox, QGraphicsOpacityEffect, QProxyStyle, QStackedWidget, QStyle,
                               QWidget)

__all__ = ['ACCENT', 'ACCENT_HOVER', 'ON_ACCENT', 'ROSE', 'SuiteStyle', 'install', 'Colors', 'is_dark', 'veil',
           'colors', 'ink', 'font', 'paint_shadow', 'card_style', 'GLASS_RADIUS', 'glass_on', 'frost',
           'paint_glass', 'css', 'fade_in', 'LOOKS', 'look', 'set_look']

ACCENT = '#ffd1dc'         # the suite's blossom pink: buttons, ticks, highlights
ACCENT_HOVER = '#ffe0e8'
ON_ACCENT = '#a61e4d'      # text and ticks drawn on ACCENT, which is too pale for white
ROSE = '#c2255c'           # the mark on the icons: the pink as text or a line on white
GLASS_RADIUS = 8           # the corners Windows 11 rounds a frameless glass window to


class SuiteStyle(QProxyStyle):
    """Windows' own style, with check boxes drawn in the suite's pink.

    Windows 11's style loses a ticked box's fill once a style sheet is in
    play, leaving a bare tick that looks cut off. Drawing the box here keeps
    it whole, in any mode, whatever the accent colour of the day. A box that
    frost() wired up ticks smoothly: its fill and tick follow _TICK, which
    its animation moves from 0 to 1.
    """

    SIZE = 18

    def pixelMetric(self, metric, option=None, widget=None):
        if metric in (QStyle.PM_IndicatorWidth, QStyle.PM_IndicatorHeight):
            return self.SIZE
        return super().pixelMetric(metric, option, widget)

    def drawPrimitive(self, element, option, painter, widget=None):
        if element != QStyle.PE_IndicatorCheckBox:
            super().drawPrimitive(element, option, painter, widget)
            return
        c = colors()
        state = option.state
        ticked = bool(state & QStyle.State_On)
        partly = bool(state & QStyle.State_NoChange)
        step = widget.property(_TICK) if isinstance(widget, QCheckBox) else None
        step = float(step) if step is not None else (1.0 if ticked or partly else 0.0)
        side = min(option.rect.width(), option.rect.height(), self.SIZE) - 1
        box = QRectF(0, 0, side, side)
        box.moveCenter(QRectF(option.rect).center())
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        if not state & QStyle.State_Enabled:
            painter.setOpacity(0.45)
        hover = state & QStyle.State_MouseOver and state & QStyle.State_Enabled
        painter.setPen(QPen(c.edge if hover or step > 0.5 else c.dim, 1))
        painter.setBrush(c.chip)
        painter.drawRoundedRect(box, side * 0.22, side * 0.22)
        if step > 0:
            # The pink fills in from the middle, then the tick draws itself.
            fill = QColor(ACCENT)
            grow = 0.55 + 0.45 * min(1.0, step * 1.6)
            inner = QRectF(0, 0, side * grow, side * grow)
            inner.moveCenter(box.center())
            painter.setPen(QPen(c.edge, 1) if grow >= 1 else Qt.NoPen)
            painter.setBrush(fill)
            painter.setOpacity(painter.opacity() * min(1.0, step * 2))
            painter.drawRoundedRect(inner, inner.width() * 0.22, inner.width() * 0.22)
            painter.setOpacity(1.0 if state & QStyle.State_Enabled else 0.45)
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
        painter.restore()


def _part_of(path: QPainterPath, share: float) -> QPainterPath:
    """The first share of a path's length, for a tick drawing itself."""
    out = QPainterPath(path.pointAtPercent(0))
    steps = 24
    for i in range(1, steps + 1):
        t = share * i / steps
        out.lineTo(path.pointAtPercent(path.percentAtLength(path.length() * t)))
    return out


_style = None
_look_watch = None


def install(app) -> None:
    """Give the app the suite's style, right after making the QApplication.

    Kept here so Python never frees it under Qt. Frosted windows follow
    Windows between light and dark from here on too.
    """
    global _style, _look_watch
    _style = SuiteStyle()
    app.setStyle(_style)
    app.styleHints().colorSchemeChanged.connect(lambda *_: _refrost_all())
    # The look is chosen in Hanami, for the whole suite: each app looks for a change now and then.
    _look_watch = QTimer(app)
    _look_watch.setInterval(2000)
    _look_watch.timeout.connect(_look_changed)
    _look_watch.start()


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
    whatever is behind the window - and a little clearer in the liquid look."""
    if look() == 'liquid':
        return QColor(24, 22, 30, 150) if is_dark() else QColor(250, 247, 249, 150)
    return QColor(26, 24, 31, 190) if is_dark() else QColor(250, 247, 249, 190)


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
    """A pane of glass on a frosted window: a veil (fill, else the card colour), light along its
    top edge and a bright rim, brightest where the light falls - glass catching the light. In the
    liquid look, a frameless window's pane is drawn from what is behind it (see _liquid)."""
    window = painter.device()
    lens = getattr(window, '_suite_lens', None) if look() == 'liquid' else None
    if lens is not None:
        _paint_liquid(painter, rect, radius, lens)
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
    """Stylesheet for the inputs and buttons that sit on a card: glassy, lit from above."""
    c = colors()
    text, dim, chip, border = c.text.name(), c.dim.name(), css(c.chip), css(c.border)
    edge = c.edge.name()
    dark = is_dark()
    shine = css(QColor(255, 255, 255, 26 if dark else 170))
    rim = css(QColor(255, 255, 255, 34 if dark else 200))
    return f"""
        QLabel {{ color: {text}; background: transparent; }}
        QLabel[role="dim"] {{ color: {dim}; }}
        QLabel[role="error"] {{ color: {c.error.name()}; }}
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
        QProgressBar {{ background: {chip}; border: none; border-radius: 3px; max-height: 6px; }}
        QProgressBar::chunk {{ background: {edge}; border-radius: 3px; }}
        QToolButton {{ color: {dim}; background: transparent; border: 1px solid transparent;
                       border-radius: 8px; padding: 0px 8px 2px 8px; }}
        QToolButton:hover {{ color: {text}; }}
        QToolButton:hover {{ border: 1px solid {edge}; }}
        QToolButton::menu-indicator {{ image: none; width: 0; }}
        QCheckBox {{ color: {text}; background: transparent; spacing: 8px; }}
        QListView {{ background: transparent; border: none; outline: none; }}
        QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
        QScrollBar::handle:vertical {{ background: {border}; border-radius: 3px; min-height: 24px; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    """


# --------------------------------------------------------------------------- frosted glass

_glass: bool | None = None
_TICK = '_suite_tick'          # a check box's step through its animation, 0 to 1
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


# --------------------------------------------------------------------------- looks

LOOKS = {'frosted': 'Frosted', 'liquid': 'Liquid glass'}
_LOOK_KEY = r'Software\blossqt\Suite'
_look: str | None = None


def look() -> str:
    """The suite's glass, as chosen in Hanami: 'frosted' (Windows' Acrylic) or 'liquid' (clearer,
    with what is behind a pop-up bent at its edges, as through a lens)."""
    global _look
    if _look is None:
        _look = _read_look()
    return _look


def _read_look() -> str:
    if os.environ.get('SUITE_LOOK') in LOOKS:             # tests and screenshots
        return os.environ['SUITE_LOOK']
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _LOOK_KEY) as key:
            value = str(winreg.QueryValueEx(key, 'Look')[0])
    except OSError:
        return 'frosted'
    return value if value in LOOKS else 'frosted'


def set_look(name: str) -> None:
    """Choose the suite's glass (Hanami's window does). Every suite app running takes it within a
    couple of seconds, without starting again."""
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _LOOK_KEY) as key:
        winreg.SetValueEx(key, 'Look', 0, winreg.REG_SZ, name if name in LOOKS else 'frosted')
    _look_changed()


def _look_changed() -> None:
    global _look
    now = _read_look()
    if now != _look:
        _look = now
        _refrost_all()


class _Margins(ctypes.Structure):
    _fields_ = [('left', ctypes.c_int), ('right', ctypes.c_int), ('top', ctypes.c_int), ('bottom', ctypes.c_int)]


class _Accent(ctypes.Structure):
    _fields_ = [('state', ctypes.c_int), ('flags', ctypes.c_int), ('color', ctypes.c_uint), ('animation', ctypes.c_int)]


class _CompositionData(ctypes.Structure):
    _fields_ = [('attribute', ctypes.c_int), ('data', ctypes.c_void_p), ('size', ctypes.c_size_t)]


_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWA_SYSTEMBACKDROP_TYPE = 38
_DWMWCP_ROUND, _DWMSBT_TRANSIENTWINDOW = 2, 3          # rounded corners; Acrylic
_WCA_ACCENT_POLICY, _ACCENT_ENABLE_ACRYLICBLURBEHIND = 19, 4


def _dwm(hwnd: int, attribute: int, value: int) -> None:
    v = ctypes.c_int(value)
    ctypes.windll.dwmapi.DwmSetWindowAttribute(wintypes.HWND(hwnd), attribute, ctypes.byref(v), ctypes.sizeof(v))


def _frameless(window: QWidget) -> bool:
    return bool(window.windowFlags() & Qt.FramelessWindowHint)


def _tint() -> int:
    """The frameless glass's tint, as Windows takes it (alpha, blue, green, red): a deep plum on
    dark, a pinkish white on light."""
    color, alpha = (QColor('#221e27'), 0xA6) if is_dark() else (QColor('#fbf5f8'), 0xB4)
    return (alpha << 24) | (color.blue() << 16) | (color.green() << 8) | color.red()


def _apply_glass(window: QWidget) -> None:
    hwnd = int(window.winId())
    _dwm(hwnd, _DWMWA_USE_IMMERSIVE_DARK_MODE, 1 if is_dark() else 0)
    if _frameless(window):
        # Windows' Acrylic stays solid on frameless windows; the older blur behind does not. The
        # liquid look draws its glass itself (_capture), so Windows adds none.
        _dwm(hwnd, _DWMWA_WINDOW_CORNER_PREFERENCE, _DWMWCP_ROUND)
        accent = _Accent(0 if look() == 'liquid' else _ACCENT_ENABLE_ACRYLICBLURBEHIND, 2, _tint(), 0)
        data = _CompositionData(_WCA_ACCENT_POLICY, ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p),
                                ctypes.sizeof(accent))
        ctypes.windll.user32.SetWindowCompositionAttribute(wintypes.HWND(hwnd), ctypes.byref(data))
    else:
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


def frost(window: QWidget, fade: bool = True) -> None:
    """Make a window frosted glass, with the suite's animations - call before it is first shown.
    A frameless window then paints itself with paint_glass() and no shadow when glass_on(), and
    fades in as it is shown - unless not fade, for one that sees to its own opacity."""
    window.installEventFilter(_frosting())
    window.setProperty(_FROSTED, True)
    window.setProperty('_suite_fade', fade)
    if glass_on():
        window.setAttribute(Qt.WA_TranslucentBackground)
        if not _frameless(window):
            window.setPalette(_glass_palette(window.palette()))


def _refrost_all() -> None:
    """Windows went light or dark, or the look changed: the frosted windows take the new tint,
    title bar, palette and glass."""
    if not glass_on():
        return
    for window in QApplication.topLevelWidgets():
        if window.property(_FROSTED) and window.isVisible():
            if not _frameless(window):
                window.setPalette(_glass_palette(window.palette()))
            elif _liquid(window):
                _recapture(window)
            _apply_glass(window)
            window.update()


class _Frosting(QObject):
    """Watches the frosted windows: glass as each is shown, then its animations - and, under a
    framed one's contents, the veil, which Qt leaves unpainted on a see-through window. A liquid
    pop-up's glass is captured as it opens, and again when it moves or changes size."""

    def eventFilter(self, obj, event):
        kind = event.type()
        if kind == QEvent.Paint and glass_on() and isinstance(obj, QWidget) and obj.isWindow() \
                and not _frameless(obj):
            painter = QPainter(obj)
            painter.fillRect(event.rect(), veil())
            painter.end()
        elif kind == QEvent.Show and isinstance(obj, QWidget) and obj.isWindow():
            if _liquid(obj):
                _capture(obj)                       # not on screen yet: what is behind it, as it is
            if glass_on():
                _apply_glass(obj)
            _animate_parts(obj)
            if _frameless(obj) and obj.property('_suite_fade'):
                fade_in(obj, window=True)
        elif kind in (QEvent.Move, QEvent.Resize) and isinstance(obj, QWidget) and obj.isWindow() \
                and obj.isVisible() and _liquid(obj):
            _recapture(obj)
        return False


_frosting_filter = None


def _frosting() -> _Frosting:
    global _frosting_filter
    if _frosting_filter is None:
        _frosting_filter = _Frosting()
    return _frosting_filter


# --------------------------------------------------------------------------- liquid glass
#
# Windows' own materials only frost. Liquid glass, as Apple's, is clearer and bends what is behind
# it near its edges, as a lens does - so a frameless window draws it: the screen behind it, taken
# just before it opens (or, once it is up, with Windows leaving the window itself out for a
# moment), lightly blurred, and squeezed towards the rim so that what lies just beyond each edge
# shows inside it. Framed windows keep Windows' Acrylic, a little clearer.

_WDA_NONE, _WDA_EXCLUDEFROMCAPTURE = 0, 0x11
_LENS_BAND = 24           # how far in from the edges the glass bends what is behind it
_LENS_STEPS = 8
_LENS_BLUR = 7            # how much the glass softens what is behind it


def _liquid(window: QWidget) -> bool:
    return glass_on() and look() == 'liquid' and _frameless(window)


def _capture(window: QWidget) -> None:
    """Take what is behind a frameless window, a little beyond its edges, and make its liquid glass
    from it (kept on the window, for paint_glass)."""
    g = window.frameGeometry()
    screen = QGuiApplication.screenAt(g.center()) or window.screen() or QGuiApplication.primaryScreen()
    origin = screen.geometry().topLeft()
    m = _LENS_BAND
    shot = screen.grabWindow(0, g.x() - origin.x() - m, g.y() - origin.y() - m, g.width() + 2 * m,
                             g.height() + 2 * m)
    window._suite_lens = None if shot.isNull() else _lens(shot, g.width(), g.height())
    window.update()


def _recapture(window: QWidget) -> None:
    """Take what is behind a window already on screen: Windows leaves it out of the capture for a
    moment. One at a time, so a window being dragged isn't taken at every step."""
    if getattr(window, '_suite_capturing', False):
        return
    window._suite_capturing = True
    hwnd = wintypes.HWND(int(window.winId()))
    ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, _WDA_EXCLUDEFROMCAPTURE)

    def take():
        try:
            if window.isVisible():
                _capture(window)
        finally:
            ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, _WDA_NONE)
            window._suite_capturing = False
    QTimer.singleShot(70, window, take)


def _lens(shot: QPixmap, width: int, height: int) -> QPixmap:
    """The glass for a window width x height from a shot of what is behind it (with _LENS_BAND
    more on each side): softened, flat in the middle, and gathering more and more of what lies
    beyond each edge towards the rim."""
    dpr = shot.devicePixelRatio()
    m = _LENS_BAND
    small = shot.scaled(max(1, shot.width() // _LENS_BLUR), max(1, shot.height() // _LENS_BLUR),
                        Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    soft = small.scaled(shot.width(), shot.height(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    out = QPixmap(round(width * dpr), round(height * dpr))
    out.setDevicePixelRatio(dpr)
    out.fill(Qt.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    full = QRectF(0, 0, width, height)
    for i in range(_LENS_STEPS):
        t = i / (_LENS_STEPS - 1)                   # 0 at the rim, 1 where the glass is flat
        reach = m * (1 - t) ** 1.5                  # how far beyond this ring it gathers light from
        inset = m * t
        ring = QPainterPath()
        ring.addRoundedRect(full.adjusted(inset, inset, -inset, -inset), max(2.0, GLASS_RADIUS * 2 - inset),
                            max(2.0, GLASS_RADIUS * 2 - inset))
        painter.setClipPath(ring)
        source = QRectF((m - reach) * dpr, (m - reach) * dpr, (width + 2 * reach) * dpr, (height + 2 * reach) * dpr)
        painter.drawPixmap(full, soft, source)
    painter.end()
    return out


def _paint_liquid(painter, rect, radius: float, lens: QPixmap) -> None:
    """Liquid glass: what is behind, through the lens, under a light veil for reading, a glow where
    the light falls, and rims that catch it - bright at the top left and bottom right."""
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
    rim = QLinearGradient(r.topLeft(), r.bottomRight())
    rim.setColorAt(0.0, QColor(255, 255, 255, 235))
    rim.setColorAt(0.3, QColor(255, 255, 255, 60))
    rim.setColorAt(0.7, QColor(255, 255, 255, 40))
    rim.setColorAt(1.0, QColor(255, 255, 255, 190))
    painter.setPen(QPen(QBrush(rim), 1.6))
    painter.setBrush(Qt.NoBrush)
    painter.drawPath(path)
    inner = QPainterPath()
    inner.addRoundedRect(r.adjusted(2.5, 2.5, -2.5, -2.5), max(1.0, radius - 2.5), max(1.0, radius - 2.5))
    shine = QLinearGradient(r.topLeft(), r.bottomLeft())
    shine.setColorAt(0.0, QColor(255, 255, 255, 110))
    shine.setColorAt(0.2, QColor(255, 255, 255, 0))
    shine.setColorAt(0.85, QColor(255, 255, 255, 0))
    shine.setColorAt(1.0, QColor(255, 255, 255, 70))
    painter.setPen(QPen(QBrush(shine), 1))
    painter.drawPath(inner)
    painter.restore()


# --------------------------------------------------------------------------- animations

_running: set = set()          # animations under way, kept from Python's collector until they finish


def _keep(animation) -> None:
    _running.add(animation)
    animation.finished.connect(lambda: _running.discard(animation))


def fade_in(widget: QWidget, ms: int = 170, window: bool = False) -> None:
    """Fade a widget in: a window by its opacity, a page inside one by an opacity effect (left
    alone if it holds video or OpenGL, which such effects break)."""
    if window:
        widget.setWindowOpacity(0.0)
        animation = QPropertyAnimation(widget, b'windowOpacity', widget)
    else:
        if widget.graphicsEffect() is not None or any(
                'Video' in type(w).__name__ or 'GL' in type(w).__name__ for w in widget.findChildren(QWidget)):
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


def _animate_parts(window: QWidget) -> None:
    """Once per window: its stacked pages fade in as they change, its check boxes tick smoothly."""
    for stack in window.findChildren(QStackedWidget):
        if not stack.property('_suite_fades'):
            stack.setProperty('_suite_fades', True)
            stack.currentChanged.connect(lambda _, s=stack: s.isVisible() and s.currentWidget() is not None
                                         and fade_in(s.currentWidget()))
    for box in window.findChildren(QCheckBox):
        if not box.property('_suite_ticks'):
            box.setProperty('_suite_ticks', True)
            box.toggled.connect(lambda on, b=box: _tick(b, on))


def _tick(box: QCheckBox, on: bool) -> None:
    if not box.isVisible():
        return
    animation = QVariantAnimation(box)
    animation.setDuration(180)
    start = box.property(_TICK)
    animation.setStartValue(float(start) if start is not None else (0.0 if on else 1.0))
    animation.setEndValue(1.0 if on else 0.0)
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

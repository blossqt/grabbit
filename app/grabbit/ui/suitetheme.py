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

The glass is frosted: clear glass that softens what is behind it, and gathers
a little of what lies beyond a window's edges in at its rim. (A second look,
Liquid glass, was removed on 2026-11-20 at the user's wish; look() is always
'frosted'.)

frost() also brings the suite's animations, unless they are off (motion_on()):
pop-ups fade in, and out again when closed with fade_out(); a window's parts
rise into place one after another as it opens (stagger_in), and a stacked
page's as it comes up; progress bars glide to each new value;
and check boxes tick (switches spring across) smoothly - all moving on once
a frame, at the fastest screen's refresh rate (frame_interval). A style sheet
made from this module's colours should be set with restyle(), so it follows
the look and Windows' light or dark as they change.
"""

from __future__ import annotations

import ctypes
import re
import itertools
import json
import math
import os
import queue
import sys
import threading
import time
import weakref
from ctypes import wintypes
from dataclasses import dataclass, replace

from PySide6.QtCore import (QAbstractAnimation, QEasingCurve, QEvent, QObject, QPoint, QPointF, QPropertyAnimation,
                            QRect, QRectF, QSize, Qt, QSequentialAnimationGroup, QTimer, QVariantAnimation, Signal)
from PySide6.QtGui import (QBrush, QColor, QCursor, QFont, QFontDatabase, QGuiApplication, QImage, QLinearGradient,
                           QFontMetricsF, QPainter, QPainterPath, QPalette, QPen, QPixmap, QRegion,
                           QTextLayout)
from PySide6.QtWidgets import (QAbstractItemView, QAbstractScrollArea, QApplication, QCheckBox,
                               QDialog, QFrame, QGraphicsEffect, QLabel,
                               QGraphicsOpacityEffect, QLineEdit, QMainWindow, QMenuBar, QProgressBar, QProxyStyle,
                               QPushButton, QStackedWidget, QStatusBar, QStyle, QToolBar, QToolButton, QVBoxLayout, QWidget)

__all__ = ['ACCENT', 'ACCENT_HOVER', 'ON_ACCENT', 'ROSE', 'SuiteStyle', 'install', 'Colors', 'is_dark', 'veil',
           'colors', 'ink', 'font', 'serif', 'paint_shadow', 'card_style', 'panel_css', 'panel_fill', 'GLASS_RADIUS',
           'glass_on', 'frost', 'paint_glass', 'css', 'fade_in', 'LOOKS', 'look',
           'glass_clear', 'set_glass_clear', 'CLEAR_DEFAULT',
           'motion_on', 'motion_chosen', 'set_motion', 'MODES', 'mode', 'set_mode', 'app_mode', 'restyle',
           'on_restyle', 'light_up', 'BACKDROP_OPACITY', 'fade_out', 'stagger_in', 'GAME_DOWNLOADS',
           'game_quiet_chosen', 'set_game_quiet', 'game_downloads', 'set_game_downloads', 'game_in_front',
           'quiet_now', 'on_game', 'prepare_glass', 'frame_interval', 'animate', 'stop_animation', 'leave', 'walled', 'closes_on_escape', 'ESCAPE_CLOSES', 'TYPE', 'SPACE', 'QUICK', 'STEADY', 'GENTLE', 'text', 'Label', 'Pages', 'Rolling']

ACCENT = '#ffd1dc'         # the suite's blossom pink: buttons, ticks, highlights
ACCENT_HOVER = '#ffe0e8'
ON_ACCENT = '#a61e4d'      # text and ticks drawn on ACCENT, which is too pale for white
ROSE = '#c2255c'           # the mark on the icons: the pink as text or a line on white
# The suite's scale (2026-11-01): every window takes its text sizes, gaps and timings from these,
# so the apps feel like one. Text in points; title and page are Geist Serif (serif()).
# Since 2026-11-02 its lower steps are Windows 11's (caption 12 px, body 14 px) and steps are a
# clear 1-2 px apart: 8.5 pt notes under 9.5 pt labels were too small and read as a mistake.
# small (13 px) is a line about something: a setting's description, a card's.
# heading (15 px) is a section's title, in Segoe UI Semibold.
TYPE = {'tiny': 8, 'caption': 9, 'small': 9.75, 'body': 10.5, 'strong': 10.5,
        'heading': 11.25, 'title': 17, 'page': 21}
SPACE = (4, 8, 12, 16, 24, 32)     # px: gaps and margins are these (a 1-2 px nudge aside)
QUICK, STEADY, GENTLE = 90, 140, 180     # ms: a press or fade, a slide, a panel or toast coming in

GLASS_RADIUS = 8           # the corners Windows 11 rounds a frameless glass window to


_INDICATOR_WIDTH, _INDICATOR_HEIGHT = QStyle.PM_IndicatorWidth, QStyle.PM_IndicatorHeight


class SuiteStyle(QProxyStyle):
    """Windows' own style, with check boxes drawn in the suite's pink (as switches where asked).

    Windows 11's style loses a ticked box's fill once a style sheet is in
    play, leaving a bare tick that looks cut off. Drawing the box here keeps
    it whole, in any mode, whatever the accent colour of the day. A box that
    frost() wired up ticks smoothly (a switch springs across): its fill and
    tick follow _TICK, which its animation moves from 0 to 1.
    """

    SIZE = 18
    SWITCH = (34, 20)

    def pixelMetric(self, metric, option=None, widget=None):
        # Asked hundreds of times a frame (a list's header and rows), so the two it answers itself
        # are told apart by identity, not by == on enums, which Python spends far longer over.
        if metric is _INDICATOR_WIDTH:
            return self.SWITCH[0] if _switches(widget) else self.SIZE
        if metric is _INDICATOR_HEIGHT:
            return self.SWITCH[1] if _switches(widget) else self.SIZE
        return super().pixelMetric(metric, option, widget)

    def drawPrimitive(self, element, option, painter, widget=None):
        if element == QStyle.PE_FrameFocusRect:     # no focus outlines, dotted or otherwise (the user's wish)
            return
        if element != QStyle.PE_IndicatorCheckBox:
            super().drawPrimitive(element, option, painter, widget)
            return
        state = option.state
        partly = bool(state & QStyle.State_NoChange)
        step = widget.property(_TICK) if isinstance(widget, QCheckBox) else None
        moving = step is not None
        if not moving:
            step = 1.0 if state & QStyle.State_On or partly else 0.0
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        if not state & QStyle.State_Enabled:
            painter.setOpacity(0.45)
        if _switches(widget):
            self._pill_switch(option, painter, float(step))
        else:
            self._box(option, painter, float(step), partly)
        painter.restore()

    def _pill_switch(self, option, painter, step):
        """A switch in the frosted look (21st.dev's HeroUI switch): a soft track that turns pink,
        and a wide white knob that slides across it - wider still while it is held down."""
        c = colors()
        state = option.state
        width, height = self.SWITCH
        rect = QRectF(option.rect)
        track = QRectF(rect.x() + 0.5, rect.center().y() - height / 2 + 0.5, width - 1, height - 1)
        bend = track.height() / 2
        on = min(1.0, max(0.0, step))
        hover = state & QStyle.State_MouseOver and state & QStyle.State_Enabled
        painter.setPen(QPen(c.edge, 1) if hover else Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, 34) if is_dark() else QColor(0, 0, 0, 22))
        painter.drawRoundedRect(track, bend, bend)
        if on > 0:
            base = painter.opacity()
            painter.setOpacity(base * on)
            painter.setPen(QPen(c.edge, 1))
            painter.setBrush(QColor(ACCENT))
            painter.drawRoundedRect(track, bend, bend)
            painter.setOpacity(base)
        size = track.height() - 4
        knob_width = size * 1.15 + (3 if state & QStyle.State_Sunken else 0)
        room = track.width() - 4 - knob_width
        knob = QRectF(track.x() + 2 + room * step, track.center().y() - size / 2, knob_width, size)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 28))
        painter.drawRoundedRect(knob.translated(0, 1), size / 2, size / 2)
        painter.setBrush(QColor(255, 255, 255))
        painter.drawRoundedRect(knob, size / 2, size / 2)

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

SWITCH = '_suite_switch'        # a check box's property: drawn as a switch (a setting's on/off)


def _switches(widget) -> bool:
    """Whether a check box is drawn as a switch: those marked SWITCH (settings)."""
    return isinstance(widget, QCheckBox) and bool(widget.property(SWITCH))


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
_wall_watch = None


def install(app) -> None:
    """Give the app the suite's style, right after making the QApplication.

    Kept here so Python never frees it under Qt. The suite's light or dark is
    taken now; Windows going light or dark, and the look, the animations or
    light or dark changing in Hanami, are followed from here on.
    """
    global _style, _watch, _wall_watch
    _style = SuiteStyle()
    app.setStyle(_style)
    # One family for every widget (2026-11-02): Windows gives buttons, boxes and menus its own
    # Segoe UI, beside the Segoe UI Variable of font() - two fonts side by side. At its own size.
    app.setFont(font(app.font().pointSizeF() if app.font().pointSizeF() > 0 else 9))
    _apply_mode(starting=True)
    app.styleHints().colorSchemeChanged.connect(lambda *_: _suite_changed())
    # The look is chosen in Hanami, for the whole suite: each app looks for a change now and then.
    _watch = QTimer(app)
    _watch.setInterval(2000)
    _watch.timeout.connect(_look_changed)
    _watch.start()
    _honour_timers()
    _frame_rate()
    app.installEventFilter(_wheels())
    _ui_effects()
    on_restyle(_ui_effects)
    app.screenAdded.connect(_screen_added)
    app.screenRemoved.connect(lambda *_: _frame_rate())
    for screen in app.screens():
        screen.refreshRateChanged.connect(lambda *_: _frame_rate())
    QTimer.singleShot(_WARM_AFTER, _warm_up)
    _wall_watch = QTimer(app)
    _wall_watch.setInterval(60_000)
    _wall_watch.timeout.connect(_look_at_wall)
    _wall_watch.start()
    QTimer.singleShot(_WARM_AFTER + 500, _look_at_wall)


def leave(code: int) -> int:
    """End the app's process once its event loop is over: settings waiting to be written are
    written, output flushed, and then the process ends there, before Python and PySide take Qt,
    COM and Windows' text readers apart. That teardown hung Hanami 0.1.49 with a core busy, and
    crashed Dropshift 0.1.27 inside Windows' OCR module (2026-11-01). TerminateProcess, not
    os._exit: that runs the DLLs' own teardown, and crashed with Qt still alive."""
    app = QApplication.instance()
    if app is not None:
        app.processEvents()                 # QSettings write themselves on a posted event
    for stream in (sys.stdout, sys.stderr):
        try:
            stream and stream.flush()
        except (OSError, ValueError):
            pass
    kernel32 = ctypes.windll.kernel32
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.TerminateProcess(ctypes.c_void_p(kernel32.GetCurrentProcess()), code)
    return code


_WARM_AFTER = 3000      # ms after an app starts: what Qt sets up for the first window it draws, done unseen


def _warm_up() -> None:
    """Draw a hidden window of the suite's parts once, soon after the app starts: Qt reads its fonts,
    style sheets and glyphs the first time it draws them, which made each app's first window open
    about 0.15 s slower than the next. Not while a game is in front: once it has gone."""
    if quiet_now():
        QTimer.singleShot(_WARM_AFTER, _warm_up)
        return
    sample = QWidget()
    sample.setAttribute(Qt.WA_DontShowOnScreen)
    layout = QVBoxLayout(sample)
    title = QLabel('Aa')
    title.setFont(serif(TYPE['page']))
    layout.addWidget(title)
    for role in ('caption', 'body', 'strong'):
        line = QLabel('Aa 0123')
        line.setFont(text(role))
        layout.addWidget(line)
    layout.addWidget(QPushButton('Aa'))
    layout.addWidget(QLineEdit('Aa'))
    layout.addWidget(QCheckBox('Aa'))
    restyle(sample, card_style)
    sample.grab()
    sample.deleteLater()


# Qt moves its animations on every 16 ms, 60 times a second, whatever the monitor - on a faster one
# they step where they should glide. Its animation timer (QUnifiedTimer, which PySide doesn't wrap,
# so it is reached in Qt's own DLL) is set to tick at least once a frame on the fastest screen, and
# set again when screens come, go or change their rate. Should that fail, they stay at 60.

_FRAME_CALLS = ('?instance@QUnifiedTimer@@SAPEAV1@XZ', '?setTimingInterval@QUnifiedTimer@@QEAAXH@Z')


def frame_interval() -> int:
    """How often, in whole ms, an animation moves on: at least once a frame on the fastest screen."""
    rate = max((screen.refreshRate() for screen in QGuiApplication.screens()), default=60.0)
    return max(1, int(1000 / max(rate, 1.0)))


def _frame_rate() -> None:
    try:
        kernel = ctypes.windll.kernel32
        kernel.GetModuleHandleW.restype = ctypes.c_void_p
        kernel.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        handle = kernel.GetModuleHandleW('Qt6Core.dll')
        if not handle:
            return
        core = ctypes.WinDLL('Qt6Core.dll', handle=handle)
        timer, set_interval = (getattr(core, name) for name in _FRAME_CALLS)
    except (AttributeError, OSError):
        return
    timer.restype, timer.argtypes = ctypes.c_void_p, []
    set_interval.restype, set_interval.argtypes = None, [ctypes.c_void_p, ctypes.c_int]
    unified = timer()                       # this thread's: the windows' own
    if unified:
        set_interval(unified, frame_interval())


class _Throttling(ctypes.Structure):
    _fields_ = [('version', wintypes.ULONG), ('control', wintypes.ULONG), ('state', wintypes.ULONG)]


_PROCESS_POWER_THROTTLING, _IGNORE_TIMER_RESOLUTION = 4, 0x4


def _honour_timers() -> None:
    """Windows 11 stops honouring a process's timer resolution while none of its windows can be
    seen - an app sat in the tray - and its fast timers then tick every 15.6 ms: a pop-up's
    animations ran at 64 frames a second until Windows saw the pop-up. Opted out of that, they
    keep to the frame from the first. (Timers that fine run only while something animates.)"""
    try:
        kernel = ctypes.WinDLL('kernel32')
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.SetProcessInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        state = _Throttling(1, _IGNORE_TIMER_RESOLUTION, 0)     # the choice is the app's: always honoured
        kernel.SetProcessInformation(kernel.GetCurrentProcess(), _PROCESS_POWER_THROTTLING, ctypes.byref(state),
                                     ctypes.sizeof(state))
    except (AttributeError, OSError):
        pass


def _screen_added(screen) -> None:
    screen.refreshRateChanged.connect(lambda *_: _frame_rate())
    _frame_rate()


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
    """The suite's colours for the mode Windows is in - see-through where they sit on glass. Kept
    for each: a window being resized asks for them thousands of times a second. They are shared,
    so copy one (QColor(c.text)) before changing it."""
    key = (is_dark(), glass_on())
    kept = _colors_kept.get(key)
    if kept is None:
        kept = _colors_kept[key] = _colors(*key)
    return kept


_colors_kept: dict = {}


def _colors(dark: bool, glass: bool) -> Colors:
    if dark:
        solid = Colors(card=QColor('#202127'), border=QColor(255, 255, 255, 30), text=QColor('#ececf1'),
                       dim=QColor('#9a9aa6'), chip=QColor('#2c2d35'),
                       selected=QColor(ACCENT), selected_text=QColor(ON_ACCENT),
                       shadow=QColor(0, 0, 0, 120), error=QColor('#ff8a80'),
                       edge=QColor(ACCENT), tile=QColor('#2b2a31'))
        if glass:
            return replace(solid, card=QColor(24, 22, 28, 70), chip=QColor(255, 255, 255, 22),
                           tile=QColor(255, 255, 255, 16), border=QColor(255, 255, 255, 40),
                           dim=QColor('#a9a8b4'))
        return solid
    solid = Colors(card=QColor('#ffffff'), border=QColor(0, 0, 0, 28), text=QColor('#1d1d22'),
                   dim=QColor('#6e6e7a'), chip=QColor('#f1f2f6'),
                   selected=QColor(ACCENT), selected_text=QColor(ON_ACCENT),
                   shadow=QColor(20, 24, 40, 70), error=QColor('#c62828'),
                   edge=QColor('#ee9fb5'), tile=QColor('#fff6f8'))
    if glass:
        return replace(solid, card=QColor(255, 255, 255, 36), chip=QColor(255, 255, 255, 64),
                       tile=QColor(255, 250, 252, 44), border=QColor(0, 0, 0, 24), dim=QColor('#5f5f6b'))
    return solid


def veil() -> QColor:
    """A framed glass window's own tint over Windows' Acrylic: enough that text stays easy to read
    whatever is behind the window."""
    # after canvasui.dev's cards: a plainer, lighter tint, so more of the blur shows
    return walled(QColor(10, 10, 12, _clear_scaled(112)) if is_dark() else QColor(250, 250, 252, _clear_scaled(64)))


WALL_SHARE = 0.5          # how far the glass's tint leans to the wallpaper's hue, at its own lightness
_wall: QColor | None = None
_wall_seen: tuple = ()


def walled(color: QColor) -> QColor:
    """A glass tint leaning faintly to the wallpaper's colour (2026-11-01, the user's wish), so the
    glass changes with the day's wallpaper; as it is when the wallpaper isn't known."""
    if _wall is None:
        return color
    hue, saturation, _, _ = _wall.getHslF()
    lightness = color.lightnessF()
    lightness = min(lightness, 0.94) if lightness > 0.5 else max(lightness, 0.10)
    leaning = QColor.fromHslF(max(hue, 0.0), min(saturation, 0.5), lightness)
    mixed = QColor(round(color.red() + (leaning.red() - color.red()) * WALL_SHARE),
                   round(color.green() + (leaning.green() - color.green()) * WALL_SHARE),
                   round(color.blue() + (leaning.blue() - color.blue()) * WALL_SHARE))
    mixed.setAlpha(color.alpha())
    return mixed


def _wallpaper() -> str:
    """The desktop picture's file: Wallpaper Engine's picture of its wallpaper, when it has one
    chosen (it draws the desktop itself, and Windows' own setting stays the default picture -
    as on this PC), else Windows' ('' for a plain colour)."""
    engine = _engine_wallpaper()
    if engine:
        return engine
    buffer = ctypes.create_unicode_buffer(520)
    try:
        ctypes.windll.user32.SystemParametersInfoW(0x0073, len(buffer), buffer, 0)    # SPI_GETDESKWALLPAPER
    except (AttributeError, OSError):
        return ''
    return buffer.value


def _engine_wallpaper() -> str:
    """The preview picture of the wallpaper Wallpaper Engine shows on the first screen, from its
    config.json in Steam's folder; '' without it."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam') as key:
            steam = winreg.QueryValueEx(key, 'SteamPath')[0]
        with open(os.path.join(steam, 'steamapps', 'common', 'wallpaper_engine', 'config.json'),
                  encoding='utf-8') as file:
            config = json.load(file)
    except (OSError, ValueError):
        return ''
    for user in config.values() if isinstance(config, dict) else ():
        general = user.get('general') if isinstance(user, dict) else None
        chosen = ((general or {}).get('wallpaperconfig') or {}).get('selectedwallpapers') or {}
        for monitor in sorted(chosen):
            folder = os.path.dirname(str((chosen[monitor] or {}).get('file', '')))
            for name in ('preview.jpg', 'preview.png', 'preview.gif'):
                if folder and os.path.isfile(os.path.join(folder, name)):
                    return os.path.join(folder, name)
    return ''


def _look_at_wall() -> None:
    """Take the wallpaper's colour again if its picture changed: every minute, never while a game
    is in front, and only on real screens. A change repaints the glass."""
    global _wall, _wall_seen
    if QGuiApplication.platformName() != 'windows' or os.environ.get('SUITE_WALL') == '0' or quiet_now():
        return
    path = _wallpaper()
    try:
        seen = (path, os.path.getmtime(path)) if path else ('', 0)
    except OSError:
        seen = (path, 0)
    if seen == _wall_seen:
        return
    _wall_seen = seen
    color = None
    if path:
        picture = QImage(path)
        if not picture.isNull():
            color = QColor(picture.scaled(1, 1, Qt.IgnoreAspectRatio, Qt.SmoothTransformation).pixel(0, 0))
    else:
        bgr = ctypes.windll.user32.GetSysColor(1)        # COLOR_DESKTOP, a plain colour
        color = QColor(bgr & 0xFF, (bgr >> 8) & 0xFF, (bgr >> 16) & 0xFF)
    if color != _wall:
        _wall = color
        _suite_changed()


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
    _figures(f)
    return f


def _figures(f: QFont) -> None:
    """Every figure one width (OpenType tnum): a speed, a count or a code changing doesn't jitter.
    Segoe UI Variable's 1 is narrower than its other figures otherwise."""
    try:
        f.setFeature(QFont.Tag('tnum'), 1)
    except (AttributeError, TypeError):
        pass                    # a Qt before 6.7


SERIF_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'GeistSerif-Regular.ttf')
_serif: list[str] = []          # the family's name, once loaded ('' if the file is missing)




def _figure(text: str) -> float:
    found = re.search(r'-?\d+(?:\.\d+)?', text)
    return float(found.group()) if found else 0.0


class Rolling:
    """Figures that roll to a new value, like an odometer's digits (after Bencho's Rolling counter,
    2026-11-02): each character that changes slides out as its new one slides in - up when the
    value rises, down when it falls - and those that stay the same stay still. For a widget that
    paints its own text: set() the new text, paint() it right-aligned in a rectangle. Figures are
    one width (tnum), so the characters keep their places. Without animations, it simply changes."""

    def __init__(self, widget: QWidget, text: str = ''):
        self.widget, self.text, self.old = widget, text, text
        self.t, self.up = 1.0, True
        self._roll: QVariantAnimation | None = None

    def set(self, text: str) -> None:
        if text == self.text:
            return
        self.old, self.text = self.text, text
        self.up = _figure(text) >= _figure(self.old)
        stop_animation(self._roll)
        if not motion_on() or not self.widget.isVisible() or not self.old:
            self.t = 1.0
            self.widget.update()
            return
        roll = self._roll = QVariantAnimation(self.widget)
        roll.setDuration(STEADY)
        roll.setEasingCurve(QEasingCurve.OutCubic)
        roll.setStartValue(0.0)
        roll.setEndValue(1.0)
        roll.valueChanged.connect(lambda v: (setattr(self, 't', float(v)), self.widget.update()))
        self.t = 0.0
        animate(roll)

    def paint(self, painter: QPainter, rect: QRectF) -> None:
        """The text right-aligned in rect, centred in its height, mid-roll if it is rolling."""
        m = QFontMetricsF(painter.font())
        rolling = self.t < 1.0 and self.old != self.text
        n = max(len(self.old), len(self.text)) if rolling else len(self.text)
        new, old = self.text.rjust(n), (self.old.rjust(n) if rolling else self.text.rjust(n))
        travel = max(rect.height(), m.height())      # a whole line: out of sight at either end
        away = -1 if self.up else 1                 # rising: the old one goes up, the new comes from below
        painter.save()
        painter.setClipRect(rect, Qt.IntersectClip)
        x = rect.right()
        for a, b in zip(reversed(old), reversed(new)):
            width = max(m.horizontalAdvance(a), m.horizontalAdvance(b))
            x -= width
            slot = QRectF(x, rect.top(), width, rect.height())
            if a == b or not rolling:
                painter.drawText(slot, Qt.AlignRight | Qt.AlignVCenter, b)
                continue
            for char, shift, alpha in ((a, away * travel * self.t, 1 - self.t),
                                       (b, -away * travel * (1 - self.t), self.t)):
                if char.strip():
                    painter.setOpacity(alpha)
                    painter.drawText(slot.translated(0, shift), Qt.AlignRight | Qt.AlignVCenter, char)
            painter.setOpacity(1.0)
        painter.restore()


class Label(QLabel):
    """A label whose box is its ink (CSS's text-box-trim, 2026-11-02): from the top of its first
    line's letters - capitals, or the x-height of a line with none - to the bottom of its last
    line's. A font's box holds 3-8 px of nothing above and below them, so a gap set in a layout
    showed as anything from 12 to 20; with this it is the gap the eye sees, and spacing can be
    even (the user: "split all empty space evenly").

    Rich text (links, bold) is laid out and drawn by QLabel as ever - its links still answer - and
    cropped through negative contents margins (_fit_rich). Pictures are drawn whole."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._rich_margins = (0, 0)
        self._fit_rich()

    def _rich(self) -> bool:
        text = self.text()
        return self.textFormat() == Qt.RichText or (self.textFormat() == Qt.AutoText and '<' in text and '>' in text)

    def _ink(self, text: str, width: int | None) -> tuple[int, int]:
        """The room above the first line's letters and under the last line's, in this font, less a
        pixel each: at 125% or 150% the letters are drawn on the screen's own pixels and stand up to
        a pixel taller than measured, which cut the tops off "for" and "itself" on the user's laptop."""
        m = QFontMetricsF(self.font(), self)
        paragraphs = [p for p in text.replace('\u2028', '\n').replace('\u2029', '\n').split('\n')] or ['']
        lines = [paragraphs[0], paragraphs[-1]]
        if self.wordWrap() and width:
            wrapped = []
            # one paragraph (most labels) is wrapped once, not once for its first line and again
            # for its last: a resizing window asks every wrapped label at each new width
            for paragraph in ((paragraphs[0],) if len(paragraphs) == 1 else (paragraphs[0], paragraphs[-1])):
                layout = QTextLayout(paragraph, self.font())
                layout.beginLayout()
                parts = []
                while True:
                    line = layout.createLine()
                    if not line.isValid():
                        break
                    line.setLineWidth(width)
                    parts.append(paragraph[line.textStart():line.textStart() + line.textLength()])
                layout.endLayout()
                wrapped.append(parts or [''])
            lines = [wrapped[0][0], wrapped[-1][-1]]
        first = m.tightBoundingRect(lines[0].strip() or 'x')
        last = m.tightBoundingRect(lines[-1].strip() or 'x')
        return (max(0, math.floor(m.ascent() + first.top()) - 1),
                max(0, math.floor(m.descent() - last.bottom()) - 1))

    def _fit_rich(self) -> None:
        """Rich text's crop, as negative contents margins (QLabel lays it out within them)."""
        if not self._rich() or not self.text():
            margins = (0, 0)
        else:
            from PySide6.QtGui import QTextDocument
            document = QTextDocument()
            document.setHtml(self.text())
            width = self.width() if self.wordWrap() and self.width() > 1 else None
            margins = self._ink(document.toPlainText(), width)
        if margins != self._rich_margins:
            m = self.contentsMargins()
            old_top, old_bottom = self._rich_margins
            self._rich_margins = margins
            self.setContentsMargins(m.left(), m.top() + old_top - margins[0], m.right(),
                                    m.bottom() + old_bottom - margins[1])

    def setText(self, text: str) -> None:
        super().setText(text)
        self._fit_rich()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.wordWrap() and event.size().width() != event.oldSize().width():
            self._fit_rich()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.FontChange:
            self._fit_rich()

    def _trims(self, width: int | None = None) -> tuple[int, int]:
        text = self.text()
        if not text or self._rich() or not self.pixmap().isNull():
            return 0, 0
        key = (text, self.font().key(), width if self.wordWrap() else None, self.devicePixelRatioF())
        kept = self.__dict__.setdefault('_trims_kept', {})      # a layout asks a few widths in turn
        found = kept.get(key)
        if found is None:
            if len(kept) > 48:
                kept.clear()
            found = kept[key] = self._ink(text, width)
        return found

    def _width(self) -> int | None:
        return self.contentsRect().width() if self.wordWrap() and self.width() > 1 else None

    def sizeHint(self):
        size = super().sizeHint()
        top, bottom = self._trims(self._width())
        return QSize(size.width(), size.height() - top - bottom)

    def minimumSizeHint(self):
        size = super().minimumSizeHint()
        top, bottom = self._trims(self._width())
        return QSize(size.width(), max(0, size.height() - top - bottom))

    def heightForWidth(self, width: int) -> int:
        height = super().heightForWidth(width)
        if height < 0:
            return height
        m = self.contentsMargins()
        top, bottom = self._trims(width - m.left() - m.right())
        return height - top - bottom

    def paintEvent(self, event):
        top, bottom = self._trims(self._width())
        if not (top or bottom):
            return super().paintEvent(event)
        painter = QPainter(self)
        room = self.contentsRect()
        ink = self.heightForWidth(self.width()) if self.wordWrap() else self.sizeHint().height()
        m = self.contentsMargins()
        spare = room.height() - (ink - m.top() - m.bottom())     # a cell taller than its words
        down = spare // 2 if self.alignment() & Qt.AlignVCenter else spare if self.alignment() & Qt.AlignBottom else 0
        room = room.adjusted(0, down - top, 0, bottom)
        flags = int(self.alignment() & ~Qt.AlignVertical_Mask) | int(Qt.AlignTop)
        if self.wordWrap():
            flags |= int(Qt.TextWordWrap)
        self.style().drawItemText(painter, room, flags, self.palette(), self.isEnabled(), self.text(),
                                  self.foregroundRole())
        painter.end()


class Pages(QStackedWidget):
    """A stack of pages whose height for a width is the page in sight's. A QStackedWidget asks
    every page, hidden or not, and Qt asks the stack's layout itself, past any override of the
    stack's, so each page is given one of its own: hidden, it has none. Before, a window being
    resized laid out the words of every page frame after frame - 200 labels a frame on Hanami's
    eight settings pages, near half of each frame (2026-11-02, the user: resizing was "very
    laggy")."""

    def __init__(self, *args):
        super().__init__(*args)
        self.currentChanged.connect(lambda _: self.updateGeometry())

    def addWidget(self, widget: QWidget) -> int:
        _in_sight_only(self, widget)
        return super().addWidget(widget)

    def insertWidget(self, index: int, widget: QWidget) -> int:
        _in_sight_only(self, widget)
        return super().insertWidget(index, widget)


def _in_sight_only(stack: QStackedWidget, page: QWidget) -> None:
    """A page of a stack answers for its height only while it is the one in sight (-1 otherwise,
    which the stack's layout passes over). Set on the page itself: Qt calls it from C++."""
    stack_ref, page_ref, own = weakref.ref(stack), weakref.ref(page), type(page).heightForWidth

    def height_for_width(width: int) -> int:
        s, p = stack_ref(), page_ref()
        if p is None or (s is not None and s.currentWidget() is not p):
            return -1
        return own(p, width)
    page.heightForWidth = height_for_width


def text(role: str, weight: QFont.Weight = QFont.Normal) -> QFont:
    """A font from the suite's scale: TYPE's role, in Geist Serif for title and page. A heading
    is semibold unless asked otherwise."""
    if role in ('title', 'page'):
        return serif(TYPE[role])
    if role == 'heading' and weight == QFont.Normal:
        weight = QFont.DemiBold
    return font(TYPE[role], weight)


def serif(point_size: float) -> QFont:
    """Vercel's Geist Serif (SIL Open Font License, beside this file), for headings.

    It has one weight, so a heading in it stands out by size alone. Without the file it is
    font()'s Segoe UI at the weight a heading had."""
    if not _serif:
        loaded = QFontDatabase.addApplicationFont(SERIF_FILE) if os.path.isfile(SERIF_FILE) else -1
        families = QFontDatabase.applicationFontFamilies(loaded) if loaded >= 0 else []
        _serif.append(families[0] if families else '')
    if not _serif[0]:
        return font(point_size, QFont.DemiBold)
    f = QFont(_serif[0])
    f.setPointSizeF(point_size)
    f.setWeight(QFont.Normal)
    _figures(f)
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
    screen"). Should that fail, a veil (fill, else the card colour) over Windows' blur, light
    along its top edge and a bright rim."""
    window = painter.device()
    lens = _lens_for(window) if isinstance(window, QWidget) and _screen_glass(window) else None
    if lens is not None:
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
    """Stylesheet for the inputs and buttons that sit on a card: glassy, lit from above. Set it with
    restyle(), so it follows light or dark."""
    c = colors()
    text, dim, chip, border = c.text.name(), c.dim.name(), css(c.chip), css(c.border)
    edge = c.edge.name()
    dark = is_dark()
    shine = css(QColor(255, 255, 255, 26 if dark else 170))
    # 21st.dev's Texture Button: a darker ring, a bright line along the top, and a face shading
    # down from light - lifted, as if it could be pressed in.
    ring, ring_low = (css(QColor(255, 255, 255, 30)), css(QColor(0, 0, 0, 90))) if dark else \
        (css(QColor(0, 0, 0, 34)), css(QColor(0, 0, 0, 58)))
    face_top, face_low = (css(QColor(255, 255, 255, 30)), css(QColor(255, 255, 255, 10))) if dark else \
        (css(QColor(255, 255, 255, 150)), css(QColor(236, 236, 240, 90)))
    controls = f"""
        QLineEdit {{
            color: {text}; background: {chip}; border: 1px solid {border};
            border-radius: 8px; padding: 7px 10px; selection-background-color: {ACCENT};
            selection-color: {ON_ACCENT};
        }}
        QLineEdit:focus {{ border: 1px solid {edge}; }}
        QPushButton {{
            color: {text}; border: 1px solid {ring}; border-bottom-color: {ring_low};
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {shine}, stop:0.08 {face_top},
                                        stop:1 {face_low});
            border-radius: 10px; padding: 6px 16px;
        }}
        QPushButton:hover {{
            border-color: {edge};
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {shine}, stop:0.08 {face_low},
                                        stop:1 {face_top});
        }}
        QPushButton:pressed {{ background: {face_low}; }}
        QPushButton[role="primary"] {{
            color: {ON_ACCENT}; border: 1px solid {edge}; border-bottom-color: {ROSE}; font-weight: 600;
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #fff8fa, stop:0.08 #ffe4eb, stop:1 #ffc2d1);
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


def panel_fill() -> QColor:
    """The solid ground panel_css puts under what is read, as one colour: for panes that run edge
    to edge (a vault's sidebar and list) rather than floating as a rounded panel."""
    if not glass_on():
        return QColor(colors().tile)
    return QColor(16, 15, 20, 175) if is_dark() else QColor(255, 255, 255, 190)


def panel_css(selector: str, radius: int = 12) -> str:
    """Style sheet for a panel sitting on a window (an app's card, a box of settings): on glass,
    canvasui.dev's cards - a plain, mostly solid tint, a hairline and rounder corners - so what is
    read sits on solid ground (2026-11-02, the user: "a huge problem is readability")."""
    c = colors()
    if glass_on():
        fill, line = (QColor(16, 15, 20, 175), QColor(255, 255, 255, 26)) if is_dark() else \
            (QColor(255, 255, 255, 190), QColor(0, 0, 0, 20))
        return (f'{selector} {{ background: {css(fill)}; border: 1px solid {css(line)}; '
                f'border-radius: {radius + 6}px; }}')
    return (f'{selector} {{ background: {css(c.tile)}; border: 1px solid {css(c.border)}; '
            f'border-radius: {radius}px; }}')


# --------------------------------------------------------------------------- glass

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


# --------------------------------------------------------------------------- the suite's choices

LOOKS = {'frosted': 'Frosted'}
MODES = {'system': 'Follow Windows', 'light': 'Light', 'dark': 'Dark'}
_LOOK_KEY = r'Software\blossqt\Suite'
_motion: bool | None = None
_mode: str | None = None
_app_mode = 'system'


def look() -> str:
    """The suite's glass: always 'frosted' (Liquid glass was removed on 2026-11-20)."""
    return 'frosted'


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


CLEAR_DEFAULT = 50          # the glass's transparency as it was before it could be set
_clear_value: int | None = None


def glass_clear() -> int:
    """How see-through the frosted glass is, 0 to 100, as chosen in Hanami: at 50 its tint is as it
    has always been; higher thins it, to none at 100; lower thickens it, to twice at 0."""
    global _clear_value
    if _clear_value is None:
        _clear_value = _read_clear()
    return _clear_value


def _read_clear() -> int:
    value = os.environ.get('SUITE_GLASS_CLEAR')            # tests and screenshots
    if value is None:
        value = _suite_value('GlassClear')
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return CLEAR_DEFAULT


def set_glass_clear(value: int) -> None:
    """Set the glass's transparency (Hanami's settings do, as its slider moves): this app's glass
    windows are drawn again at once, and every other app takes it within seconds. Only the glass
    is drawn again - not every style sheet - so dragging the slider stays smooth."""
    global _clear_value
    import winreg
    _clear_value = max(0, min(100, int(value)))
    _set_suite_value('GlassClear', winreg.REG_DWORD, _clear_value)
    app = QApplication.instance()
    for window in app.topLevelWidgets() if app is not None else ():
        if window.isVisible() and window.property(_FROSTED):
            window.update()


READABLE_SHARE = 0.8      # how much of the tint text behind brings stays, however clear the glass is chosen


def _clear_scaled(base: float, lift: float = 0.0, most: float = 255.0) -> int:
    """A glass tint (0 to 255) as thick as the transparency chosen makes it: base, the tint over a
    calm backdrop, thinned or thickened in step with it; lift, what text or detail behind adds so
    the window's own words stay readable, thinned only to READABLE_SHARE of itself (at 85% the user
    once saw the text behind a window read as clearly as its own). Up to most, unless chosen
    thicker than it has always been."""
    share = (100 - glass_clear()) / (100 - CLEAR_DEFAULT)      # 1 as it has always been, 0 fully clear
    if share >= 1:
        return int(min(255.0, min(most, base + lift) * share))
    return int(max(0.0, min(most, base * share + lift * max(share, READABLE_SHARE))))


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
    global _motion, _mode, _clear_value
    now = (_read_motion(), _read_mode(), _read_clear())
    if now != (_motion, _mode, _clear_value):
        mode_changed = now[1] != _mode
        _motion, _mode, _clear_value = now
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
        if kind == QEvent.KeyPress and event.key() == Qt.Key_Escape and not event.modifiers() \
                and obj.property(ESCAPE_CLOSES) and not isinstance(obj, QDialog):
            obj.close()                 # a dialog does this itself; a window says it should (closes_on_escape)
            return True
        if kind == QEvent.Paint and not _frameless(obj):
            if glass_on():
                painter = QPainter(obj)
                painter.setClipRegion(event.region())    # the parts asked for: a rim repaint is its band, not the whole window
                _paint_bridge(painter, obj)
                painter.fillRect(event.rect(), veil())      # frosted: Windows' blur under a plain tint, no rim
                painter.end()
            if getattr(obj, '_suite_backdrop', None):
                return _paint_backdrop(obj, event)
        elif kind == QEvent.Show:
            _place(obj)
            _stop_unpainted(obj)
            if _screen_glass(obj):
                _capture(obj)                       # not on screen yet: what is behind it, as it is
                _watch_around(obj, start=True)
            if glass_on() and not _frameless(obj):
                _bridge(obj)
            if glass_on():
                _apply_glass(obj)
            _animate_parts(obj)
            if _frameless(obj) and obj.property('_suite_fade'):
                if _screen_glass(obj) and motion_on() and _unpainted_behind(obj):
                    _wait_for_paint(obj)            # the program behind hasn't drawn its first frame yet
                else:
                    obj.setWindowOpacity(1.0)
                    _open(obj)
            elif not _frameless(obj):
                _settle_in(obj)
        elif kind == QEvent.Hide:
            _stop_unpainted(obj)
            _remember_place(obj)
            _stop_live(obj)
            _watch_around(obj, stop=True)
            _let_go(obj)
        elif kind in (QEvent.Move, QEvent.Resize) and obj.isVisible() and _screen_glass(obj):
            gliding = getattr(obj, '_suite_gliding', False)
            if not gliding and not _waiting_for_paint(obj):
                _follow(obj)
            # A gliding window keeps the glass it had, drawn from its own corner, so a move alone
            # changes nothing in it: repainting it whole each frame of the glide only made frames late.
            if _frameless(obj) and not (gliding and kind == QEvent.Move):
                obj.update()
        elif kind == QEvent.ChildAdded and obj.isVisible():
            QTimer.singleShot(0, obj, lambda: _animate_parts(obj))
        return False


_HELD_PICTURES = ('_suite_shot', '_suite_clean', '_suite_lens', '_suite_bridge', '_suite_backdrop_pixmap')


def _let_go(window: QWidget) -> None:
    """A window put away keeps none of its glass's pictures: the screen behind it (with its margins,
    and a clean copy), its cut glass, its opening bridge and its scaled backdrop - a few MB a window,
    held by every hidden settings window, card and panel. Each is taken or made again as it shows."""
    for name in _HELD_PICTURES:
        if getattr(window, name, None) is not None:
            setattr(window, name, None)
    window._suite_lens_key = None
    window._suite_backdrop_key = None


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
    kept = getattr(window, '_suite_backdrop_pixmap', None)
    now = time.perf_counter()
    if getattr(window, '_suite_backdrop_key', None) != key and kept is not None \
            and now - getattr(window, '_suite_backdrop_at', -math.inf) < _BACKDROP_SETTLE:
        # Being resized: the last picture stretched to fit - at a tenth, nobody sees it - and
        # scaled again once the size settles. Scaling it took 1.7 ms of every frame.
        window._suite_backdrop_at = now
        _backdrop_later(window)
        painter = QPainter(window)
        painter.setClipRegion(event.region())
        painter.setOpacity(BACKDROP_OPACITY)
        kw, kh = kept.width() / kept.devicePixelRatio(), kept.height() / kept.devicePixelRatio()
        if abs(kw - window.width()) <= 48 and abs(kh - window.height()) <= 48:
            painter.drawPixmap(0, 0, kept)          # a little off: as it is (stretching took 1.7 ms)
        else:
            painter.drawPixmap(QRectF(window.rect()), kept, QRectF(kept.rect()))
        painter.end()
        return True
    if getattr(window, '_suite_backdrop_key', None) != key:
        window._suite_backdrop_at = now
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
    painter.setClipRegion(event.region())
    painter.setOpacity(BACKDROP_OPACITY)
    painter.drawPixmap(0, 0, window._suite_backdrop_pixmap)
    painter.end()
    return True


_BACKDROP_SETTLE = 0.15    # s: a backdrop is scaled to a window's new size once it has kept it this long


def _backdrop_later(window: QWidget) -> None:
    timer = getattr(window, '_suite_backdrop_timer', None)
    if timer is None:
        timer = window._suite_backdrop_timer = QTimer(window)
        timer.setSingleShot(True)
        timer.setInterval(int(_BACKDROP_SETTLE * 1000) + 20)
        ref = weakref.ref(window)
        timer.timeout.connect(lambda: (w := ref()) is not None and w.update())
    timer.start()


_frosting_filter = None


ESCAPE_CLOSES = '_suite_escape'
_PLACED = '_suite_placed'
_PLACES_KEY = _LOOK_KEY + r'\Windows'


def closes_on_escape(window: QWidget) -> None:
    """Esc closes this (frosted) window, as it does a dialog: for a pop-up or a side window, never
    a main window or one holding unsaved work."""
    window.setProperty(ESCAPE_CLOSES, True)


def _place_name(window: QWidget) -> str | None:
    """Under what name a framed window's size and place are kept, or None for one that isn't: a
    pop-up, one that places itself (property _suite_place False), or with no real screens."""
    if _frameless(window) or window.property('_suite_place') is False or os.environ.get('SUITE_PLACES') == '0' \
            or QGuiApplication.platformName() != 'windows':
        return None
    return f'{QGuiApplication.applicationName() or "app"}.{window.objectName() or type(window).__name__}'


def _place(window: QWidget) -> None:
    """A framed window opens where it was last, at the size it had - once, as it is first shown, and
    only if that place is still on a screen."""
    name = _place_name(window)
    if name is None or window.property(_PLACED):
        return
    window.setProperty(_PLACED, True)
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _PLACES_KEY) as key:
            saved = bytes(winreg.QueryValueEx(key, name)[0])
    except OSError:
        return
    before = window.geometry()
    if window.restoreGeometry(saved) and not any(s.availableGeometry().intersects(window.frameGeometry())
                                                  for s in QGuiApplication.screens()):
        window.setGeometry(before)      # its screen is gone


def _remember_place(window: QWidget) -> None:
    name = _place_name(window)
    if name is None or not window.property(_PLACED):
        return
    try:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _PLACES_KEY) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_BINARY, bytes(window.saveGeometry()))
    except OSError:
        pass


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
# and again, the glass made from the last shot, shifted, in between; and a still window looks now
# and then at what is around it, and takes the screen again when that changed. Those shots catch
# the window itself, so where it is (and its shadow) is filled in from its last shot, which had
# none of it (_clean): the first was taken before it was on screen. Windows can leave a window out
# of shots itself (SetWindowDisplayAffinity), but is never asked to: while any window is left
# out, desktop recorders (NVIDIA's) refuse to record at all. So what is right behind a still
# window is as it was when the window last moved off it.
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

_MARGIN = 56              # how far beyond a window's edges its shot of the screen reaches
_MARGIN_FRAMELESS = 160   # a pop-up's, further: one dragged fast stays within its last shot until the next comes
# canvasui.dev's frosted cards (the user's wish, 2026-11-01): what is behind, flat and well blurred
# (its backdrop-filter: blur(12px))
_BLUR = 12
_LIVE_MS = 33             # how often a moving window takes the screen behind it again
_SETTLE = 0.35            # and for how long after it stops
_SHADOW = 24              # px around a pop-up that Windows' shadow for it can darken in a shot
_WATCH_MS = 600           # how often a still window looks at what is around it


def _screen_glass(window: QWidget) -> bool:
    """Whether a window's glass is made from the screen: a frameless one's, and a framed one's rim,
    which bends in what lies beyond its edges."""
    return glass_on() and bool(window.property(_FROSTED))


def _where(window: QWidget, frame: QRect | None = None):
    """Where a window's shot of the screen is taken from: its screen, the part of the screen wanted
    (the window - or there, frame - and a margin around it), the part of that on the screen - None
    if none is - and the margin."""
    g = window.frameGeometry() if frame is None else frame
    screen = QGuiApplication.screenAt(g.center()) or window.screen() or QGuiApplication.primaryScreen()
    margin = _MARGIN_FRAMELESS if _frameless(window) else _MARGIN
    want = g.adjusted(-margin, -margin, margin, margin)
    got = want.intersected(screen.geometry())
    return None if got.isEmpty() else (screen, got, want, margin)


_PREPARED_FOR = 0.4       # s: how long glass made ahead (prepare_glass) is good for


def prepare_glass(window: QWidget, frame: QRect, hidden: QRect | None = None) -> None:
    """Make a frameless pop-up's glass ahead, on the glass's own thread, for where it is about to
    open (frame, its frame on screen): it then opens with its glass made, not first taking the
    screen and softening it on the windows' thread as it shows. hidden: a part of the screen
    behind it that will be gone by then (the edge panel's tab), healed from around it. Glass made
    more than _PREPARED_FOR before it opens, or for somewhere else, is let go."""
    if not _screen_glass(window) or not _frameless(window) or quiet_now():
        return
    where = _where(window, frame)
    if where is None:
        return
    screen, got, want, margin = where
    native = _native(screen, got)
    asked = time.monotonic()
    ref = weakref.ref(window)

    def make(image):
        clean = _clean(image, got, want, _drag_images(native), hidden)
        return _made(clean, want, want, _BLUR, False, True, margin), clean

    def then(made):
        w = ref()
        if w is not None and made is not None:
            w._suite_prepared = (asked, want, made)
    _grabber().take(('prepare', id(window)), native, screen.devicePixelRatio(), make, then)


def _prepared(window: QWidget, want: QRect):
    """The glass made ahead for a window opening there (prepare_glass), if it is still good - let go either way."""
    prepared = getattr(window, '_suite_prepared', None)
    window._suite_prepared = None
    if prepared is None:
        return None
    asked, made_for, made = prepared
    return made if made_for == want and time.monotonic() - asked <= _PREPARED_FOR else None


def _capture(window: QWidget) -> None:
    """Take what is behind a window, a little beyond its edges, for its glass - now, as it opens
    (or made ahead: prepare_glass). Later shots are taken on the glass's own thread: _capture_later.
    None while a game is in front: the glass is then plain."""
    where = None if quiet_now() else _where(window)
    if where is None:
        return
    screen, got, want, margin = where
    made = _prepared(window, want) if _frameless(window) else None
    if made is not None:
        window._suite_box = window.frameGeometry()
        glass, clean = made
        _took(window, *glass, clean=clean, fresh=True)
        return
    area = screen.geometry()
    shot = screen.grabWindow(0, got.x() - area.x(), got.y() - area.y(), got.width(), got.height())
    if not shot.isNull():
        framed = not _frameless(window)
        window._suite_box = window.frameGeometry()
        clean = _clean(shot.toImage(), got, want, _drag_images(_native(screen, got)))
        _took(window, *_made(clean, want, want, _BLUR, framed, True, margin),
              clean=None if framed else clean)


def _heal_in(shot: QImage, origin: QPoint, rects) -> QImage:
    dpr = shot.devicePixelRatio()
    parts = []
    for r in rects:
        c = r.translated(-origin)
        parts.append(QRect(round(c.x() * dpr), round(c.y() * dpr), round(c.width() * dpr), round(c.height() * dpr)))
    return _heal(shot, parts) if parts else shot


def _clean(shot, got: QRect, want: QRect, dragged: list = (), covered: QRect | None = None,
           before: QImage | None = None, before_at: QPoint | None = None) -> QImage:
    """A shot of the screen (a QImage), on any thread, as the glass wants it: the images of anything
    being dragged taken out (dragged, in its pixels: _heal), where the window is on screen (covered,
    with its shadow) filled in from its last clean shot (before, of the screen from before_at) - or
    healed from around it when the scene behind changed meanwhile - and only then made up past any
    screen edge to the size wanted (so the window itself is never stretched beyond the edge)."""
    if dragged:
        shot = _heal(shot, dragged)
    if covered is not None:
        on_screen = covered.intersected(got)
        if not on_screen.isEmpty():
            reused = QRect()
            if before is not None and before_at is not None:
                b_dpr = before.devicePixelRatio()
                before_box = QRect(before_at.x(), before_at.y(),
                                   round(before.width() / b_dpr), round(before.height() / b_dpr))
                reused = on_screen.intersected(before_box)
            if not reused.isEmpty() and not _scene_changed(shot, got.topLeft(), before, before_at, covered, got):
                painter = QPainter(shot)
                painter.setClipRect(QRectF(reused.translated(-got.topLeft())))
                painter.drawImage(QPointF(before_at.x() - got.x(), before_at.y() - got.y()), before)
                painter.end()
                if reused != on_screen:
                    shot = _heal_in(shot, got.topLeft(), QRegion(on_screen).subtracted(QRegion(reused)))
            else:
                shot = _heal_in(shot, got.topLeft(), (on_screen,))
    if got != want:
        shot = _fill_out(shot, got, want)
    return shot


def _scene_changed(shot: QImage, shot_at: QPoint, before: QImage, before_at: QPoint, covered: QRect,
                   bounds: QRect | None = None) -> bool:
    """Whether what lies just outside covered in shot differs from before - the window behind
    repainted, scrolled or gave way to another since before was taken, so before no longer holds
    what is under the pop-up."""
    s_dpr, b_dpr = shot.devicePixelRatio(), before.devicePixelRatio()
    shot_box = bounds if bounds is not None else QRect(
        shot_at.x(), shot_at.y(), round(shot.width() / s_dpr), round(shot.height() / s_dpr))
    before_box = QRect(before_at.x(), before_at.y(), round(before.width() / b_dpr), round(before.height() / b_dpr))
    strip = _AROUND[1]
    outer = covered.adjusted(-strip, -strip, strip, strip).intersected(shot_box).intersected(before_box)
    if outer.isEmpty():
        return True
    now = _around_from(shot, shot_at, outer, covered, gap=0)
    was = _around_from(before, before_at, outer, covered, gap=0)
    if not now or not was or len(now) != len(was):
        return True
    return sum(abs(a - b) for a, b in zip(now, was)) / len(now) > 0.05


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
            span_y = max(1, round(part.height() / 20))
            across = QImage(2, span_y, QImage.Format_RGB32)
            edges = QPainter(across)
            edges.drawImage(0, 0, image.copy(xs[0], part.top(), 1, part.height()).scaled(
                1, span_y, Qt.IgnoreAspectRatio, Qt.SmoothTransformation))
            edges.drawImage(1, 0, image.copy(xs[-1], part.top(), 1, part.height()).scaled(
                1, span_y, Qt.IgnoreAspectRatio, Qt.SmoothTransformation))
            edges.end()
            blends.append(across)
        if ys:
            span_x = max(1, round(part.width() / 20))
            down = QImage(span_x, 2, QImage.Format_RGB32)
            edges = QPainter(down)
            edges.drawImage(0, 0, image.copy(part.left(), ys[0], part.width(), 1).scaled(
                span_x, 1, Qt.IgnoreAspectRatio, Qt.SmoothTransformation))
            edges.drawImage(0, 1, image.copy(part.left(), ys[-1], part.width(), 1).scaled(
                span_x, 1, Qt.IgnoreAspectRatio, Qt.SmoothTransformation))
            edges.end()
            blends.append(down)
        for n, blend in enumerate(blends):
            painter.setOpacity(1.0 / (n + 1))                               # the two, evenly
            painter.drawImage(QRectF(part), blend)
        painter.setOpacity(1.0)
    painter.end()
    image.setDevicePixelRatio(dpr)
    return image


def _took(window: QWidget, shot, at: QPoint, tone: tuple | None, clean: QImage | None = None,
          fresh: bool = False) -> None:
    """A window has a new shot for its glass (a framed one keeps it for the glass it opens over,
    _bridge). A pop-up keeps the shot as it was before it was softened
    (clean), to fill its next one in from where it is (_clean)."""
    if isinstance(shot, QImage):
        shot = QPixmap.fromImage(shot)
    window._suite_shot = shot
    window._suite_shot_at = at
    if not getattr(window, '_suite_gliding', False):
        window._suite_shot_corner = window.mapToGlobal(QPoint(0, 0))     # where it was: _lens_for
    if clean is not None:
        window._suite_clean, window._suite_clean_at = clean, at
    framed = not _frameless(window)
    if tone is not None:
        old = getattr(window, '_suite_tone', None)
        if old is not None and (_live(window) or framed) and not fresh:
            tone = tuple(0.6 * a + 0.4 * b for a, b in zip(old, tone))
        window._suite_tone = tone
    if not framed:
        window.update()                     # a framed one's veil doesn't show its shot


def _logical_window(hwnd: int, origin: QPoint, dpr: float) -> QRect | None:
    """Where hwnd sits on screen now, in Qt's logical coordinates - read on any thread."""
    if not hwnd:
        return None
    user32 = _gdi()[0]
    box = wintypes.RECT()
    if not user32.IsWindowVisible(hwnd) or not user32.GetWindowRect(hwnd, ctypes.byref(box)):
        return None
    if box.right <= box.left or box.bottom <= box.top:
        return None
    return QRect(origin.x() + math.floor((box.left - origin.x()) / dpr),
                 origin.y() + math.floor((box.top - origin.y()) / dpr),
                 math.ceil((box.right - box.left) / dpr) + 1,
                 math.ceil((box.bottom - box.top) / dpr) + 1)


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
    blur, framed = _BLUR, not _frameless(window)
    tone = final or not framed
    dpr, origin = screen.devicePixelRatio(), screen.geometry().topLeft()
    native = _native(screen, got)
    hwnd = 0 if framed else int(window.winId())
    now_box = window.frameGeometry()
    prev_box = getattr(window, '_suite_box', None) or now_box
    window._suite_box = now_box
    box = prev_box.united(now_box)
    before, before_at = getattr(window, '_suite_clean', None), getattr(window, '_suite_clean_at', None)
    ref = weakref.ref(window)

    def make(image):
        covered = None
        if not framed:
            live_box = _logical_window(hwnd, origin, dpr)
            both = box.united(live_box) if live_box is not None else box
            covered = both.adjusted(-_SHADOW, -_SHADOW, _SHADOW, _SHADOW)
        clean = _clean(image, got, want, _drag_images(native), covered, before, before_at)
        return _made(clean, want, want, blur, framed, tone, margin), None if framed else clean

    def then(made):
        w = ref()
        if w is None or not w.isVisible():
            return
        if made is not None:
            _took(w, *made[0], clean=made[1], fresh=final and not framed)
        if final and not _live(w):
            _stop_live(w)

    return _grabber().take(('shot', id(window)), native, screen.devicePixelRatio(), make, then)


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
            # Read where Windows put it, and copied once, by Qt: through a Python bytes first, the
            # copy held Python's lock 3 ms a shot, and a window being dragged waited on it.
            view = memoryview((ctypes.c_char * (width * height * 4)).from_address(bits.value)).cast('B')
            image = QImage(view, width, height, width * 4, QImage.Format_RGB32).copy()
            del view
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
    """A shot cut short by the screen's edge, made up to the size wanted: beyond each edge, what is
    right at that edge of the screen. (A QPixmap, or on any thread a QImage.)"""
    dpr = shot.devicePixelRatio()
    ww, wh = max(1, round(want.width() * dpr)), max(1, round(want.height() * dpr))
    gx, gy = round((got.x() - want.x()) * dpr), round((got.y() - want.y()) * dpr)
    gw, gh = shot.width(), shot.height()
    image = isinstance(shot, QImage)
    full = QImage(ww, wh, QImage.Format_RGB32) if image else QPixmap(ww, wh)
    full.setDevicePixelRatio(1.0)
    source = QImage(shot) if image else QPixmap(shot)
    source.setDevicePixelRatio(1.0)
    painter = QPainter(full)
    draw = painter.drawImage if image else painter.drawPixmap
    draw(QPointF(gx, gy), source)
    if gw > 0 and gh > 0:
        if gx > 0:
            draw(QRectF(0, gy, gx, gh), source, QRectF(0, 0, 1, gh))
        if gx + gw < ww:
            draw(QRectF(gx + gw, gy, ww - gx - gw, gh), source, QRectF(gw - 1, 0, 1, gh))
        painter.end()
        if gy > 0 or gy + gh < wh:
            band = full.copy(0, max(0, min(gy, wh - 1)), ww, min(gh, wh))
            painter = QPainter(full)
            if gy > 0:
                draw = painter.drawImage if image else painter.drawPixmap
                draw(QRectF(0, 0, ww, gy), band, QRectF(0, 0, ww, 1))
            if gy + gh < wh:
                draw = painter.drawImage if image else painter.drawPixmap
                draw(QRectF(0, gy + gh, ww, wh - gy - gh), band, QRectF(0, band.height() - 1, ww, 1))
            painter.end()
    else:
        painter.end()
    full.setDevicePixelRatio(dpr)
    return full


def _soften(shot, factor: int):
    """A shot blurred by about factor pixels: made that much smaller, then large again - by way of
    a size between for a strong blur, so it comes out smooth rather than in soft squares."""
    if factor <= 1:
        return shot
    small = shot.scaled(max(1, shot.width() // factor), max(1, shot.height() // factor), Qt.IgnoreAspectRatio,
                        Qt.SmoothTransformation)
    if factor >= 8:
        small = small.scaled(max(1, small.width() * 3), max(1, small.height() * 3), Qt.IgnoreAspectRatio,
                             Qt.SmoothTransformation)
        small = small.scaled(max(1, small.width() // 2), max(1, small.height() // 2), Qt.IgnoreAspectRatio,
                             Qt.SmoothTransformation)
    soft = small.scaled(shot.width(), shot.height(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    soft.setDevicePixelRatio(shot.devicePixelRatio())
    return soft


def _tone(shot, inner: QRectF) -> tuple[float, float, float, float]:
    """How bright what is behind the window is (0 to 1), how busy (how much it changes from one
    spot to the next), and how bright its darker and its brighter parts are (TONE_SPREAD in from
    either end) - so the glass's tint can keep text on it easy to read even where only part of
    it lies over a dark window, say. (Of a QPixmap, or on any thread a QImage.)"""
    dpr = shot.devicePixelRatio()
    part = shot.copy(QRect(round(inner.x() * dpr), round(inner.y() * dpr), max(1, round(inner.width() * dpr)),
                           max(1, round(inner.height() * dpr))))
    n = 32
    whole = part.toImage() if isinstance(part, QPixmap) else part

    def lum(transform) -> list:
        image = whole.scaled(n, n, Qt.IgnoreAspectRatio, transform).convertToFormat(QImage.Format_RGB32)
        raw, line = image.constBits(), image.bytesPerLine()      # blue, green, red, - for each spot
        return [[(0.0722 * raw[i] + 0.7152 * raw[i + 1] + 0.2126 * raw[i + 2]) / 255
                 for i in range(y * line, y * line + 4 * n, 4)] for y in range(n)]

    smooth = sorted(value for row in lum(Qt.SmoothTransformation) for value in row)
    mean = sum(smooth) / (n * n)
    # Busy from single pixels, not averages: averaging blurred lines of small text - the hardest
    # thing to read over - into a calm grey, and the tint stayed near 100 over a page of text.
    spots = lum(Qt.FastTransformation)
    busy = sum(abs(spots[y][x] - spots[y][x + 1]) + abs(spots[y][x] - spots[y + 1][x])
               for y in range(n - 1) for x in range(n - 1)) / ((n - 1) ** 2)
    edge = int(len(smooth) * TONE_SPREAD)
    return mean, busy, smooth[edge], smooth[-1 - edge]


TONE_SPREAD = 0.08      # the darker and brighter parts behind a window: this share in from either end


def _tone_around(shot: QPixmap, inside: QRectF) -> tuple[float, float, float, float]:
    """_tone of the strips of a shot around the window in it, as a guess at what is behind it."""
    width, height = shot.width() / shot.devicePixelRatio(), shot.height() / shot.devicePixelRatio()
    strips = [QRectF(0, 0, width, inside.top()), QRectF(0, inside.bottom(), width, height - inside.bottom()),
              QRectF(0, inside.top(), inside.left(), inside.height()),
              QRectF(inside.right(), inside.top(), width - inside.right(), inside.height())]
    parts = [(strip.width() * strip.height(), _tone(shot, strip)) for strip in strips
             if strip.width() >= 2 and strip.height() >= 2]
    total = sum(area for area, _ in parts) or 1
    return tuple(sum(area * tone[k] for area, tone in parts) / total for k in range(4))


def _lens_for(window: QWidget) -> QPixmap | None:
    """A pop-up's glass for where it is now, made again only when it moved, changed size or took a
    new shot."""
    shot = getattr(window, '_suite_shot', None)
    if shot is None:
        return None
    at = window._suite_shot_at
    # A gliding window (the edge panel sliding out) is not repainted as it moves, so its glass is cut
    # for where its shot was taken - where it comes to rest - and every part of it repainted on the
    # way (an icon, the dots) matches the rest. Cut for each place as it went, those parts showed as
    # bands of other glass across it (Hanami 0.1.90-0.1.91).
    held = getattr(window, '_suite_shot_corner', None) if getattr(window, '_suite_gliding', False) else None
    corner = held if held is not None else window.mapToGlobal(QPoint(0, 0))
    key = (window.width(), window.height(), corner.x() - at.x(), corner.y() - at.y(), shot.cacheKey())
    if getattr(window, '_suite_lens_key', None) != key:
        window._suite_lens = _lens(shot, key[2], key[3], window.width(), window.height())
        window._suite_lens_key = key
    return window._suite_lens


def _lens(shot: QPixmap, x: float, y: float, width: int, height: int) -> QPixmap:
    """Glass width x height, for a window at (x, y) in the shot: what is behind it (the caller clips
    it to the window's shape)."""
    dpr = shot.devicePixelRatio()
    room_x, room_y = shot.width() / dpr, shot.height() / dpr
    x = min(max(x, 0.0), max(0.0, room_x - width))      # kept inside the shot
    y = min(max(y, 0.0), max(0.0, room_y - height))
    out = QPixmap(max(1, round(width * dpr)), max(1, round(height * dpr)))
    out.setDevicePixelRatio(dpr)
    out.fill(Qt.transparent)
    painter = QPainter(out)
    painter.drawPixmap(QRectF(0, 0, width, height), shot, QRectF(x * dpr, y * dpr, width * dpr, height * dpr))
    painter.end()
    return out


def _live(window: QWidget) -> bool:
    timer = getattr(window, '_suite_live', None)
    return timer is not None and timer.isActive()


def _follow(window: QWidget) -> None:
    """The window moved, changed size, or what is behind it changed: take the screen behind it
    again and again until it settles."""
    window._suite_settle = time.monotonic() + _SETTLE
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
    final = time.monotonic() >= window._suite_settle
    if _capture_later(window, final) and final:
        window._suite_live.stop()


_UNPAINTED_MS, _UNPAINTED_TRIES = 60, 6


def _unpainted_behind(window: QWidget) -> bool:
    """Whether the shot behind a newly opened pop-up caught only an unpainted black surface (a
    program that gave a box the keyboard a moment before its first frame reached the screen)."""
    tone = getattr(window, '_suite_tone', None)
    return tone is not None and tone[0] < 0.015 and tone[1] < 0.002


def _waiting_for_paint(window: QWidget) -> bool:
    timer = getattr(window, '_suite_paint_wait', None)
    return timer is not None and timer.isActive()


def _stop_unpainted(window: QWidget) -> None:
    timer = getattr(window, '_suite_paint_wait', None)
    if timer is not None and timer.isActive():
        timer.stop()
        window.setWindowOpacity(1.0)


def _wait_for_paint(window: QWidget) -> None:
    """Keep a pop-up unseen for up to a third of a second while the window behind it paints its
    first frame, taking the shot again each tick, then fade it in over the real page."""
    window.setWindowOpacity(0.0)
    window._suite_paint_tries = 0
    timer = getattr(window, '_suite_paint_wait', None)
    if timer is None:
        timer = window._suite_paint_wait = QTimer(window)
        timer.setInterval(_UNPAINTED_MS)
        ref = weakref.ref(window)

        def tick() -> None:
            w = ref()
            if w is None or not w.isVisible():
                timer.stop()
                return
            w._suite_paint_tries += 1
            _capture(w)
            if not _unpainted_behind(w) or w._suite_paint_tries >= _UNPAINTED_TRIES:
                timer.stop()
                _watch_around(w, start=True)
                _apply_glass(w)
                w.setWindowOpacity(1.0)
                _open(w)
        timer.timeout.connect(tick)
    timer.start()


def _stop_live(window: QWidget) -> None:
    timer = getattr(window, '_suite_live', None)
    if timer is not None:
        timer.stop()
    window._suite_around = _window_around(window)


def _around_of(window: QWidget) -> tuple | None:
    g = window.frameGeometry()
    screen = QGuiApplication.screenAt(g.center()) or window.screen() or QGuiApplication.primaryScreen()
    gap, strip = _AROUND
    outer = g.adjusted(-gap - strip, -gap - strip, gap + strip, gap + strip).intersected(screen.geometry())
    return None if outer.isEmpty() else (screen, outer, g)


def _around_from(clean: QImage, at: QPoint, outer: QRect, g: QRect, gap: int | None = None) -> list | None:
    dpr = clean.devicePixelRatio()
    box = QRect(at.x(), at.y(), round(clean.width() / dpr), round(clean.height() / dpr))
    if not box.contains(outer):
        return None
    piece = clean.copy(QRect(round((outer.x() - at.x()) * dpr), round((outer.y() - at.y()) * dpr),
                             round(outer.width() * dpr), round(outer.height() * dpr)))
    piece.setDevicePixelRatio(dpr)
    return _around(piece, outer, g, gap=gap)


def _window_around(window: QWidget) -> list | None:
    clean, at = getattr(window, '_suite_clean', None), getattr(window, '_suite_clean_at', None)
    found = _around_of(window) if clean is not None and at is not None else None
    return _around_from(clean, at, found[1], found[2]) if found is not None else None


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
        ref = weakref.ref(window)
        if timer is None:
            timer = window._suite_watch = QTimer(window)
            timer.setInterval(_WATCH_MS)
            timer.timeout.connect(lambda: (w := ref()) is not None and _watch_around(w))
        window._suite_around = _window_around(window)
        timer.start()
        QTimer.singleShot(300, window, lambda: (w := ref()) is not None and _watch_around(w))
        return
    if not window.isVisible() or _live(window) or _waiting_for_paint(window) \
            or getattr(window, '_suite_gliding', False) or window.windowOpacity() < 0.99 \
            or not _screen_glass(window) or _button_down() or quiet_now():
        return
    found = _around_of(window)
    if found is None:
        return
    screen, outer, g = found
    ref = weakref.ref(window)

    def then(now):
        w = ref()
        if w is None or not w.isVisible() or _live(w) or getattr(w, '_suite_gliding', False):
            return
        last = getattr(w, '_suite_around', None)
        w._suite_around = now
        if now and last and len(now) == len(last) \
                and sum(abs(a - b) for a, b in zip(now, last)) / len(now) > 0.025:
            _follow(w)

    _grabber().take(('around', id(window)), _native(screen, outer), screen.devicePixelRatio(),
                    lambda shot: _around(shot, outer, g), then)


_AROUND = _SHADOW, 12   # the strips a still window watches: outside its shadow, and how wide


def _button_down() -> bool:
    """Whether a mouse button is held: something being dragged about, most likely, near the
    window - which a still window's glass leaves out, rather than chase it with shots that would
    each show it a moment late. Once the button is let go, the glass catches up."""
    keys = _gdi()[0].GetAsyncKeyState
    return any(keys(key) & 0x8000 for key in (0x01, 0x02, 0x04))      # left, right, middle


def _around(shot, outer: QRect, g: QRect, gap: int | None = None) -> list:
    """The colours of the strips of screen just around a window (g), a few of each, from a shot of
    outer - on any thread."""
    if gap is None:
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


READ_LIGHT, READ_DARK = 150 / 255, 95 / 255    # what is behind a pop-up's words: at least / at most, as Dockline's


def _frost_tint(window: QWidget) -> int:
    """How much frosted glass tints what is behind it (0 to 255): canvasui.dev's quarter (black at
    25%, on dark), and more only where text on it would be hard to read - over what is bright
    under light text, or dark under dark text, or busy."""
    tone = getattr(window, '_suite_tone', None) or (0.5, 0.05)
    mean, busy = tone[0], tone[1]
    low, high = (tone[2], tone[3]) if len(tone) > 3 else (mean, mean)
    # Enough tint to bring the darker part behind up to READ_LIGHT under dark text (the brighter part
    # down to READ_DARK under light text), whatever transparency is chosen. The old lift went by the
    # average alone, with the base tint assumed: over half a dark window and half a white page, at
    # full transparency, dark words sat on a 24% veil over the dark half (Keytap's picker, 2026-11-23).
    if is_dark():
        need = 255 * (1 - READ_DARK / high) if high > READ_DARK else 0.0
        return _clear_scaled(64, max(150 * max(0.0, mean - 0.4), need) + 400 * busy, 170)
    need = 255 * (READ_LIGHT - low) / (1 - low) if low < READ_LIGHT else 0.0
    return _clear_scaled(56, max(130 * max(0.0, 0.6 - mean), need) + 400 * busy, 160)


def _paint_frosted(painter, rect, radius: float, window: QWidget, lens: QPixmap) -> None:
    """Frosted glass, as canvasui.dev's cards are: what is behind, flat and well blurred, under a
    plain tint - dark on dark, light on light - with a hairline round its edge. No glow, and no
    rim of light."""
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
    alpha = _frost_tint(window)
    painter.fillPath(path, walled(QColor(0, 0, 0, alpha) if dark else QColor(255, 255, 255, alpha)))
    painter.setPen(QPen(QColor(255, 255, 255, 26) if dark else QColor(0, 0, 0, 22), 1))
    painter.setBrush(Qt.NoBrush)
    painter.drawPath(path)
    painter.restore()


# --------------------------------------------------------------------------- light and shape

_LIGHT = '_suite_light'


def light_up(widget: QWidget, radius: float | None = None) -> None:
    """Let a widget (a button, say) brighten as the pointer comes onto it and dim as it is pressed,
    in a moment rather than at once (_fades); radius: its corners, as its style sheet's are."""
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


class _Light(QObject):
    """A button's hover and press, faded in and out (_paint_fade), and its give as it is pressed
    (_sink)."""

    def eventFilter(self, obj, event):
        kind = event.type()
        if kind == QEvent.Paint:
            return self._paint(obj, event)
        if kind == QEvent.MouseButtonPress and _fades(obj):
            self._after_menu(obj)
        if not motion_on() or isinstance(obj, QLineEdit) or not _fades(obj):
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
            _sink(obj, True)
            self._to(obj, 'press', 1.0, 110)
        elif kind == QEvent.MouseButtonRelease:
            self._to(obj, 'press', 0.0, 480)
        return False

    def _after_menu(self, button) -> None:
        """A button whose press opens its menu (a ⋯ button) never hears its release, which the menu
        takes, nor the pointer leaving while the menu is open: it stayed pressed and lit after the
        menu closed. Now, once its menu closes, it eases back as if let go, and lets go of the
        hover unless the pointer is on it still."""
        menu = button.menu() if hasattr(button, 'menu') else None
        if menu is None or getattr(button, '_suite_menu_watched', None) is menu:
            return
        button._suite_menu_watched = menu
        ref = weakref.ref(button)

        def closed():
            b = ref()
            if b is None:
                return
            try:
                over = b.rect().contains(b.mapFromGlobal(QCursor.pos()))
                if not over:
                    b.setAttribute(Qt.WA_UnderMouse, False)     # Qt's own :hover, for its style sheet
                if motion_on() and getattr(b, _LIGHT, None):
                    self._to(b, 'press', 0.0, 480)
                    if not over:
                        self._to(b, 'hover', 0.0, 380)
                b.update()
            except RuntimeError:            # the button went with its window
                pass
        menu.aboutToHide.connect(closed)

    @staticmethod
    def _state(obj) -> dict:
        state = getattr(obj, _LIGHT, None)
        if state is None:
            state = {'hover': 0.0, 'press': 0.0, 'at': QPointF(), 'moving': {}}
            setattr(obj, _LIGHT, state)
        return state

    def _to(self, obj, which: str, value: float, ms: int) -> None:
        state = self._state(obj)
        stop_animation(state['moving'].get(which))
        animation = QVariantAnimation(obj)
        animation.setDuration(ms)
        animation.setStartValue(float(state[which]))
        animation.setEndValue(value)
        animation.setEasingCurve(QEasingCurve.OutCubic)

        def step(v):
            state[which] = float(v)
            obj.update()
        animation.valueChanged.connect(step)
        if which == 'press' and value == 0.0:
            animation.finished.connect(lambda: _sink(obj, False))      # eased back: drawn as it is again
        state['moving'][which] = animation
        _keep(animation)
        animation.start()

    def _paint(self, obj, event) -> bool:
        state = getattr(obj, _LIGHT, None)
        if not state or (state['hover'] < 0.01 and state['press'] < 0.01):
            return False
        if not _fades(obj):
            return False
        try:
            type(obj).paintEvent(obj, event)                # the widget as it paints itself, then the light
        except (AttributeError, RuntimeError, TypeError):
            return False
        return _paint_fade(obj, state, QPainter(obj))


def _fades(obj) -> bool:
    """A button brightens as the pointer comes onto it and dims as it is
    pressed, in a moment rather than at once; a panel doesn't."""
    return isinstance(obj, (QPushButton, QToolButton))


PRESS_SINK = 3.0                    # px a pressed button shrinks by, at most, along its longer side


class _Sink(QGraphicsEffect):
    """A pressed button gives under the finger: drawn as it painted itself (its light too), a touch
    smaller. Set only while it is pressed or easing back (_Light), and taken off after. Until
    0.1.53 the button drew itself into a picture from inside its own paint, which Qt can't do:
    pressed buttons lost their text, or showed a square behind the capsule."""

    def __init__(self, button: QWidget):
        super().__init__(button)
        self._button = button

    def scale(self) -> float:
        state = getattr(self._button, _LIGHT, None) or {}
        b = self._button
        return 1 - state.get('press', 0.0) * min(0.04, PRESS_SINK * 2 / max(b.width(), b.height(), 1))

    def draw(self, painter):
        scale = self.scale()
        if scale > 0.999:
            self.drawSource(painter)
            return
        image = self.sourcePixmap(Qt.LogicalCoordinates, QPoint(), QGraphicsEffect.NoPad)
        if image.isNull():
            return
        ratio = image.devicePixelRatio() or 1.0
        centre = QPointF(image.width() / ratio / 2, image.height() / ratio / 2)
        painter.save()
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.translate(centre)
        painter.scale(scale, scale)
        painter.translate(-centre)
        painter.drawPixmap(QPointF(0, 0), image)
        painter.restore()


def _sink(obj, on: bool) -> None:
    """Put a button's _Sink on as it is pressed, and take it off once it has eased back."""
    if not isinstance(obj, (QPushButton, QToolButton)):
        return
    effect = obj.graphicsEffect()
    if on and effect is None and motion_on():
        obj.setGraphicsEffect(_Sink(obj))
    elif not on and isinstance(effect, _Sink):
        obj.setGraphicsEffect(None)


def _paint_fade(obj, state: dict, painter: QPainter) -> bool:
    """The Frosted look's hover and press, over a button as it painted itself: a soft lightening
    under the pointer, a shade as it is pressed."""
    painter.setRenderHint(QPainter.Antialiasing)
    r = QRectF(obj.rect()).adjusted(1, 1, -1, -1)
    radius = getattr(obj, '_suite_light_radius', None)
    radius = min(8.0, r.height() / 2) if radius is None else radius
    path = QPainterPath()
    path.addRoundedRect(r, radius, radius)
    dark = is_dark()
    hover, press = state['hover'], state['press']
    if hover > 0.01:
        painter.fillPath(path, QColor(255, 255, 255, int((22 if dark else 70) * hover)))
    if press > 0.01:
        painter.fillPath(path, QColor(0, 0, 0, int((40 if dark else 22) * press)))
    painter.end()
    return True


_lights_filter = None


def _lights() -> _Light:
    global _lights_filter
    if _lights_filter is None:
        _lights_filter = _Light()
    return _lights_filter


# --------------------------------------------------------------------------- animations

_running: set = set()          # animations under way, kept from Python's collector until they stop


def _keep(animation) -> None:
    """Hold an animation while it runs, and let it go once it stops, finished or not. Qt keeps an
    animation made with a parent until that parent goes, and the suite's windows live as long as
    their apps: each hover, tick and fade left one more behind (until 2026-10-26)."""
    _running.add(animation)

    def stopped(state, _=None) -> None:
        if state == QAbstractAnimation.State.Stopped:
            _running.discard(animation)
            try:
                animation.deleteLater()
            except RuntimeError:            # its widget went first, and took it along
                pass
    animation.stateChanged.connect(stopped)


def animate(animation) -> None:
    """Start an animation that is let go once it stops (_keep). One kept to be stopped later is
    stopped with stop_animation, as it may be gone by then."""
    _keep(animation)
    animation.start()


def stop_animation(animation) -> None:
    """Stop an animation if it is still there: the suite's are deleted once they stop."""
    if animation is None:
        return
    try:
        animation.stop()
    except RuntimeError:                    # gone already
        pass


# --------------------------------------------------------------------------- smooth scrolling

SCROLL_MS = 150              # a wheel notch's scroll glides this long (the next one picks it up from where it is)


class _Wheels(QObject):
    """Every scroll area's mouse wheel, gliding: Qt jumps a notch's 60 px at once. The distance is
    Qt's own (lines a notch times the bar's step); only the getting there is animated. A touchpad's
    scrolling is smooth already and is left to Qt, as is Ctrl with the wheel (zoom), and anything
    with nothing to scroll that way - the wheel then goes on to what holds it, as it would."""

    def eventFilter(self, obj, event):
        if event.type() != QEvent.Wheel or not motion_on():
            return False
        area = obj.parent() if isinstance(obj, QWidget) else None
        if not isinstance(area, QAbstractScrollArea) or obj is not area.viewport():
            return False
        if not event.pixelDelta().isNull() or event.modifiers() & Qt.ControlModifier:
            return False
        delta = event.angleDelta()
        sideways = abs(delta.x()) > abs(delta.y()) or bool(event.modifiers() & Qt.ShiftModifier)
        notches = (delta.x() or delta.y() if sideways else delta.y()) / 120
        if not notches:
            return False
        if isinstance(area, QAbstractItemView) and _by_pixel(area):
            area.updateGeometries()                 # its bar's step a row's height now, not 1
        bar = area.horizontalScrollBar() if sideways else area.verticalScrollBar()
        if bar is None or bar.maximum() <= bar.minimum():
            return False
        glide = getattr(bar, '_suite_glide', None)
        start = bar.value()
        target = glide['to'] if glide and glide['running'] else start
        step = QApplication.wheelScrollLines() * bar.singleStep()
        target = max(bar.minimum(), min(bar.maximum(), round(target - notches * step)))
        if target == start and not (glide and glide['running']):
            return False                            # at its end already: as Qt leaves it
        _glide_bar(bar, target)
        event.accept()
        return True


def _by_pixel(view: QAbstractItemView) -> bool:
    """A list or table scrolled by the pixel, so a glide doesn't step a row at a time (a notch still
    goes three rows, as before). True if it wasn't yet."""
    changed = False
    if view.verticalScrollMode() != QAbstractItemView.ScrollPerPixel:
        view.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        changed = True
    if view.horizontalScrollMode() != QAbstractItemView.ScrollPerPixel:
        view.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        changed = True
    return changed


def _glide_bar(bar, target: int) -> None:
    """Scroll a bar to target, easing out - stopping if anything else moves it meanwhile (the bar
    dragged, a jump to an item)."""
    glide = getattr(bar, '_suite_glide', None)
    if glide is None:
        glide = bar._suite_glide = {'running': False, 'to': bar.value(), 'set': None, 'animation': None}
        bar.sliderPressed.connect(lambda: stop_animation(glide['animation']))
    stop_animation(glide['animation'])
    animation = QVariantAnimation(bar)
    animation.setDuration(SCROLL_MS)
    animation.setStartValue(float(bar.value()))
    animation.setEndValue(float(target))
    animation.setEasingCurve(QEasingCurve.OutCubic)
    glide.update(running=True, to=target, set=bar.value(), animation=animation)

    def step(value):
        if bar.value() != glide['set']:
            animation.stop()                        # moved elsewhere meanwhile
            return
        glide['set'] = round(value)
        bar.setValue(glide['set'])

    def stopped(state, _=None):
        if state == QAbstractAnimation.State.Stopped:
            glide['running'] = False
    animation.valueChanged.connect(step)
    animation.stateChanged.connect(stopped)
    _keep(animation)
    animation.start()


_wheels_filter = None


def _wheels() -> _Wheels:
    global _wheels_filter
    if _wheels_filter is None:
        _wheels_filter = _Wheels()
    return _wheels_filter


def _ui_effects() -> None:
    """Qt's own animations - menus and drop-down lists sliding open, tooltips fading - with the
    suite's: on while Animations is on."""
    on = motion_on()
    for effect in (Qt.UI_General, Qt.UI_AnimateMenu, Qt.UI_FadeMenu, Qt.UI_AnimateCombo, Qt.UI_AnimateTooltip,
                   Qt.UI_FadeTooltip):
        QApplication.setEffectEnabled(effect, on)


def fade_in(widget: QWidget, ms: int = QUICK, window: bool = False) -> None:
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
    """A frameless window opening: it fades in."""
    if motion_on():
        fade_in(window, 170, window=True)


def _animate_parts(window: QWidget) -> None:
    """Once per window part: its stacked pages fade in as they change, its check boxes tick
    smoothly, and its buttons fade their hover and press."""
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
    for view in window.findChildren(QAbstractItemView):
        _by_pixel(view)
    for bar in window.findChildren(QProgressBar):
        if not bar.property('_suite_glides'):
            bar.setProperty('_suite_glides', True)
            _glide_progress(bar)


def _page_in(page: QWidget) -> None:
    """A page coming up in a stack: its sections rise into place one after another (or, without
    any, it fades in)."""
    if not motion_on():
        return
    sections = _sections(page)
    if sections:
        stagger_in(sections)
    else:
        fade_in(page)


def _tick(box: QCheckBox, on: bool) -> None:
    if not box.isVisible() or not motion_on():
        return
    animation = QVariantAnimation(box)
    start = box.property(_TICK)
    animation.setStartValue(float(start) if start is not None else (0.0 if on else 1.0))
    animation.setEndValue(1.0 if on else 0.0)
    animation.setDuration(QUICK)
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


_STAGGER_GAP = 16       # ms between one part's rising and the next's
_STAGGER_MOST = 4       # parts after this many come in with the last


def stagger_in(widgets, by: int = 8, ms: int = QUICK) -> None:
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


def fade_out(window: QWidget, then=None, ms: int = QUICK) -> QPropertyAnimation | None:
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
        stop_animation(state['moving'])
        start = state['shown']
        if not motion_on() or not bar.isVisible() or bar.maximum() <= bar.minimum() or value <= start \
                or start < bar.minimum():
            state['shown'] = value
            return
        put(start)
        glide = QVariantAnimation(bar)
        glide.setDuration(GENTLE)
        glide.setStartValue(float(start))
        glide.setEndValue(float(value))
        glide.setEasingCurve(QEasingCurve.OutCubic)
        glide.valueChanged.connect(lambda v: put(int(round(v))))
        state['moving'] = glide
        _keep(glide)
        glide.start()
    bar.valueChanged.connect(changed)

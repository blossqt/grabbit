"""suitetheme: the suite's look in a Qt app - its pink, check boxes, colours, fonts and cards.

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
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QProxyStyle, QStyle

__all__ = ['ACCENT', 'ACCENT_HOVER', 'ON_ACCENT', 'ROSE', 'SuiteStyle', 'install', 'Colors', 'is_dark',
           'colors', 'ink', 'font', 'paint_shadow', 'card_style']

ACCENT = '#ffd1dc'         # the suite's blossom pink: buttons, ticks, highlights
ACCENT_HOVER = '#ffe0e8'
ON_ACCENT = '#a61e4d'      # text and ticks drawn on ACCENT, which is too pale for white
ROSE = '#c2255c'           # the mark on the icons: the pink as text or a line on white


class SuiteStyle(QProxyStyle):
    """Windows' own style, with check boxes drawn in the suite's pink.

    Windows 11's style loses a ticked box's fill once a style sheet is in
    play, leaving a bare tick that looks cut off. Drawing the box here keeps
    it whole, in any mode, whatever the accent colour of the day.
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
        side = min(option.rect.width(), option.rect.height(), self.SIZE) - 1
        box = QRectF(0, 0, side, side)
        box.moveCenter(QRectF(option.rect).center())
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        if not state & QStyle.State_Enabled:
            painter.setOpacity(0.45)
        if ticked or partly:
            painter.setPen(QPen(c.edge, 1))
            painter.setBrush(QColor(ACCENT))
        else:
            hover = state & QStyle.State_MouseOver and state & QStyle.State_Enabled
            painter.setPen(QPen(c.edge if hover else c.dim, 1))
            painter.setBrush(c.chip)
        painter.drawRoundedRect(box, side * 0.22, side * 0.22)
        pen = QPen(QColor(ON_ACCENT), max(1.6, side * 0.12), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        x, y = box.x(), box.y()
        if ticked:
            tick = QPainterPath(QPointF(x + side * 0.26, y + side * 0.52))
            tick.lineTo(x + side * 0.43, y + side * 0.69)
            tick.lineTo(x + side * 0.75, y + side * 0.33)
            painter.drawPath(tick)
        elif partly:
            painter.drawLine(QPointF(x + side * 0.28, y + side / 2), QPointF(x + side * 0.72, y + side / 2))
        painter.restore()


_style = None


def install(app) -> None:
    """Give the app the suite's style, right after making the QApplication.

    Kept here so Python never frees it under Qt.
    """
    global _style
    _style = SuiteStyle()
    app.setStyle(_style)


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
    if is_dark():
        return Colors(card=QColor('#202127'), border=QColor(255, 255, 255, 30), text=QColor('#ececf1'),
                      dim=QColor('#9a9aa6'), chip=QColor('#2c2d35'),
                      selected=QColor(ACCENT), selected_text=QColor(ON_ACCENT),
                      shadow=QColor(0, 0, 0, 120), error=QColor('#ff8a80'),
                      edge=QColor(ACCENT), tile=QColor('#2b2a31'))
    return Colors(card=QColor('#ffffff'), border=QColor(0, 0, 0, 28), text=QColor('#1d1d22'),
                  dim=QColor('#6e6e7a'), chip=QColor('#f1f2f6'),
                  selected=QColor(ACCENT), selected_text=QColor(ON_ACCENT),
                  shadow=QColor(20, 24, 40, 70), error=QColor('#c62828'),
                  edge=QColor('#ee9fb5'), tile=QColor('#fff6f8'))


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


def card_style() -> str:
    """Stylesheet for the inputs and buttons that sit on a card."""
    c = colors()
    text, dim, chip, border = c.text.name(), c.dim.name(), c.chip.name(), c.border.name(QColor.HexArgb)
    edge = c.edge.name()
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
            color: {text}; background: {chip}; border: 1px solid {border};
            border-radius: 8px; padding: 6px 16px;
        }}
        QPushButton:hover {{ border: 1px solid {edge}; }}
        QPushButton[role="primary"] {{ color: {ON_ACCENT}; background: {ACCENT}; border: 1px solid {edge};
                                       font-weight: 600; }}
        QPushButton[role="primary"]:hover {{ background: {ACCENT_HOVER}; }}
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

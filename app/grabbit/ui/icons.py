"""Simple line icons, drawn as SVG and tinted to match the current theme."""

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap, QPolygonF
from PySide6.QtWidgets import QApplication

from . import suiteicons

_SHAPES = suiteicons.SHAPES      # the suite's one set of line icons


def theme_color(role: str = 'text') -> QColor:
    palette = QApplication.palette()
    if role == 'accent':
        # The pink as a line on the window: the highlight itself is too pale
        # to draw with on white, so theme.py makes the link colour its ink.
        return palette.link().color()
    if role == 'dim':
        return palette.color(palette.ColorGroup.Disabled, palette.ColorRole.Text)
    return palette.windowText().color()


def icon(name: str, color: QColor | str | None = None, size: int = 20, width: float = suiteicons.STROKE) -> QIcon:
    """A QIcon for one of the suite's line icons, tinted for the current palette."""
    if name not in _SHAPES:
        return QIcon()
    return suiteicons.icon(name, theme_color() if color is None else color, size, width)


def clear_cache():
    """Called when the palette changes so icons pick up the new colours."""
    suiteicons.clear_cache()


# The icon follows the suite's shared shape, so it sits beside the other suite
# apps' as one of a set: a tile in blossom pink filling the whole square,
# corners rounded to 42/192 of its width, and a mark in deep rose on it -
# white would vanish on the pale pink.
#
# The colours are constants on purpose. make_icon.py bakes this icon into the
# exe at build time, and a colour taken from the palette there is simply
# whatever accent Windows had that day - so each release froze a different
# one, blue one time and gold the next, and none of them followed the accent
# afterwards. The rest of the interface still follows the theme; the icon is
# the app's name.
APP_ICON_COLOR = '#ffd1dc'
APP_ICON_MARK = '#c2255c'
APP_ICON_CORNER = 42 / 192


def render_app_icon(size: int) -> QPixmap:
    """The app icon at one size: a downward arrow dropping into a tray."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.scale(size / 64.0, size / 64.0)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(APP_ICON_COLOR))
    corner = 64 * APP_ICON_CORNER
    painter.drawRoundedRect(QRectF(0, 0, 64, 64), corner, corner)
    painter.setBrush(QColor(APP_ICON_MARK))
    painter.drawRoundedRect(QRectF(28, 14, 8, 22), 4, 4)
    painter.drawPolygon(QPolygonF([QPointF(20, 30), QPointF(44, 30), QPointF(32, 46)]))
    painter.drawRoundedRect(QRectF(16, 48, 32, 6), 3, 3)
    painter.end()
    return pixmap


def app_icon() -> QIcon:
    """The window, tray and notification icon - and the one in the exe."""
    result = QIcon()
    for size in (16, 24, 32, 48, 64, 256):
        result.addPixmap(render_app_icon(size))
    return result

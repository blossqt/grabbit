"""Simple line icons, drawn as SVG and tinted to match the current theme."""

from PySide6.QtCore import QByteArray, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap, QPolygonF
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

# 24x24 stroke drawings. '{c}' is filled in with the requested colour.
_STROKE = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
           'stroke="{c}" stroke-width="{w}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>')

_SHAPES = {
    'add': '<path d="M12 5v14M5 12h14"/>',
    'link': '<path d="M9.5 14.5 14.5 9.5"/><path d="M11 7l1.5-1.5a4 4 0 0 1 5.7 5.7L16.7 12.7"/>'
            '<path d="M13 17l-1.5 1.5a4 4 0 0 1-5.7-5.7L7.3 11.3"/>',
    'magnet': '<path d="M6 4v8a6 6 0 0 0 12 0V4"/><path d="M6 9h4M14 9h4"/><path d="M6 4h4M14 4h4"/>',
    'play': '<path d="M8 5.5v13l11-6.5z"/>',
    'pause': '<path d="M9.5 5v14M14.5 5v14"/>',
    'trash': '<path d="M4 7h16"/><path d="M9.5 7V5h5v2"/><path d="M6.5 7l1 12.5h9L17.5 7"/>'
             '<path d="M10.5 10.5v6M13.5 10.5v6"/>',
    'folder': '<path d="M3.5 7.5A1.5 1.5 0 0 1 5 6h4l2 2.5h6.5a1.5 1.5 0 0 1 1.5 1.5v7.5A1.5 1.5 0 0 1 17.5 19h-13A1.5 1.5 0 0 1 3 17.5z"/>',
    'settings': '<path d="M4 7h10M18 7h2M4 12h2M10 12h10M4 17h8M16 17h4"/>'
                '<circle cx="16" cy="7" r="2"/><circle cx="8" cy="12" r="2"/><circle cx="14" cy="17" r="2"/>',
    'search': '<circle cx="11" cy="11" r="6"/><path d="M15.5 15.5 20 20"/>',
    'video': '<rect x="3" y="6" width="13" height="12" rx="2"/><path d="M16 11l5-3v8l-5-3z"/>',
    'image': '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><circle cx="9" cy="10" r="1.6"/>'
             '<path d="M5 17l4.5-4.5 3 3L16 12l3 3.5"/>',
    'file': '<path d="M13 3.5H7A1.5 1.5 0 0 0 5.5 5v14A1.5 1.5 0 0 0 7 20.5h10a1.5 1.5 0 0 0 1.5-1.5V9z"/>'
            '<path d="M13 3.5V9h5.5"/>',
    'music': '<path d="M9 18V6l10-2v12"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="16.5" cy="16" r="2.5"/>',
    'check': '<path d="M5 12.5 9.5 17 19 7.5"/>',
    'close': '<path d="M6 6l12 12M18 6 6 18"/>',
    'down': '<path d="M12 4v14"/><path d="M6 12.5 12 19l6-6.5"/>',
    'up': '<path d="M12 20V6"/><path d="M6 11.5 12 5l6 6.5"/>',
    'refresh': '<path d="M19 12a7 7 0 1 1-2.1-5"/><path d="M19.5 4v4.5H15"/>',
    'clipboard': '<rect x="6" y="4.5" width="12" height="15" rx="2"/>'
                 '<path d="M9.5 4.5V3.5h5v1"/><path d="M9 11h6M9 14.5h4"/>',
    'seed': '<path d="M12 21V9"/><path d="M12 9c0-3 2-6 6-6 0 4-2.5 6-6 6z"/>'
            '<path d="M12 13c0-2.5-2-4.5-5-4.5 0 3 2 4.5 5 4.5z"/>',
    'error': '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5v5.5M12 16.2v.3"/>',
    'queue': '<path d="M4 7h11M4 12h11M4 17h7"/><path d="M17.5 14.5 20 17l-2.5 2.5"/>',
    'grid': '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/>'
            '<rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>',
    'chart': '<path d="M4 19h16"/><path d="M5 15.5 9.5 10l3.5 3.5L19 6"/><path d="M19 6h-3.5M19 6v3.5"/>',
    'info': '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5.5M12 7.8v.3"/>',
}

_cache: dict = {}


def theme_color(role: str = 'text') -> QColor:
    palette = QApplication.palette()
    if role == 'accent':
        return palette.highlight().color()
    if role == 'dim':
        return palette.color(palette.ColorGroup.Disabled, palette.ColorRole.Text)
    return palette.windowText().color()


def icon(name: str, color: QColor | str | None = None, size: int = 20, width: float = 1.8) -> QIcon:
    """A QIcon for one of the names above, tinted for the current palette."""
    body = _SHAPES.get(name)
    if not body:
        return QIcon()
    if color is None:
        color = theme_color()
    if isinstance(color, QColor):
        color = color.name()
    key = (name, color, size, width)
    cached = _cache.get(key)
    if cached is not None:
        return cached

    svg = _STROKE.format(c=color, w=width, body=body)
    renderer = QSvgRenderer(QByteArray(svg.encode('utf-8')))
    ratio = QApplication.primaryScreen().devicePixelRatio() if QApplication.primaryScreen() else 1.0
    pixmap = QPixmap(int(size * ratio), int(size * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    result = QIcon(pixmap)
    _cache[key] = result
    return result


def clear_cache():
    """Called when the palette changes so icons pick up the new colours."""
    _cache.clear()


# The icon follows the suite's shared shape, so it sits beside Homework Hub's
# as one of a set: a tile in this blue filling the whole square, corners
# rounded to 42/192 of its width, and a white mark on it.
#
# The colour is a constant on purpose. make_icon.py bakes this icon into the
# exe at build time, and a colour taken from the palette there is simply
# whatever accent Windows had that day - so each release froze a different
# one, blue one time and gold the next, and none of them followed the accent
# afterwards. The rest of the interface still follows the theme; the icon is
# the app's name.
APP_ICON_COLOR = '#3b5bdb'
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
    painter.setBrush(QColor('#ffffff'))
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

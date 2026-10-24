"""Light/dark handling and the handful of style rules Grabbit adds.

The suite's look - its pink, the check boxes SuiteStyle draws, ink() - comes
from suitetheme.py, the shared file Hanami keeps in step across the suite's
apps. Grabbit adds a palette of its own here, for a window built from Qt's
standard widgets rather than painted cards.

The pink is too pale for white text or for lines on white, so text on it is
deep rose (ON_ACCENT), and lines and icons drawn in it use ink().
"""

from pathlib import Path

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from .suitetheme import *  # noqa: F401,F403 - the suite's look, used as theme.<name>
from .suitetheme import ACCENT, ON_ACCENT, app_mode, glass_on, ink, is_dark, liquid_controls_css, panel_css

BACKDROP = str(Path(__file__).with_name('backdrop.jpg'))     # TOMOKO UJI's blossom, on Unsplash, faint behind the windows

_DARK = {
    'window': '#1e1f22', 'base': '#17181a', 'card': '#17181a', 'alt': '#232427', 'text': '#e6e6e6',
    'dim': '#9aa0a6', 'border': '#34363b', 'hover': '#2a2c31', 'bar': '#2b2d31',
}
_LIGHT = {
    'window': '#f4f5f7', 'base': '#ffffff', 'card': '#ffffff', 'alt': '#fafafa', 'text': '#1a1c1e',
    'dim': '#5f6368', 'border': '#d8dade', 'hover': '#eceef1', 'bar': '#e9ebef',
}


# On glass (suitetheme.frost), the window's own colour is the suite's veil, painted under it, so
# the style sheet leaves it see-through and keeps its panels and lists faint: as faint as the
# suite's cards (card), its boxes as its own (base). Alternate rows are drawn over the list.
_DARK_GLASS = {**_DARK, 'window': '#00000000', 'base': '#10ffffff', 'card': '#10ffffff', 'alt': '#0affffff',
               'border': '#2effffff', 'hover': '#20ffffff', 'bar': '#2affffff'}
_LIGHT_GLASS = {**_LIGHT, 'window': '#00000000', 'base': '#8cffffff', 'card': '#5affffff', 'alt': '#24ffffff',
                'border': '#22000000', 'hover': '#14000000', 'bar': '#20000000'}


def colors() -> dict:
    if glass_on():
        return dict(_DARK_GLASS if is_dark() else _LIGHT_GLASS)
    return dict(_DARK if is_dark() else _LIGHT)


def apply_theme(app: QApplication, mode: str = 'system'):
    """mode: 'system' (as the suite is - chosen in Hanami, else as Windows is) | 'light' | 'dark'."""
    app_mode(mode)

    if is_dark():
        _apply_dark_palette(app)
    else:
        palette = app.style().standardPalette()
        _pink(palette)
        app.setPalette(palette)
    app.setStyleSheet(stylesheet())


def _pink(palette: QPalette) -> None:
    palette.setColor(QPalette.Highlight, QColor(ACCENT))
    palette.setColor(QPalette.HighlightedText, QColor(ON_ACCENT))
    palette.setColor(QPalette.Link, QColor(ink()))
    # Windows 11's style fills radio buttons, sliders and progress bars with
    # the accent (check boxes are SuiteStyle's), and marks them in white on a
    # light window: there the pink is deepened so the white still shows.
    palette.setColor(QPalette.Accent, QColor(ACCENT if is_dark() else '#e64980'))


def _apply_dark_palette(app: QApplication):
    c = _DARK
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(c['window']))
    palette.setColor(QPalette.WindowText, QColor(c['text']))
    palette.setColor(QPalette.Base, QColor(c['base']))
    palette.setColor(QPalette.AlternateBase, QColor(c['alt']))
    palette.setColor(QPalette.Text, QColor(c['text']))
    palette.setColor(QPalette.Button, QColor(c['window']))
    palette.setColor(QPalette.ButtonText, QColor(c['text']))
    palette.setColor(QPalette.ToolTipBase, QColor(c['alt']))
    palette.setColor(QPalette.ToolTipText, QColor(c['text']))
    _pink(palette)
    palette.setColor(QPalette.PlaceholderText, QColor(c['dim']))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        palette.setColor(QPalette.Disabled, role, QColor(c['dim']))
    app.setPalette(palette)


def stylesheet() -> str:
    c = colors()
    return f"""
    QToolBar {{ border: 0; padding: 4px 6px; spacing: 2px; background: {c['window']}; }}
    QToolBar QToolButton {{ padding: 5px 9px; border-radius: 6px; }}
    QToolBar QToolButton:hover {{ background: {c['hover']}; }}
    QToolBar QToolButton:pressed {{ background: {c['bar']}; }}
    QToolBar QToolButton:disabled {{ color: {c['dim']}; }}

    #Sidebar {{ background: {c['window']}; border: 0; outline: 0; padding: 6px 4px; }}
    #Sidebar::item {{ padding: 6px 8px; border-radius: 6px; margin: 1px 2px; color: {c['text']}; }}
    #Sidebar::item:selected {{ background: {c['hover']}; color: {c['text']}; }}
    #Sidebar::item:hover {{ background: {c['hover']}; }}

    #SidebarHeader {{ color: {c['dim']}; font-size: 11px; font-weight: 600;
                      padding: 10px 10px 2px 10px; text-transform: uppercase; }}
    #DetailLabel {{ color: {c['dim']}; }}
    #Card {{ background: {c['card']}; border: 1px solid {c['border']}; border-radius: 10px; }}
    #CardTitle {{ font-weight: 600; }}
    #Muted {{ color: {c['dim']}; }}
    #Banner {{ background: {c['alt']}; border: 1px solid {c['border']}; border-radius: 8px; }}

    QTreeView, QTableView {{ border: 1px solid {c['border']}; border-radius: 8px;
                             background: {c['card']}; alternate-background-color: {c['alt']}; }}
    QHeaderView::section {{ background: {c['window']}; border: 0;
                            border-bottom: 1px solid {c['border']}; padding: 5px 8px; }}
    QStatusBar {{ border-top: 1px solid {c['border']}; }}
    QStatusBar::item {{ border: 0; }}
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {{
        border: 1px solid {c['border']}; border-radius: 6px; padding: 4px 7px;
        background: {c['base']}; selection-background-color: {ACCENT};
        selection-color: {ON_ACCENT}; }}
    QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{
        border-color: {ink()}; }}
    QTabWidget::pane {{ border: 1px solid {c['border']}; border-radius: 8px; top: -1px; }}
    QTabBar::tab {{ padding: 6px 12px; border: 0; margin-right: 2px; border-radius: 6px; }}
    QTabBar::tab:selected {{ background: {c['hover']}; }}
    QScrollArea {{ border: 0; }}
    """ + _liquid()


def _liquid() -> str:
    """In the suite's liquid look, its capsules of glass for buttons and boxes, and glass cards."""
    controls = liquid_controls_css()
    return controls + panel_css('#Card', 10) if controls else ''

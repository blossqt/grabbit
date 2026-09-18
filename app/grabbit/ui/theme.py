"""Light/dark handling and the handful of style rules Grabbit adds."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

ACCENT = QColor('#3a86ff')

_DARK = {
    'window': '#1e1f22', 'base': '#17181a', 'alt': '#232427', 'text': '#e6e6e6',
    'dim': '#9aa0a6', 'border': '#34363b', 'hover': '#2a2c31', 'bar': '#2b2d31',
}
_LIGHT = {
    'window': '#f4f5f7', 'base': '#ffffff', 'alt': '#fafafa', 'text': '#1a1c1e',
    'dim': '#5f6368', 'border': '#d8dade', 'hover': '#eceef1', 'bar': '#e9ebef',
}


def is_dark() -> bool:
    app = QApplication.instance()
    if app is None:
        return False
    try:
        return app.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:
        return app.palette().window().color().lightness() < 128


def colors() -> dict:
    return dict(_DARK if is_dark() else _LIGHT)


def apply_theme(app: QApplication, mode: str = 'system'):
    """mode: 'system' | 'light' | 'dark'."""
    try:
        scheme = {'light': Qt.ColorScheme.Light, 'dark': Qt.ColorScheme.Dark}.get(
            mode, Qt.ColorScheme.Unknown)
        app.styleHints().setColorScheme(scheme)
    except (AttributeError, TypeError):
        pass

    if mode == 'dark' or (mode == 'system' and is_dark()):
        _apply_dark_palette(app)
    else:
        app.setPalette(app.style().standardPalette())
    app.setStyleSheet(stylesheet())


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
    palette.setColor(QPalette.Highlight, ACCENT)
    palette.setColor(QPalette.HighlightedText, QColor('#ffffff'))
    palette.setColor(QPalette.Link, ACCENT)
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
    #Card {{ background: {c['base']}; border: 1px solid {c['border']}; border-radius: 10px; }}
    #CardTitle {{ font-weight: 600; }}
    #Muted {{ color: {c['dim']}; }}
    #Banner {{ background: {c['alt']}; border: 1px solid {c['border']}; border-radius: 8px; }}

    QTreeView, QTableView {{ border: 1px solid {c['border']}; border-radius: 8px;
                             background: {c['base']}; alternate-background-color: {c['alt']}; }}
    QHeaderView::section {{ background: {c['window']}; border: 0;
                            border-bottom: 1px solid {c['border']}; padding: 5px 8px; }}
    QStatusBar {{ border-top: 1px solid {c['border']}; }}
    QStatusBar::item {{ border: 0; }}
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {{
        border: 1px solid {c['border']}; border-radius: 6px; padding: 4px 7px;
        background: {c['base']}; selection-background-color: {ACCENT.name()}; }}
    QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{
        border-color: {ACCENT.name()}; }}
    QTabWidget::pane {{ border: 1px solid {c['border']}; border-radius: 8px; top: -1px; }}
    QTabBar::tab {{ padding: 6px 12px; border: 0; margin-right: 2px; border-radius: 6px; }}
    QTabBar::tab:selected {{ background: {c['hover']}; }}
    QScrollArea {{ border: 0; }}
    """

r"""The suite's icons: one set of line drawings, for every app (Hanami's original, copied into
each Qt app's ui\ - tests\check_copies.py keeps them alike).

Each is a 24x24 drawing in round-capped strokes of one width (STROKE), drawn at any size and
colour. Since 2026-11-01 they stand in for Segoe Fluent Icons too (settings pages, empty places,
Hanami's buttons), so the suite has one kind of icon. Names shared by two apps' sets kept one
drawing. Made by Keytap\.dev\scale\make_suiteicons.py from the apps' own sets."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

STROKE = 1.8            # the line's width, in the drawings' 24 units
SIZE = 18               # px: an icon by a label or on a button

_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="{c}" stroke-width="{w}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>')

# '{c}' is the colour, for the few parts that are filled.
SHAPES = {
    'add': '<path d="M12 5v14M5 12h14"/>',
    'archive': '<rect x="3.5" y="4.5" width="17" height="4.5" rx="1"/><path d="M5 9v9.5a1.5 1.5 0 0 0 1.5 1.5h11a1.5 1.5 0 0 0 1.5-1.5V9M10 13h4"/>',
    'audio': '<path d="M9 18V6l10-2v12"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="16.5" cy="16" r="2.5"/>',
    'card': '<rect x="3" y="5.5" width="18" height="13" rx="2"/><path d="M3 10h18M6.5 15h4"/>',
    'chart': '<path d="M4 19h16"/><path d="M5 15.5 9.5 10l3.5 3.5L19 6"/><path d="M19 6h-3.5M19 6v3.5"/>',
    'check': '<path d="M5 12.5 9.5 17 19 7.5"/>',
    'clip': '<path d="M15.5 7l-6.8 6.8a1.8 1.8 0 0 0 2.5 2.5l7-7a3.5 3.5 0 0 0-5-5l-7 7a5.2 5.2 0 0 0 7.4 7.4L20 12"/>',
    'clipboard': '<rect x="6" y="4.5" width="12" height="15" rx="2"/><path d="M9.5 4.5V3.5h5v1"/><path d="M9 11h6M9 14.5h4"/>',
    'clock': '<circle cx="12" cy="12" r="8"/><path d="M12 8v4l2.5 2.5"/>',
    'close': '<path d="M6 6l12 12M18 6 6 18"/>',
    'close_x': '<path d="M6 6l12 12M18 6 6 18"/>',
    'combine': '<rect x="4" y="3.5" width="10" height="13" rx="1.5"/><rect x="10" y="7.5" width="10" height="13" rx="1.5"/>',
    'compress': '<path d="M4 14h6v6"/><path d="M20 10h-6V4"/><path d="M14 10l6.5-6.5"/><path d="M3.5 20.5 10 14"/>',
    'copy': '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h8"/>',
    'data': '<path d="M9 4.5H8a2 2 0 0 0-2 2v3a2.5 2.5 0 0 1-2 2.5 2.5 2.5 0 0 1 2 2.5v3a2 2 0 0 0 2 2h1"/><path d="M15 4.5h1a2 2 0 0 1 2 2v3a2.5 2.5 0 0 0 2 2.5 2.5 2.5 0 0 0-2 2.5v3a2 2 0 0 1-2 2h-1"/>',
    'dice': '<rect x="4" y="4" width="16" height="16" rx="3"/><circle cx="9" cy="9" r="1.1" fill="{c}"/><circle cx="15" cy="15" r="1.1" fill="{c}"/><circle cx="12" cy="12" r="1.1" fill="{c}"/>',
    'document': '<path d="M14 3.5H6.5A1.5 1.5 0 0 0 5 5v14a1.5 1.5 0 0 0 1.5 1.5h11A1.5 1.5 0 0 0 19 19V8.5z"/><path d="M14 3.5V8.5h5"/><path d="M8.5 12.5h7M8.5 15.5h7M8.5 18h4"/>',
    'down': '<path d="M12 4v14"/><path d="M6 12.5 12 19l6-6.5"/>',
    'download': '<path d="M12 4v11.5M7.5 11L12 15.5l4.5-4.5M4.5 15v3.5a1.5 1.5 0 0 0 1.5 1.5h12a1.5 1.5 0 0 0 1.5-1.5V15"/>',
    'duplicate': '<path d="M3.5 7.5A1.5 1.5 0 0 1 5 6h4l2 2.5h6.5a1.5 1.5 0 0 1 1.5 1.5v7.5A1.5 1.5 0 0 1 17.5 19h-13A1.5 1.5 0 0 1 3 17.5z"/><path d="M11.5 11v5M9 13.5h5"/>',
    'edit': '<path d="M4.5 19.5l1-4L16 5a2 2 0 0 1 3 3L8.5 18.5z"/><path d="M14 7l3 3"/>',
    'error': '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5v5.5M12 16.2v.3"/>',
    'external': '<path d="M14 4h6v6M20 4l-8.5 8.5"/><path d="M18.5 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7.5a2 2 0 0 1 2-2h4"/>',
    'eye': '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="3"/>',
    'eye_off': '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="3"/><path d="M4 4l16 16"/>',
    'file': '<path d="M13 3.5H7A1.5 1.5 0 0 0 5.5 5v14A1.5 1.5 0 0 0 7 20.5h10a1.5 1.5 0 0 0 1.5-1.5V9z"/><path d="M13 3.5V9h5.5"/>',
    'files': '<path d="M15.5 7.5V5A1.5 1.5 0 0 0 14 3.5H6A1.5 1.5 0 0 0 4.5 5v11A1.5 1.5 0 0 0 6 17.5h2"/><rect x="9.5" y="7.5" width="10" height="13" rx="1.5"/>',
    'filter': '<path d="M4 5.5h16l-6 7.5v5.5l-4 1.5V13z"/>',
    'flip': '<path d="M12 3v18"/><path d="M9 7.5 4 17h5z"/><path d="M15 7.5l5 9.5h-5z"/>',
    'folder': '<path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2.5h7a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>',
    'gamepad': '<rect x="2.5" y="7" width="19" height="10.5" rx="4"/><path d="M7 10.5v4M5 12.5h4M15.5 11.5h.01M18 13.5h.01"/>',
    'gear': '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-1.8-.3 1.6 1.6 0 0 0-1 1.5V21a2 2 0 0 1-4 0v-.1a1.6 1.6 0 0 0-1-1.5 1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H3a2 2 0 0 1 0-4h.1a1.6 1.6 0 0 0 1.5-1 1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3H9a1.6 1.6 0 0 0 1-1.5V3a2 2 0 0 1 4 0v.1a1.6 1.6 0 0 0 1 1.5 1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8V9a1.6 1.6 0 0 0 1.5 1H21a2 2 0 0 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1z"/>',
    'globe': '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.4 2.6 3.6 5.4 3.6 8.5s-1.2 5.9-3.6 8.5c-2.4-2.6-3.6-5.4-3.6-8.5s1.2-5.9 3.6-8.5z"/>',
    'grayscale': '<circle cx="12" cy="12" r="8.5"/><path d="M12 3.5a8.5 8.5 0 0 1 0 17z" fill="{c}"/>',
    'grid': '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>',
    'headphones': '<path d="M4 18v-5a8 8 0 0 1 16 0v5"/><rect x="3.5" y="14" width="4" height="6" rx="1.5"/><rect x="16.5" y="14" width="4" height="6" rx="1.5"/>',
    'image': '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><circle cx="9" cy="10" r="1.6"/><path d="M5 17l4.5-4.5 3 3L16 12l3 3.5"/>',
    'info': '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5.5M12 7.8v.3"/>',
    'key': '<circle cx="8" cy="12" r="3.5"/><path d="M11.5 12H20M17 12v3M20 12v2.5"/>',
    'keyboard': '<rect x="2.5" y="6" width="19" height="12" rx="2"/><path d="M6.5 10h.01M10 10h.01M14 10h.01M17.5 10h.01M8 14h8"/>',
    'link': '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
    'list': '<path d="M9 6.5h11M9 12h11M9 17.5h11M4.5 6.5h.01M4.5 12h.01M4.5 17.5h.01"/>',
    'lock': '<rect x="5" y="10.5" width="14" height="10" rx="2"/><path d="M8 10.5V7.5a4 4 0 0 1 8 0v3"/>',
    'magnet': '<path d="M6 4v8a6 6 0 0 0 12 0V4"/><path d="M6 9h4M14 9h4"/><path d="M6 4h4M14 4h4"/>',
    'mic': '<rect x="9" y="3.5" width="6" height="11" rx="3"/><path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v2.5"/>',
    'mic_off': '<path d="M15 9.3V6.5a3 3 0 0 0-5.7-1.3M9 9v2.5a3 3 0 0 0 5.1 2.1M18.5 11.5a6.4 6.4 0 0 1-1 3.4M5.5 11.5a6.5 6.5 0 0 0 10.6 5M12 18v2.5M4 4l16 16"/>',
    'more': '<circle cx="6" cy="12" r="1.2" fill="{c}"/><circle cx="12" cy="12" r="1.2" fill="{c}"/><circle cx="18" cy="12" r="1.2" fill="{c}"/>',
    'move': '<path d="M3.5 7.5A1.5 1.5 0 0 1 5 6h4l2 2.5h6.5a1.5 1.5 0 0 1 1.5 1.5v7.5A1.5 1.5 0 0 1 17.5 19h-13A1.5 1.5 0 0 1 3 17.5z"/><path d="M8.5 13.5h6.5M12.5 11l2.5 2.5-2.5 2.5"/>',
    'music': '<path d="M9 18V6l10-2v12"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="16.5" cy="16" r="2.5"/>',
    'mute': '<path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z"/><path d="M16 9.5l5 5M21 9.5l-5 5"/>',
    'note': '<path d="M6 3.5h8.5L19 8v11a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 5 19V5A1.5 1.5 0 0 1 6.5 3.5z"/><path d="M14 3.5V8h5M8.5 12.5h7M8.5 16h5"/>',
    'open': '<path d="M13.5 4.5h6v6"/><path d="M19.5 4.5 11 13"/><path d="M17 14v4.5a1.5 1.5 0 0 1-1.5 1.5h-10A1.5 1.5 0 0 1 4 18.5v-10A1.5 1.5 0 0 1 5.5 7H10"/>',
    'other': '<path d="M14 3.5H6.5A1.5 1.5 0 0 0 5 5v14a1.5 1.5 0 0 0 1.5 1.5h11A1.5 1.5 0 0 0 19 19V8.5z"/><path d="M14 3.5V8.5h5"/>',
    'palette': '<path d="M12 3.5a8.5 8.5 0 1 0 0 17c1.2 0 1.8-.8 1.8-1.7 0-1.3-1.2-1.6-1.2-2.8 0-1 .8-1.7 1.8-1.7h2.1a3.9 3.9 0 0 0 4-4C20.5 6.6 16.7 3.5 12 3.5z"/><path d="M7.5 11.5h.01M9.5 7.5h.01M14 7h.01M17 10h.01"/>',
    'pause': '<path d="M9.5 5v14M14.5 5v14"/>',
    'pdf': '<path d="M14 3.5H6.5A1.5 1.5 0 0 0 5 5v14a1.5 1.5 0 0 0 1.5 1.5h11A1.5 1.5 0 0 0 19 19V8.5z"/><path d="M14 3.5V8.5h5"/><path d="M8.5 13h7M8.5 16.5h5"/>',
    'person': '<circle cx="12" cy="8.5" r="3.5"/><path d="M5 19.5a7 7 0 0 1 14 0"/>',
    'pin': '<path d="M9 3.5h6l-1 6 3.5 3.5h-11L10 9.5z"/><path d="M12 13v7.5"/>',
    'play': '<path d="M8 5.5v13l11-6.5z"/>',
    'plus': '<path d="M12 5v14M5 12h14"/>',
    'queue': '<path d="M4 7h11M4 12h11M4 17h7"/><path d="M17.5 14.5 20 17l-2.5 2.5"/>',
    'refresh': '<path d="M19.5 10.5A7.5 7.5 0 0 0 6 7.2L4.5 9M4.5 4.5V9H9"/><path d="M4.5 13.5A7.5 7.5 0 0 0 18 16.8l1.5-1.8M19.5 19.5V15H15"/>',
    'restore': '<path d="M4.5 12a7.5 7.5 0 1 0 2.2-5.3L4.5 9M4.5 4.5V9H9"/>',
    'rotate_left': '<path d="M4 12a8 8 0 1 0 2.3-5.6"/><path d="M4 4v5h5"/>',
    'rotate_right': '<path d="M20 12a8 8 0 1 1-2.3-5.6"/><path d="M20 4v5h-5"/>',
    'save': '<path d="M5.5 4h10l3.5 3.5v11a1.5 1.5 0 0 1-1.5 1.5h-12A1.5 1.5 0 0 1 4 18.5v-13A1.5 1.5 0 0 1 5.5 4z"/><path d="M8 4v4.5h7V4M8 20v-6h8v6"/>',
    'scan': '<path d="M4 8V5.5A1.5 1.5 0 0 1 5.5 4H8M16 4h2.5A1.5 1.5 0 0 1 20 5.5V8M20 16v2.5a1.5 1.5 0 0 1-1.5 1.5H16M8 20H5.5A1.5 1.5 0 0 1 4 18.5V16"/><path d="M8 10h8M8 14h5"/>',
    'scissors': '<circle cx="6.5" cy="7" r="2.5"/><circle cx="6.5" cy="17" r="2.5"/><path d="M8.6 8.4 20 17M8.6 15.6 20 7"/>',
    'search': '<circle cx="10.5" cy="10.5" r="6"/><path d="M15 15l5 5"/>',
    'seed': '<path d="M12 21V9"/><path d="M12 9c0-3 2-6 6-6 0 4-2.5 6-6 6z"/><path d="M12 13c0-2.5-2-4.5-5-4.5 0 3 2 4.5 5 4.5z"/>',
    'send': '<path d="M20.5 3.5L10 14M20.5 3.5L14 20.5l-4-6.5-6.5-4z"/>',
    'settings': '<path d="M4 7h10M18 7h2M4 12h2M10 12h10M4 17h8M16 17h4"/><circle cx="16" cy="7" r="2"/><circle cx="8" cy="12" r="2"/><circle cx="14" cy="17" r="2"/>',
    'sheet': '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M4 9.5h16M4 15h16M10 4v16"/>',
    'shield': '<path d="M12 3.5l7 3v5.5c0 4.2-3 7.4-7 8.5-4-1.1-7-4.3-7-8.5V6.5z"/><path d="M9 12l2 2 4-4"/>',
    'shrink': '<path d="M20.5 9V5A1.5 1.5 0 0 0 19 3.5H5A1.5 1.5 0 0 0 3.5 5v14A1.5 1.5 0 0 0 5 20.5h4"/><rect x="12" y="12" width="8.5" height="8.5" rx="1.5"/>',
    'sidebar': '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><path d="M15 4.5v15"/>',
    'sliders': '<path d="M4 7h9M18 7h2M4 17h3M12 17h8"/><circle cx="15.5" cy="7" r="2.3"/><circle cx="9.5" cy="17" r="2.3"/>',
    'slides': '<rect x="3.5" y="4.5" width="17" height="11" rx="1.5"/><path d="M12 15.5v4M8.5 19.5h7"/>',
    'speaker': '<path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z"/><path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11"/>',
    'split': '<rect x="3.5" y="4" width="7" height="16" rx="1.5"/><rect x="13.5" y="4" width="7" height="16" rx="1.5"/>',
    'star': '<path d="M12 4l2.4 5 5.4.6-4 3.7 1.1 5.3L12 16l-4.9 2.6 1.1-5.3-4-3.7 5.4-.6z" fill="{c}"/>',
    'star_line': '<path d="M12 4l2.4 5 5.4.6-4 3.7 1.1 5.3L12 16l-4.9 2.6 1.1-5.3-4-3.7 5.4-.6z"/>',
    'subtitles': '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7 15h4M13 15h4M7 11.5h10"/>',
    'svg': '<path d="M12 3.5 18 10l-3 8.5H9L6 10z"/><circle cx="12" cy="12" r="1.5"/><path d="M12 3.5v7"/>',
    'tag': '<path d="M3.5 12.2V4.5a1 1 0 0 1 1-1h7.7l8.3 8.3a1.4 1.4 0 0 1 0 2l-6.2 6.2a1.4 1.4 0 0 1-2 0z"/><circle cx="8" cy="8" r="1.5"/>',
    'terminal': '<rect x="3" y="4.5" width="18" height="15" rx="2"/><path d="M7 9.5l3 2.5-3 2.5M12.5 15H17"/>',
    'text': '<path d="M14 3.5H6.5A1.5 1.5 0 0 0 5 5v14a1.5 1.5 0 0 0 1.5 1.5h11A1.5 1.5 0 0 0 19 19V8.5z"/><path d="M8.5 9h4M8.5 12.5h7M8.5 16h7"/>',
    'trash': '<path d="M4.5 7h15M9.5 7V4.5h5V7M6.5 7l1 12.5h9l1-12.5"/><path d="M10 11v5M14 11v5"/>',
    'unzip': '<path d="M4 8.5h16v10a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 18.5z"/><path d="M4 8.5 6.5 4h11L20 8.5"/><path d="M12 11.5v5M9.5 14l2.5 2.5 2.5-2.5"/>',
    'up': '<path d="M12 20V6"/><path d="M6 11.5 12 5l6 6.5"/>',
    'upload': '<path d="M12 15.5V4M7.5 8.5L12 4l4.5 4.5M4.5 15v3.5a1.5 1.5 0 0 0 1.5 1.5h12a1.5 1.5 0 0 0 1.5-1.5V15"/>',
    'video': '<rect x="3" y="6" width="13" height="12" rx="2"/><path d="M16 11l5-3v8l-5-3z"/>',
    'window': '<rect x="3.5" y="5" width="17" height="14" rx="2"/><path d="M3.5 9h17"/>',
    'wrench': '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.8-3.8a6 6 0 0 1-7.9 7.9l-6.9 6.9a2.1 2.1 0 0 1-3-3l6.9-6.9a6 6 0 0 1 7.9-7.9z"/>',
    'zip': '<path d="M14 3.5H6.5A1.5 1.5 0 0 0 5 5v14a1.5 1.5 0 0 0 1.5 1.5h11A1.5 1.5 0 0 0 19 19V8.5z"/><path d="M11 3.5v2M13 6.5v2M11 9.5v2M13 12.5v2"/><rect x="10.5" y="15.5" width="3" height="3" rx=".8"/>',
}
SHAPES['close'] = SHAPES['close_x']
SHAPES['add'] = SHAPES['plus']

_cache: dict = {}


def pixmap(name: str, color: QColor | str, size: int = SIZE, width: float = STROKE) -> QPixmap:
    """One icon, drawn crisp for the screen at size px in color (its alpha kept). A null pixmap
    for a name not in the set."""
    body = SHAPES.get(name)
    if body is None:
        return QPixmap()
    color = QColor(color)
    screen = QApplication.primaryScreen() if QApplication.instance() else None
    ratio = screen.devicePixelRatio() if screen else 1.0
    key = (name, color.rgba(), size, width, ratio)
    cached = _cache.get(key)
    if cached is not None:
        return cached
    alpha = color.alphaF()
    body = body.replace('{c}', color.name())
    svg = _SVG.format(c=color.name(), w=width, body=f'<g opacity="{alpha:.3f}">{body}</g>' if alpha < 1 else body)
    image = QPixmap(round(size * ratio), round(size * ratio))
    image.setDevicePixelRatio(ratio)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    QSvgRenderer(QByteArray(svg.encode('utf-8'))).render(painter, QRectF(0, 0, size, size))
    painter.end()
    _cache[key] = image
    return image


def icon(name: str, color: QColor | str, size: int = SIZE, width: float = STROKE) -> QIcon:
    return QIcon(pixmap(name, color, size, width))


def draw(painter: QPainter, rect: QRectF, name: str, color: QColor | str, size: int = SIZE) -> None:
    """Draw an icon centred in rect, as a widget paints itself."""
    image = pixmap(name, color, size)
    if not image.isNull():
        painter.drawPixmap(QRectF(rect.center().x() - size / 2, rect.center().y() - size / 2, size, size),
                           image, QRectF(image.rect()))


def clear_cache() -> None:
    _cache.clear()

"""The same colours the desktop uses, in the form Kivy wants them.

Taken from ui/theme.py and ui/transfer_model.py on the desktop side. Keeping
the two builds recognisably the same program is the point, and a hex string
copied by hand drifts, so they are written here once and converted.
"""

from grabbit.tasks import State

ACCENT = '#3a86ff'

# The desktop's dark palette. A phone downloader is a dark-mode thing; the
# light palette can follow if anyone ever asks for it.
PALETTE = {
    'window': '#1e1f22',
    'base': '#17181a',
    'alt': '#232427',
    'text': '#e6e6e6',
    'dim': '#9aa0a6',
    'border': '#34363b',
    'hover': '#2a2c31',
    'bar': '#2b2d31',
}

STATE_COLORS = {
    State.DOWNLOADING: '#3a86ff',
    State.EXTRACTING: '#3a86ff',
    State.METADATA: '#8a7cff',
    State.PROCESSING: '#8a7cff',
    State.SEEDING: '#1faa59',
    State.COMPLETED: '#1faa59',
    State.PAUSED: '#8d9299',
    State.QUEUED: '#8d9299',
    State.CHECKING: '#e8a33d',
    State.ERROR: '#e5484d',
}

UPLOAD = '#1faa59'


def rgba(value: str, alpha: float = 1.0) -> tuple:
    """'#3a86ff' -> (0.23, 0.53, 1.0, alpha)."""
    text = value.lstrip('#')
    if len(text) == 3:
        text = ''.join(c * 2 for c in text)
    red, green, blue = (int(text[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return red, green, blue, alpha


def state_color(state: str, alpha: float = 1.0) -> tuple:
    return rgba(STATE_COLORS.get(state, PALETTE['dim']), alpha)


# Ready-made, because these are used on almost every line.
WINDOW = rgba(PALETTE['window'])
BASE = rgba(PALETTE['base'])
ALT = rgba(PALETTE['alt'])
TEXT = rgba(PALETTE['text'])
DIM = rgba(PALETTE['dim'])
BORDER = rgba(PALETTE['border'])
HOVER = rgba(PALETTE['hover'])
BAR = rgba(PALETTE['bar'])
BLUE = rgba(ACCENT)
GREEN = rgba(UPLOAD)
TRANSPARENT = (0, 0, 0, 0)

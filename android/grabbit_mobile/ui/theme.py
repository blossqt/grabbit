"""Material 3's colours, type and shapes, in the form Kivy wants them.

On Android 12 and later the colours are the phone's own: Android makes five
tonal palettes from the wallpaper (Material You), and they are turned here
into the roles Android's own apps are drawn in - primary for what acts,
containers for what is chosen, surfaces in steps of tone for what holds
things. Older phones, and the desktop preview, get the same roles made from
Grabbit's own blue (#3b5bdb, the suite's icon colour). Light or dark follows the phone; in the preview,
GRABBIT_THEME=light or dark decides.

Everything is worked out once, as this is first imported, and the rest of
the app reads the results as constants.
"""

import logging
import os

from kivy.metrics import dp

from grabbit.tasks import State

log = logging.getLogger(__name__)

# ------------------------------------------------------------- colour maths
def rgba(value: str, alpha: float = 1.0) -> tuple:
    """'#3a86ff' -> (0.23, 0.53, 1.0, alpha)."""
    text = value.lstrip('#')
    if len(text) == 3:
        text = ''.join(c * 2 for c in text)
    red, green, blue = (int(text[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return red, green, blue, alpha


def with_alpha(color, alpha: float) -> tuple:
    return (*color[:3], alpha)


def hex_of(color) -> str:
    return '#' + ''.join(f'{round(max(0.0, min(1.0, c)) * 255):02x}' for c in color[:3])


def _linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _gamma(c):
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


_WHITE = (0.95047, 1.0, 1.08883)          # D65


def _lab_f(t):
    return t ** (1 / 3) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116


def _lab_f_inverse(t):
    return t ** 3 if t ** 3 > 216 / 24389 else (116 * t - 16) / (24389 / 27)


def to_lab(color) -> tuple:
    """CIELAB for an sRGB colour: L* is Material's tone, 0 black to 100 white."""
    r, g, b = (_linear(c) for c in color[:3])
    x = 0.4124 * r + 0.3576 * g + 0.1805 * b
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = 0.0193 * r + 0.1192 * g + 0.9505 * b
    fx, fy, fz = (_lab_f(v / w) for v, w in zip((x, y, z), _WHITE))
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def _from_lab(lightness, a, b) -> tuple:
    fy = (lightness + 16) / 116
    x, y, z = (_lab_f_inverse(f) * w for f, w in zip((fy + a / 500, fy, fy - b / 200), _WHITE))
    r = 3.2406 * x - 1.5372 * y - 0.4986 * z
    g = -0.9689 * x + 1.8758 * y + 0.0415 * z
    b = 0.0557 * x - 0.2040 * y + 1.0570 * z
    return tuple(_gamma(max(0.0, c)) if c > 0 else c for c in (r, g, b))


def _fits(rgb) -> bool:
    return all(-0.001 <= c <= 1.001 for c in rgb)


def at_tone(tone: float, a: float, b: float) -> tuple:
    """The colour of this tone and hue, as colourful as the screen can show
    it up to the chroma asked for."""
    rgb = _from_lab(tone, a, b)
    if not _fits(rgb):
        low, high = 0.0, 1.0
        for _ in range(24):
            middle = (low + high) / 2
            if _fits(_from_lab(tone, a * middle, b * middle)):
                low = middle
            else:
                high = middle
        rgb = _from_lab(tone, a * low, b * low)
    return (*(max(0.0, min(1.0, c)) for c in rgb), 1.0)


# --------------------------------------------------------------- palettes
# The tones of Android's thirteen palette steps, 0 to 1000.
STEPS = (100, 99, 95, 90, 80, 70, 60, 50, 40, 30, 20, 10, 0)


class Palette:
    """One hue at every tone from black to white, as Material builds them."""

    def __init__(self, known: dict):
        self.known = known                      # tone -> colour, where it is known exactly
        self._labs = {tone: to_lab(color) for tone, color in known.items()}

    def tone(self, tone: float) -> tuple:
        """Exact at the known tones; between them, the neighbours' hue and
        chroma blended, at exactly this tone."""
        if tone in self.known:
            return self.known[tone]
        below = max(t for t in self.known if t < tone)
        above = min(t for t in self.known if t > tone)
        share = (tone - below) / (above - below)
        _, a1, b1 = self._labs[below]
        _, a2, b2 = self._labs[above]
        return at_tone(tone, a1 + (a2 - a1) * share, b1 + (b2 - b1) * share)


# Grabbit's own palettes, for a phone that makes none: its blue at every tone,
# with the quieter blues, the neighbouring hue and the near-greys Material's
# default scheme puts beside it. Worked out once, in Oklab - which keeps a
# hue as it lightens, where CIELAB turns blue to lilac - at CIELAB's tones.
def _table(tones: dict) -> Palette:
    return Palette({tone: rgba(value) for tone, value in tones.items()})


_PRIMARY = {
    100: '#ffffff', 99: '#fbfcff', 95: '#ebf1ff', 90: '#d7e2ff', 80: '#b0c6ff', 70: '#89a9ff',
    60: '#708ee2', 50: '#5873c5', 40: '#415aa9', 30: '#2b418e', 20: '#172973', 10: '#080f5a',
    0: '#000000'}
_SECONDARY = {
    100: '#ffffff', 99: '#fbfcff', 95: '#ebf1ff', 90: '#d7e2ff', 80: '#bac6e5', 70: '#9fabc9',
    60: '#8591ad', 50: '#6c7793', 40: '#545e79', 30: '#3d4660', 20: '#273048', 10: '#131b31',
    0: '#000000'}
_TERTIARY = {
    100: '#ffffff', 99: '#fffbff', 95: '#ffebfe', 90: '#ffd6fd', 80: '#e7b8e4', 70: '#ca9dc8',
    60: '#af82ad', 50: '#946992', 40: '#7a5078', 30: '#60395f', 20: '#482247', 10: '#310c30',
    0: '#000000'}
_NEUTRAL = {
    100: '#ffffff', 99: '#fbfcff', 95: '#edf1f9', 90: '#dfe2eb', 80: '#c3c6cf', 70: '#a8abb3',
    60: '#8d9198', 50: '#74777e', 40: '#5b5e65', 30: '#44474d', 20: '#2e3037', 10: '#191b21',
    0: '#000000'}
_VARIANT = {
    100: '#ffffff', 99: '#fbfcff', 95: '#ebf1ff', 90: '#dce2f3', 80: '#c0c6d7', 70: '#a5abbb',
    60: '#8a91a0', 50: '#717786', 40: '#595e6d', 30: '#414754', 20: '#2b303d', 10: '#171b27',
    0: '#000000'}
_GREEN = {
    100: '#ffffff', 99: '#f5fff4', 95: '#c6ffc6', 90: '#a6f5a8', 80: '#8ad88d', 70: '#6fbc73',
    60: '#54a159', 50: '#3a863f', 40: '#1c6d26', 30: '#005311', 20: '#003909', 10: '#002203',
    0: '#000000'}
_AMBER = {
    100: '#ffffff', 99: '#fffcf7', 95: '#ffeed8', 90: '#ffddb0', 80: '#fdba54', 70: '#e09f34',
    60: '#c38400', 50: '#a26c00', 40: '#815600', 30: '#624000', 20: '#442b00', 10: '#291800',
    0: '#000000'}


def _seeded():
    """Primary, secondary, tertiary, neutral and neutral variant palettes."""
    return tuple(_table(tones) for tones in (_PRIMARY, _SECONDARY, _TERTIARY, _NEUTRAL, _VARIANT))


def _from_android(colors):
    """The five palettes Android made from the wallpaper (Look.java)."""
    palettes = []
    for start in range(0, len(colors), len(STEPS)):
        known = {}
        for tone, value in zip(STEPS, colors[start:start + len(STEPS)]):
            value &= 0xffffffff
            known[tone] = ((value >> 16 & 255) / 255, (value >> 8 & 255) / 255,
                           (value & 255) / 255, 1.0)
        palettes.append(Palette(known))
    return tuple(palettes)


def _phone():
    """(dark, palettes) as the phone has them - palettes None before
    Android 12 - or (None, None) off a phone."""
    try:
        from jnius import JavaClass, JavaStaticMethod, MetaJavaClass, autoclass
    except ImportError:
        return None, None
    try:
        class Look(JavaClass, metaclass=MetaJavaClass):
            __javaclass__ = 'com/grabbit/downloader/Look'
            palettes = JavaStaticMethod('(Landroid/app/Activity;)[I')
            night = JavaStaticMethod('(Landroid/app/Activity;)Z')

        activity = autoclass('org.kivy.android.PythonActivity').mActivity
        colors = list(Look.palettes(activity))
        palettes = _from_android(colors) if len(colors) == 5 * len(STEPS) else None
        return bool(Look.night(activity)), palettes
    except Exception:
        log.exception("could not read the phone's colours")
        return True, None


_night, _palettes = _phone()
if _night is None:
    _night = os.environ.get('GRABBIT_THEME', 'dark') != 'light'
DARK = _night
DYNAMIC = _palettes is not None
_primary, _secondary, _tertiary, _neutral, _variant = _palettes or _seeded()

# Material's own error colours, which no wallpaper changes.
_error = Palette({tone: rgba(value) for tone, value in {
    100: '#ffffff', 99: '#fffbf9', 95: '#fceeee', 90: '#f9dedc', 80: '#f2b8b5', 70: '#ec928e',
    60: '#e46962', 50: '#dc362e', 40: '#b3261e', 30: '#8c1d18', 20: '#601410', 10: '#410e0b',
    0: '#000000'}.items()})
# And two Material has no role for: finished and seeding are green on the
# desktop, and checking is amber.
_green = _table(_GREEN)
_amber = _table(_AMBER)


def _role(palette, light: int, dark: int) -> tuple:
    return palette.tone(dark if DARK else light)


# ------------------------------------------------------------------ roles
PRIMARY = _role(_primary, 40, 80)
ON_PRIMARY = _role(_primary, 100, 20)
PRIMARY_CONTAINER = _role(_primary, 90, 30)
ON_PRIMARY_CONTAINER = _role(_primary, 10, 90)
SECONDARY = _role(_secondary, 40, 80)
SECONDARY_CONTAINER = _role(_secondary, 90, 30)
ON_SECONDARY_CONTAINER = _role(_secondary, 10, 90)
TERTIARY = _role(_tertiary, 40, 80)
TERTIARY_CONTAINER = _role(_tertiary, 90, 30)
ON_TERTIARY_CONTAINER = _role(_tertiary, 10, 90)
ERROR = _role(_error, 40, 80)
ON_ERROR = _role(_error, 100, 20)
ERROR_CONTAINER = _role(_error, 90, 30)
ON_ERROR_CONTAINER = _role(_error, 10, 90)

SURFACE = _role(_neutral, 98, 6)
SURFACE_CONTAINER_LOWEST = _role(_neutral, 100, 4)
SURFACE_CONTAINER_LOW = _role(_neutral, 96, 10)
SURFACE_CONTAINER = _role(_neutral, 94, 12)
SURFACE_CONTAINER_HIGH = _role(_neutral, 92, 17)
SURFACE_CONTAINER_HIGHEST = _role(_neutral, 90, 22)
ON_SURFACE = _role(_neutral, 10, 90)
ON_SURFACE_VARIANT = _role(_variant, 30, 80)
OUTLINE = _role(_variant, 50, 60)
OUTLINE_VARIANT = _role(_variant, 80, 30)
INVERSE_SURFACE = _role(_neutral, 20, 90)
INVERSE_ON_SURFACE = _role(_neutral, 95, 20)
SCRIM = (0.0, 0.0, 0.0, 1.0)

SUCCESS = _role(_green, 40, 80)
WARNING = _role(_amber, 50, 80)

TRANSPARENT = (0, 0, 0, 0)

# How far a pressed thing is tinted with the colour of what is on it.
PRESSED = 0.10
# What cannot be used just now: its words, and its fill.
DISABLED_TEXT = with_alpha(ON_SURFACE, 0.38)
DISABLED_FILL = with_alpha(ON_SURFACE, 0.12)

STATE_COLORS = {
    State.DOWNLOADING: PRIMARY,
    State.EXTRACTING: PRIMARY,
    State.METADATA: TERTIARY,
    State.PROCESSING: TERTIARY,
    State.SEEDING: SUCCESS,
    State.COMPLETED: SUCCESS,
    State.PAUSED: OUTLINE,
    State.QUEUED: OUTLINE,
    State.CHECKING: WARNING,
    State.ERROR: ERROR,
}


def state_color(state: str, alpha: float = 1.0) -> tuple:
    return with_alpha(STATE_COLORS.get(state, ON_SURFACE_VARIANT), alpha)


# -------------------------------------------------------------- type, shape
# Material's type scale. In dp, as the rest of the app's sizes are, so a
# larger font setting on the phone does not push words out of their rows.
HEADLINE_SMALL = dp(24)
TITLE_LARGE = dp(22)
TITLE_MEDIUM = dp(16)
TITLE_SMALL = dp(14)
BODY_LARGE = dp(16)
BODY_MEDIUM = dp(14)
BODY_SMALL = dp(12)
LABEL_LARGE = dp(14)
LABEL_MEDIUM = dp(12)
LABEL_SMALL = dp(11)

# Material's corners, in dp.
EXTRA_SMALL, SMALL, MEDIUM, LARGE, EXTRA_LARGE = 4, 8, 12, 16, 28

"""The phone's own font, and Android's fallbacks for what it lacks.

Kivy draws every label in one font file, and a character that file does not
have comes out as an empty box. Android does better: it reads its font list
(/system/etc/fonts.xml), draws in the system font - Roboto, or the maker's
own - and takes each character that font lacks from the next font down its
fallback chain, Noto for every script and symbol there is. This does the
same for Kivy: the default font becomes the system's, and Kivy's measuring
and drawing are taught to split text into runs by which font has each
character.

Two things Android draws cannot be drawn here. Emoji are colour bitmaps,
which Kivy's font renderer does not handle, so they are left out rather than
drawn as boxes. And the invisible characters that join or style emoji go with
them.

Finding the fonts means reading each font's character map, which is quick
but not free, so what was found is kept (fonts.json in the data folder) until
the phone's font list changes - a system update.
"""

import bisect
import json
import logging
import os
import struct
import threading

FONT_LISTS = ('/system/etc/font_fallback.xml', '/system/etc/fonts.xml')
SYSTEM_FONTS = '/system/fonts'

# Characters that draw nothing of their own - joiners, variation selectors,
# emoji tags - and would otherwise come out as boxes.
INVISIBLE = frozenset([*range(0x200B, 0x2010), *range(0x2060, 0x2065),
                       *range(0xFE00, 0xFE10), *range(0xE0020, 0xE0080), 0x20E3])


def _emoji(code: int) -> bool:
    """Emoji and pictographs: left out when no font can draw them."""
    return (0x1F000 <= code <= 0x1FAFF or 0x2600 <= code <= 0x27BF
            or 0x2B00 <= code <= 0x2BFF or 0x1F1E6 <= code <= 0x1F1FF)


# ------------------------------------------------------- character maps
def coverage(path: str):
    """The characters a font has, as (starts, ends) of sorted ranges - or
    None for a font Kivy cannot draw from (colour bitmaps only, or unreadable).
    A collection (.ttc) is read as its first font, the one Kivy opens."""
    try:
        with open(path, 'rb') as handle:
            start = 0
            if handle.read(4) == b'ttcf':
                handle.seek(12)
                start = struct.unpack('>I', handle.read(4))[0]
            handle.seek(start + 4)
            count = struct.unpack('>H', handle.read(2))[0]
            handle.seek(start + 12)
            tables = {}
            for _ in range(count):
                tag, _sum, offset, length = struct.unpack('>4sIII', handle.read(16))
                tables[tag] = (offset, length)
            if b'cmap' not in tables or not (tables.keys() & {b'glyf', b'CFF ', b'CFF2'}):
                return None
            offset, length = tables[b'cmap']
            handle.seek(offset)
            data = handle.read(length)
    except (OSError, struct.error):
        return None
    try:
        ranges = _read_cmap(data)
    except (struct.error, IndexError, ValueError):
        return None
    if not ranges:
        return None
    ranges.sort()
    merged = [list(ranges[0])]
    for first, last in ranges[1:]:
        if first <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], last)
        else:
            merged.append([first, last])
    return [r[0] for r in merged], [r[1] for r in merged]


def _read_cmap(data: bytes) -> list:
    count = struct.unpack_from('>H', data, 2)[0]
    subtables = {}
    for index in range(count):
        platform, encoding, offset = struct.unpack_from('>HHI', data, 4 + 8 * index)
        subtables[(platform, encoding)] = offset
    # The full Unicode map where there is one, else the basic plane's.
    for key in ((3, 10), (0, 6), (0, 4), (3, 1), (0, 3), (0, 2), (0, 1), (0, 0)):
        if key in subtables:
            offset = subtables[key]
            kind = struct.unpack_from('>H', data, offset)[0]
            if kind == 12:
                return _format_12(data, offset)
            if kind == 4:
                return _format_4(data, offset)
    return []


def _format_12(data: bytes, offset: int) -> list:
    groups = struct.unpack_from('>I', data, offset + 12)[0]
    return [struct.unpack_from('>II', data, offset + 16 + 12 * i) for i in range(groups)]


def _format_4(data: bytes, offset: int) -> list:
    segments = struct.unpack_from('>H', data, offset + 6)[0] // 2
    ends = struct.unpack_from(f'>{segments}H', data, offset + 14)
    starts_at = offset + 16 + 2 * segments
    starts = struct.unpack_from(f'>{segments}H', data, starts_at)
    ranges_at = starts_at + 4 * segments
    range_offsets = struct.unpack_from(f'>{segments}H', data, ranges_at)
    found = []
    for i, (first, last) in enumerate(zip(starts, ends)):
        if first == 0xFFFF:
            continue
        if range_offsets[i] == 0:
            found.append((first, last))
            continue
        # Glyphs looked up one by one: some of the range may map to none.
        for code in range(first, last + 1):
            where = ranges_at + 2 * i + range_offsets[i] + 2 * (code - first)
            if where + 2 <= len(data) and struct.unpack_from('>H', data, where)[0]:
                found.append((code, code))
    return found


# ------------------------------------------------------ the phone's fonts
def _android_fonts(listing: str):
    """(regular, bold, fallbacks) from Android's font list, or None."""
    import xml.etree.ElementTree as ElementTree
    try:
        root = ElementTree.parse(listing).getroot()
    except (OSError, ElementTree.ParseError):
        return None
    regular = bold = None
    fallbacks = []
    for family in root.iter('family'):
        fonts = [(font, os.path.join(SYSTEM_FONTS, (font.text or '').strip()))
                 for font in family.iter('font')
                 if (font.get('style') or 'normal') == 'normal'
                 and (font.get('index') or '0') == '0']
        fonts = [(font, path) for font, path in fonts if os.path.isfile(path)]
        if not fonts:
            continue
        by_weight = {int(font.get('weight') or 400): path for font, path in fonts}
        if family.get('name') == 'sans-serif' and regular is None:
            regular = by_weight.get(400) or fonts[0][1]
            bold = by_weight.get(700)
        elif family.get('name') is None:
            path = by_weight.get(400) or fonts[0][1]
            # Colour emoji are bitmaps, which Kivy cannot draw.
            if 'Emoji' not in os.path.basename(path) and path not in fallbacks:
                fallbacks.append(path)
    if regular:
        return regular, bold, fallbacks
    return None


class Fonts:
    """Which font draws each character: the system's where it can, then
    Android's fallbacks in Android's order, then Kivy's own DejaVu Sans."""

    def __init__(self, regular, bold, chain, remember=None, found=None):
        self.regular, self.bold, self.chain = regular, bold, chain
        self._maps = {}
        self._found = dict(found or {})          # code point -> font path, or ''
        self._remember = remember
        self._lock = threading.Lock()

    def discard(self, path: str) -> None:
        """A fallback Kivy could not open: never offered again."""
        self._maps[path] = None
        # What it was to draw is looked for again, further down the chain.
        self._found = {code: found for code, found in self._found.items() if found != path}

    def has(self, path: str, code: int) -> bool:
        if path not in self._maps:
            self._maps[path] = coverage(path)
        found = self._maps[path]
        if found is None:
            return False
        starts, ends = found
        i = bisect.bisect_right(starts, code) - 1
        return i >= 0 and code <= ends[i]

    def font_for(self, code: int, primary: str):
        """The font to draw a character with, primary if it has it; '' to
        leave it out, None to draw it in primary anyway (as a box)."""
        if code < 0x80 or self.has(primary, code):
            return primary
        if code in INVISIBLE:
            return ''
        with self._lock:
            if code not in self._found:
                self._found[code] = next((path for path in self.chain if self.has(path, code)), '')
                if self._remember is not None:
                    self._remember(self._found)
            path = self._found[code]
        if path:
            return path
        return '' if _emoji(code) else None

    def runs(self, text: str, primary: str) -> list:
        """[(font path, text)] - the text in pieces, each for one font."""
        pieces = []
        for char in text:
            path = self.font_for(ord(char), primary)
            if path == '':
                continue
            path = path or primary
            if pieces and pieces[-1][0] == path:
                pieces[-1][1].append(char)
            else:
                pieces.append((path, [char]))
        return [(path, ''.join(chars)) for path, chars in pieces]


def _kivy_font(name: str) -> str:
    import kivy
    return os.path.join(os.path.dirname(kivy.__file__), 'data', 'fonts', name)


def _load(cache_path) -> Fonts:
    dejavu = _kivy_font('DejaVuSans.ttf')
    kivy_bold = _kivy_font('Roboto-Bold.ttf')
    key = None
    for listing in FONT_LISTS:
        try:
            stamp = os.stat(listing)
        except OSError:
            continue
        key = [listing, stamp.st_mtime, stamp.st_size]
        break
    if key is None:
        # Not a phone: Kivy's own Roboto, much as a phone's, with DejaVu
        # behind it - so the fallback is at work in the preview too.
        return Fonts(_kivy_font('Roboto-Regular.ttf'), kivy_bold, [dejavu])

    saved = {}
    try:
        with open(cache_path, encoding='utf-8') as handle:
            saved = json.load(handle)
    except (OSError, ValueError):
        pass
    if isinstance(saved, dict) and saved.get('key') == key and saved.get('fonts'):
        regular, bold, fallbacks = saved['fonts']
        remembered = {int(code): path for code, path in saved.get('found', {}).items()}
    else:
        found = _android_fonts(key[0])
        if found is None:
            return Fonts(_kivy_font('Roboto-Regular.ttf'), kivy_bold, [dejavu])
        regular, bold, fallbacks = found
        remembered = {}
    fonts = [regular, bold, fallbacks]

    def remember(found):
        try:
            tmp = f'{cache_path}.tmp'
            with open(tmp, 'w', encoding='utf-8') as handle:
                json.dump({'key': key, 'fonts': fonts, 'found': found}, handle)
            os.replace(tmp, cache_path)
        except OSError:
            pass

    if not remembered:
        remember({})
    # One file for every weight is a variable font, which Kivy can only draw
    # at its default weight: bold comes from Kivy's own Roboto instead.
    if not bold or bold == regular:
        bold = kivy_bold
    return Fonts(regular, bold, [*fallbacks, dejavu], remember, remembered)


FONTS = None


def use_system_font(cache_path) -> Fonts:
    """Make the phone's own font Kivy's default, with its fallbacks behind it.
    Before any label is drawn. Should the phone's font be one Kivy cannot
    open, Kivy's own Roboto stands in - text in a font not quite the phone's
    rather than no text at all."""
    global FONTS
    from kivy.core.text import DEFAULT_FONT, LabelBase
    try:
        FONTS = _load(str(cache_path))
        LabelBase.register(DEFAULT_FONT, FONTS.regular, None, FONTS.bold, None)
        for bold in (False, True):
            _probe(bold)
    except Exception:
        logging.getLogger(__name__).exception('the phone font cannot be used; Roboto instead')
        FONTS = Fonts(_kivy_font('Roboto-Regular.ttf'), _kivy_font('Roboto-Bold.ttf'),
                      [*(FONTS.chain if FONTS else []), _kivy_font('DejaVuSans.ttf')])
        LabelBase.register(DEFAULT_FONT, FONTS.regular, None, FONTS.bold, None)
    _teach_kivy(FONTS)
    return FONTS


def _probe(bold: bool) -> None:
    """Draw a word in the default font, which raises if Kivy cannot open it."""
    from kivy.core.text import Label as CoreLabel
    word = CoreLabel(text='Grabbit', font_size=12, bold=bold)
    word.refresh()


def _teach_kivy(fonts: Fonts) -> None:
    """Measure and draw text a run at a time, each in its own font.

    Every label - markup ones too - measures with get_extents and draws with
    _render_text, and both use whichever font the label's options name, so
    naming each run's font in turn is all it takes. A run in another font is
    moved to sit on the same baseline."""
    try:
        from kivy.core.text.text_sdl2 import LabelSDL2
    except ImportError:
        return
    if getattr(LabelSDL2, 'grabbit_fonts', None) is not None:
        LabelSDL2.grabbit_fonts = fonts
        return
    measure, draw = LabelSDL2.get_extents, LabelSDL2._render_text
    LabelSDL2.grabbit_fonts = fonts

    def get_extents(self, text):
        if text.isascii():
            return measure(self, text)
        primary = self.options['font_name_r']
        pieces = LabelSDL2.grabbit_fonts.runs(text, primary)
        if len(pieces) == 1 and pieces[0][0] == primary:
            return measure(self, pieces[0][1])
        width, height = 0, measure(self, ' ')[1]
        for path, piece in pieces:
            width += _in_font(self, path, primary, lambda: measure(self, piece)[0])
        return width, height

    def _render_text(self, text, x, y):
        if text.isascii():
            return draw(self, text, x, y)
        primary = self.options['font_name_r']
        pieces = LabelSDL2.grabbit_fonts.runs(text, primary)
        if len(pieces) == 1 and pieces[0][0] == primary:
            return draw(self, pieces[0][1], x, y)
        ascent = self.get_ascent()

        def drawn(piece, x):
            width = measure(self, piece)[0]
            draw(self, piece, x, y + ascent - self.get_ascent())
            return width

        for path, piece in pieces:
            x += _in_font(self, path, primary, lambda: drawn(piece, x))

    LabelSDL2.get_extents = get_extents
    LabelSDL2._render_text = _render_text


def _in_font(label, path, primary, work):
    """work() with the label set to another font for the moment - or, if
    Kivy cannot open that font, in the label's own, and that font is not
    tried again."""
    if path == primary:
        return work()
    label.options['font_name_r'] = path
    try:
        return work()
    except Exception:
        label.grabbit_fonts.discard(path)
    finally:
        label.options['font_name_r'] = primary
    return work()

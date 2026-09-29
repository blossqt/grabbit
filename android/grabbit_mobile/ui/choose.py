"""What to make of a link, asked once it is known what the link is.

The desktop shows a card for every pasted link, holding the choices that link
allows; this is that card, full screen. A video can be kept as a video at a
chosen quality, as its sound, as a GIF, or as one picture - chosen by
scrubbing through it, with the frame under the slider read straight from the
stream (grabbit.frames), so nothing is downloaded to find it. A post of several
photos and videos, or a playlist, shows them all in a grid, to untick the ones
not wanted.
"""

import logging
import queue
import threading
import urllib.request
from io import BytesIO

from kivy.clock import Clock
from kivy.core.image import Image as CoreImage
from kivy.core.window import Window
from kivy.graphics import Color, Ellipse, InstructionGroup, Line, RoundedRectangle, Triangle
from kivy.metrics import dp
from kivy.properties import NumericProperty
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.modalview import ModalView
from kivy.uix.scrollview import ScrollView
from kivy.uix.stacklayout import StackLayout
from kivy.uix.widget import Widget

from grabbit import analyze as analyze_mod
from grabbit import frames
from grabbit.tasks import State
from grabbit.util import human_duration, human_size, site_name

from . import theme
from .widgets import Card, FlatButton, Option

log = logging.getLogger(__name__)

TYPES = [('video', 'Video'), ('audio', 'Audio'), ('gif', 'GIF'), ('image', 'Image')]
CONTAINERS = [('mp4', 'MP4'), ('mkv', 'MKV'), ('any', 'Original')]
AUDIO = [('audio_mp3', 'MP3'), ('audio_m4a', 'M4A')]

# A browser's name, for sites that turn away anything else asking for a picture.
_AGENT = ('Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 '
          '(KHTML, like Gecko) Chrome/141.0.0.0 Mobile Safari/537.36')


def media_items(analysis) -> list:
    """The videos and sounds behind a link - what the type choice applies to."""
    probe = getattr(analysis, 'probe', None)
    if probe is None or analysis.kind not in (analyze_mod.KIND_MEDIA, analyze_mod.KIND_GALLERY,
                                              analyze_mod.KIND_PLAYLIST):
        return []
    return [item for item in probe.items
            if not item.optional and not (item.kind == 'image' and item.direct_url)]


def post_items(analysis) -> list:
    """(place, item) for each photo or video in a post or playlist - what is
    ticked or not in its grid. A slideshow's soundtrack is left to the PC."""
    probe = getattr(analysis, 'probe', None)
    if probe is None or analysis.kind not in (analyze_mod.KIND_GALLERY,
                                              analyze_mod.KIND_PLAYLIST):
        return []
    return [(place, item) for place, item in enumerate(probe.items) if not item.optional]


def summary(analysis) -> tuple:
    """(title, one line about it) for any kind of link."""
    kind = analysis.kind
    probe = getattr(analysis, 'probe', None)
    if kind == analyze_mod.KIND_ERROR:
        return analysis.url, analysis.error or 'That link could not be read'
    if kind == analyze_mod.KIND_MAGNET:
        info = analysis.magnet or {}
        return (info.get('name') or 'Magnet link',
                'BitTorrent · the file list comes from the swarm first')
    if kind == analyze_mod.KIND_TORRENT and analysis.torrent_info is not None:
        meta = analysis.torrent_info
        count = len(meta.files)
        return meta.name or 'Torrent', (f'BitTorrent · {count} file{"s" if count != 1 else ""}'
                                        f' · {human_size(meta.total_size)}')
    if kind == analyze_mod.KIND_METALINK:
        files = analysis.metalink_files
        total = sum(f.size for f in files)
        return (files[0].name if len(files) == 1 else f'{len(files)} files',
                ' · '.join(b for b in ('Metalink', human_size(total)) if b))
    if kind == analyze_mod.KIND_FILE:
        bits = [analysis.content_type or 'File', human_size(analysis.filesize),
                site_name(analysis.url)]
        return analysis.filename or analysis.url, ' · '.join(b for b in bits if b)
    if probe is None:
        return analysis.title or analysis.url, ''
    bits = [probe.site or site_name(analysis.url), probe.uploader]
    if kind == analyze_mod.KIND_MEDIA:
        item = probe.items[0]
        bits.append(human_duration(item.duration))
        if probe.is_live:
            bits.append('LIVE')
    else:
        photos = sum(1 for i in probe.items if i.kind == 'image')
        videos = sum(1 for i in probe.items if i.kind == 'video')
        sounds = sum(1 for i in probe.items if i.kind == 'audio' and not i.optional)
        for count, word in ((photos, 'photo'), (videos, 'video'), (sounds, 'sound')):
            if count:
                bits.append(f'{count} {word}{"s" if count != 1 else ""}')
    return probe.title or analysis.url, ' · '.join(b for b in bits if b)


def picture_url(analysis) -> tuple:
    """(url, headers) of the picture that best stands for a link, if any."""
    probe = getattr(analysis, 'probe', None)
    if probe is None or not probe.items:
        return '', {}
    item = probe.items[0]
    return (item.preview or item.thumbnail or probe.thumbnail), (item.headers or {})


def fetch_picture(url: str, headers: dict) -> bytes:
    """A picture's bytes, with the certificate bundle Android does not supply."""
    from grabbit import updates
    request = urllib.request.Request(url, headers={'User-Agent': _AGENT, **(headers or {})})
    with urllib.request.urlopen(request, timeout=20, context=updates.ssl_context()) as response:
        return response.read(8 << 20)


PNG = b'\x89PNG\r\n\x1a\n'
JPEG = b'\xff\xd8\xff'


def texture_from(data: bytes):
    """A texture for a JPEG or PNG; made on the interface thread, as Kivy needs."""
    return CoreImage(BytesIO(data), ext='png' if data.startswith(PNG) else 'jpg').texture


def drawable(data: bytes) -> bytes:
    """A picture in a form Kivy reads everywhere.

    That is JPEG and PNG. Anything else - WebP, which YouTube likes for
    thumbnails, or AVIF - goes through the FFmpeg the app carries anyway and
    comes out a JPEG. Slow enough to belong on a worker thread.
    """
    return data if data.startswith((PNG, JPEG)) else _to_jpeg(data)


def small(data: bytes, side: int) -> bytes:
    """A picture no bigger than a tile needs: its shorter side at most `side`
    pixels. Some sites only offer the full photo, and a grid of those, drawn
    whole, would take the memory of a hundred screens."""
    return _to_jpeg(data, side)


def _to_jpeg(data: bytes, side: int = 0) -> bytes:
    import subprocess
    from grabbit.paths import find_tool
    from grabbit.util import CREATE_NO_WINDOW
    ffmpeg = find_tool('ffmpeg')
    if not ffmpeg:
        return data
    shrink = ['-vf', f"scale=w='if(lt(iw,ih),min({side},iw),-2)':h='if(lt(iw,ih),-2,min({side},ih))'"
              ] if side else []
    result = subprocess.run([ffmpeg, '-v', 'error', '-i', 'pipe:0', '-frames:v', '1', *shrink,
                             '-f', 'image2pipe', '-c:v', 'mjpeg', '-q:v', '3', 'pipe:1'],
                            input=data, capture_output=True, timeout=30,
                            creationflags=CREATE_NO_WINDOW)
    return result.stdout or data


class Tile(ButtonBehavior, FloatLayout):
    """One photo or video of a post, ticked to be saved or not - a touch
    turns it over. Square, its picture cropped to fill it, and dimmed while
    it is not wanted, as the PC's grid does it."""

    def __init__(self, place, item, on_toggle, **kwargs):
        super().__init__(size_hint_y=None, **kwargs)
        self.place = place
        self.item = item
        self.chosen = True
        self._on_toggle = on_toggle
        radius = [dp(8)]
        with self.canvas.before:
            Color(*theme.BASE)
            self._ground = RoundedRectangle(radius=radius)
            self._picture_color = Color(1, 1, 1, 0)
            self._picture = RoundedRectangle(radius=radius)
            self._veil_color = Color(0, 0, 0, 0)
            self._veil = RoundedRectangle(radius=radius)
        with self.canvas.after:
            self._edge_color = Color(*theme.BLUE)
            self._edge = Line(width=dp(1.2))
            self._mark = InstructionGroup()
        if item.kind in ('video', 'audio'):
            self.badge = Label(text=human_duration(item.duration) if item.duration else '',
                               color=(1, 1, 1, 1), font_size=dp(11), size_hint=(None, None),
                               height=dp(18))
            self.badge.bind(texture_size=lambda label, size: setattr(
                label, 'width', size[0] + dp(22)))
            with self.badge.canvas.before:
                Color(0, 0, 0, 0.6)
                self._pill = RoundedRectangle(radius=[dp(9)])
                Color(1, 1, 1, 1)
                self._play = Triangle()
            self.badge.bind(pos=self._place_badge, size=self._place_badge)
            self.badge.padding = [dp(16), 0, dp(6), 0]
            self.add_widget(self.badge)
        self.bind(width=lambda _, width: setattr(self, 'height', width),
                  pos=self._redraw, size=self._redraw)

    def _place_badge(self, *_):
        badge = self.badge
        self._pill.pos, self._pill.size = badge.pos, badge.size
        x, middle = badge.x + dp(7), badge.center_y
        self._play.points = [x, middle - dp(4), x, middle + dp(4), x + dp(7), middle]

    def _redraw(self, *_):
        for shape in (self._ground, self._picture, self._veil):
            shape.pos, shape.size = self.pos, self.size
        if hasattr(self, 'badge'):
            self.badge.pos = (self.x + dp(6), self.y + dp(6))
        self._show_choice()

    def show_picture(self, data: bytes):
        """The picture, cropped to the middle square of it."""
        try:
            texture = texture_from(data)
        except Exception as exc:
            log.info('a tile could not be shown: %s', exc)
            return
        width, height = texture.size
        side = min(width, height)
        self._picture.texture = texture.get_region((width - side) / 2, (height - side) / 2,
                                                   side, side)
        self._picture_color.a = 1

    def on_release(self):
        self.set_chosen(not self.chosen)
        self._on_toggle()

    def set_chosen(self, chosen: bool):
        self.chosen = chosen
        self._show_choice()

    def _show_choice(self):
        self._veil_color.a = 0 if self.chosen else 0.45
        self._edge_color.a = 1 if self.chosen else 0
        self._edge.rounded_rectangle = (self.x, self.y, self.width, self.height, dp(8))
        # The mark in the corner: a ring, ticked and filled in once chosen.
        # (Not self.right and self.top: mid-layout, they still say the old size.)
        size = dp(22)
        x, y = self.x + self.width - size - dp(6), self.y + self.height - size - dp(6)
        self._mark.clear()
        if self.chosen:
            self._mark.add(Color(*theme.BLUE))
            self._mark.add(Ellipse(pos=(x, y), size=(size, size)))
            self._mark.add(Color(1, 1, 1, 1))
            self._mark.add(Line(points=[x + size * 0.28, y + size * 0.50, x + size * 0.44,
                                        y + size * 0.35, x + size * 0.72, y + size * 0.64],
                                width=dp(1.1)))
        else:
            self._mark.add(Color(0, 0, 0, 0.35))
            self._mark.add(Ellipse(pos=(x, y), size=(size, size)))
            self._mark.add(Color(1, 1, 1, 0.9))
            self._mark.add(Line(circle=(x + size / 2, y + size / 2, size / 2 - dp(1)),
                                width=dp(1.1)))


class TileLoader:
    """Fetches the tiles' pictures, a few at a time and in order, off the
    interface thread; each is shrunk to the tile before it is drawn."""

    WORKERS = 3

    def __init__(self, side: int):
        self.side = side
        self.closed = False
        self._waiting = queue.Queue()
        for number in range(self.WORKERS):
            threading.Thread(target=self._work, name=f'grabbit-tiles-{number}',
                             daemon=True).start()

    def want(self, tile):
        item = tile.item
        url = item.thumbnail or item.preview or (item.direct_url if item.kind == 'image' else '')
        if url:
            self._waiting.put((tile, url, item.headers or {}))

    def _work(self):
        while True:
            job = self._waiting.get()
            if job is None or self.closed:
                return
            tile, url, headers = job
            try:
                data = small(fetch_picture(url, headers), self.side)
            except Exception as exc:
                log.info('no picture for a tile, %s: %s', url[:80], exc)
                continue
            if not self.closed:
                Clock.schedule_once(lambda _, t=tile, d=data: t.show_picture(d))

    def close(self):
        self.closed = True
        for _ in range(self.WORKERS):
            self._waiting.put(None)


class Scrubber(Widget):
    """A slider along a video. Dragging it moves `value` (in seconds); where it
    is let go - or where the finger rests - is where a frame is fetched."""

    value = NumericProperty(0.0)
    duration = NumericProperty(0.0)

    def __init__(self, on_rest=None, **kwargs):
        super().__init__(**kwargs)
        self.size_hint_y = None
        self.height = dp(40)             # a thumb's height, though it draws thinner
        self._on_rest = on_rest
        self._rest = None
        with self.canvas:
            Color(*theme.BAR)
            self._track = RoundedRectangle(radius=[dp(2)])
            Color(*theme.BLUE)
            self._fill = RoundedRectangle(radius=[dp(2)])
            Color(1, 1, 1, 1)
            self._knob = Ellipse()
        self.bind(pos=self._redraw, size=self._redraw, value=self._redraw,
                  duration=self._redraw)

    @property
    def _span(self):
        return self.x + dp(12), max(1.0, self.width - dp(24))

    def _redraw(self, *_):
        start, span = self._span
        middle = self.center_y
        fraction = (self.value / self.duration) if self.duration else 0.0
        self._track.pos = (start, middle - dp(2))
        self._track.size = (span, dp(4))
        self._fill.pos = (start, middle - dp(2))
        self._fill.size = (span * fraction, dp(4))
        knob = dp(20)
        self._knob.pos = (start + span * fraction - knob / 2, middle - knob / 2)
        self._knob.size = (knob, knob)

    def _follow(self, x):
        start, span = self._span
        fraction = max(0.0, min(1.0, (x - start) / span))
        self.value = fraction * self.duration
        # Fetch where the finger stops, not everywhere it passes through.
        if self._rest is not None:
            self._rest.cancel()
        self._rest = Clock.schedule_once(lambda _: self._rested(), 0.25)

    def _rested(self):
        self._rest = None
        if self._on_rest:
            self._on_rest(self.value)

    def _hits(self, touch) -> bool:
        """A finger at either end of the track still counts, just past it."""
        return (self.x - dp(12) <= touch.x <= self.right + dp(12)
                and self.y <= touch.y <= self.top)

    def on_touch_down(self, touch):
        if not self._hits(touch) or not self.duration:
            return super().on_touch_down(touch)
        touch.grab(self)
        self._follow(touch.x)
        return True

    def on_touch_move(self, touch):
        if touch.grab_current is self:
            self._follow(touch.x)
            return True
        return super().on_touch_move(touch)

    def on_touch_up(self, touch):
        if touch.grab_current is self:
            touch.ungrab(self)
            self._follow(touch.x)
            return True
        return super().on_touch_up(touch)


def _heading(text):
    label = Label(text=text, color=theme.DIM, font_size=dp(12), bold=True,
                  size_hint_y=None, height=dp(22), halign='left', valign='bottom')
    label.bind(size=lambda widget, value: setattr(widget, 'text_size', value))
    return label


class _Choosing:
    """A group of Options where exactly one is chosen."""

    def _fill(self, options, chosen, on_choose, equal):
        self.chosen = chosen
        self._on_choose = on_choose
        self.buttons = {}
        for value, label in options:
            button = Option(text=label, selected=(value == chosen))
            if equal:
                button.size_hint_x = 1        # an equal share of the row
            button.bind(on_release=lambda _, v=value: self.choose(v))
            self.buttons[value] = button
            self.add_widget(button)

    def choose(self, value):
        self.chosen = value
        for option, button in self.buttons.items():
            button.set_selected(option == value)
        if self._on_choose:
            self._on_choose(value)


class OptionRow(_Choosing, BoxLayout):
    """Options sharing one row equally: Video, Audio, GIF, Image."""

    def __init__(self, options, chosen, on_choose, **kwargs):
        super().__init__(size_hint_y=None, height=dp(34), spacing=dp(6), **kwargs)
        self._fill(options, chosen, on_choose, equal=True)


class OptionWrap(_Choosing, StackLayout):
    """Options as wide as their words, wrapping onto more lines: the qualities."""

    def __init__(self, options, chosen, on_choose, **kwargs):
        super().__init__(size_hint_y=None, spacing=dp(8), **kwargs)
        self.bind(minimum_height=self.setter('height'))
        self._fill(options, chosen, on_choose, equal=False)


class ChoosePage(ModalView):
    """Full screen: the link, what it is, and the choices it allows.

    Opened as soon as Download is touched, reading the link; show() fills it in
    when the answer comes. on_choose gets a dict of the choice - quality,
    container, and for a picture the moment and the file type - or an empty
    one for links that offer no choice.
    """

    def __init__(self, url, settings, on_choose, insets=(0, 0), **kwargs):
        super().__init__(size_hint=(1, 1), background='', background_color=theme.WINDOW,
                         overlay_color=(0, 0, 0, 0), auto_dismiss=True, **kwargs)
        self.url = url
        self.settings = settings
        self.analysis = None
        self._on_choose = on_choose
        self._reader = None
        self._frame_texture = None
        self._picture_texture = None
        self.kind = ''
        self.quality = settings.video_quality or 'best'
        self.container = settings.video_container or 'mp4'
        self.audio = self.quality if self.quality.startswith('audio') else 'audio_mp3'
        self.frame_format = getattr(settings, 'frame_format', '') or 'png'
        self.frame_at = 0.0
        self.fps = 0.0
        self.tiles = []
        self._tile_loader = None

        top, bottom = insets
        self.column = BoxLayout(orientation='vertical', spacing=dp(10),
                                padding=[dp(18), dp(10) + top, dp(18), dp(14) + bottom])
        self.add_widget(self.column)

        header = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(4))
        back = FlatButton(text='‹', font_size=dp(30), size_hint_x=None, width=dp(40),
                          color=theme.TEXT, fill=theme.TRANSPARENT)
        back.bind(on_release=lambda *_: self.dismiss())
        self.heading = Label(text='Download', color=theme.TEXT, font_size=dp(18), bold=True,
                             halign='left', valign='middle')
        self.heading.bind(size=lambda widget, value: setattr(widget, 'text_size', value))
        header.add_widget(back)
        header.add_widget(self.heading)
        self.column.add_widget(header)

        # The picture: the link's thumbnail, or the frame being chosen.
        self.picture_card = Card(radius=12, fill=theme.BASE, size_hint_y=None)
        self.picture_box = FloatLayout()
        self.picture = Image(fit_mode='contain', opacity=0,
                             size_hint=(1, 1), pos_hint={'x': 0, 'y': 0})
        self.picture_note = Label(text='', color=theme.DIM, font_size=dp(13),
                                  size_hint=(1, 1), pos_hint={'x': 0, 'y': 0},
                                  halign='center', valign='middle')
        self.picture_note.bind(size=lambda widget, value: setattr(widget, 'text_size', value))
        self.picture_box.add_widget(self.picture)
        self.picture_box.add_widget(self.picture_note)
        self.picture_card.add_widget(self.picture_box)
        self.column.add_widget(self.picture_card)
        self.column.bind(width=self._size_picture)

        self.title = Label(text=url, color=theme.TEXT, font_size=dp(15), bold=True,
                           size_hint_y=None, halign='left', valign='top',
                           max_lines=2, shorten=True, shorten_from='right')
        self.title.bind(width=lambda widget, width: setattr(widget, 'text_size', (width, None)),
                        texture_size=lambda widget, size: setattr(widget, 'height', size[1]))
        self.about = Label(text='Reading the link…', color=theme.DIM, font_size=dp(12),
                           size_hint_y=None, halign='left', valign='top')
        self.about.bind(width=lambda widget, width: setattr(widget, 'text_size', (width, None)),
                        texture_size=lambda widget, size: setattr(widget, 'height', size[1]))
        self.column.add_widget(self.title)
        self.column.add_widget(self.about)

        # The choices, rebuilt for each kind of link and each type.
        self.choices = BoxLayout(orientation='vertical', spacing=dp(8), size_hint_y=None)
        self.choices.bind(minimum_height=self.choices.setter('height'))
        self.column.add_widget(self.choices)
        self.filler = Widget()                       # everything else sits at the top
        self.column.add_widget(self.filler)

        buttons = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(10))
        cancel = FlatButton(text='Cancel', font_size=dp(15), color=theme.TEXT, fill=theme.HOVER)
        cancel.bind(on_release=lambda *_: self.dismiss())
        self.go = FlatButton(text='Download', font_size=dp(15), color=(1, 1, 1, 1),
                             fill=theme.BLUE)
        self.go.bind(on_release=lambda *_: self.confirm())
        buttons.add_widget(cancel)
        buttons.add_widget(self.go)
        self.column.add_widget(buttons)
        self._enable(False)

    # ------------------------------------------------------------- layout
    def _size_picture(self, *_):
        width = self.column.width - self.column.padding[0] - self.column.padding[2]
        self.picture_card.height = min(width * 9 / 16, Window.height * 0.3)

    def _enable(self, enabled: bool):
        self.go.disabled = not enabled
        self.go.set_fill(theme.BLUE if enabled else theme.HOVER)
        self.go.color = (1, 1, 1, 1) if enabled else theme.DIM

    # --------------------------------------------------------------- fill
    def show(self, analysis):
        """The link has been read: say what it is, and offer its choices."""
        self.analysis = analysis
        title, about = summary(analysis)
        self.title.text = title
        self.about.text = about
        failed = analysis.kind == analyze_mod.KIND_ERROR
        self.about.color = theme.state_color(State.ERROR) if failed else theme.DIM
        self.picture_note.text = ''
        pieces = post_items(analysis)
        url, headers = picture_url(analysis)
        if len(pieces) > 1:
            self._show_grid(pieces)             # the pictures are in the grid
        elif url:
            self.picture_note.text = ''
            threading.Thread(target=self._load_picture, args=(url, headers),
                             name='grabbit-thumbnail', daemon=True).start()
        else:
            self.picture_note.text = self._glyph_for(analysis.kind)

        self.items = media_items(analysis)
        self.choices.clear_widgets()
        if self.items:
            single = analysis.kind == analyze_mod.KIND_MEDIA
            item = self.items[0]
            audio_only = single and item.kind == 'audio'
            offered = [('audio', 'Audio')] if audio_only else [
                t for t in TYPES if t[0] != 'image' or single]
            self.kind = 'audio' if audio_only else self._kind_from(self.quality)
            self.heights = sorted({h for i in self.items for h in (i.heights or [])},
                                  reverse=True)
            self.choices.add_widget(_heading('SAVE AS'))
            self.type_row = OptionRow(offered, self.kind, self.choose_kind)
            self.choices.add_widget(self.type_row)
            self.detail = BoxLayout(orientation='vertical', spacing=dp(8), size_hint_y=None)
            self.detail.bind(minimum_height=self.detail.setter('height'))
            self.choices.add_widget(self.detail)
            self.choose_kind(self.kind)
        elif failed and analysis.url.lower().startswith(('http://', 'https://')):
            self.go.text = 'Save as a file'
        if self.tiles:
            self.choices.add_widget(self.tick_row)
        self._enable(not failed or analysis.url.lower().startswith(('http://', 'https://')))
        if self.tiles:
            self._count_ticked()

    # --------------------------------------------------------------- grid
    def _show_grid(self, pieces):
        """Every photo and video of a post in place of its one picture, three
        across and scrolling, each ticked to begin with."""
        self.column.remove_widget(self.picture_card)
        grid = GridLayout(cols=3, spacing=dp(6), size_hint_y=None)
        grid.bind(minimum_height=grid.setter('height'))
        self.tiles = [Tile(place, item, on_toggle=self._count_ticked) for place, item in pieces]
        for tile in self.tiles:
            grid.add_widget(tile)
        self.grid_scroller = ScrollView(do_scroll_x=False, bar_width=dp(3),
                                        bar_color=theme.DIM, bar_inactive_color=theme.BORDER)
        self.grid_scroller.add_widget(grid)
        place = self.column.children.index(self.filler)
        self.column.remove_widget(self.filler)
        self.column.add_widget(self.grid_scroller, index=place)

        # How many are ticked, and a way to tick or untick them all at once.
        self.tick_row = BoxLayout(size_hint_y=None, height=dp(36), spacing=dp(8))
        self.ticked_label = Label(text='', color=theme.TEXT, font_size=dp(14),
                                  halign='left', valign='middle')
        self.ticked_label.bind(size=lambda widget, value: setattr(widget, 'text_size', value))
        self.tick_all_button = FlatButton(text='Deselect', font_size=dp(14), color=theme.TEXT,
                                          size_hint_x=None, width=dp(108))
        self.tick_all_button.bind(on_release=lambda *_: self.tick_all())
        self.tick_row.add_widget(self.ticked_label)
        self.tick_row.add_widget(self.tick_all_button)

        self._tile_loader = TileLoader(side=int(max(Window.width / 2, dp(120))))
        for tile in self.tiles:
            self._tile_loader.want(tile)

    def tick_all(self):
        everything = all(tile.chosen for tile in self.tiles)
        for tile in self.tiles:
            tile.set_chosen(not everything)
        self._count_ticked()

    def _count_ticked(self):
        ticked = sum(1 for tile in self.tiles if tile.chosen)
        self.ticked_label.text = f'{ticked} of {len(self.tiles)} selected'
        self.tick_all_button.text = 'Deselect' if ticked == len(self.tiles) else 'Select all'
        self._enable(ticked > 0)

    @staticmethod
    def _glyph_for(kind):
        return {analyze_mod.KIND_MAGNET: 'Magnet link', analyze_mod.KIND_TORRENT: 'Torrent',
                analyze_mod.KIND_METALINK: 'Metalink', analyze_mod.KIND_FILE: 'File',
                analyze_mod.KIND_ERROR: ''}.get(kind, '')

    @staticmethod
    def _kind_from(quality: str) -> str:
        if quality.startswith('audio'):
            return 'audio'
        if quality == 'gif':
            return 'gif'
        return 'video'

    def choose_kind(self, kind: str):
        self.kind = kind
        self.detail.clear_widgets()
        if kind == 'video':
            offered = [('best', 'Best')] + [(str(h), f'{h}p') for h in self.heights[:7]]
            if self.quality not in dict(offered):
                self.quality = 'best'
            self.detail.add_widget(_heading('QUALITY'))
            self.quality_row = OptionWrap(offered, self.quality, self._set('quality'))
            self.detail.add_widget(self.quality_row)
            self.detail.add_widget(_heading('FILE TYPE'))
            self.container_row = OptionWrap(CONTAINERS, self.container, self._set('container'))
            self.detail.add_widget(self.container_row)
        elif kind == 'audio':
            self.detail.add_widget(_heading('FILE TYPE'))
            self.audio_row = OptionWrap(AUDIO, self.audio, self._set('audio'))
            self.detail.add_widget(self.audio_row)
        elif kind == 'gif':
            seconds = int(getattr(self.settings, 'gif_max_seconds', 30) or 0)
            note = Label(text=(f'The first {seconds} seconds' if seconds else 'The whole video')
                         + f', {getattr(self.settings, "gif_width", 480)} pixels wide, '
                           f'{getattr(self.settings, "gif_fps", 15)} frames a second.',
                         color=theme.DIM, font_size=dp(13), size_hint_y=None,
                         halign='left', valign='top')
            note.bind(width=lambda widget, width: setattr(widget, 'text_size', (width, None)),
                      texture_size=lambda widget, size: setattr(widget, 'height', size[1]))
            self.detail.add_widget(note)
        elif kind == 'image':
            self._build_scrubber()
        if kind != 'image':
            self._show_picture()

    def _set(self, name):
        return lambda value: setattr(self, name, value)

    # ---------------------------------------------------------- one frame
    def _build_scrubber(self):
        item = self.items[0]
        self.detail.add_widget(_heading('CHOOSE THE FRAME'))
        self.scrubber = Scrubber(on_rest=self._want_frame)
        self.scrubber.duration = float(item.duration or 0)
        self.scrubber.value = min(self.frame_at, self.scrubber.duration or self.frame_at)
        self.scrubber.bind(value=lambda *_: self._show_clock())
        self.detail.add_widget(self.scrubber)

        steps = BoxLayout(size_hint_y=None, height=dp(36), spacing=dp(8))
        back = FlatButton(text='‹', font_size=dp(22), size_hint_x=None, width=dp(48),
                          color=theme.TEXT)
        back.bind(on_release=lambda *_: self._step(-1))
        self.clock = Label(text='', color=theme.TEXT, font_size=dp(14))
        forward = FlatButton(text='›', font_size=dp(22), size_hint_x=None, width=dp(48),
                             color=theme.TEXT)
        forward.bind(on_release=lambda *_: self._step(1))
        self.step_back, self.step_forward = back, forward
        for widget in (back, self.clock, forward):
            steps.add_widget(widget)
        self.detail.add_widget(steps)
        self.detail.add_widget(_heading('FILE TYPE'))
        self.frame_row = OptionRow(frames.IMAGE_TYPES, self.frame_format,
                                   self._set('frame_format'))
        self.detail.add_widget(self.frame_row)
        self._show_clock()
        if self._frame_texture is not None:
            self._show_picture(self._frame_texture)
        self._want_frame(self.scrubber.value)

    def _show_clock(self):
        self.frame_at = self.scrubber.value
        total = self.scrubber.duration
        self.clock.text = (f'{frames.clock(self.frame_at)}  /  {frames.clock(total)}'
                           if total else frames.clock(self.frame_at))

    def _step(self, direction: int):
        """One frame on or back - or a tenth of a second, when the rate is unknown."""
        step = 1.0 / self.fps if self.fps else 0.1
        limit = self.scrubber.duration or (self.frame_at + step)
        self.scrubber.value = max(0.0, min(limit, self.scrubber.value + direction * step))
        self._want_frame(self.scrubber.value)

    def _want_frame(self, seconds: float):
        if self.kind != 'image' or self.analysis is None:
            return
        if self._reader is None:
            item = self.items[0]
            self.picture_note.text = 'Finding the video…'
            self._reader = frames.FrameReader(
                item.url or self.analysis.url, self.settings,
                on_frame=lambda at, data: Clock.schedule_once(lambda _: self._got_frame(at, data)),
                on_error=lambda message: Clock.schedule_once(lambda _: self._frame_failed(message)),
                on_stream=lambda stream: Clock.schedule_once(lambda _: self._got_stream(stream)),
                width=int(max(Window.width, 640)))
        elif self._reader.stream is not None:
            self.picture_note.text = ''
        self._reader.want(seconds)
        self.picture.color = (1, 1, 1, 0.55)       # dimmed until the new frame arrives

    def _got_stream(self, stream):
        self.fps = stream.fps
        if stream.duration and not self.scrubber.duration:
            self.scrubber.duration = stream.duration
            self._show_clock()

    def _got_frame(self, at, data):
        try:
            self._frame_texture = texture_from(data)
        except Exception as exc:
            self._frame_failed(f'That frame could not be shown: {exc}')
            return
        if self.kind == 'image':
            self.picture_note.text = ''
            self._show_picture(self._frame_texture)

    def _frame_failed(self, message):
        if self.kind == 'image':
            self.picture.color = (1, 1, 1, 1)
            self.picture_note.text = message

    # ------------------------------------------------------------ picture
    def _load_picture(self, url, headers):
        try:
            data = drawable(fetch_picture(url, headers))
        except Exception as exc:
            log.info('no thumbnail for %s: %s', url, exc)
            return
        Clock.schedule_once(lambda _: self._got_picture(data))

    def _got_picture(self, data):
        try:
            self._picture_texture = texture_from(data)
        except Exception:
            log.exception('the thumbnail could not be shown')
            return
        if self.kind != 'image':
            self._show_picture()

    def _show_picture(self, texture=None):
        texture = texture or self._picture_texture
        self.picture.color = (1, 1, 1, 1)
        if texture is None:
            self.picture.opacity = 0
            return
        self.picture.texture = texture
        self.picture.opacity = 1

    # ------------------------------------------------------------- answer
    def choice(self) -> dict:
        """What was chosen, in the terms the engine takes."""
        chosen = self._how()
        if self.tiles:
            chosen['items'] = [tile.place for tile in self.tiles if tile.chosen]
        return chosen

    def _how(self) -> dict:
        if not getattr(self, 'items', None):
            return {}
        if self.kind == 'audio':
            return {'quality': self.audio, 'container': self.container}
        if self.kind == 'gif':
            return {'quality': 'gif', 'container': self.container}
        if self.kind == 'image':
            return {'quality': 'frame', 'container': self.container,
                    'frame_at': round(self.frame_at, 3), 'frame_format': self.frame_format}
        return {'quality': self.quality, 'container': self.container}

    def confirm(self):
        if self.analysis is None or self.go.disabled:
            return
        chosen = self.choice()
        # Remembered for next time, except a moment in one particular video.
        if chosen.get('quality') and chosen['quality'] != 'frame':
            self.settings.video_quality = chosen['quality']
        if chosen.get('container'):
            self.settings.video_container = chosen['container']
        if chosen.get('frame_format'):
            self.settings.frame_format = chosen['frame_format']
        self.dismiss()
        self._on_choose(self.analysis, chosen)

    def on_dismiss(self):
        if self._reader is not None:
            self._reader.close()
            self._reader = None
        if self._tile_loader is not None:
            self._tile_loader.close()
            self._tile_loader = None

"""Small pieces the rest of the interface is built from.

Kivy draws nothing by itself, so a card, a chip and a progress bar all have to
be described in canvas instructions. They are here so the screens can read as
layout rather than as drawing.
"""

from kivy.metrics import dp
from kivy.graphics import Color, Ellipse, Line, RoundedRectangle, Triangle
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from grabbit.tasks import KIND_IMAGE, KIND_MAGNET, KIND_MEDIA, KIND_TORRENT, State

from . import theme


class Card(BoxLayout):
    """A panel with the base colour and a hairline border, like #Card."""

    def __init__(self, radius=10, fill=theme.BASE, border=theme.BORDER, **kwargs):
        super().__init__(**kwargs)
        self._radius = dp(radius)
        with self.canvas.before:
            self._fill_color = Color(*fill)
            self._rect = RoundedRectangle(radius=[self._radius])
            self._border_color = Color(*border)
            self._line = Line(width=1.0)
        self.bind(pos=self._redraw, size=self._redraw)

    def set_fill(self, color):
        self._fill_color.rgba = color

    def _redraw(self, *_):
        self._rect.pos = self.pos
        self._rect.size = self.size
        self._line.rounded_rectangle = (self.x, self.y, self.width, self.height, self._radius)


class Chip(ButtonBehavior, Label):
    """One filter, on or off. The sidebar's rows, made to fit sideways."""

    def __init__(self, text='', selected=False, **kwargs):
        super().__init__(text=text, **kwargs)
        self.font_size = dp(13)
        self.size_hint_x = None
        self.height = dp(30)
        self.padding_x = dp(12)
        self.selected = selected
        with self.canvas.before:
            self._color = Color(*(theme.HOVER if selected else theme.TRANSPARENT))
            self._rect = RoundedRectangle(radius=[dp(15)])
        self.bind(pos=self._redraw, size=self._redraw, texture_size=self._resize)
        self._apply()

    def _resize(self, *_):
        if self.size_hint_x is None:
            self.width = max(dp(56), self.texture_size[0] + dp(26))

    def _redraw(self, *_):
        self._rect.pos = self.pos
        self._rect.size = self.size

    def set_selected(self, selected: bool):
        if selected != self.selected:
            self.selected = selected
            self._apply()

    def _apply(self):
        self._color.rgba = theme.HOVER if self.selected else theme.TRANSPARENT
        self.color = theme.TEXT if self.selected else theme.DIM


class FlatButton(Button):
    """A button that draws its own rounded background, because Kivy's default
    one is a grey bitmap from 2011."""

    def __init__(self, fill=theme.HOVER, radius=8, **kwargs):
        super().__init__(background_normal='', background_down='',
                         background_color=theme.TRANSPARENT, **kwargs)
        self._radius = dp(radius)
        with self.canvas.before:
            self._color = Color(*fill)
            self._rect = RoundedRectangle(radius=[self._radius])
        self.bind(pos=self._redraw, size=self._redraw)

    def set_fill(self, color):
        self._color.rgba = color

    def _redraw(self, *_):
        self._rect.pos = self.pos
        self._rect.size = self.size


class ProgressTrack(Widget):
    """The desktop's progress column: a rounded track with a coloured fill.

    Not called ProgressBar: Kivy styles widgets by class name, and its own
    ProgressBar rule would be applied to this one and then ask it for
    attributes it does not have.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.size_hint_y = None
        self.height = dp(6)
        self.fraction = 0.0
        self.tint = theme.BLUE
        with self.canvas:
            self._track_color = Color(*theme.BAR)
            self._track = RoundedRectangle(radius=[dp(3)])
            self._fill_color = Color(*self.tint)
            self._fill = RoundedRectangle(radius=[dp(3)])
        self.bind(pos=self._redraw, size=self._redraw)

    def show(self, fraction: float, tint):
        self.fraction = max(0.0, min(1.0, fraction or 0.0))
        self.tint = tint
        self._redraw()

    def _redraw(self, *_):
        radius = self.height / 2
        self._track.pos = self.pos
        self._track.size = self.size
        self._track.radius = [radius]
        self._fill_color.rgba = self.tint
        # A sliver of colour at the very start still reads as "begun".
        width = max(self.height, self.width * self.fraction) if self.fraction else 0
        self._fill.pos = self.pos
        self._fill.size = (width, self.height)
        self._fill.radius = [radius]


class KindGlyph(Widget):
    """What sort of download this is, drawn rather than shipped as an icon.

    The desktop colours its icons by state; so does this, which makes a list
    readable at a glance without any text being involved.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.size_hint = (None, None)
        self.size = (dp(22), dp(22))
        self._kind = 'file'
        self._color = theme.DIM
        self.bind(pos=self._redraw, size=self._redraw)

    def show(self, task):
        kind = task.kind
        if task.state == State.ERROR:
            self._kind = 'error'
        elif kind in (KIND_TORRENT, KIND_MAGNET):
            self._kind = 'seed' if task.state == State.SEEDING else 'torrent'
        elif kind == KIND_IMAGE:
            self._kind = 'image'
        elif kind == KIND_MEDIA:
            quality = str((task.media or {}).get('quality', ''))
            self._kind = 'audio' if quality.startswith('audio') else 'video'
        else:
            self._kind = 'file'
        self._color = theme.state_color(task.state)
        self._redraw()

    def _redraw(self, *_):
        self.canvas.clear()
        x, y = self.pos
        size = min(self.width, self.height)
        stroke = max(1.2, size * 0.085)
        with self.canvas:
            Color(*self._color)
            getattr(self, f'_draw_{self._kind}')(x, y, size, stroke)

    # Each of these draws inside a square of `size`, from its bottom left.
    def _draw_video(self, x, y, size, stroke):
        Line(rounded_rectangle=(x + size * 0.08, y + size * 0.18, size * 0.84,
                                size * 0.64, size * 0.12), width=stroke)
        Triangle(points=[x + size * 0.40, y + size * 0.32,
                         x + size * 0.40, y + size * 0.68,
                         x + size * 0.68, y + size * 0.50])

    def _draw_audio(self, x, y, size, stroke):
        Ellipse(pos=(x + size * 0.16, y + size * 0.16), size=(size * 0.3, size * 0.26))
        Line(points=[x + size * 0.44, y + size * 0.28, x + size * 0.44, y + size * 0.82],
             width=stroke)
        Line(points=[x + size * 0.44, y + size * 0.82, x + size * 0.80, y + size * 0.70],
             width=stroke)

    def _draw_image(self, x, y, size, stroke):
        Line(rounded_rectangle=(x + size * 0.1, y + size * 0.16, size * 0.8,
                                size * 0.68, size * 0.1), width=stroke)
        Ellipse(pos=(x + size * 0.24, y + size * 0.58), size=(size * 0.14, size * 0.14))
        Line(points=[x + size * 0.16, y + size * 0.34, x + size * 0.40, y + size * 0.56,
                     x + size * 0.62, y + size * 0.36, x + size * 0.84, y + size * 0.52],
             width=stroke)

    def _draw_torrent(self, x, y, size, stroke):
        Line(points=[x + size * 0.5, y + size * 0.86, x + size * 0.5, y + size * 0.22],
             width=stroke)
        Line(points=[x + size * 0.24, y + size * 0.48, x + size * 0.5, y + size * 0.18,
                     x + size * 0.76, y + size * 0.48], width=stroke)

    def _draw_seed(self, x, y, size, stroke):
        Line(points=[x + size * 0.5, y + size * 0.14, x + size * 0.5, y + size * 0.78],
             width=stroke)
        Line(points=[x + size * 0.24, y + size * 0.52, x + size * 0.5, y + size * 0.82,
                     x + size * 0.76, y + size * 0.52], width=stroke)

    def _draw_file(self, x, y, size, stroke):
        Line(points=[x + size * 0.24, y + size * 0.12, x + size * 0.24, y + size * 0.88,
                     x + size * 0.60, y + size * 0.88, x + size * 0.78, y + size * 0.68,
                     x + size * 0.78, y + size * 0.12, x + size * 0.24, y + size * 0.12],
             width=stroke)
        Line(points=[x + size * 0.60, y + size * 0.88, x + size * 0.60, y + size * 0.68,
                     x + size * 0.78, y + size * 0.68], width=stroke)

    def _draw_error(self, x, y, size, stroke):
        Line(circle=(x + size * 0.5, y + size * 0.5, size * 0.38), width=stroke)
        Line(points=[x + size * 0.5, y + size * 0.30, x + size * 0.5, y + size * 0.56],
             width=stroke)
        Line(points=[x + size * 0.5, y + size * 0.70, x + size * 0.5, y + size * 0.72],
             width=stroke * 1.4)

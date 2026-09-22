"""Small pieces the rest of the interface is built from.

Kivy draws nothing by itself, so a card, a chip and a progress bar all have to
be described in canvas instructions. They are here so the screens can read as
layout rather than as drawing.
"""

from kivy.core.text import Label as CoreLabel
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.graphics import Color, Ellipse, Line, RoundedRectangle, Triangle
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.modalview import ModalView
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


class TapLabel(ButtonBehavior, Label):
    """Text that does something when touched, and looks no different."""


class Chip(ButtonBehavior, Label):
    """One filter, on or off. The sidebar's rows, made to fit sideways."""

    def __init__(self, text='', selected=False, **kwargs):
        super().__init__(text=text, halign='center', valign='middle', shorten=True, **kwargs)
        self.font_size = dp(13)
        self.size_hint_x = None
        self.height = dp(30)
        self.padding = [dp(12), 0, dp(12), 0]
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
        if self.size_hint_x is not None:
            # A share of a row rather than the width of its words: the word
            # keeps clear of the edges, and is cut short if it has to be.
            self.padding = [0, 0, 0, 0]
            self.text_size = (max(0, self.width - 2 * BUTTON_MARGIN), self.height)

    def set_selected(self, selected: bool):
        if selected != self.selected:
            self.selected = selected
            self._apply()

    def _apply(self):
        self._color.rgba = theme.HOVER if self.selected else theme.TRANSPARENT
        self.color = theme.TEXT if self.selected else theme.DIM


def share_by_words(chips, room=dp(20)):
    """Chips splitting one row between them, each in proportion to its word
    and some room either side of it, rather than evenly - so a long word is
    not squeezed into the share a short one needs."""
    for chip in chips:
        chip.size_hint_x = CoreLabel(font_size=chip.font_size).get_extents(chip.text)[0] + room


class Option(Chip):
    """One of a few choices, like a segment of a switch: outlined while it is
    only on offer, filled with the accent once it is the one chosen."""

    def __init__(self, text='', selected=False, **kwargs):
        self._outline = None
        super().__init__(text=text, selected=selected, **kwargs)
        self.size_hint_y = None
        self.height = dp(34)
        self.font_size = dp(14)
        with self.canvas.before:
            self._outline_color = Color(*theme.BORDER)
            self._outline = Line(width=1.0)
        self._apply()
        self._redraw()

    def _redraw(self, *_):
        super()._redraw()
        radius = min(dp(17), self.height / 2)
        self._rect.radius = [radius]
        if self._outline is not None:
            self._outline.rounded_rectangle = (self.x, self.y, self.width, self.height, radius)

    def _apply(self):
        self._color.rgba = theme.BLUE if self.selected else theme.TRANSPARENT
        self.color = (1, 1, 1, 1) if self.selected else theme.TEXT
        if self._outline is not None:
            self._outline_color.rgba = theme.BLUE if self.selected else theme.BORDER


class FlatButton(Button):
    """A button that draws its own rounded background, because Kivy's default
    one is a grey bitmap from 2011. Its label keeps clear of the edges, and
    is cut short rather than running into them should it ever be too long."""

    def __init__(self, fill=theme.HOVER, radius=8, **kwargs):
        kwargs.setdefault('halign', 'center')
        kwargs.setdefault('valign', 'middle')
        kwargs.setdefault('shorten', True)
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
        self.text_size = (max(0, self.width - 2 * BUTTON_MARGIN), self.height)


# The least room a button's label keeps from either edge.
BUTTON_MARGIN = dp(8)

# The looks a dialog's buttons come in: (fill, label colour).
BUTTON_STYLES = {
    'primary': (theme.BLUE, (1, 1, 1, 1)),
    'danger': (theme.state_color(State.ERROR), (1, 1, 1, 1)),
    'plain': (theme.HOVER, theme.TEXT),
}


class Dialog(ModalView):
    """A question in a rounded card over the dimmed app.

    Every dialog in the app is one of these, so they all look like the rest
    of it - Kivy's own Popup is a square box with a rule under its title.
    buttons are (label, style, action), style being one of BUTTON_STYLES,
    listed as a row reads: the one that does nothing first, the main action
    last. They share a row when every label fits its share with room either
    side, and stack, full width, when one would not - the main action on top,
    the one that does nothing at the bottom - so no label is squeezed
    against its edges, whatever the words or the width of the phone. Any of
    them closes the dialog, then runs its action, if it has one.
    """

    ROOM = dp(16)          # each side of a label, for its button to count as fitting

    def __init__(self, title, message='', buttons=(), **kwargs):
        super().__init__(size_hint=(None, None), background='', background_color=theme.TRANSPARENT,
                         overlay_color=(0, 0, 0, 0.6), **kwargs)
        self.width = min(Window.width - dp(40), dp(400))
        padding = [dp(22), dp(20), dp(22), dp(18)]
        inner = self.width - padding[0] - padding[2]
        card = Card(orientation='vertical', radius=16, fill=theme.ALT, size_hint_y=None,
                    padding=padding, spacing=dp(10))
        card.bind(minimum_height=card.setter('height'), height=self.setter('height'))
        card.add_widget(self._words(title, dp(17), theme.TEXT, inner, bold=True))
        if message:
            card.add_widget(self._words(message, dp(14), theme.DIM, inner))
        card.add_widget(Widget(size_hint_y=None, height=dp(6)))
        self.buttons = self._buttons(buttons, inner)
        card.add_widget(self.buttons)
        self.add_widget(card)

    @staticmethod
    def _words(text, size, color, width, bold=False):
        label = Label(text=text, font_size=size, color=color, bold=bold, halign='left',
                      valign='top', size_hint_y=None, text_size=(width, None))
        label.bind(texture_size=lambda widget, value: setattr(widget, 'height', value[1]))
        label.texture_update()          # measured now, so the card opens at its size
        return label

    def _buttons(self, buttons, width):
        buttons = list(buttons)
        gap, height, stacked_gap = dp(10), dp(46), dp(8)
        share = (width - gap * (len(buttons) - 1)) / max(1, len(buttons))
        measure = CoreLabel(font_size=dp(15))
        in_a_row = all(measure.get_extents(label)[0] + 2 * self.ROOM <= share
                       for label, _, _ in buttons)
        box = BoxLayout(orientation='horizontal' if in_a_row else 'vertical',
                        spacing=gap if in_a_row else stacked_gap, size_hint_y=None)
        box.height = (height if in_a_row
                      else len(buttons) * height + (len(buttons) - 1) * stacked_gap)
        for label, style, action in (buttons if in_a_row else reversed(buttons)):
            fill, color = BUTTON_STYLES[style]
            button = FlatButton(text=label, font_size=dp(15), fill=fill, color=color)
            button.bind(on_release=lambda _, act=action: self._pick(act))
            box.add_widget(button)
        return box

    def _pick(self, action):
        self.dismiss()
        if action is not None:
            action()


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
            if quality == 'frame':              # one picture out of a video
                self._kind = 'image'
            else:
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

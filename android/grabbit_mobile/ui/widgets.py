"""Small pieces the rest of the interface is built from - Material 3's.

Kivy draws nothing by itself, so a card, a chip, a switch and a progress bar
all have to be described in canvas instructions. They are here, drawn to
Material 3's measurements and in its colour roles (theme.py), so the screens
read as layout rather than as drawing, and every screen gets the same ones.
"""

import math

from kivy.animation import Animation
from kivy.core.text import Label as CoreLabel
from kivy.core.window import Window
from kivy.graphics import Color, Ellipse, Line, Rectangle, RoundedRectangle, Triangle
from kivy.metrics import dp
from kivy.properties import BooleanProperty, NumericProperty
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.modalview import ModalView
from kivy.uix.stacklayout import StackLayout
from kivy.uix.widget import Widget

from grabbit.tasks import KIND_IMAGE, KIND_MAGNET, KIND_MEDIA, KIND_TORRENT, State

from . import theme
from .motion import CHANGE, STANDARD, Entrance, blend, ripple

# Every drawn symbol's line: a Kivy Line is drawn this far either side of
# its path, so twice this thick - about the weight of the text beside it.
STROKE = dp(0.8)
# An outline or a divider: Material's 1dp.
HAIRLINE = dp(0.5)

# The least room a button's label keeps from either edge.
BUTTON_MARGIN = dp(8)
# Either side of the words of a button as wide as they are (a text button's is half).
BUTTON_PADDING = dp(24)


def text_width(text: str, font_size) -> float:
    return CoreLabel(font_size=font_size).get_extents(text)[0]


def _corners(radius) -> list:
    return list(radius) if isinstance(radius, (list, tuple)) else [radius] * 4


def recolor(label, color):
    """A label's text to a new colour, over a moment once it is on screen."""
    Animation.cancel_all(label, 'color')
    if label.get_root_window() is None:
        label.color = color
    else:
        Animation(color=color, d=CHANGE, t=STANDARD).start(label)


# ------------------------------------------------------------------ cards
class Card(BoxLayout):
    """A Material card: a filled, rounded panel - outlined too, if given a
    border. radius is in dp: one for every corner, or four (top left, top
    right, bottom right, bottom left)."""

    def __init__(self, radius=theme.MEDIUM, fill=theme.SURFACE_CONTAINER, border=None, **kwargs):
        super().__init__(**kwargs)
        self._corners = [dp(r) for r in _corners(radius)]
        self._radius = max(self._corners)
        with self.canvas.before:
            self._fill_color = Color(*fill)
            self._rect = RoundedRectangle(radius=self._corners)
            self._border_color = Color(*(border or theme.TRANSPARENT))
            self._line = Line(width=HAIRLINE)
        self.bind(pos=self._redraw, size=self._redraw)

    def set_fill(self, color):
        blend(self._fill_color, color, widget=self)

    def set_border(self, color):
        blend(self._border_color, color or theme.TRANSPARENT, widget=self)

    def _redraw(self, *_):
        self._rect.pos = self.pos
        self._rect.size = self.size
        self._line.rounded_rectangle = (self.x, self.y, self.width, self.height, self._radius)


class Handle(Widget):
    """Material's drag handle: a short rounded bar, 32 by 4dp, in the middle."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        with self.canvas:
            Color(*theme.with_alpha(theme.ON_SURFACE_VARIANT, 0.4))
            self._bar = RoundedRectangle(radius=[dp(2)])
        self.bind(pos=self._redraw, size=self._redraw)

    def _redraw(self, *_):
        self._bar.pos = (self.center_x - dp(16), self.center_y - dp(2))
        self._bar.size = (dp(32), dp(4))


class TapLabel(ButtonBehavior, Label):
    """Text that does something when touched, and looks no different - but
    for the ripple under the finger."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.ripple = ripple(self, radius=dp(theme.SMALL), color=lambda: self.color)


def top_bar(title: str, on_back, height=dp(64)):
    """Material's small top app bar for a page: back, and the page's name.
    (bar, the name's label)."""
    bar = BoxLayout(size_hint_y=None, height=height, spacing=dp(4), padding=[-dp(8), 0, 0, 0])
    back = IconButton('back', color=theme.ON_SURFACE, width=dp(48))
    back.bind(on_release=lambda *_: on_back())
    name = Label(text=title, color=theme.ON_SURFACE, font_size=theme.TITLE_LARGE,
                 halign='left', valign='middle', shorten=True, shorten_from='right')
    name.bind(size=lambda widget, value: setattr(widget, 'text_size', value))
    bar.add_widget(back)
    bar.add_widget(name)
    return bar, name


# ------------------------------------------------------------------ chips
TICK = dp(18)           # the tick on a chosen chip or segment
TICK_GAP = dp(8)


def _draw_tick(line, x, y, size):
    line.points = [x + size * 0.2, y + size * 0.52, x + size * 0.4, y + size * 0.32,
                   x + size * 0.8, y + size * 0.72]


class Chip(ButtonBehavior, Label):
    """Material's filter chip: outlined while off; filled, with a tick in
    front of its word, once on. As wide as its words."""

    SIDE = dp(16)

    def __init__(self, text='', selected=False, **kwargs):
        super().__init__(text=text, halign='center', valign='middle', **kwargs)
        self.font_size = theme.LABEL_LARGE
        self.size_hint = (None, None)
        self.height = dp(32)
        self.pos_hint = {'center_y': 0.5}
        self.selected = selected
        with self.canvas.before:
            self._fill_color = Color(*theme.TRANSPARENT)
            self._rect = RoundedRectangle(radius=[dp(theme.SMALL)])
            self._edge_color = Color(*theme.OUTLINE_VARIANT)
            self._edge = Line(width=HAIRLINE)
        with self.canvas.after:
            self._tick_color = Color(*theme.TRANSPARENT)
            self._tick = Line(width=STROKE, joint='round', cap='round')
        self.bind(pos=self._redraw, size=self._redraw, texture_size=self._resize)
        self._apply()
        self.ripple = ripple(self, radius=dp(theme.SMALL), color=lambda: self.color)

    def _resize(self, *_):
        self.width = self.texture_size[0]

    def _redraw(self, *_):
        self._rect.pos = self.pos
        self._rect.size = self.size
        self._edge.rounded_rectangle = (self.x, self.y, self.width, self.height, dp(theme.SMALL))
        _draw_tick(self._tick, self.x + TICK_GAP, self.center_y - TICK / 2, TICK)

    def set_selected(self, selected: bool):
        if selected != self.selected:
            self.selected = selected
            self._apply()

    def _apply(self):
        on = self.selected
        self.padding = [2 * TICK_GAP + TICK if on else self.SIDE, 0, self.SIDE, 0]
        blend(self._fill_color, theme.SECONDARY_CONTAINER if on else theme.TRANSPARENT, widget=self)
        blend(self._edge_color, theme.TRANSPARENT if on else theme.OUTLINE_VARIANT, widget=self)
        blend(self._tick_color, theme.ON_SECONDARY_CONTAINER if on else theme.TRANSPARENT,
              widget=self)
        recolor(self, theme.ON_SECONDARY_CONTAINER if on else theme.ON_SURFACE_VARIANT)


class Segment(ButtonBehavior, Label):
    """One choice of a segmented button. The row it is in draws the outline
    around them all, and says which of its corners are rounded."""

    def __init__(self, text='', selected=False, **kwargs):
        super().__init__(text=text, halign='center', valign='middle', shorten=True, **kwargs)
        self.font_size = theme.LABEL_LARGE
        self.selected = selected
        self.corners = [0, 0, 0, 0]
        with self.canvas.before:
            self._fill_color = Color(*theme.TRANSPARENT)
            self._rect = RoundedRectangle(radius=self.corners)
        with self.canvas.after:
            self._tick_color = Color(*theme.TRANSPARENT)
            self._tick = Line(width=STROKE, joint='round', cap='round')
        self.bind(pos=self._redraw, size=self._redraw)
        self._apply()
        self.ripple = ripple(self, radius=lambda: self.corners, color=lambda: self.color)

    def _ticked(self) -> bool:
        """Chosen, and with room for the tick beside its word."""
        return self.selected and (text_width(self.text, self.font_size) + TICK + TICK_GAP
                                  + 2 * BUTTON_MARGIN <= self.width)

    def _redraw(self, *_):
        self._rect.pos = self.pos
        self._rect.size = self.size
        self._rect.radius = self.corners
        # The word and the tick in front of it, centred together.
        ticked = self._ticked()
        words = text_width(self.text, self.font_size)
        self.padding = [TICK + TICK_GAP if ticked else 0, 0, 0, 0]
        self.text_size = (max(0, self.width - 2 * BUTTON_MARGIN), self.height)
        start = self.center_x - (words + TICK + TICK_GAP) / 2
        _draw_tick(self._tick, start, self.center_y - TICK / 2, TICK)
        blend(self._tick_color, theme.ON_SECONDARY_CONTAINER if ticked else theme.TRANSPARENT,
              widget=self)

    def set_selected(self, selected: bool):
        if selected != self.selected:
            self.selected = selected
            self._apply()

    def _apply(self):
        on = self.selected
        blend(self._fill_color, theme.SECONDARY_CONTAINER if on else theme.TRANSPARENT, widget=self)
        recolor(self, theme.ON_SECONDARY_CONTAINER if on else theme.ON_SURFACE)
        self._redraw()


class Choices:
    """A group of choices where exactly one is chosen."""

    def _fill(self, options, chosen, on_choose, make):
        self.chosen = chosen
        self._on_choose = on_choose
        self.buttons = {}
        for value, label in options:
            button = make(text=label, selected=(value == chosen))
            button.bind(on_release=lambda _, v=value: self.choose(v))
            self.buttons[value] = button
            self.add_widget(button)

    def choose(self, value):
        self.chosen = value
        for option, button in self.buttons.items():
            button.set_selected(option == value)
        if self._on_choose:
            self._on_choose(value)


class SegmentedRow(Choices, BoxLayout):
    """Material's segmented button: a few choices sharing one outlined pill,
    the chosen one filled - Video, Audio, GIF, Image; Off and On."""

    def __init__(self, options, chosen, on_choose, **kwargs):
        super().__init__(size_hint_y=None, height=dp(40), **kwargs)
        self._fill(options, chosen, on_choose, Segment)
        segments = list(self.buttons.values())
        end = self.height / 2
        for index, segment in enumerate(segments):
            first, last = index == 0, index == len(segments) - 1
            segment.corners = [end if first else 0, end if last else 0,
                               end if last else 0, end if first else 0]
            segment.bind(pos=self._redraw, size=self._redraw)
        with self.canvas.after:
            Color(*theme.OUTLINE)
            self._outline = Line(width=HAIRLINE)
            self._dividers = [Line(width=HAIRLINE) for _ in segments[1:]]
        self.bind(pos=self._redraw, size=self._redraw)

    def _redraw(self, *_):
        self._outline.rounded_rectangle = (self.x, self.y, self.width, self.height,
                                           self.height / 2)
        for line, segment in zip(self._dividers, list(self.buttons.values())[1:]):
            line.points = [segment.x, self.y, segment.x, self.top]


class ChipWrap(Choices, StackLayout):
    """Choices as chips, as wide as their words, wrapping onto more lines:
    a video's qualities."""

    def __init__(self, options, chosen, on_choose, **kwargs):
        super().__init__(size_hint_y=None, spacing=dp(8), **kwargs)
        self.bind(minimum_height=self.setter('height'))
        self._fill(options, chosen, on_choose, Chip)


def share_by_words(widgets, room=dp(20)):
    """Widgets splitting one row between them, each in proportion to its word
    and some room either side of it, rather than evenly - so a long word is
    not squeezed into the share a short one needs."""
    for widget in widgets:
        widget.size_hint_x = text_width(widget.text, widget.font_size) + room


# ---------------------------------------------------------------- buttons
# (fill, label, outline) for each kind of Material button.
BUTTON_STYLES = {
    'filled': (theme.PRIMARY, theme.ON_PRIMARY, None),
    'tonal': (theme.SECONDARY_CONTAINER, theme.ON_SECONDARY_CONTAINER, None),
    'outlined': (theme.TRANSPARENT, theme.PRIMARY, theme.OUTLINE),
    'text': (theme.TRANSPARENT, theme.PRIMARY, None),
    'danger': (theme.TRANSPARENT, theme.ERROR, None),
    'danger-tonal': (theme.ERROR_CONTAINER, theme.ON_ERROR_CONTAINER, None),
}


class FlatButton(Button):
    """A Material button, full-rounded and 40dp high: 'filled' for the one
    main action on a screen, 'tonal' for the others, 'outlined' and 'text'
    for the least - and 'danger' and 'danger-tonal' for what cannot be
    undone. Given size_hint_x=None and no width, it is as wide as its words.
    Its label keeps clear of the edges, and is cut short rather than running
    into them should it ever be too long."""

    def __init__(self, style='tonal', **kwargs):
        kwargs.setdefault('font_size', theme.LABEL_LARGE)
        kwargs.setdefault('halign', 'center')
        kwargs.setdefault('valign', 'middle')
        kwargs.setdefault('shorten', True)
        kwargs.setdefault('size_hint_y', None)
        kwargs.setdefault('height', dp(40))
        kwargs.setdefault('pos_hint', {'center_y': 0.5})
        fit = kwargs.get('size_hint_x', 1) is None and 'width' not in kwargs
        super().__init__(background_normal='', background_down='',
                         background_disabled_normal='', background_disabled_down='',
                         background_color=theme.TRANSPARENT, **kwargs)
        self.style = style
        self.disabled_color = theme.DISABLED_TEXT
        with self.canvas.before:
            self._color = Color(*theme.TRANSPARENT)
            self._rect = RoundedRectangle()
            self._edge_color = Color(*theme.TRANSPARENT)
            self._edge = Line(width=HAIRLINE)
        self.bind(pos=self._redraw, size=self._redraw, disabled=lambda *_: self._paint())
        if fit:
            self.bind(text=self._fit)
            self._fit()
        self._paint()
        self.ripple = ripple(self, radius=lambda: self.height / 2, color=lambda: self.color)

    def _fit(self, *_):
        side = BUTTON_PADDING / 2 if self.style in ('text', 'danger') else BUTTON_PADDING
        self.width = text_width(self.text, self.font_size) + 2 * side

    def set_style(self, style: str):
        self.style = style
        self._paint()

    def _paint(self):
        fill, label, edge = BUTTON_STYLES[self.style]
        if self.disabled:
            fill = theme.DISABLED_FILL if fill != theme.TRANSPARENT else fill
            edge = theme.DISABLED_FILL if edge else None
        blend(self._color, fill, widget=self)
        blend(self._edge_color, edge or theme.TRANSPARENT, widget=self)
        self.color = label

    def _redraw(self, *_):
        radius = self.height / 2
        self._rect.pos = self.pos
        self._rect.size = self.size
        self._rect.radius = [radius]
        self._edge.rounded_rectangle = (self.x, self.y, self.width, self.height, radius)
        self.text_size = (max(0, self.width - 2 * BUTTON_MARGIN), self.height)


class IconButton(ButtonBehavior, Widget):
    """Material's icon button: a symbol - share, details, close, back,
    settings, the graph - drawn 24dp in a 40dp circle of its own, which is
    where its ripple shows. As a toggle it is filled in while on.

    No font on a phone has Material's symbols, and a symbol typed as text
    sits wherever its font puts it rather than on the line the rest of a
    row is centred on.
    """

    def __init__(self, kind: str, extent=dp(24), color=None, toggle=False, **kwargs):
        kwargs.setdefault('size_hint_x', None)
        kwargs.setdefault('width', dp(40))
        super().__init__(**kwargs)
        self.kind, self.extent = kind, extent
        self.color = color or theme.ON_SURFACE_VARIANT
        self.toggle = toggle
        self.selected = False
        with self.canvas.before:
            self._fill_color = Color(*theme.TRANSPARENT)
            self._fill = Ellipse()
        self.bind(pos=self._redraw, size=self._redraw)
        self.ripple = ripple(self, circle=True, color=lambda: self._ink())

    def set_selected(self, selected: bool):
        self.selected = selected
        blend(self._fill_color, theme.SECONDARY_CONTAINER if self.toggle and selected
              else theme.TRANSPARENT, widget=self)
        self._redraw()

    def _ink(self):
        return theme.ON_SECONDARY_CONTAINER if self.toggle and self.selected else self.color

    def _redraw(self, *_):
        circle = min(self.width, self.height, dp(40))
        self._fill.pos = (self.center_x - circle / 2, self.center_y - circle / 2)
        self._fill.size = (circle, circle)
        self.canvas.clear()
        size = min(self.extent, self.width, self.height)
        x, y = self.center_x - size / 2, self.center_y - size / 2
        with self.canvas:
            Color(*self._ink())
            getattr(self, f'_draw_{self.kind}')(x, y, size)

    # Each draws Material's symbol in a square of `size`, from its bottom left.
    @staticmethod
    def _draw_share(x, y, size):
        dots = [(0.75, 0.79), (0.25, 0.5), (0.75, 0.21)]
        Line(points=[value for dx, dy in dots for value in (x + size * dx, y + size * dy)],
             width=STROKE)
        radius = size * 0.11
        for dx, dy in dots:
            Ellipse(pos=(x + size * dx - radius, y + size * dy - radius),
                    size=(2 * radius, 2 * radius))

    @staticmethod
    def _draw_info(x, y, size):
        Line(circle=(x + size / 2, y + size / 2, size * 0.40), width=STROKE)
        Line(points=[x + size / 2, y + size * 0.29, x + size / 2, y + size * 0.53], width=STROKE)
        dot = STROKE * 1.3
        Ellipse(pos=(x + size / 2 - dot, y + size * 0.67 - dot), size=(2 * dot, 2 * dot))

    @staticmethod
    def _draw_close(x, y, size):
        low, high = size * 0.27, size * 0.73
        Line(points=[x + low, y + low, x + high, y + high], width=STROKE)
        Line(points=[x + low, y + high, x + high, y + low], width=STROKE)

    @staticmethod
    def _draw_back(x, y, size):
        Line(points=[x + size * 0.18, y + size * 0.5, x + size * 0.82, y + size * 0.5],
             width=STROKE)
        Line(points=[x + size * 0.48, y + size * 0.8, x + size * 0.18, y + size * 0.5,
                     x + size * 0.48, y + size * 0.2], width=STROKE, joint='round')

    @staticmethod
    def _draw_pause(x, y, size):
        for left in (0.3, 0.6):
            RoundedRectangle(pos=(x + size * left, y + size * 0.22),
                             size=(size * 0.12, size * 0.56), radius=[size * 0.03])

    @staticmethod
    def _draw_play(x, y, size):
        Triangle(points=[x + size * 0.32, y + size * 0.2, x + size * 0.32, y + size * 0.8,
                         x + size * 0.8, y + size * 0.5])

    @staticmethod
    def _draw_retry(x, y, size):
        # Round most of the way, with the arrowhead where it ends.
        cx, cy, reach = x + size / 2, y + size / 2, size * 0.32
        Line(circle=(cx, cy, reach, 60, 360), width=STROKE)
        tip = (cx + reach * math.sin(math.radians(60)), cy + reach * math.cos(math.radians(60)))
        Triangle(points=[tip[0] - size * 0.13, tip[1] + size * 0.02,
                         tip[0] + size * 0.07, tip[1] + size * 0.15,
                         tip[0] + size * 0.07, tip[1] - size * 0.12])

    @staticmethod
    def _draw_previous(x, y, size):
        Line(points=[x + size * 0.6, y + size * 0.78, x + size * 0.34, y + size * 0.5,
                     x + size * 0.6, y + size * 0.22], width=STROKE, joint='round')

    @staticmethod
    def _draw_next(x, y, size):
        Line(points=[x + size * 0.4, y + size * 0.78, x + size * 0.66, y + size * 0.5,
                     x + size * 0.4, y + size * 0.22], width=STROKE, joint='round')

    @staticmethod
    def _draw_settings(x, y, size):
        # A gear: eight flat-topped teeth round a ring, and the hole in it.
        cx, cy = x + size / 2, y + size / 2
        outer, inner, teeth = size * 0.42, size * 0.32, 8
        points = []
        for step in range(teeth * 4):
            angle = 2 * math.pi * (step - 0.5) / (teeth * 4) + math.pi / teeth / 2
            reach = outer if step % 4 in (1, 2) else inner
            points += [cx + reach * math.cos(angle), cy + reach * math.sin(angle)]
        Line(points=points, close=True, width=STROKE, joint='round')
        Line(circle=(cx, cy, size * 0.13), width=STROKE)

    @staticmethod
    def _draw_graph(x, y, size):
        Line(points=[x + size * 0.14, y + size * 0.3, x + size * 0.38, y + size * 0.58,
                     x + size * 0.58, y + size * 0.4, x + size * 0.86, y + size * 0.74],
             width=STROKE, joint='round', cap='round')
        Line(points=[x + size * 0.14, y + size * 0.16, x + size * 0.86, y + size * 0.16],
             width=STROKE)


class Toggle(ButtonBehavior, Widget):
    """Material's switch. Off: an outlined track with a small thumb. On: a
    filled track, the thumb grown and over at the other end, with a tick.
    It slides between the two; on_change(active) hears a touch change it.

    Not called Switch: Kivy styles widgets by class name, and would draw its
    own switch's pictures over this one."""

    active = BooleanProperty(False)
    travel = NumericProperty(0.0)          # 0 off to 1 on, as the thumb goes

    def __init__(self, active=False, on_change=None, **kwargs):
        super().__init__(size_hint=(None, None), size=(dp(52), dp(32)),
                         pos_hint={'center_y': 0.5}, **kwargs)
        self._on_change = on_change
        with self.canvas:
            self._track_color = Color()
            self._track = RoundedRectangle(radius=[dp(16)])
            self._edge_color = Color()
            self._edge = Line(width=dp(1))
            self._thumb_color = Color()
            self._thumb = Ellipse()
            self._tick_color = Color()
            self._tick = Line(width=STROKE, joint='round', cap='round')
        self.active = active
        self.travel = 1.0 if active else 0.0
        self.bind(pos=self._redraw, size=self._redraw, travel=self._redraw)
        self._redraw()

    def on_release(self):
        self.set_active(not self.active)
        if self._on_change:
            self._on_change(self.active)

    def set_active(self, active: bool):
        self.active = active
        Animation.cancel_all(self, 'travel')
        target = 1.0 if active else 0.0
        if self.get_root_window() is None:
            self.travel = target
        else:
            Animation(travel=target, d=CHANGE, t=STANDARD).start(self)

    @staticmethod
    def _mix(off, on, share):
        return tuple(a + (b - a) * share for a, b in zip(off, on))

    def _redraw(self, *_):
        share = self.travel
        self._track.pos, self._track.size = self.pos, self.size
        self._track_color.rgba = self._mix(theme.SURFACE_CONTAINER_HIGHEST, theme.PRIMARY, share)
        self._edge_color.rgba = theme.with_alpha(theme.OUTLINE, 1 - share)
        inset = dp(1)
        self._edge.rounded_rectangle = (self.x + inset, self.y + inset, self.width - 2 * inset,
                                        self.height - 2 * inset, self.height / 2 - inset)
        thumb = dp(16) + dp(8) * share
        cx = self.x + dp(16) + (self.width - dp(32)) * share
        self._thumb_color.rgba = self._mix(theme.OUTLINE, theme.ON_PRIMARY, share)
        self._thumb.pos = (cx - thumb / 2, self.center_y - thumb / 2)
        self._thumb.size = (thumb, thumb)
        self._tick_color.rgba = theme.with_alpha(theme.ON_PRIMARY_CONTAINER, share)
        _draw_tick(self._tick, cx - dp(8), self.center_y - dp(8), dp(16))


class Tab(ButtonBehavior, Label):
    def __init__(self, **kwargs):
        super().__init__(halign='center', valign='middle', shorten=True, **kwargs)
        self.font_size = theme.TITLE_SMALL
        self.bind(size=lambda widget, value: setattr(
            widget, 'text_size', (max(0, value[0] - 2 * BUTTON_MARGIN), value[1])))
        self.ripple = ripple(self, radius=0, color=lambda: self.color)


class Tabs(BoxLayout):
    """Material's secondary tabs: words across, the chosen one underlined in
    the primary colour, a hairline under them all. The line slides over to a
    tab as it is chosen."""

    line_x = NumericProperty(0.0)
    line_width = NumericProperty(0.0)

    def __init__(self, names, chosen, on_choose, **kwargs):
        super().__init__(size_hint_y=None, height=dp(48), **kwargs)
        self.chosen = chosen
        self.buttons = {}
        for name in names:
            tab = Tab(text=name, color=theme.ON_SURFACE_VARIANT)
            tab.bind(on_release=lambda _, n=name: on_choose(n))
            tab.bind(pos=self._snap, size=self._snap)
            self.buttons[name] = tab
            self.add_widget(tab)
        # Five across a phone: "Trackers" needs more of it than "Log".
        share_by_words(self.buttons.values(), room=2 * BUTTON_MARGIN + dp(4))
        with self.canvas.after:
            Color(*theme.OUTLINE_VARIANT)
            self._rule = Rectangle()
            Color(*theme.PRIMARY)
            self._line = Rectangle()
        self.bind(pos=self._snap, size=self._snap, line_x=self._draw, line_width=self._draw)
        self.select(chosen)

    def select(self, name: str):
        moving = name != self.chosen
        self.chosen = name
        for key, tab in self.buttons.items():
            recolor(tab, theme.ON_SURFACE if key == name else theme.ON_SURFACE_VARIANT)
        tab = self.buttons[name]
        Animation.cancel_all(self, 'line_x', 'line_width')
        if moving and self.get_root_window() is not None:
            Animation(line_x=tab.x, line_width=tab.width, d=0.25, t=STANDARD).start(self)
        else:
            self.line_x, self.line_width = tab.x, tab.width

    def _snap(self, *_):
        tab = self.buttons[self.chosen]
        Animation.cancel_all(self, 'line_x', 'line_width')
        self.line_x, self.line_width = tab.x, tab.width
        self._draw()

    def _draw(self, *_):
        self._rule.pos, self._rule.size = (self.x, self.y), (self.width, 2 * HAIRLINE)
        self._line.pos, self._line.size = (self.line_x, self.y), (self.line_width, dp(2))


# ---------------------------------------------------------------- dialogs
# Material's dialogs have text buttons; what cannot be undone is in red.
DIALOG_STYLES = {'primary': 'text', 'plain': 'text', 'danger': 'danger'}


class Dialog(Entrance, ModalView):
    """A question in Material's dialog: a rounded card over the dimmed app,
    its title, what it is about, and text buttons at the bottom right.

    buttons are (label, style, action), style being 'primary', 'plain' or
    'danger', listed as a row reads: the one that does nothing first, the
    main action last. They share a row when they fit, and stack on the right
    when they do not - the main action on top, the one that does nothing at
    the bottom. Any of them closes the dialog, then runs its action, if it
    has one.
    """

    ROOM = dp(12)          # each side of a label: a text button's padding
    entrance = 'card'

    def __init__(self, title, message='', buttons=(), **kwargs):
        super().__init__(size_hint=(None, None), background='', background_color=theme.TRANSPARENT,
                         overlay_color=theme.with_alpha(theme.SCRIM, 0.32), **kwargs)
        self.width = max(dp(280), min(Window.width - dp(48), dp(560)))
        padding = [dp(24), dp(24), dp(24), dp(24)]
        inner = self.width - padding[0] - padding[2]
        card = Card(orientation='vertical', radius=theme.EXTRA_LARGE,
                    fill=theme.SURFACE_CONTAINER_HIGH, size_hint_y=None, padding=padding,
                    spacing=dp(16))
        card.bind(minimum_height=card.setter('height'), height=self.setter('height'))
        card.add_widget(self._words(title, theme.HEADLINE_SMALL, theme.ON_SURFACE, inner))
        if message:
            card.add_widget(self._words(message, theme.BODY_MEDIUM, theme.ON_SURFACE_VARIANT, inner))
        card.add_widget(Widget(size_hint_y=None, height=dp(8)))       # 24dp over the buttons
        self.buttons = self._buttons(buttons, inner)
        card.add_widget(self.buttons)
        self.add_widget(card)

    @staticmethod
    def _words(text, size, color, width):
        label = Label(text=text, font_size=size, color=color, halign='left',
                      valign='top', size_hint_y=None, text_size=(width, None))
        label.bind(texture_size=lambda widget, value: setattr(widget, 'height', value[1]))
        label.texture_update()          # measured now, so the card opens at its size
        return label

    def _buttons(self, buttons, width):
        buttons = list(buttons)
        gap, height = dp(8), dp(40)
        widths = [text_width(label, theme.LABEL_LARGE) + 2 * self.ROOM for label, _, _ in buttons]
        row = sum(widths) + gap * (len(buttons) - 1)
        if row <= width:
            # Pushed over to the right by the room left of them.
            box = BoxLayout(orientation='horizontal', spacing=gap, size_hint_y=None,
                            height=height, padding=[width - row, 0, 0, 0])
            order, place = zip(buttons, widths), {'center_y': 0.5}
        else:
            box = BoxLayout(orientation='vertical', spacing=gap, size_hint_y=None,
                            height=len(buttons) * height + (len(buttons) - 1) * gap)
            order, place = reversed(list(zip(buttons, widths))), {'right': 1}
        for (label, style, action), share in order:
            button = FlatButton(text=label, style=DIALOG_STYLES[style], size_hint_x=None,
                                width=share, pos_hint=place)
            button.bind(on_release=lambda _, act=action: self._pick(act))
            box.add_widget(button)
        return box

    def _pick(self, action):
        self.dismiss()
        if action is not None:
            action()


# ------------------------------------------------------------- progress
class ProgressTrack(Widget):
    """Material's linear progress indicator: the part done in the
    download's colour, a gap, the rest of the track, and a dot at its end
    to say where it stops.

    Not called ProgressBar: Kivy styles widgets by class name, and its own
    ProgressBar rule would be applied to this one and then ask it for
    attributes it does not have.

    The indicator glides up to each new figure - they come a second apart -
    rather than jumping there; `shown` is where it has got to.
    """

    shown = NumericProperty(0.0)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.size_hint_y = None
        self.height = dp(4)
        self.fraction = 0.0
        self.tint = theme.PRIMARY
        with self.canvas:
            Color(*theme.SECONDARY_CONTAINER)
            self._track = RoundedRectangle()
            self._fill_color = Color(*self.tint)
            self._fill = RoundedRectangle()
            self._stop = Ellipse()
        self.bind(pos=self._redraw, size=self._redraw, shown=self._redraw)

    def show(self, fraction: float, tint, glide: bool = True):
        """glide: this is the same download as before, moving on - not a
        different one, whose figure has nothing to do with the last."""
        fraction = max(0.0, min(1.0, fraction or 0.0))
        self.tint = tint
        if fraction != self.fraction or not glide:
            Animation.cancel_all(self, 'shown')
            if glide and fraction > self.shown and self.get_root_window() is not None:
                Animation(shown=fraction, d=0.4, t=STANDARD).start(self)
            else:
                self.shown = fraction
            self.fraction = fraction
        self._redraw()

    def _redraw(self, *_):
        height = self.height
        radius = [height / 2]
        self._fill_color.rgba = self.tint
        # A sliver of colour at the very start still reads as "begun".
        done = max(height, self.width * self.shown) if self.shown else 0
        self._fill.pos = self.pos
        self._fill.size = (done, height)
        self._fill.radius = radius
        start = self.x + done + (dp(4) if done else 0)
        self._track.pos = (start, self.y)
        self._track.size = (max(0, self.right - start), height)
        self._track.radius = radius
        stop = height if self.shown < 1 else 0
        self._stop.pos = (self.right - stop, self.y)
        self._stop.size = (stop, stop)


# ------------------------------------------------------------------ kinds
class KindGlyph(Widget):
    """What sort of download this is, drawn rather than shipped as an icon.

    The desktop colours its icons by state; so does this, which makes a list
    readable at a glance without any text being involved.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.size_hint = (None, None)
        self.size = (dp(24), dp(24))
        self.pos_hint = {'center_y': 0.5}
        self._kind = 'file'
        self._color = theme.ON_SURFACE_VARIANT
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

    def show_choice(self, chosen: bool):
        """While downloads are being picked out: a ring, ticked once picked."""
        self._kind = 'chosen' if chosen else 'unchosen'
        self._color = theme.PRIMARY if chosen else theme.ON_SURFACE_VARIANT
        self._redraw()

    def _redraw(self, *_):
        self.canvas.clear()
        x, y = self.pos
        size = min(self.width, self.height)
        stroke = STROKE
        with self.canvas:
            Color(*self._color)
            getattr(self, f'_draw_{self._kind}')(x, y, size, stroke)

    # Each of these draws inside a square of `size`, from its bottom left.
    def _draw_video(self, x, y, size, stroke):
        Line(rounded_rectangle=(x + size * 0.1, y + size * 0.2, size * 0.8,
                                size * 0.6, size * 0.1), width=stroke)
        Triangle(points=[x + size * 0.43, y + size * 0.38,
                         x + size * 0.43, y + size * 0.62,
                         x + size * 0.62, y + size * 0.50])

    def _draw_audio(self, x, y, size, stroke):
        Ellipse(pos=(x + size * 0.18, y + size * 0.18), size=(size * 0.28, size * 0.24))
        Line(points=[x + size * 0.44, y + size * 0.3, x + size * 0.44, y + size * 0.8],
             width=stroke)
        Line(points=[x + size * 0.44, y + size * 0.8, x + size * 0.78, y + size * 0.69],
             width=stroke)

    def _draw_image(self, x, y, size, stroke):
        Line(rounded_rectangle=(x + size * 0.12, y + size * 0.18, size * 0.76,
                                size * 0.64, size * 0.1), width=stroke)
        Ellipse(pos=(x + size * 0.26, y + size * 0.56), size=(size * 0.13, size * 0.13))
        Line(points=[x + size * 0.18, y + size * 0.34, x + size * 0.40, y + size * 0.54,
                     x + size * 0.60, y + size * 0.37, x + size * 0.82, y + size * 0.52],
             width=stroke)

    def _draw_torrent(self, x, y, size, stroke):
        Line(points=[x + size * 0.5, y + size * 0.82, x + size * 0.5, y + size * 0.24],
             width=stroke)
        Line(points=[x + size * 0.27, y + size * 0.47, x + size * 0.5, y + size * 0.2,
                     x + size * 0.73, y + size * 0.47], width=stroke)

    def _draw_seed(self, x, y, size, stroke):
        Line(points=[x + size * 0.5, y + size * 0.18, x + size * 0.5, y + size * 0.76],
             width=stroke)
        Line(points=[x + size * 0.27, y + size * 0.53, x + size * 0.5, y + size * 0.8,
                     x + size * 0.73, y + size * 0.53], width=stroke)

    def _draw_file(self, x, y, size, stroke):
        Line(points=[x + size * 0.26, y + size * 0.14, x + size * 0.26, y + size * 0.86,
                     x + size * 0.58, y + size * 0.86, x + size * 0.76, y + size * 0.68,
                     x + size * 0.76, y + size * 0.14, x + size * 0.26, y + size * 0.14],
             width=stroke)
        Line(points=[x + size * 0.58, y + size * 0.86, x + size * 0.58, y + size * 0.68,
                     x + size * 0.76, y + size * 0.68], width=stroke)

    def _draw_chosen(self, x, y, size, stroke):
        Ellipse(pos=(x + size * 0.08, y + size * 0.08), size=(size * 0.84, size * 0.84))
        Color(*theme.ON_PRIMARY)
        Line(points=[x + size * 0.32, y + size * 0.51, x + size * 0.45, y + size * 0.38,
                     x + size * 0.69, y + size * 0.62], width=stroke)

    def _draw_unchosen(self, x, y, size, stroke):
        Line(circle=(x + size * 0.5, y + size * 0.5, size * 0.42), width=stroke)

    def _draw_error(self, x, y, size, stroke):
        Line(circle=(x + size * 0.5, y + size * 0.5, size * 0.4), width=stroke)
        Line(points=[x + size * 0.5, y + size * 0.32, x + size * 0.5, y + size * 0.56],
             width=stroke)
        Line(points=[x + size * 0.5, y + size * 0.68, x + size * 0.5, y + size * 0.70],
             width=stroke * 1.4)

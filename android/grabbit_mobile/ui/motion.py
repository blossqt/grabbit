"""How the interface moves: a ripple under a finger, pages that slide in and
dialogs that grow in, colours that change over a moment rather than at once -
and frames as fast as the screen can show them while any of that goes on.

Kivy draws nothing of this by itself. Everything here is shared, so every
button, page and dialog moves the same way.
"""

import math

from kivy.animation import Animation
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.event import EventDispatcher
from kivy.graphics import (Color, Ellipse, InstructionGroup, PopMatrix, PushMatrix, Rectangle,
                           RoundedRectangle, Scale, StencilPop, StencilPush, StencilUnUse,
                           StencilUse, Translate)
from kivy.metrics import dp
from kivy.properties import NumericProperty

# Android's own timings, near enough: quick to answer, a little slower to settle.
ENTER = 0.24            # a page or dialog coming in
LEAVE = 0.18            # and going
CHANGE = 0.15           # a colour, as something is chosen


def decelerate(progress: float) -> float:
    """Fast at first, easing into place - for things arriving. Run backwards,
    as a leaving page runs it, it is the matching speeding-up exit."""
    return 1 - (1 - progress) ** 3


# ------------------------------------------------------------------ frames
class _Pace:
    """As many frames a second as the screen shows while anything moves, and
    an easy pace while nothing does.

    Kivy waits between frames for a set rate - 60 a second by default - and
    on a phone its waits run over, so frames miss the screen's refreshes now
    and then and it shows about 45. While frames are being drawn, the wait
    is set short enough never to be the one keeping a frame back: the screen,
    which a frame waits for as it is shown, sets the pace. A moment after the
    last one, the wait goes back to 60 a second, so an app sitting still
    costs no more than it did.
    """

    REST = 0.3              # seconds after the last frame drawn

    def __init__(self, rate: float):
        self.busy = 2 * max(60.0, rate)
        self.idle = 60.0
        self._last = 0.0
        self._resting = Clock.create_trigger(self._rest, self.REST)
        Window.bind(on_flip=self._drew)

    def _drew(self, *_):
        self._last = Clock.get_boottime()
        Clock._max_fps = self.busy
        self._resting()

    def _rest(self, *_):
        still = Clock.get_boottime() - self._last
        if still < self.REST:
            Clock.schedule_once(self._rest, self.REST - still)
        else:
            Clock._max_fps = self.idle


_pace = None


def pace(rate: float):
    """Draw at `rate` frames a second - the screen's - whenever anything moves."""
    global _pace
    if _pace is None:
        _pace = _Pace(rate)


# ------------------------------------------------------------------ ripple
class Ripple(EventDispatcher):
    """Android's touch ripple: from where the finger lands, a circle of light
    spreads across the shape, and fades once the finger lifts.

    Drawn only while it shows - an empty group the rest of the time - since
    clipping every button on screen on every frame would cost the frames
    this is meant to use. radius is the shape's corners, or a function
    giving them; circle, a round one around the middle instead.
    """

    spread = NumericProperty(0.0)
    glow = NumericProperty(0.0)

    def __init__(self, widget, radius=dp(8), circle=False):
        super().__init__()
        self.widget, self.radius, self.circle = widget, radius, circle
        self.origin = (0, 0)
        self._pressed = False
        self.group = InstructionGroup()
        widget.canvas.before.add(self.group)
        self.fbind('spread', self._draw)
        self.fbind('glow', self._draw)

    def press(self, pos):
        Animation.cancel_all(self)
        self.origin = pos
        self._pressed = True
        self.spread = 0.0
        (Animation(spread=1.0, d=0.45, t='out_quad')
         & Animation(glow=1.0, d=0.08)).start(self)

    def release(self):
        if not self._pressed:
            return
        self._pressed = False
        Animation.cancel_all(self)
        (Animation(spread=1.0, d=0.2, t='out_quad')
         & Animation(glow=0.0, d=0.4, t='in_quad')).start(self)

    def _draw(self, *_):
        group = self.group
        group.clear()
        if self.glow <= 0:
            return
        widget = self.widget
        x, y, width, height = widget.x, widget.y, widget.width, widget.height
        if self.circle:
            reach = min(width, height) / 2 * (0.6 + 0.4 * self.spread)
            group.add(Color(1, 1, 1, 0.14 * self.glow))
            group.add(Ellipse(pos=(widget.center_x - reach, widget.center_y - reach),
                              size=(2 * reach, 2 * reach)))
            return
        radius = self.radius() if callable(self.radius) else self.radius
        radius = min(radius, width / 2, height / 2)
        ox, oy = self.origin
        # Far enough to reach the corner furthest from the finger.
        reach = max(math.hypot(ox - cx, oy - cy)
                    for cx in (x, x + width) for cy in (y, y + height))
        reach *= 0.15 + 0.85 * self.spread

        def shape():
            return RoundedRectangle(pos=(x, y), size=(width, height), radius=[radius])

        group.add(StencilPush())
        group.add(shape())
        group.add(StencilUse())
        group.add(Color(1, 1, 1, 0.05 * self.glow))
        group.add(Rectangle(pos=(x, y), size=(width, height)))
        group.add(Color(1, 1, 1, 0.09 * self.glow))
        group.add(Ellipse(pos=(ox - reach, oy - reach), size=(2 * reach, 2 * reach)))
        group.add(StencilUnUse())
        group.add(shape())
        group.add(StencilPop())


def ripple(button, radius=dp(8), circle=False) -> Ripple:
    """A ripple under a button's finger - anything with ButtonBehavior."""
    effect = Ripple(button, radius=radius, circle=circle)

    def follow(_, state):
        if state == 'down':
            touch = button.last_touch
            effect.press(touch.pos if touch is not None else button.center)
        else:
            effect.release()

    button.fbind('state', follow)
    return effect


# ----------------------------------------------------------------- colours
_blending = {}


def blend(color, target, duration=CHANGE, widget=None):
    """Move a Color instruction to `target` over a moment - or at once, when
    `widget` is given and is not on screen yet, as when it is being built."""
    target = tuple(target)
    running = _blending.pop(id(color), None)
    if running is not None:
        running.cancel()
    start = tuple(color.rgba)
    if start == target or (widget is not None and widget.get_root_window() is None):
        color.rgba = target
        return
    began = Clock.get_boottime()

    def step(_):
        progress = min(1.0, (Clock.get_boottime() - began) / duration)
        eased = decelerate(progress)
        color.rgba = tuple(a + (b - a) * eased for a, b in zip(start, target))
        if progress >= 1.0:
            _blending.pop(id(color), None)
            return False
        return True

    _blending[id(color)] = Clock.schedule_interval(step, 0)


def fade_in(widget, duration=ENTER):
    """Bring a widget that has just been put on screen up from nothing."""
    Animation.cancel_all(widget, 'opacity')
    widget.opacity = 0
    Animation(opacity=1, d=duration, t='out_quad').start(widget)


# ---------------------------------------------------------- pages, dialogs
class Entrance:
    """How a ModalView comes and goes, mixed in before it.

    'page' - a screen of its own - slides in a little way from the right as
    it fades in, and back out to the right: forward and back. 'card' - a
    dialog, the details - grows in from slightly smaller over the dimmed
    app, whose dimming Kivy fades itself.
    """

    entrance = 'page'

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._moved = None              # (widget moved, its Translate or Scale)
        self._shown = 0.0
        self._leaving = False
        self.fbind('_anim_alpha', self._move)

    def open(self, *args, **kwargs):
        self._anim_duration = ENTER
        self._shown = 0.0
        self._move(self, 0.0)           # hidden from the very first frame
        super().open(*args, **kwargs)

    def dismiss(self, *args, **kwargs):
        self._anim_duration = LEAVE
        super().dismiss(*args, **kwargs)

    # On its way out, it no longer takes touches: a tap on what it is
    # uncovering is meant for that, not for something already closing.
    def on_touch_down(self, touch):
        return False if self._leaving else super().on_touch_down(touch)

    def on_touch_move(self, touch):
        return False if self._leaving else super().on_touch_move(touch)

    def on_touch_up(self, touch):
        return False if self._leaving else super().on_touch_up(touch)

    def _moving(self):
        if self.entrance == 'page':
            return self
        return self.children[0] if self.children else None

    def _move(self, _, alpha):
        self._leaving = alpha < self._shown
        self._shown = alpha
        target = self._moving()
        if target is None:
            return
        if self._moved is None or self._moved[0] is not target:
            step = Translate() if self.entrance == 'page' else Scale()
            target.canvas.before.insert(0, step)
            target.canvas.before.insert(0, PushMatrix())
            target.canvas.after.add(PopMatrix())
            self._moved = (target, step)
        progress = decelerate(alpha)
        target.opacity = progress
        step = self._moved[1]
        if self.entrance == 'page':
            step.x = (1 - progress) * dp(48)
        else:
            step.origin = target.center
            step.x = step.y = 0.94 + 0.06 * progress

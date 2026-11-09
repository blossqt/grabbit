"""How the interface moves: a ripple under a finger, pages that slide in,
dialogs that grow in and sheets that rise, colours that change over a moment
rather than at once - and frames as fast as the screen can show them while
any of that goes on.

The timings and curves are Material 3's. Kivy draws nothing of this by
itself; everything here is shared, so every button, page and dialog moves
the same way.
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


def _bezier(x1, y1, x2, y2):
    """A CSS-style cubic-bezier easing: progress in time to progress on screen."""
    def ease(x):
        if x <= 0:
            return 0.0
        if x >= 1:
            return 1.0
        low, high = 0.0, 1.0
        for _ in range(20):
            t = (low + high) / 2
            if 3 * (1 - t) ** 2 * t * x1 + 3 * (1 - t) * t * t * x2 + t ** 3 < x:
                low = t
            else:
                high = t
        t = (low + high) / 2
        return 3 * (1 - t) ** 2 * t * y1 + 3 * (1 - t) * t * t * y2 + t ** 3
    return ease


# Material 3's curves: emphasized for what arrives and leaves, standard for
# what changes where it is.
EMPHASIZED_DECELERATE = _bezier(0.05, 0.7, 0.1, 1.0)
EMPHASIZED_ACCELERATE = _bezier(0.3, 0.0, 0.8, 0.15)
STANDARD = _bezier(0.2, 0.0, 0.0, 1.0)

# And its durations.
ENTER = 0.35            # a page, dialog or sheet coming in
LEAVE = 0.2             # and going
CHANGE = 0.2            # a colour, as something is chosen

# The furthest one frame moves a page on. Time that ran on through a long frame
# would skip the part of the move nobody saw; this only slows it for a moment.
MOST = 1 / 30
# How far through its entrance a page has all but arrived: the curve is 97% of
# the way there, and what is left is too little to see a frame missed.
SETTLED = 0.6


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
    """Android's touch ripple: from where the finger lands, a circle spreads
    across the shape, over a faint tint of all of it, and both fade once the
    finger lifts. They are the colour of what is on the thing pressed - its
    words or its symbol - as Material's state layers are.

    Drawn only while it shows - an empty group the rest of the time - since
    clipping every button on screen on every frame would cost the frames
    this is meant to use. radius is the shape's corners: one for all, four
    (top left, top right, bottom right, bottom left), or a function giving
    either. circle, a round one around the middle instead. color, a colour
    or a function giving one.
    """

    spread = NumericProperty(0.0)
    glow = NumericProperty(0.0)

    def __init__(self, widget, radius=dp(8), circle=False, color=(1, 1, 1, 1)):
        super().__init__()
        self.widget, self.radius, self.circle, self.color = widget, radius, circle, color
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
        (Animation(spread=1.0, d=0.45, t=STANDARD)
         & Animation(glow=1.0, d=0.08)).start(self)

    def release(self):
        if not self._pressed:
            return
        self._pressed = False
        Animation.cancel_all(self)
        (Animation(spread=1.0, d=0.2, t=STANDARD)
         & Animation(glow=0.0, d=0.4, t='in_quad')).start(self)

    def _draw(self, *_):
        group = self.group
        group.clear()
        if self.glow <= 0:
            return
        widget = self.widget
        x, y, width, height = widget.x, widget.y, widget.width, widget.height
        tint = self.color() if callable(self.color) else self.color
        if self.circle:
            reach = min(width, height, dp(40)) / 2 * (0.6 + 0.4 * self.spread)
            group.add(Color(*tint[:3], 0.12 * self.glow))
            group.add(Ellipse(pos=(widget.center_x - reach, widget.center_y - reach),
                              size=(2 * reach, 2 * reach)))
            return
        radius = self.radius() if callable(self.radius) else self.radius
        corners = list(radius) if isinstance(radius, (list, tuple)) else [radius] * 4
        corners = [min(r, width / 2, height / 2) for r in corners]
        ox, oy = self.origin
        # Far enough to reach the corner furthest from the finger.
        reach = max(math.hypot(ox - cx, oy - cy)
                    for cx in (x, x + width) for cy in (y, y + height))
        reach *= 0.15 + 0.85 * self.spread

        def shape():
            return RoundedRectangle(pos=(x, y), size=(width, height), radius=corners)

        group.add(StencilPush())
        group.add(shape())
        group.add(StencilUse())
        group.add(Color(*tint[:3], 0.05 * self.glow))
        group.add(Rectangle(pos=(x, y), size=(width, height)))
        group.add(Color(*tint[:3], 0.08 * self.glow))
        group.add(Ellipse(pos=(ox - reach, oy - reach), size=(2 * reach, 2 * reach)))
        group.add(StencilUnUse())
        group.add(shape())
        group.add(StencilPop())


def ripple(button, radius=dp(8), circle=False, color=(1, 1, 1, 1)) -> Ripple:
    """A ripple under a button's finger - anything with ButtonBehavior."""
    effect = Ripple(button, radius=radius, circle=circle, color=color)

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
        eased = STANDARD(progress)
        color.rgba = tuple(a + (b - a) * eased for a, b in zip(start, target))
        if progress >= 1.0:
            _blending.pop(id(color), None)
            return False
        return True

    _blending[id(color)] = Clock.schedule_interval(step, 0)


def glide(widget, name, target, duration=CHANGE, curve=None, then=None):
    """Move a widget's number to `target`, never more than MOST in one frame, so
    a frame that runs long slows the move a moment and hides none of it. Gives
    what stops it (cancel); then() once it has arrived."""
    start, moved = getattr(widget, name), [0.0]

    def step(dt):
        moved[0] += min(dt, MOST)
        progress = min(1.0, moved[0] / duration) if duration > 0 else 1.0
        eased = curve(progress) if curve is not None else progress
        setattr(widget, name, target if progress >= 1.0 else start + (target - start) * eased)
        if progress < 1.0:
            return True
        if then is not None:
            then()
        return False

    return Clock.schedule_interval(step, 0)


def fade_in(widget, duration=ENTER):
    """Bring a widget that has just been put on screen up from nothing."""
    Animation.cancel_all(widget, 'opacity')
    widget.opacity = 0
    Animation(opacity=1, d=duration, t=EMPHASIZED_DECELERATE).start(widget)


# ---------------------------------------------------- pages, dialogs, sheets
class Entrance:
    """How a ModalView comes and goes, mixed in before it - Material's
    transitions for each kind of thing.

    'page' - a screen of its own - slides in a little way from the right as
    it fades in, and back out to the right: forward and back. 'card' - a
    dialog - grows in from slightly smaller. 'sheet' rises from the bottom
    edge and sinks back into it. Over the app behind, Kivy fades the scrim.
    """

    entrance = 'page'

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._moved = None              # (widget moved, its Translate or Scale)
        self._shown = 0.0
        self._leaving = False
        self._glide = None              # the clock moving it, while it moves
        self._start = None              # the wait for its first frame
        self._waiting = []              # what when_settled was given
        self.fbind('_anim_alpha', self._move)

    def open(self, *args, **kwargs):
        if self._is_open:
            return
        self._anim_duration = ENTER
        self._shown = 0.0
        self._move(self, 0.0)           # hidden from the very first frame
        super().open(*args, **kwargs)
        Animation.cancel_all(self, '_anim_alpha')       # Kivy's: this one is below
        # The first frame with it in is the slow one - everything in it is laid
        # out and its words drawn. The move starts once that is on the screen.
        Window.bind(on_flip=self._drawn)
        self._start = Clock.schedule_once(self._drawn, 0.25)

    def _drawn(self, *_):
        Window.unbind(on_flip=self._drawn)
        if self._start is None:
            return
        self._start.cancel()
        self._start = None
        self._glide_to(1.0, ENTER, lambda: self.dispatch('on_open'))

    def when_settled(self, then):
        """then() once this has all but arrived - for work that would take the
        interpreter from the frames of its entrance. Not if it is closed first."""
        if self._anim_alpha >= SETTLED and not self._leaving:
            then()
        else:
            self._waiting.append(then)

    def dismiss(self, *args, **kwargs):
        if not self._is_open or (self._glide is not None and self._leaving):
            return
        Window.unbind(on_flip=self._drawn)
        if self._start is not None:
            self._start.cancel()
            self._start = None
        self._waiting.clear()
        self._stop_gliding()
        self._anim_duration = LEAVE
        super().dismiss(*args, **kwargs)
        Animation.cancel_all(self, '_anim_alpha')
        if self._anim_alpha <= 0:
            self._real_remove_widget()          # closed before it was ever seen
        else:
            self._leaving = True
            self._glide_to(0.0, LEAVE * self._anim_alpha)

    def _stop_gliding(self):
        if self._glide is not None:
            self._glide.cancel()
            self._glide = None

    def _glide_to(self, target, duration, then=None):
        self._stop_gliding()

        def arrived():
            self._glide = None
            if then is not None:
                then()

        self._glide = glide(self, '_anim_alpha', target, duration, then=arrived)

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
        if self._waiting and not self._leaving and alpha >= SETTLED:
            waiting, self._waiting = self._waiting, []
            for then in waiting:
                then()
        target = self._moving()
        if target is None:
            return
        if self._moved is None or self._moved[0] is not target:
            step = Scale() if self.entrance == 'card' else Translate()
            target.canvas.before.insert(0, step)
            target.canvas.before.insert(0, PushMatrix())
            target.canvas.after.add(PopMatrix())
            self._moved = (target, step)
        # Kivy runs alpha evenly in time; the curve is put on here - one
        # that arrives gently, and one that leaves quickly.
        if self._leaving:
            progress = 1 - EMPHASIZED_ACCELERATE(1 - alpha)
        else:
            progress = EMPHASIZED_DECELERATE(alpha)
        step = self._moved[1]
        # Seen over the first half of its time, evenly. On the curve, it would
        # be two thirds there by the second frame: a jump, not a fade.
        seen = min(1.0, alpha * 2)
        if self.entrance == 'page':
            target.opacity = seen
            step.x = (1 - progress) * dp(30)
        elif self.entrance == 'card':
            target.opacity = seen
            step.origin = target.center
            step.x = step.y = 0.9 + 0.1 * progress
        else:
            target.opacity = 1
            step.y = -(1 - progress) * target.height

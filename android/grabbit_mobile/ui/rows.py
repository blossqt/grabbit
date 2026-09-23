"""One download in the list.

The desktop shows eleven columns. A phone cannot, so the same facts are
arranged as a name, a bar and a line of detail underneath - and the things
you actually do to a download are the things you can tap. Holding one picks
it out, as holding an item does in Android's own lists, and from then on a
tap picks or drops a download rather than acting on it (main.py).
"""

import time

from kivy.clock import Clock
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button

from grabbit.tasks import State
from grabbit.util import human_eta, human_size, human_speed

from ..files import finished
from . import theme
from .widgets import Card, IconButton, KindGlyph, ProgressTrack

ROW_HEIGHT = dp(84)

# A long press, as Android times one: how long a finger rests, and how far it
# may wander meanwhile.
HOLD = 0.5
WANDER = dp(12)

# A picked-out download: the accent, faintly, with its border drawn in it.
CHOSEN_FILL = theme.rgba(theme.ACCENT, 0.16)


def detail_line(task, hints: bool = True) -> str:
    """Everything the desktop's columns say, in one sentence - and what a tap
    on it does, unless a tap is picking downloads out instead."""
    if task.state == State.ERROR:
        # What went wrong can run long, so the way out comes first; the
        # details sheet has all of it.
        failed = 'Failed - tap to try again' if hints else 'Failed'
        return '   ·   '.join(b for b in (failed, task.error) if b)
    bits = [task.status_text]
    if task.total:
        bits.append(f'{human_size(task.done)} / {human_size(task.total)}')
    elif task.done:
        bits.append(human_size(task.done))
    if task.down_speed:
        bits.append(f'↓ {human_speed(task.down_speed)}')
    if task.up_speed:
        bits.append(f'↑ {human_speed(task.up_speed)}')
    if task.eta:
        bits.append(human_eta(task.eta))
    if task.is_torrent and task.connections:
        bits.append(f'{task.seeds} seed(s), {max(0, task.connections - task.seeds)} peer(s)')
    # Only the download that is waiting for you gets told what a tap does. On
    # a running one the same hint just pushes the numbers off the end.
    if hints and task.state == State.PAUSED:
        bits.append('tap to start')
    return '   ·   '.join(b for b in bits if b)


class TaskRow(Card):
    """A card per download: glyph, name, progress, detail - and share, for
    a finished one, details and remove.

    on_select picks it for the graph and on_open opens what it made - a tap
    on the name does whichever fits; on_toggle is the detail line's;
    on_hold is a long press, and on_choose a tap while downloads are being
    picked out. Each is called with the download's id.
    """

    def __init__(self, on_select=None, on_details=None, on_toggle=None, on_remove=None,
                 on_open=None, on_share=None, on_hold=None, on_choose=None, **kwargs):
        super().__init__(orientation='vertical', size_hint_y=None, height=ROW_HEIGHT,
                         padding=[dp(10), dp(8)], spacing=dp(5), fill=theme.ALT, **kwargs)
        self.task_id = ''
        self.finished = False
        # None as usual; while downloads are being picked out, whether this one is.
        self.choosing = None
        self._fill = theme.ALT
        self._on_hold, self._on_choose = on_hold, on_choose
        self._hold = None           # (touch, timer) while a long press is waited for

        self.heading = BoxLayout(size_hint_y=None, height=dp(26), spacing=dp(8))
        self.glyph = KindGlyph()
        self.title = Button(text='', color=theme.TEXT, font_size=dp(14), halign='left',
                            valign='middle', shorten=True, shorten_from='right',
                            background_normal='', background_down='',
                            background_color=theme.TRANSPARENT)
        self.title.bind(size=lambda widget, value: setattr(widget, 'text_size', value))
        # The name opens what a finished download made, and otherwise picks
        # the download the graph is drawing.
        self.title.bind(on_release=lambda button: self._tap(
            button, on_open if self.finished else on_select))

        def icon(kind, extent, action):
            button = IconButton(kind, extent=dp(extent), size_hint_x=None, width=dp(32))
            button.bind(on_release=lambda pressed: self._tap(pressed, action))
            return button

        self.share_button = icon('share', 18, on_share)
        self.details_button = icon('info', 18, on_details)
        self.remove_button = icon('close', 12, on_remove)
        self.heading.add_widget(self.glyph)
        self.heading.add_widget(self.title)

        self.bar = ProgressTrack()
        # The detail line is also the start/pause control: a button, so it
        # behaves inside a scrolling list.
        self.detail = Button(text='', color=theme.DIM, font_size=dp(11), halign='left',
                             valign='middle', size_hint_y=None, height=dp(20),
                             shorten=True, shorten_from='right',
                             background_normal='', background_down='',
                             background_color=theme.TRANSPARENT)
        self.detail.bind(size=lambda widget, value: setattr(widget, 'text_size', value))
        self.detail.bind(on_release=lambda pressed: self._tap(pressed, on_toggle))

        self.add_widget(self.heading)
        self.add_widget(self.bar)
        self.add_widget(self.detail)
        self._arrange()

    def show(self, task, selected: bool = False, choosing=None):
        """selected: the graph's download. choosing: None as usual, or - while
        downloads are being picked out - whether this one is."""
        self.task_id = task.id
        self.finished = finished(task)
        self.choosing = choosing
        if choosing is None:
            self.glyph.show(task)
        else:
            self.glyph.show_choice(choosing)
        self.title.text = task.name or task.source
        self.bar.show(task.progress, theme.state_color(task.state))
        self.detail.text = detail_line(task, hints=choosing is None)
        self.detail.color = theme.state_color(task.state) if task.state in (
            State.ERROR, State.COMPLETED, State.SEEDING) else theme.DIM
        if choosing is None:
            fill = theme.HOVER if selected else theme.ALT
        else:
            fill = CHOSEN_FILL if choosing else theme.ALT
        if fill != self._fill:
            self._fill = fill
            self.set_fill(fill)
            self.set_border(theme.BLUE if fill is CHOSEN_FILL else theme.BORDER)
        self._arrange()

    def _arrange(self):
        """The buttons this download has now: none while picking, share once
        it is finished. Taken out rather than hidden - a hidden button is
        still there to be touched."""
        if self.choosing is not None:
            wanted = []
        else:
            wanted = ([self.share_button] if self.finished else []) + [
                self.details_button, self.remove_button]
        buttons = (self.share_button, self.details_button, self.remove_button)
        if [w for w in reversed(self.heading.children) if w in buttons] == wanted:
            return
        for button in buttons:
            if button.parent is not None:
                self.heading.remove_widget(button)
        for button in wanted:
            self.heading.add_widget(button)

    def _tap(self, button, action):
        # The finger that held a download down, lifted, is not also a tap.
        touch = button.last_touch
        if touch is not None and touch.ud.get('grabbit.held'):
            return
        if action is not None:
            action(self.task_id)

    # ------------------------------------------------------------- touches
    def on_touch_down(self, touch):
        if not self.collide_point(*touch.pos) or touch.is_mouse_scrolling:
            return super().on_touch_down(touch)
        if self.choosing is not None:
            # While picking, the whole card is one thing to tap.
            touch.grab(self)
            return True
        handled = super().on_touch_down(touch)
        self._wait_for_hold(touch)
        return handled

    def on_touch_up(self, touch):
        if self._hold is not None and self._hold[0] is touch:
            self._hold[1].cancel()
            self._hold = None
        if touch.grab_current is self:
            touch.ungrab(self)
            if self.choosing is not None and self.collide_point(*touch.pos) and self._on_choose:
                self._on_choose(self.task_id)
            return True
        return super().on_touch_up(touch)

    def _wait_for_hold(self, touch):
        """Count a finger resting here as a long press - from when it landed,
        since the list keeps a touch a moment before handing it on."""
        if self._hold is not None:
            self._hold[1].cancel()
        start = touch.spos

        def held(_):
            self._hold = None
            if touch.time_end != -1 or self.choosing is not None or self.parent is None:
                return                  # lifted, or already picking
            moved = ((touch.sx - start[0]) * Window.width) ** 2 + (
                (touch.sy - start[1]) * Window.height) ** 2
            if moved > WANDER ** 2:
                return                  # a scroll, not a press
            touch.ud['grabbit.held'] = True
            if self._on_hold is not None:
                self._on_hold(self.task_id)

        self._hold = (touch, Clock.schedule_once(
            held, max(0.0, HOLD - (time.time() - touch.time_start))))

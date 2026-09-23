"""One download in the list.

The desktop shows eleven columns. A phone cannot, so the same facts are
arranged as a name, a bar and a line of detail underneath - and the three
things you actually do to a download are the three things you can tap.
"""

from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label

from grabbit.tasks import State
from grabbit.util import human_eta, human_size, human_speed

from . import theme
from .widgets import Card, KindGlyph, ProgressTrack

ROW_HEIGHT = dp(84)


def detail_line(task) -> str:
    """Everything the desktop's columns say, in one sentence."""
    if task.state == State.ERROR:
        # What went wrong can run long, so the way out comes first; the
        # details sheet has all of it.
        return '   ·   '.join(b for b in ('Failed - tap to try again', task.error) if b)
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
    # Only the download that is waiting for you gets told how to start it. On
    # a running one the same hint just pushes the numbers off the end.
    if task.state == State.PAUSED:
        bits.append('tap to start')
    return '   ·   '.join(b for b in bits if b)


class TaskRow(Card):
    """A card per download: glyph, name, progress, detail, remove."""

    def __init__(self, on_select=None, on_open=None, on_toggle=None, on_remove=None, **kwargs):
        super().__init__(orientation='vertical', size_hint_y=None, height=ROW_HEIGHT,
                         padding=[dp(10), dp(8)], spacing=dp(5), fill=theme.ALT, **kwargs)
        self.task_id = ''
        self._selected = False

        heading = BoxLayout(size_hint_y=None, height=dp(26), spacing=dp(8))
        self.glyph = KindGlyph()
        self.title = Button(text='', color=theme.TEXT, font_size=dp(14), halign='left',
                            valign='middle', shorten=True, shorten_from='right',
                            background_normal='', background_down='',
                            background_color=theme.TRANSPARENT)
        self.title.bind(size=lambda widget, value: setattr(widget, 'text_size', value))
        # Tapping the name picks the download the graph is drawing.
        self.title.bind(on_release=lambda *_: on_select and on_select(self.task_id))

        def flat(symbol, width, size, action):
            button = Button(text=symbol, size_hint_x=None, width=dp(width), font_size=dp(size),
                            color=theme.DIM, background_normal='', background_down='',
                            background_color=theme.TRANSPARENT)
            button.bind(on_release=lambda *_: action and action(self.task_id))
            return button

        heading.add_widget(self.glyph)
        heading.add_widget(self.title)
        heading.add_widget(flat('i', 30, 15, on_open))
        heading.add_widget(flat('×', 32, 20, on_remove))

        self.bar = ProgressTrack()
        # The detail line is also the start/pause control: a button, so it
        # behaves inside a scrolling list.
        self.detail = Button(text='', color=theme.DIM, font_size=dp(11), halign='left',
                             valign='middle', size_hint_y=None, height=dp(20),
                             shorten=True, shorten_from='right',
                             background_normal='', background_down='',
                             background_color=theme.TRANSPARENT)
        self.detail.bind(size=lambda widget, value: setattr(widget, 'text_size', value))
        self.detail.bind(on_release=lambda *_: on_toggle and on_toggle(self.task_id))

        self.add_widget(heading)
        self.add_widget(self.bar)
        self.add_widget(self.detail)

    def show(self, task, selected: bool = False):
        self.task_id = task.id
        self.glyph.show(task)
        self.title.text = task.name or task.source
        self.bar.show(task.progress, theme.state_color(task.state))
        self.detail.text = detail_line(task)
        self.detail.color = theme.state_color(task.state) if task.state in (
            State.ERROR, State.COMPLETED, State.SEEDING) else theme.DIM
        if selected != self._selected:
            self._selected = selected
            self.set_fill(theme.HOVER if selected else theme.ALT)

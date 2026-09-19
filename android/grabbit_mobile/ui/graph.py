"""The speed graph, drawn the same way the desktop draws it.

Same history, same colours, same two-minute window; only the drawing is Kivy
instead of Qt. It shows one download when one is selected and everything at
once otherwise, which is what makes it worth the room it takes up.
"""

from kivy.graphics import Color, Line, Mesh
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from grabbit.speeds import SpeedHistory
from grabbit.util import human_size, human_speed

from . import theme
from .widgets import Card

GLOBAL = ''


class Plot(Widget):
    """The curves themselves: a filled area for down, a line for up."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.down = []
        self.up = []
        self.scale = 65536
        self.bind(pos=self._redraw, size=self._redraw)

    def show(self, history: SpeedHistory):
        self.down = list(history.down)
        self.up = list(history.up)
        self.scale = history.scale()
        self._redraw()

    def _redraw(self, *_):
        self.canvas.clear()
        if self.width < dp(8) or self.height < dp(8):
            return
        with self.canvas:
            Color(*theme.rgba(theme.PALETTE['border'], 0.55))
            for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
                y = self.y + self.height * fraction
                Line(points=[self.x, y, self.right, y], width=1.0,
                     dash_length=1 if fraction == 0 else 4,
                     dash_offset=0 if fraction == 0 else 4)
            self._draw_area(self.down, theme.BLUE)
            self._draw_line(self.up, theme.GREEN)

    def _points(self, values):
        count = len(values)
        if count < 2:
            return []
        step = self.width / (count - 1)
        points = []
        for index, value in enumerate(values):
            height = min(1.0, (value or 0) / self.scale) * self.height
            points.extend([self.x + index * step, self.y + height])
        return points

    def _draw_area(self, values, color):
        points = self._points(values)
        if not points:
            return
        # A triangle strip between the baseline and the curve; Kivy has no
        # filled-polygon primitive, and this is what one looks like.
        vertices = []
        indices = []
        for index in range(0, len(points), 2):
            x, y = points[index], points[index + 1]
            vertices.extend([x, self.y, 0, 0, x, y, 0, 0])
            indices.extend([len(indices), len(indices) + 1])
        Color(*color[:3], 0.28)
        Mesh(vertices=vertices, indices=indices, mode='triangle_strip')
        Color(*color)
        Line(points=points, width=dp(1.1))

    def _draw_line(self, values, color):
        points = self._points(values)
        if points:
            Color(*color)
            Line(points=points, width=dp(1.1))


class SpeedGraph(Card):
    """Header, axis and plot - the desktop pane, narrower."""

    def __init__(self, store=None, samples: int = 120, **kwargs):
        super().__init__(orientation='vertical', padding=[dp(10), dp(8)], spacing=dp(2), **kwargs)
        self.store = store
        self._samples = samples
        self._history = {GLOBAL: SpeedHistory(samples)}
        self.task_id = ''

        header = BoxLayout(size_hint_y=None, height=dp(34), spacing=dp(6))
        titles = BoxLayout(orientation='vertical')
        self.caption = Label(text='All downloads', color=theme.TEXT, font_size=dp(13),
                             bold=True, halign='left', valign='middle', shorten=True,
                             shorten_from='right')
        self.subtitle = Label(text='', color=theme.DIM, font_size=dp(11),
                              halign='left', valign='middle', shorten=True)
        for label in (self.caption, self.subtitle):
            label.bind(size=lambda widget, value: setattr(widget, 'text_size', value))
        titles.add_widget(self.caption)
        titles.add_widget(self.subtitle)

        speeds = BoxLayout(orientation='vertical', size_hint_x=None, width=dp(104))
        self.down_label = Label(text='↓ 0 B/s', color=theme.BLUE, font_size=dp(12),
                                halign='right', valign='middle')
        self.up_label = Label(text='↑ 0 B/s', color=theme.GREEN, font_size=dp(12),
                              halign='right', valign='middle')
        for label in (self.down_label, self.up_label):
            label.bind(size=lambda widget, value: setattr(widget, 'text_size', value))
        speeds.add_widget(self.down_label)
        speeds.add_widget(self.up_label)

        header.add_widget(titles)
        header.add_widget(speeds)
        self.add_widget(header)

        body = BoxLayout(spacing=dp(4))
        gutter = FloatLayout(size_hint_x=None, width=dp(64))
        self.scale_labels = []
        # No text_size on these: it would let them wrap, and "128.0 KiB/s" on
        # two lines lands on top of the heading. They sit where pos_hint puts
        # them, just inside the top and bottom of the plot.
        for fraction in (0.96, 0.5, 0.04):
            label = Label(text='', color=theme.DIM, font_size=dp(10),
                          size_hint=(None, None), height=dp(14),
                          pos_hint={'right': 1, 'center_y': fraction})
            label.bind(texture_size=lambda widget, size: setattr(widget, 'size', size))
            gutter.add_widget(label)
            self.scale_labels.append(label)
        self.plot = Plot()
        body.add_widget(gutter)
        body.add_widget(self.plot)
        self.add_widget(body)

        footer = BoxLayout(size_hint_y=None, height=dp(14))
        for text, align in (('2 min ago', 'left'), ('now', 'right')):
            label = Label(text=text, color=theme.DIM, font_size=dp(10),
                          halign=align, valign='middle')
            label.bind(size=lambda widget, value: setattr(widget, 'text_size', value))
            footer.add_widget(label)
        self.add_widget(footer)

    # ------------------------------------------------------------ feeding it
    def select(self, task_id: str):
        self.task_id = task_id or ''
        self.refresh()

    def push(self, down: int, up: int = 0):
        self._history[GLOBAL].push(down, up)

    def push_tasks(self, store):
        live = {GLOBAL}
        for task in store:
            live.add(task.id)
            history = self._history.get(task.id)
            if history is None:
                history = self._history[task.id] = SpeedHistory(self._samples)
            history.push(task.down_speed, task.up_speed)
        for key in [k for k in self._history if k not in live]:
            del self._history[key]

    def refresh(self):
        history = self._history.get(self.task_id or GLOBAL) or self._history[GLOBAL]
        task = self.store.get(self.task_id) if (self.store and self.task_id) else None
        if task is None:
            count = len(list(self.store)) if self.store else 0
            self.caption.text = 'All downloads'
            self.subtitle.text = f'{count} in the list'
        else:
            self.caption.text = task.name or task.source
            self.subtitle.text = (f'{task.status_text} · {human_size(task.done)} of '
                                  f'{human_size(task.total) or "unknown"}')
        down, up = history.latest
        self.down_label.text = f'↓ {human_speed(down) or "0 B/s"}'
        self.up_label.text = f'↑ {human_speed(up) or "0 B/s"}'
        scale = history.scale()
        for label, fraction in zip(self.scale_labels, (1.0, 0.5, 0.0)):
            label.text = human_speed(int(scale * fraction)) or '0 B/s'
        self.plot.show(history)

"""The speed graph pane.

Two minutes of download and upload speed, for one download or for everything
at once. A downloader without one leaves you guessing whether a
stalled-looking transfer is actually moving.
"""

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from ..speeds import SpeedHistory
from ..util import human_size, human_speed

DOWN = QColor('#3a86ff')
UP = QColor('#1faa59')

GLOBAL = ''          # the key the totals are kept under


class SpeedGraph(QWidget):
    """Draws SpeedHistory. Fed from the window's once-a-second tick."""

    def __init__(self, store=None, parent=None, samples: int = 120):
        super().__init__(parent)
        self.store = store
        self._history = {GLOBAL: SpeedHistory(samples)}
        self._samples = samples
        self._task_id = ''
        self.setMinimumHeight(120)

    # ------------------------------------------------------------ feeding it
    def select(self, task_ids):
        """Show one download, or the totals when nothing is selected."""
        self._task_id = (task_ids or [''])[0] if task_ids else ''
        self.update()

    def push_tasks(self, store):
        """One sample per download.

        Every task gets a history whether or not it is the one on screen, so
        selecting a download shows where it has been rather than starting from
        a blank graph.
        """
        live = {GLOBAL}
        for task in store:
            live.add(task.id)
            history = self._history.get(task.id)
            if history is None:
                history = self._history[task.id] = SpeedHistory(self._samples)
            history.push(task.down_speed, task.up_speed)
        for key in [k for k in self._history if k not in live]:
            del self._history[key]
        self.update()

    def push(self, down: int, up: int = 0):
        """aria2's own totals, which are the authority for the whole app."""
        self._history[GLOBAL].push(down, up)
        self.update()

    def _titles(self) -> tuple:
        task = self.store.get(self._task_id) if (self.store and self._task_id) else None
        if task is None:
            count = len(list(self.store)) if self.store else 0
            return 'All downloads', f'{count} in the list'
        return (task.name or task.source,
                f'{task.status_text} · {human_size(task.done)} of '
                f'{human_size(task.total) or "unknown"}')

    # --------------------------------------------------------------- drawing
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        base = self.palette().base().color()
        border = self.palette().mid().color()
        dim = self.palette().placeholderText().color()
        text = self.palette().windowText().color()

        area = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(border, 1))
        painter.setBrush(base)
        painter.drawRoundedRect(area, 8, 8)

        history = self._history.get(self._task_id or GLOBAL) or self._history[GLOBAL]
        scale = history.scale()
        metrics = QFontMetrics(painter.font())
        gutter = max(46, metrics.horizontalAdvance(human_speed(scale) or '') + 10)

        header = QRectF(area.left() + 10, area.top() + 6, area.width() - 20, 34)
        plot = QRectF(area.left() + gutter + 6, header.bottom() + 4,
                      area.width() - gutter - 22, area.bottom() - header.bottom() - 26)
        if plot.height() < 20 or plot.width() < 20:
            painter.end()
            return

        self._draw_header(painter, header, history, text, dim)
        self._draw_grid(painter, plot, gutter, scale, border, dim)
        self._draw_series(painter, plot, history.down, scale, DOWN, fill=True)
        self._draw_series(painter, plot, history.up, scale, UP, fill=False)

        painter.setPen(dim)
        painter.drawText(QRectF(plot.left(), plot.bottom() + 3, plot.width(), 16),
                         Qt.AlignLeft, '2 min ago')
        painter.drawText(QRectF(plot.left(), plot.bottom() + 3, plot.width(), 16),
                         Qt.AlignRight, 'now')
        painter.end()

    def _draw_header(self, painter, header, history, text, dim):
        caption, subtitle = self._titles()
        down, up = history.latest
        painter.setPen(text)
        font = painter.font()
        font.setBold(True)
        painter.setFont(font)
        elided = QFontMetrics(font).elidedText(caption, Qt.ElideMiddle,
                                               int(header.width() * 0.55))
        painter.drawText(header, Qt.AlignLeft | Qt.AlignTop, elided)
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(dim)
        painter.drawText(QRectF(header.left(), header.top() + 17, header.width(), 16),
                         Qt.AlignLeft, subtitle)

        painter.setPen(DOWN)
        painter.drawText(header, Qt.AlignRight | Qt.AlignTop,
                         f'↓ {human_speed(down) or "0 B/s"}')
        painter.setPen(UP)
        painter.drawText(QRectF(header.left(), header.top() + 17, header.width(), 16),
                         Qt.AlignRight, f'↑ {human_speed(up) or "0 B/s"}')

    @staticmethod
    def _draw_grid(painter, plot, gutter, scale, border, dim):
        faint = QColor(border.red(), border.green(), border.blue(), 130)
        for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
            y = plot.bottom() - plot.height() * fraction
            painter.setPen(QPen(faint, 1, Qt.SolidLine if fraction == 0 else Qt.DotLine))
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            if fraction in (0.5, 1.0):
                painter.setPen(dim)
                painter.drawText(QRectF(plot.left() - gutter - 4, y - 8, gutter, 16),
                                 Qt.AlignRight | Qt.AlignVCenter,
                                 human_speed(int(scale * fraction)) or '0 B/s')

    @staticmethod
    def _draw_series(painter: QPainter, plot: QRectF, values, scale: int,
                     color: QColor, fill: bool):
        count = len(values)
        if count < 2 or plot.width() <= 0:
            return
        step = plot.width() / (count - 1)

        def point(index, value):
            y = plot.bottom() - plot.height() * min(1.0, value / scale)
            return QPointF(plot.left() + index * step, y)

        path = QPainterPath(point(0, values[0]))
        for index, value in enumerate(values):
            if index:
                path.lineTo(point(index, value))

        if fill:
            shaded = QPainterPath(path)
            shaded.lineTo(plot.right(), plot.bottom())
            shaded.lineTo(plot.left(), plot.bottom())
            shaded.closeSubpath()
            gradient = QLinearGradient(0, plot.top(), 0, plot.bottom())
            gradient.setColorAt(0.0, QColor(color.red(), color.green(), color.blue(), 90))
            gradient.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 10))
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(gradient))
            painter.drawPath(shaded)

        painter.setPen(QPen(color, 1.6))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)

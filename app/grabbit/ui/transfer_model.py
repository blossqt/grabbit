"""Table model, filtering and painting for the transfer list."""

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QRectF, QSortFilterProxyModel, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QApplication, QStyledItemDelegate

from ..tasks import (KIND_IMAGE, KIND_MAGNET, KIND_MEDIA, KIND_TORRENT, RUNNING_STATES,
                     State)
from ..util import human_eta, human_size, human_speed, human_time
from . import icons

SORT_ROLE = Qt.UserRole + 1
TASK_ID_ROLE = Qt.UserRole + 2

COL_NAME, COL_SIZE, COL_PROGRESS, COL_STATUS, COL_PEERS, COL_DOWN, COL_UP, COL_ETA, \
    COL_RATIO, COL_ADDED, COL_PATH = range(11)

HEADERS = ['Name', 'Size', 'Progress', 'Status', 'Seeds / Peers', 'Down speed',
           'Up speed', 'ETA', 'Ratio', 'Added', 'Save path']

STATE_COLORS = {
    State.DOWNLOADING: '#3a86ff',
    State.EXTRACTING: '#3a86ff',
    State.METADATA: '#8a7cff',
    State.PROCESSING: '#8a7cff',
    State.SEEDING: '#1faa59',
    State.COMPLETED: '#1faa59',
    State.PAUSED: '#8d9299',
    State.QUEUED: '#8d9299',
    State.CHECKING: '#e8a33d',
    State.ERROR: '#e5484d',
}


def task_icon(task):
    color = STATE_COLORS.get(task.state, '#8d9299')
    if task.state == State.ERROR:
        return icons.icon('error', color, 18)
    if task.kind in (KIND_TORRENT, KIND_MAGNET):
        return icons.icon('seed' if task.state == State.SEEDING else 'magnet', color, 18)
    if task.kind == KIND_IMAGE:
        return icons.icon('image', color, 18)
    if task.kind == KIND_MEDIA:
        quality = (task.media or {}).get('quality', '')
        return icons.icon('music' if str(quality).startswith('audio') else 'video', color, 18)
    return icons.icon('file', color, 18)


class TransferModel(QAbstractTableModel):
    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self._ids: list = [t.id for t in engine.store]

    # ------------------------------------------------------------ Qt plumbing
    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._ids)

    def columnCount(self, parent=QModelIndex()):
        return len(HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return HEADERS[section]
        return None

    def task_at(self, row: int):
        if 0 <= row < len(self._ids):
            return self.engine.store.get(self._ids[row])
        return None

    def row_of(self, task_id: str) -> int:
        try:
            return self._ids.index(task_id)
        except ValueError:
            return -1

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        task = self.task_at(index.row())
        if task is None:
            return None
        column = index.column()

        if role == TASK_ID_ROLE:
            return task.id
        if role == Qt.DecorationRole and column == COL_NAME:
            return task_icon(task)
        if role == Qt.ToolTipRole:
            lines = [task.name or task.source]
            if task.error:
                lines.append(f'Error: {task.error}')
            if task.save_dir:
                lines.append(task.save_dir)
            return '\n'.join(lines)
        if role == Qt.TextAlignmentRole and column not in (COL_NAME, COL_STATUS, COL_PATH):
            return int(Qt.AlignRight | Qt.AlignVCenter)

        if role == SORT_ROLE:
            return {
                COL_NAME: (task.name or '').lower(),
                COL_SIZE: task.total,
                COL_PROGRESS: task.progress,
                COL_STATUS: task.state,
                COL_PEERS: task.seeds * 1000 + task.connections,
                COL_DOWN: task.down_speed,
                COL_UP: task.up_speed,
                COL_ETA: task.eta if task.eta is not None else float('inf'),
                COL_RATIO: task.ratio,
                COL_ADDED: task.added_at,
                COL_PATH: (task.save_dir or '').lower(),
            }.get(column, '')

        if role != Qt.DisplayRole:
            return None

        if column == COL_NAME:
            return task.name or task.source
        if column == COL_SIZE:
            return human_size(task.total) if task.total else ''
        if column == COL_PROGRESS:
            return task.progress          # painted by ProgressDelegate
        if column == COL_STATUS:
            return task.status_text
        if column == COL_PEERS:
            if task.is_torrent:
                return f'{task.seeds} / {max(0, task.connections - task.seeds)}'
            return str(task.connections) if task.connections else ''
        if column == COL_DOWN:
            return human_speed(task.down_speed)
        if column == COL_UP:
            return human_speed(task.up_speed) if task.is_torrent else ''
        if column == COL_ETA:
            return human_eta(task.eta) if task.state in RUNNING_STATES and task.eta else ''
        if column == COL_RATIO:
            return f'{task.ratio:.2f}' if task.is_torrent else ''
        if column == COL_ADDED:
            return human_time(task.added_at)
        if column == COL_PATH:
            return task.save_dir
        return None

    # ------------------------------------------------------------- mutations
    def add_task(self, task_id: str):
        if task_id in self._ids:
            return
        row = len(self._ids)
        self.beginInsertRows(QModelIndex(), row, row)
        self._ids.append(task_id)
        self.endInsertRows()

    def remove_task(self, task_id: str):
        row = self.row_of(task_id)
        if row < 0:
            return
        self.beginRemoveRows(QModelIndex(), row, row)
        self._ids.pop(row)
        self.endRemoveRows()

    def update_tasks(self, task_ids):
        rows = sorted(r for r in (self.row_of(i) for i in task_ids) if r >= 0)
        if not rows:
            return
        # One dataChanged per contiguous run of rows.
        start = previous = rows[0]
        for row in rows[1:]:
            if row == previous + 1:
                previous = row
                continue
            self.dataChanged.emit(self.index(start, 0), self.index(previous, len(HEADERS) - 1))
            start = previous = row
        self.dataChanged.emit(self.index(start, 0), self.index(previous, len(HEADERS) - 1))

    def reload(self):
        self.beginResetModel()
        self._ids = [t.id for t in self.engine.store]
        self.endResetModel()


class TransferFilter(QSortFilterProxyModel):
    """Sidebar status/type filters plus the toolbar search box."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSortRole(SORT_ROLE)
        self.setDynamicSortFilter(True)
        self.status = 'all'
        self.kind = 'all'
        self.search = ''

    def set_status(self, status: str):
        self.status = status
        self.invalidateFilter()

    def set_kind(self, kind: str):
        self.kind = kind
        self.invalidateFilter()

    def set_search(self, text: str):
        self.search = (text or '').strip().lower()
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):
        model = self.sourceModel()
        task = model.task_at(row)
        if task is None:
            return False

        status = self.status
        if status == 'downloading' and task.state not in (
                State.DOWNLOADING, State.QUEUED, State.METADATA, State.EXTRACTING, State.PROCESSING):
            return False
        if status == 'seeding' and task.state != State.SEEDING:
            return False
        if status == 'completed' and task.state != State.COMPLETED:
            return False
        if status == 'paused' and task.state != State.PAUSED:
            return False
        if status == 'active' and not (task.down_speed or task.up_speed):
            return False
        if status == 'inactive' and (task.down_speed or task.up_speed):
            return False
        if status == 'error' and task.state != State.ERROR:
            return False

        if self.kind == 'torrent' and not task.is_torrent:
            return False
        if self.kind == 'video' and task.kind != KIND_MEDIA:
            return False
        if self.kind == 'image' and task.kind != KIND_IMAGE:
            return False
        if self.kind == 'file' and (task.is_torrent or task.kind in (KIND_MEDIA, KIND_IMAGE)):
            return False

        if self.search:
            haystack = f'{task.name} {task.source} {task.site} {task.save_dir}'.lower()
            if self.search not in haystack:
                return False
        return True


class ProgressDelegate(QStyledItemDelegate):
    """Draws the progress column as a filled bar with the percentage on top."""

    def paint(self, painter: QPainter, option, index):
        self.initStyleOption(option, index)
        style = QApplication.style()
        style.drawPrimitive(style.PrimitiveElement.PE_PanelItemViewItem, option, painter, option.widget)

        value = index.data(Qt.DisplayRole)
        try:
            fraction = max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            fraction = 0.0

        task_id = index.data(TASK_ID_ROLE)
        state = None
        model = index.model()
        source = model.sourceModel() if isinstance(model, QSortFilterProxyModel) else model
        if source is not None and task_id:
            task = source.engine.store.get(task_id)
            state = task.state if task else None

        rect = QRectF(option.rect).adjusted(6, 6, -6, -6)
        radius = rect.height() / 2
        palette = option.palette
        track = QColor(palette.window().color())
        track = track.darker(112) if track.lightness() > 128 else track.lighter(135)

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(rect, radius, radius)

        if fraction > 0:
            fill = QRectF(rect)
            fill.setWidth(max(rect.height(), rect.width() * fraction))
            painter.setBrush(QColor(STATE_COLORS.get(state, '#3a86ff')))
            painter.drawRoundedRect(fill, radius, radius)

        text = f'{fraction * 100:.1f}%'
        painter.setPen(QPen(palette.windowText().color()))
        font = painter.font()
        font.setPointSizeF(max(7.0, font.pointSizeF() - 0.5))
        painter.setFont(font)
        painter.drawText(option.rect, Qt.AlignCenter, text)
        painter.restore()

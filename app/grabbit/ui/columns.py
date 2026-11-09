"""The transfer list's header: the columns always fit the list, and the sort arrow can be seen.

With room to spare, the name column takes it. As the list narrows, every column gives way together,
from the width it would like (roomy) to the least that still reads (tight), and only past that does
the list scroll sideways."""

from PySide6.QtCore import QByteArray, QEvent, QRectF, Qt, QTimer
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QHeaderView, QTreeView

from . import icons, theme

PAD = 6             # a section's padding, as theme.py's style sheet has it
MOST_SQUEEZED = 36  # below the tightest column, so Qt's own floor never decides
ARROW = 16          # the sort arrow's size
ARROW_GAP = 2       # between a label and its arrow
ARROW_STROKE = 2.6  # in the icon's 24 units: thicker than the suite's line, at this size


class FittingHeader(QHeaderView):
    """A horizontal header for a QTreeView. Set it on the view before the model."""

    def __init__(self, view: QTreeView, stretch: int, least: dict[int, int]):
        super().__init__(Qt.Horizontal, view)
        self.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)        # as QTreeView's own header
        self.setStretchLastSection(False)   # from the start: turned off after hiding a last column, Qt miscounts the length
        self._view = view
        self._stretch = stretch             # the column that takes what is spare
        self._least = least                 # column: the least its contents read in
        self._roomy: dict[int, int] = {}    # column: the width it would like
        self._share = 1.0                   # how far from tight to roomy the last fit went
        self._fitting = False
        self._held = False                  # the mouse is down on the header
        self._dragged: int | None = None
        self._kept: int | None = None
        self._soon = QTimer(self, singleShot=True, interval=0)
        self._soon.timeout.connect(lambda: self.fit(self._kept))
        self.sectionResized.connect(self._resized)
        self.sortIndicatorChanged.connect(lambda *_: self.fit())
        view.viewport().installEventFilter(self)

    # ---------------------------------------------------------------- set up and remembered
    def set_widths(self, widths: dict[int, int]) -> None:
        self._roomy.update(widths)
        self.settle()

    def settle(self) -> None:
        """What a saved state would overwrite, put back, then fitted. Qt's own arrow stays off:
        this class draws the arrow, and clicking a column still sorts."""
        self.setStretchLastSection(False)
        self.setMinimumSectionSize(MOST_SQUEEZED)
        self.setSectionResizeMode(QHeaderView.Interactive)
        self.setSectionResizeMode(self._stretch, QHeaderView.Fixed)     # its width is whatever is left
        self.setSortIndicatorShown(False)
        self.fit()

    def restore(self, state: QByteArray) -> None:
        self._fitting = True
        try:
            self.restoreState(state)
            for column in range(self.count()):
                if column != self._stretch and self.sectionSize(column) > 0:
                    self._roomy[column] = self.sectionSize(column)
        finally:
            self._fitting = False
        self.settle()

    def remembered(self) -> QByteArray:
        """The state to save: the widths wanted, not those a narrow window squeezed them to."""
        self._view.setUpdatesEnabled(False)
        self._fitting = True
        try:
            for column, width in self._roomy.items():
                if column < self.count() and not self.isSectionHidden(column):
                    self.resizeSection(column, width)
            return self.saveState()
        finally:
            self._fitting = False
            self.fit()
            self._view.setUpdatesEnabled(True)

    # ---------------------------------------------------------------- fitting
    def _tight(self, column: int) -> int:
        text = self.model().headerData(column, Qt.Horizontal, Qt.DisplayRole) or ''
        width = self.fontMetrics().horizontalAdvance(str(text)) + 2 * PAD
        if column == self.sortIndicatorSection():
            width += ARROW_GAP + ARROW
        return max(width, self._least.get(column, 0))

    def fit(self, keep: int | None = None) -> None:
        """Every column's width for the room there is. keep: a column just dragged stays as dragged."""
        if self._fitting or self._held or self.model() is None or not self.count():
            return
        for _ in range(3):                  # changing widths can bring a scroll bar, and with it less room
            room = self._view.viewport().width()
            self._fit_to(room, keep)
            if self._view.viewport().width() == room:
                break

    def _fit_to(self, room: int, keep: int | None) -> None:
        columns = [c for c in range(self.count()) if not self.isSectionHidden(c)]
        tight = {c: self._tight(c) for c in columns}
        roomy = {c: max(tight[c], self._roomy.get(c, tight[c])) for c in columns}
        if keep in tight:
            tight[keep] = roomy[keep] = self.sectionSize(keep)
        most, least = sum(roomy.values()), sum(tight.values())
        share = 1.0 if room >= most or most == least else max(0.0, (room - least) / (most - least))
        self._share = share
        sizes = {c: tight[c] + int((roomy[c] - tight[c]) * share) for c in columns}
        if self._stretch in sizes:
            sizes[self._stretch] += max(0, room - sum(sizes.values()))
        self._fitting = True
        try:
            for column, width in sizes.items():
                if self.sectionSize(column) != width:
                    self.resizeSection(column, width)
        finally:
            self._fitting = False

    def _resized(self, column: int, old: int, new: int) -> None:
        if self._fitting:
            return
        if self._held:
            if new:
                self._dragged = column
            return
        self._kept = None
        if old and new:                     # the handle double-clicked: fitted to its contents
            self._take(column, new)
            self._kept = column
        self._soon.start()                  # or a column hidden or shown. Not from inside Qt's own
                                            # resize: widths changed there leave its length wrong

    def _take(self, column: int, width: int) -> None:
        """The roomy width that a width chosen now stands for."""
        tight = self._tight(column)
        if 0 < self._share < 1:
            width = tight + round((width - tight) / self._share)
        self._roomy[column] = max(tight, width)

    def mousePressEvent(self, event):
        self._held = True
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self._held = False
        dragged, self._dragged = self._dragged, None
        if dragged is None:
            self.fit()
        else:
            self._take(dragged, self.sectionSize(dragged))
            self.fit(dragged)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Resize:
            self.fit()
        return super().eventFilter(watched, event)

    # ---------------------------------------------------------------- the arrow
    def paintSection(self, painter: QPainter, rect, logical: int) -> None:
        super().paintSection(painter, rect, logical)
        if logical != self.sortIndicatorSection():
            return
        text = self.model().headerData(logical, Qt.Horizontal, Qt.DisplayRole) or ''
        after = rect.left() + PAD + self.fontMetrics().horizontalAdvance(str(text)) + ARROW_GAP
        x = min(after, rect.right() - ARROW)
        y = rect.top() + (rect.height() - ARROW) / 2
        name = 'up' if self.sortIndicatorOrder() == Qt.AscendingOrder else 'down'
        arrow = icons.icon(name, theme.ink(), ARROW, ARROW_STROKE)
        arrow.paint(painter, QRectF(x, y, ARROW, ARROW).toRect())

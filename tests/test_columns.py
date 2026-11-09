"""The transfer list's columns: they fit the list at any width, and the wanted widths survive being squeezed.
    <Grabbit's venv python> tests\\test_columns.py"""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent, QStandardItemModel  # noqa: E402
from PySide6.QtCore import QEvent, QPointF  # noqa: E402
from PySide6.QtWidgets import QApplication, QTreeView, QWidget  # noqa: E402

app = QApplication.instance() or QApplication([])

from grabbit.ui import suitesettings, suitetheme, theme  # noqa: E402
from grabbit.ui.columns import FittingHeader  # noqa: E402

theme.install(app)
LABELS = ['Name', 'Size', 'Progress', 'Status', 'Added', 'Save path']
WANTED = {0: 320, 1: 80, 2: 100, 3: 150, 4: 120, 5: 200}
LEAST = {0: 140, 1: 66, 3: 90}


def make(width=1200):
    model = QStandardItemModel(0, len(LABELS))
    model.setHorizontalHeaderLabels(LABELS)
    view = QTreeView()
    header = FittingHeader(view, 0, LEAST)
    view.setHeader(header)
    view.setModel(model)
    view.setSortingEnabled(True)
    header.setSectionHidden(5, True)
    view.sortByColumn(4, Qt.DescendingOrder)
    header.set_widths(WANTED)
    view.resize(width, 300)
    view.show()
    app.processEvents()
    return view, header, model


def total(header):
    return sum(header.sectionSize(c) for c in range(header.count()) if not header.isSectionHidden(c))


def click(header, section):
    point = QPointF(header.sectionViewportPosition(section) + 20, 8)
    for kind in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
        event = QMouseEvent(kind, point, header.mapToGlobal(point), Qt.LeftButton,
                            Qt.LeftButton if kind == QEvent.MouseButtonPress else Qt.NoButton, Qt.NoModifier)
        app.sendEvent(header.viewport(), event)
    app.processEvents()


class Fit(unittest.TestCase):
    def setUp(self):
        self.view, self.header, self.model = make()
        self.addCleanup(self.view.close)

    def room(self):
        return self.view.viewport().width()

    def test_wide_the_name_takes_what_is_spare(self):
        self.view.resize(1600, 300)
        app.processEvents()
        self.assertEqual(total(self.header), self.room())
        self.assertEqual(self.header.sectionSize(1), 80)
        self.assertEqual(self.header.sectionSize(0), self.room() - 80 - 100 - 150 - 120)

    def test_narrower_every_column_gives_way_and_there_is_no_scrolling(self):
        least = sum(self.header._tight(c) for c in range(5))     # font widths differ from run to run here
        self.view.resize(least + 40, 300)
        app.processEvents()
        self.assertEqual(total(self.header), self.room())
        self.assertLess(self.header.sectionSize(0), 320)
        self.assertLess(self.header.sectionSize(3), 150)
        self.assertEqual(self.view.horizontalScrollBar().maximum(), 0)

    def test_past_the_least_it_scrolls_and_never_cuts_below_it(self):
        self.view.resize(250, 300)
        app.processEvents()
        self.assertEqual(self.header.sectionSize(0), self.header._tight(0))
        self.assertEqual(self.header.sectionSize(1), self.header._tight(1))
        self.assertGreater(self.view.horizontalScrollBar().maximum(), 0)

    def test_widening_again_brings_the_wanted_widths_back(self):
        self.view.resize(500, 300)
        app.processEvents()
        self.view.resize(1500, 300)
        app.processEvents()
        for column in (1, 2, 3, 4):
            self.assertEqual(self.header.sectionSize(column), WANTED[column])

    def test_a_saved_state_has_the_wanted_widths(self):
        self.view.resize(500, 300)
        app.processEvents()
        state = self.header.remembered()
        self.assertEqual(total(self.header), max(self.room(), total(self.header)))      # fitted again
        other, header, _ = make(1500)
        self.addCleanup(other.close)
        header.restore(state)
        self.assertEqual(header.sectionSize(3), 150)
        self.assertEqual(total(header), other.viewport().width())

    def test_a_saved_state_cant_turn_the_fit_off(self):
        self.header.setSectionResizeMode(0, self.header.ResizeMode.Interactive)
        self.header.setStretchLastSection(True)
        self.header.setSortIndicatorShown(True)
        state = self.header.saveState()
        self.header.restore(state)
        self.assertEqual(self.header.sectionResizeMode(0), self.header.ResizeMode.Fixed)
        self.assertFalse(self.header.stretchLastSection())
        self.assertFalse(self.header.isSortIndicatorShown())

    def test_showing_a_hidden_column_keeps_the_fit(self):
        self.header.setSectionHidden(5, False)
        app.processEvents()
        self.assertEqual(total(self.header), self.room())
        self.header.setSectionHidden(5, True)
        app.processEvents()
        self.assertEqual(total(self.header), self.room())

    def test_a_click_still_sorts_with_qts_arrow_off(self):
        self.assertEqual(self.header.sortIndicatorSection(), 4)
        click(self.header, 1)
        self.assertEqual(self.header.sortIndicatorSection(), 1)
        first = self.header.sortIndicatorOrder()
        click(self.header, 1)
        self.assertNotEqual(self.header.sortIndicatorOrder(), first)

    def test_a_dragged_column_keeps_its_width_and_the_rest_make_room(self):
        self.header._held = True
        self.header.resizeSection(3, 220)
        event = QMouseEvent(QEvent.MouseButtonRelease, QPointF(5, 5), self.header.mapToGlobal(QPoint(5, 5)),
                            Qt.LeftButton, Qt.NoButton, Qt.NoModifier)
        self.header.mouseReleaseEvent(event)
        self.assertEqual(self.header.sectionSize(3), 220)
        self.assertEqual(total(self.header), self.room())

    def test_the_sorted_column_has_room_for_its_arrow(self):
        tight_sorted = self.header._tight(4)
        click(self.header, 2)
        self.assertLess(self.header._tight(4), tight_sorted + 1)
        self.assertGreater(self.header._tight(2), self.header.fontMetrics().horizontalAdvance('Progress') + 12)


class EmptyPlace(unittest.TestCase):
    def make(self, height):
        room = QWidget()                # as the apps use it: a child, placed by its parent
        room.resize(500, 300)
        place = suitesettings.Empty('filter', 'Nothing matches', 'A line about it', room)
        place.setGeometry(0, 0, 500, height)
        room.show()
        app.processEvents()
        self.addCleanup(room.close)
        return place

    def bottoms_inside(self, place):
        for part in (place.chip, place.title, place.text):
            if part.isVisible():
                self.assertGreaterEqual(part.y(), 0)
                self.assertLessEqual(part.geometry().bottom(), place.height())

    def test_with_room_all_of_it_shows(self):
        place = self.make(300)
        self.assertTrue(place.chip.isVisible() and place.text.isVisible())

    def test_shorter_the_icon_goes_first_then_the_line(self):
        heights = {}
        for height in (120, 70, 28):
            place = self.make(height)
            heights[height] = (place.chip.isVisible(), place.text.isVisible(), place.title.isVisible())
            self.bottoms_inside(place)
        self.assertEqual(heights[70], (False, True, True))
        self.assertEqual(heights[28], (False, False, True))

    def test_nothing_overlaps_at_any_height(self):
        for height in range(24, 200, 6):
            place = self.make(height)
            shown = [p for p in (place.chip, place.title, place.text) if p.isVisible()]
            for upper, lower in zip(shown, shown[1:]):
                self.assertLessEqual(upper.geometry().bottom(), lower.y(), height)


class Timing(unittest.TestCase):
    def test_nothing_in_a_settings_window_takes_long(self):
        self.assertLessEqual(theme.GENTLE, 240)
        self.assertLessEqual(theme.STEADY, 180)
        self.assertLessEqual(theme.QUICK + suitetheme._STAGGER_GAP * suitetheme._STAGGER_MOST, 200)


if __name__ == '__main__':
    unittest.main()

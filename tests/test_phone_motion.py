"""The phone app's transitions: the link box in step with what opens over it, pages that a long
frame cannot skip, and nothing heavy done while something moves.

    %LOCALAPPDATA%\\GrabbitBuild\\venv\\Scripts\\python.exe tests\\test_phone_motion.py

Kivy runs for real, in a hidden window. Android's view above it is a stand-in: only a phone has one.
"""

import os
import sys
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'android'), str(ROOT / 'app')]
for name, value in (('KIVY_NO_ARGS', '1'), ('KIVY_NO_CONSOLELOG', '1'), ('KIVY_NO_FILELOG', '1'),
                    ('KIVY_METRICS_DENSITY', '1'), ('KCFG_GRAPHICS_WINDOW_STATE', 'hidden')):
    os.environ.setdefault(name, value)

from kivy.base import EventLoop  # noqa: E402
from kivy.clock import Clock  # noqa: E402
from kivy.core.window import Window  # noqa: E402
from kivy.uix.modalview import ModalView  # noqa: E402

EventLoop.ensure_window()

from grabbit import analyze as analyze_mod  # noqa: E402
from grabbit_mobile.ui import details, linkbox, motion, theme  # noqa: E402
from grabbit_mobile.ui.choose import ChoosePage  # noqa: E402

URL = 'https://example.com/a/rather/long/address/that/runs/well/past/the/end/of/the/box/it/is/in.mp4'


class Page(motion.Entrance, ModalView):
    def __init__(self, **kwargs):
        super().__init__(size_hint=(1, 1), background='', **kwargs)
        self.closed = 0
        self.opened = 0

    def on_dismiss(self):
        self.closed += 1

    def on_open(self):
        self.opened += 1


def frames(until=None, limit=3.0, count=None):
    began = time.perf_counter()
    done = 0
    while True:
        EventLoop.idle()
        done += 1
        if count is not None and done >= count:
            return
        if until is not None and until():
            return
        if time.perf_counter() - began > limit:
            raise AssertionError('that never happened')
        time.sleep(0.005)


def landed(page):
    frames(until=lambda: page._anim_alpha == 1)


def gone(page):
    frames(until=lambda: page.parent is None)


class TearDown(unittest.TestCase):
    def tearDown(self):
        for child in list(Window.children):
            Window.remove_widget(child)
            if isinstance(child, ModalView):
                child._is_open = False
        frames(count=2)


class Edit:
    def __init__(self, *_):
        self.visibility = linkbox.VISIBLE
        self.scrolled = 0

    def __getattr__(self, name):
        return lambda *a, **k: None

    def setVisibility(self, value):
        self.visibility = value

    def getScrollX(self):
        return self.scrolled


class Anything:
    def __init__(self, **values):
        self.__dict__.update(values)

    def __getattr__(self, name):
        return lambda *a, **k: Anything()


def public_class(name):
    if name == 'android.widget.EditText':
        return Edit
    if name == 'android.widget.FrameLayout$LayoutParams':
        return lambda *a: Anything()
    if name == 'java.lang.String':
        return str
    if name == 'android.view.ContextThemeWrapper':
        return lambda *a: Anything()
    return SimpleNamespace(parseColor=lambda value: 0, TRANSPARENT=0, IME_ACTION_GO=2, IME_FLAG_NO_EXTRACT_UI=1,
                           TYPE_CLASS_TEXT=1, TYPE_TEXT_VARIATION_URI=16, CENTER_VERTICAL=16)


class LinkBoxTests(TearDown):
    """The field is Android's and floats above everything Kivy draws."""

    def setUp(self):
        activity = Anything(getResources=lambda: Anything(getIdentifier=lambda *a: 0))
        pieces = (lambda f: f, object, lambda name: Anything(mActivity=activity, getSurface=lambda: None),
                  lambda signature: (lambda f: f))
        jnius = types.SimpleNamespace(cast=lambda name, value: value)
        for patcher in (mock.patch.object(linkbox, '_android', lambda: pieces),
                        mock.patch.object(linkbox, 'public_class', public_class),
                        mock.patch.dict(sys.modules, {'jnius': jnius})):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.box = linkbox.LinkBox(size_hint=(None, None), size=(300, 56))
        Window.add_widget(self.box)
        self.box.ready()
        self.addCleanup(lambda: Window.funbind('children', self.box.native._follow_cover))
        self.view = self.box.native.view
        self.box.text = URL

    def test_words_drawn_first_and_the_field_goes_with_the_frame(self):
        Page().open()
        self.assertTrue(self.box._copied, 'told at once, not at the next look')
        self.assertEqual(self.box._copy_color.rgba, list(theme.ON_SURFACE))
        self.assertEqual(self.view.visibility, linkbox.VISIBLE, 'the copy is not on the screen yet')
        frames(count=1)
        self.assertEqual(self.view.visibility, linkbox.GONE)

    def test_field_back_before_the_copy_goes(self):
        page = Page()
        page.open()
        landed(page)
        page.dismiss()
        gone(page)
        self.assertEqual(self.view.visibility, linkbox.VISIBLE, 'the moment the page has gone')
        self.assertTrue(self.box._copied, 'Android has yet to draw it')
        frames(until=lambda: not self.box._copied, limit=linkbox.HANDOVER + 0.5)

    def test_opened_again_during_the_handover(self):
        page = Page()
        page.open()
        landed(page)
        page.dismiss()
        gone(page)
        again = Page()
        again.open()
        frames(count=1)
        time.sleep(linkbox.HANDOVER + 0.02)
        frames(count=2)
        self.assertTrue(self.box._copied)
        self.assertEqual(self.view.visibility, linkbox.GONE)

    def test_copy_stays_in_the_box_as_far_along_as_the_field(self):
        self.view.scrolled = 120
        Page().open()
        room = self.box.width - 2 * linkbox.SIDE
        self.assertLessEqual(self.box._copy.size[0], room)
        self.assertEqual(self.box._copy.pos[0], self.box.x + linkbox.SIDE)
        self.assertGreater(self.box._copy.texture.uvpos[0], 0, 'its beginning has scrolled away')

    def test_cleared_while_covered(self):
        Page().open()
        self.box.set_text('')
        self.assertEqual(self.box._copy_color.rgba, list(theme.ON_SURFACE_VARIANT), 'the hint, as the empty field has')

    def test_covered_while_the_field_was_being_made(self):
        self.box.native.view = None
        self.box.native.visible = True
        page = Page()
        page.open()
        self.box.native.view = self.view
        self.box.native.follow()
        self.assertTrue(self.box._copied)


class EntranceTests(TearDown):
    def test_does_not_move_until_its_first_frame_is_out(self):
        page = Page()
        page.open()
        self.assertEqual(page._anim_alpha, 0)
        self.assertIsNone(page._glide)
        frames(count=1)
        self.assertIsNotNone(page._glide)
        landed(page)
        self.assertEqual(page.opened, 1)

    def test_a_long_frame_hides_none_of_the_move(self):
        page = Page()
        page.open()
        frames(count=2)
        before = page._anim_alpha
        time.sleep(0.25)
        frames(count=1)
        self.assertLessEqual(page._anim_alpha - before, motion.MOST / motion.ENTER + 0.001)

    def test_comes_into_view_evenly(self):
        page = Page()
        page.open()
        frames(until=lambda: page._anim_alpha > 0)
        self.assertAlmostEqual(page.opacity, min(1, page._anim_alpha * 2), places=3)
        self.assertLess(page.opacity, 0.5, 'not two thirds there by the second frame')

    def test_closed_before_it_was_seen(self):
        page = Page()
        page.open()
        page.dismiss()
        self.assertIsNone(page.parent, 'at 0 already: nothing would ever move it off the screen')
        time.sleep(0.3)
        frames(count=2)
        self.assertEqual(page.opened, 0)

    def test_closed_once_however_often_it_is_asked(self):
        page = Page()
        page.open()
        landed(page)
        page.dismiss()
        page.dismiss()
        gone(page)
        self.assertEqual(page.closed, 1)

    def test_turned_back_as_it_opens(self):
        page = Page()
        page.open()
        frames(until=lambda: page._anim_alpha > 0.3)
        page.dismiss()
        gone(page)
        self.assertEqual(page.opened, 0)

    def test_work_waits_until_it_has_all_but_arrived(self):
        page = Page()
        called = []
        page.open()
        page.when_settled(lambda: called.append(page._anim_alpha))
        frames(count=2)
        self.assertEqual(called, [])
        landed(page)
        self.assertEqual(len(called), 1)
        self.assertGreaterEqual(called[0], motion.SETTLED)
        self.assertLess(called[0], 1, 'not held until the very end')

    def test_no_work_for_a_page_closed_first(self):
        page = Page()
        called = []
        page.open()
        page.when_settled(lambda: called.append(1))
        frames(until=lambda: page._anim_alpha > 0.2)
        page.dismiss()
        gone(page)
        self.assertEqual(called, [])

    def test_already_there(self):
        page = Page()
        page.open()
        landed(page)
        called = []
        page.when_settled(lambda: called.append(1))
        self.assertEqual(called, [1])


class GlideTests(TearDown):
    def test_arrives_exactly_and_says_so(self):
        thing, arrived = SimpleNamespace(height=0.0), []
        motion.glide(thing, 'height', 100.0, 0.05, curve=motion.STANDARD, then=lambda: arrived.append(1))
        frames(until=lambda: arrived)
        self.assertEqual(thing.height, 100.0)

    def test_stopped(self):
        thing = SimpleNamespace(height=0.0)
        moving = motion.glide(thing, 'height', 100.0, 5.0)
        frames(count=2)
        moving.cancel()
        now = thing.height
        frames(count=3)
        self.assertEqual(thing.height, now)


def settings():
    return SimpleNamespace(video_quality='best', video_container='mp4', frame_format='png')


class ChoosePageTests(TearDown):
    def error(self):
        return analyze_mod.Analysis(url='https://x.test/', kind=analyze_mod.KIND_ERROR, error='No')

    def test_an_answer_before_it_lands_waits_for_it(self):
        page = ChoosePage('https://x.test/', settings(), on_choose=lambda *a: None)
        page.open()
        page.show(self.error())
        self.assertIsNone(page.analysis)
        landed(page)
        self.assertIsNotNone(page.analysis)

    def test_what_it_says_comes_up_in_place(self):
        page = ChoosePage('https://x.test/', settings(), on_choose=lambda *a: None)
        page.open()
        landed(page)
        page.show(self.error())
        self.assertEqual(page.title.opacity, 0)
        frames(until=lambda: page.title.opacity == 1)

    def test_an_answer_for_a_page_that_has_left(self):
        page = ChoosePage('https://x.test/', settings(), on_choose=lambda *a: None)
        page.open()
        landed(page)
        page.dismiss()
        gone(page)
        page.show(self.error())
        self.assertIsNone(page.analysis)


class ReadingTests(TearDown):
    def test_reading_waits_for_the_page(self):
        import main as phone
        waiting, engine = [], SimpleNamespace(analyze_link=mock.Mock())
        app = SimpleNamespace(page=None, settings=None, _chosen=None, _insets=(0, 0), engine=engine,
                              _page_closed=lambda page: None)

        class Stub:
            def __init__(self, *args, **kwargs):
                pass

            def bind(self, **kwargs):
                pass

            def open(self):
                pass

            def when_settled(self, then):
                waiting.append(then)

        with mock.patch.object(phone, 'ChoosePage', Stub):
            phone.GrabbitApp.inspect(app, URL)
        engine.analyze_link.assert_not_called()
        waiting[0]()
        engine.analyze_link.assert_called_once()


class Store(dict):
    def __iter__(self):
        return iter(self.values())


class DetailsTests(TearDown):
    def sheet(self, task):
        engine = SimpleNamespace(store=SimpleNamespace(get=lambda _id: task), fetch_peers=mock.Mock(),
                                 fetch_log=mock.Mock())
        sheet = details.DetailsSheet(engine)
        sheet.task_id = 'id'
        return sheet, engine

    def task(self):
        return SimpleNamespace(progress=0.5, down_speed=1000, up_speed=0, is_torrent=True, uploaded=0, ratio=0.0,
                               seeds=1, connections=2, name='A file', source='src', status_text='Going',
                               total=10, done=5, eta=3, file_path='', save_dir='/x', added_at=0, completed_at=0,
                               error='', state='downloading')

    def test_general_keeps_its_lines_every_second(self):
        task = self.task()
        sheet, _ = self.sheet(task)
        sheet.show_tab('General')
        before = list(sheet.rows.children)
        task.status_text = 'Still going'
        sheet.refresh()
        self.assertEqual(list(sheet.rows.children), before)
        self.assertIn('Still going', [label.text for label in sheet.rows._pair_values])

    def test_an_error_adds_its_line(self):
        task = self.task()
        sheet, _ = self.sheet(task)
        sheet.show_tab('General')
        task.error = 'It broke'
        sheet.refresh()
        self.assertEqual(sheet.rows._pair_keys[-1], 'Error:')
        self.assertEqual(len(sheet.rows.children), len(sheet.rows._pair_keys))

    def test_a_tab_that_asks_is_not_blanked_while_it_waits(self):
        task = self.task()
        sheet, engine = self.sheet(task)
        sheet.show_tab('Peers')
        answer = engine.fetch_peers.call_args[0][1]
        answer([{'ip': '1.2.3.4', 'port': 1, 'peerId': '', 'bitfield': 'ff'}])
        frames(until=lambda: sheet.rows.children)
        lines = list(sheet.rows.children)
        sheet.refresh()
        self.assertEqual(list(sheet.rows.children), lines, 'still showing, until the new answer')

    def test_the_same_answer_is_not_drawn_again(self):
        task = self.task()
        sheet, engine = self.sheet(task)
        sheet.show_tab('Peers')
        peers = [{'ip': '1.2.3.4', 'port': 1, 'peerId': '', 'bitfield': 'ff'}]
        engine.fetch_peers.call_args[0][1](peers)
        frames(until=lambda: sheet.rows.children)
        lines = list(sheet.rows.children)
        sheet.refresh()
        engine.fetch_peers.call_args[0][1](list(peers))
        frames(count=3)
        self.assertEqual(list(sheet.rows.children), lines)

    def test_changing_tab_starts_clean(self):
        task = self.task()
        sheet, _ = self.sheet(task)
        sheet.show_tab('General')
        task.is_torrent = False
        sheet.show_tab('Peers')
        self.assertEqual(len(sheet.rows.children), 1)
        sheet.refresh()
        self.assertEqual(len(sheet.rows.children), 1, 'its one line, not one more each second')


if __name__ == '__main__':
    result = unittest.main(testRunner=unittest.TextTestRunner(stream=sys.stdout, verbosity=1), exit=False)
    sys.stdout.flush()
    os._exit(0 if result.result.wasSuccessful() else 1)

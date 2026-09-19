"""Grabbit for Android.

The desktop window, folded into one column: the same filters, the same
download list, the same speed graph and the same details, arranged for a thumb
instead of a mouse.
"""

import os
import re
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'shared'))

from kivy.app import App                                    # noqa: E402
from kivy.clock import Clock                                # noqa: E402
from kivy.core.window import Window                         # noqa: E402
from kivy.metrics import dp                                 # noqa: E402
from kivy.uix.boxlayout import BoxLayout                    # noqa: E402
from kivy.uix.button import Button                          # noqa: E402
from kivy.uix.label import Label                            # noqa: E402
from kivy.uix.popup import Popup                            # noqa: E402
from kivy.uix.scrollview import ScrollView                  # noqa: E402
from kivy.uix.textinput import TextInput                    # noqa: E402

from grabbit.settings import Settings                       # noqa: E402
from grabbit.tasks import (KIND_IMAGE, KIND_MEDIA, RUNNING_STATES,  # noqa: E402
                           State)
from grabbit.util import human_speed                        # noqa: E402
from grabbit_mobile import paths                            # noqa: E402
from grabbit_mobile.engine import MobileEngine              # noqa: E402
from grabbit_mobile.ui import theme                         # noqa: E402
from grabbit_mobile.ui.details import DetailsSheet          # noqa: E402
from grabbit_mobile.ui.graph import SpeedGraph              # noqa: E402
from grabbit_mobile.ui.rows import TaskRow                  # noqa: E402
from grabbit_mobile.ui.widgets import Card, Chip, FlatButton  # noqa: E402

# Shared text is rarely just a link - "look at this <url> 😂" is the normal
# shape of it, so pick the link out rather than refusing the message.
LINK_IN_TEXT = re.compile(r'(?:https?://|magnet:\?)\S+')

# What to fetch. An empty string means whatever the settings say, which is the
# best video; the other two are yt-dlp quality names the shared code knows.
FORMATS = [('Video', ''), ('MP3', 'audio_mp3'), ('GIF', 'gif')]

# The desktop sidebar, as two groups of chips.
STATUS_FILTERS = [('all', 'All'), ('downloading', 'Downloading'), ('seeding', 'Seeding'),
                  ('completed', 'Completed'), ('paused', 'Paused'), ('error', 'Errored')]
KIND_FILTERS = [('all', 'Everything'), ('torrent', 'Torrents'), ('video', 'Videos'),
                ('image', 'Photos'), ('file', 'Files')]

DOWNLOADING_STATES = (State.DOWNLOADING, State.QUEUED, State.METADATA,
                      State.EXTRACTING, State.PROCESSING)


def matches(task, status: str, kind: str) -> bool:
    """The sidebar's filters, in the same order the desktop applies them."""
    if status == 'downloading' and task.state not in DOWNLOADING_STATES:
        return False
    if status == 'seeding' and task.state != State.SEEDING:
        return False
    if status == 'completed' and task.state != State.COMPLETED:
        return False
    if status == 'paused' and task.state != State.PAUSED:
        return False
    if status == 'error' and task.state != State.ERROR:
        return False
    if kind == 'torrent' and not task.is_torrent:
        return False
    if kind == 'video' and task.kind != KIND_MEDIA:
        return False
    if kind == 'image' and task.kind != KIND_IMAGE:
        return False
    if kind == 'file' and (task.is_torrent or task.kind in (KIND_MEDIA, KIND_IMAGE)):
        return False
    return True


class DragHandle(Button):
    """The splitter handle from the desktop, as something to drag on glass."""

    def __init__(self, on_drag=None, **kwargs):
        # Tall enough to find with a thumb.
        super().__init__(text='———', size_hint_y=None, height=dp(24), font_size=dp(12),
                         color=theme.DIM, background_normal='', background_down='',
                         background_color=theme.TRANSPARENT, **kwargs)
        self._on_drag = on_drag
        self._last = None

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            self._last = touch.y
            touch.grab(self)
            return True
        return super().on_touch_down(touch)

    def on_touch_move(self, touch):
        if touch.grab_current is self and self._last is not None:
            if self._on_drag:
                self._on_drag(self._last - touch.y)
            self._last = touch.y
            return True
        return super().on_touch_move(touch)

    def on_touch_up(self, touch):
        if touch.grab_current is self:
            touch.ungrab(self)
            self._last = None
            return True
        return super().on_touch_up(touch)


class GrabbitApp(App):
    title = 'Grabbit'

    def build(self):
        # Extraction is pure Python and CPU-bound, and it runs on a worker
        # thread while the interface draws on this one. With the default switch
        # interval the worker keeps the interpreter long enough for taps to
        # arrive seconds late; asking for finer slicing costs a little
        # throughput and keeps the interface answering.
        sys.setswitchinterval(0.002)
        theme.use_symbol_font()
        Window.clearcolor = theme.WINDOW
        self.settings = Settings.load()
        self.settings.download_dir = str(paths.downloads_dir())
        self.settings.max_media_jobs = 2
        self.engine = MobileEngine(self.settings,
                                   on_change=self._schedule_refresh,
                                   on_message=self._schedule_message)
        self._rows = {}
        self._pending = []          # links that arrived before aria2 was up
        self.selected_id = ''
        self.status_filter = 'all'
        self.kind_filter = 'all'
        self.format = ''

        root = BoxLayout(orientation='vertical', padding=dp(8), spacing=dp(6))
        root.add_widget(self._build_top_bar())
        root.add_widget(self._build_add_row())
        root.add_widget(self._build_formats())

        self.graph = SpeedGraph(store=self.engine.store, size_hint_y=None,
                                height=dp(200))
        self.graph_handle = DragHandle(on_drag=self._resize_graph)
        self.graph_pane = BoxLayout(orientation='vertical', spacing=dp(2))
        self.graph_pane.add_widget(self.graph)
        self.graph_pane.add_widget(self.graph_handle)
        # The pane lives in a slot that is emptied when the graph is off.
        # Leaving it in place at zero height does not work: its children keep
        # their own heights, so they stay laid out over whatever is above
        # them, invisible and still taking every touch meant for the chips.
        self.graph_slot = BoxLayout(size_hint_y=None, height=0)
        root.add_widget(self.graph_slot)

        root.add_widget(self._build_filters())

        # One line, ellipsised: a shared TikTok title is long enough to wrap
        # twice and push itself out of its own row.
        self.message = Label(text='Starting…', color=theme.DIM, font_size=dp(11),
                             size_hint_y=None, height=dp(18), halign='left',
                             valign='middle', shorten=True, shorten_from='right')
        self.message.bind(size=lambda widget, value: setattr(widget, 'text_size', value))
        root.add_widget(self.message)

        self.scroll = ScrollView()
        self.list = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(6))
        self.list.bind(minimum_height=self.list.setter('height'))
        self.scroll.add_widget(self.list)
        root.add_widget(self.scroll)

        self.footer = Label(text='', color=theme.DIM, font_size=dp(11),
                            size_hint_y=None, height=dp(20))
        root.add_widget(self.footer)

        self.details = DetailsSheet(self.engine)
        self.show_graph(bool(getattr(self.settings, 'show_graph', False)))

        Clock.schedule_once(lambda *_: self._start_engine(), 0.4)
        Clock.schedule_interval(lambda *_: self.refresh(), 1.0)
        return root

    # ----------------------------------------------------------------- parts
    def _build_top_bar(self):
        bar = BoxLayout(size_hint_y=None, height=dp(34), spacing=dp(8))
        name = Label(text='Grabbit', color=theme.TEXT, font_size=dp(18), bold=True,
                     halign='left', valign='middle')
        name.bind(size=lambda widget, value: setattr(widget, 'text_size', value))
        self.speed_label = Label(text='↓ 0 B/s   ↑ 0 B/s', color=theme.DIM, font_size=dp(11),
                                 halign='right', valign='middle', shorten=True,
                                 size_hint_x=None, width=dp(172))
        self.speed_label.bind(size=lambda widget, value: setattr(widget, 'text_size', value))
        self.graph_chip = Chip(text='Graph')
        self.graph_chip.bind(on_release=lambda *_: self.show_graph(not self.graph_shown))
        bar.add_widget(name)
        bar.add_widget(self.speed_label)
        bar.add_widget(self.graph_chip)
        return bar

    def _build_add_row(self):
        row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
        self.input = TextInput(hint_text='Paste a link', multiline=False,
                               background_color=theme.BASE, foreground_color=theme.TEXT,
                               hint_text_color=theme.DIM, cursor_color=theme.BLUE,
                               font_size=dp(14), padding=[dp(10), dp(12)])
        self.input.bind(on_text_validate=lambda *_: self.download())
        button = FlatButton(text='Download', size_hint_x=None, width=dp(112),
                            font_size=dp(14), color=(1, 1, 1, 1), fill=theme.BLUE)
        button.bind(on_release=lambda *_: self.download())
        row.add_widget(self.input)
        row.add_widget(button)
        return row

    def _build_formats(self):
        row = BoxLayout(size_hint_y=None, height=dp(32), spacing=dp(6))
        self._format_chips = {}
        for label, value in FORMATS:
            chip = Chip(text=label, selected=(value == self.format))
            chip.size_hint_x = 1
            chip.bind(on_release=lambda widget, v=value: self._choose_format(v))
            self._format_chips[value] = chip
            row.add_widget(chip)
        return row

    def _build_filters(self):
        strip = ScrollView(size_hint_y=None, height=dp(34), do_scroll_y=False, bar_width=0)
        row = BoxLayout(size_hint_x=None, spacing=dp(6), height=dp(30))
        row.bind(minimum_width=row.setter('width'))
        self._status_chips = {}
        self._kind_chips = {}

        for key, label in STATUS_FILTERS:
            chip = Chip(text=label, selected=(key == 'all'))
            chip.bind(on_release=lambda widget, k=key: self._choose_status(k))
            self._status_chips[key] = chip
            row.add_widget(chip)

        divider = Label(text='|', color=theme.BORDER, size_hint_x=None, width=dp(10))
        row.add_widget(divider)

        for key, label in KIND_FILTERS:
            chip = Chip(text=label, selected=(key == 'all'))
            chip.bind(on_release=lambda widget, k=key: self._choose_kind(k))
            self._kind_chips[key] = chip
            row.add_widget(chip)

        strip.add_widget(row)
        return strip

    # ------------------------------------------------------------- plumbing
    def _start_engine(self):
        self._request_permissions()
        self._watch_for_shared_links()
        threading.Thread(target=self._start_engine_worker, daemon=True).start()

    def _start_engine_worker(self):
        from grabbit_mobile.bootstrap import unpack_tools
        try:
            unpack_tools()
        except Exception as exc:
            self._schedule_message('error', f'Could not unpack the tools: {exc}')
            return
        if self.engine.start():
            self._schedule_message('info', f'Saving to {paths.downloads_dir()}')
            for url, wanted in self._pending:
                self.engine.add_link(url, wanted)
            self._pending.clear()
            Clock.schedule_once(lambda *_: self._maybe_ask_for_storage(), 0.5)
        self._schedule_refresh()

    @staticmethod
    def _request_permissions():
        try:
            from android.permissions import Permission, request_permissions
            request_permissions([Permission.WRITE_EXTERNAL_STORAGE,
                                 Permission.READ_EXTERNAL_STORAGE,
                                 Permission.POST_NOTIFICATIONS])
        except Exception:
            pass          # not on a phone, or the version does not need them

    # ------------------------------------------------ links from other apps
    def _watch_for_shared_links(self):
        """Grabbit sits in the share sheet and owns magnet links, so most links
        arrive from another app rather than through the text box."""
        self._handle_intent(self._current_intent())
        try:
            from android import activity as android_activity
            android_activity.bind(on_new_intent=self._on_new_intent)
        except Exception:
            pass          # off-device: nothing shares anything with us

    @staticmethod
    def _current_intent():
        try:
            from jnius import autoclass
            return autoclass('org.kivy.android.PythonActivity').mActivity.getIntent()
        except Exception:
            return None

    def _on_new_intent(self, intent):
        Clock.schedule_once(lambda *_: self._handle_intent(intent), 0)

    def _handle_intent(self, intent):
        link = self._link_from_intent(intent)
        if link:
            self._schedule_message('info', f'Shared with Grabbit: {link[:70]}')
            self._queue_link(link)

    @staticmethod
    def _link_from_intent(intent) -> str:
        if intent is None:
            return ''
        try:
            from jnius import autoclass
            Intent = autoclass('android.content.Intent')
            action = intent.getAction()
            if action == Intent.ACTION_SEND:
                text = intent.getStringExtra(Intent.EXTRA_TEXT) or ''
            elif action == Intent.ACTION_VIEW:
                text = intent.getDataString() or ''
            else:
                return ''
            # The activity keeps its intent, so without clearing it every return
            # to the app would add the same link again.
            intent.setAction(Intent.ACTION_MAIN)
            intent.removeExtra(Intent.EXTRA_TEXT)
            intent.setData(None)
        except Exception:
            return ''
        found = LINK_IN_TEXT.search(text)
        return found.group(0) if found else text.strip()

    # ------------------------------------------------------------- storage
    def _maybe_ask_for_storage(self):
        """Android 11 and later hide the real Downloads folder behind a
        permission only the user can grant, on a screen only they can reach."""
        from grabbit_mobile.bootstrap import has_all_files_access, open_all_files_settings
        marker = paths.data_dir() / '.asked-for-storage'
        if has_all_files_access() is not False or marker.exists():
            return
        marker.write_text('asked')

        body = BoxLayout(orientation='vertical', padding=dp(14), spacing=dp(12))
        body.add_widget(Label(
            text=('Android is keeping Grabbit out of your Downloads folder.\n\n'
                  'Without file access, downloads are saved inside the app\'s own '
                  'folder instead - still readable over USB, but not in the '
                  'Downloads app.'),
            color=theme.TEXT, halign='left', valign='top',
            text_size=(Window.width * 0.7, None)))
        buttons = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(10))
        popup = Popup(title='Where downloads go', content=body,
                      size_hint=(0.88, None), height=dp(320))
        later = FlatButton(text='Not now', color=theme.TEXT, fill=theme.HOVER)
        later.bind(on_release=popup.dismiss)
        grant = FlatButton(text='Open settings', color=(1, 1, 1, 1), fill=theme.BLUE)

        def go(*_):
            popup.dismiss()
            open_all_files_settings()
        grant.bind(on_release=go)
        buttons.add_widget(later)
        buttons.add_widget(grant)
        body.add_widget(buttons)
        popup.open()

    def _schedule_refresh(self):
        Clock.schedule_once(lambda *_: self.refresh(), 0)

    def _schedule_message(self, level, text):
        def show(*_):
            self.message.text = text
            self.message.color = theme.state_color(State.ERROR) if level == 'error' else theme.DIM
        Clock.schedule_once(show, 0)

    # -------------------------------------------------------------- actions
    def download(self):
        url = self.input.text.strip()
        if not url:
            return
        self.input.text = ''
        self._queue_link(url)

    def _choose_format(self, value: str):
        self.format = value
        for option, chip in self._format_chips.items():
            chip.set_selected(option == value)
        chosen = dict((v, k) for k, v in FORMATS).get(value, 'Video')
        self._schedule_message('info', f'Next download: {chosen}')

    def _choose_status(self, key: str):
        self.status_filter = key
        for option, chip in self._status_chips.items():
            chip.set_selected(option == key)
        self.refresh()

    def _choose_kind(self, key: str):
        self.kind_filter = key
        for option, chip in self._kind_chips.items():
            chip.set_selected(option == key)
        self.refresh()

    def show_graph(self, visible: bool):
        """The sidebar's Speed graph entry, as a chip."""
        self.graph_shown = bool(visible)
        self.graph_chip.set_selected(self.graph_shown)
        height = getattr(self, '_graph_height', 0) or int(Window.height * 0.42)
        self._graph_height = height
        self.graph_slot.clear_widgets()
        if self.graph_shown:
            self.graph_slot.add_widget(self.graph_pane)
            self.graph_slot.height = height + dp(26)
            self.graph.height = height
        else:
            self.graph_slot.height = 0
        self.settings.show_graph = self.graph_shown
        if self.graph_shown:
            self.graph.refresh()

    def _resize_graph(self, delta):
        """Drag the handle under the graph to give it more or less room."""
        if not self.graph_shown:
            return
        smallest, largest = dp(120), Window.height * 0.7
        self._graph_height = max(smallest, min(largest, self._graph_height + delta))
        self.graph.height = self._graph_height
        self.graph_slot.height = self._graph_height + dp(26)

    def select_task(self, task_id: str):
        self.selected_id = '' if task_id == self.selected_id else task_id
        self.graph.select(self.selected_id)
        self.refresh()

    def open_details(self, task_id: str):
        self.details.open_task(task_id)

    def toggle_task(self, task_id: str):
        """Start what is waiting, pause what is running."""
        task = self.engine.store.get(task_id)
        if task is None:
            return
        if task.state == State.PAUSED:
            self.engine.resume([task_id])
        elif task.state in (State.DOWNLOADING, State.SEEDING, State.QUEUED):
            self.engine.pause([task_id])
        self._schedule_refresh()

    def confirm_remove(self, task_id: str):
        """Stop a download, and ask before throwing away what it has."""
        task = self.engine.store.get(task_id)
        if task is None:
            return
        body = BoxLayout(orientation='vertical', padding=dp(14), spacing=dp(12))
        body.add_widget(Label(text=task.name or task.source, color=theme.TEXT, shorten=True,
                              shorten_from='right', text_size=(Window.width * 0.7, None),
                              halign='left', valign='top'))
        buttons = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(10))
        popup = Popup(title='Remove this download', content=body,
                      size_hint=(0.88, None), height=dp(240))

        def finish(delete_files):
            popup.dismiss()
            self.engine.remove([task_id], delete_files=delete_files)
            if self.selected_id == task_id:
                self.selected_id = ''
                self.graph.select('')
            self._schedule_refresh()

        for label, delete, colour in (('Keep the file', False, theme.HOVER),
                                      ('Delete it too', True, theme.state_color(State.ERROR))):
            button = FlatButton(text=label, fill=colour, color=theme.TEXT, font_size=dp(14))
            button.bind(on_release=lambda widget, d=delete: finish(d))
            buttons.add_widget(button)
        cancel = FlatButton(text='Cancel', fill=theme.HOVER, color=theme.DIM, font_size=dp(14))
        cancel.bind(on_release=popup.dismiss)
        buttons.add_widget(cancel)
        body.add_widget(buttons)
        popup.open()

    def _queue_link(self, url: str):
        """Links can arrive before aria2 is listening; none of them get lost."""
        if self.engine.running:
            self.engine.add_link(url, self.format)
        else:
            self._pending.append((url, self.format))

    # --------------------------------------------------------------- drawing
    def refresh(self):
        tasks = [t for t in self.engine.store
                 if matches(t, self.status_filter, self.kind_filter)]
        seen = set()
        for task in tasks:
            seen.add(task.id)
            row = self._rows.get(task.id)
            if row is None:
                row = TaskRow(on_select=self.select_task, on_open=self.open_details,
                              on_toggle=self.toggle_task, on_remove=self.confirm_remove)
                self._rows[task.id] = row
                self.list.add_widget(row)
            row.show(task, selected=(task.id == self.selected_id))
        for task_id in list(self._rows):
            if task_id not in seen:
                self.list.remove_widget(self._rows.pop(task_id))

        # The desktop puts a count beside each filter; so does this.
        for key, label in STATUS_FILTERS:
            count = sum(1 for t in self.engine.store if matches(t, key, self.kind_filter))
            self._status_chips[key].text = f'{label}  {count}' if count else label

        stats = self.engine.stats
        down = human_speed(stats.get('download_speed')) or '0 B/s'
        up = human_speed(stats.get('upload_speed')) or '0 B/s'
        self.speed_label.text = f'↓ {down}   ↑ {up}'
        total = len(list(self.engine.store))
        active = sum(1 for t in self.engine.store if t.state in RUNNING_STATES)
        shown = '' if len(tasks) == total else f'{len(tasks)} shown   ·   '
        process = getattr(self.engine, 'process', None)
        engine = (f'aria2 {process.version} · ready' if self.engine.running and process
                  else 'engine stopped')
        self.footer.text = f'{engine}   ·   {shown}{total} download(s), {active} active'

        self.graph.push(stats.get('download_speed') or 0, stats.get('upload_speed') or 0)
        self.graph.push_tasks(self.engine.store)
        if self.graph_shown:
            self.graph.refresh()

    def on_stop(self):
        self.settings.save()
        self.engine.shutdown()


if __name__ == '__main__':
    GrabbitApp().run()

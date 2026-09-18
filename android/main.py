"""Grabbit for Android.

A deliberately plain first interface: paste a link, watch it download. The
point of this build is to prove the engine works on a phone - yt-dlp, aria2
over RPC, and the storage rules - before any effort goes into looking nice.
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
from kivy.uix.progressbar import ProgressBar                # noqa: E402
from kivy.uix.scrollview import ScrollView                  # noqa: E402
from kivy.uix.textinput import TextInput                    # noqa: E402

from grabbit.settings import Settings                       # noqa: E402
from grabbit.tasks import State                             # noqa: E402
from grabbit.util import human_size, human_speed            # noqa: E402
from grabbit_mobile import paths                            # noqa: E402
from grabbit_mobile.engine import MobileEngine              # noqa: E402

BACKGROUND = (0.09, 0.09, 0.11, 1)
CARD = (0.15, 0.15, 0.18, 1)
TEXT = (0.92, 0.92, 0.94, 1)
MUTED = (0.62, 0.64, 0.68, 1)
ACCENT = (0.23, 0.53, 1.0, 1)

# Shared text is rarely just a link - "look at this <url> 😂" is the normal
# shape of it, so pick the link out rather than refusing the message.
LINK_IN_TEXT = re.compile(r'(?:https?://|magnet:\?)\S+')

# What to fetch. An empty string means whatever the settings say, which is the
# best video; the other two are yt-dlp quality names the shared code knows.
FORMATS = [('Video', ''), ('MP3', 'audio_mp3'), ('GIF', 'gif')]

STATE_COLOURS = {
    State.DOWNLOADING: ACCENT,
    State.EXTRACTING: ACCENT,
    State.METADATA: (0.54, 0.49, 1.0, 1),
    State.PROCESSING: (0.54, 0.49, 1.0, 1),
    State.SEEDING: (0.12, 0.67, 0.35, 1),
    State.COMPLETED: (0.12, 0.67, 0.35, 1),
    State.ERROR: (0.9, 0.28, 0.31, 1),
}


class TaskRow(BoxLayout):
    """One download: name, progress, and what it is doing."""

    def __init__(self, **kwargs):
        super().__init__(orientation='vertical', size_hint_y=None, height=dp(86),
                         padding=dp(10), spacing=dp(4), **kwargs)
        self.title = Label(text='', color=TEXT, halign='left', valign='middle',
                           shorten=True, shorten_from='right', size_hint_y=None, height=dp(24))
        self.title.bind(size=lambda widget, value: setattr(widget, 'text_size', value))
        self.bar = ProgressBar(max=1.0, value=0, size_hint_y=None, height=dp(8))
        self.status = Label(text='', color=MUTED, font_size=dp(12), halign='left',
                            valign='middle', size_hint_y=None, height=dp(20))
        self.status.bind(size=lambda widget, value: setattr(widget, 'text_size', value))
        self.add_widget(self.title)
        self.add_widget(self.bar)
        self.add_widget(self.status)

    def show(self, task):
        self.title.text = task.name or task.source
        self.bar.value = task.progress
        bits = [task.status_text]
        if task.total:
            bits.append(f'{human_size(task.done)} / {human_size(task.total)}')
        if task.down_speed:
            bits.append(human_speed(task.down_speed))
        self.status.text = '   ·   '.join(b for b in bits if b)
        self.status.color = STATE_COLOURS.get(task.state, MUTED)


class GrabbitApp(App):
    title = 'Grabbit'

    def build(self):
        Window.clearcolor = BACKGROUND
        self.settings = Settings.load()
        self.settings.download_dir = str(paths.downloads_dir())
        self.settings.max_media_jobs = 2
        self.engine = MobileEngine(self.settings,
                                   on_change=self._schedule_refresh,
                                   on_message=self._schedule_message)
        self._rows = {}
        self._pending = []        # links that arrived before aria2 was up

        root = BoxLayout(orientation='vertical', padding=dp(10), spacing=dp(8))

        entry = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        self.input = TextInput(hint_text='Paste a link', multiline=False,
                               background_color=CARD, foreground_color=TEXT,
                               cursor_color=ACCENT, padding=[dp(10), dp(12)])
        self.input.bind(on_text_validate=lambda *_: self.download())
        button = Button(text='Download', size_hint_x=None, width=dp(120),
                        background_normal='', background_color=ACCENT, color=(1, 1, 1, 1))
        button.bind(on_release=lambda *_: self.download())
        entry.add_widget(self.input)
        entry.add_widget(button)
        root.add_widget(entry)

        self.format = ''
        self._format_buttons = {}
        chooser = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(8))
        for label, value in FORMATS:
            choice = Button(text=label, background_normal='', color=TEXT, font_size=dp(14))
            choice.bind(on_release=lambda widget, v=value: self._choose_format(v))
            self._format_buttons[value] = choice
            chooser.add_widget(choice)
        root.add_widget(chooser)
        self._choose_format('')

        self.message = Label(text='Ready', color=MUTED, font_size=dp(12),
                             size_hint_y=None, height=dp(20), halign='left', valign='middle')
        self.message.bind(size=lambda widget, value: setattr(widget, 'text_size', value))
        root.add_widget(self.message)

        self.scroll = ScrollView()
        self.list = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(6))
        self.list.bind(minimum_height=self.list.setter('height'))
        self.scroll.add_widget(self.list)
        root.add_widget(self.scroll)

        self.footer = Label(text='', color=MUTED, font_size=dp(12),
                            size_hint_y=None, height=dp(22))
        root.add_widget(self.footer)

        Clock.schedule_once(lambda *_: self._start_engine(), 0.4)
        Clock.schedule_interval(lambda *_: self.refresh(), 1.0)
        return root

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
            color=TEXT, halign='left', valign='top',
            text_size=(Window.width * 0.7, None)))
        buttons = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(10))
        popup = Popup(title='Where downloads go', content=body,
                      size_hint=(0.88, None), height=dp(320))
        later = Button(text='Not now', background_normal='', background_color=CARD, color=TEXT)
        later.bind(on_release=popup.dismiss)
        grant = Button(text='Open settings', background_normal='',
                       background_color=ACCENT, color=(1, 1, 1, 1))

        def go(*_):
            popup.dismiss()
            open_all_files_settings()
        grant.bind(on_release=go)
        buttons.add_widget(later)
        buttons.add_widget(grant)
        body.add_widget(buttons)
        popup.open()

    def _choose_format(self, value: str):
        self.format = value
        for option, choice in self._format_buttons.items():
            choice.background_color = ACCENT if option == value else CARD

    def _queue_link(self, url: str):
        """Links can arrive before aria2 is listening; none of them get lost."""
        if self.engine.running:
            self.engine.add_link(url, self.format)
        else:
            self._pending.append((url, self.format))

    def _schedule_refresh(self):
        Clock.schedule_once(lambda *_: self.refresh(), 0)

    def _schedule_message(self, level, text):
        def show(*_):
            self.message.text = text
            self.message.color = STATE_COLOURS.get(State.ERROR, MUTED) if level == 'error' else MUTED
        Clock.schedule_once(show, 0)

    # ---------------------------------------------------------------- actions
    def download(self):
        url = self.input.text.strip()
        if not url:
            return
        self.input.text = ''
        self._queue_link(url)

    def refresh(self):
        tasks = list(self.engine.store)
        seen = set()
        for task in tasks:
            seen.add(task.id)
            row = self._rows.get(task.id)
            if row is None:
                row = TaskRow()
                self._rows[task.id] = row
                self.list.add_widget(row)
            row.show(task)
        for task_id in list(self._rows):
            if task_id not in seen:
                self.list.remove_widget(self._rows.pop(task_id))

        stats = self.engine.stats
        active = sum(1 for t in tasks if t.state == State.DOWNLOADING)
        self.footer.text = (f'{len(tasks)} download(s), {active} active   ·   '
                            f'↓ {human_speed(stats.get("download_speed")) or "0 B/s"}')

    def on_stop(self):
        self.engine.shutdown()


if __name__ == '__main__':
    GrabbitApp().run()

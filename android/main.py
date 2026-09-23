"""Grabbit for Android.

The desktop window, folded into one column: the same filters, the same
download list, the same speed graph and the same details, arranged for a thumb
instead of a mouse.
"""

import os
import re
import sys
import threading
import time

# When this file began running, for the start-up report (see _first_frame).
LAUNCHED = time.monotonic()

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'shared'))

if 'ANDROID_ARGUMENT' in os.environ:
    # Kivy's clock asks ctypes where the C library is as it loads, and on a
    # phone python-for-android answers that by asking Java for the app's
    # library folder - four Java classes read through pyjnius, a quarter of a
    # second before the window can open - to find a library every Android
    # process has already loaded. Name it straight away instead.
    import ctypes.util
    _find_library = ctypes.util.find_library
    ctypes.util.find_library = lambda name: 'libc.so' if name == 'c' else _find_library(name)

from kivy.app import App                                    # noqa: E402
from kivy.base import EventLoop                             # noqa: E402
from kivy.clock import Clock                                # noqa: E402
from kivy.core.window import Window                         # noqa: E402
from kivy.logger import Logger                              # noqa: E402
from kivy.metrics import dp                                 # noqa: E402
from kivy.uix.boxlayout import BoxLayout                    # noqa: E402
from kivy.uix.button import Button                          # noqa: E402
from kivy.uix.label import Label                            # noqa: E402
from kivy.uix.scrollview import ScrollView                  # noqa: E402
from kivy.utils import platform                             # noqa: E402

from grabbit import APP_VERSION, updates                    # noqa: E402
from grabbit.settings import Settings                       # noqa: E402
from grabbit.tasks import (KIND_IMAGE, KIND_MEDIA, RUNNING_STATES,  # noqa: E402
                           State)
from grabbit.util import human_speed                        # noqa: E402
from grabbit_mobile import paths                            # noqa: E402
from grabbit_mobile.files import finished, finished_files   # noqa: E402
from grabbit_mobile.remote import RemoteEngine              # noqa: E402
from grabbit_mobile.ui import theme                         # noqa: E402
from grabbit_mobile.ui.choose import ChoosePage             # noqa: E402
from grabbit_mobile.ui.details import DetailsSheet          # noqa: E402
from grabbit_mobile.ui.graph import SpeedGraph              # noqa: E402
from grabbit_mobile.ui.linkbox import LinkBox               # noqa: E402
from grabbit_mobile.ui.rows import TaskRow                  # noqa: E402
from grabbit_mobile.ui.widgets import Card, Chip, Dialog, FlatButton, TapLabel  # noqa: E402

IMPORTED = time.monotonic()

# Shared text is rarely just a link - "look at this <url> 😂" is the normal
# shape of it, so pick the link out rather than refusing the message.
LINK_IN_TEXT = re.compile(r'(?:https?://|magnet:\?)\S+')

# The desktop sidebar, as two groups of chips.
STATUS_FILTERS = [('all', 'All'), ('downloading', 'Downloading'), ('seeding', 'Seeding'),
                  ('completed', 'Completed'), ('paused', 'Paused'), ('error', 'Errored')]
KIND_FILTERS = [('all', 'Everything'), ('torrent', 'Torrents'), ('video', 'Videos'),
                ('image', 'Photos'), ('file', 'Files')]

DOWNLOADING_STATES = (State.DOWNLOADING, State.QUEUED, State.METADATA,
                      State.EXTRACTING, State.PROCESSING)

# Coming back to the app asks for updates again once this many seconds have
# passed since the last check (on_resume).
UPDATE_RECHECK = 60


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


def process_age() -> float | None:
    """Seconds since this process began - on a phone, the moment Android
    started the app, a few milliseconds after the icon was touched - or None
    where that cannot be read.

    The kernel keeps it, in clock ticks since boot, in /proc: asking Android
    instead would have pyjnius read a Java class, which is itself a
    noticeable part of starting up.
    """
    try:
        with open('/proc/self/stat') as stat:
            fields = stat.read().rsplit(')', 1)[1].split()
        started = int(fields[19]) / os.sysconf('SC_CLK_TCK')
        return time.clock_gettime(time.CLOCK_BOOTTIME) - started
    except (OSError, ValueError, IndexError, AttributeError):
        return None


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
        # The downloads run in a process of their own, which outlives this
        # window - closing it, swiping Grabbit away, leaves them running
        # (grabbit_mobile/host.py). This asks it for everything (remote.py).
        self.engine = RemoteEngine(self.settings,
                                   on_change=self._schedule_refresh,
                                   on_message=self._schedule_message,
                                   schedule=lambda fn: Clock.schedule_once(lambda *_: fn(), 0))
        self._rows = {}
        self._insets = (0, 0)
        self.page = None            # the one asking what to make of a link
        self.selected_id = ''
        self.status_filter = 'all'
        self.kind_filter = 'all'
        # Holding a download picks it out, and a tap then picks or drops
        # others: the downloads picked, while that goes on (start_choosing).
        self.choosing = False
        self.chosen = set()
        self.hands = None           # FileShare and Touch (java/), on a phone

        root = BoxLayout(orientation='vertical', padding=dp(8), spacing=dp(6))
        self.root_box = root
        # While downloads are being picked out, the title bar says how many
        # and the footer becomes what to do with them.
        self.top_bar = self._build_top_bar()
        self.choose_bar = self._build_choose_bar()
        self.top_slot = BoxLayout(size_hint_y=None, height=self.top_bar.height)
        self.top_slot.add_widget(self.top_bar)
        root.add_widget(self.top_slot)
        # A newer Grabbit, when a check finds one. Like the graph below, it
        # lives in a slot that is emptied rather than shrunk to nothing: a
        # zero-height layout still lays out its children, and they go on
        # taking touches meant for whatever is drawn where they are.
        self._update_answer = None
        self._update_declined = set()       # "Later", until Grabbit next starts
        self._update_checking = False
        self._last_update_check = 0.0
        self.update_banner = self._build_update_banner()
        self.update_slot = BoxLayout(size_hint_y=None, height=0)
        root.add_widget(self.update_slot)
        root.add_widget(self._build_add_row())

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

        # It carries this copy's version, and touching it asks whether there
        # is a newer one - the phone's Help › Check for updates.
        self.footer = TapLabel(text='', color=theme.DIM, font_size=dp(11),
                               size_hint_y=None, height=dp(20), shorten=True,
                               halign='center', valign='middle')
        self.footer.bind(size=lambda widget, value: setattr(widget, 'text_size', value))
        self.footer.bind(on_release=lambda *_: self.check_for_updates(manual=True))
        self.choose_actions = self._build_choose_actions()
        self.bottom_slot = BoxLayout(size_hint_y=None, height=self.footer.height)
        self.bottom_slot.add_widget(self.footer)
        root.add_widget(self.bottom_slot)

        self.details = None         # made the first time one is opened
        self.settings_page = None   # while Settings is open
        self.show_graph(bool(getattr(self.settings, 'show_graph', False)))

        # Clear of the cutout from the first frame - and asked again once the
        # window has settled, and whenever the space Android gives the app may
        # have changed: the keyboard coming or going moves the system bars,
        # and on some phones the app's surface with them.
        self._apply_insets()
        Clock.schedule_once(lambda *_: self._apply_insets(), 0.3)
        Clock.schedule_once(lambda *_: self._apply_insets(), 1.5)
        self._insets_later = Clock.create_trigger(lambda *_: self._apply_insets(), 0.35)
        Window.bind(on_resize=lambda *_: self._insets_later(),
                    keyboard_height=lambda *_: self._insets_later())
        # Back leaves picking downloads before it leaves the app.
        Window.bind(on_keyboard=self._on_key)
        Clock.schedule_interval(lambda *_: self.refresh(), 1.0)
        # The first check comes as soon as the app is on screen (_on_screen);
        # after that twice a day while open, and each time it is opened again
        # (on_resume).
        Clock.schedule_interval(lambda *_: self.check_for_updates(), updates.CHECK_INTERVAL)
        # Kivy takes the splash screen down as its first frame begins, before
        # that frame is on screen, which leaves a moment of black between the
        # two. The app takes it down itself, once the frame is there.
        EventLoop.remove_android_splash = lambda *_: None
        self._built = time.monotonic()
        Window.bind(on_flip=self._first_frame)
        return root

    def _first_frame(self, *_):
        """The first frame is drawn; it is on screen by the next."""
        Window.unbind(on_flip=self._first_frame)
        self._drawn = time.monotonic()
        Clock.schedule_once(lambda *_: self._on_screen(), 0)

    def _on_screen(self):
        """Take the splash screen down, then do what could wait for it.

        Android takes the splash down on its UI thread, one thing after
        another, so it goes first: work queued there ahead of it would keep
        it up. The engine starts before the link box is made, so that a page
        opened for a shared link is already there to make it wait (LinkBox).
        """
        if platform == 'android':
            try:
                from android import remove_presplash
                remove_presplash()
            except Exception:
                # python-for-android takes it down itself five seconds in.
                Logger.exception('Start-up: could not take the splash screen down')
        self._report_start()
        self._start_engine()
        self.check_for_updates()            # every launch asks, straight away
        self.input.ready()
        # Whatever part of the window the app does not draw - behind a
        # keyboard as it slides in, around a surface Android has moved - is
        # the app's own colour rather than black.
        from grabbit_mobile.bootstrap import paint_window
        paint_window(theme.PALETTE['window'])

    def _report_start(self):
        """Say in the log how long opening took.

        build/android/startup_time.ps1 reads this line back from the phone.
        """
        age = process_age()
        marks = [('imported', IMPORTED), ('built', self._built), ('first frame', self._drawn)]
        if age is None:
            zero, since = LAUNCHED, 'main.py began'
        else:
            zero, since = time.monotonic() - age, 'the process started'
            marks.insert(0, ('main.py', LAUNCHED))
        Logger.info('Start-up: ' + ', '.join(f'{name} {moment - zero:.2f}s' for name, moment in marks)
                    + f' (since {since})')

    # ----------------------------------------------------------------- parts
    def _build_top_bar(self):
        # The title starts where the text in the link box does, clear of the
        # screen's rounded corner rather than hard against it.
        bar = BoxLayout(size_hint_y=None, height=dp(34), spacing=dp(8),
                        padding=[dp(10), 0, dp(2), 0])
        # The name is always as wide as it is - on a narrower phone the speeds
        # beside it give way, rather than the name wrapping onto two lines.
        name = Label(text='Grabbit', color=theme.TEXT, font_size=dp(18), bold=True,
                     size_hint_x=None)
        name.bind(texture_size=lambda widget, value: setattr(widget, 'width', value[0]))
        name.texture_update()
        self.speed_label = Label(text='↓ 0 B/s   ↑ 0 B/s', color=theme.DIM, font_size=dp(11),
                                 halign='right', valign='middle', shorten=True,
                                 shorten_from='right')
        self.speed_label.bind(size=lambda widget, value: setattr(widget, 'text_size', value))
        self.graph_chip = Chip(text='Graph')
        self.graph_chip.bind(on_release=lambda *_: self.show_graph(not self.graph_shown))
        self.settings_button = FlatButton(text='⚙', font_size=dp(20), size_hint_x=None,
                                          width=dp(38), color=theme.DIM, fill=theme.TRANSPARENT)
        self.settings_button.bind(on_release=lambda *_: self.open_settings())
        bar.add_widget(name)
        bar.add_widget(self.speed_label)
        bar.add_widget(self.graph_chip)
        bar.add_widget(self.settings_button)
        return bar

    def _build_update_banner(self):
        """The desktop's update banner, in one row: what is out on one line and
        what this copy is under it, so neither is cut short on a narrow phone."""
        banner = Card(size_hint_y=None, height=dp(44), spacing=dp(6),
                      padding=[dp(12), dp(5), dp(5), dp(5)])
        words = BoxLayout(orientation='vertical')
        self.update_label = Label(text='', color=theme.TEXT, font_size=dp(13), halign='left',
                                  valign='bottom', shorten=True, shorten_from='right')
        self.update_note = Label(text='', color=theme.DIM, font_size=dp(11), halign='left',
                                 valign='top', shorten=True, shorten_from='right')
        for label in (self.update_label, self.update_note):
            label.bind(size=lambda widget, value: setattr(widget, 'text_size', value))
            words.add_widget(label)
        get = FlatButton(text='Get it', size_hint_x=None, width=dp(68), font_size=dp(13),
                         color=(1, 1, 1, 1), fill=theme.BLUE)
        get.bind(on_release=lambda *_: self._get_update())
        self.update_later = FlatButton(text='Later', size_hint_x=None, width=dp(60),
                                       font_size=dp(13), color=theme.DIM)
        self.update_later.bind(on_release=lambda *_: self._decline_update())
        for widget in (words, get, self.update_later):
            banner.add_widget(widget)
        return banner

    def _build_choose_bar(self):
        """The title bar while downloads are being picked out: leave, how
        many, and all of them."""
        bar = BoxLayout(size_hint_y=None, height=dp(34), spacing=dp(6), padding=[0, 0, dp(2), 0])
        leave = FlatButton(text='×', font_size=dp(22), size_hint_x=None, width=dp(38),
                           color=theme.TEXT, fill=theme.TRANSPARENT)
        leave.bind(on_release=lambda *_: self.stop_choosing())
        self.chosen_label = Label(text='', color=theme.TEXT, font_size=dp(16), bold=True,
                                  halign='left', valign='middle', shorten=True,
                                  shorten_from='right')
        self.chosen_label.bind(size=lambda widget, value: setattr(widget, 'text_size', value))
        self.choose_all_button = FlatButton(text='Select all', size_hint_x=None, width=dp(108),
                                            font_size=dp(13), color=theme.TEXT)
        self.choose_all_button.bind(on_release=lambda *_: self.choose_all())
        for widget in (leave, self.chosen_label, self.choose_all_button):
            bar.add_widget(widget)
        return bar

    def _build_choose_actions(self):
        """The footer while downloads are being picked out: what to do with them."""
        row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
        self.share_chosen_button = FlatButton(text='Share', font_size=dp(15), color=theme.TEXT)
        self.share_chosen_button.bind(on_release=lambda *_: self.share_chosen())
        self.remove_chosen_button = FlatButton(text='Remove', font_size=dp(15),
                                               color=theme.state_color(State.ERROR))
        self.remove_chosen_button.bind(on_release=lambda *_: self.remove_chosen())
        row.add_widget(self.share_chosen_button)
        row.add_widget(self.remove_chosen_button)
        return row

    def _build_add_row(self):
        row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
        # Android's own text field on a phone, so holding it gives Android's
        # own copy and paste menu (ui/linkbox.py).
        self.input = LinkBox(hint='Paste a link')
        self.input.bind(on_submit=lambda *_: self.download())
        button = FlatButton(text='Download', size_hint_x=None, width=dp(112),
                            font_size=dp(14), color=(1, 1, 1, 1), fill=theme.BLUE)
        self.download_button = button
        button.bind(on_release=lambda *_: self.download())
        row.add_widget(self.input)
        row.add_widget(button)
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

    def _apply_insets(self):
        """Keep the interface clear of the camera cutout and the gesture bar.

        The app draws edge to edge, so without this the title sits under the
        punch-hole and the footer under the bar at the bottom.
        """
        from grabbit_mobile.bootstrap import safe_insets
        top, bottom = safe_insets(Window.height)
        self._insets = (top, bottom)
        self.root_box.padding = [dp(8), dp(8) + top, dp(8), dp(8) + bottom]

    # ------------------------------------------------------------- plumbing
    def _start_engine(self):
        from grabbit_mobile.bootstrap import prepare_environment, window_controls
        prepare_environment()       # temp and cache folders, before any link is read
        self._request_permissions()
        reading = self._watch_for_shared_links()
        # Start the downloader - or find it still running, downloads and all,
        # from before this window opened.
        self.engine.controls, self.engine.context = window_controls()
        from grabbit_mobile.bootstrap import hands
        self.hands = hands()
        if self.engine.controls is None:
            self._run_downloader_here()
        self.engine.start()
        threading.Thread(target=self._prepare_tools, daemon=True).start()
        if not reading:
            # A link shared to open the app loads yt-dlp as it is read, and
            # is better off with the phone to itself than sharing it with this.
            threading.Thread(target=self._load_ytdlp, name='grabbit-load-ytdlp', daemon=True).start()

    @staticmethod
    def _load_ytdlp():
        """Load yt-dlp now that the window is up, so reading a link does not wait for it.

        It is the largest thing the app loads, and nothing needs it until a
        link is read or a video resumes, so the app opens without it and it
        comes in here, behind the first frame. Its sites are matched once too:
        that compiles every one of their address patterns, which the first
        link would otherwise sit through.
        """
        started = time.monotonic()
        try:
            from grabbit import analyze as analyze_mod
            from grabbit import media                   # noqa: F401 - loading is the point
            analyze_mod.matching_extractor('https://grabbit.invalid/')
        except Exception:
            return        # the first link loads it instead, and reports what is wrong
        Logger.info(f'Warm-up: yt-dlp loaded in {time.monotonic() - started:.2f}s')

    def _run_downloader_here(self):
        """Off a phone there is no service to start, so the downloader runs on
        a thread of this process instead - the same code, reached the same
        way, which is enough to try the window on a desktop."""
        from grabbit_mobile.host import Host

        def run():
            host = Host(Settings.load())
            host.start()
            host.run()

        threading.Thread(target=run, name='grabbit-downloader', daemon=True).start()

    def _prepare_tools(self):
        """FFmpeg and QuickJS, which reading a link here uses - the frames of a
        video, YouTube's challenges. The downloader has its own copies."""
        from grabbit_mobile.bootstrap import unpack_tools
        try:
            unpack_tools()
        except Exception as exc:
            self._schedule_message('error', f'Could not unpack the tools: {exc}')
            return
        folder = paths.chosen_downloads_dir(getattr(self.settings, 'android_download_dir', ''))
        self._schedule_message('info', f'Saving to {folder}')
        Clock.schedule_once(lambda *_: self._maybe_ask_for_storage(), 0.5)

    @staticmethod
    def _request_permissions():
        """Ask for storage where the ordinary permission still reaches Downloads.

        That is Android 10 and older; later ones hide the folder behind the
        all-files screen instead (_maybe_ask_for_storage). Asking is not free
        even when the answer is already known: Android opens an invisible
        screen of its own over the app, which pauses and resumes it while it
        is still starting - so ask only where it matters, and only while the
        permission is missing.
        """
        try:
            from android.permissions import Permission, check_permission, request_permissions
            from jnius import autoclass
            if autoclass('android.os.Build$VERSION').SDK_INT >= 30:
                return
            missing = [permission for permission in (Permission.WRITE_EXTERNAL_STORAGE,
                                                     Permission.READ_EXTERNAL_STORAGE)
                       if not check_permission(permission)]
            if missing:
                request_permissions(missing)
        except Exception:
            pass          # not on a phone

    # ------------------------------------------------ links from other apps
    def _watch_for_shared_links(self) -> bool:
        """Grabbit sits in the share sheet and owns magnet links, so most links
        arrive from another app rather than through the text box.

        True when a link shared to open the app is being read.
        """
        reading = self._handle_intent(self._current_intent())
        try:
            from android import activity as android_activity
            android_activity.bind(on_new_intent=self._on_new_intent)
        except Exception:
            pass          # off-device: nothing shares anything with us
        return reading

    @staticmethod
    def _current_intent():
        try:
            from jnius import autoclass
            return autoclass('org.kivy.android.PythonActivity').mActivity.getIntent()
        except Exception:
            return None

    def _on_new_intent(self, intent):
        Clock.schedule_once(lambda *_: self._handle_intent(intent), 0)

    def _handle_intent(self, intent) -> bool:
        """Act on what another app sent; True when it was a link to read."""
        # Android's installer answers an update through an intent too.
        from grabbit_mobile.bootstrap import install_status
        installing = install_status(intent)
        if installing is not None:
            if installing[0] == 'failed':
                self._update_failed(installing[1])
            return False
        link = self._link_from_intent(intent)
        if link:
            self.inspect(link)
        return bool(link)

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
        from grabbit_mobile.bootstrap import has_all_files_access
        marker = paths.data_dir() / '.asked-for-storage'
        if has_all_files_access() is not False or marker.exists():
            return
        marker.write_text('asked')
        self.ask_for_storage()

    def ask_for_storage(self):
        from grabbit_mobile.bootstrap import open_all_files_settings
        Dialog('Where downloads go',
               'Android is keeping Grabbit out of your Downloads folder.\n\n'
               'Without file access, downloads are saved inside the app\'s own folder '
               'instead - still readable over USB, but not in the Downloads app.',
               [('Not now', 'plain', None),
                ('Open settings', 'primary', open_all_files_settings)]).open()

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
        found = LINK_IN_TEXT.search(url)
        self.input.unfocus()
        self.inspect(found.group(0) if found else url)

    def inspect(self, url: str):
        """Read a link, and ask what to make of it before fetching anything.

        The page opens at once, saying it is reading the link, and fills in
        when the answer comes: a video's qualities and types, a torrent's size,
        a file's name.
        """
        if self.page is not None:
            self.page.dismiss()
        page = ChoosePage(url, self.settings, on_choose=self._chosen, insets=self._insets)
        page.bind(on_dismiss=lambda *_: self._page_closed(page))
        self.page = page
        page.open()
        self.engine.analyze_link(url, lambda result: Clock.schedule_once(
            lambda *_: page.show(result)))

    def _page_closed(self, page):
        if self.page is page:
            self.page = None

    def _chosen(self, analysis, choice: dict):
        """The page's Download: queue it, now or once the engine is up."""
        from grabbit import analyze as analyze_mod
        if analysis.kind == analyze_mod.KIND_ERROR:
            choice['as_file'] = True          # the page offers that only for web links
        if self.input.text.strip() and analysis.url in self.input.text:
            self.input.set_text('')
        self.settings.save()
        # Into the foreground straight away, while the app is certainly on
        # screen: Android allows a foreground service to start only then.
        self.engine.expect(analysis.title or analysis.url)
        self._ask_for_notifications()
        # Sent as soon as the downloader answers, if it has not yet.
        self.engine.add_analysis(analysis, choice)
        if not self.engine.connected:
            self._schedule_message('info', 'It starts as soon as the engine is ready')

    def _ask_for_notifications(self):
        """Ask, once, to show a download's progress - at the first download,
        when the question makes sense. Android 13 and later need leave for
        the notification; the downloads carry on without it all the same."""
        marker = paths.data_dir() / '.asked-for-notifications'
        if marker.exists():
            return
        try:
            from android.permissions import Permission, check_permission, request_permissions
            from jnius import autoclass
            if autoclass('android.os.Build$VERSION').SDK_INT < 33:
                return
            marker.write_text('asked')
            if not check_permission(Permission.POST_NOTIFICATIONS):
                request_permissions([Permission.POST_NOTIFICATIONS])
        except Exception:
            pass          # not on a phone

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

    # -------------------------------------------------------------- updates
    def show_update(self, answer):
        """Show the banner for a check that found a newer Grabbit, or clear it."""
        self._update_answer = answer if answer is not None and answer.available else None
        self.update_slot.clear_widgets()
        if self._update_answer is None:
            self.update_slot.height = 0
            return
        self.update_label.text = f'Grabbit {answer.latest} is out'
        self.update_note.text = f'You have {APP_VERSION}'
        self.update_slot.add_widget(self.update_banner)
        self.update_slot.height = self.update_banner.height

    def check_for_updates(self, manual: bool = False, reply=None):
        """Ask GitHub whether a newer Grabbit is out, off the interface thread.

        Unprompted checks respect the setting and say nothing unless there is
        something to offer; one asked for - from the footer, or Settings -
        always answers, and reply(answer) hears it too.
        """
        if not manual and not getattr(self.settings, 'check_for_updates', True):
            return
        if self._update_checking:
            if reply is not None:
                self._update_replies.append(reply)
            return
        self._update_checking = True
        self._update_replies = [reply] if reply is not None else []
        self._last_update_check = time.monotonic()
        if manual:
            self._schedule_message('info', 'Checking for updates…')

        def ask():
            answer = updates.check('android')
            Clock.schedule_once(lambda *_: self._on_update_checked(answer, manual), 0)

        threading.Thread(target=ask, name='grabbit-update-check', daemon=True).start()

    def _decline_update(self):
        """Later: not this version again until Grabbit next starts - as on
        Windows. Asking from the footer still shows it."""
        if self._update_answer is not None:
            self._update_declined.add(self._update_answer.latest)
        self.show_update(None)

    def _on_update_checked(self, answer, manual: bool):
        self._update_checking = False
        replies, self._update_replies = getattr(self, '_update_replies', []), []
        for reply in replies:
            reply(answer)
        manual = manual or bool(replies)
        if answer.available:
            if manual or answer.latest not in self._update_declined:
                self.show_update(answer)
            if manual:
                self._schedule_message('info', f'Grabbit {answer.latest} is out.')
        elif manual:
            self._schedule_message('error' if answer.error else 'info',
                                   answer.error or f'This is the newest version: Grabbit {APP_VERSION}.')

    def take_update(self, answer):
        """Settings' Get it: the same as the banner's."""
        self._update_answer = answer
        self._get_update()

    def _get_update(self):
        """Download the APK here, held to the signed manifest's size and hash,
        and hand it to Android's installer - which asks to confirm, as it must.
        If that cannot be done, the browser fetches it instead."""
        answer = self._update_answer
        if answer is None or answer.asset is None or getattr(self, '_updating', False):
            return
        self._updating, self._installing_answer = True, answer
        self.show_update(None)
        shown = [-1]

        def progress(done, total):
            megabytes = done // 1_000_000
            if megabytes != shown[0]:
                shown[0] = megabytes
                self._schedule_message('info', f'Downloading Grabbit {answer.latest}… '
                                       f'{megabytes} of {total // 1_000_000} MB')

        def work():
            try:
                apk = updates.download(answer.asset, paths.data_dir() / 'updates', progress)
            except updates.UpdateError as error:
                message = str(error)
                Clock.schedule_once(lambda *_: self._update_failed(message), 0)
                return
            Clock.schedule_once(lambda *_: self._hand_to_installer(apk, answer), 0)

        threading.Thread(target=work, name='grabbit-update-download', daemon=True).start()

    def _hand_to_installer(self, apk, answer):
        from grabbit_mobile.bootstrap import install_apk, open_url
        try:
            install_apk(str(apk))
            self._schedule_message('info', 'Android will ask you to confirm the update.')
        except Exception:
            # Not on a phone, or the installer turned the session down: the
            # browser fetches the same APK, and Android installs it the same way.
            if open_url(answer.asset.url):
                self._schedule_message('info', f'Downloading {answer.asset.name} in the browser - '
                                       'open it when it finishes to install')
        self._updating = False

    def _update_failed(self, message):
        self._updating = False
        self._schedule_message('error', message)
        self.show_update(getattr(self, '_installing_answer', None))

    def on_pause(self):
        # Off screen, the window stops asking the downloader how things are,
        # which lets it end itself once there is nothing left to do.
        self.engine.set_active(False)
        return True

    def on_resume(self):
        self.engine.set_active(True)
        # Opening Grabbit again counts as launching it, since a phone keeps an
        # app in memory for days: ask again - unless it asked a moment ago, as
        # Android also resumes it on the way back from its own screens, the
        # installer's and the permission questions.
        if time.monotonic() - self._last_update_check > UPDATE_RECHECK:
            self.check_for_updates()
        if self.settings_page is not None:
            self.settings_page.on_resume()
        return True

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

    def open_settings(self):
        from grabbit_mobile.ui.settings import SettingsPage
        if self.settings_page is not None:
            return
        page = SettingsPage(self, insets=self._insets)
        page.bind(on_dismiss=lambda *_: setattr(self, 'settings_page', None))
        self.settings_page = page
        page.open()

    def battery_unrestricted(self):
        """Whether Android lets Grabbit run without battery limits; None off a phone."""
        controls = self.engine.controls
        if controls is None:
            return None
        try:
            return bool(controls.unrestricted(self.engine.context))
        except Exception:
            return None

    def ask_for_battery(self):
        """Android's own question; the answer is the person's."""
        controls = self.engine.controls
        if controls is not None:
            try:
                controls.askForUnrestricted(self.engine.context)
            except Exception:
                Logger.exception('Settings: could not ask about battery use')

    def open_details(self, task_id: str, tab: str = 'General'):
        if self.details is None:
            self.details = DetailsSheet(self.engine, on_open_file=self.open_file)
        self.details.open_task(task_id, tab)

    def toggle_task(self, task_id: str):
        """Start what is waiting, pause what is running, try again what
        failed, open what is finished."""
        task = self.engine.store.get(task_id)
        if task is None:
            return
        if task.state == State.COMPLETED:
            self.open_task(task_id)
        elif task.state in (State.PAUSED, State.ERROR):
            self.engine.expect(task.name or task.source)
            self.engine.resume([task_id])
        elif task.state in (State.DOWNLOADING, State.SEEDING, State.QUEUED):
            self.engine.pause([task_id])
        self._schedule_refresh()

    def confirm_remove(self, task_id: str):
        """Stop a download, and ask before throwing away what it has."""
        self._confirm_removing([task_id])

    def _confirm_removing(self, ids, then=None):
        """One question for any number of downloads; then() once they are gone."""
        tasks = [task for task in map(self.engine.store.get, ids) if task is not None]
        if not tasks:
            return

        def finish(delete_files):
            gone = [task.id for task in tasks]
            self.engine.remove(gone, delete_files=delete_files)
            if self.selected_id in gone:
                self.selected_id = ''
                self.graph.select('')
            if then is not None:
                then()
            self._schedule_refresh()

        names = [task.name or task.source for task in tasks]
        if len(tasks) == 1:
            title, keep, delete = 'Remove this download?', 'Keep the file', 'Delete it too'
            about = names[0]
        else:
            title, keep, delete = f'Remove {len(tasks)} downloads?', 'Keep the files', 'Delete them too'
            about = '\n'.join(names[:3]) + (f'\nand {len(names) - 3} more' if len(names) > 3 else '')
        Dialog(title, about, [
            ('Cancel', 'plain', None),
            (keep, 'plain', lambda: finish(False)),
            (delete, 'danger', lambda: finish(True))]).open()

    # ----------------------------------------------------- finished downloads
    def open_task(self, task_id: str):
        """Open what a finished download made: its file, in whatever the phone
        opens that kind with - or, for a torrent of several, the list of
        them, to open one from."""
        task = self.engine.store.get(task_id)
        if task is None:
            return
        found = finished_files(task)
        if len(found) == 1:
            self.open_file(found[0])
        elif found:
            self.open_details(task_id, 'Files')
        else:
            self._say_handed('gone', task.name or task.source)

    def open_file(self, path: str):
        if self.hands is not None:
            answer = self.hands.open(path)
        else:
            # Off a phone, the preview opens it the desktop's way.
            from grabbit.util import open_path
            answer = '' if open_path(path) else 'gone'
        self._say_handed(answer, os.path.basename(path))

    def share_tasks(self, ids):
        """Android's share sheet, with every file the finished ones of these made."""
        tasks = [task for task in map(self.engine.store.get, ids) if task is not None]
        found = [path for task in tasks if finished(task) for path in finished_files(task)]
        if not found:
            if tasks:
                self._say_handed('gone', tasks[0].name if len(tasks) == 1 else 'They')
            return
        answer = self.hands.share(found) if self.hands is not None else 'no-phone'
        self._say_handed(answer, os.path.basename(found[0]))

    def _say_handed(self, answer: str, name: str):
        """Say why Android could not take a file, if it could not."""
        if not answer:
            return
        kind = os.path.splitext(name)[1].lstrip('.').upper()
        self._schedule_message('error', {
            'gone': f'{name} has been moved or deleted',
            'no-app': (f'Nothing on this phone opens {kind} files' if kind
                       else 'Nothing on this phone opens this kind of file'),
            'no-phone': 'Sharing needs the phone',
        }.get(answer, 'Android would not take it'))

    # ------------------------------------------------ picking out downloads
    def start_choosing(self, task_id: str):
        """A long press: pick this download out, and the ones tapped next."""
        if self.engine.store.get(task_id) is None:
            return
        if self.choosing:
            self.toggle_chosen(task_id)
            return
        if self.hands is not None:
            self.hands.held()
        self.choosing = True
        self.chosen = {task_id}
        self.top_slot.clear_widgets()
        self.top_slot.add_widget(self.choose_bar)
        self.bottom_slot.clear_widgets()
        self.bottom_slot.add_widget(self.choose_actions)
        self.bottom_slot.height = self.choose_actions.height
        self.refresh()

    def toggle_chosen(self, task_id: str):
        self.chosen ^= {task_id}
        if not self.chosen:
            self.stop_choosing()
            return
        self.refresh()

    def choose_all(self):
        """Every download on show - or, when they all are already, none."""
        shown = set(self._rows)
        if shown and not shown <= self.chosen:
            self.chosen = shown
            self.refresh()
        else:
            self.stop_choosing()

    def stop_choosing(self):
        self.choosing = False
        self.chosen = set()
        self.top_slot.clear_widgets()
        self.top_slot.add_widget(self.top_bar)
        self.bottom_slot.clear_widgets()
        self.bottom_slot.add_widget(self.footer)
        self.bottom_slot.height = self.footer.height
        self.refresh()

    def share_chosen(self):
        ids = list(self.chosen)
        if not any(finished(task) for task in map(self.engine.store.get, ids) if task is not None):
            self._schedule_message('info', 'Only a finished download can be shared')
            return
        self.stop_choosing()
        self.share_tasks(ids)

    def remove_chosen(self):
        self._confirm_removing(list(self.chosen), then=self.stop_choosing)

    def _on_key(self, _window, key, *_):
        if key == 27 and self.choosing:
            self.stop_choosing()
            return True
        return False

    def _show_choosing(self):
        """The count, and what the buttons would do to what is picked."""
        tasks = [task for task in map(self.engine.store.get, self.chosen) if task is not None]
        self.chosen_label.text = f'{len(tasks)} selected'
        everything = bool(self._rows) and set(self._rows) <= self.chosen
        self.choose_all_button.text = 'Select none' if everything else 'Select all'
        self.share_chosen_button.color = (theme.TEXT if any(finished(t) for t in tasks)
                                          else theme.DIM)

    # --------------------------------------------------------------- drawing
    def refresh(self):
        tasks = [t for t in self.engine.store
                 if matches(t, self.status_filter, self.kind_filter)]
        seen = {task.id for task in tasks}
        if self.choosing:
            # Only what is on show stays picked: a button never acts on a
            # download a filter, or its removal, has taken out of sight.
            self.chosen &= seen
            if not self.chosen:
                self.stop_choosing()
                return
        for task in tasks:
            row = self._rows.get(task.id)
            if row is None:
                row = TaskRow(on_select=self.select_task, on_details=self.open_details,
                              on_toggle=self.toggle_task, on_remove=self.confirm_remove,
                              on_open=self.open_task, on_share=lambda i: self.share_tasks([i]),
                              on_hold=self.start_choosing, on_choose=self.toggle_chosen)
                self._rows[task.id] = row
                self.list.add_widget(row)
            row.show(task, selected=(task.id == self.selected_id),
                     choosing=(task.id in self.chosen) if self.choosing else None)
        for task_id in list(self._rows):
            if task_id not in seen:
                self.list.remove_widget(self._rows.pop(task_id))
        if self.choosing:
            self._show_choosing()

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
        shown = '' if len(tasks) == total else f'{len(tasks)} shown  ·  '
        if self.engine.running:
            engine = f'aria2 {self.engine.version}' if self.engine.version else 'engine running'
            if self.engine.held:
                engine += '  ·  waiting for Wi-Fi'
        else:
            engine = 'engine stopped' if self.engine.connected else 'engine starting'
        # The version comes first: touching this line checks for a newer one.
        self.footer.text = (f'Grabbit {APP_VERSION}  ·  {engine}  ·  '
                            f'{shown}{total} download(s), {active} active')

        self.graph.push(stats.get('download_speed') or 0, stats.get('upload_speed') or 0)
        self.graph.push_tasks(self.engine.store)
        if self.graph_shown:
            self.graph.refresh()

    def on_stop(self):
        # The window is going; the downloads are not (host.py).
        self.settings.save()
        self.engine.shutdown()


if __name__ == '__main__':
    GrabbitApp().run()

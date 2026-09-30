"""Everything about one download: the desktop's bottom panel, as a Material
bottom sheet rising over the list.

Same five tabs, same fields, same sources - General from the task, Files and
Peers and Trackers from aria2, Log from what the job recorded. Once a download
has finished, Files lists what it left on the phone instead, and a tap opens
any of it.
"""

import os

from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.modalview import ModalView
from kivy.uix.scrollview import ScrollView

from grabbit.tasks import KIND_MEDIA, State
from grabbit.torrentmeta import peer_client
from grabbit.util import human_eta, human_size, human_speed, human_time

from ..files import finished, finished_files
from . import theme
from .motion import Entrance, ripple
from .widgets import Card, Handle, IconButton, Tabs

TABS = ['General', 'Files', 'Peers', 'Trackers', 'Log']


class Rows(BoxLayout):
    """A scrolling column of lines, rebuilt whenever the content changes."""

    def __init__(self, **kwargs):
        super().__init__(orientation='vertical', size_hint_y=None,
                         padding=[dp(2), dp(4)], spacing=dp(2), **kwargs)
        self.bind(minimum_height=self.setter('height'))

    def pair(self, key: str, value: str):
        """One label-and-value line, as the General tab has."""
        line = BoxLayout(size_hint_y=None, spacing=dp(8))
        name = Label(text=key, color=theme.ON_SURFACE_VARIANT, font_size=theme.BODY_SMALL,
                     halign='right', valign='top', size_hint_x=None, width=dp(104))
        content = Label(text=value or '—', color=theme.ON_SURFACE, font_size=theme.BODY_SMALL,
                        halign='left', valign='top')
        for label in (name, content):
            label.bind(size=lambda widget, v: setattr(widget, 'text_size', (v[0], None)))
        content.bind(texture_size=lambda widget, size: setattr(line, 'height',
                                                               max(dp(20), size[1] + dp(6))))
        line.height = dp(20)
        line.add_widget(name)
        line.add_widget(content)
        self.add_widget(line)

    def line(self, text: str, color=None, size=12):
        label = Label(text=text, color=color or theme.ON_SURFACE, font_size=dp(size),
                      halign='left', valign='top', size_hint_y=None)
        label.bind(size=lambda widget, v: setattr(widget, 'text_size', (v[0], None)))
        label.bind(texture_size=lambda widget, s: setattr(widget, 'height', s[1] + dp(4)))
        self.add_widget(label)

    def columns(self, values: list, widths: list, color=None, header=False):
        """A row of a table: Files, Peers."""
        line = BoxLayout(size_hint_y=None, height=dp(22), spacing=dp(6))
        for text, width in zip(values, widths):
            label = Label(text=str(text), font_size=dp(11),
                          color=color or (theme.ON_SURFACE_VARIANT if header else theme.ON_SURFACE),
                          halign='left' if width == 0 else 'right', valign='middle',
                          shorten=True, shorten_from='right',
                          size_hint_x=(1 if width == 0 else None),
                          width=(0 if width == 0 else dp(width)))
            label.bind(size=lambda widget, v: setattr(widget, 'text_size', v))
            line.add_widget(label)
        self.add_widget(line)


class FileLine(ButtonBehavior, BoxLayout):
    """A finished file, to open with a tap: its name - cut short in the
    middle, so the type at the end stays - and its size."""

    def __init__(self, name: str, size: str, **kwargs):
        super().__init__(size_hint_y=None, height=dp(38), spacing=dp(8),
                         padding=[dp(8), 0], **kwargs)
        self.ripple = ripple(self, radius=dp(theme.SMALL), color=theme.ON_SURFACE)
        title = Label(text=name, color=theme.ON_SURFACE, font_size=theme.BODY_MEDIUM,
                      halign='left', valign='middle', shorten=True, shorten_from='center')
        amount = Label(text=size, color=theme.ON_SURFACE_VARIANT, font_size=theme.BODY_SMALL,
                       halign='right', valign='middle', size_hint_x=None, width=dp(66))
        for label in (title, amount):
            label.bind(size=lambda widget, value: setattr(widget, 'text_size', value))
            self.add_widget(label)


class DetailsSheet(Entrance, ModalView):
    """on_open_file(path) opens one of a finished download's files. insets
    is (top, bottom): the sheet keeps its last line clear of the gesture bar."""

    entrance = 'sheet'

    def __init__(self, engine, on_open_file=None, insets=(0, 0), **kwargs):
        super().__init__(size_hint=(1, 0.9), background_color=theme.TRANSPARENT,
                         background='', overlay_color=theme.with_alpha(theme.SCRIM, 0.32),
                         auto_dismiss=True, **kwargs)
        self.engine = engine
        self.on_open_file = on_open_file
        self.task_id = ''
        self.tab = 'General'
        self._refresher = None
        self._listed = None             # the finished files on show, as last listed

        # Rounded at the top only: the sheet comes up out of the bottom edge.
        top = theme.EXTRA_LARGE
        frame = Card(orientation='vertical', radius=(top, top, 0, 0), spacing=dp(4),
                     padding=[dp(16), 0, dp(16), dp(12) + insets[1]],
                     fill=theme.SURFACE_CONTAINER_LOW)
        frame.add_widget(Handle(size_hint_y=None, height=dp(28)))
        header = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(8))
        self.title = Label(text='', color=theme.ON_SURFACE, font_size=theme.TITLE_MEDIUM,
                           halign='left', valign='middle', shorten=True, shorten_from='right')
        self.title.bind(size=lambda widget, value: setattr(widget, 'text_size', value))
        close = IconButton('close')
        close.bind(on_release=lambda *_: self.dismiss())
        header.add_widget(self.title)
        header.add_widget(close)
        frame.add_widget(header)

        self.tabs = Tabs(TABS, self.tab, self.show_tab)
        self.tab_chips = self.tabs.buttons
        frame.add_widget(self.tabs)

        self.scroll = ScrollView()
        self.rows = Rows()
        self.scroll.add_widget(self.rows)
        frame.add_widget(self.scroll)
        self.add_widget(frame)

    def _align_center(self, *_):
        """Along the bottom edge, rather than in the middle where Kivy puts a view."""
        if self._is_open:
            self.center_x = self._window.center[0]
            self.y = 0

    # ------------------------------------------------------------------ show
    def open_task(self, task_id: str, tab: str = 'General'):
        self.task_id = task_id
        self.show_tab(tab)
        self.open()
        self._align_center()
        self._refresher = Clock.schedule_interval(lambda _: self.refresh(), 1.0)

    def on_dismiss(self):
        if self._refresher:
            self._refresher.cancel()
            self._refresher = None
        return super().on_dismiss()

    def show_tab(self, name: str):
        self.tab = name
        self._listed = None
        self.tabs.select(name)
        self.refresh()

    def refresh(self):
        task = self.engine.store.get(self.task_id)
        if task is None:
            self.dismiss()
            return
        self.title.text = task.name or task.source
        if self.tab == 'Files' and finished(task) and self.on_open_file is not None:
            found = finished_files(task)
            # Drawn again only when the files change, so a finger on one is
            # not left touching a line that has been replaced.
            if self._listed != (task.id, found):
                self._listed = (task.id, found)
                self.rows.clear_widgets()
                self._list_finished(found)
            return
        self._listed = None
        self.rows.clear_widgets()
        getattr(self, f'_show_{self.tab.lower()}')(task)

    # ------------------------------------------------------------ the tabs
    def _show_general(self, task):
        percent = f'{task.progress * 100:.1f}%'
        speed = human_speed(task.down_speed) or '—'
        if task.up_speed:
            speed = f'{speed}   ↑ {human_speed(task.up_speed)}'
        if task.is_torrent:
            shared = f'{human_size(task.uploaded)}  ({task.ratio:.2f})'
            peers = f'{task.seeds} seed(s), {max(0, task.connections - task.seeds)} peer(s)'
        else:
            shared = '—'
            peers = f'{task.connections} connection(s)' if task.connections else '—'

        for key, value in (
                ('Name', task.name or task.source),
                ('Status', task.status_text),
                ('Size', human_size(task.total) or 'unknown'),
                ('Downloaded', f'{human_size(task.done)}  ({percent})'),
                ('Speed', speed),
                ('Time left', human_eta(task.eta) if task.eta else '—'),
                ('Save path', task.file_path or task.save_dir),
                ('Source', task.source[:400]),
                ('Added', human_time(task.added_at)),
                ('Completed', human_time(task.completed_at) or '—'),
                ('Uploaded / ratio', shared),
                ('Peers', peers)):
            self.rows.pair(key + ':', value)
        if task.error:
            self.rows.pair('Error:', task.error)

    def _list_finished(self, found):
        if not found:
            self.rows.line('The files are not where they were saved any more - '
                           'moved or deleted since.', theme.ON_SURFACE_VARIANT)
            return
        self.rows.line('Tap a file to open it.', theme.ON_SURFACE_VARIANT, size=11)
        # Inside a torrent's folder, each by its place in it.
        base = os.path.dirname(os.path.commonpath(found)) if len(found) == 1 else os.path.commonpath(found)
        for path in found:
            try:
                size = human_size(os.path.getsize(path))
            except OSError:
                size = ''
            line = FileLine(os.path.relpath(path, base), size)
            line.bind(on_release=lambda _, chosen=path: self.on_open_file(chosen))
            self.rows.add_widget(line)

    def _show_files(self, task):
        widths = [0, 62, 62, 46]
        self.rows.columns(['File', 'Size', 'Done', ''], widths, header=True)

        def show(files):
            def paint(_):
                if self.engine.store.get(self.task_id) is not task or self.tab != 'Files':
                    return
                self.rows.clear_widgets()
                self.rows.columns(['File', 'Size', 'Done', ''], widths, header=True)
                for entry in files or []:
                    length = int(entry.get('length') or 0)
                    done = int(entry.get('completedLength') or 0)
                    skipped = entry.get('selected') == 'false'
                    self.rows.columns(
                        [os.path.basename(entry.get('path') or '') or '?',
                         human_size(length), human_size(done),
                         'skipped' if skipped else f'{(done / length * 100) if length else 0:.0f}%'],
                        widths, color=theme.ON_SURFACE_VARIANT if skipped else None)
                if not files:
                    if task.file_path:
                        self.rows.columns([os.path.basename(task.file_path),
                                           human_size(task.total), human_size(task.done),
                                           '100%' if task.state == State.COMPLETED else ''],
                                          widths)
                    else:
                        self.rows.line('No file list for this download.', theme.ON_SURFACE_VARIANT)
            Clock.schedule_once(paint, 0)

        if task.gid:
            self.engine.fetch_files(task, show)
        else:
            show([])

    def _show_peers(self, task):
        if not task.is_torrent:
            self.rows.line('Peers are a torrent thing.', theme.ON_SURFACE_VARIANT)
            return
        widths = [0, 86, 40, 70, 70]

        def show(peers):
            def paint(_):
                if self.engine.store.get(self.task_id) is not task or self.tab != 'Peers':
                    return
                self.rows.clear_widgets()
                self.rows.columns(['Address', 'Client', 'Done', 'Down', 'Up'],
                                  widths, header=True)
                for peer in peers or []:
                    bitfield = peer.get('bitfield') or ''
                    ones = sum(bin(int(c, 16)).count('1') for c in bitfield if c in '0123456789abcdefABCDEF')
                    done = f'{ones / (len(bitfield) * 4) * 100:.0f}%' if bitfield else '—'
                    self.rows.columns(
                        [f"{peer.get('ip', '?')}:{peer.get('port', '')}",
                         peer_client(peer.get('peerId', '')), done,
                         human_speed(int(peer.get('downloadSpeed') or 0)) or '—',
                         human_speed(int(peer.get('uploadSpeed') or 0)) or '—'], widths)
                if not peers:
                    self.rows.line('No peers connected right now.', theme.ON_SURFACE_VARIANT)
            Clock.schedule_once(paint, 0)

        self.engine.fetch_peers(task, show)

    def _show_trackers(self, task):
        if not task.is_torrent:
            self.rows.line('Trackers are a torrent thing.', theme.ON_SURFACE_VARIANT)
            return

        def show(status):
            def paint(_):
                if self.engine.store.get(self.task_id) is not task or self.tab != 'Trackers':
                    return
                self.rows.clear_widgets()
                announce = (status.get('bittorrent') or {}).get('announceList') or []
                for tier in announce:
                    for url in (tier if isinstance(tier, list) else [tier]):
                        self.rows.line(url, size=11)
                if not announce:
                    self.rows.line('No trackers (DHT and peer exchange only).', theme.ON_SURFACE_VARIANT)
            Clock.schedule_once(paint, 0)

        self.engine.fetch_status(task, ['bittorrent'], show)

    def _show_log(self, task):
        # Asked for, like the file list: the downloader keeps it, in its own
        # process.
        def show(entries):
            def paint(_):
                if self.engine.store.get(self.task_id) is not task or self.tab != 'Log':
                    return
                self.rows.clear_widgets()
                if not entries and task.kind != KIND_MEDIA:
                    self.rows.line('No messages for this download.', theme.ON_SURFACE_VARIANT)
                    return
                for entry in entries:
                    self.rows.line(entry, size=11)
            Clock.schedule_once(paint, 0)

        self.engine.fetch_log(task, show)

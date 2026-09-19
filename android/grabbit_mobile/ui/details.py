"""Everything about one download: the desktop's bottom panel, full screen.

Same five tabs, same fields, same sources - General from the task, Files and
Peers and Trackers from aria2, Log from what the job recorded.
"""

import os

from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.modalview import ModalView
from kivy.uix.scrollview import ScrollView

from grabbit.tasks import KIND_MEDIA, State
from grabbit.torrentmeta import peer_client
from grabbit.util import human_eta, human_size, human_speed, human_time

from . import theme
from .widgets import Card, Chip

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
        name = Label(text=key, color=theme.DIM, font_size=dp(12), halign='right',
                     valign='top', size_hint_x=None, width=dp(104))
        content = Label(text=value or '—', color=theme.TEXT, font_size=dp(12),
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
        label = Label(text=text, color=color or theme.TEXT, font_size=dp(size),
                      halign='left', valign='top', size_hint_y=None)
        label.bind(size=lambda widget, v: setattr(widget, 'text_size', (v[0], None)))
        label.bind(texture_size=lambda widget, s: setattr(widget, 'height', s[1] + dp(4)))
        self.add_widget(label)

    def columns(self, values: list, widths: list, color=None, header=False):
        """A row of a table: Files, Peers."""
        line = BoxLayout(size_hint_y=None, height=dp(22), spacing=dp(6))
        for text, width in zip(values, widths):
            label = Label(text=str(text), font_size=dp(11),
                          color=color or (theme.DIM if header else theme.TEXT),
                          halign='left' if width == 0 else 'right', valign='middle',
                          shorten=True, shorten_from='right',
                          size_hint_x=(1 if width == 0 else None),
                          width=(0 if width == 0 else dp(width)))
            label.bind(size=lambda widget, v: setattr(widget, 'text_size', v))
            line.add_widget(label)
        self.add_widget(line)


class DetailsSheet(ModalView):
    def __init__(self, engine, **kwargs):
        super().__init__(size_hint=(0.96, 0.9), background_color=(0, 0, 0, 0.6),
                         background='', auto_dismiss=True, **kwargs)
        self.engine = engine
        self.task_id = ''
        self.tab = 'General'
        self._refresher = None

        frame = Card(orientation='vertical', padding=[dp(12), dp(10)], spacing=dp(8),
                     fill=theme.WINDOW)
        header = BoxLayout(size_hint_y=None, height=dp(30), spacing=dp(8))
        self.title = Label(text='', color=theme.TEXT, font_size=dp(15), bold=True,
                           halign='left', valign='middle', shorten=True, shorten_from='right')
        self.title.bind(size=lambda widget, value: setattr(widget, 'text_size', value))
        close = Button(text='×', size_hint_x=None, width=dp(38), font_size=dp(22),
                       color=theme.DIM, background_normal='', background_down='',
                       background_color=theme.TRANSPARENT)
        close.bind(on_release=lambda *_: self.dismiss())
        header.add_widget(self.title)
        header.add_widget(close)
        frame.add_widget(header)

        tabs = BoxLayout(size_hint_y=None, height=dp(32), spacing=dp(4))
        self.tab_chips = {}
        for name in TABS:
            chip = Chip(text=name, selected=(name == self.tab))
            chip.size_hint_x = 1      # five of them, sharing the width evenly
            chip.font_size = dp(12)
            chip.bind(on_release=lambda widget, n=name: self.show_tab(n))
            self.tab_chips[name] = chip
            tabs.add_widget(chip)
        frame.add_widget(tabs)

        self.scroll = ScrollView()
        self.rows = Rows()
        self.scroll.add_widget(self.rows)
        frame.add_widget(self.scroll)
        self.add_widget(frame)

    # ------------------------------------------------------------------ show
    def open_task(self, task_id: str):
        self.task_id = task_id
        self.show_tab('General')
        self.open()
        self._refresher = Clock.schedule_interval(lambda _: self.refresh(), 1.0)

    def on_dismiss(self):
        if self._refresher:
            self._refresher.cancel()
            self._refresher = None
        return super().on_dismiss()

    def show_tab(self, name: str):
        self.tab = name
        for key, chip in self.tab_chips.items():
            chip.set_selected(key == name)
        self.refresh()

    def refresh(self):
        task = self.engine.store.get(self.task_id)
        if task is None:
            self.dismiss()
            return
        self.title.text = task.name or task.source
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

    def _show_files(self, task):
        widths = [0, 62, 62, 46]
        self.rows.columns(['File', 'Size', 'Done', ''], widths, header=True)

        def show(files):
            def paint(_):
                if self.engine.store.get(self.task_id) is not task:
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
                        widths, color=theme.DIM if skipped else None)
                if not files:
                    if task.file_path:
                        self.rows.columns([os.path.basename(task.file_path),
                                           human_size(task.total), human_size(task.done),
                                           '100%' if task.state == State.COMPLETED else ''],
                                          widths)
                    else:
                        self.rows.line('No file list for this download.', theme.DIM)
            Clock.schedule_once(paint, 0)

        if task.gid:
            self.engine.fetch_files(task, show)
        else:
            show([])

    def _show_peers(self, task):
        if not task.is_torrent:
            self.rows.line('Peers are a torrent thing.', theme.DIM)
            return
        widths = [0, 86, 40, 70, 70]

        def show(peers):
            def paint(_):
                if self.engine.store.get(self.task_id) is not task:
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
                    self.rows.line('No peers connected right now.', theme.DIM)
            Clock.schedule_once(paint, 0)

        self.engine.fetch_peers(task, show)

    def _show_trackers(self, task):
        if not task.is_torrent:
            self.rows.line('Trackers are a torrent thing.', theme.DIM)
            return

        def show(status):
            def paint(_):
                if self.engine.store.get(self.task_id) is not task:
                    return
                self.rows.clear_widgets()
                announce = (status.get('bittorrent') or {}).get('announceList') or []
                for tier in announce:
                    for url in (tier if isinstance(tier, list) else [tier]):
                        self.rows.line(url, size=11)
                if not announce:
                    self.rows.line('No trackers (DHT and peer exchange only).', theme.DIM)
            Clock.schedule_once(paint, 0)

        self.engine.fetch_status(task, ['bittorrent'], show)

    def _show_log(self, task):
        entries = task.log[-200:]
        if not entries and task.kind != KIND_MEDIA:
            self.rows.line('No messages for this download.', theme.DIM)
            return
        for entry in entries:
            self.rows.line(entry, size=11)

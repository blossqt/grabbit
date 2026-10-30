"""Grabbit's settings, laid out as every suite app's are (suitesettings.py)."""

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractSpinBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
                               QHBoxLayout, QLineEdit, QPlainTextEdit, QPushButton, QSpinBox)

from .. import APP_NAME, APP_VERSION, associations, media
from ..paths import data_dir, find_tool
from ..settings import COOKIE_BROWSERS, VIDEO_QUALITIES
from . import icons, suitesettings, theme
from .suitesettings import form, label, note, section, switch

# Where a refused download is told to look, as this window names it. Set here rather than in
# media.py, which the phone app shares (and clears this in).
media.REFUSED_HINT = 'Settings › Videos and photos › "Use cookies from" (Firefox works best) usually fixes this.'


class SettingsDialog(QDialog):
    PAGES = [('\ue896', 'Downloads'), ('\ue716', 'BitTorrent'), ('\ue714', 'Videos and photos'),
             ('\ue80f', 'General'), ('\ue90f', 'Advanced')]

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        theme.frost(self, backdrop=theme.BACKDROP)
        self.settings = settings
        self.restart_needed = False
        self.setWindowTitle('Grabbit settings')
        self.resize(860, 640)
        frame = suitesettings.SettingsFrame(self, icons.render_app_icon(28))
        pages = iter(self.PAGES)

        # ---------------------------------------------------------- downloads
        layout = frame.add(*next(pages))
        box_frame, box = section('Where')
        rows = form()
        row = QHBoxLayout()
        self.download_dir = QLineEdit(settings.download_dir)
        browse = QPushButton('Browse…')
        browse.clicked.connect(self._browse_dir)
        row.addWidget(self.download_dir, 1)
        row.addWidget(browse)
        rows.addRow(label('Save downloads to'), row)
        box.addLayout(rows)
        holder, self.site_subfolders = switch('Put downloads in a folder per site (YouTube, Instagram…)', '',
                                              settings.site_subfolders)
        box.addWidget(holder)
        layout.addWidget(box_frame)

        box_frame, box = section('Speed')
        rows = form()
        self.max_active = QSpinBox()
        self.max_active.setRange(1, 50)
        self.max_active.setValue(settings.max_active_downloads)
        rows.addRow(label('Most downloads at once'), self.max_active)

        self.max_media = QSpinBox()
        self.max_media.setRange(1, 20)
        self.max_media.setValue(settings.max_media_jobs)
        rows.addRow(label('Most video downloads at once'), self.max_media)

        self.connections = QSpinBox()
        self.connections.setRange(1, 16)
        self.connections.setValue(settings.connections_per_server)
        rows.addRow(label('Connections per download'), self.connections)

        self.min_split = QSpinBox()
        self.min_split.setRange(1, 64)
        self.min_split.setSuffix(' MiB')
        self.min_split.setValue(settings.min_split_size_mb)
        rows.addRow(label('Smallest piece per connection'), self.min_split)

        self.down_limit = QSpinBox()
        self.down_limit.setRange(0, 10_000_000)
        self.down_limit.setSuffix(' KiB/s')
        self.down_limit.setSpecialValueText('No limit')
        self.down_limit.setValue(settings.download_limit_kib)
        rows.addRow(label('Download speed limit'), self.down_limit)

        self.up_limit = QSpinBox()
        self.up_limit.setRange(0, 10_000_000)
        self.up_limit.setSuffix(' KiB/s')
        self.up_limit.setSpecialValueText('No limit')
        self.up_limit.setValue(settings.upload_limit_kib)
        rows.addRow(label('Upload speed limit'), self.up_limit)
        box.addLayout(rows)
        layout.addWidget(box_frame)
        layout.addStretch(1)

        # -------------------------------------------------------- bittorrent
        layout = frame.add(*next(pages))
        box_frame, box = section('Peers')
        rows = form()
        self.bt_port = QSpinBox()
        self.bt_port.setRange(1024, 65535)
        self.bt_port.setValue(settings.bt_port)
        rows.addRow(label('Listening port'), self.bt_port)
        self.max_peers = QSpinBox()
        self.max_peers.setRange(1, 500)
        self.max_peers.setValue(settings.max_peers)
        rows.addRow(label('Most peers per torrent'), self.max_peers)
        box.addLayout(rows)
        holder, self.enable_dht = switch('Use DHT', 'Finds peers without a tracker.', settings.enable_dht)
        box.addWidget(holder)
        holder, self.enable_pex = switch('Use peer exchange', '', settings.enable_pex)
        box.addWidget(holder)
        holder, self.enable_lpd = switch('Look for peers on the local network', '', settings.enable_lpd)
        box.addWidget(holder)
        holder, self.require_encryption = switch('Require encrypted peer connections', '',
                                                 settings.require_encryption)
        box.addWidget(holder)
        layout.addWidget(box_frame)

        box_frame, box = section('Seeding')
        holder, self.seed_after = switch('Keep seeding after a torrent finishes', '', settings.seed_after_download)
        box.addWidget(holder)
        rows = form()
        self.seed_ratio = QDoubleSpinBox()
        self.seed_ratio.setRange(0.0, 100.0)
        self.seed_ratio.setSingleStep(0.1)
        self.seed_ratio.setDecimals(2)
        self.seed_ratio.setSpecialValueText('No limit')
        self.seed_ratio.setValue(settings.seed_ratio)
        rows.addRow(label('Stop seeding at ratio'), self.seed_ratio)
        self.seed_time = QSpinBox()
        self.seed_time.setRange(0, 100000)
        self.seed_time.setSuffix(' min')
        self.seed_time.setSpecialValueText('No limit')
        self.seed_time.setValue(settings.seed_time_minutes)
        rows.addRow(label('Stop seeding after'), self.seed_time)
        box.addLayout(rows)
        layout.addWidget(box_frame)

        box_frame, box = section('Adding torrents')
        holder, self.show_torrent_dialog = switch('Ask which files to download when adding a torrent', '',
                                                  settings.show_torrent_dialog)
        box.addWidget(holder)
        box.addWidget(label('Extra trackers'))
        self.trackers = QPlainTextEdit(settings.extra_trackers)
        self.trackers.setPlaceholderText('One tracker URL per line, added to every torrent')
        self.trackers.setMaximumHeight(90)
        box.addWidget(self.trackers)
        layout.addWidget(box_frame)
        layout.addStretch(1)

        # ------------------------------------------------------ videos/photos
        layout = frame.add(*next(pages))
        box_frame, box = section('Videos')
        rows = form()
        self.quality = QComboBox()
        for value, title in VIDEO_QUALITIES:
            self.quality.addItem(title, value)
        index = self.quality.findData(settings.video_quality)
        self.quality.setCurrentIndex(max(0, index))
        rows.addRow(label('Quality'), self.quality)

        self.container = QComboBox()
        for value, title in (('mp4', 'MP4 (most compatible)'), ('mkv', 'MKV'),
                             ('webm', 'WebM, where the site has it'),
                             ('any', 'Whatever the site offers')):
            self.container.addItem(title, value)
        index = self.container.findData(settings.video_container)
        self.container.setCurrentIndex(max(0, index))
        rows.addRow(label('Container'), self.container)

        self.filename_template = QLineEdit(settings.filename_template)
        rows.addRow(label('File name pattern'), self.filename_template)
        box.addLayout(rows)
        box.addWidget(note('A yt-dlp output template, such as %(title)s [%(id)s].%(ext)s'))
        holder, self.embed_thumbnail = switch('Embed the thumbnail as cover art', '', settings.embed_thumbnail)
        box.addWidget(holder)
        holder, self.embed_metadata = switch('Embed title, uploader and chapters', '', settings.embed_metadata)
        box.addWidget(holder)
        holder, self.media_via_aria2 = switch('Download video streams through aria2',
                                              'Faster, with several connections at once.', settings.media_via_aria2)
        box.addWidget(holder)
        layout.addWidget(box_frame)

        box_frame, box = section('Subtitles')
        holder, self.subtitles = switch('Download and embed subtitles', '', settings.download_subtitles)
        box.addWidget(holder)
        rows = form()
        self.subtitle_langs = QLineEdit(settings.subtitle_langs)
        self.subtitle_langs.setPlaceholderText('en.*, de, fr')
        rows.addRow(label('Languages'), self.subtitle_langs)
        box.addLayout(rows)
        layout.addWidget(box_frame)

        box_frame, box = section('GIFs')
        gif_row = QHBoxLayout()
        gif_row.setSpacing(8)
        self.gif_fps = QSpinBox()
        self.gif_fps.setRange(5, 50)
        self.gif_fps.setSuffix(' fps')
        self.gif_fps.setValue(settings.gif_fps)
        self.gif_width = QSpinBox()
        self.gif_width.setRange(120, 1920)
        self.gif_width.setSingleStep(40)
        self.gif_width.setSuffix(' px wide')
        self.gif_width.setValue(settings.gif_width)
        self.gif_seconds = QSpinBox()
        self.gif_seconds.setRange(0, 600)
        self.gif_seconds.setSuffix(' s')
        self.gif_seconds.setSpecialValueText('Whole video')
        self.gif_seconds.setValue(settings.gif_max_seconds)
        for widget in (self.gif_fps, self.gif_width, self.gif_seconds):
            gif_row.addWidget(widget)
        gif_row.addStretch(1)
        box.addLayout(gif_row)
        box.addWidget(note('GIFs get big fast: a minute at 480 px and 15 fps is tens of megabytes.'))
        layout.addWidget(box_frame)

        box_frame, box = section('Signed-in sites')
        rows = form()
        self.cookies_browser = QComboBox()
        for value, title in COOKIE_BROWSERS:
            self.cookies_browser.addItem(title, value)
        index = self.cookies_browser.findData(settings.cookies_browser)
        self.cookies_browser.setCurrentIndex(max(0, index))
        rows.addRow(label('Use cookies from'), self.cookies_browser)
        row = QHBoxLayout()
        self.cookies_file = QLineEdit(settings.cookies_file)
        self.cookies_file.setPlaceholderText('Optional cookies.txt, used instead of the browser')
        pick = QPushButton('Browse…')
        pick.clicked.connect(self._browse_cookies)
        row.addWidget(self.cookies_file, 1)
        row.addWidget(pick)
        rows.addRow(label('Cookies file'), row)
        box.addLayout(rows)
        box.addWidget(note('Lets Grabbit reach posts that need you to be logged in. '
                           'Chrome and Edge keep their cookies locked, so Firefox works best.'))
        layout.addWidget(box_frame)
        layout.addStretch(1)

        # -------------------------------------------------------- general
        layout = frame.add(*next(pages))
        box_frame, box = section('Links')
        holder, self.watch_clipboard = switch('Offer to download links I copy', '', settings.watch_clipboard)
        box.addWidget(holder)
        picked = ('Windows opens magnet links with another app now. Change it under Settings › Apps › '
                  'Default apps.' if associations.windows_picked_another_app() else '')
        holder, self.magnet_handler = switch('Open magnet links with Grabbit', picked,
                                             associations.is_magnet_handler())
        box.addWidget(holder)
        layout.addWidget(box_frame)

        box_frame, box = section('Window and notifications')
        holder, self.close_to_tray = switch('Keep running in the tray when closed', '', settings.close_to_tray)
        box.addWidget(holder)
        holder, self.notify = switch('Show a notification when a download finishes', '',
                                     settings.notify_on_complete)
        box.addWidget(holder)
        holder, self.confirm_remove = switch('Ask before removing downloads', '', settings.confirm_remove)
        box.addWidget(holder)
        rows = form()
        self.theme = QComboBox()
        for value, title in (('system', 'As the suite (Hanami)'), ('light', 'Light'), ('dark', 'Dark')):
            self.theme.addItem(title, value)
        index = self.theme.findData(settings.theme)
        self.theme.setCurrentIndex(max(0, index))
        rows.addRow(label('Light or dark'), self.theme)
        box.addLayout(rows)
        layout.addWidget(box_frame)

        # Updates: whether to look at start, and a way to look now. "Check now"
        # saves and closes first, so an update that restarts Grabbit cannot
        # take anything typed here with it.
        box_frame, box = section('Updates')
        holder, self.check_for_updates = switch('Check for updates when Grabbit starts', '',
                                                settings.check_for_updates)
        box.addWidget(holder)
        self.check_now_requested = False
        check_now = QPushButton('Check now')
        check_now.clicked.connect(self._check_now)
        version_row = QHBoxLayout()
        version_row.addWidget(label(f'You have {APP_NAME} {APP_VERSION}.'))
        version_row.addStretch(1)
        version_row.addWidget(check_now)
        box.addLayout(version_row)
        layout.addWidget(box_frame)
        layout.addStretch(1)

        # --------------------------------------------------------- advanced
        layout = frame.add(*next(pages))
        box_frame, box = section('Network')
        rows = form()
        self.proxy = QLineEdit(settings.proxy)
        self.proxy.setPlaceholderText('http://host:port, for aria2 and yt-dlp')
        rows.addRow(label('Proxy'), self.proxy)

        self.user_agent = QLineEdit(settings.user_agent)
        self.user_agent.setPlaceholderText('Empty: as Chrome')
        rows.addRow(label('User agent'), self.user_agent)

        self.ipv6 = QComboBox()
        for value, title in (('auto', 'Automatic (off when unreachable)'), ('on', 'Always on'), ('off', 'Always off')):
            self.ipv6.addItem(title, value)
        index = self.ipv6.findData(settings.ipv6_mode)
        self.ipv6.setCurrentIndex(max(0, index))
        rows.addRow(label('IPv6'), self.ipv6)
        box.addLayout(rows)
        layout.addWidget(box_frame)

        box_frame, box = section('Extra aria2 options')
        self.extra_options = QPlainTextEdit(settings.aria2_extra_options)
        self.extra_options.setPlaceholderText('One aria2 option per line, such as\nfile-allocation=falloc')
        self.extra_options.setMaximumHeight(90)
        box.addWidget(self.extra_options)
        layout.addWidget(box_frame)

        box_frame, box = section('Components')
        tools = []
        for name in ('aria2c', 'ffmpeg', 'deno'):
            path = find_tool(name)
            tools.append(f'{name}: {path if path else "not found"}')
        tools.append(f'Data folder: {data_dir()}')
        info = note('\n'.join(tools))
        info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        box.addWidget(info)
        layout.addWidget(box_frame)
        layout.addStretch(1)

        for box in self.findChildren(QAbstractSpinBox):
            box.setMinimumWidth(140)                # one width down a section, as a column

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setProperty('role', 'primary')
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        bottom = QHBoxLayout()
        bottom.addWidget(label(f'{APP_NAME} {APP_VERSION}', 9, role='dim'))
        bottom.addStretch(1)
        bottom.addWidget(buttons)
        frame.footer(bottom)

    def _browse_dir(self):
        folder = QFileDialog.getExistingDirectory(self, 'Save downloads to', self.download_dir.text())
        if folder:
            self.download_dir.setText(folder)

    def _browse_cookies(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Choose a cookies.txt file',
                                              os.path.dirname(self.cookies_file.text()),
                                              'Cookie files (*.txt);;All files (*)')
        if path:
            self.cookies_file.setText(path)

    def _check_now(self):
        self.check_now_requested = True
        self._save()

    def _save(self):
        s = self.settings
        restart_keys = (s.bt_port, s.enable_dht, s.enable_pex, s.enable_lpd,
                        s.require_encryption, s.seed_after_download, s.seed_ratio,
                        s.seed_time_minutes, s.max_peers, s.extra_trackers, s.proxy,
                        s.user_agent, s.aria2_extra_options, s.ipv6_mode)

        s.download_dir = self.download_dir.text().strip() or s.download_dir
        s.max_active_downloads = self.max_active.value()
        s.max_media_jobs = self.max_media.value()
        s.connections_per_server = self.connections.value()
        s.min_split_size_mb = self.min_split.value()
        s.download_limit_kib = self.down_limit.value()
        s.upload_limit_kib = self.up_limit.value()
        s.site_subfolders = self.site_subfolders.isChecked()

        s.bt_port = self.bt_port.value()
        s.enable_dht = self.enable_dht.isChecked()
        s.enable_pex = self.enable_pex.isChecked()
        s.enable_lpd = self.enable_lpd.isChecked()
        s.require_encryption = self.require_encryption.isChecked()
        s.seed_after_download = self.seed_after.isChecked()
        s.seed_ratio = self.seed_ratio.value()
        s.seed_time_minutes = self.seed_time.value()
        s.max_peers = self.max_peers.value()
        s.extra_trackers = self.trackers.toPlainText()
        s.show_torrent_dialog = self.show_torrent_dialog.isChecked()

        s.video_quality = self.quality.currentData()
        s.video_container = self.container.currentData()
        s.embed_thumbnail = self.embed_thumbnail.isChecked()
        s.embed_metadata = self.embed_metadata.isChecked()
        s.download_subtitles = self.subtitles.isChecked()
        s.subtitle_langs = self.subtitle_langs.text().strip() or 'en.*'
        s.filename_template = self.filename_template.text().strip() or '%(title).150B [%(id)s].%(ext)s'
        s.gif_fps = self.gif_fps.value()
        s.gif_width = self.gif_width.value()
        s.gif_max_seconds = self.gif_seconds.value()
        s.cookies_browser = self.cookies_browser.currentData()
        s.cookies_file = self.cookies_file.text().strip()
        s.media_via_aria2 = self.media_via_aria2.isChecked()

        s.theme = self.theme.currentData()
        s.watch_clipboard = self.watch_clipboard.isChecked()
        if self.magnet_handler.isChecked() != associations.is_magnet_handler():
            s.magnet_handler = self.magnet_handler.isChecked()
            associations.set_magnet_handler(s.magnet_handler)
            if s.magnet_handler:
                associations.register_torrent_type()
        s.close_to_tray = self.close_to_tray.isChecked()
        s.notify_on_complete = self.notify.isChecked()
        s.confirm_remove = self.confirm_remove.isChecked()
        s.check_for_updates = self.check_for_updates.isChecked()

        s.proxy = self.proxy.text().strip()
        s.user_agent = self.user_agent.text().strip()
        s.ipv6_mode = self.ipv6.currentData()
        s.aria2_extra_options = self.extra_options.toPlainText()

        new_keys = (s.bt_port, s.enable_dht, s.enable_pex, s.enable_lpd,
                    s.require_encryption, s.seed_after_download, s.seed_ratio,
                    s.seed_time_minutes, s.max_peers, s.extra_trackers, s.proxy,
                    s.user_agent, s.aria2_extra_options, s.ipv6_mode)
        self.restart_needed = restart_keys != new_keys
        s.save()
        self.accept()

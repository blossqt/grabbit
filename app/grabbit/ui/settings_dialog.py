"""Options window."""

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QPushButton, QSpinBox, QTabWidget, QVBoxLayout,
                               QWidget)

from .. import associations
from ..paths import data_dir, find_tool
from ..settings import COOKIE_BROWSERS, VIDEO_QUALITIES


def _page(tabs: QTabWidget, title: str) -> QFormLayout:
    page = QWidget()
    outer = QVBoxLayout(page)
    form = QFormLayout()
    form.setLabelAlignment(Qt.AlignRight)
    form.setHorizontalSpacing(12)
    form.setVerticalSpacing(8)
    outer.addLayout(form)
    outer.addStretch(1)
    tabs.addTab(page, title)
    return form


class SettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.restart_needed = False
        self.setWindowTitle('Options')
        self.resize(660, 620)

        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs, 1)

        # ---------------------------------------------------------- downloads
        form = _page(tabs, 'Downloads')
        row = QHBoxLayout()
        self.download_dir = QLineEdit(settings.download_dir)
        browse = QPushButton('Browse…')
        browse.clicked.connect(self._browse_dir)
        row.addWidget(self.download_dir, 1)
        row.addWidget(browse)
        form.addRow('Save downloads to', row)

        self.max_active = QSpinBox()
        self.max_active.setRange(1, 50)
        self.max_active.setValue(settings.max_active_downloads)
        form.addRow('Maximum active downloads', self.max_active)

        self.max_media = QSpinBox()
        self.max_media.setRange(1, 20)
        self.max_media.setValue(settings.max_media_jobs)
        form.addRow('Maximum video downloads at once', self.max_media)

        self.connections = QSpinBox()
        self.connections.setRange(1, 16)
        self.connections.setValue(settings.connections_per_server)
        form.addRow('Connections per download', self.connections)

        self.min_split = QSpinBox()
        self.min_split.setRange(1, 64)
        self.min_split.setSuffix(' MiB')
        self.min_split.setValue(settings.min_split_size_mb)
        form.addRow('Smallest piece per connection', self.min_split)

        self.down_limit = QSpinBox()
        self.down_limit.setRange(0, 10_000_000)
        self.down_limit.setSuffix(' KiB/s')
        self.down_limit.setSpecialValueText('unlimited')
        self.down_limit.setValue(settings.download_limit_kib)
        form.addRow('Download speed limit', self.down_limit)

        self.up_limit = QSpinBox()
        self.up_limit.setRange(0, 10_000_000)
        self.up_limit.setSuffix(' KiB/s')
        self.up_limit.setSpecialValueText('unlimited')
        self.up_limit.setValue(settings.upload_limit_kib)
        form.addRow('Upload speed limit', self.up_limit)

        self.site_subfolders = QCheckBox('Put downloads in a folder per site (YouTube, Instagram…)')
        self.site_subfolders.setChecked(settings.site_subfolders)
        form.addRow('', self.site_subfolders)

        # -------------------------------------------------------- bittorrent
        form = _page(tabs, 'BitTorrent')
        self.bt_port = QSpinBox()
        self.bt_port.setRange(1024, 65535)
        self.bt_port.setValue(settings.bt_port)
        form.addRow('Listening port', self.bt_port)

        self.enable_dht = QCheckBox('Use DHT (find peers without a tracker)')
        self.enable_dht.setChecked(settings.enable_dht)
        form.addRow('', self.enable_dht)
        self.enable_pex = QCheckBox('Use peer exchange')
        self.enable_pex.setChecked(settings.enable_pex)
        form.addRow('', self.enable_pex)
        self.enable_lpd = QCheckBox('Look for peers on the local network')
        self.enable_lpd.setChecked(settings.enable_lpd)
        form.addRow('', self.enable_lpd)
        self.require_encryption = QCheckBox('Require encrypted peer connections')
        self.require_encryption.setChecked(settings.require_encryption)
        form.addRow('', self.require_encryption)

        self.seed_after = QCheckBox('Keep seeding after a torrent finishes')
        self.seed_after.setChecked(settings.seed_after_download)
        form.addRow('', self.seed_after)

        self.seed_ratio = QDoubleSpinBox()
        self.seed_ratio.setRange(0.0, 100.0)
        self.seed_ratio.setSingleStep(0.1)
        self.seed_ratio.setDecimals(2)
        self.seed_ratio.setSpecialValueText('no limit')
        self.seed_ratio.setValue(settings.seed_ratio)
        form.addRow('Stop seeding at ratio', self.seed_ratio)

        self.seed_time = QSpinBox()
        self.seed_time.setRange(0, 100000)
        self.seed_time.setSuffix(' min')
        self.seed_time.setSpecialValueText('no limit')
        self.seed_time.setValue(settings.seed_time_minutes)
        form.addRow('Stop seeding after', self.seed_time)

        self.max_peers = QSpinBox()
        self.max_peers.setRange(1, 500)
        self.max_peers.setValue(settings.max_peers)
        form.addRow('Maximum peers per torrent', self.max_peers)

        self.show_torrent_dialog = QCheckBox('Ask which files to download when adding a torrent')
        self.show_torrent_dialog.setChecked(settings.show_torrent_dialog)
        form.addRow('', self.show_torrent_dialog)

        self.trackers = QPlainTextEdit(settings.extra_trackers)
        self.trackers.setPlaceholderText('One tracker URL per line, added to every torrent')
        self.trackers.setMaximumHeight(90)
        form.addRow('Extra trackers', self.trackers)

        # ------------------------------------------------------ videos/photos
        form = _page(tabs, 'Videos & photos')
        self.quality = QComboBox()
        for value, label in VIDEO_QUALITIES:
            self.quality.addItem(label, value)
        index = self.quality.findData(settings.video_quality)
        self.quality.setCurrentIndex(max(0, index))
        form.addRow('Default quality', self.quality)

        self.container = QComboBox()
        for value, label in (('mp4', 'MP4 (most compatible)'), ('mkv', 'MKV'), ('any', 'Whatever the site offers')):
            self.container.addItem(label, value)
        index = self.container.findData(settings.video_container)
        self.container.setCurrentIndex(max(0, index))
        form.addRow('Container', self.container)

        self.embed_thumbnail = QCheckBox('Embed the thumbnail as cover art')
        self.embed_thumbnail.setChecked(settings.embed_thumbnail)
        form.addRow('', self.embed_thumbnail)
        self.embed_metadata = QCheckBox('Embed title, uploader and chapters')
        self.embed_metadata.setChecked(settings.embed_metadata)
        form.addRow('', self.embed_metadata)
        self.subtitles = QCheckBox('Download and embed subtitles')
        self.subtitles.setChecked(settings.download_subtitles)
        form.addRow('', self.subtitles)
        self.subtitle_langs = QLineEdit(settings.subtitle_langs)
        self.subtitle_langs.setPlaceholderText('en.*, de, fr')
        form.addRow('Subtitle languages', self.subtitle_langs)

        self.filename_template = QLineEdit(settings.filename_template)
        form.addRow('File name pattern', self.filename_template)
        hint = QLabel('yt-dlp output template, e.g. %(title)s [%(id)s].%(ext)s')
        hint.setObjectName('Muted')
        form.addRow('', hint)

        gif_row = QHBoxLayout()
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
        self.gif_seconds.setSpecialValueText('whole video')
        self.gif_seconds.setValue(settings.gif_max_seconds)
        for widget in (self.gif_fps, self.gif_width, self.gif_seconds):
            gif_row.addWidget(widget)
        gif_row.addStretch(1)
        form.addRow('GIF settings', gif_row)
        gif_hint = QLabel('GIFs get big fast — a minute at 480px and 15 fps is tens of megabytes.')
        gif_hint.setObjectName('Muted')
        gif_hint.setWordWrap(True)
        form.addRow('', gif_hint)

        self.cookies_browser = QComboBox()
        for value, label in COOKIE_BROWSERS:
            self.cookies_browser.addItem(label, value)
        index = self.cookies_browser.findData(settings.cookies_browser)
        self.cookies_browser.setCurrentIndex(max(0, index))
        form.addRow('Use cookies from', self.cookies_browser)
        cookie_hint = QLabel('Lets Grabbit reach posts that need you to be logged in. '
                             'Chrome and Edge keep their cookies locked, so Firefox works best.')
        cookie_hint.setObjectName('Muted')
        cookie_hint.setWordWrap(True)
        form.addRow('', cookie_hint)

        row = QHBoxLayout()
        self.cookies_file = QLineEdit(settings.cookies_file)
        self.cookies_file.setPlaceholderText('Optional cookies.txt file (overrides the browser above)')
        pick = QPushButton('Browse…')
        pick.clicked.connect(self._browse_cookies)
        row.addWidget(self.cookies_file, 1)
        row.addWidget(pick)
        form.addRow('Cookies file', row)

        self.media_via_aria2 = QCheckBox('Download video streams through aria2 (faster, multi-connection)')
        self.media_via_aria2.setChecked(settings.media_via_aria2)
        form.addRow('', self.media_via_aria2)

        # -------------------------------------------------------- interface
        form = _page(tabs, 'Interface')
        self.theme = QComboBox()
        for value, label in (('system', 'Follow Windows'), ('light', 'Light'), ('dark', 'Dark')):
            self.theme.addItem(label, value)
        index = self.theme.findData(settings.theme)
        self.theme.setCurrentIndex(max(0, index))
        form.addRow('Theme', self.theme)

        self.watch_clipboard = QCheckBox('Offer to download links I copy')
        self.watch_clipboard.setChecked(settings.watch_clipboard)
        form.addRow('', self.watch_clipboard)

        self.magnet_handler = QCheckBox('Open magnet links with Grabbit')
        self.magnet_handler.setChecked(associations.is_magnet_handler())
        form.addRow('', self.magnet_handler)
        if associations.windows_picked_another_app():
            warning = QLabel('Windows currently opens magnet links with another app. '
                             'Change it under Settings › Apps › Default apps.')
            warning.setObjectName('Muted')
            warning.setWordWrap(True)
            form.addRow('', warning)
        self.close_to_tray = QCheckBox('Keep running in the notification area when closed')
        self.close_to_tray.setChecked(settings.close_to_tray)
        form.addRow('', self.close_to_tray)
        self.notify = QCheckBox('Show a notification when a download finishes')
        self.notify.setChecked(settings.notify_on_complete)
        form.addRow('', self.notify)
        self.confirm_remove = QCheckBox('Ask before removing downloads')
        self.confirm_remove.setChecked(settings.confirm_remove)
        form.addRow('', self.confirm_remove)

        # --------------------------------------------------------- advanced
        form = _page(tabs, 'Advanced')
        self.proxy = QLineEdit(settings.proxy)
        self.proxy.setPlaceholderText('http://host:port  (used by aria2 and yt-dlp)')
        form.addRow('Proxy', self.proxy)

        self.user_agent = QLineEdit(settings.user_agent)
        self.user_agent.setPlaceholderText('Leave empty to look like Chrome')
        form.addRow('User agent', self.user_agent)

        self.ipv6 = QComboBox()
        for value, label in (('auto', 'Automatic (off when unreachable)'), ('on', 'Always on'), ('off', 'Always off')):
            self.ipv6.addItem(label, value)
        index = self.ipv6.findData(settings.ipv6_mode)
        self.ipv6.setCurrentIndex(max(0, index))
        form.addRow('IPv6', self.ipv6)

        self.extra_options = QPlainTextEdit(settings.aria2_extra_options)
        self.extra_options.setPlaceholderText('One aria2 option per line, e.g.\nfile-allocation=falloc')
        self.extra_options.setMaximumHeight(90)
        form.addRow('Extra aria2 options', self.extra_options)

        tools = []
        for name in ('aria2c', 'ffmpeg', 'deno'):
            path = find_tool(name)
            tools.append(f'{name}: {path if path else "not found"}')
        tools.append(f'data folder: {data_dir()}')
        info = QLabel('\n'.join(tools))
        info.setObjectName('Muted')
        info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        info.setWordWrap(True)
        form.addRow('Components', info)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

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

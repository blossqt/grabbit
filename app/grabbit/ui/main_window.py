"""The main window: toolbar, filters, transfer list, details and tray icon."""

import logging
import os
import threading

from PySide6.QtCore import QByteArray, QPoint, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QDialog, QFileDialog,
                               QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
                               QPushButton, QSplitter, QSystemTrayIcon, QToolBar, QTreeView,
                               QVBoxLayout, QWidget)

from .. import APP_NAME, APP_VERSION, updates
from ..paths import data_dir
from ..tasks import KIND_MEDIA, RUNNING_STATES, State
from ..torrentmeta import parse_torrent
from ..util import extract_links, human_speed, open_path, reveal_in_explorer
from . import icons, theme
from .add_dialog import AddDialog
from .details import DetailsPanel
from .settings_dialog import SettingsDialog
from .speedgraph import SpeedGraph
from .torrent_dialog import TorrentFilesDialog
from .transfer_model import (COL_NAME, COL_PROGRESS, ProgressDelegate, TASK_ID_ROLE,
                             TransferFilter, TransferModel)

log = logging.getLogger(__name__)

STATUS_FILTERS = [
    ('all', 'All', 'queue'),
    ('downloading', 'Downloading', 'down'),
    ('seeding', 'Seeding', 'seed'),
    ('completed', 'Completed', 'check'),
    ('paused', 'Paused', 'pause'),
    ('active', 'Active', 'play'),
    ('inactive', 'Inactive', 'pause'),
    ('error', 'Errored', 'error'),
]

KIND_FILTERS = [
    ('all', 'Everything', 'grid'),
    ('torrent', 'Torrents', 'magnet'),
    ('video', 'Videos', 'video'),
    ('image', 'Photos', 'image'),
    ('file', 'Files', 'file'),
]


class MainWindow(QMainWindow):
    links_dropped = Signal(list)
    # An update check's answer, delivered back on the interface thread:
    # (updates.Check, whether someone asked for it).
    update_checked = Signal(object, bool)

    def __init__(self, engine, settings):
        super().__init__()
        self.engine = engine
        self.settings = settings
        self._clipboard_seen = ''
        self._pending_magnets: list = []

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(icons.app_icon())
        self.resize(1180, 720)
        self.setAcceptDrops(True)

        self._build_toolbar()
        self._build_body()
        self._build_status_bar()
        self._build_tray()
        self._connect_engine()
        self._restore_layout()

        self._ui_timer = QTimer(self)
        self._ui_timer.setInterval(1000)
        self._ui_timer.timeout.connect(self._tick)
        self._ui_timer.start()

        # Updates: a minute after starting, then twice a day.
        self._update_answer = None          # the last check that found one
        self._update_announced = ''         # the version the tray last mentioned
        self._update_checking = False
        self.update_checked.connect(self._on_update_checked)
        QTimer.singleShot(updates.FIRST_CHECK_DELAY * 1000, lambda: self.check_for_updates(False))
        self._update_timer = QTimer(self)
        self._update_timer.setInterval(updates.CHECK_INTERVAL * 1000)
        self._update_timer.timeout.connect(lambda: self.check_for_updates(False))
        self._update_timer.start()

        QGuiApplication.clipboard().dataChanged.connect(self._on_clipboard)
        self._update_actions()

    # ---------------------------------------------------------------- layout
    def _build_toolbar(self):
        bar = QToolBar('Main')
        bar.setIconSize(QSize(19, 19))
        bar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        bar.setMovable(False)
        self.addToolBar(bar)

        self.action_add = QAction(icons.icon('add', icons.theme_color('accent')), 'Add link', self)
        self.action_add.setShortcut(QKeySequence('Ctrl+N'))
        self.action_add.triggered.connect(self.open_add_dialog)
        bar.addAction(self.action_add)

        self.action_add_file = QAction(icons.icon('file'), 'Add torrent file', self)
        self.action_add_file.setShortcut(QKeySequence('Ctrl+O'))
        self.action_add_file.triggered.connect(self.open_torrent_file)
        bar.addAction(self.action_add_file)
        bar.addSeparator()

        self.action_resume = QAction(icons.icon('play'), 'Resume', self)
        self.action_resume.triggered.connect(lambda: self.engine.resume(self._selected_ids()))
        bar.addAction(self.action_resume)

        self.action_pause = QAction(icons.icon('pause'), 'Pause', self)
        self.action_pause.triggered.connect(lambda: self.engine.pause(self._selected_ids()))
        bar.addAction(self.action_pause)

        self.action_remove = QAction(icons.icon('trash'), 'Remove', self)
        self.action_remove.setShortcut(QKeySequence.Delete)
        self.action_remove.triggered.connect(lambda: self.remove_selected(False))
        bar.addAction(self.action_remove)
        bar.addSeparator()

        self.action_open_folder = QAction(icons.icon('folder'), 'Open folder', self)
        self.action_open_folder.triggered.connect(self.open_containing_folder)
        bar.addAction(self.action_open_folder)

        spacer = QWidget()
        spacer.setSizePolicy(spacer.sizePolicy().horizontalPolicy().Expanding,
                             spacer.sizePolicy().verticalPolicy().Preferred)
        bar.addWidget(spacer)

        self.search = QLineEdit()
        self.search.setPlaceholderText('Filter downloads…')
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(260)
        self.search.addAction(icons.icon('search', icons.theme_color('dim'), 16),
                              QLineEdit.LeadingPosition)
        self.search.textChanged.connect(lambda text: self.proxy.set_search(text))
        bar.addWidget(self.search)

        self.action_settings = QAction(icons.icon('settings'), 'Options', self)
        self.action_settings.setShortcut(QKeySequence('Ctrl+,'))
        self.action_settings.triggered.connect(self.open_settings)
        bar.addAction(self.action_settings)

        self._build_menus()

    def _build_menus(self):
        file_menu = self.menuBar().addMenu('&File')
        file_menu.addAction(self.action_add)
        file_menu.addAction(self.action_add_file)
        file_menu.addSeparator()
        quit_action = QAction('E&xit', self)
        quit_action.setShortcut(QKeySequence('Ctrl+Q'))
        quit_action.triggered.connect(self.quit_app)
        file_menu.addAction(quit_action)

        edit_menu = self.menuBar().addMenu('&Edit')
        edit_menu.addAction(self.action_resume)
        edit_menu.addAction(self.action_pause)
        edit_menu.addAction(self.action_remove)
        remove_files = QAction('Remove and delete files…', self)
        remove_files.setShortcut(QKeySequence('Shift+Del'))
        remove_files.triggered.connect(lambda: self.remove_selected(True))
        edit_menu.addAction(remove_files)
        edit_menu.addSeparator()
        select_all = QAction('Select all', self)
        select_all.setShortcut(QKeySequence.SelectAll)
        select_all.triggered.connect(lambda: self.view.selectAll())
        edit_menu.addAction(select_all)

        view_menu = self.menuBar().addMenu('&View')
        self.action_graph = QAction('Speed graph', self)
        self.action_graph.setCheckable(True)
        self.action_graph.setShortcut(QKeySequence('Ctrl+G'))
        self.action_graph.toggled.connect(self.show_graph)
        view_menu.addAction(self.action_graph)

        tools_menu = self.menuBar().addMenu('&Tools')
        tools_menu.addAction(self.action_settings)
        open_data = QAction('Open data folder', self)
        open_data.triggered.connect(lambda: open_path(str(data_dir())))
        tools_menu.addAction(open_data)

        help_menu = self.menuBar().addMenu('&Help')
        self.action_check_updates = QAction('Check for updates…', self)
        self.action_check_updates.triggered.connect(lambda: self.check_for_updates(True))
        help_menu.addAction(self.action_check_updates)
        about = QAction(f'About {APP_NAME}', self)
        about.triggered.connect(self.show_about)
        help_menu.addAction(about)

    def _build_body(self):
        self.splitter = QSplitter(Qt.Horizontal)

        self.sidebar = QListWidget()
        self.sidebar.setObjectName('Sidebar')
        self.sidebar.setFixedWidth(196)
        self.sidebar.setFrameShape(QListWidget.NoFrame)
        self._fill_sidebar()
        self.sidebar.currentItemChanged.connect(self._on_filter_changed)
        self.sidebar.itemClicked.connect(self._on_sidebar_clicked)
        self.splitter.addWidget(self.sidebar)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)

        self.banner = QWidget()
        self.banner.setObjectName('Banner')
        banner_layout = QHBoxLayout(self.banner)
        banner_layout.setContentsMargins(10, 6, 8, 6)
        self.banner_label = QLabel()
        self.banner_label.setTextFormat(Qt.PlainText)
        banner_layout.addWidget(QLabel(pixmap=icons.icon('clipboard', icons.theme_color('accent'), 18).pixmap(18, 18)))
        banner_layout.addWidget(self.banner_label, 1)
        banner_add = QPushButton('Download')
        banner_add.clicked.connect(self._banner_accept)
        banner_dismiss = QPushButton('Dismiss')
        banner_dismiss.clicked.connect(lambda: self.banner.hide())
        banner_layout.addWidget(banner_add)
        banner_layout.addWidget(banner_dismiss)
        self.banner.hide()
        right_layout.addWidget(self.banner)

        # A newer Grabbit, when a check finds one. The same shape as the
        # clipboard banner above, so it reads as part of the window.
        self.update_banner = QWidget()
        self.update_banner.setObjectName('Banner')
        update_layout = QHBoxLayout(self.update_banner)
        update_layout.setContentsMargins(10, 6, 8, 6)
        update_layout.addWidget(QLabel(pixmap=icons.icon('refresh', icons.theme_color('accent'), 18).pixmap(18, 18)))
        self.update_label = QLabel()
        self.update_label.setTextFormat(Qt.PlainText)
        update_layout.addWidget(self.update_label, 1)
        update_get = QPushButton('Download')
        update_get.clicked.connect(self._download_update)
        update_notes = QPushButton('What’s new')
        update_notes.clicked.connect(self._open_release_page)
        update_later = QPushButton('Later')
        update_later.clicked.connect(lambda: self.update_banner.hide())
        for button in (update_get, update_notes, update_later):
            update_layout.addWidget(button)
        self.update_banner.hide()
        right_layout.addWidget(self.update_banner)

        self.model = TransferModel(self.engine, self)
        self.proxy = TransferFilter(self)
        self.proxy.setSourceModel(self.model)

        self.view = QTreeView()
        self.view.setModel(self.proxy)
        self.view.setRootIsDecorated(False)
        self.view.setUniformRowHeights(True)
        self.view.setAlternatingRowColors(True)
        self.view.setSortingEnabled(True)
        self.view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._show_context_menu)
        self.view.doubleClicked.connect(self._on_double_click)
        self.view.setItemDelegateForColumn(COL_PROGRESS, ProgressDelegate(self))
        header = self.view.header()
        header.setSectionsMovable(True)
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setMinimumSectionSize(50)
        header.setContextMenuPolicy(Qt.CustomContextMenu)
        header.customContextMenuRequested.connect(self._show_header_menu)
        for column, width in ((COL_NAME, 320), (1, 80), (2, 100), (3, 150), (4, 95), (5, 95),
                              (6, 85), (7, 70), (8, 55), (9, 120), (10, 200)):
            self.view.setColumnWidth(column, width)
        self.view.header().setSectionHidden(10, True)   # save path: on demand
        self.view.sortByColumn(9, Qt.DescendingOrder)
        right_layout.addWidget(self.view, 1)

        self.details = DetailsPanel(self.engine, self)

        # The graph is a pane of its own between the list and the details, so
        # dragging either handle resizes it. It starts hidden; the sidebar and
        # the View menu turn it on.
        self.graph = SpeedGraph(store=self.engine.store)
        self.graph.hide()

        self.vertical_splitter = QSplitter(Qt.Vertical)
        self.vertical_splitter.addWidget(right)
        self.vertical_splitter.addWidget(self.graph)
        self.vertical_splitter.addWidget(self.details)
        self.vertical_splitter.setStretchFactor(0, 3)
        self.vertical_splitter.setStretchFactor(1, 2)
        self.vertical_splitter.setStretchFactor(2, 1)
        self.splitter.addWidget(self.vertical_splitter)
        self.splitter.setStretchFactor(1, 1)
        self.setCentralWidget(self.splitter)

        self.view.selectionModel().selectionChanged.connect(self._on_selection_changed)

    def _fill_sidebar(self):
        def header(text):
            item = QListWidgetItem(text)
            item.setFlags(Qt.NoItemFlags)
            item.setData(Qt.UserRole, None)
            font = item.font()
            font.setPointSizeF(max(7.5, font.pointSizeF() - 1))
            font.setBold(True)
            item.setFont(font)
            item.setForeground(icons.theme_color('dim'))
            self.sidebar.addItem(item)

        header('STATUS')
        for key, label, icon_name in STATUS_FILTERS:
            item = QListWidgetItem(icons.icon(icon_name, icons.theme_color('dim'), 16), label)
            item.setData(Qt.UserRole, ('status', key))
            self.sidebar.addItem(item)
        header('TYPE')
        for key, label, icon_name in KIND_FILTERS:
            item = QListWidgetItem(icons.icon(icon_name, icons.theme_color('dim'), 16), label)
            item.setData(Qt.UserRole, ('kind', key))
            self.sidebar.addItem(item)

        header('VIEW')
        # Not selectable: clicking it turns the graph on and off rather than
        # changing what the list is filtered to.
        self.graph_item = QListWidgetItem(
            icons.icon('chart', icons.theme_color('dim'), 16), 'Speed graph')
        self.graph_item.setData(Qt.UserRole, ('view', 'graph'))
        self.graph_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
        self.graph_item.setCheckState(Qt.Unchecked)
        self.sidebar.addItem(self.graph_item)

        self.sidebar.setCurrentRow(1)

    def _build_status_bar(self):
        bar = self.statusBar()
        self.engine_label = QLabel()
        self.count_label = QLabel()
        self.down_label = QLabel()
        self.up_label = QLabel()
        for widget in (self.down_label, self.up_label):
            widget.setCursor(Qt.PointingHandCursor)
            widget.setToolTip('Click to set a speed limit')
        self.down_label.mousePressEvent = lambda event: self._ask_limit('download')
        self.up_label.mousePressEvent = lambda event: self._ask_limit('upload')
        bar.addWidget(self.engine_label)
        bar.addPermanentWidget(self.count_label)
        bar.addPermanentWidget(self.down_label)
        bar.addPermanentWidget(self.up_label)

    def _build_tray(self):
        self.tray = QSystemTrayIcon(icons.app_icon(), self)
        self.tray.setToolTip(APP_NAME)
        menu = QMenu()
        show_action = menu.addAction('Show Grabbit')
        show_action.triggered.connect(self._restore_window)
        add_action = menu.addAction('Add link…')
        add_action.triggered.connect(self.open_add_dialog)
        menu.addSeparator()
        quit_action = menu.addAction('Exit')
        quit_action.triggered.connect(self.quit_app)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self._restore_window() if reason == QSystemTrayIcon.Trigger else None)
        self.tray.show()

    # ---------------------------------------------------------------- engine
    def _connect_engine(self):
        self.engine.task_added.connect(self.model.add_task)
        self.engine.task_removed.connect(self.model.remove_task)
        self.engine.tasks_updated.connect(self._on_tasks_updated)
        self.engine.task_finished.connect(self._on_task_finished)
        self.engine.magnet_ready.connect(self._on_magnet_ready)
        self.engine.global_stats.connect(self._on_stats)
        self.engine.engine_message.connect(self._on_engine_message)

    def _on_tasks_updated(self, task_ids):
        self.model.update_tasks(task_ids)
        if self.details.task_id in task_ids:
            self.details.refresh()
        self._update_actions()

    def _on_stats(self, stats):
        self.graph.push(stats.get('download_speed') or 0, stats.get('upload_speed') or 0)
        down = human_speed(stats.get('download_speed')) or '0 B/s'
        up = human_speed(stats.get('upload_speed')) or '0 B/s'
        limit_down = f' / {self.settings.download_limit_kib} KiB/s' if self.settings.download_limit_kib else ''
        limit_up = f' / {self.settings.upload_limit_kib} KiB/s' if self.settings.upload_limit_kib else ''
        self.down_label.setText(f'  ↓ {down}{limit_down}  ')
        self.up_label.setText(f'  ↑ {up}{limit_up}  ')
        self.tray.setToolTip(f'{APP_NAME}\n↓ {down}   ↑ {up}')

    def _on_engine_message(self, level: str, message: str):
        if level == 'error':
            QMessageBox.critical(self, APP_NAME, message)
        elif level == 'warning':
            self.statusBar().showMessage(message, 6000)
        else:
            self.statusBar().showMessage(message, 4000)

    def _on_task_finished(self, task_id: str):
        task = self.engine.store.get(task_id)
        if not task or not self.settings.notify_on_complete:
            return
        if task.state == State.SEEDING:
            body = 'Download finished, now seeding'
        else:
            body = 'Download finished'
        self.tray.showMessage(task.name or APP_NAME, body, icons.app_icon(), 5000)

    def _on_magnet_ready(self, task_id: str):
        task = self.engine.store.get(task_id)
        if not task or not task.torrent_file:
            return
        if not self.settings.show_torrent_dialog:
            self.engine.start_torrent_from_metadata(task, None, task.save_dir)
            return
        self._pending_magnets.append(task_id)
        QTimer.singleShot(0, self._drain_magnets)

    def _drain_magnets(self):
        while self._pending_magnets:
            task_id = self._pending_magnets.pop(0)
            task = self.engine.store.get(task_id)
            if not task or not task.torrent_file or not os.path.exists(task.torrent_file):
                continue
            try:
                meta = parse_torrent(open(task.torrent_file, 'rb').read())
            except Exception as exc:
                QMessageBox.warning(self, APP_NAME, f'Could not read the torrent metadata: {exc}')
                continue
            dialog = TorrentFilesDialog(meta, None, task.save_dir or self.settings.download_dir,
                                        self.settings.recent_dirs, parent=self)
            if dialog.exec() == QDialog.Accepted:
                folder = dialog.save_dir() or task.save_dir
                self.settings.remember_dir(folder)
                self.settings.save()
                self.engine.start_torrent_from_metadata(task, dialog.selected_indexes(), folder)
            else:
                self.engine.pause([task_id])

    # ----------------------------------------------------------- user actions
    def open_add_dialog(self, text: str = ''):
        clipboard_text = ''
        if not text:
            clipboard = QGuiApplication.clipboard().text()
            if extract_links(clipboard or ''):
                clipboard_text = clipboard.strip()
        dialog = AddDialog(self.settings, self, text or clipboard_text)
        if dialog.exec() == QDialog.Accepted:
            self._run_requests(dialog.requests, dialog.save_dir, dialog.start_now)
            self.settings.remember_dir(dialog.save_dir)
            self.settings.save()
        self.banner.hide()

    def open_torrent_file(self):
        paths, _ = QFileDialog.getOpenFileNames(self, 'Open torrent files', self.settings.download_dir,
                                                'Torrents (*.torrent);;All files (*)')
        if not paths:
            return
        dialog = AddDialog(self.settings, self)
        for path in paths:
            dialog.add_torrent_file(path)
        if dialog.exec() == QDialog.Accepted:
            self._run_requests(dialog.requests, dialog.save_dir, dialog.start_now)

    def _run_requests(self, requests, save_dir: str, start: bool):
        added = 0
        for request in requests:
            kind = request.get('type')
            try:
                if kind == 'media':
                    self.engine.add_media(request['item'], request['probe'], request['quality'],
                                          request['container'], save_dir, start)
                elif kind == 'image':
                    self.engine.add_image(request['item'], request['probe'], save_dir, start)
                elif kind == 'magnet':
                    self.engine.add_magnet(request['url'], save_dir, start)
                elif kind == 'torrent':
                    self.engine.add_torrent(request['data'], save_dir, request.get('selected'),
                                            start, request.get('source', ''))
                elif kind == 'file':
                    self.engine.add_direct(request['url'], save_dir, request.get('name', ''),
                                           start=start, mirrors=request.get('urls'),
                                           checksum=request.get('checksum', ''))
                added += 1
            except Exception as exc:
                log.exception('could not add download')
                QMessageBox.warning(self, APP_NAME, f'Could not add that download: {exc}')
        if added:
            self.statusBar().showMessage(f'Added {added} download(s)', 4000)

    def remove_selected(self, delete_files: bool):
        ids = self._selected_ids()
        if not ids:
            return
        if self.settings.confirm_remove or delete_files:
            names = [self.engine.store.get(i).name or '(unnamed)' for i in ids[:5]
                     if self.engine.store.get(i)]
            listing = '\n'.join(f'• {n}' for n in names)
            if len(ids) > 5:
                listing += f'\n• …and {len(ids) - 5} more'
            box = QMessageBox(self)
            box.setWindowTitle('Remove downloads')
            box.setIcon(QMessageBox.Question)
            box.setText(f'Remove {len(ids)} download(s) from the list?')
            box.setInformativeText(listing)
            delete_check = QCheckBox('Also move the downloaded files to the Recycle Bin')
            delete_check.setChecked(delete_files)
            box.setCheckBox(delete_check)
            box.setStandardButtons(QMessageBox.Yes | QMessageBox.Cancel)
            box.setDefaultButton(QMessageBox.Cancel)
            if box.exec() != QMessageBox.Yes:
                return
            delete_files = delete_check.isChecked()
        self.engine.remove(ids, delete_files)

    def watch_task(self, task):
        """Play the finished file, or stream the source if it isn't downloaded yet."""
        from . import preview
        if task.file_path and os.path.exists(task.file_path):
            preview.play_file(task.file_path, self)
        else:
            preview.play_link(task.source, self.settings, self)

    def open_containing_folder(self):
        for task_id in self._selected_ids()[:5]:
            task = self.engine.store.get(task_id)
            if not task:
                continue
            target = task.file_path or os.path.join(task.save_dir, task.out or task.name or '')
            if not reveal_in_explorer(target):
                open_path(task.save_dir)

    def open_settings(self):
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() != QDialog.Accepted:
            return
        theme.apply_theme(QApplication.instance(), self.settings.theme)
        icons.clear_cache()
        self.engine.apply_settings()
        if dialog.restart_needed:
            QMessageBox.information(
                self, APP_NAME,
                'Some of those settings (BitTorrent, proxy or advanced options) apply the next '
                'time Grabbit starts.')

    def show_about(self):
        aria2 = self.engine.aria2_version() or 'not running'
        try:
            from yt_dlp.version import __version__ as ytdlp_version
        except Exception:
            ytdlp_version = 'unavailable'
        QMessageBox.about(
            self, f'About {APP_NAME}',
            f'<h3>{APP_NAME} {APP_VERSION}</h3>'
            f'<p>A paste-a-link downloader.</p>'
            f'<p><b>Download engine:</b> aria2 {aria2}, compiled from source<br>'
            f'<b>Site support:</b> yt-dlp {ytdlp_version}<br>'
            f'<b>Media tools:</b> FFmpeg, Deno</p>'
            f'<p style="color:gray">aria2 is GPLv2+; yt-dlp is Unlicense; FFmpeg here is a GPL build.</p>')

    # --------------------------------------------------------------- updates
    def check_for_updates(self, manual: bool = True):
        """Ask GitHub whether a newer Grabbit is out, off the interface thread.

        The automatic checks respect the setting and say nothing unless there
        is something to offer; asking from the Help menu always answers.
        """
        if not manual and not self.settings.check_for_updates:
            return
        if self._update_checking:
            return
        self._update_checking = True
        if manual:
            self.statusBar().showMessage('Checking for updates…')

        def ask():
            self.update_checked.emit(updates.check('windows'), manual)

        threading.Thread(target=ask, name='grabbit-update-check', daemon=True).start()

    def _on_update_checked(self, answer, manual: bool):
        self._update_checking = False
        if manual:
            self.statusBar().clearMessage()
        if answer.available:
            self._update_answer = answer
            self.update_label.setText(f'{APP_NAME} {answer.latest} is out - you have {APP_VERSION}.')
            self.update_banner.show()
            # Unprompted, and with the window out of sight, the banner would go
            # unseen - so say it once in the notification area as well.
            hidden = not self.isVisible() or self.isMinimized()
            if not manual and hidden and answer.latest != self._update_announced:
                self._update_announced = answer.latest
                self.tray.showMessage(f'{APP_NAME} {answer.latest} is out',
                                      'Open Grabbit to download it.', icons.app_icon(), 8000)
            return
        if not manual:
            return      # up to date, or offline: nothing worth interrupting for
        if answer.error:
            QMessageBox.warning(self, 'Check for updates',
                                f'Couldn’t check for updates.\n\n{answer.error}')
        else:
            QMessageBox.information(self, 'Check for updates',
                                    f'This is the newest version: {APP_NAME} {APP_VERSION}.')

    def _download_update(self):
        """Fetch the new zip in the browser. Grabbit runs from a folder of
        files it cannot replace while it is using them, so installing stays a
        matter of unzipping the new one."""
        answer = self._update_answer
        if answer is None or answer.asset is None:
            return
        QDesktopServices.openUrl(QUrl(answer.asset.url))
        self.update_banner.hide()
        self.statusBar().showMessage(f'Downloading {answer.asset.name} in your browser…', 8000)

    def _open_release_page(self):
        answer = self._update_answer
        QDesktopServices.openUrl(QUrl(answer.release.page if answer else updates.RELEASES_PAGE))

    def _ask_limit(self, which: str):
        current = (self.settings.download_limit_kib if which == 'download'
                   else self.settings.upload_limit_kib)
        value, ok = QInputDialog.getInt(
            self, f'{which.title()} limit', f'{which.title()} speed limit in KiB/s (0 = unlimited):',
            current, 0, 10_000_000, 64)
        if ok:
            if which == 'download':
                self.engine.set_speed_limits(value, self.settings.upload_limit_kib)
            else:
                self.engine.set_speed_limits(self.settings.download_limit_kib, value)
            self._on_stats(self.engine.last_stats)

    # ------------------------------------------------------------- list glue
    def _selected_ids(self) -> list:
        ids = []
        for index in self.view.selectionModel().selectedRows():
            task_id = index.data(TASK_ID_ROLE)
            if task_id:
                ids.append(task_id)
        return ids

    def _on_selection_changed(self):
        ids = self._selected_ids()
        self.details.set_task(ids[0] if ids else '')
        self.graph.select(ids[:1] or None)
        self._update_actions()

    def _on_double_click(self, index):
        task = self.engine.store.get(index.data(TASK_ID_ROLE))
        if not task:
            return
        if task.state == State.COMPLETED:
            target = task.file_path or os.path.join(task.save_dir, task.out or task.name or '')
            if not open_path(target):
                open_path(task.save_dir)
        elif task.state == State.PAUSED:
            self.engine.resume([task.id])
        else:
            self.engine.pause([task.id])

    def _update_actions(self):
        tasks = [self.engine.store.get(i) for i in self._selected_ids()]
        tasks = [t for t in tasks if t]
        self.action_pause.setEnabled(any(t.state in RUNNING_STATES or t.state == State.QUEUED
                                         for t in tasks))
        self.action_resume.setEnabled(any(t.state in (State.PAUSED, State.ERROR) for t in tasks))
        self.action_remove.setEnabled(bool(tasks))
        self.action_open_folder.setEnabled(bool(tasks))

    def _show_context_menu(self, position: QPoint):
        ids = self._selected_ids()
        if not ids:
            return
        task = self.engine.store.get(ids[0])
        menu = QMenu(self)
        menu.addAction(self.action_resume)
        menu.addAction(self.action_pause)
        menu.addSeparator()
        if task and task.kind == KIND_MEDIA:
            menu.addAction(icons.icon('play'), 'Watch', lambda: self.watch_task(task))
        if task and task.state == State.COMPLETED:
            menu.addAction('Open file', lambda: self._on_double_click(
                self.view.selectionModel().selectedRows()[0]))
        menu.addAction('Open containing folder', self.open_containing_folder)
        menu.addSeparator()
        menu.addAction('Copy link', lambda: QGuiApplication.clipboard().setText(
            '\n'.join(self.engine.store.get(i).source for i in ids if self.engine.store.get(i))))
        if task and task.is_torrent and task.info_hash:
            menu.addAction('Copy magnet link', lambda: QGuiApplication.clipboard().setText(
                f'magnet:?xt=urn:btih:{task.info_hash}'))
        menu.addSeparator()
        menu.addAction(self.action_remove)
        menu.addAction('Remove and delete files…', lambda: self.remove_selected(True))
        menu.exec(self.view.viewport().mapToGlobal(position))

    def _show_header_menu(self, position: QPoint):
        from .transfer_model import HEADERS
        menu = QMenu(self)
        header = self.view.header()
        for column, label in enumerate(HEADERS):
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(not header.isSectionHidden(column))
            action.setEnabled(column != COL_NAME)
            action.toggled.connect(lambda checked, c=column: header.setSectionHidden(c, not checked))
        menu.exec(header.mapToGlobal(position))

    def _on_sidebar_clicked(self, item):
        role = item.data(Qt.UserRole)
        if role and role[0] == 'view':
            self.show_graph(item.checkState() != Qt.Checked)

    def show_graph(self, visible: bool):
        """Turn the speed graph on or off, keeping the sidebar and menu honest."""
        visible = bool(visible)
        self.graph.setVisible(visible)
        self.graph_item.setCheckState(Qt.Checked if visible else Qt.Unchecked)
        if self.action_graph.isChecked() != visible:
            self.action_graph.setChecked(visible)
        self.settings.show_graph = visible

        if visible:
            sizes = self.vertical_splitter.sizes()
            if sizes[1] < 80:
                # First time, or after being hidden: about half the window, as
                # a starting point. Once it has been dragged, that height is
                # what comes back.
                total = max(360, self.vertical_splitter.height())
                details = min(sizes[2] or int(total * 0.22), int(total * 0.35))
                graph = int(total * 0.45)
                self.vertical_splitter.setSizes(
                    [max(120, total - graph - details), graph, details])
            self.graph.select(self._selected_ids()[:1] or None)
            self.graph.push_tasks(self.engine.store)

    def _on_filter_changed(self, current, previous):
        if current is None:
            return
        data = current.data(Qt.UserRole)
        if not data:
            return
        which, key = data
        if which == 'status':
            self.proxy.set_status(key)
        else:
            self.proxy.set_kind(key)

    def _tick(self):
        """Refresh derived UI once a second."""
        counts = {}
        for task in self.engine.store:
            counts[task.state] = counts.get(task.state, 0) + 1
        total = len(self.engine.store)
        running = sum(counts.get(state, 0) for state in RUNNING_STATES)
        self.count_label.setText(f'  {total} download(s), {running} active  ')
        self.engine_label.setText(
            f'  aria2 {self.engine.aria2_version()} · ready  ' if self.engine.running
            else '  engine stopped  ')
        for row in range(self.sidebar.count()):
            item = self.sidebar.item(row)
            data = item.data(Qt.UserRole)
            if not data:
                continue
            which, key = data
            if which != 'status':
                continue
            if key == 'all':
                count = total
            elif key == 'downloading':
                count = sum(counts.get(s, 0) for s in (State.DOWNLOADING, State.QUEUED,
                                                       State.METADATA, State.EXTRACTING,
                                                       State.PROCESSING))
            elif key == 'seeding':
                count = counts.get(State.SEEDING, 0)
            elif key == 'completed':
                count = counts.get(State.COMPLETED, 0)
            elif key == 'paused':
                count = counts.get(State.PAUSED, 0)
            elif key == 'error':
                count = counts.get(State.ERROR, 0)
            elif key == 'active':
                count = sum(1 for t in self.engine.store if t.down_speed or t.up_speed)
            else:
                count = sum(1 for t in self.engine.store if not (t.down_speed or t.up_speed))
            label = dict((k, l) for k, l, _ in STATUS_FILTERS)[key]
            item.setText(f'{label}  ({count})' if count else label)
        if self.graph.isVisible():
            self.graph.push_tasks(self.engine.store)
        if self.details.task_id:
            self.details.refresh()

    # -------------------------------------------------------- clipboard/drops
    def _on_clipboard(self):
        if not self.settings.watch_clipboard:
            return
        text = (QGuiApplication.clipboard().text() or '').strip()
        if not text or text == self._clipboard_seen or len(text) > 4000:
            return
        links = extract_links(text)
        if not links:
            return
        self._clipboard_seen = text
        if any(self.engine.store.find_source(link) for link in links):
            return
        self._banner_links = links
        first = links[0]
        extra = f' and {len(links) - 1} more' if len(links) > 1 else ''
        self.banner_label.setText(f'Link copied: {first[:90]}{extra}')
        self.banner.show()
        if not self.isVisible() or self.isMinimized():
            self.tray.showMessage(APP_NAME, 'Copied link ready to download—click to open',
                                  icons.app_icon(), 4000)

    def _banner_accept(self):
        self.banner.hide()
        self.open_add_dialog('\n'.join(getattr(self, '_banner_links', [])))

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() or event.mimeData().hasText():
            event.acceptProposedAction()

    def dropEvent(self, event):
        mime = event.mimeData()
        torrents, links = [], []
        for url in mime.urls():
            if url.isLocalFile():
                path = url.toLocalFile()
                if path.lower().endswith('.torrent'):
                    torrents.append(path)
            else:
                links.append(url.toString())
        if mime.hasText():
            links.extend(extract_links(mime.text()))
        if torrents:
            dialog = AddDialog(self.settings, self)
            for path in torrents:
                dialog.add_torrent_file(path)
            if links:
                dialog.input.setPlainText('\n'.join(links))
            if dialog.exec() == QDialog.Accepted:
                self._run_requests(dialog.requests, dialog.save_dir, dialog.start_now)
        elif links:
            self.open_add_dialog('\n'.join(dict.fromkeys(links)))
        event.acceptProposedAction()

    def handle_links(self, links):
        """Links passed on the command line or from a second instance."""
        if not links:
            self._restore_window()
            return
        magnets = [link for link in links if link.lower().startswith('magnet:')]
        torrents = [link for link in links if link.lower().endswith('.torrent') and os.path.isfile(link)]
        others = [link for link in links if link not in magnets and link not in torrents]
        self._restore_window()
        if torrents:
            dialog = AddDialog(self.settings, self)
            for path in torrents:
                dialog.add_torrent_file(path)
            if dialog.exec() == QDialog.Accepted:
                self._run_requests(dialog.requests, dialog.save_dir, dialog.start_now)
        if magnets or others:
            self.open_add_dialog('\n'.join(magnets + others))

    # ------------------------------------------------------------ window life
    def _restore_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _restore_layout(self):
        for value, target in ((self.settings.window_geometry, self.restoreGeometry),
                              (self.settings.window_state, self.restoreState)):
            if value:
                try:
                    target(QByteArray.fromBase64(value.encode('ascii')))
                except Exception:
                    pass
        if self.settings.header_state:
            try:
                self.view.header().restoreState(
                    QByteArray.fromBase64(self.settings.header_state.encode('ascii')))
            except Exception:
                pass
        for value, splitter in ((self.settings.splitter_state, self.splitter),
                                (self.settings.details_splitter_state, self.vertical_splitter)):
            if value:
                try:
                    splitter.restoreState(QByteArray.fromBase64(value.encode('ascii')))
                except Exception:
                    pass
        # After the splitter, so a remembered height survives being shown.
        if self.settings.show_graph:
            self.show_graph(True)

    def _save_layout(self):
        def encode(data: QByteArray) -> str:
            return bytes(data.toBase64()).decode('ascii')

        self.settings.window_geometry = encode(self.saveGeometry())
        self.settings.window_state = encode(self.saveState())
        self.settings.header_state = encode(self.view.header().saveState())
        self.settings.splitter_state = encode(self.splitter.saveState())
        self.settings.details_splitter_state = encode(self.vertical_splitter.saveState())
        self.settings.save()

    def quit_app(self):
        self._closing = True
        self.close()
        QApplication.instance().quit()

    def closeEvent(self, event):
        if self.settings.close_to_tray and not getattr(self, '_closing', False):
            event.ignore()
            self.hide()
            self.tray.showMessage(APP_NAME, 'Still running in the notification area',
                                  icons.app_icon(), 3000)
            return
        # Get off the screen first: everything below is bookkeeping, and a
        # window that lingers while it happens just looks stuck.
        self.hide()
        self.tray.hide()
        QApplication.processEvents()

        self._save_layout()
        self.engine.shutdown()
        from .. import streamserver
        streamserver.server().stop()
        event.accept()
        # The app keeps running with no windows open (so the tray option works),
        # so closing for real has to end it explicitly.
        QApplication.instance().quit()

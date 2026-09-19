"""Checks the desktop window's interface, without a screen or a network.

selftest.py exercises the engine against real downloads; this exercises the
window around it - the filters, the speed graph, the details tabs - with a few
invented tasks and Qt's offscreen platform, so it runs anywhere in seconds.

    %LOCALAPPDATA%\\GrabbitBuild\\venv\\Scripts\\python.exe build\\ui_check.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'app'))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from grabbit import paths as _paths                # noqa: E402

# Somewhere of its own: reading the real task list would make the counts below
# depend on whatever happens to be in it, and writing to it would leave
# invented downloads in the app.
_paths.set_data_dir(tempfile.mkdtemp(prefix='grabbit-ui-check-'))

from PySide6.QtCore import Qt                      # noqa: E402
from PySide6.QtWidgets import QApplication         # noqa: E402

from grabbit.engine import Engine                  # noqa: E402
from grabbit.settings import Settings              # noqa: E402
from grabbit.tasks import KIND_MEDIA, KIND_TORRENT, State, Task  # noqa: E402
from grabbit.ui import theme                       # noqa: E402

results = []


def report(name, ok, detail=''):
    results.append((name, ok))
    print(f'  [{"PASS" if ok else "FAIL"}] {name}' + (f' - {detail}' if detail else ''))


def sample(engine):
    folder = os.path.join(os.path.expanduser('~'), 'Downloads', 'Grabbit')
    for task in (
            Task(kind=KIND_MEDIA, source='https://youtu.be/jNQXAC9IVRw',
                 name='Me at the zoo.mp4', save_dir=folder, state=State.DOWNLOADING,
                 total=44_000_000, done=18_500_000, down_speed=3_900_000,
                 connections=8, media={'quality': 'best'}),
            Task(kind=KIND_TORRENT, source='magnet:?xt=urn:btih:abc',
                 name='ubuntu-24.04.iso', save_dir=folder, state=State.SEEDING,
                 total=5_170_000_000, done=5_170_000_000, up_speed=820_000,
                 seeds=4, connections=19, uploaded=2_400_000_000, info_hash='abc'),
            Task(kind=KIND_MEDIA, source='https://tiktok.com/x', name='clip.mp4',
                 save_dir=folder, state=State.COMPLETED, total=2_742_793,
                 done=2_742_793, media={'quality': 'best'})):
        engine.store.add(task)


def main():
    app = QApplication([])
    theme.apply_theme(app, 'dark')

    from grabbit.ui.main_window import MainWindow

    settings = Settings()
    settings.show_graph = False
    engine = Engine(settings)
    sample(engine)
    window = MainWindow(engine, settings)
    window.resize(1180, 760)
    window.model._ids = [t.id for t in engine.store]
    window.model.layoutChanged.emit()
    window.show()
    app.processEvents()

    report('the window builds with downloads in it',
           window.proxy.rowCount() == 3, f'{window.proxy.rowCount()} rows')

    report('the graph starts hidden', not window.graph.isVisible())

    window._on_sidebar_clicked(window.graph_item)
    app.processEvents()
    sizes = window.vertical_splitter.sizes()
    share = sizes[1] / max(1, sum(sizes))
    report('the sidebar entry shows the graph', window.graph.isVisible(),
           f'{window.graph_item.text()} is {"checked" if window.graph_item.checkState() == Qt.Checked else "unchecked"}')
    report('it opens at about half the height', 0.3 < share < 0.6, f'{share:.0%} of the pane')

    window.graph.push(4_000_000, 900_000)
    window.graph.push_tasks(engine.store)
    window.graph.repaint()
    report('the graph draws the totals', window.graph._titles()[0] == 'All downloads',
           window.graph._titles()[1])

    first = next(iter(engine.store))
    window.view.setCurrentIndex(window.proxy.index(0, 0))
    window._on_selection_changed()
    window.graph.repaint()
    caption = window.graph._titles()[0]
    report('selecting a download switches the graph to it',
           caption not in ('All downloads', ''), caption)

    # Its own history, not a copy of the totals.
    history = window.graph._history.get(window.graph._task_id)
    report('each download keeps its own history', history is not None
           and history is not window.graph._history[''],
           f'{len(history.down)} samples' if history else 'none')

    window.details.set_task(first.id)
    for index in range(window.details.count()):
        window.details.setCurrentIndex(index)
        window.details.refresh(force=True)
        app.processEvents()
    report('every details tab refreshes', True,
           ', '.join(window.details.tabText(i) for i in range(window.details.count())))

    for key in ('completed', 'downloading', 'all'):
        window.proxy.set_status(key)
    window.proxy.set_kind('torrent')
    torrents = window.proxy.rowCount()
    window.proxy.set_kind('all')
    report('the filters narrow the list', torrents == 1, f'{torrents} torrent(s)')

    window._on_sidebar_clicked(window.graph_item)
    app.processEvents()
    report('the sidebar entry hides it again', not window.graph.isVisible())

    window._tick()
    report('the once-a-second refresh runs', True, window.count_label.text().strip())

    engine.shutdown()
    failures = [name for name, ok in results if not ok]
    print(f'\n{len(results) - len(failures)}/{len(results)} checks passed')
    for name in failures:
        print(f'  failed: {name}')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication

from iPhoto.gui.ui.controllers.status_bar_controller import StatusBarController
from iPhoto.gui.ui.widgets.chrome_status_bar import ChromeStatusBar


def _make_controller(qapp: QApplication) -> tuple[StatusBarController, ChromeStatusBar, QAction]:
    status_bar = ChromeStatusBar()
    action = QAction("Rescan", status_bar)
    context = SimpleNamespace(library=SimpleNamespace(deleted_directory=lambda: None))
    controller = StatusBarController(
        status_bar,
        status_bar.progress_bar,
        action,
        context,
    )
    return controller, status_bar, action


def test_load_events_do_not_hide_active_scan_progress(qapp: QApplication) -> None:
    controller, status_bar, _action = _make_controller(qapp)
    progress = status_bar.progress_bar

    controller.begin_scan()
    controller.handle_scan_progress(Path("/library"), 4, 10)

    assert not progress.isHidden()
    assert progress.minimum() == 0
    assert progress.maximum() == 10
    assert progress.value() == 4

    controller.handle_load_started(Path("/library/album-a"))
    controller.handle_load_progress(Path("/library/album-a"), 1, 3)
    controller.handle_load_finished(Path("/library/album-a"), True)

    assert not progress.isHidden()
    assert progress.minimum() == 0
    assert progress.maximum() == 10
    assert progress.value() == 4
    assert status_bar.currentMessage() == "Scanning… (4/10)"


def test_scan_progress_preempts_album_load_context(qapp: QApplication) -> None:
    controller, status_bar, _action = _make_controller(qapp)
    progress = status_bar.progress_bar

    controller.handle_load_started(Path("/library/album-a"))
    assert not progress.isHidden()
    assert status_bar.currentMessage() == "Loading items…"

    controller.handle_scan_progress(Path("/library"), 3, 8)
    controller.handle_load_finished(Path("/library/album-a"), True)

    assert not progress.isHidden()
    assert progress.minimum() == 0
    assert progress.maximum() == 8
    assert progress.value() == 3
    assert status_bar.currentMessage() == "Scanning… (3/8)"


def test_scan_finished_hides_progress_and_restores_rescan_action(qapp: QApplication) -> None:
    controller, status_bar, action = _make_controller(qapp)
    progress = status_bar.progress_bar

    controller.begin_scan()
    assert not action.isEnabled()
    assert not progress.isHidden()

    controller.handle_scan_finished(Path("/library"), True)

    assert progress.isHidden()
    assert progress.minimum() == 0
    assert progress.maximum() == 0
    assert action.isEnabled()
    assert status_bar.currentMessage() == "Scan complete."


def test_album_load_progress_still_hides_when_no_scan_is_active(qapp: QApplication) -> None:
    controller, status_bar, _action = _make_controller(qapp)
    progress = status_bar.progress_bar

    controller.handle_load_started(Path("/library/album-a"))
    controller.handle_load_progress(Path("/library/album-a"), 2, 5)

    assert not progress.isHidden()
    assert progress.minimum() == 0
    assert progress.maximum() == 5
    assert progress.value() == 2
    assert status_bar.currentMessage() == "Loading items… (2/5)"

    controller.handle_load_finished(Path("/library/album-a"), True)

    assert progress.isHidden()
    assert progress.minimum() == 0
    assert progress.maximum() == 0
    assert status_bar.currentMessage() == "Album loaded."

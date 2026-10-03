from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for GUI tests", exc_type=ImportError)
pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets not available", exc_type=ImportError)

from PySide6.QtCore import QRunnable, Qt
from PySide6.QtWidgets import QApplication, QWidget

from iPhoto.gui.ui.controllers.map_extension_download_controller import (
    MapExtensionDownloadController,
)
from iPhoto.gui.ui.tasks.map_extension_download_worker import (
    MapExtensionDownloadResult,
    MapExtensionDownloadSignals,
)


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_controller_temporarily_hides_stays_on_top_child_windows(
    qapp: QApplication, tmp_path: Path
) -> None:
    del qapp
    owner = QWidget()
    owner.show()

    floating = QWidget(
        owner,
        Qt.WindowType.Window | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint,
    )
    floating.show()

    context = SimpleNamespace(settings=SimpleNamespace(get=lambda *_args, **_kwargs: True))
    controller = MapExtensionDownloadController(owner, context, package_root=tmp_path / "maps")

    controller._hide_blocking_top_level_windows()

    assert floating.isHidden()

    controller._restore_temporarily_hidden_windows()

    assert floating.isVisible()
    floating.close()
    owner.close()


def test_restart_failure_restores_hidden_windows(qapp: QApplication, tmp_path: Path) -> None:
    del qapp
    owner = QWidget()
    owner.show()

    floating = QWidget(
        owner,
        Qt.WindowType.Window | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint,
    )
    floating.show()

    context = SimpleNamespace(settings=SimpleNamespace(get=lambda *_args, **_kwargs: True))
    controller = MapExtensionDownloadController(owner, context, package_root=tmp_path / "maps")

    controller._hide_blocking_top_level_windows()
    assert floating.isHidden()

    with (
        patch(
            "iPhoto.gui.ui.controllers.map_extension_download_controller.QProcess.startDetached",
            return_value=False,
        ),
        patch(
            "iPhoto.gui.ui.controllers.map_extension_download_controller.QMessageBox.critical",
            return_value=0,
        ),
    ):
        controller._restart_application()

    assert floating.isVisible()
    floating.close()
    owner.close()


@pytest.fixture
def controller(qapp, tmp_path):
    service = SimpleNamespace(download_url=lambda _: "https://example.invalid/extension.zip")
    context = SimpleNamespace(
        map_extensions=service, settings=SimpleNamespace(get=lambda *a: False)
    )
    owner = QWidget()
    value = MapExtensionDownloadController(owner, context, package_root=tmp_path / "maps")
    yield value
    if value._progress_dialog is not None:
        value._progress_dialog.allow_close()
        value._progress_dialog.close()
    owner.close()


def test_start_download_keeps_worker_until_finished_and_passes_service(controller):
    with patch(
        "iPhoto.gui.ui.controllers.map_extension_download_controller.QThreadPool.globalInstance"
    ) as pool:
        controller.start_download(source="test")
    worker = pool.return_value.start.call_args.args[0]
    assert worker is controller._active_worker
    assert worker._service is controller._context.map_extensions
    assert controller._download_inflight
    worker.signals.finished.emit()
    assert controller._active_worker is None
    assert not controller._download_inflight


def test_shared_preparation_defers_all_callbacks_until_finished(controller):
    events = []
    with patch(
        "iPhoto.gui.ui.controllers.map_extension_download_controller.QThreadPool.globalInstance"
    ) as pool:
        controller.prepare_runtime(lambda: events.append("map"))
        controller.prepare_runtime(lambda: events.append("info"))
    pool.return_value.start.assert_called_once()
    worker = controller._active_worker
    assert worker._request.operation == "prepare"
    assert events == []
    worker.signals.ready.emit(
        MapExtensionDownloadResult(Path("pending"), Path("active"), "missing")
    )
    assert events == []
    worker.signals.finished.emit()
    assert events == ["map", "info"]
    controller.prepare_runtime(lambda: events.append("ready"))
    assert events[-1] == "ready"


def test_pending_result_prompts_restart_without_claiming_installed(controller):
    controller._last_request = SimpleNamespace(operation="install")
    controller._handle_ready(
        MapExtensionDownloadResult(Path("pending"), Path("active"), "pending_restart")
    )
    with patch(
        "iPhoto.gui.ui.controllers.map_extension_download_controller.QMessageBox.question"
    ) as question:
        controller._handle_finished()
    assert "staged" in question.call_args.args[2]
    assert "waiting for restart" in question.call_args.args[2]


def test_local_archive_selection_needs_no_failed_download(controller, tmp_path):
    archive = tmp_path / "官方地图.zip"
    with (
        patch(
            "iPhoto.gui.ui.controllers.map_extension_download_controller.QFileDialog.getOpenFileName",
            return_value=(str(archive), ""),
        ),
        patch.object(controller, "_start") as start,
    ):
        controller.install_from_file()
    assert start.call_args.args[0].local_archive_path == archive
    assert start.call_args.args[0].operation == "install"


def test_direct_connection_is_explicit_and_not_persisted(controller):
    with patch.object(controller, "_start") as start:
        controller.start_download(source="recovery", network_mode="direct")
        assert start.call_args.args[0].network_mode == "direct"
        controller.start_download(source="settings")
        assert start.call_args.args[0].network_mode == "system"


def test_failure_dialog_runs_after_worker_is_released(controller):
    from iPhoto.application.ports.map_extension import MapExtensionError

    controller._last_request = SimpleNamespace(operation="install")
    controller._active_worker = object()
    controller._download_inflight = True
    controller._handle_error(MapExtensionError("refused", "download", code=10061))

    def show(_failure):
        assert controller._active_worker is None
        assert not controller._download_inflight

    with patch.object(controller, "_show_failure", side_effect=show) as dialog:
        controller._handle_finished()
    dialog.assert_called_once()


def test_browser_download_uses_official_package_url(controller):
    with patch(
        "iPhoto.gui.ui.controllers.map_extension_download_controller.QDesktopServices.openUrl"
    ) as open_url:
        controller.open_download_page()
    assert open_url.call_args.args[0].toString() == "https://example.invalid/extension.zip"


def test_nuitka_restart_does_not_pass_executable_as_an_argument(controller, monkeypatch):
    from iPhoto.gui.ui.controllers import map_extension_download_controller as module

    monkeypatch.setattr(module, "__compiled__", object(), raising=False)
    monkeypatch.setattr(module.sys, "frozen", False, raising=False)
    monkeypatch.setattr(module.sys, "argv", ["C:/Program Files/iPhoto/entrypoint.exe", "album"])
    app = SimpleNamespace(applicationFilePath=lambda: "C:/Program Files/iPhoto/entrypoint.exe")
    assert controller._restart_command(app) == ("C:/Program Files/iPhoto/entrypoint.exe", ["album"])

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for GUI tests", exc_type=ImportError)
pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets not available", exc_type=ImportError)

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget

from iPhoto.gui.ui.controllers.map_extension_download_controller import (
    MapExtensionDownloadController,
)
from iPhoto.gui.ui.tasks.map_extension_download_worker import (
    MapExtensionDownloadResult,
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
    service = SimpleNamespace(
        download_url=lambda _: "https://example.invalid/extension.zip",
        supports_local_install=lambda _: True,
    )
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


@pytest.mark.parametrize("operation", ["install", "prepare"])
def test_pending_result_prompts_restart_without_claiming_installed(controller, operation):
    controller._last_request = SimpleNamespace(operation=operation)
    controller._handle_ready(
        MapExtensionDownloadResult(Path("pending"), Path("active"), "pending_restart")
    )
    with patch(
        "iPhoto.gui.ui.controllers.map_extension_download_controller.QMessageBox.question"
    ) as question:
        controller._handle_finished()
    question.assert_called_once()
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


def test_preparation_reuses_normalized_root_but_reprepares_changed_root(controller, tmp_path):
    calls = []
    with patch(
        "iPhoto.gui.ui.controllers.map_extension_download_controller.QThreadPool.globalInstance"
    ) as pool:
        root = controller._package_root
        controller.prepare_runtime(lambda: calls.append("first"))
        controller._active_worker.signals.finished.emit()
        controller.set_package_root(root / ".")
        controller.prepare_runtime(lambda: calls.append("same"))
        assert calls == ["first", "same"]
        assert pool.return_value.start.call_count == 1
        controller.set_package_root(tmp_path / "other")
        controller.prepare_runtime(lambda: calls.append("other"))
        assert calls == ["first", "same"]
        assert pool.return_value.start.call_count == 2
        controller._active_worker.signals.finished.emit()
        assert calls == ["first", "same", "other"]


@pytest.mark.parametrize("return_to_first_root", [False, True])
@pytest.mark.parametrize("failed", [False, True])
def test_old_completion_never_releases_new_root_callbacks(
    controller, tmp_path, return_to_first_root, failed
):
    from iPhoto.application.ports.map_extension import MapExtensionError

    calls = []
    with (
        patch(
            "iPhoto.gui.ui.controllers.map_extension_download_controller.QThreadPool.globalInstance"
        ) as pool,
        patch.object(controller, "_show_failure") as show_error,
    ):
        first_root = controller._package_root
        controller.prepare_runtime(lambda: calls.append("stale"))
        old_worker = controller._active_worker
        controller.set_package_root(tmp_path / "other")
        if return_to_first_root:
            controller.set_package_root(first_root)
        controller.prepare_runtime(lambda: calls.append("current"))
        assert pool.return_value.start.call_count == 1
        if failed:
            old_worker.signals.error.emit(MapExtensionError("permission", "prepare"))
        else:
            old_worker.signals.ready.emit(MapExtensionDownloadResult(Path("pending"), first_root))
        old_worker.signals.finished.emit()
        assert calls == []
        assert not controller._runtime_prepared
        show_error.assert_not_called()
        assert pool.return_value.start.call_count == 2
        new_worker = controller._active_worker
        assert new_worker is not old_worker
        assert new_worker._request.package_root == controller._package_root
        new_worker.signals.finished.emit()
        assert calls == ["current"]
        assert controller._runtime_prepared


def test_callback_root_change_stops_remaining_stale_callbacks(controller, tmp_path):
    calls = []

    def switch_root():
        controller.set_package_root(tmp_path / "other")
        controller.prepare_runtime(lambda: calls.append("new"))

    with patch(
        "iPhoto.gui.ui.controllers.map_extension_download_controller.QThreadPool.globalInstance"
    ):
        controller.prepare_runtime(switch_root)
        controller.prepare_runtime(lambda: calls.append("stale"))
        controller._active_worker.signals.finished.emit()
        assert calls == []
        assert not controller._runtime_prepared
        controller._active_worker.signals.finished.emit()
        assert calls == ["new"]


@pytest.mark.parametrize("operation", ["install", "prepare"])
def test_installed_result_does_not_prompt_restart(controller, operation):
    from iPhoto.gui.ui.tasks.map_extension_download_worker import MapExtensionDownloadRequest

    owner = controller._parent
    owner.show()
    floating = QWidget(owner, Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
    floating.show()
    try:
        with (
            patch(
                "iPhoto.gui.ui.controllers.map_extension_download_controller.QThreadPool.globalInstance"
            ),
            patch(
                "iPhoto.gui.ui.controllers.map_extension_download_controller.QMessageBox.question"
            ) as question,
            patch(
                "iPhoto.gui.ui.controllers.map_extension_download_controller.QMessageBox.information"
            ) as information,
        ):
            controller._start(
                MapExtensionDownloadRequest(controller._package_root, "win32", operation=operation)
            )
            worker = controller._active_worker
            assert floating.isHidden()
            worker.signals.ready.emit(
                MapExtensionDownloadResult(Path("pending"), Path("active"), "installed")
            )
            worker.signals.finished.emit()
        question.assert_not_called()
        information.assert_not_called()
        assert floating.isVisible()
        assert controller._progress_dialog is None
        assert controller._active_worker is None
        assert not controller._download_inflight
    finally:
        floating.close()


def test_installed_result_continues_waiting_preparation_without_restart(controller):
    callbacks = []
    with (
        patch(
            "iPhoto.gui.ui.controllers.map_extension_download_controller.QThreadPool.globalInstance"
        ) as pool,
        patch(
            "iPhoto.gui.ui.controllers.map_extension_download_controller.QMessageBox.question"
        ) as question,
    ):
        controller.start_download(source="settings")
        install_worker = controller._active_worker
        controller.prepare_runtime(lambda: callbacks.append("ready"))
        install_worker.signals.ready.emit(
            MapExtensionDownloadResult(Path("pending"), Path("active"))
        )
        install_worker.signals.finished.emit()
        assert pool.return_value.start.call_count == 2
        prepare_worker = controller._active_worker
        assert prepare_worker._request.operation == "prepare"
        assert callbacks == []
        prepare_worker.signals.ready.emit(
            MapExtensionDownloadResult(Path("pending"), Path("active"))
        )
        prepare_worker.signals.finished.emit()
    question.assert_not_called()
    assert callbacks == ["ready"]


def test_error_takes_precedence_over_installed_result(controller):
    from iPhoto.application.ports.map_extension import MapExtensionError

    controller._last_request = SimpleNamespace(operation="install")
    failure = MapExtensionError("permission", "activate")
    controller._handle_ready(MapExtensionDownloadResult(Path("pending"), Path("active")))
    controller._handle_error(failure)
    with (
        patch.object(controller, "_show_failure") as show_failure,
        patch(
            "iPhoto.gui.ui.controllers.map_extension_download_controller.QMessageBox.question"
        ) as question,
        patch(
            "iPhoto.gui.ui.controllers.map_extension_download_controller.QMessageBox.information"
        ) as information,
    ):
        controller._handle_finished()
    show_failure.assert_called_once_with(failure)
    question.assert_not_called()
    information.assert_not_called()


@pytest.fixture
def platform_controller(controller, monkeypatch):
    from iPhoto.gui.ui.controllers import map_extension_download_controller as module

    def configure(platform):
        monkeypatch.setattr(module, "sys", SimpleNamespace(platform=platform))
        controller._context.map_extensions = SimpleNamespace(
            download_url=lambda p: (
                "https://example.invalid/extension.zip" if p in {"win32", "linux"} else None
            ),
            supports_local_install=lambda p: p in {"win32", "linux"},
        )
        controller._context.settings.get = lambda *args: True
        return controller

    return configure


@pytest.mark.parametrize("platform", ["darwin", "unsupported"])
@pytest.mark.parametrize("entry", ["show_options", "install_from_file"])
def test_unsupported_manual_install_shows_information_without_chooser(
    platform_controller, platform, entry
):
    controller = platform_controller(platform)
    from PySide6.QtWidgets import QMessageBox

    with (
        patch(
            "iPhoto.gui.ui.controllers.map_extension_download_controller.QMessageBox.information"
        ) as information,
        patch(
            "iPhoto.gui.ui.controllers.map_extension_download_controller.QFileDialog.getOpenFileName"
        ) as chooser,
        patch.object(controller, "_start") as start,
    ):
        getattr(controller, entry)()
    chooser.assert_not_called()
    start.assert_not_called()
    information.assert_called_once()
    assert information.call_args.args[3] == QMessageBox.StandardButton.Close
    text = information.call_args.args[2]
    if platform == "darwin":
        assert "included with the app" in text
        assert "automatically" in text
    else:
        assert "not available" in text


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_supported_platform_imports_local_archive(platform_controller, platform, tmp_path):
    controller = platform_controller(platform)
    archive = tmp_path / ("extension.zip" if platform == "win32" else "extension.tar.xz")
    with (
        patch(
            "iPhoto.gui.ui.controllers.map_extension_download_controller.QFileDialog.getOpenFileName",
            return_value=(str(archive), ""),
        ),
        patch.object(controller, "_start") as start,
    ):
        controller.install_from_file()
    start.assert_called_once()
    request = start.call_args.args[0]
    assert request.platform == platform
    assert request.local_archive_path == archive


@pytest.mark.parametrize("entry", ["show_options", "maybe_prompt_on_startup", "_show_failure"])
@pytest.mark.parametrize("supported", [False, True])
def test_manual_install_buttons_follow_service_capability(
    controller, monkeypatch, entry, supported
):
    from PySide6.QtWidgets import QMessageBox
    from iPhoto.application.ports.map_extension import MapExtensionError

    controller._context.map_extensions.supports_local_install = lambda _: supported
    controller._context.settings.get = lambda *args: True
    controller._runtime_prepared = True
    controller._latest_result = MapExtensionDownloadResult(
        Path("pending"), Path("active"), "missing"
    )
    states = []

    def observe(box):
        local = next(button for button in box.buttons() if button.text() == "Install from File...")
        states.append(local.isEnabled())
        return 0

    monkeypatch.setattr(QMessageBox, "exec", observe)
    monkeypatch.setattr(QMessageBox, "open", observe)
    if entry == "_show_failure":
        controller._show_failure(MapExtensionError("refused", "download"))
    else:
        getattr(controller, entry)()
    assert states == [supported]
    if controller._startup_prompt is not None:
        controller._startup_prompt.deleteLater()
        controller._startup_prompt = None


def test_macos_has_no_manual_startup_prompt(platform_controller):
    controller = platform_controller("darwin")
    controller._runtime_prepared = True
    controller._latest_result = MapExtensionDownloadResult(
        Path("pending"), Path("active"), "missing"
    )
    with (
        patch(
            "iPhoto.gui.ui.controllers.map_extension_download_controller.QMessageBox.open"
        ) as opened,
        patch.object(controller, "_start") as start,
    ):
        assert controller.maybe_prompt_on_startup() is False
    opened.assert_not_called()
    start.assert_not_called()


def test_macos_recovery_disables_local_install(platform_controller, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from iPhoto.application.ports.map_extension import MapExtensionError

    controller = platform_controller("darwin")
    states = []

    def observe(box):
        states.append({b.text(): b.isEnabled() for b in box.buttons()})
        return 0

    monkeypatch.setattr(QMessageBox, "exec", observe)
    with patch.object(controller, "_start") as start:
        controller._show_failure(MapExtensionError("permission", "prepare"))
    assert states[0]["Install from File..."] is False
    assert states[0]["Download in Browser"] is False
    start.assert_not_called()


def test_controller_constructor_does_not_resolve_install_capabilities(qapp, tmp_path):
    class Context:
        @property
        def map_extensions(self):
            raise AssertionError("install service resolved during startup")

    owner = QWidget()
    MapExtensionDownloadController(owner, Context(), package_root=tmp_path)
    owner.close()

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for GUI tests", exc_type=ImportError)
pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets not available", exc_type=ImportError)

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QCheckBox, QPlainTextEdit, QPushButton, QWidget

from iPhoto.application.ports.map_extension import MapExtensionError
from iPhoto.gui.ui.controllers.map_extension_download_controller import (
    MapExtensionDownloadController,
)
from iPhoto.gui.ui.widgets import dialogs
from iPhoto.gui.ui.widgets.information_popup import InformationPopup, ProgressPopup
from iPhoto.gui.ui.tasks.map_extension_download_worker import (
    MapExtensionDownloadRequest,
    MapExtensionDownloadResult,
)


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _button(popup, label):
    return next(button for button in popup.findChildren(QPushButton) if button.text() == label)


@pytest.fixture
def observe_popup(monkeypatch):
    original = dialogs.open_action_popup
    failures = []

    def set_observer(observer):
        def open_popup(*args, **kwargs):
            popup = original(*args, **kwargs)

            def interact():
                try:
                    assert isinstance(popup, InformationPopup)
                    observer(popup)
                except BaseException as exc:
                    failures.append(exc)
                finally:
                    if popup.isVisible():
                        popup.close()

            QTimer.singleShot(0, popup, interact)
            return popup

        monkeypatch.setattr(dialogs, "open_action_popup", open_popup)

    yield set_observer
    if failures:
        raise failures[0]


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
            return_value=(False, 0),
        ),
        patch.object(dialogs, "run_action_popup") as warning,
    ):
        controller._restart_application()

    assert warning.call_args.kwargs["title"] == "Restart Failed"
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
    owner.show()
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
        controller.start_download()
    worker = pool.return_value.start.call_args.args[0]
    assert worker is controller._active_worker
    assert worker._service is controller._context.map_extensions
    assert controller._download_inflight
    assert controller._progress_dialog.isVisible()
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
    assert controller._progress_dialog is None
    assert events == []
    worker.signals.ready.emit(
        MapExtensionDownloadResult(Path("pending"), Path("active"), "missing")
    )
    assert events == []
    worker.signals.finished.emit()
    assert events == ["map", "info"]
    controller.prepare_runtime(lambda: events.append("ready"))
    assert events[-1] == "ready"


def test_preparation_shows_progress_when_installation_actually_starts(controller):
    with patch(
        "iPhoto.gui.ui.controllers.map_extension_download_controller.QThreadPool.globalInstance"
    ):
        controller.prepare_runtime(lambda: None)

    worker = controller._active_worker
    assert controller._progress_dialog is None
    worker.signals.progress.emit(0, 0, "Extracting map extension...")
    dialog = controller._progress_dialog
    assert isinstance(dialog, ProgressPopup)
    assert dialog.isVisible()
    assert dialog.windowModality() == Qt.WindowModality.WindowModal
    assert dialog.message() == "Extracting map extension..."
    worker.signals.progress.emit(50, 100, "Downloading map extension...")
    assert dialog._percent_label.text() == "50%"
    worker.signals.progress.emit(0, 0, "Installing map extension...")
    assert controller._progress_dialog is dialog
    worker.signals.ready.emit(MapExtensionDownloadResult(Path("pending"), Path("active")))
    worker.signals.finished.emit()
    assert controller._progress_dialog is None


@pytest.mark.parametrize("operation", ["install", "prepare"])
def test_pending_result_prompts_restart_without_claiming_installed(
    controller, observe_popup, operation
):
    controller._last_request = SimpleNamespace(operation=operation)
    controller._handle_ready(
        MapExtensionDownloadResult(Path("pending"), Path("active"), "pending_restart")
    )
    messages = []

    def accept(popup):
        messages.append(popup.message())
        assert popup.title() == "Restart Required"
        _button(popup, "Restart").click()

    observe_popup(accept)
    with patch.object(controller, "_restart_application") as restart:
        controller._handle_finished()
    restart.assert_called_once()
    assert "staged" in messages[0]
    assert "waiting for restart" in messages[0]


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


@pytest.mark.parametrize("browser_opened", [True, False])
def test_browser_download_offers_local_install(controller, observe_popup, tmp_path, browser_opened):

    archive = tmp_path / "extension.zip"

    def select_install(box):
        if browser_opened:
            assert box.message() == "Select the archive after the download finishes."
        else:
            assert "https://example.invalid/extension.zip" in box.message()
            assert box._message_label.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse
        _button(box, "Install from File...").click()

    observe_popup(select_install)
    with (
        patch(
            "iPhoto.gui.ui.controllers.map_extension_download_controller.QDesktopServices.openUrl",
            return_value=browser_opened,
        ) as open_url,
        patch(
            "iPhoto.gui.ui.controllers.map_extension_download_controller.QFileDialog.getOpenFileName",
            return_value=(str(archive), ""),
        ),
        patch.object(controller, "_start") as start,
    ):
        controller.open_download_page()
    open_url.assert_called_once()
    assert open_url.call_args.args[0].toString() == "https://example.invalid/extension.zip"
    assert start.call_args.args[0].local_archive_path == archive


@pytest.mark.parametrize("category", ["dns", "refused", "timeout", "tls", "http", "integrity"])
@pytest.mark.parametrize("choice", ["Download in Browser", "Install from File...", "Cancel"])
def test_download_failure_offers_manual_recovery(controller, observe_popup, category, choice):

    payload = MapExtensionDownloadRequest(controller._package_root, "win32")
    controller._last_request = payload
    failure = MapExtensionError(category, "download", code=11001, detail="host=github.com")

    def select_action(box):
        assert box.message().startswith("Could not download the map extension from GitHub.")
        assert "VPN" in box.message()
        assert "proxy" in box.message()
        details = box.findChild(QPlainTextEdit)
        assert details.isHidden()
        _button(box, "Details").click()
        assert details.isVisible() and details.isReadOnly()
        assert "11001" in details.toPlainText()
        assert "host=github.com" in details.toPlainText()
        buttons = {button.text() for button in box.findChildren(QPushButton)}
        assert {"Retry", "Try Direct Connection"}.isdisjoint(buttons)
        _button(box, choice).click()

    observe_popup(select_action)
    with (
        patch.object(controller, "_start") as start,
        patch.object(controller, "open_download_page") as browser,
        patch.object(controller, "install_from_file") as local,
    ):
        controller._show_failure(failure)
    start.assert_not_called()
    assert browser.call_count == (choice == "Download in Browser")
    assert local.call_count == (choice == "Install from File...")


def test_cancel_file_picker_after_browser_download_does_not_install(controller, observe_popup):

    def select_install(box):
        _button(box, "Install from File...").click()

    observe_popup(select_install)
    with (
        patch(
            "iPhoto.gui.ui.controllers.map_extension_download_controller.QDesktopServices.openUrl",
            return_value=True,
        ),
        patch(
            "iPhoto.gui.ui.controllers.map_extension_download_controller.QFileDialog.getOpenFileName",
            return_value=("", ""),
        ),
        patch.object(controller, "_start") as start,
    ):
        controller.open_download_page()
    start.assert_not_called()


def test_settings_can_import_after_closing_the_browser_handoff(controller, observe_popup, tmp_path):

    archive = tmp_path / "extension.zip"

    def select_close_or_install(box):
        close = next((b for b in box.findChildren(QPushButton) if b.text() == "Close"), None)
        if close is not None:
            close.click()
        else:
            _button(box, "Install from File...").click()

    observe_popup(select_close_or_install)
    with (
        patch(
            "iPhoto.gui.ui.controllers.map_extension_download_controller.QDesktopServices.openUrl",
            return_value=True,
        ),
        patch(
            "iPhoto.gui.ui.controllers.map_extension_download_controller.QFileDialog.getOpenFileName",
            return_value=(str(archive), ""),
        ) as chooser,
        patch.object(controller, "_start") as start,
    ):
        controller.open_download_page()
        chooser.assert_not_called()
        start.assert_not_called()
        controller.show_options()
    assert start.call_args.args[0].local_archive_path == archive


@pytest.mark.parametrize(
    ("category", "stage", "message"),
    [
        ("busy", "prepare", "Another map extension installation is running."),
        ("disk", "extract", "There is not enough disk space"),
        ("integrity", "verify", "The map extension package is damaged or incomplete."),
    ],
)
def test_other_failures_keep_their_own_message(controller, observe_popup, category, stage, message):

    def observe(box):
        assert box.message().startswith(message)
        assert "VPN" not in box.message()
        _button(box, "Cancel").click()

    observe_popup(observe)
    controller._show_failure(MapExtensionError(category, stage))


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


@pytest.mark.parametrize("retry", [False, True])
def test_failed_preparation_defers_runtime_probe_until_retry_or_cancel(
    controller, observe_popup, retry
):

    callbacks = []

    def select_action(box):
        assert callbacks == []
        assert not controller._runtime_prepared
        if retry:
            _button(box, "Retry").click()
        else:
            _button(box, "Cancel").click()

    observe_popup(select_action)
    with patch(
        "iPhoto.gui.ui.controllers.map_extension_download_controller.QThreadPool.globalInstance"
    ) as pool:
        controller.prepare_runtime(lambda: callbacks.append("ready"))
        worker = controller._active_worker
        worker.signals.error.emit(MapExtensionError("busy", "prepare"))
        worker.signals.finished.emit()
        assert pool.return_value.start.call_count == (2 if retry else 1)
        if retry:
            assert callbacks == []
            replacement = controller._active_worker
            assert replacement._request.operation == "prepare"
            assert not replacement._request.defer_activation
            replacement.signals.ready.emit(
                MapExtensionDownloadResult(Path("pending"), Path("active"), "installed")
            )
            replacement.signals.finished.emit()
        assert callbacks == ["ready"]
        assert controller._runtime_prepared


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
        old_worker.signals.progress.emit(0, 0, "Installing map extension...")
        assert controller._progress_dialog is None
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
            patch.object(dialogs, "run_action_popup") as popup,
        ):
            controller._start(
                MapExtensionDownloadRequest(controller._package_root, "win32", operation=operation)
            )
            worker = controller._active_worker
            assert floating.isHidden() == (operation == "install")
            worker.signals.ready.emit(
                MapExtensionDownloadResult(Path("pending"), Path("active"), "installed")
            )
            worker.signals.finished.emit()
        popup.assert_not_called()
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
        patch.object(dialogs, "run_action_popup") as popup,
    ):
        controller.start_download()
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
    popup.assert_not_called()
    assert callbacks == ["ready"]


def test_error_takes_precedence_over_installed_result(controller):
    from iPhoto.application.ports.map_extension import MapExtensionError

    controller._last_request = SimpleNamespace(operation="install")
    failure = MapExtensionError("permission", "activate")
    controller._handle_ready(MapExtensionDownloadResult(Path("pending"), Path("active")))
    controller._handle_error(failure)
    with (
        patch.object(controller, "_show_failure") as show_failure,
        patch.object(dialogs, "run_action_popup") as popup,
    ):
        controller._handle_finished()
    show_failure.assert_called_once_with(failure)
    popup.assert_not_called()


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

    with (
        patch.object(dialogs, "run_action_popup") as information,
        patch(
            "iPhoto.gui.ui.controllers.map_extension_download_controller.QFileDialog.getOpenFileName"
        ) as chooser,
        patch.object(controller, "_start") as start,
    ):
        getattr(controller, entry)()
    chooser.assert_not_called()
    start.assert_not_called()
    information.assert_called_once()
    assert information.call_args.kwargs["actions"] == (("Close", None),)
    text = information.call_args.args[1]
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
    controller, observe_popup, entry, supported
):
    from iPhoto.application.ports.map_extension import MapExtensionError

    controller._context.map_extensions.supports_local_install = lambda _: supported
    controller._context.settings.get = lambda *args: True
    controller._runtime_prepared = True
    controller._latest_result = MapExtensionDownloadResult(
        Path("pending"), Path("active"), "missing"
    )
    states = []

    def observe(box):
        local = _button(box, "Install from File...")
        states.append(local.isEnabled())
        return 0

    observe_popup(observe)
    if entry == "_show_failure":
        controller._show_failure(MapExtensionError("refused", "download"))
    else:
        getattr(controller, entry)()
    QApplication.processEvents()
    assert states == [supported]
    assert controller._startup_prompt is None


def test_startup_popup_saves_opt_out_on_dismissal(controller, observe_popup):
    controller._context.settings.get = lambda *args: True
    controller._context.settings.set = Mock()
    controller._runtime_prepared = True
    controller._latest_result = MapExtensionDownloadResult(
        Path("pending"), Path("active"), "missing"
    )

    def opt_out(popup):
        assert popup.title() == "Map Extension"
        assert popup.frameGeometry().center() == controller._parent.frameGeometry().center()
        checkbox = popup.findChild(QCheckBox)
        assert checkbox.text() == "Do not show again"
        checkbox.setChecked(True)
        _button(popup, "Not Now").click()

    observe_popup(opt_out)
    controller.maybe_prompt_on_startup()
    QApplication.processEvents()

    assert controller._startup_prompt is None
    controller._context.settings.set.assert_called_once_with(
        "ui.show_map_extension_startup_prompt", False
    )


def test_macos_has_no_manual_startup_prompt(platform_controller):
    controller = platform_controller("darwin")
    controller._runtime_prepared = True
    controller._latest_result = MapExtensionDownloadResult(
        Path("pending"), Path("active"), "missing"
    )
    with (
        patch.object(dialogs, "open_action_popup") as opened,
        patch.object(controller, "_start") as start,
    ):
        assert controller.maybe_prompt_on_startup() is False
    opened.assert_not_called()
    start.assert_not_called()


def test_macos_recovery_disables_local_install(platform_controller, observe_popup):
    from iPhoto.application.ports.map_extension import MapExtensionError

    controller = platform_controller("darwin")
    states = []

    def observe(box):
        states.append({b.text(): b.isEnabled() for b in box.findChildren(QPushButton)})
        return 0

    observe_popup(observe)
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

from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QCoreApplication, Qt, QThreadPool
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMainWindow, QPushButton

from iPhoto.application.ports.extension_install import ExtensionInstallError
from iPhoto.application.ports.recognition_extension import (
    RecognitionExtensionRequest,
    RecognitionExtensionResult,
    RecognitionModelFailure,
)
from iPhoto.gui.ui.controllers import recognition_extension_controller as module
from iPhoto.gui.ui.widgets.information_popup import InformationPopup, ProgressPopup
from iPhoto.gui.ui.widgets.main_header import MainHeaderWidget


def _button(popup, label):
    return next(button for button in popup.findChildren(QPushButton) if button.text() == label)


@pytest.fixture
def desktop(qtbot, tmp_path):
    window = QMainWindow()
    window.setGeometry(40, 60, 900, 620)
    qtbot.addWidget(window)
    window.show()
    context = SimpleNamespace(
        library=Mock(),
        recognition_extensions=Mock(
            download_url="https://github.com/example/recognition.zip",
            execute=Mock(return_value=RecognitionExtensionResult(tmp_path / "models")),
        ),
    )
    controller = module.RecognitionExtensionController(window, context, notification_toast=Mock())
    yield window, controller, context
    controller.shutdown()


def test_menu_is_next_to_maps_and_opens_three_choices_without_library(desktop):
    window, controller, context = desktop
    header = MainHeaderWidget(window, window)
    actions = header.settings_menu.actions()
    assert actions[actions.index(header.download_map_extension_action) + 1] is (
        header.download_recognition_extension_action
    )
    header.download_recognition_extension_action.triggered.connect(controller.show_options)
    header.download_recognition_extension_action.trigger()

    popup = controller._popup
    assert isinstance(popup, InformationPopup)
    assert popup.frameGeometry().center() == window.frameGeometry().center()
    assert [btn.text() for btn in popup.findChildren(QPushButton)] == [
        "Download", "Download in Browser", "Install from File…", "Cancel"
    ]
    context.recognition_extensions.execute.assert_not_called()


def test_unpublished_v2_only_offers_offline_install_and_preserves_pending_work(desktop, tmp_path):
    _, controller, context = desktop
    context.recognition_extensions.download_url = ""
    controller.show_options()
    assert [button.text() for button in controller._popup.findChildren(QPushButton)] == ["Install from File…", "Cancel"]
    controller._popup.close()
    QCoreApplication.processEvents()
    context.library.recognition_model_failure_is_current.return_value = True
    controller.model_unavailable(RecognitionModelFailure(tmp_path, 1, "pet", "missing"))
    assert "not available for download yet" in controller._popup.message()
    context.recognition_extensions.execute.assert_not_called()
    context.library.recognition_extension_installed.assert_not_called()


@pytest.mark.parametrize("opened", [True, False])
def test_browser_flow_uses_exact_link_and_offers_offline_install(desktop, monkeypatch, opened):
    _, controller, context = desktop
    open_url = Mock(return_value=opened)
    select = Mock(return_value=("", ""))
    monkeypatch.setattr(module.QDesktopServices, "openUrl", open_url)
    monkeypatch.setattr(module.QFileDialog, "getOpenFileName", select)
    controller.show_options()
    _button(controller._popup, "Download in Browser").click()

    assert open_url.call_args.args[0].toString() == context.recognition_extensions.download_url
    assert (context.recognition_extensions.download_url in controller._popup.message()) is not opened
    _button(controller._popup, "Install from File…").click()
    select.assert_called_once()
    context.recognition_extensions.execute.assert_not_called()


@pytest.mark.parametrize("local", [True, False])
def test_background_install_notifies_library_on_success(desktop, monkeypatch, tmp_path, local):
    _, controller, context = desktop
    path = tmp_path / "models.zip"
    monkeypatch.setattr(module.QFileDialog, "getOpenFileName", lambda *_: (str(path), ""))
    controller.show_options()
    label = "Install from File…" if local else "Download"
    _button(controller._popup, label).click()
    assert isinstance(controller._progress_dialog, ProgressPopup)

    assert QThreadPool.globalInstance().waitForDone(5000)
    QCoreApplication.processEvents()
    context.library.recognition_extension_installed.assert_called_once()
    assert context.recognition_extensions.execute.call_args.args[0] == (
        RecognitionExtensionRequest(path if local else None)
    )
    assert controller._progress_dialog is None
    controller._toast.show_toast.assert_called_once_with("Installed")


@pytest.mark.parametrize("first_kind, second_kind", [("face", "pet"), ("pet", "face")])
def test_missing_models_share_one_silent_package_install(desktop, tmp_path, first_kind, second_kind):
    _, controller, context = desktop
    context.library.recognition_model_failure_is_current.return_value = True
    controller.model_unavailable(RecognitionModelFailure(tmp_path, 1, first_kind, "missing"))
    controller.model_unavailable(RecognitionModelFailure(tmp_path, 1, second_kind, "missing"))
    assert controller._popup is None
    assert controller._progress_dialog is None

    assert QThreadPool.globalInstance().waitForDone(5000)
    QCoreApplication.processEvents()

    assert context.recognition_extensions.execute.call_count == 1
    assert context.recognition_extensions.execute.call_args.args[0] == RecognitionExtensionRequest()
    context.library.recognition_extension_installed.assert_called_once()
    controller._toast.show_toast.assert_not_called()
    assert controller._popup is None


def test_model_popup_is_once_per_process_and_discards_stale_results(desktop, tmp_path):
    _, controller, context = desktop
    context.recognition_extensions.execute.side_effect = ExtensionInstallError("dns", "download")
    error = RecognitionModelFailure(tmp_path, 1, "face", "download_failed")
    context.library.recognition_model_failure_is_current.return_value = False
    controller.model_unavailable(error)
    assert controller._popup is None

    context.library.recognition_model_failure_is_current.return_value = True
    controller.model_unavailable(RecognitionModelFailure(tmp_path, 2, "pet", "download_disabled"))
    assert controller._popup is None
    controller.model_unavailable(error)
    assert controller._popup is None
    assert controller._progress_dialog is None
    assert QThreadPool.globalInstance().waitForDone(5000)
    QCoreApplication.processEvents()
    context.recognition_extensions.execute.assert_called_once()
    context.library.recognition_extension_installed.assert_not_called()
    popup = controller._popup
    assert "recognition models" in popup.message()
    controller.model_unavailable(RecognitionModelFailure(tmp_path, 1, "pet", "missing"))
    assert controller._popup is popup
    QTest.keyClick(popup, Qt.Key.Key_Escape)
    assert controller._popup is None
    controller.library_changed()
    controller.model_unavailable(RecognitionModelFailure(tmp_path, 2, "face", "download_failed"))
    assert controller._popup is None


def test_failed_download_shows_recovery_and_does_not_resume_workers(desktop):
    _, controller, context = desktop
    context.recognition_extensions.execute.side_effect = ExtensionInstallError("dns", "download")
    controller.show_options()
    _button(controller._popup, "Download").click()

    assert QThreadPool.globalInstance().waitForDone(5000)
    QCoreApplication.processEvents()

    popup = controller._popup
    assert "Could not download" in popup.message()
    assert _button(popup, "Download in Browser")
    assert _button(popup, "Install from File…")
    context.library.recognition_extension_installed.assert_not_called()
    controller._toast.show_toast.assert_not_called()


def test_shutdown_cancels_silent_install_without_dispatching_result(desktop, tmp_path):
    _, controller, context = desktop
    installing = Event()
    observed_cancel = Event()

    def install(_request, _progress, *, is_cancelled):
        installing.set()
        deadline = monotonic() + 1
        while not is_cancelled() and monotonic() < deadline:
            sleep(0.005)
        if is_cancelled():
            observed_cancel.set()
        return RecognitionExtensionResult(tmp_path / "models")

    context.recognition_extensions.execute.side_effect = install
    controller.model_unavailable(RecognitionModelFailure(tmp_path, 1, "face", "missing"))
    assert installing.wait(1)
    controller.shutdown()

    assert QThreadPool.globalInstance().waitForDone(1000)
    assert observed_cancel.is_set()
    QCoreApplication.processEvents()
    context.library.recognition_extension_installed.assert_not_called()


def test_invalid_face_override_does_not_consume_pet_install_attempt(desktop, tmp_path):
    _, controller, context = desktop
    controller.model_unavailable(RecognitionModelFailure(tmp_path, 1, "face", "invalid_override"))
    popup = controller._popup
    assert "Fix or unset IPHOTO_FACE_MODEL_DIR" in popup.message()
    assert [button.text() for button in popup.findChildren(QPushButton)] == ["Close"]
    context.recognition_extensions.execute.assert_not_called()

    controller.model_unavailable(RecognitionModelFailure(tmp_path, 1, "pet", "missing"))
    assert QThreadPool.globalInstance().waitForDone(5000)
    QCoreApplication.processEvents()
    context.recognition_extensions.execute.assert_called_once()
    context.library.recognition_extension_installed.assert_called_once()
    assert controller._popup is popup

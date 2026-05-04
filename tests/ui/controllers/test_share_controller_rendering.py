"""Unit tests for share controller clipboard rendering logic."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional
from unittest.mock import Mock, patch

import pytest
from PySide6.QtGui import QImage, QAction, QActionGroup, QGuiApplication
from PySide6.QtWidgets import QApplication, QPushButton

from iPhoto.application.ports import EditRenderingState
from iPhoto.gui.ui.controllers.share_controller import ShareController, RenderClipboardWorker

@pytest.fixture()
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app

class StubSettings:
    def __init__(self, value: Optional[str] = None) -> None:
        self._value = value
    def get(self, key: str, default: str) -> str:
        return self._value if self._value is not None else default
    def set(self, key: str, value: str) -> None:
        self._value = value

class StubPathProvider:
    def __init__(self, path: Optional[Path]) -> None:
        self._path = path
    def __call__(self) -> Optional[Path]:
        return self._path

class StubStatusBar:
    def __init__(self) -> None:
        self.messages: list[tuple[str, int]] = []
    def show_message(self, message: str, timeout: int) -> None:
        self.messages.append((message, timeout))

class StubToast:
    def __init__(self) -> None:
        self.messages: list[str] = []
    def show_toast(self, message: str) -> None:
        self.messages.append(message)

@pytest.fixture()
def controller_factory(qapp: QApplication):
    def factory(*, settings: StubSettings, current_path_provider: StubPathProvider) -> ShareController:
        status_bar = StubStatusBar()
        toast = StubToast()
        share_button = QPushButton("Share")
        action_group = QActionGroup(share_button)
        copy_file_action = QAction("Copy File", share_button)
        copy_path_action = QAction("Copy Path", share_button)
        reveal_action = QAction("Reveal", share_button)

        controller = ShareController(
            settings=settings,
            current_path_provider=current_path_provider,
            status_bar=status_bar,
            notification_toast=toast,
            share_button=share_button,
            share_action_group=action_group,
            copy_file_action=copy_file_action,
            copy_path_action=copy_path_action,
            reveal_action=reveal_action,
        )
        return controller
    return factory

def test_copy_file_no_sidecar(controller_factory, tmp_path):
    """If no sidecar exists, standard file copy is used."""
    path = tmp_path / "photo.jpg"
    path.touch()

    settings = StubSettings("copy_file")
    path_provider = StubPathProvider(path)
    controller = controller_factory(settings=settings, current_path_provider=path_provider)
    controller._edit_service_getter = lambda: Mock(sidecar_exists=Mock(return_value=False))

    with patch.object(QGuiApplication, "clipboard") as clipboard_mock:
        mock_clipboard_inst = clipboard_mock.return_value
        controller._copy_file_to_clipboard(path)

    mock_clipboard_inst.setMimeData.assert_called()
    assert "Copied to Clipboard" in controller._toast.messages

def test_copy_file_with_sidecar_success(controller_factory, tmp_path, qapp):
    """If sidecar exists, render worker is started and success sets image."""
    path = tmp_path / "photo.jpg"
    path.touch()

    settings = StubSettings("copy_file")
    path_provider = StubPathProvider(path)
    controller = controller_factory(settings=settings, current_path_provider=path_provider)
    edit_service = Mock()
    edit_service.sidecar_exists.return_value = True
    controller._edit_service_getter = lambda: edit_service

    def mock_start(worker):
        worker.run()

    with patch.object(QGuiApplication, "clipboard") as clipboard_mock, patch(
        "PySide6.QtCore.QThreadPool.globalInstance"
    ) as pool_mock, patch(
        "iPhoto.gui.ui.controllers.share_controller.render_image",
        return_value=QImage(100, 100, QImage.Format_ARGB32),
    ):
        pool_mock.return_value.start.side_effect = mock_start
        mock_clipboard_inst = clipboard_mock.return_value
        controller._copy_file_to_clipboard(path)

    mock_clipboard_inst.setImage.assert_called()
    assert "Preparing image..." in controller._toast.messages
    assert "Copied to Clipboard" in controller._toast.messages

def test_copy_file_with_sidecar_failure(controller_factory, tmp_path, qapp):
    """If rendering fails, fallback to file copy."""
    path = tmp_path / "photo.jpg"
    path.touch()

    settings = StubSettings("copy_file")
    path_provider = StubPathProvider(path)
    controller = controller_factory(settings=settings, current_path_provider=path_provider)
    edit_service = Mock()
    edit_service.sidecar_exists.return_value = True
    controller._edit_service_getter = lambda: edit_service

    def mock_start(worker):
        worker.run()

    with patch.object(QGuiApplication, "clipboard") as clipboard_mock, patch(
        "PySide6.QtCore.QThreadPool.globalInstance"
    ) as pool_mock, patch(
        "iPhoto.gui.ui.controllers.share_controller.render_image",
        return_value=None,
    ):
        pool_mock.return_value.start.side_effect = mock_start
        mock_clipboard_inst = clipboard_mock.return_value
        controller._copy_file_to_clipboard(path)

    # Should fallback to setMimeData
    mock_clipboard_inst.setMimeData.assert_called()
    assert "Copied Original File" in controller._toast.messages


def test_copy_file_with_video_sidecar_uses_video_render(controller_factory, tmp_path):
    path = tmp_path / "clip.mov"
    path.touch()

    settings = StubSettings("copy_file")
    path_provider = StubPathProvider(path)
    controller = controller_factory(settings=settings, current_path_provider=path_provider)
    edit_service = Mock()
    edit_service.sidecar_exists.return_value = True
    edit_service.describe_adjustments.return_value = EditRenderingState(
        sidecar_exists=True,
        raw_adjustments={"Video_Trim_In_Sec": 1.0},
        resolved_adjustments={},
        adjusted_preview=False,
        has_visible_edits=True,
        trim_range_ms=(1000, 4000),
        effective_duration_sec=3.0,
    )
    controller._edit_service_getter = lambda: edit_service

    with patch.object(controller, "_copy_rendered_video_to_clipboard") as render_spy:
        controller._copy_file_to_clipboard(path)

        render_spy.assert_called_once_with(path)

def test_worker_logic(tmp_path):
    """Test RenderClipboardWorker internal logic."""
    path = tmp_path / "test.jpg"
    path.touch()
    edit_service = Mock()

    worker = RenderClipboardWorker(path, edit_service=edit_service)

    success_spy = Mock()
    fail_spy = Mock()
    worker.signals.success.connect(success_spy)
    worker.signals.failed.connect(fail_spy)

    with patch(
        "iPhoto.gui.ui.controllers.share_controller.render_image",
        return_value=QImage(100, 100, QImage.Format.Format_ARGB32),
    ):
        worker.run()

    if fail_spy.called:
        pytest.fail(f"Worker failed with: {fail_spy.call_args[0][0]}")

    success_spy.assert_called_once()
    result_image = success_spy.call_args[0][0]

    assert not result_image.isNull()
    assert result_image.width() == 100
    assert result_image.height() == 100

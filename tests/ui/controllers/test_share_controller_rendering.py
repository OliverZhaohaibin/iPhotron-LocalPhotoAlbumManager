"""Unit tests for share controller clipboard rendering logic."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import pytest
from PySide6.QtGui import QImage, QAction, QActionGroup, QGuiApplication
from PySide6.QtWidgets import QApplication, QPushButton

from iPhotos.src.iPhoto.gui.ui.controllers.share_controller import ShareController, RenderClipboardWorker
from iPhotos.src.iPhoto.gui.ui.models.asset_model import Roles

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

class StubPlaylist:
    def __init__(self, row: int) -> None:
        self._row = row
    def current_row(self) -> int:
        return self._row

class StubIndex:
    def __init__(self, valid: bool, absolute_path: Optional[str]) -> None:
        self._valid = valid
        self._absolute_path = absolute_path
    def isValid(self) -> bool:
        return self._valid
    def data(self, role: Roles) -> Optional[str]:
        if role == Roles.ABS:
            return self._absolute_path
        return None

class StubAssetModel:
    def __init__(self, absolute_path: Optional[str]) -> None:
        self._absolute_path = absolute_path
    def index(self, row: int, column: int) -> StubIndex:
        return StubIndex(True, self._absolute_path)

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
    def factory(*, settings: StubSettings, playlist: StubPlaylist, asset_model: StubAssetModel) -> ShareController:
        status_bar = StubStatusBar()
        toast = StubToast()
        share_button = QPushButton("Share")
        action_group = QActionGroup(share_button)
        copy_file_action = QAction("Copy File", share_button)
        copy_path_action = QAction("Copy Path", share_button)
        reveal_action = QAction("Reveal", share_button)

        controller = ShareController(
            settings=settings,
            playlist=playlist,
            asset_model=asset_model,
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

def test_copy_file_no_sidecar(controller_factory, mocker, tmp_path):
    """If no sidecar exists, standard file copy is used."""
    path = tmp_path / "photo.jpg"
    path.touch()

    mocker.patch("iPhotos.src.iPhoto.io.sidecar.sidecar_path_for_asset", return_value=path.with_suffix(".ipo"))
    # Ensure sidecar does not exist

    settings = StubSettings("copy_file")
    playlist = StubPlaylist(0)
    model = StubAssetModel(str(path))
    controller = controller_factory(settings=settings, playlist=playlist, asset_model=model)

    # Mock clipboard
    clipboard_mock = mocker.patch.object(QGuiApplication, "clipboard")
    mock_clipboard_inst = clipboard_mock.return_value

    controller._copy_file_to_clipboard(path)

    mock_clipboard_inst.setMimeData.assert_called()
    assert "Copied to Clipboard" in controller._toast.messages

def test_copy_file_with_sidecar_success(controller_factory, mocker, tmp_path, qapp):
    """If sidecar exists, render worker is started and success sets image."""
    path = tmp_path / "photo.jpg"
    path.touch()
    sidecar_path = path.with_suffix(".ipo")
    sidecar_path.touch()

    mocker.patch("iPhotos.src.iPhoto.io.sidecar.sidecar_path_for_asset", return_value=sidecar_path)

    settings = StubSettings("copy_file")
    playlist = StubPlaylist(0)
    model = StubAssetModel(str(path))
    controller = controller_factory(settings=settings, playlist=playlist, asset_model=model)

    clipboard_mock = mocker.patch.object(QGuiApplication, "clipboard")
    mock_clipboard_inst = clipboard_mock.return_value

    # Mock ThreadPool to run worker synchronously or we just verify worker start
    # But we want to test success callback.
    # We can mock QThreadPool.start to execute run() immediately?

    def mock_start(worker):
        worker.run()

    mocker.patch("PySide6.QtCore.QThreadPool.globalInstance").return_value.start.side_effect = mock_start

    # Mock RenderClipboardWorker to emit success immediately
    # We can patch the class in the module

    # Instead of patching class, let's patch _do_work or dependencies to make it succeed.
    mocker.patch("iPhotos.src.iPhoto.io.sidecar.load_adjustments", return_value={"Crop_W": 1.0})
    mocker.patch("iPhotos.src.iPhoto.utils.image_loader.load_qimage", return_value=QImage(100, 100, QImage.Format_ARGB32))
    mocker.patch("iPhotos.src.iPhoto.io.sidecar.resolve_render_adjustments", return_value={})
    mocker.patch("iPhotos.src.iPhoto.gui.ui.controllers.share_controller.apply_adjustments", return_value=QImage(100, 100, QImage.Format_ARGB32))

    controller._copy_file_to_clipboard(path)

    mock_clipboard_inst.setImage.assert_called()
    assert "Preparing image..." in controller._toast.messages
    assert "Copied to Clipboard" in controller._toast.messages

def test_copy_file_with_sidecar_failure(controller_factory, mocker, tmp_path, qapp):
    """If rendering fails, fallback to file copy."""
    path = tmp_path / "photo.jpg"
    path.touch()
    sidecar_path = path.with_suffix(".ipo")
    sidecar_path.touch()

    mocker.patch("iPhotos.src.iPhoto.io.sidecar.sidecar_path_for_asset", return_value=sidecar_path)

    settings = StubSettings("copy_file")
    playlist = StubPlaylist(0)
    model = StubAssetModel(str(path))
    controller = controller_factory(settings=settings, playlist=playlist, asset_model=model)

    clipboard_mock = mocker.patch.object(QGuiApplication, "clipboard")
    mock_clipboard_inst = clipboard_mock.return_value

    def mock_start(worker):
        worker.run()

    mocker.patch("PySide6.QtCore.QThreadPool.globalInstance").return_value.start.side_effect = mock_start

    # Force failure by making load_adjustments return empty
    mocker.patch("iPhotos.src.iPhoto.io.sidecar.load_adjustments", return_value={})

    controller._copy_file_to_clipboard(path)

    # Should fallback to setMimeData
    mock_clipboard_inst.setMimeData.assert_called()
    assert "Copied Original File" in controller._toast.messages

def test_worker_logic(mocker, tmp_path):
    """Test RenderClipboardWorker internal logic."""
    path = tmp_path / "test.jpg"
    path.touch()

    worker = RenderClipboardWorker(path)

    # Mock dependencies
    mocker.patch("iPhotos.src.iPhoto.io.sidecar.load_adjustments", return_value={
        "Crop_CX": 0.5, "Crop_CY": 0.5, "Crop_W": 0.5, "Crop_H": 0.5, "Crop_Rotate90": 1.0, "Crop_FlipH": True
    })

    original_image = QImage(100, 100, QImage.Format.Format_ARGB32)
    original_image.fill(0xFF000000) # Black

    mocker.patch("iPhotos.src.iPhoto.utils.image_loader.load_qimage", return_value=original_image)
    mocker.patch("iPhotos.src.iPhoto.io.sidecar.resolve_render_adjustments", return_value={})
    mocker.patch("iPhotos.src.iPhoto.gui.ui.controllers.share_controller.apply_adjustments", side_effect=lambda img, adj: img)

    success_spy = mocker.Mock()
    fail_spy = mocker.Mock()
    worker.signals.success.connect(success_spy)
    worker.signals.failed.connect(fail_spy)

    worker.run()

    if fail_spy.called:
        pytest.fail(f"Worker failed with: {fail_spy.call_args[0][0]}")

    success_spy.assert_called_once()
    result_image = success_spy.call_args[0][0]

    # Original 100x100.
    # Crop 0.5 W/H (50x50) at Center.
    # Flip H.
    # Rotate 90.
    # Result should be 50x50.
    assert result_image.width() == 50
    assert result_image.height() == 50

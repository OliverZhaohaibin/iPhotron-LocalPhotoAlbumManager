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


def test_controller_temporarily_hides_stays_on_top_child_windows(qapp: QApplication, tmp_path: Path) -> None:
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

    with patch(
        "iPhoto.gui.ui.controllers.map_extension_download_controller.QProcess.startDetached",
        return_value=False,
    ), patch(
        "iPhoto.gui.ui.controllers.map_extension_download_controller.QMessageBox.critical",
        return_value=0,
    ):
        controller._restart_application()

    assert floating.isVisible()
    floating.close()
    owner.close()


def test_ready_does_not_prompt_restart_when_install_folder_is_not_verified(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    del qapp
    owner = QWidget()
    owner.show()

    context = SimpleNamespace(settings=SimpleNamespace(get=lambda *_args, **_kwargs: True))
    controller = MapExtensionDownloadController(owner, context, package_root=tmp_path / "maps")

    with patch(
        "iPhoto.gui.ui.controllers.map_extension_download_controller.verify_osmand_extension_install",
        return_value=False,
    ), patch.object(
        controller,
        "_handle_error",
    ) as handle_error, patch(
        "iPhoto.gui.ui.controllers.map_extension_download_controller.QMessageBox.question",
    ) as question:
        controller._handle_ready(
            MapExtensionDownloadResult(
                pending_root=tmp_path / "maps" / "tiles" / "extension.pending",
                extension_root=tmp_path / "maps" / "tiles" / "extension",
            )
        )

    handle_error.assert_called_once()
    question.assert_not_called()
    owner.close()


def test_start_download_keeps_worker_alive_until_finished(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    del qapp
    owner = QWidget()
    owner.show()

    class _FakeWorker(QRunnable):
        def __init__(self, _request) -> None:
            super().__init__()
            self.signals = MapExtensionDownloadSignals()

        def run(self) -> None:
            self.signals.finished.emit()

    context = SimpleNamespace(settings=SimpleNamespace(get=lambda *_args, **_kwargs: True))
    controller = MapExtensionDownloadController(owner, context, package_root=tmp_path / "maps")

    with patch(
        "iPhoto.gui.ui.controllers.map_extension_download_controller.supports_map_extension_download",
        return_value=True,
    ), patch(
        "iPhoto.gui.ui.controllers.map_extension_download_controller.MapExtensionDownloadWorker",
        _FakeWorker,
    ), patch(
        "iPhoto.gui.ui.controllers.map_extension_download_controller.QThreadPool.globalInstance"
    ) as global_instance:
        started_workers: list[_FakeWorker] = []

        def _start(worker: _FakeWorker, _priority: int = 0) -> None:
            started_workers.append(worker)

        global_instance.return_value.start.side_effect = _start
        controller.start_download(source="test")

    assert controller._active_worker is started_workers[0]
    assert controller._download_inflight is True

    started_workers[0].run()

    assert controller._active_worker is None
    assert controller._download_inflight is False
    owner.close()


def test_startup_pending_install_is_recovered_and_verified(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    del qapp
    owner = QWidget()
    owner.show()

    context = SimpleNamespace(settings=SimpleNamespace(get=lambda *_args, **_kwargs: True))
    controller = MapExtensionDownloadController(owner, context, package_root=tmp_path / "maps")

    with patch(
        "iPhoto.gui.ui.controllers.map_extension_download_controller.supports_map_extension_download",
        return_value=True,
    ), patch(
        "iPhoto.gui.ui.controllers.map_extension_download_controller.has_installed_osmand_extension",
        return_value=False,
    ), patch(
        "iPhoto.gui.ui.controllers.map_extension_download_controller.has_pending_osmand_extension_install",
        return_value=True,
    ), patch(
        "iPhoto.gui.ui.controllers.map_extension_download_controller.apply_pending_osmand_extension_install",
    ) as apply_pending, patch(
        "iPhoto.gui.ui.controllers.map_extension_download_controller.verify_osmand_extension_install",
        return_value=True,
    ), patch(
        "iPhoto.gui.ui.controllers.map_extension_download_controller.QMessageBox.information",
        return_value=0,
    ) as information, patch(
        "iPhoto.gui.ui.controllers.map_extension_download_controller.QMessageBox.critical",
        return_value=0,
    ) as critical:
        controller.maybe_prompt_on_startup()

    apply_pending.assert_called_once_with(controller._package_root)
    information.assert_called_once()
    critical.assert_not_called()
    owner.close()


def test_startup_pending_install_reports_verification_failure(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    del qapp
    owner = QWidget()
    owner.show()

    context = SimpleNamespace(settings=SimpleNamespace(get=lambda *_args, **_kwargs: True))
    controller = MapExtensionDownloadController(owner, context, package_root=tmp_path / "maps")

    with patch(
        "iPhoto.gui.ui.controllers.map_extension_download_controller.supports_map_extension_download",
        return_value=True,
    ), patch(
        "iPhoto.gui.ui.controllers.map_extension_download_controller.has_installed_osmand_extension",
        return_value=False,
    ), patch(
        "iPhoto.gui.ui.controllers.map_extension_download_controller.has_pending_osmand_extension_install",
        return_value=True,
    ), patch(
        "iPhoto.gui.ui.controllers.map_extension_download_controller.apply_pending_osmand_extension_install",
    ), patch(
        "iPhoto.gui.ui.controllers.map_extension_download_controller.verify_osmand_extension_install",
        return_value=False,
    ), patch(
        "iPhoto.gui.ui.controllers.map_extension_download_controller.QMessageBox.critical",
        return_value=0,
    ) as critical:
        controller.maybe_prompt_on_startup()

    critical.assert_called_once()
    message = critical.call_args.args[2]
    assert "pending map extension install" in message
    assert "Pending folder:" in message
    assert "Active extension folder:" in message
    owner.close()

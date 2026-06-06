"""Tests for :mod:`iPhoto.gui.ui.tasks.assign_location_worker`."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip(
    "PySide6",
    reason="PySide6 is required for worker tests",
    exc_type=ImportError,
)
pytest.importorskip(
    "PySide6.QtWidgets",
    reason="Qt widgets are required for worker tests",
    exc_type=ImportError,
)

from PySide6.QtWidgets import QApplication

from iPhoto.application.services.assign_location_service import AssignedLocationResult
from iPhoto.gui.ui.tasks import assign_location_worker as worker_module
from iPhoto.gui.ui.tasks.assign_location_worker import AssignLocationRequest, AssignLocationWorker


@pytest.fixture()
def qapp() -> QApplication:
    """Ensure a QApplication exists for QObject-based signals."""

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_video_file_write_unexpected_error_emits_cleanup_error(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    del qapp

    asset_path = tmp_path / "video.mp4"
    expected = AssignedLocationResult(
        asset_path=asset_path,
        asset_rel="video.mp4",
        display_name="Munich",
        gps={"lat": 48.137154, "lon": 11.576124},
        metadata={"location": "Munich"},
    )

    class FakeMetadataService:
        def write_gps_metadata(self, path: Path, **kwargs: Any) -> None:
            assert path == asset_path
            assert kwargs == {
                "latitude": 48.137154,
                "longitude": 11.576124,
                "is_video": True,
            }
            raise RuntimeError("unexpected writer failure")

    class FakeAssignLocationService:
        def __init__(self, state_repository: object, metadata: FakeMetadataService) -> None:
            self.metadata = metadata

        def persist_library_assignment(self, **kwargs: Any) -> AssignedLocationResult:
            assert kwargs["asset_path"] == asset_path
            return expected

    monkeypatch.setattr(worker_module, "ExifToolLocationMetadataService", FakeMetadataService)
    monkeypatch.setattr(worker_module, "AssignLocationService", FakeAssignLocationService)

    worker = AssignLocationWorker(
        AssignLocationRequest(
            library_root=tmp_path,
            asset_path=asset_path,
            asset_rel="video.mp4",
            display_name="Munich",
            latitude=48.137154,
            longitude=11.576124,
            is_video=True,
            existing_metadata={},
        )
    )
    ready: list[AssignedLocationResult] = []
    file_write_errors: list[tuple[Path, str]] = []
    errors: list[str] = []
    finished: list[bool] = []
    worker.signals.ready.connect(ready.append)
    worker.signals.file_write_error.connect(
        lambda path, message: file_write_errors.append((path, message))
    )
    worker.signals.error.connect(errors.append)
    worker.signals.finished.connect(lambda: finished.append(True))

    worker.run()

    assert ready == [expected]
    assert file_write_errors == [(asset_path, "unexpected writer failure")]
    assert errors == []
    assert finished == [True]

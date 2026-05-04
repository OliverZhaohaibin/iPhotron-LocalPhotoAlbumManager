"""Tests for :mod:`iPhoto.gui.ui.tasks.manual_face_add_worker`."""

from __future__ import annotations

import os
from pathlib import Path

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

from iPhoto.people.service import ManualFaceAddResult
from iPhoto.gui.ui.tasks.manual_face_add_worker import ManualFaceAddWorker


@pytest.fixture()
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_manual_face_add_worker_uses_factory_when_service_not_injected(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    del qapp

    expected = ManualFaceAddResult(
        asset_id="asset-1",
        face_id="face-1",
        person_id="person-1",
        created_new_person=False,
    )
    factory_calls: list[Path] = []
    add_calls: list[dict[str, object]] = []

    class FakePeopleService:
        def add_manual_face(
            self,
            *,
            asset_id: str,
            requested_box: tuple[int, int, int, int],
            name_or_none: str | None,
            person_id: str | None,
        ) -> ManualFaceAddResult:
            add_calls.append(
                {
                    "asset_id": asset_id,
                    "requested_box": requested_box,
                    "name_or_none": name_or_none,
                    "person_id": person_id,
                }
            )
            return expected

    def _fake_create_people_service(root: Path) -> FakePeopleService:
        factory_calls.append(root)
        return FakePeopleService()

    monkeypatch.setattr(
        "iPhoto.gui.ui.tasks.manual_face_add_worker.create_people_service",
        _fake_create_people_service,
    )

    worker = ManualFaceAddWorker(
        library_root=tmp_path,
        asset_id="asset-1",
        requested_box=(1, 2, 3, 4),
        name_or_none="Alice",
        person_id="person-1",
    )
    ready: list[ManualFaceAddResult] = []
    errors: list[str] = []
    finished: list[bool] = []
    worker.signals.ready.connect(ready.append)
    worker.signals.error.connect(errors.append)
    worker.signals.finished.connect(lambda: finished.append(True))

    worker.run()

    assert factory_calls == [tmp_path]
    assert add_calls == [
        {
            "asset_id": "asset-1",
            "requested_box": (1, 2, 3, 4),
            "name_or_none": "Alice",
            "person_id": "person-1",
        }
    ]
    assert ready == [expected]
    assert errors == []
    assert finished == [True]

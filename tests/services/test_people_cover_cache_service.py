from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for people cover cache tests", exc_type=ImportError)

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from iPhoto.infrastructure.services.people_cover_cache_service import (
    PeopleCoverCacheService,
    PeopleCoverRenderTask,
    PeopleCoverWorkerSignals,
)


@pytest.fixture
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_render_task_emits_empty_image_when_renderer_returns_none(qapp: QApplication) -> None:
    captured: list[tuple[str, QImage]] = []
    signals = PeopleCoverWorkerSignals()
    signals.result.connect(lambda cache_key, image: captured.append((cache_key, image)))

    task = PeopleCoverRenderTask(
        cache_key="cache-key",
        renderer=lambda: None,
        signals=signals,
    )
    task.run()

    assert len(captured) == 1
    cache_key, image = captured[0]
    assert cache_key == "cache-key"
    assert image.isNull()


def test_get_rendered_cover_returns_empty_when_service_is_shutting_down(
    qapp: QApplication, tmp_path: Path
) -> None:
    service = PeopleCoverCacheService(tmp_path / "people-covers")
    service.shutdown()

    cache_key, pixmap = service.get_rendered_cover(
        cache_id="group-1",
        size=(20, 20),
        signature="sig",
        renderer=lambda: QImage(20, 20, QImage.Format.Format_RGBA8888),
    )

    assert cache_key is None
    assert pixmap is None


def test_handle_render_result_skips_cache_write_during_shutdown(
    qapp: QApplication, tmp_path: Path
) -> None:
    service = PeopleCoverCacheService(tmp_path / "people-covers")
    service.shutdown()

    image = QImage(20, 20, QImage.Format.Format_RGBA8888)
    image.fill(0xFFFFFFFF)
    service._handle_render_result("cache-key", image)

    assert service.cached_pixmap("cache-key") is None
    assert not (tmp_path / "people-covers" / "cache-key.png").exists()

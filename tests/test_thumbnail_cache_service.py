from pathlib import Path

import pytest

PySide6 = pytest.importorskip("PySide6")
QSize = pytest.importorskip("PySide6.QtCore").QSize
QImage = pytest.importorskip("PySide6.QtGui").QImage
QSignalSpy = pytest.importorskip("PySide6.QtTest").QSignalSpy

from iPhoto.infrastructure.services.thumbnail_cache_service import (
    ThumbnailCacheService,
    ThumbnailGenerationTask,
    ThumbnailWorkerSignals,
)


def test_start_generation_retains_worker_and_signals(tmp_path: Path) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    service._thread_pool.start = lambda worker: None  # type: ignore[assignment]

    path = tmp_path / "photo.jpg"
    size = QSize(128, 128)
    key = service._cache_key(path, size)

    service._pending_tasks.add(key)
    service._start_generation(path, size)

    assert key in service._active_workers
    assert key in service._active_signals

    service._handle_generation_failed(path, size)

    assert key not in service._pending_tasks
    assert key not in service._active_workers
    assert key not in service._active_signals


def test_generation_task_emits_failed_on_null_image(tmp_path: Path) -> None:
    signals = ThumbnailWorkerSignals()
    failed_spy = QSignalSpy(signals.failed)
    result_spy = QSignalSpy(signals.result)

    task = ThumbnailGenerationTask(
        renderer=lambda _path, _size: QImage(),
        path=tmp_path / "missing.jpg",
        size=QSize(64, 64),
        signals=signals,
    )

    task.run()

    assert failed_spy.count() == 1
    assert result_spy.count() == 0

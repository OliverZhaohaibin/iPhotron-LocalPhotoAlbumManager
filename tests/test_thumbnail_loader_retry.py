import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QSize

from iPhoto.gui.ui.tasks.thumbnail_loader import ThumbnailLoader


def test_thumbnail_loader_retries_once_on_failure(tmp_path):
    loader = ThumbnailLoader()
    loader.reset_for_album(tmp_path)

    rel = "missing.jpg"
    abs_path = tmp_path / rel
    abs_path.write_bytes(b"")
    size = QSize(512, 512)
    base_key = loader._base_key(rel, size)

    loader._job_specs[base_key] = (
        rel,
        abs_path,
        size,
        None,
        tmp_path,
        tmp_path,
        True,
        False,
        None,
        None,
    )
    loader._pending_keys.add(base_key)
    loader._active_jobs_count = 1
    loader._max_active_jobs = 0

    loader._handle_result((*base_key, 0), None, rel)

    assert loader._failure_counts[base_key] == 1
    assert base_key not in loader._failures
    assert loader._pending_heap
    _, _, retry_key, retry_job = loader._pending_heap[0]
    assert retry_key == base_key
    assert getattr(retry_job, "_rel") == rel

    loader._pending_heap.clear()
    loader._pending_keys.clear()
    loader._pending_priorities.clear()
    loader._pending_keys.add(base_key)
    loader._active_jobs_count = 1

    loader._handle_result((*base_key, 0), None, rel)

    assert loader._failure_counts[base_key] == 1
    assert base_key in loader._failures
    assert not loader._pending_heap


def test_thumbnail_loader_retry_preserves_video_parameters(tmp_path):
    loader = ThumbnailLoader()
    loader.reset_for_album(tmp_path)
    loader._max_active_jobs = 0

    rel = "clip.mp4"
    abs_path = tmp_path / rel
    abs_path.write_bytes(b"video")
    size = QSize(512, 512)
    base_key = loader._base_key(rel, size)

    loader._job_specs[base_key] = (
        rel,
        abs_path,
        size,
        99,
        tmp_path,
        tmp_path,
        False,
        True,
        1.5,
        8.0,
    )

    assert loader._retry_after_failure(base_key, rel) is True
    assert loader._pending_heap

    _, _, retry_key, retry_job = loader._pending_heap[0]
    assert retry_key == base_key
    assert getattr(retry_job, "_is_image") is False
    assert getattr(retry_job, "_is_video") is True
    assert getattr(retry_job, "_still_image_time") == 1.5
    assert getattr(retry_job, "_duration") == 8.0
    assert getattr(retry_job, "_known_stamp") == 99

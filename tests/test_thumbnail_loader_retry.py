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

    lib_root = tmp_path
    loader._job_specs[base_key] = (
        rel,
        abs_path,
        size,
        None,
        tmp_path,
        lib_root,
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
    assert loader._pending_deque
    retry_key, retry_job = loader._pending_deque[0]
    assert retry_key == base_key
    assert getattr(retry_job, "_rel") == rel

    loader._pending_deque.clear()
    loader._pending_keys.add(base_key)
    loader._active_jobs_count = 1

    loader._handle_result((*base_key, 0), None, rel)

    assert loader._failure_counts[base_key] == 1
    assert base_key in loader._failures
    assert not loader._pending_deque

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

import pytest
pytest.importorskip("PySide6", reason="PySide6 is required for thumbnail tests", exc_type=ImportError)
from PySide6.QtCore import QSize, QThreadPool
from PySide6.QtGui import QImage
from PySide6.QtTest import QSignalSpy

from iPhoto.infrastructure.services.thumbnail_cache_service import ThumbnailCacheService


def test_thumbnail_cache_service_remaps_album_disk_cache(tmp_path: Path) -> None:
    cache_dir = tmp_path / "thumbs"
    service = ThumbnailCacheService(cache_dir)
    old_album = tmp_path / "Trips"
    new_album = tmp_path / "Renamed Trips"
    old_photo = old_album / "photo.jpg"
    new_photo = new_album / "photo.jpg"
    new_photo.parent.mkdir(parents=True)
    new_photo.write_bytes(b"image")

    size = QSize(512, 512)
    old_key = service._cache_key(old_photo, size)
    new_key = service._cache_key(new_photo, size)
    old_cache_file = cache_dir / f"{old_key}.jpg"
    new_cache_file = cache_dir / f"{new_key}.jpg"
    old_cache_file.write_bytes(b"cached-thumbnail")

    service.remap_album_paths(old_album, new_album, size=size)

    assert new_cache_file.read_bytes() == b"cached-thumbnail"


def test_thumbnail_cache_service_uses_dedicated_thread_pool(tmp_path: Path) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")

    assert service._thread_pool is not QThreadPool.globalInstance()
    assert service._thread_pool.maxThreadCount() >= 1
    service.shutdown()


def test_render_thumbnail_skips_color_stats_without_sidecar(tmp_path: Path) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    edit_service = Mock()
    edit_service.sidecar_exists.return_value = False
    service.set_edit_service(edit_service)
    image = QImage(8, 8, QImage.Format.Format_ARGB32_Premultiplied)
    path = tmp_path / "photo.jpg"
    path.write_bytes(b"image")
    size = QSize(64, 64)

    with patch(
        "iPhoto.infrastructure.services.thumbnail_cache_service.image_loader.load_qimage",
        return_value=image,
    ), patch(
        "iPhoto.infrastructure.services.thumbnail_cache_service.compute_color_statistics",
    ) as compute_stats:
        rendered = service._render_thumbnail(path, size)

    assert rendered is not None
    edit_service.describe_adjustments.assert_not_called()
    compute_stats.assert_not_called()


def test_thumbnail_cache_service_emits_ready_with_size(tmp_path: Path, qapp) -> None:
    del qapp
    service = ThumbnailCacheService(tmp_path / "thumbs")
    path = tmp_path / "photo.jpg"
    size = QSize(64, 64)
    image = QImage(8, 8, QImage.Format.Format_ARGB32_Premultiplied)
    spy = QSignalSpy(service.thumbnailReady)

    service._handle_generation_result(path, size, image)

    assert spy.count() == 1
    assert spy.at(0)[0] == path
    assert spy.at(0)[1] == size


def test_handle_generation_result_clears_pending_on_null_image(tmp_path: Path, qapp) -> None:
    del qapp
    service = ThumbnailCacheService(tmp_path / "thumbs")
    path = tmp_path / "photo.jpg"
    size = QSize(64, 64)
    key = service._cache_key(path, size)
    service._pending_tasks.add(key)

    service._handle_generation_result(path, size, QImage())

    assert key not in service._pending_tasks


def test_get_thumbnail_retries_after_failed_generation(tmp_path: Path, qapp) -> None:
    del qapp
    service = ThumbnailCacheService(tmp_path / "thumbs")
    path = tmp_path / "photo.jpg"
    size = QSize(64, 64)
    path.write_bytes(b"image")

    calls = {"count": 0}

    def fake_start_generation(request_path: Path, request_size: QSize) -> None:
        calls["count"] += 1
        service._handle_generation_result(request_path, request_size, QImage())

    with patch.object(service, "_start_generation", side_effect=fake_start_generation):
        assert service.get_thumbnail(path, size) is None
        assert service.get_thumbnail(path, size) is None

    assert calls["count"] == 2


def test_get_thumbnail_removes_corrupt_disk_cache(tmp_path: Path, qapp) -> None:
    del qapp
    cache_dir = tmp_path / "thumbs"
    service = ThumbnailCacheService(cache_dir)
    path = tmp_path / "photo.jpg"
    size = QSize(64, 64)
    key = service._cache_key(path, size)
    disk_file = cache_dir / f"{key}.jpg"
    disk_file.write_bytes(b"not-a-valid-jpeg")

    with patch.object(service, "_start_generation") as start_generation:
        assert service.get_thumbnail(path, size) is None

    assert not disk_file.exists()
    start_generation.assert_called_once_with(path, size)

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
pytest.importorskip("PySide6", reason="PySide6 is required for thumbnail tests", exc_type=ImportError)
from PIL import Image
from PySide6.QtCore import QSize
from PySide6.QtGui import QImage, QPixmap

from iPhoto.infrastructure.services.thumbnail_cache_keys import thumbnail_cache_file
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


def test_thumbnail_failure_has_cooldown(tmp_path: Path) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    path = tmp_path / "missing.jpg"
    size = QSize(64, 64)
    key = service._cache_key(path, size)

    service._handle_generation_failure(path, size, "empty_render")
    with patch.object(service, "_queue_generation") as queue_generation:
        assert service.get_thumbnail(path, size) is None

    queue_generation.assert_not_called()
    assert service._failure_until[key] > 0


def test_l1_l2_hit_does_not_enqueue_generation(tmp_path: Path) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    path = tmp_path / "photo.jpg"
    path.write_bytes(b"image")
    size = QSize(64, 64)
    key = service._cache_key(path, size)
    image = QImage(8, 8, QImage.Format.Format_ARGB32_Premultiplied)
    service._memory_cache[key] = image

    with patch.object(service, "_queue_generation") as queue_generation:
        assert service.get_thumbnail(path, size) is image

    queue_generation.assert_not_called()


def test_peek_is_memory_only_and_does_not_schedule_work(tmp_path: Path, qapp) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    path = tmp_path / "photo.jpg"
    size = QSize(64, 64)
    key = service._cache_key(path, size)
    pixmap = QPixmap(8, 8)
    service._memory_cache[key] = pixmap

    with (
        patch(
            "iPhoto.infrastructure.services.thumbnail_cache_service.thumbnail_cache_file_for_key"
        ) as disk_lookup,
        patch.object(service, "_queue_generation") as queue_generation,
        patch(
            "iPhoto.infrastructure.services.thumbnail_cache_service.emit_perf_event"
        ) as emit_perf_event,
    ):
        assert service.peek(path, size) is pixmap
        assert service.peek(tmp_path / "missing.jpg", size) is None

    disk_lookup.assert_not_called()
    queue_generation.assert_not_called()
    emit_perf_event.assert_not_called()


def test_request_many_queues_misses_without_caller_disk_access(tmp_path: Path) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    first = tmp_path / "first.jpg"
    second = tmp_path / "second.jpg"
    size = QSize(64, 64)
    service._memory_cache[service._cache_key(first, size)] = QImage(8, 8, QImage.Format_RGB32)

    with (
        patch(
            "iPhoto.infrastructure.services.thumbnail_cache_service.thumbnail_cache_file_for_key"
        ) as disk_lookup,
        patch.object(service, "_queue_generation") as queue_generation,
    ):
        queued = service.request_many([first, first, second], size, priority="visible")

    assert queued == 1
    queue_generation.assert_called_once_with(second, size, priority="visible")
    disk_lookup.assert_not_called()


def test_worker_loader_prefers_l2_without_rendering_source(tmp_path: Path) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    path = tmp_path / "photo.jpg"
    size = QSize(64, 64)
    disk_file = thumbnail_cache_file(tmp_path / "thumbs", path, (64, 64))
    disk_file.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64), "red").save(disk_file, format="JPEG")

    with patch.object(service, "_render_thumbnail") as render_thumbnail:
        image = service._load_or_render_thumbnail(path, size)

    assert image is not None
    assert not image.isNull()
    render_thumbnail.assert_not_called()


def test_generation_result_publishes_without_gui_disk_write(tmp_path: Path, qapp) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    path = tmp_path / "photo.jpg"
    size = QSize(64, 64)
    image = QImage(64, 64, QImage.Format_RGB32)
    key = service._cache_key(path, size)
    service._pending_tasks.add(key)
    service._active_tasks = 1

    with patch(
        "iPhoto.infrastructure.services.thumbnail_cache_service.thumbnail_cache_file_for_key"
    ) as disk_lookup:
        service._handle_generation_result(path, size, image)

    disk_lookup.assert_not_called()
    assert service.peek(path, size) is not None


def test_request_many_eventually_publishes_full_thumbnail(tmp_path: Path, qapp) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    path = tmp_path / "photo.jpg"
    Image.new("RGB", (256, 128), "blue").save(path, format="JPEG")
    size = QSize(64, 64)
    ready: list[Path] = []
    service.thumbnailReady.connect(ready.append)

    assert service.request_many([path], size, priority="visible") == 1

    deadline = time.monotonic() + 3.0
    while not ready and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)

    assert ready == [path]
    pixmap = service.peek(path, size)
    assert pixmap is not None
    assert not pixmap.isNull()
    assert thumbnail_cache_file(tmp_path / "thumbs", path, (64, 64)).exists()


def test_l2_hit_for_scan_written_512_thumbnail_does_not_enqueue_generation(
    tmp_path: Path,
    qapp,
) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    path = tmp_path / "photo.jpg"
    path.write_bytes(b"image")
    size = QSize(512, 512)
    disk_file = thumbnail_cache_file(tmp_path / "thumbs", path, (512, 512))
    disk_file.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (512, 512), "red").save(disk_file, format="JPEG")

    with patch.object(service, "_queue_generation") as queue_generation:
        pixmap = service.get_thumbnail(path, size)

    assert pixmap is not None
    assert not pixmap.isNull()
    queue_generation.assert_not_called()

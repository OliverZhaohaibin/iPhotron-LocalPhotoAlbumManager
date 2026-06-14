from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

import pytest
pytest.importorskip("PySide6", reason="PySide6 is required for thumbnail tests", exc_type=ImportError)
from PIL import Image
from PySide6.QtCore import QSize
from PySide6.QtGui import QImage

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


def test_l2_hit_is_not_read_synchronously_from_get_thumbnail(
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

    assert pixmap is None
    queue_generation.assert_called_once()


def test_worker_loads_l2_hit_without_rendering_source(tmp_path: Path) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    path = tmp_path / "photo.jpg"
    size = QSize(512, 512)
    disk_file = thumbnail_cache_file(tmp_path / "thumbs", path, (512, 512))
    disk_file.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (512, 512), "red").save(disk_file, format="JPEG")

    with patch.object(service, "_render_thumbnail") as render:
        image = service._load_or_render_thumbnail(path, size)

    assert image is not None
    assert not image.isNull()
    render.assert_not_called()


def test_peek_full_thumbnail_never_touches_disk(tmp_path: Path) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")

    with patch.object(Path, "exists", side_effect=AssertionError("disk access")):
        assert service.peek_full_thumbnail(tmp_path / "photo.jpg", QSize(512, 512)) is None


def test_reentered_pending_thumbnail_promotes_generation(tmp_path: Path) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    service._max_active_jobs = 0
    path = tmp_path / "photo.jpg"
    size = QSize(512, 512)

    service.request_many([(path, size, "low")], generation=1)
    service.request_many([(path, size, "visible")], generation=9)

    key = service._cache_key(path, size)
    assert service._pending_generations[key] == 9
    assert service._queued_tasks[key][2:] == ("visible", 9)


def test_reconcile_demand_keeps_only_latest_visible_and_hot_queue(tmp_path: Path) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    service._max_active_jobs = 0
    size = QSize(512, 512)
    first = tmp_path / "first.jpg"
    second = tmp_path / "second.jpg"
    third = tmp_path / "third.jpg"

    service.reconcile_demand(
        visible_paths=[first],
        hot_paths=[second, third],
        size=size,
        generation=1,
    )
    service.reconcile_demand(
        visible_paths=[second],
        hot_paths=[],
        size=size,
        generation=2,
    )

    first_key = service._cache_key(first, size)
    second_key = service._cache_key(second, size)
    third_key = service._cache_key(third, size)
    assert set(service._queued_tasks) == {second_key}
    assert first_key not in service._pending_tasks
    assert third_key not in service._pending_tasks
    assert service._queued_tasks[second_key][2:] == ("visible", 2)
    assert service._pinned_keys == {second_key}


def test_stale_worker_result_is_discarded_before_pixmap_conversion(tmp_path: Path) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    path = tmp_path / "photo.jpg"
    size = QSize(512, 512)
    key = service._cache_key(path, size)
    service._current_generation = 9
    service._pending_tasks.add(key)
    service._pending_generations[key] = 1
    service._active_tasks = 1
    image = QImage(8, 8, QImage.Format.Format_ARGB32_Premultiplied)

    with patch.object(service, "_add_to_memory") as add_to_memory:
        service._handle_generation_result(path, size, image, generation=1)

    add_to_memory.assert_not_called()
    assert key not in service._pending_tasks


def test_promoted_active_failure_retries_current_generation(tmp_path: Path) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    service._max_active_jobs = 0
    path = tmp_path / "photo.jpg"
    size = QSize(512, 512)
    key = service._cache_key(path, size)
    service._current_generation = 9
    service._pending_tasks.add(key)
    service._pending_generations[key] = 9
    service._active_tasks = 1

    service._handle_generation_failure(path, size, "old failure", generation=1)

    assert service._queued_tasks[key][2:] == ("visible", 9)
    assert key not in service._failure_until

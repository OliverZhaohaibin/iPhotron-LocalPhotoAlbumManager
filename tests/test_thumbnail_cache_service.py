from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import threading
import time
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
pytest.importorskip("PySide6", reason="PySide6 is required for thumbnail tests", exc_type=ImportError)
from PIL import Image
from PySide6.QtCore import QFile, QSize
from PySide6.QtGui import QImage, QPixmap

from iPhoto.infrastructure.services.thumbnail_cache_keys import thumbnail_cache_file
from iPhoto.infrastructure.services.thumbnail_cache_service import (
    ThumbnailCacheService,
    ThumbnailGenerationTask,
    ThumbnailLoadResult,
    ThumbnailPrefetchCandidate,
    ThumbnailRequest,
    ThumbnailRequestKind,
    ThumbnailWorkerSignals,
    _CancellationToken,
)
from iPhoto.infrastructure.services.thumbnail_runtime_policy import ThumbnailRuntimePolicy


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
    old_key = service._disk_cache_key(old_photo)
    new_key = service._disk_cache_key(new_photo)
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
    with patch.object(service, "_queue_visible") as queue_generation:
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

    with patch.object(service, "_queue_visible") as queue_generation:
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

    with patch.object(service, "_queue_visible") as queue_generation:
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

    service.request_many(
        [ThumbnailRequest(path, size, ThumbnailRequestKind.VISIBLE, 1)],
        generation=1,
    )
    service.request_many(
        [ThumbnailRequest(path, size, ThumbnailRequestKind.VISIBLE, 9)],
        generation=9,
    )

    key = service._cache_key(path, size)
    assert service._pending_generations[key] == 9
    assert service._queued_tasks[key].kind is ThumbnailRequestKind.VISIBLE
    assert service._queued_tasks[key].generation == 9


def test_reconcile_demand_keeps_only_latest_visible_and_prefetch_queue(tmp_path: Path) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    service._max_active_jobs = 0
    size = QSize(512, 512)
    first = tmp_path / "first.jpg"
    second = tmp_path / "second.jpg"
    third = tmp_path / "third.jpg"

    with patch.object(service, "_start_generation"):
        service.reconcile_demand(
            visible_paths=[],
            prefetch_paths=[second, third],
            size=size,
            generation=1,
        )
        service.reconcile_demand(
            visible_paths=[second],
            prefetch_paths=[],
            size=size,
            generation=2,
        )

    first_key = service._cache_key(first, size)
    second_key = service._cache_key(second, size)
    third_key = service._cache_key(third, size)
    assert set(service._queued_tasks) == set()
    assert first_key not in service._pending_tasks
    assert third_key not in service._prefetch_pending
    assert second_key in service._prefetch_promoted_visible
    assert second_key in service._pending_tasks
    assert service._pending_generations[second_key] == 2
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

    assert service._queued_tasks[key].kind is ThumbnailRequestKind.VISIBLE
    assert service._queued_tasks[key].generation == 9
    assert key not in service._failure_until


def test_prefetch_uses_separate_pool_and_never_enters_foreground_queue(tmp_path: Path) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    service._max_active_jobs = 0
    path = tmp_path / "prefetch.jpg"
    size = QSize(512, 512)

    with patch.object(service, "_start_generation") as start_generation:
        service.reconcile_demand(
            visible_paths=[],
            prefetch_paths=[path],
            size=size,
            generation=1,
        )

    key = service._cache_key(path, size)
    assert key not in service._pending_tasks
    assert key not in service._queued_tasks
    assert key in service._prefetch_pending
    assert start_generation.call_args.kwargs["kind"] is ThumbnailRequestKind.PREFETCH


def test_visible_request_promotes_active_prefetch_without_canceling_it(tmp_path: Path) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    service._max_active_jobs = 0
    path = tmp_path / "photo.jpg"
    size = QSize(512, 512)
    key = service._cache_key(path, size)
    token = service._prefetch_active_tokens[key] = _CancellationToken()
    service._prefetch_pending.add(key)
    service._prefetch_active_tasks = 1

    service.request_many(
        [ThumbnailRequest(path, size, ThumbnailRequestKind.VISIBLE, 2)],
        generation=2,
    )

    assert not token.cancelled()
    assert key in service._prefetch_promoted_visible
    assert key in service._pending_tasks
    assert key not in service._queued_tasks


def test_visible_request_does_not_promote_already_canceled_prefetch(tmp_path: Path) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    service._max_active_jobs = 0
    path = tmp_path / "photo.jpg"
    size = QSize(512, 512)
    key = service._cache_key(path, size)
    token = service._prefetch_active_tokens[key] = _CancellationToken()
    token.cancel()
    service._prefetch_pending.add(key)
    service._prefetch_active_tasks = 1

    service.request_many(
        [ThumbnailRequest(path, size, ThumbnailRequestKind.VISIBLE, 2)],
        generation=2,
    )

    assert key not in service._prefetch_promoted_visible
    assert service._queued_tasks[key].kind is ThumbnailRequestKind.VISIBLE


def test_visible_miss_does_not_pause_unrelated_active_prefetch(tmp_path: Path) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    service._max_active_jobs = 0
    prefetch_path = tmp_path / "prefetch.jpg"
    visible_path = tmp_path / "visible.jpg"
    size = QSize(512, 512)
    prefetch_key = service._cache_key(prefetch_path, size)
    token = service._prefetch_active_tokens[prefetch_key] = _CancellationToken()
    service._prefetch_pending.add(prefetch_key)
    service._prefetch_active_tasks = 1

    service.request_many(
        [ThumbnailRequest(visible_path, size, ThumbnailRequestKind.VISIBLE, 2)],
        generation=2,
    )

    assert not token.cancelled()


def test_prefetch_waits_while_visible_work_is_queued(tmp_path: Path) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    service._max_active_jobs = 0
    visible_path = tmp_path / "visible.jpg"
    prefetch_path = tmp_path / "prefetch.jpg"
    size = QSize(512, 512)

    with patch.object(service, "_start_generation") as start_generation:
        service.reconcile_demand(
            visible_paths=[visible_path],
            prefetch_paths=[prefetch_path],
            prefetch_candidates=[
                ThumbnailPrefetchCandidate(prefetch_path, "prefetch-key", "far_speculative")
            ],
            size=size,
            generation=1,
        )

    assert service._pending_tasks
    assert service._prefetch_active_tasks == 0
    start_generation.assert_not_called()


def test_promoted_prefetch_hit_updates_visible_tile_without_duplicate_worker(
    tmp_path: Path,
    qapp,
) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    path = tmp_path / "photo.jpg"
    size = QSize(512, 512)
    key = service._cache_key(path, size)
    token = service._prefetch_active_tokens[key] = _CancellationToken()
    service._prefetch_pending.add(key)
    service._prefetch_generations[key] = 1
    service._prefetch_active_tasks = 1
    emitted = []
    service.thumbnailReady.connect(emitted.append)

    with patch.object(service, "_start_generation") as start_generation:
        service.request_many(
            [ThumbnailRequest(path, size, ThumbnailRequestKind.VISIBLE, 2)],
            generation=2,
        )
        service._handle_prefetch_result(
            path,
            size,
            QImage(8, 8, QImage.Format.Format_ARGB32_Premultiplied),
            generation=1,
        )
        service._drain_publish_queue()

    assert not token.cancelled()
    start_generation.assert_not_called()
    assert key in service._memory_cache
    assert key not in service._pending_tasks
    assert emitted == [path]


def test_promoted_prefetch_miss_falls_back_to_foreground_generation(tmp_path: Path) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    service._max_active_jobs = 0
    path = tmp_path / "photo.jpg"
    size = QSize(512, 512)
    key = service._cache_key(path, size)
    service._prefetch_active_tokens[key] = _CancellationToken()
    service._prefetch_pending.add(key)
    service._prefetch_generations[key] = 1
    service._prefetch_active_tasks = 1

    service.request_many(
        [ThumbnailRequest(path, size, ThumbnailRequestKind.VISIBLE, 2)],
        generation=2,
    )
    service._handle_prefetch_failure(path, size, "empty_render", generation=1)

    assert key not in service._prefetch_promoted_visible
    assert key in service._pending_tasks
    assert service._queued_tasks[key].kind is ThumbnailRequestKind.VISIBLE
    assert service._queued_tasks[key].generation == 2


def test_overlapping_active_prefetch_survives_new_generation(tmp_path: Path) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    service._max_active_jobs = 0
    path = tmp_path / "prefetch.jpg"
    size = QSize(512, 512)
    key = service._cache_key(path, size)
    token = service._prefetch_active_tokens[key] = _CancellationToken()
    service._prefetch_pending.add(key)
    service._prefetch_generations[key] = 1
    service._prefetch_active_tasks = 1

    service.reconcile_demand(
        visible_paths=[],
        prefetch_paths=[path],
        size=size,
        generation=2,
    )

    assert not token.cancelled()
    assert service._prefetch_generations[key] == 2


def test_active_prefetch_is_canceled_after_leaving_latest_demand(tmp_path: Path) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    service._max_active_jobs = 0
    path = tmp_path / "prefetch.jpg"
    size = QSize(512, 512)
    key = service._cache_key(path, size)
    token = service._prefetch_active_tokens[key] = _CancellationToken()
    service._prefetch_pending.add(key)
    service._prefetch_generations[key] = 1
    service._prefetch_active_tasks = 1

    service.reconcile_demand(
        visible_paths=[],
        prefetch_paths=[],
        size=size,
        generation=2,
    )

    assert token.cancelled()


def test_prefetch_l2_miss_never_renders_source(tmp_path: Path) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    path = tmp_path / "missing-cache.jpg"
    size = QSize(512, 512)

    with patch.object(service, "_render_thumbnail") as render:
        image = service._load_cached_thumbnail_only(path, size)

    assert image is None
    render.assert_not_called()


def test_prefetch_l2_miss_uses_short_retry_ttl(tmp_path: Path) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    service._max_active_jobs = 0
    path = tmp_path / "missing-cache.jpg"
    size = QSize(512, 512)
    key = service._cache_key(path, size)

    token = service._prefetch_active_tokens[key] = _CancellationToken()
    token.l2_outcome = "miss"
    service._prefetch_pending.add(key)
    service._prefetch_active_tasks = 1
    service._handle_prefetch_failure(path, size, "empty_render", generation=1)
    service._queue_prefetch(
        ThumbnailRequest(path, size, ThumbnailRequestKind.PREFETCH, generation=2)
    )

    assert key in service._prefetch_l2_miss_until
    assert service._prefetch_l2_miss_until[key] - time.monotonic() > 1.0
    assert key not in service._prefetch_pending
    service._prefetch_l2_miss_until[key] = time.monotonic() - 1.0
    service._queue_prefetch(
        ThumbnailRequest(path, size, ThumbnailRequestKind.PREFETCH, generation=2)
    )
    assert key in service._prefetch_pending


def test_predictive_l2_miss_uses_shorter_retry_ttl(tmp_path: Path) -> None:
    policy = replace(
        ThumbnailRuntimePolicy.detect(platform="win32", windows_probe=lambda: 8 * 1024**3),
        predictive_miss_ttl_seconds=0.5,
        prefetch_miss_ttl_seconds=2.0,
    )
    service = ThumbnailCacheService(tmp_path / "thumbs", runtime_policy=policy)
    path = tmp_path / "missing-predictive.jpg"
    size = QSize(512, 512)
    key = service._cache_key(path, size)

    token = service._prefetch_active_tokens[key] = _CancellationToken()
    token.l2_outcome = "miss"
    service._prefetch_pending.add(key)
    service._prefetch_active_tasks = 1
    service._predictive_active_tasks = 1

    service._handle_prefetch_failure(
        path,
        size,
        "empty_render",
        generation=1,
        kind=ThumbnailRequestKind.PREDICTIVE,
    )

    ttl = service._prefetch_l2_miss_until[key] - time.monotonic()
    assert 0.0 < ttl <= 0.75


def test_prefetch_result_is_cached_without_thumbnail_ready_signal(tmp_path: Path, qapp) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    path = tmp_path / "prefetch.jpg"
    size = QSize(512, 512)
    key = service._cache_key(path, size)
    service._prefetch_active_tokens[key] = _CancellationToken()
    service._prefetch_pending.add(key)
    service._prefetch_generations[key] = 1
    service._prefetch_active_tasks = 1
    service._prefetch_key_order = [key]
    image = QImage(8, 8, QImage.Format.Format_ARGB32_Premultiplied)
    emitted = []
    service.thumbnailReady.connect(emitted.append)

    service._handle_prefetch_result(path, size, image, generation=1)
    service._drain_publish_queue()

    assert key in service._memory_cache
    assert emitted == []


def test_existing_l2_prefetch_streams_into_memory_without_ui_update(
    tmp_path: Path,
    qapp,
) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    path = tmp_path / "prefetch.jpg"
    size = QSize(512, 512)
    disk_file = thumbnail_cache_file(tmp_path / "thumbs", path, (512, 512))
    disk_file.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (512, 512), "red").save(disk_file, format="JPEG")
    emitted = []
    service.thumbnailReady.connect(emitted.append)

    service.reconcile_demand(
        visible_paths=[],
        prefetch_paths=[path],
        size=size,
        generation=1,
    )
    deadline = time.monotonic() + 2.0
    while not service.has_full_thumbnail(path, size) and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)

    assert service.has_full_thumbnail(path, size)
    assert emitted == []


def test_slow_l2_prefetch_survives_visible_work_and_new_generation(
    tmp_path: Path,
    qapp,
) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    prefetch_path = tmp_path / "prefetch.jpg"
    visible_path = tmp_path / "visible.jpg"
    size = QSize(512, 512)
    started = threading.Event()
    release = threading.Event()
    image = QImage(8, 8, QImage.Format.Format_ARGB32_Premultiplied)

    def _slow_l2_load(_path, _size, cancellation):
        started.set()
        release.wait(timeout=2.0)
        assert not cancellation.cancelled()
        return image

    with patch.object(service, "_load_cached_thumbnail_only", side_effect=_slow_l2_load):
        service.reconcile_demand(
            visible_paths=[],
            prefetch_paths=[prefetch_path],
            size=size,
            generation=1,
        )
        assert started.wait(timeout=1.0)
        token = service._prefetch_active_tokens[service._cache_key(prefetch_path, size)]

        service.reconcile_demand(
            visible_paths=[visible_path],
            prefetch_paths=[prefetch_path],
            size=size,
            generation=2,
        )
        assert not token.cancelled()
        release.set()

        deadline = time.monotonic() + 2.0
        while not service.has_full_thumbnail(prefetch_path, size) and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.01)

    assert service.has_full_thumbnail(prefetch_path, size)


def test_memory_pressure_evicts_farthest_prefetch_before_visible(
    tmp_path: Path,
    qapp,
) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    size = QSize(8, 8)
    visible = tmp_path / "visible.jpg"
    near = tmp_path / "near.jpg"
    far = tmp_path / "far.jpg"
    incoming = tmp_path / "incoming.jpg"
    visible_key = service._cache_key(visible, size)
    near_key = service._cache_key(near, size)
    far_key = service._cache_key(far, size)
    incoming_key = service._cache_key(incoming, size)
    pixmap = QPixmap(8, 8)
    service._memory_limit_bytes = 10_000
    service._pinned_keys = {visible_key}
    service._prefetch_key_order = [near_key, far_key]
    for key in (visible_key, near_key, far_key):
        service._add_to_memory(key, pixmap)
    service._memory_limit_bytes = 3 * 8 * 8 * 4

    service._add_to_memory(incoming_key, pixmap)

    assert visible_key in service._memory_cache
    assert near_key in service._memory_cache
    assert far_key not in service._memory_cache


def test_windows_l1_refresh_replaces_old_pages_when_saturated(
    tmp_path: Path,
    qapp,
) -> None:
    policy = replace(
        ThumbnailRuntimePolicy.detect(platform="win32", windows_probe=lambda: 16 * 1024**3),
        memory_limit_bytes=5 * 8 * 8 * 4,
        l1_replacement_threshold_ratio=0.90,
        l1_replacement_target_ratio=0.72,
    )
    service = ThumbnailCacheService(tmp_path / "thumbs", runtime_policy=policy)
    size = QSize(8, 8)
    visible = tmp_path / "visible.jpg"
    near = tmp_path / "near.jpg"
    future = tmp_path / "future.jpg"
    old_paths = [tmp_path / f"old-{index}.jpg" for index in range(3)]
    visible_key = service._cache_key(visible, size)
    near_key = service._cache_key(near, size)
    future_key = service._cache_key(future, size)
    old_keys = [service._cache_key(path, size) for path in old_paths]
    pixmap = QPixmap(8, 8)

    for key in (*old_keys, visible_key, near_key):
        service._add_to_memory(key, pixmap)

    assert service._memory_used_bytes == policy.memory_limit_bytes

    with patch.object(service, "_start_generation"):
        service.reconcile_demand(
            visible_paths=[visible],
            prefetch_paths=[near, future],
            size=size,
            generation=1,
            phase="slow",
            intent="slow_continuous",
            l1_demand_complete=True,
        )

    assert visible_key in service._memory_cache
    assert near_key in service._memory_cache
    assert any(key not in service._memory_cache for key in old_keys)
    assert future_key in service._prefetch_pending
    assert service._memory_used_bytes <= int(
        policy.memory_limit_bytes * policy.l1_replacement_target_ratio
    )


def test_windows_l1_refresh_replaces_old_pages_before_saturation(
    tmp_path: Path,
    qapp,
) -> None:
    tile_bytes = 8 * 8 * 4
    policy = replace(
        ThumbnailRuntimePolicy.detect(platform="win32", windows_probe=lambda: 16 * 1024**3),
        memory_limit_bytes=100 * tile_bytes,
        l1_replacement_threshold_ratio=0.90,
        l1_replacement_target_ratio=0.72,
    )
    service = ThumbnailCacheService(tmp_path / "thumbs", runtime_policy=policy)
    size = QSize(8, 8)
    visible = tmp_path / "visible.jpg"
    near = tmp_path / "near.jpg"
    future = tmp_path / "future.jpg"
    old_paths = [tmp_path / f"old-{index}.jpg" for index in range(20)]
    visible_key = service._cache_key(visible, size)
    near_key = service._cache_key(near, size)
    old_keys = [service._cache_key(path, size) for path in old_paths]
    pixmap = QPixmap(8, 8)

    for key in (*old_keys, visible_key, near_key):
        service._add_to_memory(key, pixmap)

    assert service._memory_used_bytes < int(
        policy.memory_limit_bytes * policy.l1_replacement_threshold_ratio
    )

    with patch.object(service, "_start_generation"):
        service.reconcile_demand(
            visible_paths=[visible],
            prefetch_paths=[near, future],
            size=size,
            generation=1,
            phase="slow",
            intent="slow_continuous",
            l1_demand_complete=True,
        )

    assert visible_key in service._memory_cache
    assert near_key in service._memory_cache
    assert all(key not in service._memory_cache for key in old_keys)


def test_partial_l1_demand_preserves_preheated_next_page(
    tmp_path: Path,
    qapp,
) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    size = QSize(8, 8)
    visible = tmp_path / "visible.jpg"
    next_paths = [tmp_path / f"next-{index}.jpg" for index in range(6)]
    next_keys = [service._cache_key(path, size) for path in next_paths]
    pixmap = QPixmap(8, 8)

    for key in next_keys:
        service._add_to_memory(key, pixmap)

    with patch.object(service, "_start_generation"):
        service.reconcile_demand(
            visible_paths=[visible],
            prefetch_paths=[],
            size=size,
            generation=1,
            phase="slow",
            intent="slow_continuous",
            l1_demand_complete=False,
        )

    assert all(key in service._memory_cache for key in next_keys)


def test_complete_l1_demand_prunes_preheated_old_viewpoint(
    tmp_path: Path,
    qapp,
) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    size = QSize(8, 8)
    visible = tmp_path / "visible.jpg"
    near = tmp_path / "near.jpg"
    old_paths = [tmp_path / f"old-{index}.jpg" for index in range(6)]
    visible_key = service._cache_key(visible, size)
    near_key = service._cache_key(near, size)
    old_keys = [service._cache_key(path, size) for path in old_paths]
    pixmap = QPixmap(8, 8)

    for key in old_keys:
        service._add_to_memory(key, pixmap)

    with patch.object(service, "_start_generation"):
        service.reconcile_demand(
            visible_paths=[visible],
            prefetch_paths=[near],
            size=size,
            generation=1,
            phase="slow",
            intent="slow_continuous",
            l1_demand_complete=True,
        )

    service._add_to_memory(visible_key, pixmap)
    service._add_to_memory(near_key, pixmap)

    assert visible_key in service._memory_cache
    assert near_key in service._memory_cache
    assert all(key not in service._memory_cache for key in old_keys)


def test_l1_rejects_stale_thumbnail_writes_outside_current_demand(
    tmp_path: Path,
    qapp,
) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    service._max_active_jobs = 0
    size = QSize(8, 8)
    visible = tmp_path / "visible.jpg"
    stale = tmp_path / "stale.jpg"
    stale_key = service._cache_key(stale, size)

    with patch.object(service, "_start_generation"):
        service.reconcile_demand(
            visible_paths=[visible],
            prefetch_paths=[],
            size=size,
            generation=1,
            phase="slow",
            intent="slow_continuous",
            l1_demand_complete=True,
        )

    service._add_to_memory(stale_key, QPixmap(8, 8))

    assert stale_key not in service._memory_cache
    assert stale_key not in service._memory_bytes


def test_l2_reader_opens_once_without_exists_or_read_bytes(tmp_path: Path) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    path = tmp_path / "photo.jpg"
    size = QSize(512, 512)
    disk_file = thumbnail_cache_file(tmp_path / "thumbs", path, (512, 512))
    disk_file.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 32), "red").save(disk_file, format="JPEG")

    with (
        patch.object(Path, "exists", side_effect=AssertionError("exists called")),
        patch.object(Path, "read_bytes", side_effect=AssertionError("read_bytes called")),
        patch(
            "iPhoto.infrastructure.services.thumbnail_cache_service.QFile",
            wraps=QFile,
        ) as qfile,
    ):
        image = service._load_cached_thumbnail_only(path, size)

    assert image is not None and not image.isNull()
    assert qfile.call_count == 1


def test_l2_512_file_decodes_directly_to_display_bucket_without_new_disk_file(
    tmp_path: Path,
) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    path = tmp_path / "photo.jpg"
    disk_file = thumbnail_cache_file(tmp_path / "thumbs", path, (512, 512))
    disk_file.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (512, 512), "red").save(disk_file, format="JPEG")

    image = service._load_cached_thumbnail_only(path, QSize(256, 256))

    assert image is not None
    assert image.size() == QSize(256, 256)
    assert not thumbnail_cache_file(tmp_path / "thumbs", path, (256, 256)).exists()


def test_known_l2_cache_key_drives_predictive_request(tmp_path: Path) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    path = tmp_path / "photo.jpg"
    size = QSize(256, 256)

    with patch.object(service, "_start_generation") as start_generation:
        service.reconcile_demand(
            visible_paths=[],
            prefetch_paths=[path],
            prefetch_candidates=[
                ThumbnailPrefetchCandidate(path, "known-l2-key", "predictive")
            ],
            size=size,
            generation=1,
            phase="settled",
            intent="directional_dwell",
        )

    assert start_generation.call_args.kwargs["kind"] is ThumbnailRequestKind.PREDICTIVE
    assert start_generation.call_args.kwargs["l2_cache_key"] == "known-l2-key"


def test_only_far_speculative_enters_windows_background_mode(tmp_path: Path) -> None:
    del tmp_path
    entered = []

    @contextmanager
    def _background_mode(platform):
        entered.append(platform)
        yield

    image = QImage(8, 8, QImage.Format.Format_ARGB32_Premultiplied)
    signals = ThumbnailWorkerSignals()
    with patch(
        "iPhoto.infrastructure.services.thumbnail_cache_service.speculative_thread_background_mode",
        side_effect=_background_mode,
    ):
        ThumbnailGenerationTask(
            lambda _path, _size, _token: image,
            Path("predictive.jpg"),
            QSize(256, 256),
            signals,
            1,
            ThumbnailRequestKind.PREDICTIVE,
            "win32",
            _CancellationToken(),
        ).run()
        ThumbnailGenerationTask(
            lambda _path, _size, _token: image,
            Path("far.jpg"),
            QSize(256, 256),
            signals,
            1,
            ThumbnailRequestKind.PREFETCH,
            "win32",
            _CancellationToken(),
        ).run()

    assert entered == ["", "win32"]


def test_predictive_prefetch_starts_two_lanes_without_sample_warmup(tmp_path: Path) -> None:
    policy = replace(
        ThumbnailRuntimePolicy.detect(platform="linux", sysconf=lambda _name: 4096),
        prefetch_sample_size=2,
        prefetch_max_workers=2,
    )
    service = ThumbnailCacheService(tmp_path / "thumbs", runtime_policy=policy)
    size = QSize(512, 512)
    paths = [tmp_path / f"{index}.jpg" for index in range(3)]

    with patch.object(service, "_start_generation") as start_generation:
        for path in paths:
            service._queue_prefetch(
                ThumbnailRequest(path, size, ThumbnailRequestKind.PREDICTIVE, generation=0)
            )

    assert service._prefetch_active_tasks == 2
    assert start_generation.call_count == 2


def test_slow_l2_samples_do_not_throttle_predictive_deadline_coverage(tmp_path: Path) -> None:
    policy = replace(
        ThumbnailRuntimePolicy.detect(platform="win32", windows_probe=lambda: 8 * 1024**3),
        prefetch_sample_size=2,
    )
    service = ThumbnailCacheService(tmp_path / "thumbs", runtime_policy=policy)
    service._prefetch_l2_elapsed_ms.extend([50.0, 60.0])
    service._prefetch_l2_cancelled.extend([False, False])

    service._prefetch_queued["predictive"] = ThumbnailRequest(
        tmp_path / "predictive.jpg",
        QSize(512, 512),
        ThumbnailRequestKind.PREDICTIVE,
        generation=0,
    )

    assert service._prefetch_concurrency_target() == 2
    assert service._prefetch_backoff_until == 0.0


def test_visible_queue_wait_immediately_pauses_predictive_work(tmp_path: Path) -> None:
    policy = replace(
        ThumbnailRuntimePolicy.detect(platform="win32", windows_probe=lambda: 8 * 1024**3),
        visible_queue_wait_p95_ms=12.0,
    )
    service = ThumbnailCacheService(tmp_path / "thumbs", runtime_policy=policy)
    service._record_visible_queue_wait(14.0)
    service._record_visible_queue_wait(18.0)

    assert service._prefetch_concurrency_target() == 0
    assert service._prefetch_backoff_until > time.monotonic()


def test_light_visible_queue_keeps_one_predictive_lane(tmp_path: Path) -> None:
    policy = replace(
        ThumbnailRuntimePolicy.detect(platform="win32", windows_probe=lambda: 8 * 1024**3),
        visible_queue_wait_p95_ms=12.0,
    )
    service = ThumbnailCacheService(tmp_path / "thumbs", runtime_policy=policy)
    service._record_visible_queue_wait(2.0)
    service._record_visible_queue_wait(4.0)
    service._queued_tasks["visible"] = ThumbnailRequest(
        tmp_path / "visible.jpg",
        QSize(512, 512),
        ThumbnailRequestKind.VISIBLE,
        generation=1,
    )
    service._prefetch_queued["predictive"] = ThumbnailRequest(
        tmp_path / "predictive.jpg",
        QSize(512, 512),
        ThumbnailRequestKind.PREDICTIVE,
        generation=1,
    )

    assert service._prefetch_concurrency_target() == 1


def test_windows_recovery_visible_queue_keeps_two_predictive_lanes(tmp_path: Path) -> None:
    policy = replace(
        ThumbnailRuntimePolicy.detect(platform="win32", windows_probe=lambda: 8 * 1024**3),
        prefetch_max_workers=4,
        recovery_predictive_workers=4,
        visible_queue_wait_p95_ms=12.0,
    )
    service = ThumbnailCacheService(tmp_path / "thumbs", runtime_policy=policy)
    service._current_recovery = True
    service._record_visible_queue_wait(2.0)
    service._queued_tasks["visible"] = ThumbnailRequest(
        tmp_path / "visible.jpg",
        QSize(512, 512),
        ThumbnailRequestKind.VISIBLE,
        generation=1,
    )
    service._prefetch_queued["predictive-a"] = ThumbnailRequest(
        tmp_path / "predictive-a.jpg",
        QSize(512, 512),
        ThumbnailRequestKind.PREDICTIVE,
        generation=1,
    )
    service._prefetch_queued["predictive-b"] = ThumbnailRequest(
        tmp_path / "predictive-b.jpg",
        QSize(512, 512),
        ThumbnailRequestKind.PREDICTIVE,
        generation=1,
    )

    assert service._prefetch_concurrency_target() == 2


def test_stale_visible_queue_wait_does_not_permanently_pause_predictive_work(
    tmp_path: Path,
) -> None:
    policy = replace(
        ThumbnailRuntimePolicy.detect(platform="win32", windows_probe=lambda: 8 * 1024**3),
        visible_queue_wait_p95_ms=12.0,
    )
    service = ThumbnailCacheService(tmp_path / "thumbs", runtime_policy=policy)
    service._visible_queue_wait_ms.append((time.monotonic() - 60.0, 48.0))
    service._prefetch_queued["predictive"] = ThumbnailRequest(
        tmp_path / "predictive.jpg",
        QSize(512, 512),
        ThumbnailRequestKind.PREDICTIVE,
        generation=1,
    )

    assert service._prefetch_concurrency_target() == 2
    assert list(service._visible_queue_wait_ms) == []


def test_slow_demand_after_burst_clears_backoff_and_drains_predictive_prefetch(
    tmp_path: Path,
) -> None:
    policy = replace(
        ThumbnailRuntimePolicy.detect(platform="win32", windows_probe=lambda: 8 * 1024**3),
        prefetch_max_workers=2,
        visible_queue_wait_p95_ms=12.0,
    )
    service = ThumbnailCacheService(tmp_path / "thumbs", runtime_policy=policy)
    size = QSize(512, 512)
    visible = tmp_path / "visible.jpg"
    ahead = tmp_path / "ahead.jpg"
    service._current_phase = "fast"
    service._current_intent = "continuous_burst"
    service._prefetch_backoff_until = time.monotonic() + 60.0
    service._record_visible_queue_wait(48.0)

    with patch.object(service, "_start_generation") as start_generation:
        service.reconcile_demand(
            visible_paths=[visible],
            prefetch_paths=[ahead],
            prefetch_candidates=[
                ThumbnailPrefetchCandidate(ahead, "ahead-l2-key", "predictive")
            ],
            size=size,
            generation=2,
            phase="slow",
            intent="slow_continuous",
        )

    assert service._prefetch_backoff_until == 0.0
    assert list(service._visible_queue_wait_ms) == []
    assert any(
        call.kwargs.get("kind") is ThumbnailRequestKind.PREDICTIVE
        for call in start_generation.call_args_list
    )


def test_strategic_prefetch_cancellation_does_not_poison_cancel_rate(
    tmp_path: Path,
) -> None:
    policy = replace(
        ThumbnailRuntimePolicy.detect(platform="win32", windows_probe=lambda: 8 * 1024**3),
        prefetch_sample_size=2,
        prefetch_max_workers=2,
    )
    service = ThumbnailCacheService(tmp_path / "thumbs", runtime_policy=policy)
    size = QSize(512, 512)
    path = tmp_path / "predictive.jpg"
    token = _CancellationToken()
    token.cancel("demand_replaced")

    with patch.object(
        service,
        "_read_cached_thumbnail",
        return_value=(None, "cancelled", 1.0),
    ):
        assert service._load_cached_thumbnail_only(path, size, token) is None

    service._prefetch_queued["predictive"] = ThumbnailRequest(
        path,
        size,
        ThumbnailRequestKind.PREDICTIVE,
        generation=1,
    )

    assert list(service._prefetch_l2_cancelled) == []
    assert service._prefetch_concurrency_target() == 2


def test_direction_reversal_cancels_old_predictive_even_when_range_overlaps(
    tmp_path: Path,
) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    size = QSize(256, 256)
    visible = tmp_path / "visible.jpg"
    old_ahead = tmp_path / "old-ahead.jpg"
    new_ahead = tmp_path / "new-ahead.jpg"
    old_key = service._cache_key(old_ahead, size)
    token = service._prefetch_active_tokens[old_key] = _CancellationToken()
    service._prefetch_pending.add(old_key)
    service._prefetch_generations[old_key] = 1
    service._prefetch_kinds[old_key] = ThumbnailRequestKind.PREDICTIVE
    service._prefetch_active_tasks = 1
    service._predictive_active_tasks = 1

    with patch.object(service, "_start_generation"):
        service.reconcile_demand(
            visible_paths=[visible],
            prefetch_paths=[new_ahead, old_ahead],
            prefetch_candidates=[
                ThumbnailPrefetchCandidate(new_ahead, "new", "predictive"),
                ThumbnailPrefetchCandidate(old_ahead, "old", "far_speculative", rank=1),
            ],
            size=size,
            generation=2,
            phase="settled",
            intent="directional_dwell",
        )

    assert token.cancelled()
    assert token.cancel_reason == "demand_replaced"
    with patch.object(service, "_start_generation") as start_generation:
        service._handle_prefetch_failure(
            old_ahead,
            size,
            "cancelled",
            generation=1,
            kind=ThumbnailRequestKind.PREDICTIVE,
        )

    assert old_key not in service._prefetch_pending
    assert all(call.args[1] != old_ahead for call in start_generation.call_args_list)


def test_fast_phase_cancels_and_discards_speculative_before_pixmap(
    tmp_path: Path,
    qapp,
) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    path = tmp_path / "prefetch.jpg"
    size = QSize(512, 512)
    key = service._cache_key(path, size)
    token = service._prefetch_active_tokens[key] = _CancellationToken()
    service._prefetch_pending.add(key)
    service._prefetch_active_tasks = 1
    service._prefetch_key_order = [key]
    service._stage_result(
        ThumbnailLoadResult(
            path,
            size,
            QImage(8, 8, QImage.Format.Format_ARGB32_Premultiplied),
            1,
            ThumbnailRequestKind.PREDICTIVE,
        )
    )

    service.reconcile_demand(
        visible_paths=[],
        prefetch_paths=[path],
        size=size,
        generation=2,
        phase="fast",
    )
    service._drain_publish_queue()

    assert token.cancelled()
    assert not service._publish_prefetch
    assert key not in service._memory_cache


def test_staging_publisher_prioritizes_visible_and_honors_item_budget(
    tmp_path: Path,
    qapp,
) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    size = QSize(8, 8)
    visible = tmp_path / "visible.jpg"
    first = tmp_path / "first.jpg"
    second = tmp_path / "second.jpg"
    first_key = service._cache_key(first, size)
    second_key = service._cache_key(second, size)
    visible_key = service._cache_key(visible, size)
    service._current_generation = 1
    service._prefetch_key_order = [first_key, second_key]
    image = QImage(8, 8, QImage.Format.Format_ARGB32_Premultiplied)
    for path, kind in (
        (first, ThumbnailRequestKind.PREFETCH),
        (second, ThumbnailRequestKind.PREFETCH),
        (visible, ThumbnailRequestKind.VISIBLE),
    ):
        service._stage_result(ThumbnailLoadResult(path, size, image, 1, kind))

    service._drain_publish_queue()

    assert visible_key in service._memory_cache
    assert first_key in service._memory_cache
    assert second_key not in service._memory_cache
    assert len(service._publish_prefetch) == 1


def test_older_generation_active_prefetch_still_caches_when_demand_matches(
    tmp_path: Path,
    qapp,
) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    path = tmp_path / "prefetch.jpg"
    size = QSize(8, 8)
    key = service._cache_key(path, size)
    service._current_generation = 7
    service._current_phase = "settled"
    service._current_intent = "idle"
    service._prefetch_key_order = [key]
    service._prefetch_active_tokens[key] = _CancellationToken()
    service._prefetch_pending.add(key)
    service._prefetch_generations[key] = 3
    service._prefetch_active_tasks = 1

    service._handle_prefetch_result(
        path,
        size,
        QImage(8, 8, QImage.Format.Format_ARGB32_Premultiplied),
        generation=3,
        kind=ThumbnailRequestKind.PREFETCH,
    )
    service._drain_publish_queue()

    assert key in service._memory_cache


def test_older_generation_staged_prefetch_still_caches_when_demand_matches(
    tmp_path: Path,
    qapp,
) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    path = tmp_path / "prefetch.jpg"
    size = QSize(8, 8)
    key = service._cache_key(path, size)
    service._current_generation = 7
    service._current_phase = "settled"
    service._current_intent = "idle"
    service._prefetch_key_order = [key]
    service._stage_result(
        ThumbnailLoadResult(
            path,
            size,
            QImage(8, 8, QImage.Format.Format_ARGB32_Premultiplied),
            3,
            ThumbnailRequestKind.PREFETCH,
        )
    )

    service._drain_publish_queue()

    assert key in service._memory_cache


def test_older_generation_visible_result_is_discarded(
    tmp_path: Path,
    qapp,
) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    path = tmp_path / "visible.jpg"
    size = QSize(8, 8)
    key = service._cache_key(path, size)
    service._current_generation = 7
    service._stage_result(
        ThumbnailLoadResult(
            path,
            size,
            QImage(8, 8, QImage.Format.Format_ARGB32_Premultiplied),
            3,
            ThumbnailRequestKind.VISIBLE,
        )
    )

    service._drain_publish_queue()

    assert key not in service._memory_cache


def test_older_prefetch_leaving_demand_is_still_discarded(
    tmp_path: Path,
    qapp,
) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    path = tmp_path / "prefetch.jpg"
    size = QSize(8, 8)
    key = service._cache_key(path, size)
    token = service._prefetch_active_tokens[key] = _CancellationToken()
    token.cancel("demand_replaced")
    service._prefetch_pending.add(key)
    service._prefetch_generations[key] = 3
    service._prefetch_active_tasks = 1

    service._handle_prefetch_result(
        path,
        size,
        QImage(8, 8, QImage.Format.Format_ARGB32_Premultiplied),
        generation=3,
        kind=ThumbnailRequestKind.PREFETCH,
    )
    service._drain_publish_queue()

    assert key not in service._memory_cache


def test_reentered_visible_result_is_reused_from_staging_without_duplicate_worker(
    tmp_path: Path,
) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    path = tmp_path / "visible.jpg"
    size = QSize(8, 8)
    service._stage_result(
        ThumbnailLoadResult(
            path,
            size,
            QImage(8, 8, QImage.Format.Format_ARGB32_Premultiplied),
            1,
            ThumbnailRequestKind.VISIBLE,
        )
    )

    with patch.object(service, "_start_generation") as start_generation:
        service.request_many(
            [ThumbnailRequest(path, size, ThumbnailRequestKind.VISIBLE, 2)],
            generation=2,
        )

    start_generation.assert_not_called()
    assert service._publish_visible[0].generation == 2


def test_overlapping_prefetch_result_is_reused_from_staging_without_duplicate_read(
    tmp_path: Path,
) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    path = tmp_path / "prefetch.jpg"
    size = QSize(8, 8)
    service._stage_result(
        ThumbnailLoadResult(
            path,
            size,
            QImage(8, 8, QImage.Format.Format_ARGB32_Premultiplied),
            1,
            ThumbnailRequestKind.PREFETCH,
        )
    )

    with patch.object(service, "_start_generation") as start_generation:
        service._queue_prefetch(
            ThumbnailRequest(path, size, ThumbnailRequestKind.PREFETCH, 2)
        )

    start_generation.assert_not_called()
    assert service._publish_prefetch[0].generation == 2


def test_staged_prefetch_entering_visible_is_promoted_without_duplicate_read(
    tmp_path: Path,
) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    path = tmp_path / "prefetch.jpg"
    size = QSize(8, 8)
    key = service._cache_key(path, size)
    service._current_generation = 1
    service._prefetch_key_order = [key]
    service._stage_result(
        ThumbnailLoadResult(
            path,
            size,
            QImage(8, 8, QImage.Format.Format_ARGB32_Premultiplied),
            1,
            ThumbnailRequestKind.PREFETCH,
        )
    )

    with patch.object(service, "_start_generation") as start_generation:
        service.reconcile_demand(
            visible_paths=[path],
            prefetch_paths=[],
            size=size,
            generation=2,
            phase="slow",
        )

    start_generation.assert_not_called()
    assert not service._publish_prefetch
    assert service._publish_visible[0].generation == 2
    assert key not in service._pending_tasks


def test_staging_publisher_honors_time_budget(tmp_path: Path, qapp) -> None:
    policy = replace(
        ThumbnailRuntimePolicy.detect(platform="darwin", sysconf=lambda _name: 4096),
        publish_max_items=5,
        publish_budget_ms=0.0,
    )
    service = ThumbnailCacheService(tmp_path / "thumbs", runtime_policy=policy)
    size = QSize(8, 8)
    paths = [tmp_path / f"{index}.jpg" for index in range(3)]
    service._current_generation = 1
    service._prefetch_key_order = [service._cache_key(path, size) for path in paths]
    image = QImage(8, 8, QImage.Format.Format_ARGB32_Premultiplied)
    for path in paths:
        service._stage_result(
            ThumbnailLoadResult(path, size, image, 1, ThumbnailRequestKind.PREFETCH)
        )

    service._drain_publish_queue()

    assert len(service._memory_cache) == 1
    assert len(service._publish_prefetch) == 2


def test_slow_publisher_uses_four_item_batch_budget(tmp_path: Path, qapp) -> None:
    policy = ThumbnailRuntimePolicy.detect(
        platform="linux",
        sysconf=lambda _name: 4096 * 2_097_152,
    )
    service = ThumbnailCacheService(tmp_path / "thumbs", runtime_policy=policy)
    service._current_generation = 1
    service._current_phase = "slow"
    service._current_intent = "slow_continuous"
    size = QSize(8, 8)
    paths = [tmp_path / f"{index}.jpg" for index in range(5)]
    service._prefetch_key_order = [service._cache_key(path, size) for path in paths]
    image = QImage(8, 8, QImage.Format.Format_ARGB32_Premultiplied)
    for path in paths:
        service._stage_result(
            ThumbnailLoadResult(path, size, image, 1, ThumbnailRequestKind.PREFETCH)
        )

    service._drain_publish_queue()

    assert len(service._memory_cache) == 4
    assert len(service._publish_prefetch) == 1


def test_l2_reader_distinguishes_miss_read_and_decode_errors(tmp_path: Path) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    missing = tmp_path / "missing.jpg"
    invalid = tmp_path / "invalid.jpg"
    invalid.write_bytes(b"not-an-image")

    _image, miss, _elapsed = service._read_cached_thumbnail(
        missing,
        path=missing,
        cancellation=None,
        tier="L2",
    )
    _image, decode_error, _elapsed = service._read_cached_thumbnail(
        invalid,
        path=invalid,
        cancellation=None,
        tier="L2",
    )
    with patch("iPhoto.infrastructure.services.thumbnail_cache_service.QFile") as qfile:
        qfile.return_value.open.return_value = False
        qfile.return_value.errorString.return_value = "Permission denied"
        _image, read_error, _elapsed = service._read_cached_thumbnail(
            invalid,
            path=invalid,
            cancellation=None,
            tier="L2",
        )

    assert miss == "miss"
    assert decode_error == "decode_error"
    assert read_error == "read_error"


def test_memory_pressure_evicts_old_demand_before_current_prefetch(
    tmp_path: Path,
    qapp,
) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    size = QSize(8, 8)
    old_key = service._cache_key(tmp_path / "old.jpg", size)
    near_key = service._cache_key(tmp_path / "near.jpg", size)
    far_key = service._cache_key(tmp_path / "far.jpg", size)
    incoming_key = service._cache_key(tmp_path / "incoming.jpg", size)
    pixmap = QPixmap(8, 8)
    service._memory_limit_bytes = 10_000
    service._prefetch_key_order = [near_key, far_key]
    for key in (old_key, near_key, far_key):
        service._add_to_memory(key, pixmap)
    service._memory_limit_bytes = 3 * 8 * 8 * 4

    service._add_to_memory(incoming_key, pixmap)

    assert old_key not in service._memory_cache
    assert near_key in service._memory_cache
    assert far_key in service._memory_cache


def test_prefetch_admission_respects_observed_pixmap_budget(
    tmp_path: Path,
    qapp,
) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    size = QSize(8, 8)
    visible = [tmp_path / "visible.jpg"]
    prefetch = [tmp_path / f"{index}.jpg" for index in range(4)]
    service._memory_limit_bytes = 2 * 8 * 8 * 4
    service._add_to_memory(service._cache_key(visible[0], size), QPixmap(8, 8))

    admitted = service._admit_prefetch_paths(visible, prefetch, size)

    assert admitted == prefetch[:1]


def test_prefetch_admission_always_keeps_predictive_deadline(
    tmp_path: Path,
    qapp,
) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    size = QSize(8, 8)
    visible = [tmp_path / "visible.jpg"]
    prefetch = [tmp_path / f"{index}.jpg" for index in range(4)]
    service._memory_limit_bytes = 2 * 8 * 8 * 4
    service._add_to_memory(service._cache_key(visible[0], size), QPixmap(8, 8))

    admitted = service._admit_prefetch_paths(
        visible,
        prefetch,
        size,
        predictive_limit=3,
    )

    assert admitted == prefetch[:3]


def test_far_staging_does_not_block_predictive_staging(tmp_path: Path) -> None:
    policy = replace(
        ThumbnailRuntimePolicy.detect(platform="win32", windows_probe=lambda: 8 * 1024**3),
        predictive_staging_limit=2,
        far_staging_limit=1,
        staging_limit=2,
    )
    service = ThumbnailCacheService(tmp_path / "thumbs", runtime_policy=policy)
    size = QSize(8, 8)
    far = tmp_path / "far.jpg"
    predictive = tmp_path / "predictive.jpg"
    image = QImage(8, 8, QImage.Format.Format_ARGB32_Premultiplied)
    service._stage_result(
        ThumbnailLoadResult(far, size, image, 1, ThumbnailRequestKind.PREFETCH)
    )

    service._stage_result(
        ThumbnailLoadResult(predictive, size, image, 1, ThumbnailRequestKind.PREDICTIVE)
    )

    assert len(service._publish_predictive) == 1
    assert service._publish_predictive[0].path == predictive


def test_windows_low_memory_pressure_clears_far_queue_and_staging(
    tmp_path: Path,
    qapp,
) -> None:
    tile_bytes = 8 * 8 * 4
    policy = replace(
        ThumbnailRuntimePolicy.detect(platform="win32", windows_probe=lambda: 8 * 1024**3),
        memory_limit_bytes=4 * tile_bytes,
        l1_replacement_threshold_ratio=0.90,
        windows_low_memory_target_ratio=0.60,
    )
    service = ThumbnailCacheService(tmp_path / "thumbs", runtime_policy=policy)
    size = QSize(8, 8)
    far = tmp_path / "far.jpg"
    predictive = tmp_path / "predictive.jpg"
    far_key = service._cache_key(far, size)
    predictive_key = service._cache_key(predictive, size)
    image = QImage(8, 8, QImage.Format.Format_ARGB32_Premultiplied)
    pixmap = QPixmap(8, 8)
    for index in range(4):
        service._add_to_memory(service._cache_key(tmp_path / f"old-{index}.jpg", size), pixmap)
    service._prefetch_queued[far_key] = ThumbnailRequest(
        far,
        size,
        ThumbnailRequestKind.PREFETCH,
        generation=1,
    )
    service._prefetch_queued[predictive_key] = ThumbnailRequest(
        predictive,
        size,
        ThumbnailRequestKind.PREDICTIVE,
        generation=1,
    )
    service._prefetch_kinds[far_key] = ThumbnailRequestKind.PREFETCH
    service._prefetch_kinds[predictive_key] = ThumbnailRequestKind.PREDICTIVE
    service._prefetch_pending.update({far_key, predictive_key})
    service._prefetch_queue.extend([far_key, predictive_key])
    service._stage_result(ThumbnailLoadResult(far, size, image, 1, ThumbnailRequestKind.PREFETCH))

    service._apply_low_memory_pressure_if_needed()

    assert service._low_memory_pressure is True
    assert far_key not in service._prefetch_queued
    assert predictive_key in service._prefetch_queued
    assert len(service._publish_prefetch) == 0
    assert service._memory_used_bytes <= int(
        policy.memory_limit_bytes * policy.windows_low_memory_target_ratio
    )


def test_visible_staging_backpressure_pauses_new_foreground_workers(tmp_path: Path) -> None:
    policy = replace(
        ThumbnailRuntimePolicy.detect(platform="darwin", sysconf=lambda _name: 4096),
        staging_limit=1,
    )
    service = ThumbnailCacheService(tmp_path / "thumbs", runtime_policy=policy)
    size = QSize(8, 8)
    staged = tmp_path / "staged.jpg"
    queued = tmp_path / "queued.jpg"
    service._stage_result(
        ThumbnailLoadResult(
            staged,
            size,
            QImage(8, 8, QImage.Format.Format_ARGB32_Premultiplied),
            0,
            ThumbnailRequestKind.VISIBLE,
        )
    )
    key = service._cache_key(queued, size)
    service._pending_tasks.add(key)
    service._queued_tasks[key] = ThumbnailRequest(
        queued,
        size,
        ThumbnailRequestKind.VISIBLE,
        0,
    )
    service._visible_queue.append(key)

    with patch.object(service, "_start_generation") as start_generation:
        service._drain_generation_queue()

    start_generation.assert_not_called()

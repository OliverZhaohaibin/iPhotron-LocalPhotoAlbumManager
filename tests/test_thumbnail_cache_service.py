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
from iPhoto.infrastructure.services.thumbnail_cache_service import (
    ThumbnailCacheService,
    _ActiveThumbnailTask,
)


def _active_task(
    service: ThumbnailCacheService,
    path: Path,
    size: QSize,
    *,
    priority: str = "visible",
    allow_generate: bool = True,
    speculative: bool = False,
) -> _ActiveThumbnailTask:
    task = _ActiveThumbnailTask(
        task_id=1,
        key=service._cache_key(path, size),
        path=path,
        size=size,
        priority=priority,
        allow_generate=allow_generate,
        speculative=speculative,
        requested_at_ms=0.0,
    )
    service._active_jobs[task.task_id] = task
    service._active_tasks = 1
    service._pending_tasks.add(task.key)
    return task


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
    path = tmp_path / "photo.jpg"
    path.write_bytes(b"image")
    size = QSize(64, 64)

    with patch.object(
        service._generator,
        "generate",
        return_value=Image.new("RGB", (8, 8), "red"),
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

    task = _active_task(service, path, size)
    service._handle_generation_failure(task, path, size, "empty_render")
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
    call = queue_generation.call_args
    assert call.args == (second, size)
    assert call.kwargs["priority"] == "visible"
    assert call.kwargs["allow_generate"] is True
    assert call.kwargs["drain"] is False
    disk_lookup.assert_not_called()


def test_request_many_promotes_queued_low_priority_task_to_visible(tmp_path: Path) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    service._max_active_jobs = 0
    path = tmp_path / "photo.jpg"
    size = QSize(64, 64)

    assert service.request_many([path], size, priority="low") == 1
    assert service.request_many([path], size, priority="visible") == 1

    _key, next_task = service._pop_next_generation()
    assert next_task.path == path
    assert next_task.size == size
    assert next_task.allow_generate is True
    assert service._pop_next_generation() is None


def test_generate_upgrade_does_not_demote_queued_visible_task(tmp_path: Path) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    service._max_active_jobs = 0
    path = tmp_path / "photo.jpg"
    size = QSize(64, 64)

    assert service.request_many(
        [path], size, priority="visible", allow_generate=False
    ) == 1
    assert service.request_many(
        [path], size, priority="normal", allow_generate=True
    ) == 1

    task = service._queued_tasks[service._cache_key(path, size)]
    assert task.priority == "visible"
    assert task.allow_generate is True


def test_cancel_pending_except_only_cancels_matching_size(tmp_path: Path) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    service._max_active_jobs = 0
    visible = tmp_path / "visible.jpg"
    stale = tmp_path / "stale.jpg"
    other_size = tmp_path / "other-size.jpg"
    size = QSize(512, 512)
    small = QSize(256, 256)

    service.request_many([visible, stale], size, priority="low")
    service.request_many([other_size], small, priority="low")

    service.cancel_pending_except({visible}, size)

    assert service._cache_key(visible, size) in service._queued_tasks
    assert service._cache_key(stale, size) not in service._queued_tasks
    assert service._cache_key(other_size, small) in service._queued_tasks


def test_cancel_pending_except_marks_matching_active_task_stale(tmp_path: Path) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    stale = tmp_path / "stale.jpg"
    keep = tmp_path / "keep.jpg"
    size = QSize(512, 512)
    stale_task = _active_task(service, stale, size, priority="low", allow_generate=False)

    service.cancel_pending_except({keep}, size)

    assert stale_task.stale is True
    assert stale_task.cancel_reason == "stale_active"


def test_cancel_pending_except_can_preserve_active_scroll_work(tmp_path: Path) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    stale = tmp_path / "stale.jpg"
    keep = tmp_path / "keep.jpg"
    size = QSize(512, 512)
    stale_task = _active_task(service, stale, size, priority="visible")

    service.cancel_pending_except({keep}, size, cancel_active=False)

    assert stale_task.stale is False
    assert stale_task.cancel_reason is None


def test_windows_visible_request_can_start_while_slow_old_tasks_run(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "iPhoto.infrastructure.services.thumbnail_cache_service.sys.platform",
        "win32",
    )
    service = ThumbnailCacheService(tmp_path / "thumbs")
    size = QSize(512, 512)
    service._active_jobs.clear()
    for index in range(2):
        task = _active_task(
            service,
            tmp_path / f"old-{index}.jpg",
            size,
            priority="visible",
        )
        service._active_jobs.pop(task.task_id)
        task.task_id = index + 1
        service._active_jobs[task.task_id] = task
    service._active_tasks = 2
    visible = tmp_path / "visible.jpg"

    with patch.object(service._thread_pool, "start") as start:
        service.request_many([visible], size, priority="visible", allow_generate=True)

    assert service._max_active_jobs == 4
    start.assert_called_once()
    assert any(task.path == visible for task in service._active_jobs.values())


def test_active_l2_only_task_promoted_after_miss_retries_as_visible(
    tmp_path: Path,
) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    service._max_active_jobs = 0
    path = tmp_path / "photo.jpg"
    size = QSize(512, 512)
    task = _active_task(service, path, size, priority="low", allow_generate=False)
    task.cancel_reason = "l2_only_miss"

    assert service.request_many(
        [path], size, priority="visible", allow_generate=True
    ) == 1
    assert task.retry_after_cancel is True

    service._handle_generation_cancelled(task, path, size, task.cancel_reason)

    retry = service._queued_tasks[task.key]
    assert retry.priority == "visible"
    assert retry.allow_generate is True
    assert task.key not in service._failure_until


def test_stale_active_task_is_not_retried_by_low_neighbor_request(tmp_path: Path) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    service._max_active_jobs = 0
    path = tmp_path / "photo.jpg"
    size = QSize(512, 512)
    task = _active_task(service, path, size, priority="low", allow_generate=False)
    task.stale = True
    task.cancel_reason = "stale_active"

    assert service.request_many(
        [path], size, priority="low", allow_generate=False
    ) == 0
    assert task.retry_after_cancel is False

    service._handle_generation_cancelled(task, path, size, task.cancel_reason)

    assert task.key not in service._queued_tasks


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
    task = _active_task(service, path, size)

    with patch(
        "iPhoto.infrastructure.services.thumbnail_cache_service.thumbnail_cache_file_for_key"
    ) as disk_lookup:
        service._handle_generation_result(task, path, size, image)

    disk_lookup.assert_not_called()
    assert service.peek(path, size) is not None


def test_request_many_enqueues_whole_batch_before_drain(tmp_path: Path) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    paths = [tmp_path / f"{index}.jpg" for index in range(5)]
    size = QSize(64, 64)

    with patch.object(service, "_drain_generation_queue") as drain:
        assert service.request_many(paths, size, priority="visible") == len(paths)

    drain.assert_called_once_with()
    assert [task.path for task in service._queued_tasks.values()] == paths


def test_batch_drain_never_starts_more_than_two_workers(tmp_path: Path) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    paths = [tmp_path / f"{index}.jpg" for index in range(8)]
    size = QSize(64, 64)

    with patch.object(service, "_start_generation") as start_generation:
        service.request_many(paths, size, priority="visible")

    assert service._active_tasks == 2
    assert start_generation.call_count == 2


def test_speculative_warmup_reserves_one_worker_for_visible(tmp_path: Path) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    warmup_paths = [tmp_path / f"warmup-{index}.jpg" for index in range(8)]
    visible = tmp_path / "visible.jpg"
    size = QSize(512, 512)

    with patch.object(service._thread_pool, "start") as thread_pool_start:
        service.request_many(
            warmup_paths,
            size,
            priority="normal",
            allow_generate=True,
            speculative=True,
        )
        assert service._active_tasks == 1

        service.request_many([visible], size, priority="visible", allow_generate=True)

    assert service._active_tasks == 2
    assert thread_pool_start.call_count == 2
    assert any(task.path == visible for task in service._active_jobs.values())


def test_visible_request_promotes_speculative_warmup(tmp_path: Path) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    service._max_active_jobs = 0
    path = tmp_path / "photo.jpg"
    size = QSize(512, 512)

    service.request_many(
        [path],
        size,
        priority="normal",
        allow_generate=True,
        speculative=True,
    )
    service.request_many([path], size, priority="visible", allow_generate=True)

    task = service._queued_tasks[service._cache_key(path, size)]
    assert task.priority == "visible"
    assert task.speculative is False


def test_speculative_queue_continues_one_at_a_time_after_completion(
    tmp_path: Path,
) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    paths = [tmp_path / f"warmup-{index}.jpg" for index in range(3)]
    size = QSize(512, 512)

    with patch.object(service._thread_pool, "start") as thread_pool_start:
        service.request_many(
            paths,
            size,
            priority="normal",
            allow_generate=True,
            speculative=True,
        )
        first_task = next(iter(service._active_jobs.values()))
        service._handle_generation_failure(first_task, first_task.path, size, "failed")

    assert thread_pool_start.call_count == 2
    assert service._active_tasks == 1
    assert len(service._queued_tasks) == 1
    assert all(task.speculative for task in service._active_jobs.values())


def test_l2_only_miss_never_renders_or_enters_failure_cooldown(tmp_path: Path) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    path = tmp_path / "missing.jpg"
    size = QSize(64, 64)
    task = _active_task(service, path, size, priority="low", allow_generate=False)

    with patch.object(service, "_render_thumbnail") as render_thumbnail:
        assert service._load_or_render_thumbnail(path, size, task) is None

    render_thumbnail.assert_not_called()
    assert task.cancel_reason == "l2_only_miss"
    service._handle_generation_cancelled(task, path, size, task.cancel_reason)
    assert task.key not in service._failure_until


def test_stale_active_task_stops_before_source_decode(tmp_path: Path) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    path = tmp_path / "missing.jpg"
    size = QSize(64, 64)
    task = _active_task(service, path, size)
    task.stale = True

    with patch.object(service, "_render_thumbnail") as render_thumbnail:
        assert service._load_or_render_thumbnail(path, size, task) is None

    render_thumbnail.assert_not_called()


def test_task_marked_stale_during_decode_skips_disk_write(tmp_path: Path) -> None:
    service = ThumbnailCacheService(tmp_path / "thumbs")
    path = tmp_path / "photo.jpg"
    size = QSize(64, 64)
    task = _active_task(service, path, size)
    image = QImage(64, 64, QImage.Format_RGB32)

    def mark_stale(*_args):
        task.stale = True
        return image

    with patch.object(service, "_render_thumbnail", side_effect=mark_stale):
        assert service._load_or_render_thumbnail(path, size, task) is None

    assert not thumbnail_cache_file(tmp_path / "thumbs", path, (64, 64)).exists()


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

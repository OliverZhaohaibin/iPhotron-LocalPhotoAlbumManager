"""Opt-in real Qt event-loop benchmarks for full-thumbnail Gallery scrolling."""

from __future__ import annotations

import os
import statistics
import time
from dataclasses import replace
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

if os.environ.get("IPHOTO_RUN_GALLERY_SCROLL_BENCHMARK") != "1":
    pytest.skip(
        "Set IPHOTO_RUN_GALLERY_SCROLL_BENCHMARK=1 to run Gallery scroll benchmarks.",
        allow_module_level=True,
    )

pytest.importorskip("PySide6", reason="PySide6 is required for Qt scroll benchmarks")

from PySide6.QtCore import QPoint, QPointF, QSize, Qt
from PySide6.QtGui import QImage, QPixmap, QWheelEvent
from PySide6.QtWidgets import QApplication

from iPhoto.bootstrap.library_asset_query_service import LibraryAssetQueryService
from iPhoto.cache.index_store import IndexStore
from iPhoto.domain.models.query import AssetQuery
from iPhoto.gui.ui.widgets.asset_delegate import AssetGridDelegate
from iPhoto.gui.ui.widgets.gallery_grid_view import GalleryGridView
from iPhoto.gui.viewmodels.gallery_list_model_adapter import GalleryListModelAdapter
from iPhoto.infrastructure.services.thumbnail_cache_keys import thumbnail_cache_file
from iPhoto.infrastructure.services.thumbnail_cache_service import (
    ThumbnailCacheService,
    ThumbnailDemandSnapshot,
)
from iPhoto.infrastructure.services.thumbnail_runtime_policy import ThumbnailRuntimePolicy


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round((len(ordered) - 1) * 0.95))]


def _write_l2(cache_dir: Path, paths: list[Path], size: QSize) -> None:
    image = QImage(size, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.darkGray)
    for path in paths:
        disk_file = thumbnail_cache_file(cache_dir, path, (size.width(), size.height()))
        disk_file.parent.mkdir(parents=True, exist_ok=True)
        assert image.save(str(disk_file), "JPEG")


def _process_for(qapp, seconds: float) -> None:
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        qapp.processEvents()
        time.sleep(0.001)


class _BenchmarkDelegate(AssetGridDelegate):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.paint_calls = 0

    def paint(self, painter, option, index) -> None:
        self.paint_calls += 1
        super().paint(painter, option, index)


class _BenchmarkGalleryGridView(GalleryGridView):
    def __init__(self) -> None:
        super().__init__()
        self.scroll_contents_calls = 0

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        self.scroll_contents_calls += 1
        super().scrollContentsBy(dx, dy)


def _build_gallery_view(qapp, paths: list[Path], service: ThumbnailCacheService, size: QSize):
    view = _BenchmarkGalleryGridView()
    delegate = _BenchmarkDelegate(view)
    library_root = paths[0].parent
    repository = IndexStore(library_root)
    micro_buffer = BytesIO()
    Image.new("RGB", (32, 32), "#555").save(micro_buffer, format="JPEG")
    micro = micro_buffer.getvalue()
    base = datetime(2024, 1, 1)
    repository.write_rows(
        {
            "rel": path.name,
            "id": f"asset-{index:05d}",
            "dt": (base + timedelta(seconds=len(paths) - index)).isoformat(),
            "ts": len(paths) - index,
            "bytes": 1024,
            "media_type": 0,
            "mime": "image/jpeg",
            "live_role": 0,
            "is_deleted": 0,
            "thumbnail_state": "ready",
            "micro_thumbnail": micro,
            "thumb_cache_key": service._disk_cache_key(path),
        }
        for index, path in enumerate(paths)
    )
    query_service = LibraryAssetQueryService(
        library_root,
        repository_factory=lambda _root: repository,
    )
    model = GalleryListModelAdapter.create(
        asset_query_service=query_service,
        thumbnail_service=service,
        library_root=library_root,
        parent=view,
    )
    model.store.load_selection(library_root, query=AssetQuery())
    view.viewportStateChanged.connect(model.update_viewport)
    view.setItemDelegate(delegate)
    view.setModel(model)
    view.resize(800, 600)
    view.show()
    qapp.processEvents()
    view.doItemsLayout()
    deadline = time.perf_counter() + 3.0
    while model.rowCount() < len(paths) and time.perf_counter() < deadline:
        qapp.processEvents()
        time.sleep(0.001)
    assert model.rowCount() == len(paths)
    state = view._scroll_controller.viewport_state(model.rowCount())
    if state is not None:
        model.update_viewport(state)
    _process_for(qapp, 0.1)
    view._benchmark_query_service = query_service
    return view, model, delegate


def _send_wheel(view: GalleryGridView, angle_y: int) -> None:
    position = QPointF(20.0, 20.0)
    global_position = QPointF(view.viewport().mapToGlobal(position.toPoint()))
    event = QWheelEvent(
        position,
        global_position,
        QPoint(),
        QPoint(0, angle_y),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    QApplication.sendEvent(view.viewport(), event)


def _close_gallery(view: GalleryGridView, service: ThumbnailCacheService) -> None:
    model = view.model()
    if isinstance(model, GalleryListModelAdapter):
        model._window_loader.shutdown()
        model._thumbnail_hint_loader.shutdown()
    query_service = getattr(view, "_benchmark_query_service", None)
    if query_service is not None:
        query_service.shutdown()
    view.close()
    service.shutdown()


@pytest.mark.parametrize(
    ("profile", "platform", "read_delay", "cadence_ms"),
    [
        ("windows-150ms", "win32", 0.025, 150),
        ("windows-200ms", "win32", 0.025, 200),
        ("windows-250ms", "win32", 0.025, 250),
        ("linux-slow-disk", "linux", 0.035, 200),
    ],
)
def test_real_sqlite_adapter_slow_scroll_preheats_full_before_visibility(
    qapp,
    tmp_path: Path,
    profile: str,
    platform: str,
    read_delay: float,
    cadence_ms: int,
) -> None:
    del profile
    policy = ThumbnailRuntimePolicy.detect(
        platform=platform,
        windows_probe=lambda: 16 * 1024**3,
        sysconf=lambda _name: 4096 * 1024,
    )
    cache_dir = tmp_path / "thumbs"
    service = ThumbnailCacheService(cache_dir, runtime_policy=policy)
    size = QSize(512, 512)
    paths = [tmp_path / f"photo-{index:04d}.jpg" for index in range(100)]
    _write_l2(cache_dir, paths, size)
    original_reader = service._read_cached_thumbnail

    def delayed_reader(*args, **kwargs):
        if kwargs.get("tier") == "L2_prefetch":
            time.sleep(read_delay)
        return original_reader(*args, **kwargs)

    view, model, _delegate = _build_gallery_view(qapp, paths, service, size)
    ready_before_visible: list[bool] = []
    with patch.object(service, "_read_cached_thumbnail", side_effect=delayed_reader):
        _process_for(qapp, 0.75)
        for update in range(100):
            view._scroll_controller._last_input_at = time.monotonic() - 0.2
            _send_wheel(view, -120)
            _process_for(qapp, cadence_ms / 1000.0)
            state = view._scroll_controller.viewport_state(model.rowCount())
            assert state is not None
            if update:
                ready_before_visible.extend(
                    service.has_full_thumbnail(paths[row], QSize(state.display_bucket, state.display_bucket))
                    for row in range(state.visible_first, state.visible_last + 1)
                )

    _close_gallery(view, service)
    assert statistics.fmean(ready_before_visible) >= 0.99


def test_windows_discrete_wheel_directional_dwell_preheats_next_viewport(
    qapp,
    tmp_path: Path,
) -> None:
    policy = ThumbnailRuntimePolicy.detect(
        platform="win32",
        windows_probe=lambda: 16 * 1024**3,
    )
    cache_dir = tmp_path / "thumbs"
    service = ThumbnailCacheService(cache_dir, runtime_policy=policy)
    paths = [tmp_path / f"photo-{index:04d}.jpg" for index in range(240)]
    _write_l2(cache_dir, paths, QSize(512, 512))
    view, _model, _delegate = _build_gallery_view(
        qapp,
        paths,
        service,
        QSize(512, 512),
    )
    readiness: list[bool] = []

    for _index in range(6):
        view._scroll_controller._last_input_at = time.monotonic() - 0.2
        _send_wheel(view, -120)
        _process_for(qapp, 0.3)
        state = view._scroll_controller.viewport_state(len(paths))
        assert state is not None
        display_size = QSize(state.display_bucket, state.display_bucket)
        visible_count = state.visible_last - state.visible_first + 1
        next_paths = paths[
            state.visible_last + 1 : state.visible_last + 1 + visible_count
        ]
        if _index > 0:
            readiness.extend(
                service.has_full_thumbnail(path, display_size) for path in next_paths
            )

    _close_gallery(view, service)
    assert readiness
    assert statistics.fmean(readiness) >= 0.99


def test_windows_saturated_l1_pool_refreshes_for_slow_scroll(
    qapp,
    tmp_path: Path,
) -> None:
    tile_bytes = 512 * 512 * 4
    policy = replace(
        ThumbnailRuntimePolicy.detect(
            platform="win32",
            windows_probe=lambda: 16 * 1024**3,
        ),
        memory_limit_bytes=20 * tile_bytes,
        l1_replacement_threshold_ratio=0.90,
        l1_replacement_target_ratio=0.72,
    )
    cache_dir = tmp_path / "thumbs"
    service = ThumbnailCacheService(cache_dir, runtime_policy=policy)
    size = QSize(512, 512)
    paths = [tmp_path / f"photo-{index:04d}.jpg" for index in range(120)]
    stale_paths = [tmp_path / f"stale-{index:04d}.jpg" for index in range(20)]
    _write_l2(cache_dir, paths, size)
    stale_pixmap = QPixmap(512, 512)
    stale_pixmap.fill(Qt.GlobalColor.darkGray)
    for path in stale_paths:
        service._add_to_memory(service._cache_key(path, size), stale_pixmap)

    readiness: list[bool] = []
    for generation, first in enumerate(range(20, 60, 10), start=1):
        visible = paths[first : first + 10]
        prefetch = paths[first + 10 : first + 50]
        service.reconcile_demand(
            ThumbnailDemandSnapshot(
                revision=generation,
                size=size,
                visible_paths=tuple(visible),
                guard_paths=tuple(prefetch[:10]),
                speculative_paths=tuple(prefetch[10:]),
                phase="slow",
                intent="slow_continuous",
            )
        )
        _process_for(qapp, 0.8)
        next_visible = paths[first + 10 : first + 20]
        readiness.extend(
            service.has_full_thumbnail(path, size) for path in next_visible
        )

    service.shutdown()
    assert readiness
    assert statistics.fmean(readiness) >= 0.99


def test_windows_unsaturated_l1_pool_refreshes_for_slow_scroll(
    qapp,
    tmp_path: Path,
) -> None:
    tile_bytes = 512 * 512 * 4
    policy = replace(
        ThumbnailRuntimePolicy.detect(
            platform="win32",
            windows_probe=lambda: 16 * 1024**3,
        ),
        memory_limit_bytes=200 * tile_bytes,
        l1_replacement_threshold_ratio=0.90,
        l1_replacement_target_ratio=0.72,
    )
    cache_dir = tmp_path / "thumbs"
    service = ThumbnailCacheService(cache_dir, runtime_policy=policy)
    size = QSize(512, 512)
    paths = [tmp_path / f"photo-{index:04d}.jpg" for index in range(120)]
    stale_paths = [tmp_path / f"stale-{index:04d}.jpg" for index in range(20)]
    _write_l2(cache_dir, paths, size)
    stale_pixmap = QPixmap(512, 512)
    stale_pixmap.fill(Qt.GlobalColor.darkGray)
    for path in stale_paths:
        service._add_to_memory(service._cache_key(path, size), stale_pixmap)

    assert service._memory_used_bytes < int(
        policy.memory_limit_bytes * policy.l1_replacement_threshold_ratio
    )

    visible = paths[20:30]
    prefetch = paths[30:70]
    service.reconcile_demand(
        ThumbnailDemandSnapshot(
            revision=1,
            size=size,
            visible_paths=tuple(visible),
            guard_paths=tuple(prefetch[:10]),
            speculative_paths=tuple(prefetch[10:]),
            phase="slow",
            intent="slow_continuous",
        )
    )
    _process_for(qapp, 0.8)
    next_visible = paths[30:40]
    readiness = [service.has_full_thumbnail(path, size) for path in next_visible]

    service.shutdown()
    assert readiness
    assert statistics.fmean(readiness) >= 0.99


def test_fast_round_trip_does_not_start_speculative_or_block_event_loop(
    qapp,
    tmp_path: Path,
) -> None:
    policy = ThumbnailRuntimePolicy.detect(
        platform="win32", windows_probe=lambda: 16 * 1024**3
    )
    service = ThumbnailCacheService(tmp_path / "thumbs", runtime_policy=policy)
    size = QSize(512, 512)
    paths = [tmp_path / f"photo-{index:04d}.jpg" for index in range(80)]
    _write_l2(tmp_path / "thumbs", paths, size)
    view, model, delegate = _build_gallery_view(qapp, paths, service, size)
    original_load = service._load_cached_thumbnail_only
    speculative_started_during_fast = 0

    def delayed_load(*args, **kwargs):
        nonlocal speculative_started_during_fast
        if service._current_phase == "fast":
            speculative_started_during_fast += 1
        time.sleep(0.03)
        return original_load(*args, **kwargs)

    scheduling_timings: list[float] = []
    catchup_timings: list[float] = []
    scrollbar_values: list[int] = []
    round_trip = (-120, -120, 120, 120) * 6
    with patch.object(service, "_load_cached_thumbnail_only", side_effect=delayed_load):
        for angle_y in round_trip:
            view._scroll_controller._screens_per_second = 10.0
            started = time.perf_counter()
            _send_wheel(view, angle_y)
            scheduling_timings.append((time.perf_counter() - started) * 1000.0)
            qapp.processEvents()
            catchup_timings.append((time.perf_counter() - started) * 1000.0)
            scrollbar_values.append(view.verticalScrollBar().value())

    _process_for(qapp, 0.1)
    _close_gallery(view, service)
    assert speculative_started_during_fast == 0
    assert len(set(scrollbar_values)) > 1
    assert view.scroll_contents_calls > 0
    assert model.rowCount() == len(paths)
    assert delegate.paint_calls > 0
    assert _p95(scheduling_timings) <= 2.0
    assert max(catchup_timings) <= 100.0


def test_slow_scroll_recovers_symmetric_full_prefetch_after_fast_round_trip(
    qapp,
    tmp_path: Path,
) -> None:
    policy = ThumbnailRuntimePolicy.detect(
        platform="win32", windows_probe=lambda: 16 * 1024**3
    )
    cache_dir = tmp_path / "thumbs"
    service = ThumbnailCacheService(cache_dir, runtime_policy=policy)
    size = QSize(512, 512)
    paths = [tmp_path / f"photo-{index:04d}.jpg" for index in range(160)]
    _write_l2(cache_dir, paths, size)
    view, _model, _delegate = _build_gallery_view(qapp, paths, service, size)

    for angle_y in (-120, -120, 120, 120) * 4:
        view._scroll_controller._screens_per_second = 10.0
        _send_wheel(view, angle_y)
        qapp.processEvents()

    _process_for(qapp, 0.1)
    view._scroll_controller._publish_idle_state()
    _process_for(qapp, 0.1)

    readiness: list[bool] = []
    for index in range(6):
        view._scroll_controller._last_input_at = time.monotonic() - 0.2
        _send_wheel(view, -120)
        _process_for(qapp, 0.35)
        state = view._scroll_controller.viewport_state(len(paths))
        assert state is not None
        display_size = QSize(state.display_bucket, state.display_bucket)
        visible_count = state.visible_last - state.visible_first + 1
        near_rows = [
            row
            for row in state.iter_full_prefetch_rows()
            if 0 <= row < len(paths)
        ][:visible_count]
        near_paths = [paths[row] for row in near_rows]
        if index > 0:
            readiness.extend(
                service.has_full_thumbnail(path, display_size) for path in near_paths
            )

    _close_gallery(view, service)
    assert readiness
    assert statistics.fmean(readiness) >= 0.95


def test_high_dpi_publish_batches_stay_bounded(qapp, tmp_path: Path) -> None:
    policy = replace(
        ThumbnailRuntimePolicy.detect(platform="linux", sysconf=lambda _name: 4096 * 1024),
        publish_max_items=2,
        publish_budget_ms=3.0,
    )
    service = ThumbnailCacheService(tmp_path / "thumbs", runtime_policy=policy)
    size = QSize(512, 512)
    paths = [tmp_path / f"photo-{index:04d}.jpg" for index in range(20)]
    _write_l2(tmp_path / "thumbs", paths, size)
    service.reconcile_demand(
        ThumbnailDemandSnapshot(
            revision=1,
            size=size,
            visible_paths=tuple(paths[:10]),
            guard_paths=tuple(paths[10:]),
            phase="slow",
            intent="slow_continuous",
        )
    )

    event_loop_ticks: list[float] = []
    deadline = time.perf_counter() + 5.0
    while time.perf_counter() < deadline and not all(
        service.has_full_thumbnail(path, size) for path in paths
    ):
        started = time.perf_counter()
        qapp.processEvents()
        event_loop_ticks.append((time.perf_counter() - started) * 1000.0)
        time.sleep(0.001)

    assert all(service.has_full_thumbnail(path, size) for path in paths)
    assert _p95(event_loop_ticks) <= 24.0
    service.shutdown()

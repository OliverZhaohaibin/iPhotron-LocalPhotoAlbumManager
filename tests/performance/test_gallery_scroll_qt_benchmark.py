"""Opt-in real Qt event-loop benchmarks for full-thumbnail Gallery scrolling."""

from __future__ import annotations

import os
import statistics
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

if os.environ.get("IPHOTO_RUN_GALLERY_SCROLL_BENCHMARK") != "1":
    pytest.skip(
        "Set IPHOTO_RUN_GALLERY_SCROLL_BENCHMARK=1 to run Gallery scroll benchmarks.",
        allow_module_level=True,
    )

pytest.importorskip("PySide6", reason="PySide6 is required for Qt scroll benchmarks")

from PySide6.QtCore import QPoint, QPointF, QSize, Qt
from PySide6.QtGui import QImage, QPixmap, QStandardItem, QStandardItemModel, QWheelEvent
from PySide6.QtWidgets import QApplication

from iPhoto.gui.ui.models.roles import Roles
from iPhoto.gui.ui.widgets.asset_delegate import AssetGridDelegate
from iPhoto.gui.ui.widgets.gallery_grid_view import GalleryGridView
from iPhoto.infrastructure.services.thumbnail_cache_keys import thumbnail_cache_file
from iPhoto.infrastructure.services.thumbnail_cache_service import ThumbnailCacheService
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


class _BenchmarkModel(QStandardItemModel):
    def __init__(self) -> None:
        super().__init__()
        self.data_calls = 0

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        self.data_calls += 1
        return super().data(index, role)


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
    model = _BenchmarkModel()
    placeholder = QPixmap(32, 32)
    placeholder.fill(Qt.GlobalColor.darkGray)
    for path in paths:
        item = QStandardItem()
        item.setData(False, Roles.IS_SPACER)
        item.setData(path, Roles.ABS)
        item.setData(placeholder, Qt.ItemDataRole.DecorationRole)
        model.appendRow(item)

    def reconcile(state) -> None:
        visible = paths[state.visible_first : state.visible_last + 1]
        prefetch = [
            paths[row]
            for row in state.iter_full_prefetch_rows()
            if 0 <= row < len(paths)
        ]
        service.reconcile_demand(
            visible_paths=visible,
            prefetch_paths=prefetch,
            size=QSize(state.display_bucket, state.display_bucket),
            generation=state.generation,
            phase=state.phase,
            intent=state.intent,
        )

    view.viewportStateChanged.connect(reconcile)
    view.setItemDelegate(delegate)
    view.setModel(model)
    view.resize(800, 600)
    view.show()
    qapp.processEvents()
    view.doItemsLayout()
    qapp.processEvents()
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


@pytest.mark.parametrize(
    ("profile", "platform", "read_delay"),
    [
        ("delayed-ntfs", "win32", 0.012),
        ("linux-slow-disk", "linux", 0.020),
    ],
)
def test_slow_scroll_preheats_full_before_visibility(
    qapp,
    tmp_path: Path,
    profile: str,
    platform: str,
    read_delay: float,
) -> None:
    del profile
    policy = replace(
        ThumbnailRuntimePolicy.detect(
            platform=platform,
            windows_probe=lambda: 16 * 1024**3,
            sysconf=lambda _name: 4096 * 1024,
        ),
        prefetch_sample_size=8,
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

    ready_before_visible: list[bool] = []
    with patch.object(service, "_read_cached_thumbnail", side_effect=delayed_reader):
        for generation, first in enumerate(range(0, 70, 10), start=1):
            visible = paths[first : first + 10]
            prefetch = paths[first + 10 : first + 50]
            service.reconcile_demand(
                visible_paths=visible,
                prefetch_paths=prefetch,
                size=size,
                generation=generation,
                phase="slow",
            )
            _process_for(qapp, 0.6)
            next_visible = paths[first + 10 : first + 20]
            if generation > 1:
                ready_before_visible.extend(
                    service.has_full_thumbnail(path, size) for path in next_visible
                )

    service.shutdown()
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

    view.close()
    service.shutdown()
    assert readiness
    assert statistics.fmean(readiness) >= 0.99


def test_fast_round_trip_does_not_start_speculative_or_block_event_loop(
    qapp,
    tmp_path: Path,
) -> None:
    policy = replace(
        ThumbnailRuntimePolicy.detect(platform="win32", windows_probe=lambda: 16 * 1024**3),
        prefetch_sample_size=4,
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
    view.close()
    service.shutdown()
    assert speculative_started_during_fast == 0
    assert len(set(scrollbar_values)) > 1
    assert view.scroll_contents_calls > 0
    assert model.data_calls > 0
    assert delegate.paint_calls > 0
    assert _p95(scheduling_timings) <= 2.0
    assert max(catchup_timings) <= 100.0


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
        visible_paths=paths[:10],
        prefetch_paths=paths[10:],
        size=size,
        generation=1,
        phase="slow",
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

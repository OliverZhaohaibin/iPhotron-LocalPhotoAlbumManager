"""Opt-in real Qt event-loop benchmark for Gallery scrolling."""

from __future__ import annotations

import csv
import json
import os
import platform
import statistics
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

if os.environ.get("IPHOTO_RUN_GALLERY_SCROLL_BENCHMARK") != "1":
    pytest.skip(
        "Set IPHOTO_RUN_GALLERY_SCROLL_BENCHMARK=1 to run real Qt scroll benchmarks.",
        allow_module_level=True,
    )

pytest.importorskip("PySide6", reason="PySide6 is required for Qt scroll benchmarks")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QGuiApplication, QImage, QWheelEvent
from PySide6.QtWidgets import QApplication

from iPhoto.application.dtos import AssetDTO
from iPhoto.gui.ui.widgets.asset_delegate import AssetGridDelegate
from iPhoto.gui.ui.widgets.gallery_grid_view import GalleryGridView
from iPhoto.gui.viewmodels.gallery_list_model_adapter import GalleryListModelAdapter


class _Signal:
    def __init__(self) -> None:
        self._handlers: list[Any] = []

    def connect(self, handler) -> None:
        self._handlers.append(handler)

    def emit(self, *args) -> None:
        for handler in list(self._handlers):
            handler(*args)


class _SyntheticStore:
    def __init__(self, row_count: int, violations: dict[str, int]) -> None:
        self.data_changed = _Signal()
        self.window_changed = _Signal()
        self.row_changed = _Signal()
        self.thumbnail_backfill_scheduled = _Signal()
        self._row_count = row_count
        self._violations = violations
        self._cache: dict[int, AssetDTO] = {}
        self._micro = QImage(16, 16, QImage.Format.Format_RGB32)
        self._micro.fill(Qt.GlobalColor.darkGray)
        self.prioritize_calls = 0
        self.visible_publishes: list[tuple[float, int, int]] = []
        self.warm_requests: list[tuple[float, int, int]] = []

    def count(self) -> int:
        return self._row_count

    def asset_at(self, row: int) -> AssetDTO | None:
        if row < 0 or row >= self._row_count:
            return None
        return self._cache.get(row)

    def _publish_micro(self, first: int, last: int) -> None:
        for row in range(first, last + 1):
            path = Path(f"/synthetic/photo-{row:07d}.jpg")
            self._cache[row] = AssetDTO(
                id=f"asset-{row:07d}",
                abs_path=path,
                rel_path=Path(path.name),
                media_type="image",
                created_at=None,
                width=1920,
                height=1080,
                duration=0.0,
                size_bytes=1024,
                metadata={},
                is_favorite=False,
                micro_thumbnail=self._micro,
            )

    def ensure_row_loaded(self, row: int, *, emit_signals: bool = True) -> bool:
        del row, emit_signals
        self._violations["ensure_row_loaded"] += 1
        return False

    def prioritize_rows(self, first: int, last: int) -> None:
        self.prioritize_calls += 1
        self._publish_micro(first, last)
        self.visible_publishes.append((time.perf_counter(), first, last))
        self.window_changed.emit(first, last)

    def prefetch_rows(self, first: int, last: int) -> None:
        self.warm_requests.append((time.perf_counter(), first, last))


class _MemoryOnlyThumbnails:
    def __init__(self, violations: dict[str, int]) -> None:
        self.thumbnailReady = _Signal()
        self._violations = violations
        self.peek_calls = 0
        self.requested_paths = 0

    def peek(self, path: Path, size) -> None:
        del path, size
        self.peek_calls += 1
        return None

    def get_thumbnail(self, path: Path, size, *, priority: str = "normal") -> None:
        del path, size, priority
        self._violations["get_thumbnail"] += 1
        return None

    def request_many(self, paths, size, *, priority: str = "normal") -> int:
        del size, priority
        requested = len(list(paths))
        self.requested_paths += requested
        return requested


class _InstrumentedGalleryGridView(GalleryGridView):
    def __init__(self, metrics: dict[str, Any]) -> None:
        self._metrics = metrics
        self._benchmark_active = False
        super().__init__()

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        started = time.perf_counter()
        super().wheelEvent(event)
        if self._benchmark_active:
            self._metrics["wheel_ms"].append((time.perf_counter() - started) * 1000.0)

    def scrollContentsBy(self, dx: int, dy: int) -> None:  # type: ignore[override]
        started = time.perf_counter()
        super().scrollContentsBy(dx, dy)
        if self._benchmark_active:
            self._metrics["scroll_ms"].append((time.perf_counter() - started) * 1000.0)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        started = time.perf_counter()
        super().paintEvent(event)
        if self._benchmark_active:
            now = time.perf_counter()
            self._metrics["paint_started_at"].append(started)
            self._metrics["paint_ms"].append((now - started) * 1000.0)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * percentile))))
    return ordered[index]


def _summary(values: list[float]) -> dict[str, float | int]:
    return {
        "count": len(values),
        "mean_ms": round(statistics.fmean(values), 3) if values else 0.0,
        "p95_ms": round(_percentile(values, 0.95), 3),
        "max_ms": round(max(values), 3) if values else 0.0,
    }


def _wheel_event(view: GalleryGridView) -> QWheelEvent:
    center = view.viewport().rect().center()
    local = QPointF(float(center.x()), float(center.y()))
    global_pos = QPointF(view.viewport().mapToGlobal(center))
    return QWheelEvent(
        local,
        global_pos,
        QPoint(0, 0),
        QPoint(0, -120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )


def _write_report(report: dict[str, Any], row_count: int) -> tuple[Path, Path]:
    report_dir = Path(
        os.environ.get(
            "IPHOTO_GALLERY_SCROLL_REPORT_DIR",
            "/tmp/iphoto-gallery-scroll-performance",
        )
    )
    report_dir.mkdir(parents=True, exist_ok=True)
    backend = str(report["environment"]["qt_backend"]).replace("/", "-")
    stem = f"gallery-scroll-{platform.system().lower()}-{backend}-{row_count}"
    json_path = report_dir / f"{stem}.json"
    csv_path = report_dir / f"{stem}.csv"

    json_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "row_count",
                "wheel_p95_ms",
                "scroll_p95_ms",
                "paint_p95_ms",
                "frame_interval_p95_ms",
                "input_catchup_ms",
                "final_micro_publish_ms",
                "micro_or_full_ratio",
                "placeholder_ratio",
                "visible_before_warm",
                "final_scrollbar_value",
                "ensure_row_loaded_violations",
                "get_thumbnail_violations",
                "repaint_violations",
                "layout_violations",
            ],
        )
        writer.writeheader()
        writer.writerow(report["csv_summary"])
    return json_path, csv_path


@pytest.mark.parametrize("row_count", [10_000, 100_000, 1_000_000])
def test_real_qt_gallery_scroll_benchmark(qapp, row_count: int) -> None:
    violations = {
        "ensure_row_loaded": 0,
        "get_thumbnail": 0,
        "repaint": 0,
        "execute_delayed_items_layout": 0,
    }
    metrics: dict[str, Any] = {
        "wheel_ms": [],
        "scroll_ms": [],
        "paint_ms": [],
        "paint_started_at": [],
        "scrollbar_changes": [],
    }
    store = _SyntheticStore(row_count, violations)
    thumbnails = _MemoryOnlyThumbnails(violations)
    model = GalleryListModelAdapter(store, thumbnails)
    view = _InstrumentedGalleryGridView(metrics)
    view.resize(1000, 700)
    view.setModel(model)
    view.setItemDelegate(AssetGridDelegate(view))
    view.visibleRowsChanged.connect(model.prioritize_rows)
    view.show()
    qapp.processEvents()
    view.doItemsLayout()
    qapp.processEvents()

    scrollbar = view.verticalScrollBar()
    scrollbar.valueChanged.connect(
        lambda value: metrics["scrollbar_changes"].append((time.perf_counter(), value))
    )
    original_repaint = view.viewport().repaint
    original_layout = view.executeDelayedItemsLayout

    def _record_repaint(*args, **kwargs):
        violations["repaint"] += 1
        return original_repaint(*args, **kwargs)

    def _record_layout(*args, **kwargs):
        violations["execute_delayed_items_layout"] += 1
        return original_layout(*args, **kwargs)

    wheel_count = max(1, int(os.environ.get("IPHOTO_GALLERY_SCROLL_WHEEL_EVENTS", "120")))
    wheel_batch_size = max(
        1,
        int(os.environ.get("IPHOTO_GALLERY_SCROLL_WHEEL_BATCH_SIZE", "8")),
    )
    view._benchmark_active = True
    started = time.perf_counter()
    queued_at = started
    with (
        patch.object(view.viewport(), "repaint", side_effect=_record_repaint),
        patch.object(view, "executeDelayedItemsLayout", side_effect=_record_layout),
    ):
        for batch_start in range(0, wheel_count, wheel_batch_size):
            batch_end = min(wheel_count, batch_start + wheel_batch_size)
            for _index in range(batch_start, batch_end):
                QApplication.postEvent(view.viewport(), _wheel_event(view))
            queued_at = time.perf_counter()
            qapp.processEvents()

        stable_since: float | None = None
        previous_value = scrollbar.value()
        deadline = queued_at + 5.0
        while time.perf_counter() < deadline:
            qapp.processEvents()
            current_value = scrollbar.value()
            now = time.perf_counter()
            if current_value != previous_value:
                previous_value = current_value
                stable_since = None
            elif stable_since is None:
                stable_since = now
            elif now - stable_since >= 0.05:
                break
            time.sleep(0.001)
    finished = time.perf_counter()
    view._benchmark_active = False
    request_deadline = time.perf_counter() + 0.3
    while thumbnails.requested_paths == 0 and time.perf_counter() < request_deadline:
        qapp.processEvents()
        time.sleep(0.005)

    visual_rows = []
    if view._visible_range is not None:
        visual_first, visual_last = view._visible_range
        visual_rows = [
            store.asset_at(row)
            for row in range(visual_first, visual_last + 1)
        ]
    visual_count = len(visual_rows)
    micro_count = sum(
        1
        for asset in visual_rows
        if asset is not None
        and isinstance(asset.micro_thumbnail, QImage)
        and not asset.micro_thumbnail.isNull()
    )
    placeholder_count = visual_count - micro_count
    final_micro_publish_ms = next(
        (
            max(0.0, (published_at - queued_at) * 1000.0)
            for published_at, first, last in store.visible_publishes
            if published_at >= queued_at
            and view._visible_range is not None
            and first <= view._visible_range[0]
            and last >= view._visible_range[1]
        ),
        -1.0,
    )
    visible_before_warm = bool(store.warm_requests) and all(
        any(
            published_at <= warm_at
            and published_first <= warm_first
            and published_last >= warm_last
            for published_at, published_first, published_last in store.visible_publishes
        )
        for warm_at, warm_first, warm_last in store.warm_requests
    )

    paint_starts = metrics["paint_started_at"]
    frame_intervals = [
        (current - previous) * 1000.0
        for previous, current in zip(paint_starts, paint_starts[1:])
    ]
    report = {
        "environment": {
            "platform": platform.platform(),
            "qt_backend": QGuiApplication.platformName(),
            "device_pixel_ratio": view.devicePixelRatioF(),
            "runtime_label": os.environ.get("IPHOTO_RUNTIME_LABEL", "development"),
            "row_count": row_count,
            "wheel_events": wheel_count,
            "wheel_batch_size": wheel_batch_size,
        },
        "wheel": _summary(metrics["wheel_ms"]),
        "scroll": _summary(metrics["scroll_ms"]),
        "paint": _summary(metrics["paint_ms"]),
        "frame_interval": _summary(frame_intervals),
        "input_catchup_ms": round(
            max(
                0.0,
                (
                    metrics["scrollbar_changes"][-1][0] - queued_at
                    if metrics["scrollbar_changes"]
                    else finished - queued_at
                ),
            )
            * 1000.0,
            3,
        ),
        "final_micro_publish_ms": round(final_micro_publish_ms, 3),
        "visible_before_warm": visible_before_warm,
        "visual_coverage": {
            "visible_rows": visual_count,
            "micro_or_full_ratio": round(micro_count / visual_count, 4)
            if visual_count
            else 1.0,
            "placeholder_ratio": round(placeholder_count / visual_count, 4)
            if visual_count
            else 0.0,
        },
        "total_elapsed_ms": round((finished - started) * 1000.0, 3),
        "final_scrollbar_value": scrollbar.value(),
        "scrollbar_change_count": len(metrics["scrollbar_changes"]),
        "memory_thumbnail_peek_calls": thumbnails.peek_calls,
        "async_thumbnail_request_count": thumbnails.requested_paths,
        "visible_range_request_count": store.prioritize_calls,
        "violations": violations,
    }
    report["csv_summary"] = {
        "row_count": row_count,
        "wheel_p95_ms": report["wheel"]["p95_ms"],
        "scroll_p95_ms": report["scroll"]["p95_ms"],
        "paint_p95_ms": report["paint"]["p95_ms"],
        "frame_interval_p95_ms": report["frame_interval"]["p95_ms"],
        "input_catchup_ms": report["input_catchup_ms"],
        "final_micro_publish_ms": report["final_micro_publish_ms"],
        "micro_or_full_ratio": report["visual_coverage"]["micro_or_full_ratio"],
        "placeholder_ratio": report["visual_coverage"]["placeholder_ratio"],
        "visible_before_warm": report["visible_before_warm"],
        "final_scrollbar_value": report["final_scrollbar_value"],
        "ensure_row_loaded_violations": violations["ensure_row_loaded"],
        "get_thumbnail_violations": violations["get_thumbnail"],
        "repaint_violations": violations["repaint"],
        "layout_violations": violations["execute_delayed_items_layout"],
    }
    json_path, csv_path = _write_report(report, row_count)

    assert scrollbar.value() > 0
    assert metrics["wheel_ms"]
    assert metrics["scroll_ms"]
    assert metrics["paint_ms"]
    assert thumbnails.requested_paths > 0
    assert report["visual_coverage"]["micro_or_full_ratio"] == 1.0
    assert report["visual_coverage"]["placeholder_ratio"] == 0.0
    assert 0.0 <= report["final_micro_publish_ms"] <= 100.0
    assert report["visible_before_warm"] is True
    assert not any(violations.values()), f"Protected-path violations: {violations}"
    assert json_path.exists()
    assert csv_path.exists()

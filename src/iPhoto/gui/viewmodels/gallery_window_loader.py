"""Generation-aware background loading for Gallery collection windows."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from iPhoto.application.dtos import AssetDTO
from iPhoto.domain.models.query import AssetQuery, WindowResult
from iPhoto.gui.viewmodels.asset_dto_converter import scan_row_to_dto
from iPhoto.infrastructure.services.performance_events import emit_perf_event


AssetIdentity = tuple[Path, str]


@dataclass(frozen=True, slots=True)
class GallerySelectionAnchor:
    """Path-stable selection identity carried across collection revisions."""

    path: Path
    asset_id: str
    previous_row: int
    selection_version: int = 0


@dataclass(frozen=True, slots=True)
class GallerySelectionAnchorRetryTicket:
    """One bounded, cancelable retry of a path-stable selection anchor."""

    anchor: GallerySelectionAnchor
    collection_revision: int
    attempt: int
    delay_ms: int


@dataclass(frozen=True, slots=True)
class GallerySelectionAnchorResult:
    """Authoritative result of resolving an anchor on the Gallery worker."""

    anchor: GallerySelectionAnchor
    status: Literal["resolved", "missing", "retry"]
    row: int | None = None
    dto: AssetDTO | None = None
    elapsed_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class GalleryWindowRequest:
    generation: int
    root: Path
    query: AssetQuery
    query_service: Any
    view_first: int
    raw_first: int
    limit: int
    pending_source_ids: frozenset[str] = frozenset()
    pending_source_count: int = 0
    pending_source_rows: tuple[int, ...] = ()
    pending_insertions: tuple[AssetDTO, ...] = ()
    selection_anchor: GallerySelectionAnchor | None = None
    request_backfill: bool = True
    collection_revision: int = 0
    demand_generation: int = 0
    priority: int = 1
    purpose: Literal["viewport", "selection_anchor_retry"] = "viewport"
    selection_anchor_retry_attempt: int = 0
    surface_id: str = "gallery"
    refresh_after_commit: bool = False
    selection_generation: int = 0
    extra_ranges: tuple[tuple[int, int], ...] = ()
    previous_order_revision: int | None = None
    tracked_assets: frozenset[AssetIdentity] = frozenset()
    pending_source_assets: tuple[AssetIdentity, ...] = ()


@dataclass(frozen=True, slots=True)
class GalleryWindowResult:
    generation: int
    first: int
    last: int
    rows: dict[int, AssetDTO]
    total_count: int
    collection_revision: int
    backfill_queued: int = 0
    error: str | None = None
    requested_revision: int = 0
    demand_generation: int = 0
    priority: int = 1
    selection_anchor_result: GallerySelectionAnchorResult | None = None
    purpose: Literal["viewport", "selection_anchor_retry"] = "viewport"
    selection_anchor_retry_attempt: int = 0
    surface_id: str = "gallery"
    order_revision: int | None = None
    refresh_after_commit: bool = False
    requested_ranges: tuple[tuple[int, int], ...] = ()
    tracked_rows: dict[AssetIdentity, int | None] = field(default_factory=dict)


class _GalleryWindowSignals(QObject):
    completed = Signal(object)


def _dto_identity_keys(dto: AssetDTO) -> set[str]:
    keys = {
        f"abs:{os.path.normcase(os.path.abspath(os.fspath(dto.abs_path)))}",
        f"rel:{dto.rel_path.as_posix()}",
    }
    if dto.id:
        keys.add(f"id:{dto.id}")
    return keys


def _normalized_path(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def raw_offset_for_view_offset(view_offset: int, pending_source_rows: tuple[int, ...]) -> int:
    raw_offset = view_offset
    for row in pending_source_rows:
        if row > raw_offset:
            break
        raw_offset += 1
    return raw_offset


def _raw_window_ranges(request: GalleryWindowRequest) -> tuple[tuple[int, int], ...]:
    return (
        (request.raw_first, request.limit + request.pending_source_count),
        *(
            (raw_offset_for_view_offset(first, request.pending_source_rows), limit + request.pending_source_count)
            for first, limit in request.extra_ranges
        ),
    )


def _read_window_snapshot(
    request: GalleryWindowRequest, reader: Any,
) -> tuple[GalleryWindowRequest, tuple[tuple[int, int], ...], tuple[WindowResult, ...]]:
    read_windows = getattr(request.query_service, "read_gallery_asset_windows", None)
    find_positions = getattr(request.query_service, "find_gallery_asset_positions", None)
    source_paths = tuple(path for path, _asset_id in request.pending_source_assets)
    verified_order = None
    for _attempt in range(3):
        raw_ranges = _raw_window_ranges(request)
        if len(raw_ranges) > 1 and callable(read_windows):
            windows = read_windows(request.root, request.query, raw_ranges)
        else:
            windows = tuple(reader(request.root, request.query, *bounds) for bounds in raw_ranges)

        order = windows[0].order_revision
        if order is None or order == verified_order or not source_paths or not callable(find_positions):
            return request, raw_ranges, windows

        positions = find_positions(request.query, source_paths, order)
        if positions is None:
            continue
        pending_rows = tuple(sorted({
            position[1]
            for path, asset_id in request.pending_source_assets
            if (position := positions.get(path)) is not None and position[0] == asset_id
        }))
        verified_order = order
        request = replace(
            request,
            raw_first=raw_offset_for_view_offset(request.view_first, pending_rows),
            pending_source_rows=pending_rows,
        )
        # If the SQL bounds match, the existing windows can be reused with the
        # verified offsets for anchor and tracked-row lookups.
        if _raw_window_ranges(request) == raw_ranges:
            return request, raw_ranges, windows

    raise RuntimeError("collection changed during pending move lookup")


def _resolve_selection_anchor(
    request: GalleryWindowRequest,
    reader: Any,
    rows: dict[int, AssetDTO],
    collection_revision: int,
    order_revision: int | None,
) -> GallerySelectionAnchorResult | None:
    anchor = request.selection_anchor
    if anchor is None:
        return None

    started = time.perf_counter()
    target = _normalized_path(anchor.path)
    for view_row, dto in rows.items():
        if _normalized_path(dto.abs_path) == target:
            if anchor.asset_id and dto.id != anchor.asset_id:
                return GallerySelectionAnchorResult(anchor=anchor, status="missing")
            return GallerySelectionAnchorResult(
                anchor=anchor,
                status="resolved",
                row=view_row,
                dto=dto,
                elapsed_ms=(time.perf_counter() - started) * 1000.0,
            )

    try:
        find_row_by_path = getattr(request.query_service, "find_row_by_path", None)
        if not callable(find_row_by_path):
            raise RuntimeError("query service does not support anchor resolution")
        raw_row = find_row_by_path(request.query, anchor.path)
        if raw_row is None:
            find_positions = getattr(request.query_service, "find_gallery_asset_positions", None)
            if order_revision is not None and callable(find_positions):
                positions = find_positions(request.query, (anchor.path,), order_revision)
                if positions is None or positions:
                    raise RuntimeError("collection changed during anchor lookup")
            return GallerySelectionAnchorResult(
                anchor=anchor,
                status="missing",
                elapsed_ms=(time.perf_counter() - started) * 1000.0,
            )

        raw_row = int(raw_row)
        exact_window = reader(request.root, request.query, raw_row, 1)
        if int(exact_window.collection_revision) != int(collection_revision):
            raise RuntimeError("collection changed during anchor resolution")
        dto: AssetDTO | None = None
        for raw_asset in exact_window.rows:
            rel = raw_asset.get("rel") if isinstance(raw_asset, dict) else None
            if not isinstance(rel, str) or not rel:
                continue
            candidate = scan_row_to_dto(request.root, rel, raw_asset)
            if candidate is None:
                continue
            if str(candidate.id) in request.pending_source_ids:
                return GallerySelectionAnchorResult(
                    anchor=anchor,
                    status="missing",
                    elapsed_ms=(time.perf_counter() - started) * 1000.0,
                )
            if _normalized_path(candidate.abs_path) == target:
                if anchor.asset_id and candidate.id != anchor.asset_id:
                    return GallerySelectionAnchorResult(
                        anchor=anchor, status="missing",
                        elapsed_ms=(time.perf_counter() - started) * 1000.0,
                    )
                dto = candidate
                break
        if dto is None:
            # The index changed between the row lookup and exact read. Let the
            # next revision retry instead of falsely declaring the asset gone.
            raise RuntimeError("anchor row changed during resolution")

        hidden_before = sum(
            1 for source_row in request.pending_source_rows if source_row < raw_row
        )
        return GallerySelectionAnchorResult(
            anchor=anchor,
            status="resolved",
            row=max(0, raw_row - hidden_before),
            dto=dto,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
        )
    except Exception:  # noqa: BLE001 - anchor failure must not fail the window
        return GallerySelectionAnchorResult(
            anchor=anchor,
            status="retry",
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
        )


def _resolve_tracked_rows(
    request: GalleryWindowRequest,
    rows: dict[int, AssetDTO],
    order_revision: int | None,
) -> dict[AssetIdentity, int | None]:
    if order_revision is None or order_revision == request.previous_order_revision:
        return {}
    unresolved = request.tracked_assets.difference((dto.abs_path, dto.id) for dto in rows.values())
    find_positions = getattr(request.query_service, "find_gallery_asset_positions", None)
    if not unresolved or not callable(find_positions):
        return {}
    positions = find_positions(request.query, tuple(path for path, _ in unresolved), order_revision)
    if positions is None:
        return {}
    tracked_rows = {}
    for identity in unresolved:
        path, asset_id = identity
        position = positions.get(path)
        if position is None or position[0] != asset_id or asset_id in request.pending_source_ids:
            tracked_rows[identity] = None
        else:
            raw_row = position[1]
            tracked_rows[identity] = raw_row - sum(row < raw_row for row in request.pending_source_rows)
    return tracked_rows


class _GalleryWindowWorker(QRunnable):
    def __init__(self, request: GalleryWindowRequest, signals: _GalleryWindowSignals) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._request = request
        self._signals = signals

    def run(self) -> None:  # pragma: no cover - background Qt task
        request = self._request
        started = time.perf_counter()
        try:
            reader = getattr(request.query_service, "read_gallery_asset_window", None)
            if not callable(reader):
                reader = request.query_service.read_query_asset_window
            ranges = ((request.view_first, request.limit), *request.extra_ranges)
            request, raw_ranges, windows = _read_window_snapshot(request, reader)
            window = windows[0]

            rows: dict[int, AssetDTO] = {}
            last = request.view_first
            for (first, limit), part in zip(ranges, windows, strict=True):
                if part.collection_revision != window.collection_revision:
                    raise RuntimeError("collection changed between window reads")
                loaded = 0
                for raw_row in part.rows:
                    rel = raw_row.get("rel") if isinstance(raw_row, dict) else None
                    if not isinstance(rel, str) or not rel:
                        continue
                    dto = scan_row_to_dto(request.root, rel, raw_row)
                    if dto is None or str(dto.id) in request.pending_source_ids:
                        continue
                    if loaded >= limit:
                        break
                    rows[first + loaded] = dto
                    loaded += 1
                if first == request.view_first:
                    last = max(first, first + loaded - 1)

            existing_keys = {
                key
                for dto in rows.values()
                for key in _dto_identity_keys(dto)
            }
            pending_insertions: list[AssetDTO] = []
            for dto in request.pending_insertions:
                dto_keys = _dto_identity_keys(dto)
                if existing_keys.intersection(dto_keys):
                    continue
                pending_insertions.append(dto)
                existing_keys.update(dto_keys)

            total_count = max(0, int(window.total_count) - request.pending_source_count)
            total_count += len(pending_insertions)
            insertion_start = max(0, total_count - len(pending_insertions))
            for offset, dto in enumerate(pending_insertions):
                rows[insertion_start + offset] = dto

            selection_anchor_result = _resolve_selection_anchor(
                request,
                reader,
                rows,
                int(window.collection_revision),
                window.order_revision,
            )
            tracked_rows = _resolve_tracked_rows(request, rows, window.order_revision)
            emit_perf_event(
                "gallery_window_worker_finished",
                generation=request.generation,
                requested_revision=request.collection_revision,
                collection_revision=int(window.collection_revision),
                elapsed_ms=round((time.perf_counter() - started) * 1000.0, 3),
                rows=len(rows),
                total_count=total_count,
                refresh_after_commit=request.refresh_after_commit,
                anchor_status=(
                    selection_anchor_result.status
                    if selection_anchor_result is not None
                    else None
                ),
                anchor_elapsed_ms=(
                    round(selection_anchor_result.elapsed_ms, 3)
                    if selection_anchor_result is not None
                    else None
                ),
            )
            self._signals.completed.emit(
                GalleryWindowResult(
                    generation=request.generation,
                    first=request.view_first,
                    last=min(max(request.view_first, last), max(0, total_count - 1)),
                    rows=rows,
                    total_count=total_count,
                    collection_revision=int(window.collection_revision),
                    requested_revision=request.collection_revision,
                    demand_generation=request.demand_generation,
                    priority=request.priority,
                    selection_anchor_result=selection_anchor_result,
                    purpose=request.purpose,
                    selection_anchor_retry_attempt=(
                        request.selection_anchor_retry_attempt
                    ),
                    surface_id=request.surface_id,
                    order_revision=window.order_revision,
                    refresh_after_commit=request.refresh_after_commit,
                    requested_ranges=ranges,
                    tracked_rows=tracked_rows,
                )
            )
        except Exception as exc:  # noqa: BLE001 - worker boundary
            emit_perf_event(
                "gallery_window_worker_failed",
                generation=request.generation,
                requested_revision=request.collection_revision,
                elapsed_ms=round((time.perf_counter() - started) * 1000.0, 3),
                error=type(exc).__name__,
            )
            self._signals.completed.emit(
                GalleryWindowResult(
                    generation=request.generation,
                    first=request.view_first,
                    last=request.view_first + max(0, request.limit) - 1,
                    rows={},
                    total_count=0,
                    collection_revision=0,
                    error=f"{type(exc).__name__}: {exc}",
                    requested_revision=request.collection_revision,
                    demand_generation=request.demand_generation,
                    priority=request.priority,
                    purpose=request.purpose,
                    selection_anchor_retry_attempt=(
                        request.selection_anchor_retry_attempt
                    ),
                    surface_id=request.surface_id,
                    refresh_after_commit=request.refresh_after_commit,
                )
            )
        else:
            request_backfill = getattr(request.query_service, "request_thumbnail_backfill", None)
            if request.request_backfill and callable(request_backfill):
                try:
                    for (first, _raw_limit), (_view_first, limit) in zip(raw_ranges, ranges, strict=True):
                        request_backfill(request.root, request.query, first, limit)
                except Exception as exc:  # noqa: BLE001 - window result is already delivered
                    emit_perf_event("gallery_window_backfill_failed", error=type(exc).__name__)


class GalleryWindowLoader(QObject):
    """Run one query at a time and prioritize the newest viewport's chunks."""

    resultReady = Signal(object)  # noqa: N815 - Qt signal naming convention
    requestsDropped = Signal(object)  # noqa: N815 - Qt signal naming convention

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._active_generation: int | None = None
        self._active_request: GalleryWindowRequest | None = None
        self._queued_requests: list[GalleryWindowRequest] = []
        self._latest_demand_generations: dict[str, int] = {}
        self._selection_generation = 0
        self._last_started_surface: str | None = None
        self._signals: dict[int, _GalleryWindowSignals] = {}

    def request(self, request: GalleryWindowRequest) -> None:
        if request.selection_generation < self._selection_generation:
            self.requestsDropped.emit((request.generation,))
            return
        if request.selection_generation > self._selection_generation:
            self._selection_generation = request.selection_generation
            self._latest_demand_generations.clear()
            dropped = tuple(queued.generation for queued in self._queued_requests)
            self._queued_requests.clear()
            if dropped:
                self.requestsDropped.emit(dropped)

        demand_generation = int(request.demand_generation)
        surface_id = str(request.surface_id)
        latest_generation = self._latest_demand_generations.get(surface_id, 0)
        if demand_generation > latest_generation:
            self._latest_demand_generations[surface_id] = demand_generation
            dropped = [
                queued.generation
                for queued in self._queued_requests
                if (
                    queued.surface_id == surface_id
                    and
                    int(queued.demand_generation) < demand_generation
                    and int(queued.demand_generation) > 0
                )
            ]
            self._queued_requests = [
                queued
                for queued in self._queued_requests
                if (
                    queued.surface_id != surface_id
                    or int(queued.demand_generation) >= demand_generation
                    or int(queued.demand_generation) == 0
                )
            ]
            if dropped:
                self.requestsDropped.emit(tuple(dropped))
        elif demand_generation > 0 and demand_generation < latest_generation:
            self.requestsDropped.emit((request.generation,))
            return

        signature = self._request_signature(request)
        if (
            not request.refresh_after_commit
            and self._active_request is not None
            and self._request_signature(self._active_request) == signature
        ):
            self.requestsDropped.emit((request.generation,))
            return
        for index, queued in enumerate(self._queued_requests):
            if request.refresh_after_commit != queued.refresh_after_commit:
                continue
            if not request.refresh_after_commit and self._request_signature(queued) != signature:
                continue
            self._queued_requests[index] = request
            self.requestsDropped.emit((queued.generation,))
            return
        if self._active_generation is not None:
            self._queued_requests.append(request)
            return
        self._start(request)

    def discard_queued(self, surface_id: str) -> None:
        """Drop queued viewport work owned only by *surface_id*."""

        surface_id = str(surface_id)
        dropped = tuple(
            request.generation
            for request in self._queued_requests
            if request.surface_id == surface_id and int(request.demand_generation) > 0
        )
        if not dropped:
            return
        dropped_set = set(dropped)
        self._queued_requests = [
            request
            for request in self._queued_requests
            if request.generation not in dropped_set
        ]
        self.requestsDropped.emit(dropped)

    def shutdown(self) -> None:
        dropped = tuple(request.generation for request in self._queued_requests)
        self._queued_requests.clear()
        self._latest_demand_generations = {
            surface_id: generation + 1
            for surface_id, generation in self._latest_demand_generations.items()
        }
        self._pool.clear()
        if dropped:
            self.requestsDropped.emit(dropped)

    def _start(self, request: GalleryWindowRequest) -> None:
        self._active_generation = request.generation
        self._active_request = request
        self._last_started_surface = request.surface_id
        signals = _GalleryWindowSignals()
        signals.completed.connect(self._handle_completed)
        self._signals[request.generation] = signals
        self._pool.start(_GalleryWindowWorker(request, signals))

    def _handle_completed(self, result: GalleryWindowResult) -> None:
        signals = self._signals.pop(result.generation, None)
        if signals is not None:
            signals.deleteLater()
        if result.generation != self._active_generation:
            return
        self._active_request = None
        try:
            self.resultReady.emit(result)
        finally:
            self._active_generation = None
            self._start_next()

    def _start_next(self) -> None:
        if not self._queued_requests:
            return
        if self._active_generation is not None:
            return
        best_index = min(
            range(len(self._queued_requests)),
            key=lambda index: (
                self._queued_requests[index].priority,
                not self._queued_requests[index].refresh_after_commit,
                self._queued_requests[index].surface_id == self._last_started_surface,
                -int(self._queued_requests[index].demand_generation),
            ),
        )
        self._start(self._queued_requests.pop(best_index))

    @staticmethod
    def _request_signature(request: GalleryWindowRequest) -> tuple[object, ...]:
        return (
            request.root,
            id(request.query_service),
            request.query,
            request.view_first,
            request.raw_first,
            request.limit,
            request.pending_source_ids,
            request.pending_source_count,
            request.pending_source_rows,
            request.pending_insertions,
            request.selection_anchor,
            request.request_backfill,
            request.collection_revision,
            request.purpose,
            request.selection_anchor_retry_attempt,
            request.surface_id,
            request.selection_generation,
            request.extra_ranges,
            request.previous_order_revision,
            request.tracked_assets,
            request.pending_source_assets,
        )


__all__ = [
    "GallerySelectionAnchor",
    "GallerySelectionAnchorResult",
    "GallerySelectionAnchorRetryTicket",
    "GalleryWindowLoader",
    "GalleryWindowRequest",
    "GalleryWindowResult",
]

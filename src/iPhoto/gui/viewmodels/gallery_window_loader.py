"""Generation-aware background loading for Gallery collection windows."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from iPhoto.application.dtos import AssetDTO
from iPhoto.domain.models.query import AssetQuery
from iPhoto.gui.viewmodels.asset_dto_converter import scan_row_to_dto
from iPhoto.infrastructure.services.performance_events import emit_perf_event


@dataclass(frozen=True, slots=True)
class GallerySelectionAnchor:
    """Path-stable selection identity carried across collection revisions."""

    path: Path
    asset_id: str
    previous_row: int


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


def _resolve_selection_anchor(
    request: GalleryWindowRequest,
    reader: Any,
    rows: dict[int, AssetDTO],
    collection_revision: int,
) -> GallerySelectionAnchorResult | None:
    anchor = request.selection_anchor
    if anchor is None:
        return None

    started = time.perf_counter()
    target = _normalized_path(anchor.path)
    for view_row, dto in rows.items():
        if _normalized_path(dto.abs_path) == target:
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
            window = reader(
                request.root,
                request.query,
                request.raw_first,
                request.limit + request.pending_source_count,
            )

            rows: dict[int, AssetDTO] = {}
            for raw_row in window.rows:
                rel = raw_row.get("rel") if isinstance(raw_row, dict) else None
                if not isinstance(rel, str) or not rel:
                    continue
                dto = scan_row_to_dto(request.root, rel, raw_row)
                if dto is None or str(dto.id) in request.pending_source_ids:
                    continue
                if len(rows) >= request.limit:
                    break
                rows[request.view_first + len(rows)] = dto

            loaded_count = len(rows)
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
            )
            last = request.view_first + loaded_count - 1
            emit_perf_event(
                "gallery_window_worker_finished",
                generation=request.generation,
                requested_revision=request.collection_revision,
                collection_revision=int(window.collection_revision),
                elapsed_ms=round((time.perf_counter() - started) * 1000.0, 3),
                rows=len(rows),
                total_count=total_count,
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
                )
            )
            request_backfill = getattr(request.query_service, "request_thumbnail_backfill", None)
            if request.request_backfill and callable(request_backfill):
                request_backfill(
                    request.root,
                    request.query,
                    request.raw_first,
                    request.limit,
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
                )
            )


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
        self._latest_demand_generation = 0
        self._signals: dict[int, _GalleryWindowSignals] = {}

    def request(self, request: GalleryWindowRequest) -> None:
        demand_generation = int(request.demand_generation)
        if demand_generation > self._latest_demand_generation:
            self._latest_demand_generation = demand_generation
            dropped = [
                queued.generation
                for queued in self._queued_requests
                if (
                    int(queued.demand_generation) < demand_generation
                    and int(queued.demand_generation) > 0
                )
            ]
            self._queued_requests = [
                queued
                for queued in self._queued_requests
                if (
                    int(queued.demand_generation) >= demand_generation
                    or int(queued.demand_generation) == 0
                )
            ]
            if dropped:
                self.requestsDropped.emit(tuple(dropped))
        elif demand_generation > 0 and demand_generation < self._latest_demand_generation:
            self.requestsDropped.emit((request.generation,))
            return

        signature = self._request_signature(request)
        if (
            self._active_request is not None
            and self._request_signature(self._active_request) == signature
        ):
            self.requestsDropped.emit((request.generation,))
            return
        for index, queued in enumerate(self._queued_requests):
            if self._request_signature(queued) != signature:
                continue
            self._queued_requests[index] = request
            self.requestsDropped.emit((queued.generation,))
            return
        if self._active_generation is not None:
            self._queued_requests.append(request)
            return
        self._start(request)

    def shutdown(self) -> None:
        dropped = tuple(request.generation for request in self._queued_requests)
        self._queued_requests.clear()
        self._latest_demand_generation += 1
        self._pool.clear()
        if dropped:
            self.requestsDropped.emit(dropped)

    def _start(self, request: GalleryWindowRequest) -> None:
        self._active_generation = request.generation
        self._active_request = request
        signals = _GalleryWindowSignals()
        signals.completed.connect(self._handle_completed)
        self._signals[request.generation] = signals
        self._pool.start(_GalleryWindowWorker(request, signals))

    def _handle_completed(self, result: GalleryWindowResult) -> None:
        signals = self._signals.pop(result.generation, None)
        if signals is not None:
            signals.deleteLater()
        self._active_generation = None
        self._active_request = None
        self.resultReady.emit(result)
        self._start_next()

    def _start_next(self) -> None:
        if not self._queued_requests:
            return
        if self._active_generation is not None:
            return
        best_index = min(
            range(len(self._queued_requests)),
            key=lambda index: self._queued_requests[index].priority,
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
        )


__all__ = [
    "GallerySelectionAnchor",
    "GallerySelectionAnchorResult",
    "GalleryWindowLoader",
    "GalleryWindowRequest",
    "GalleryWindowResult",
]

"""Generation-aware lightweight full-thumbnail hint loading."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from iPhoto.domain.models.query import AssetQuery
from iPhoto.infrastructure.services.performance_events import emit_perf_event

ThumbnailCandidateKind = Literal["predictive", "far_speculative"]


@dataclass(frozen=True, slots=True)
class GalleryThumbnailCandidate:
    row: int
    path: Path
    l2_cache_key: str
    rank: int
    kind: ThumbnailCandidateKind


@dataclass(frozen=True, slots=True)
class GalleryThumbnailHintRequest:
    request_id: int
    generation: int
    collection_revision: int
    root: Path
    query: AssetQuery
    query_service: Any
    first: int
    limit: int
    ordered_rows: tuple[int, ...]
    predictive_rows: frozenset[int]
    urgent: bool = False


@dataclass(frozen=True, slots=True)
class GalleryThumbnailHintResult:
    request_id: int
    generation: int
    collection_revision: int
    root: Path
    query: AssetQuery
    first: int
    limit: int
    candidates: tuple[GalleryThumbnailCandidate, ...]
    elapsed_ms: float
    error: str | None = None
    urgent: bool = False


class _HintSignals(QObject):
    completed = Signal(object)


class _HintWorker(QRunnable):
    def __init__(self, request: GalleryThumbnailHintRequest, signals: _HintSignals) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._request = request
        self._signals = signals

    def run(self) -> None:  # pragma: no cover - Qt worker boundary
        started = time.perf_counter()
        request = self._request
        try:
            window = request.query_service.read_thumbnail_hint_window(
                request.root,
                request.query,
                request.first,
                request.limit,
            )
            by_row: dict[int, GalleryThumbnailCandidate] = {}
            for offset, row in enumerate(window.rows):
                rel = row.get("rel") if isinstance(row, dict) else None
                cache_key = row.get("thumb_cache_key") if isinstance(row, dict) else None
                if (
                    not isinstance(rel, str)
                    or not rel
                    or not isinstance(cache_key, str)
                    or not cache_key
                ):
                    continue
                absolute_row = request.first + offset
                by_row[absolute_row] = GalleryThumbnailCandidate(
                    row=absolute_row,
                    path=request.root / Path(rel),
                    l2_cache_key=cache_key,
                    rank=0,
                    kind=(
                        "predictive"
                        if absolute_row in request.predictive_rows
                        else "far_speculative"
                    ),
                )
            candidates = tuple(
                GalleryThumbnailCandidate(
                    row=row,
                    path=by_row[row].path,
                    l2_cache_key=by_row[row].l2_cache_key,
                    rank=rank,
                    kind=by_row[row].kind,
                )
                for rank, row in enumerate(request.ordered_rows)
                if row in by_row
            )
            result = GalleryThumbnailHintResult(
                request_id=request.request_id,
                generation=request.generation,
                collection_revision=request.collection_revision,
                root=request.root,
                query=request.query,
                first=request.first,
                limit=request.limit,
                candidates=candidates,
                elapsed_ms=(time.perf_counter() - started) * 1000.0,
                urgent=request.urgent,
            )
        except Exception as exc:  # noqa: BLE001 - worker boundary
            result = GalleryThumbnailHintResult(
                request_id=request.request_id,
                generation=request.generation,
                collection_revision=request.collection_revision,
                root=request.root,
                query=request.query,
                first=request.first,
                limit=request.limit,
                candidates=(),
                elapsed_ms=(time.perf_counter() - started) * 1000.0,
                error=f"{type(exc).__name__}: {exc}",
                urgent=request.urgent,
            )
        self._signals.completed.emit(result)


class GalleryThumbnailHintLoader(QObject):
    """Run the newest lightweight hint request, with one urgent lane for recovery."""

    resultReady = Signal(object)  # noqa: N815 - Qt signal naming

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._normal_pool = QThreadPool(self)
        self._normal_pool.setMaxThreadCount(1)
        self._urgent_pool = QThreadPool(self)
        self._urgent_pool.setMaxThreadCount(1)
        self._active_normal = False
        self._active_urgent = False
        self._queued_normal: GalleryThumbnailHintRequest | None = None
        self._queued_urgent: GalleryThumbnailHintRequest | None = None
        self._latest_request_id = 0
        self._latest_generation = 0
        self._valid_request_ids: set[int] = set()
        self._signals: dict[int, _HintSignals] = {}

    def request(self, request: GalleryThumbnailHintRequest) -> None:
        request_id = int(request.request_id)
        generation = int(request.generation)
        self._latest_request_id = max(self._latest_request_id, request_id)
        if generation > self._latest_generation:
            self._latest_generation = generation
            self._valid_request_ids.clear()
        self._valid_request_ids.add(request_id)
        if request.urgent:
            if self._active_urgent:
                self._queued_urgent = request
                return
            self._start(request)
            return
        if self._active_normal:
            self._queued_normal = request
            return
        self._start(request)

    def cancel_pending(self) -> None:
        self._queued_normal = None
        self._queued_urgent = None
        self._latest_request_id += 1
        self._valid_request_ids.clear()
        self._normal_pool.clear()
        self._urgent_pool.clear()

    def shutdown(self) -> None:
        self.cancel_pending()

    def _start(self, request: GalleryThumbnailHintRequest) -> None:
        if request.urgent:
            self._active_urgent = True
        else:
            self._active_normal = True
        signals = _HintSignals()
        signals.completed.connect(self._handle_completed)
        self._signals[request.request_id] = signals
        pool = self._urgent_pool if request.urgent else self._normal_pool
        pool.start(_HintWorker(request, signals))

    def _handle_completed(self, result: GalleryThumbnailHintResult) -> None:
        signals = self._signals.pop(result.request_id, None)
        if signals is not None:
            signals.deleteLater()
        if result.urgent:
            self._active_urgent = False
        else:
            self._active_normal = False
        if (
            result.generation < self._latest_generation
            or result.request_id not in self._valid_request_ids
        ):
            emit_perf_event(
                "gallery_thumbnail_hint_finished",
                generation=result.generation,
                candidates=len(result.candidates),
                elapsed_ms=round(result.elapsed_ms, 3),
                error="stale_request",
            )
            self._start_next()
            return
        emit_perf_event(
            "gallery_thumbnail_hint_finished",
            generation=result.generation,
            candidates=len(result.candidates),
            elapsed_ms=round(result.elapsed_ms, 3),
            error=result.error,
        )
        self.resultReady.emit(result)
        self._start_next()

    def _start_next(self) -> None:
        queued_urgent = self._queued_urgent
        if queued_urgent is not None and not self._active_urgent:
            self._queued_urgent = None
            self._start(queued_urgent)
        queued_normal = self._queued_normal
        if queued_normal is not None and not self._active_normal:
            self._queued_normal = None
            self._start(queued_normal)


__all__ = [
    "GalleryThumbnailCandidate",
    "GalleryThumbnailHintLoader",
    "GalleryThumbnailHintRequest",
    "GalleryThumbnailHintResult",
    "ThumbnailCandidateKind",
]

"""Generation-aware lightweight full-thumbnail hint loading."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from iPhoto.domain.models.query import AssetQuery
from iPhoto.infrastructure.services.performance_events import emit_perf_event

ThumbnailCandidateKind = Literal["guard", "far_speculative"]


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
    guard_rows: frozenset[int]


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
                        "guard"
                        if absolute_row in request.guard_rows
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
            )
        self._signals.completed.emit(result)


class GalleryThumbnailHintLoader(QObject):
    """Run one lightweight hint read and coalesce pending demand to the newest request."""

    resultReady = Signal(object)  # noqa: N815 - Qt signal naming

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._active = False
        self._queued: GalleryThumbnailHintRequest | None = None
        self._signals: _HintSignals | None = None
        self._latest_request_id = 0
        self._minimum_valid_request_id = 0

    def request(self, request: GalleryThumbnailHintRequest) -> None:
        self._latest_request_id = max(self._latest_request_id, int(request.request_id))
        if self._active:
            self._queued = request
            return
        self._start(request)

    def discard_queued(self) -> None:
        """Drop coalesced work without invalidating the active read."""

        self._queued = None

    def cancel_pending(self) -> None:
        self.discard_queued()
        self._minimum_valid_request_id = self._latest_request_id + 1
        self._pool.clear()

    def shutdown(self) -> None:
        self.cancel_pending()

    def _start(self, request: GalleryThumbnailHintRequest) -> None:
        self._active = True
        signals = _HintSignals()
        signals.completed.connect(self._handle_completed)
        self._signals = signals
        self._pool.start(_HintWorker(request, signals))

    def _handle_completed(self, result: GalleryThumbnailHintResult) -> None:
        if self._signals is not None:
            self._signals.deleteLater()
        self._signals = None
        self._active = False
        if result.request_id < self._minimum_valid_request_id:
            queued = self._queued
            self._queued = None
            if queued is not None:
                self._start(queued)
            return
        emit_perf_event(
            "gallery_thumbnail_hint_finished",
            generation=result.generation,
            candidates=len(result.candidates),
            elapsed_ms=round(result.elapsed_ms, 3),
            error=result.error,
        )
        self.resultReady.emit(result)
        queued = self._queued
        self._queued = None
        if queued is not None:
            self._start(queued)


__all__ = [
    "GalleryThumbnailCandidate",
    "GalleryThumbnailHintLoader",
    "GalleryThumbnailHintRequest",
    "GalleryThumbnailHintResult",
    "ThumbnailCandidateKind",
]

"""Library scan orchestration shared by GUI workers and compatibility facades."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...domain.models.scan import ScanBatchCommitted
from ..ports import AssetRepositoryPort, MediaScannerPort

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScanLibraryRequest:
    root: Path
    include: Iterable[str]
    exclude: Iterable[str]
    existing_index: dict[str, dict[str, Any]] | None = None
    progress_callback: Callable[[int, int], None] | None = None
    is_cancelled: Callable[[], bool] | None = None
    row_transform: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    chunk_callback: Callable[[list[dict[str, Any]]], None] | None = None
    visible_chunk_callback: Callable[[list[dict[str, Any]]], None] | None = None
    scan_batch_callback: Callable[[ScanBatchCommitted], None] | None = None
    batch_failed_callback: Callable[[int], None] | None = None
    chunk_size: int = 500
    persist_chunks: bool = True
    scan_job_id: str | None = None
    scan_stage_elapsed_ms: dict[str, float] | None = None


@dataclass(frozen=True)
class ScanLibraryResult:
    rows: list[dict[str, Any]]
    failed_count: int = 0
    scan_job_id: str | None = None


class ScanLibraryUseCase:
    """Discover and persist scanned facts without owning UI transport."""

    def __init__(
        self,
        *,
        scanner: MediaScannerPort,
        asset_repository: AssetRepositoryPort,
    ) -> None:
        self._scanner = scanner
        self._asset_repository = asset_repository

    def execute(self, request: ScanLibraryRequest) -> ScanLibraryResult:
        rows: list[dict[str, Any]] = []
        chunk: list[dict[str, Any]] = []
        failed_count = 0
        chunk_size = max(1, int(request.chunk_size))
        scan_started = _monotonic_ms()

        for scanned_row in self._scanner.scan(
            request.root,
            request.include,
            request.exclude,
            existing_index=request.existing_index,
            progress_callback=request.progress_callback,
        ):
            if request.is_cancelled is not None and request.is_cancelled():
                break

            row = dict(scanned_row)
            if request.row_transform is not None:
                row = request.row_transform(row)

            rows.append(row)
            if request.persist_chunks:
                chunk.append(row)
                if len(chunk) >= chunk_size:
                    failed_count += self._merge_chunk(
                        chunk,
                        request,
                        metadata_elapsed_ms=round(_monotonic_ms() - scan_started, 3),
                    )
                    chunk = []

        if (
            request.persist_chunks
            and chunk
            and not (request.is_cancelled is not None and request.is_cancelled())
        ):
            failed_count += self._merge_chunk(
                chunk,
                request,
                metadata_elapsed_ms=round(_monotonic_ms() - scan_started, 3),
            )

        return ScanLibraryResult(
            rows=rows,
            failed_count=failed_count,
            scan_job_id=request.scan_job_id,
        )

    def merge_chunk(
        self,
        chunk: list[dict[str, Any]],
        request: ScanLibraryRequest,
    ) -> int:
        """Persist one already-discovered chunk through the same merge policy."""

        return self._merge_chunk(chunk, request)

    def _merge_chunk(
        self,
        chunk: list[dict[str, Any]],
        request: ScanLibraryRequest,
        *,
        metadata_elapsed_ms: float | None = None,
    ) -> int:
        started = _monotonic_ms()
        try:
            emitted_chunk = self._asset_repository.merge_scan_rows(chunk)
        except Exception:
            LOGGER.exception("Failed to persist scan chunk of %s items", len(chunk))
            LOGGER.debug(
                "scan_batch_failed rows=%s elapsed_ms=%s",
                len(chunk),
                round(_monotonic_ms() - started, 3),
            )
            if request.batch_failed_callback is not None:
                request.batch_failed_callback(len(chunk))
            return len(chunk)

        LOGGER.debug(
            "scan_batch_committed rows=%s requested_rows=%s elapsed_ms=%s",
            len(emitted_chunk),
            len(chunk),
            round(_monotonic_ms() - started, 3),
        )
        commit_elapsed_ms = round(_monotonic_ms() - started, 3)
        ready_chunk = _ready_visible_rows(emitted_chunk)
        collection_revision = _collection_revision_from_rows(emitted_chunk)
        stage_elapsed_ms = dict(request.scan_stage_elapsed_ms or {})
        if metadata_elapsed_ms is not None:
            stage_elapsed_ms["metadata_extraction"] = metadata_elapsed_ms
        stage_elapsed_ms["db_commit"] = commit_elapsed_ms
        batch = None
        if request.scan_job_id and ready_chunk:
            batch = ScanBatchCommitted(
                job_id=request.scan_job_id,
                root=request.root,
                collection_revision=collection_revision,
                ready_count=len(ready_chunk),
                rows=ready_chunk,
                stage_elapsed_ms=stage_elapsed_ms,
            )
        append_scan_event = getattr(self._asset_repository, "append_scan_event", None)
        if request.scan_job_id and callable(append_scan_event):
            append_scan_event(
                request.scan_job_id,
                "batch_committed",
                {
                    "requested_rows": len(chunk),
                    "rows": len(emitted_chunk),
                    "ready_rows": len(ready_chunk),
                    "commit_elapsed_ms": commit_elapsed_ms,
                    "stage_elapsed_ms": stage_elapsed_ms,
                    "collection_revision": collection_revision,
                },
            )
        if request.chunk_callback is not None:
            request.chunk_callback(emitted_chunk)
        if request.visible_chunk_callback is not None and ready_chunk:
            request.visible_chunk_callback(ready_chunk)
        if request.scan_batch_callback is not None and batch is not None:
            request.scan_batch_callback(batch)
        return 0


def _monotonic_ms() -> float:
    return time.perf_counter() * 1000


def _ready_visible_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ready: list[dict[str, Any]] = []
    for row in rows:
        if row.get("thumbnail_state") != "ready":
            continue
        if not str(row.get("thumb_cache_key") or "").strip():
            continue
        ready.append(row)
    return ready


def _collection_revision_from_rows(rows: list[dict[str, Any]]) -> int:
    revision = 0
    for row in rows:
        try:
            revision = max(revision, int(row.get("index_revision") or 0))
        except (TypeError, ValueError):
            continue
    return revision


__all__ = ["ScanLibraryRequest", "ScanLibraryResult", "ScanLibraryUseCase"]

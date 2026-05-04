"""Library scan orchestration shared by GUI workers and compatibility facades."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
    batch_failed_callback: Callable[[int], None] | None = None
    chunk_size: int = 50
    persist_chunks: bool = True


@dataclass(frozen=True)
class ScanLibraryResult:
    rows: list[dict[str, Any]]
    failed_count: int = 0


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
                    failed_count += self._merge_chunk(chunk, request)
                    chunk = []

        if (
            request.persist_chunks
            and chunk
            and not (request.is_cancelled is not None and request.is_cancelled())
        ):
            failed_count += self._merge_chunk(chunk, request)

        return ScanLibraryResult(rows=rows, failed_count=failed_count)

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
    ) -> int:
        try:
            emitted_chunk = self._asset_repository.merge_scan_rows(chunk)
        except Exception:
            LOGGER.exception("Failed to persist scan chunk of %s items", len(chunk))
            if request.batch_failed_callback is not None:
                request.batch_failed_callback(len(chunk))
            return len(chunk)

        if request.chunk_callback is not None:
            request.chunk_callback(emitted_chunk)
        return 0


__all__ = ["ScanLibraryRequest", "ScanLibraryResult", "ScanLibraryUseCase"]

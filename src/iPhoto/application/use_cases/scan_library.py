"""Library scan orchestration shared by GUI workers and compatibility facades."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..ports import AssetRepositoryPort, MediaScannerPort
from .scan_models import ScanMode

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScanLibraryRequest:
    root: Path
    include: Iterable[str]
    exclude: Iterable[str]
    existing_rows_resolver: Callable[[list[str]], dict[str, dict[str, Any]]] | None = None
    existing_index: dict[str, dict[str, Any]] | None = None
    progress_callback: Callable[[int, int], None] | None = None
    is_cancelled: Callable[[], bool] | None = None
    row_transform: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    chunk_callback: Callable[[list[dict[str, Any]]], None] | None = None
    batch_failed_callback: Callable[[int], None] | None = None
    collect_rows: bool = True
    chunk_size: int = 50
    persist_chunks: bool = True
    scan_id: str | None = None
    mode: ScanMode = ScanMode.BACKGROUND
    generate_micro_thumbnails: bool | Callable[[], bool] = True


@dataclass(frozen=True)
class ScanLibraryResult:
    rows: list[dict[str, Any]]
    failed_count: int = 0
    processed_count: int = 0
    cancelled: bool = False


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
        processed_count = 0
        cancelled = False

        for scanned_row in self._scan_rows(request):
            if request.is_cancelled is not None and request.is_cancelled():
                cancelled = True
                break

            row = dict(scanned_row)
            if request.row_transform is not None:
                row = request.row_transform(row)

            processed_count += 1
            if request.collect_rows:
                rows.append(row)
            if request.persist_chunks:
                chunk.append(row)
                if len(chunk) >= chunk_size:
                    failed_count += self._merge_chunk(chunk, request)
                    chunk = []

        if request.persist_chunks and chunk:
            failed_count += self._merge_chunk(chunk, request)

        if request.is_cancelled is not None and request.is_cancelled():
            cancelled = True

        return ScanLibraryResult(
            rows=rows,
            failed_count=failed_count,
            processed_count=processed_count,
            cancelled=cancelled,
        )

    def _scan_rows(
        self,
        request: ScanLibraryRequest,
    ):
        scan_method = self._scanner.scan
        signature = self._scan_signature(scan_method)
        scan_kwargs: dict[str, Any] = {}

        if self._supports_scan_kw(signature, "existing_rows_resolver"):
            scan_kwargs["existing_rows_resolver"] = request.existing_rows_resolver
        elif (
            self._supports_scan_kw(signature, "existing_index")
            and request.existing_index is not None
        ):
            scan_kwargs["existing_index"] = request.existing_index

        if self._supports_scan_kw(signature, "progress_callback"):
            scan_kwargs["progress_callback"] = request.progress_callback
        if self._supports_scan_kw(signature, "is_cancelled"):
            scan_kwargs["is_cancelled"] = request.is_cancelled
        if self._supports_scan_kw(signature, "generate_micro_thumbnails"):
            scan_kwargs["generate_micro_thumbnails"] = request.generate_micro_thumbnails

        return scan_method(
            request.root,
            request.include,
            request.exclude,
            **scan_kwargs,
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
    ) -> int:
        try:
            emitted_chunk = self._asset_repository.merge_scan_rows(
                chunk,
                scan_id=request.scan_id,
            )
        except Exception:
            LOGGER.exception("Failed to persist scan chunk of %s items", len(chunk))
            if request.batch_failed_callback is not None:
                request.batch_failed_callback(len(chunk))
            return len(chunk)

        if request.chunk_callback is not None:
            request.chunk_callback(emitted_chunk)
        return 0

    @staticmethod
    def _scan_signature(scan_method: Callable[..., Any]) -> inspect.Signature | None:
        try:
            return inspect.signature(scan_method)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _supports_scan_kw(
        signature: inspect.Signature | None,
        name: str,
    ) -> bool:
        if signature is None:
            return False
        for parameter in signature.parameters.values():
            if parameter.kind == inspect.Parameter.VAR_KEYWORD:
                return True
        return name in signature.parameters


__all__ = ["ScanLibraryRequest", "ScanLibraryResult", "ScanLibraryUseCase"]

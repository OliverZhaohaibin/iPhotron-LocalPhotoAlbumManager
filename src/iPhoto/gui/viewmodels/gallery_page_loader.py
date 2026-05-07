"""Background helpers for streaming gallery window slices."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from iPhoto.application.dtos import AssetDTO
from iPhoto.domain.models.query import AssetQuery

from .asset_dto_converter import (
    scan_row_is_thumbnail as _scan_row_is_thumbnail_fn,
    scan_row_to_dto as _scan_row_to_dto_fn,
)
from .asset_paging import should_validate_paths as _should_validate_paths_fn


@dataclass(frozen=True)
class GalleryPageRequest:
    """Describe one async window-slice load request."""

    request_id: int
    selection_revision: int
    root: Path
    query: AssetQuery
    first: int
    last: int
    refetch_window: bool = False
    clear_pending_scan_refresh: bool = False


@dataclass(frozen=True)
class GalleryPageResult:
    """Materialized rows loaded for one window-slice request."""

    request_id: int
    selection_revision: int
    first: int
    last: int
    rows: Dict[int, AssetDTO]
    refetch_window: bool = False
    clear_pending_scan_refresh: bool = False


class _GalleryPageLoaderSignals(QObject):
    pageLoaded = Signal(object)
    pageFailed = Signal(int, int)


class _GalleryPageTask(QRunnable):
    """Load one gallery window slice off the GUI thread."""

    def __init__(
        self,
        *,
        asset_query_service,
        library_root: Path | None,
        request: GalleryPageRequest,
        signals: _GalleryPageLoaderSignals,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._asset_query_service = asset_query_service
        self._library_root = library_root
        self._request = request
        self._signals = signals

    def run(self) -> None:  # pragma: no cover - exercised through Qt worker dispatch
        try:
            sliced_query = copy.deepcopy(self._request.query)
            sliced_query.offset = self._request.first
            sliced_query.limit = max(0, self._request.last - self._request.first + 1)
            validate_paths = _should_validate_paths_fn(sliced_query, self._library_root)

            rows: Dict[int, AssetDTO] = {}
            for offset, row in enumerate(
                self._asset_query_service.read_query_asset_rows(
                    self._request.root,
                    sliced_query,
                )
            ):
                view_rel = row.get("rel") if isinstance(row, dict) else None
                if not isinstance(view_rel, str) or not view_rel:
                    continue
                if _scan_row_is_thumbnail_fn(view_rel, row):
                    continue
                dto = _scan_row_to_dto_fn(self._request.root, view_rel, row)
                if dto is None:
                    continue
                if validate_paths and not dto.abs_path.exists():
                    continue
                rows[self._request.first + offset] = dto

            self._signals.pageLoaded.emit(
                GalleryPageResult(
                    request_id=self._request.request_id,
                    selection_revision=self._request.selection_revision,
                    first=self._request.first,
                    last=self._request.last,
                    rows=rows,
                    refetch_window=self._request.refetch_window,
                    clear_pending_scan_refresh=self._request.clear_pending_scan_refresh,
                )
            )
        except Exception:
            self._signals.pageFailed.emit(
                self._request.request_id,
                self._request.selection_revision,
            )


class GalleryPageLoader(QObject):
    """Own worker dispatch for gallery window-slice fetches."""

    pageLoaded = Signal(object)
    pageFailed = Signal(int, int)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread_pool = QThreadPool.globalInstance()
        self._latest_request_id = 0
        self._latest_selection_revision = 0

    def load(self, *, asset_query_service, library_root: Path | None, request: GalleryPageRequest) -> None:
        if asset_query_service is None:
            return
        self._latest_request_id = max(self._latest_request_id, request.request_id)
        self._latest_selection_revision = max(
            self._latest_selection_revision,
            request.selection_revision,
        )
        # Keep request signals independent from the loader lifetime so an
        # in-flight task can finish quietly after the adapter/model tears down.
        signals = _GalleryPageLoaderSignals()
        signals.pageLoaded.connect(self._handle_page_loaded)
        signals.pageFailed.connect(self._handle_page_failed)
        task = _GalleryPageTask(
            asset_query_service=asset_query_service,
            library_root=library_root,
            request=request,
            signals=signals,
        )
        self._thread_pool.start(task)

    def _handle_page_loaded(self, result: GalleryPageResult) -> None:
        if result.selection_revision != self._latest_selection_revision:
            return
        if result.request_id < self._latest_request_id:
            return
        self.pageLoaded.emit(result)

    def _handle_page_failed(self, request_id: int, selection_revision: int) -> None:
        if selection_revision != self._latest_selection_revision:
            return
        if request_id < self._latest_request_id:
            return
        self.pageFailed.emit(request_id, selection_revision)

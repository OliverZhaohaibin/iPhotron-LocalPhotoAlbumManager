"""Background QRunnable workers for asset loading and page fetching."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, List

from PySide6.QtCore import QObject, QRunnable, Signal

from iPhoto.application.dtos import AssetDTO
from iPhoto.domain.models.query import AssetQuery

if TYPE_CHECKING:
    from iPhoto.gui.viewmodels.asset_data_source import AssetDataSource

_logger = logging.getLogger(__name__)


class _AssetLoadSignals(QObject):
    completed = Signal(int, list, int)


class _AssetLoadWorker(QRunnable):
    def __init__(
        self,
        data_source: AssetDataSource,
        query: AssetQuery,
        generation: int,
        validate_paths: bool,
    ) -> None:
        super().__init__()
        self._data_source = data_source
        self._query = query
        self._generation = generation
        self._validate_paths = validate_paths
        self.signals = _AssetLoadSignals()

    def run(self) -> None:
        _logger.info(
            "[LOAD-WORKER] Starting load: is_favorite=%s, album_path=%s, repo_id=%s",
            self._query.is_favorite, self._query.album_path, id(self._data_source._repo),
        )
        dtos: List[AssetDTO] = []
        raw_count = 0
        assets = self._data_source._repo.find_by_query(self._query)
        for asset in assets:
            raw_count += 1
            if self._data_source._is_thumbnail_asset(asset):
                continue
            abs_path = self._data_source._resolve_abs_path(asset.path)
            if self._validate_paths and not self._data_source._path_exists_cached(abs_path):
                continue
            dtos.append(self._data_source._to_dto(asset))
        fav_count = sum(1 for d in dtos if d.is_favorite)
        _logger.info(
            "[LOAD-WORKER] Loaded %d DTOs from %d raw rows (%d favorites)",
            len(dtos), raw_count, fav_count,
        )
        self.signals.completed.emit(self._generation, dtos, raw_count)


class _AssetPageSignals(QObject):
    completed = Signal(int, int, list, int)


class _AssetPageWorker(QRunnable):
    def __init__(
        self,
        data_source: AssetDataSource,
        query: AssetQuery,
        generation: int,
        offset: int,
        validate_paths: bool,
    ) -> None:
        super().__init__()
        self._data_source = data_source
        self._query = query
        self._generation = generation
        self._offset = offset
        self._validate_paths = validate_paths
        self.signals = _AssetPageSignals()

    def run(self) -> None:
        query = AssetQuery(**self._query.__dict__)
        query.offset = self._offset
        query.limit = self._query.limit

        dtos: List[AssetDTO] = []
        raw_count = 0
        assets = self._data_source._repo.find_by_query(query)
        for asset in assets:
            raw_count += 1
            if self._data_source._is_thumbnail_asset(asset):
                continue
            abs_path = self._data_source._resolve_abs_path(asset.path)
            if self._validate_paths and not self._data_source._path_exists_cached(abs_path):
                continue
            dtos.append(self._data_source._to_dto(asset))
        self.signals.completed.emit(self._generation, self._offset, dtos, raw_count)

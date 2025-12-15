from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple, Set

from ....cache.index_store import IndexStore
from ....core.merger import PhotoStreamMerger
from ....gui.ui.tasks.asset_loader_worker import build_asset_entry, normalize_featured


class AssetDataSource:
    def fetch_next(self, limit: int) -> List[Dict[str, object]]:
        raise NotImplementedError

    def has_more(self) -> bool:
        raise NotImplementedError

    def reset(self) -> None:
        raise NotImplementedError


class SingleAlbumSource(AssetDataSource):
    """Cursor/seek paginated source for a single album."""

    def __init__(
        self,
        root: Path,
        *,
        filter_params: Optional[Dict[str, object]] = None,
        featured: Optional[Iterable[str]] = None,
    ) -> None:
        self._root = root
        self._store = IndexStore(root)
        self._filter_params = filter_params or {}
        self._featured = normalize_featured(featured or [])
        self._cursor: Optional[Tuple[Optional[str], Optional[str]]] = None
        self._exhausted = False
        self._dir_cache: Dict[Path, Optional[Set[str]]] = {}

    def _path_exists(self, path: Path) -> bool:
        parent = path.parent
        names = self._dir_cache.get(parent)
        if names is None:
            try:
                names = {entry.name for entry in parent.iterdir()}
            except OSError:
                names = set()
            self._dir_cache[parent] = names
        return path.name in names

    def fetch_next(self, limit: int) -> List[Dict[str, object]]:
        if self._exhausted or limit <= 0:
            return []

        rows = self._store.read_geometry_page(
            limit=limit,
            cursor=self._cursor,
            filter_params=self._filter_params,
            sort_by_date=True,
        )
        if not rows:
            self._exhausted = True
            return []

        entries: List[Dict[str, object]] = []
        for row in rows:
            entry = build_asset_entry(
                self._root,
                row,
                self._featured,
                self._store,
                path_exists=self._path_exists,
            )
            if entry is not None:
                entries.append(entry)

        last = rows[-1]
        self._cursor = (last.get("dt"), last.get("id"))
        if len(rows) < limit:
            self._exhausted = True
        return entries

    def has_more(self) -> bool:
        return not self._exhausted

    def reset(self) -> None:
        self._cursor = None
        self._exhausted = False
        self._dir_cache.clear()


class MergedAlbumSource(AssetDataSource):
    """Data source backed by a PhotoStreamMerger (e.g., aggregated albums)."""

    def __init__(
        self,
        root: Path,
        merger_factory,
        *,
        featured: Optional[Iterable[str]] = None,
    ) -> None:
        self._root = root
        self._merger_factory = merger_factory
        self._featured = normalize_featured(featured or [])
        self._store = IndexStore(root)
        self._dir_cache: Dict[Path, Optional[Set[str]]] = {}
        self._merger: PhotoStreamMerger = merger_factory()

    def _path_exists(self, path: Path) -> bool:
        parent = path.parent
        names = self._dir_cache.get(parent)
        if names is None:
            try:
                names = {entry.name for entry in parent.iterdir()}
            except OSError:
                names = set()
            self._dir_cache[parent] = names
        return path.name in names

    def fetch_next(self, limit: int) -> List[Dict[str, object]]:
        if limit <= 0 or not self._merger.has_more():
            return []

        rows = self._merger.fetch_next_batch(limit)
        entries: List[Dict[str, object]] = []
        for row in rows:
            entry = build_asset_entry(
                self._root,
                row,
                self._featured,
                self._store,
                path_exists=self._path_exists,
            )
            if entry is not None:
                entries.append(entry)
        return entries

    def has_more(self) -> bool:
        return self._merger.has_more()

    def reset(self) -> None:
        self._dir_cache.clear()
        self._merger = self._merger_factory()

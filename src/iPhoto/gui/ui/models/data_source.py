from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple, Set
import threading

from ....cache.index_store import IndexStore
from ....core.merger import PhotoStreamMerger
from ....gui.ui.tasks.asset_loader_worker import build_asset_entry, normalize_featured


class AssetDataSource(ABC):
    @abstractmethod
    def fetch_next(self, limit: int) -> List[Dict[str, object]]:
        ...

    @abstractmethod
    def has_more(self) -> bool:
        ...

    @abstractmethod
    def reset(self) -> None:
        ...


class SingleAlbumSource(AssetDataSource):
    """Cursor/seek paginated source for a single album."""

    def __init__(
        self,
        root: Path,
        *,
        filter_params: Optional[Dict[str, object]] = None,
        featured: Optional[Iterable[str]] = None,
        check_exists: bool = True,
    ) -> None:
        self._root = root
        self._store = IndexStore(root)
        self._filter_params = filter_params or {}
        self._featured = normalize_featured(featured or [])
        self._cursor: Optional[Tuple[Optional[str], Optional[str]]] = None
        self._exhausted = False
        self._dir_cache: Dict[Path, Optional[Set[str]]] = {}
        self._check_exists = check_exists

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
                path_exists=(self._path_exists if self._check_exists else None),
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
        check_exists: bool = True,
    ) -> None:
        self._root = root
        self._merger_factory = merger_factory
        self._featured = normalize_featured(featured or [])
        self._store_cache: Dict[Path, IndexStore] = {}
        self._dir_cache: Dict[Path, Optional[Set[str]]] = {}
        self._merger: PhotoStreamMerger | None = None
        self._check_exists = check_exists
        self._merger_lock = threading.Lock()

    def _ensure_merger(self) -> PhotoStreamMerger:
        with self._merger_lock:
            if self._merger is None:
                self._merger = self._merger_factory()
        return self._merger

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
        merger = self._ensure_merger()
        if limit <= 0 or not merger.has_more():
            return []

        rows = merger.fetch_next_batch(limit)
        entries: List[Dict[str, object]] = []
        for row in rows:
            album_root = Path(row.pop("_album_root", self._root))
            store = self._store_cache.setdefault(
                album_root, IndexStore(album_root, lazy_init=True)
            )
            entry = build_asset_entry(
                album_root,
                row,
                self._featured,
                store,
                path_exists=(self._path_exists if self._check_exists else None),
            )
            if entry is not None:
                entries.append(entry)
        return entries

    def has_more(self) -> bool:
        if self._merger is None:
            # Unknown until first fetch; assume more to kick off background load.
            return True
        return self._merger.has_more()

    def reset(self) -> None:
        self._dir_cache.clear()
        self._merger = None
        self._store_cache.clear()

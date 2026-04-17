"""Shared Location/Map selection state for navigation flows."""

from __future__ import annotations

from pathlib import Path
from typing import Literal


LocationSelectionMode = Literal["inactive", "map", "gallery", "cluster_gallery"]


class LocationSelectionSession:
    """Own the cached Location selection snapshot and navigation mode."""

    def __init__(self) -> None:
        self._root: Path | None = None
        self._request_serial = 0
        self._mode: LocationSelectionMode = "inactive"
        self._invalidated = False
        self._has_snapshot = False
        # Keys are POSIX-normalized library_relative strings (Path(rel).as_posix());
        # values are the corresponding asset objects. Provides O(1) upsert/remove.
        # The sorted list (_full_assets) is rebuilt lazily via full_assets().
        self._asset_index: dict[str, object] = {}
        self._full_assets: list = []
        self._list_dirty: bool = False

    @property
    def root(self) -> Path | None:
        return self._root

    @property
    def mode(self) -> LocationSelectionMode:
        return self._mode

    @property
    def invalidated(self) -> bool:
        return self._invalidated

    @property
    def has_snapshot(self) -> bool:
        return self._has_snapshot

    @property
    def request_serial(self) -> int:
        return self._request_serial

    def begin_load(self, root: Path) -> int:
        normalized_root = Path(root)
        if self._root != normalized_root:
            self._asset_index = {}
            self._full_assets = []
            self._list_dirty = False
            self._has_snapshot = False
        self._root = normalized_root
        self._invalidated = True
        self._request_serial += 1
        return self._request_serial

    def accept_loaded(self, serial: int, root: Path, assets: list) -> bool:
        normalized_root = Path(root)
        if serial != self._request_serial or self._root != normalized_root:
            return False
        self._root = normalized_root
        self._asset_index = {}
        for a in assets:
            rel = getattr(a, "library_relative", None)
            if isinstance(rel, str) and rel:
                self._asset_index[Path(rel).as_posix()] = a
        self._list_dirty = True
        self._has_snapshot = True
        self._invalidated = False
        return True

    def set_mode(self, mode: LocationSelectionMode) -> None:
        self._mode = mode

    def invalidate(self) -> None:
        self._invalidated = True

    def full_assets(self) -> list:
        if self._list_dirty:
            self._full_assets = sorted(
                list(self._asset_index.values()),
                key=lambda asset: str(getattr(asset, "library_relative", "")),
            )
            self._list_dirty = False
        return list(self._full_assets)

    def replace_assets(self, assets: list) -> None:
        self._asset_index = {}
        for a in assets:
            rel = getattr(a, "library_relative", None)
            if isinstance(rel, str) and rel:
                self._asset_index[Path(rel).as_posix()] = a
        self._list_dirty = True
        self._has_snapshot = True
        self._invalidated = False

    def upsert_asset(self, asset: object) -> bool:
        library_relative = getattr(asset, "library_relative", None)
        if not isinstance(library_relative, str) or not library_relative:
            return False

        key = Path(library_relative).as_posix()
        existing = self._asset_index.get(key)
        if existing == asset:
            self._has_snapshot = True
            self._invalidated = False
            return False

        self._asset_index[key] = asset
        self._list_dirty = True
        self._has_snapshot = True
        self._invalidated = False
        return True

    def remove_asset(self, rel: str) -> bool:
        target = Path(rel).as_posix()
        if target not in self._asset_index:
            self._has_snapshot = True
            self._invalidated = False
            return False

        del self._asset_index[target]
        self._list_dirty = True
        self._has_snapshot = True
        self._invalidated = False
        return True

    def resolve_asset(self, rel: str) -> object | None:
        return self._asset_index.get(Path(rel).as_posix())

    def resolve_relative(self, rel: str) -> Path | None:
        asset = self.resolve_asset(rel)
        if asset is not None:
            absolute_path = getattr(asset, "absolute_path", None)
            if isinstance(absolute_path, Path):
                return absolute_path
        return None

    def is_cluster_gallery(self) -> bool:
        return self._mode == "cluster_gallery"

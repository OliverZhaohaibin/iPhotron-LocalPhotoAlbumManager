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
        self._full_assets: list = []

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
            self._full_assets = []
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
        self._full_assets = list(assets)
        self._has_snapshot = True
        self._invalidated = False
        return True

    def set_mode(self, mode: LocationSelectionMode) -> None:
        self._mode = mode

    def invalidate(self) -> None:
        self._invalidated = True

    def full_assets(self) -> list:
        return list(self._full_assets)

    def resolve_asset(self, rel: str):
        target = Path(rel).as_posix()
        for asset in self._full_assets:
            library_relative = getattr(asset, "library_relative", None)
            if isinstance(library_relative, str) and Path(library_relative).as_posix() == target:
                return asset
        return None

    def resolve_relative(self, rel: str) -> Path | None:
        asset = self.resolve_asset(rel)
        if asset is not None:
            absolute_path = getattr(asset, "absolute_path", None)
            if isinstance(absolute_path, Path):
                return absolute_path
        return None

    def is_cluster_gallery(self) -> bool:
        return self._mode == "cluster_gallery"

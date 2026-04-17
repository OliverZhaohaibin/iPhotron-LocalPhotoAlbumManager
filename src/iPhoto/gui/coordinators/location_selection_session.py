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

    def replace_assets(self, assets: list) -> None:
        self._full_assets = sorted(
            list(assets),
            key=lambda asset: str(getattr(asset, "library_relative", "")),
        )
        self._has_snapshot = True
        self._invalidated = False

    def upsert_asset(self, asset: object) -> bool:
        library_relative = getattr(asset, "library_relative", None)
        if not isinstance(library_relative, str) or not library_relative:
            return False

        changed = False
        updated_assets: list[object] = []
        replaced = False
        for existing in self._full_assets:
            existing_rel = getattr(existing, "library_relative", None)
            if existing_rel == library_relative:
                updated_assets.append(asset)
                replaced = True
                if existing != asset:
                    changed = True
            else:
                updated_assets.append(existing)
        if not replaced:
            updated_assets.append(asset)
            changed = True

        if changed:
            self._full_assets = sorted(
                updated_assets,
                key=lambda current: str(getattr(current, "library_relative", "")),
            )
        self._has_snapshot = True
        self._invalidated = False
        return changed

    def remove_asset(self, rel: str) -> bool:
        target = Path(rel).as_posix()
        filtered_assets = [
            asset
            for asset in self._full_assets
            if Path(str(getattr(asset, "library_relative", ""))).as_posix() != target
        ]
        changed = len(filtered_assets) != len(self._full_assets)
        if changed:
            self._full_assets = filtered_assets
        self._has_snapshot = True
        self._invalidated = False
        return changed

    def resolve_asset(self, rel: str) -> object | None:
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

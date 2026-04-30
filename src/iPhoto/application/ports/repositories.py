"""Repository ports owned by the application layer."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, Protocol


class AssetRepositoryPort(Protocol):
    """Read and merge rebuildable scan facts for one library."""

    library_root: Path
    path: Path

    def transaction(
        self,
        *,
        begin_mode: str | None = None,
    ) -> AbstractContextManager[Any]:
        """Return a transaction boundary for batched repository operations."""

    def merge_scan_rows(
        self,
        rows: Iterable[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Merge scanned facts while preserving durable user state."""

    def append_rows(self, rows: Iterable[dict[str, Any]]) -> None:
        """Append or replace already-materialized asset rows."""

    def remove_rows(self, rels: Iterable[str]) -> None:
        """Remove rows identified by library-relative paths."""

    def get_rows_by_rels(self, rels: Iterable[str]) -> dict[str, dict[str, Any]]:
        """Return existing rows keyed by library-relative path."""

    def read_all(
        self,
        sort_by_date: bool = False,
        filter_hidden: bool = False,
    ) -> Iterator[dict[str, Any]]:
        """Yield all asset rows."""

    def read_album_assets(
        self,
        album_path: str,
        include_subalbums: bool = False,
        sort_by_date: bool = True,
        filter_hidden: bool = True,
        filter_params: dict[str, Any] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield asset rows for an album scope."""

    def count(
        self,
        filter_hidden: bool = False,
        filter_params: dict[str, Any] | None = None,
        album_path: str | None = None,
        include_subalbums: bool = True,
    ) -> int:
        """Return the number of assets matching a query."""

    def get_assets_page(
        self,
        cursor_dt: str | None = None,
        cursor_id: str | None = None,
        limit: int = 100,
        album_path: str | None = None,
        include_subalbums: bool = False,
        filter_hidden: bool = True,
        filter_params: dict[str, Any] | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return one paginated asset page."""

    def apply_live_role_updates(
        self,
        updates: Iterable[tuple[str, int, str | None]],
    ) -> None:
        """Replace Live Photo role state using library-relative updates."""

    def apply_live_role_updates_for_prefix(
        self,
        prefix: str,
        updates: Iterable[tuple[str, int, str | None]],
    ) -> None:
        """Replace Live Photo role state only inside a library-relative prefix."""


class LibraryStateRepositoryPort(Protocol):
    """Persist durable user choices for one library."""

    def set_favorite_status(self, rel: str, is_favorite: bool) -> None:
        """Persist favorite state for one asset."""

    def sync_favorites(self, featured_rels: Iterable[str]) -> None:
        """Synchronize favorite state from a compatibility source."""

    def update_location(self, rel: str, location: str) -> None:
        """Persist a display location string."""

    def update_asset_geodata(
        self,
        rel: str,
        *,
        gps: dict[str, float] | None,
        location: str | None,
        metadata_updates: dict[str, Any] | None = None,
    ) -> None:
        """Persist GPS, location, and metadata overlays for one asset."""


class AssetFavoriteQueryPort(Protocol):
    """Read favorite state through a session-owned query surface."""

    def favorite_status_for_path(self, path: Path) -> bool | None:
        """Return favorite state for *path*, or None when no indexed row exists."""

"""Library state repository adapter backed by the current index store."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ...application.ports import LibraryStateRepositoryPort
from ...cache.index_store.repository import get_global_repository


class IndexStoreLibraryStateRepository(LibraryStateRepositoryPort):
    """Persist durable user choices through the current global index store."""

    def __init__(self, library_root: Path) -> None:
        self._library_root = Path(library_root)

    def set_favorite_status(self, rel: str, is_favorite: bool) -> None:
        self._repository().set_favorite_status(rel, is_favorite)

    def sync_favorites(self, featured_rels: Iterable[str]) -> None:
        self._repository().sync_favorites(featured_rels)

    def update_location(self, rel: str, location: str) -> None:
        self._repository().update_location(rel, location)

    def update_asset_geodata(
        self,
        rel: str,
        *,
        gps: dict[str, float] | None,
        location: str | None,
        metadata_updates: dict[str, Any] | None = None,
    ) -> None:
        self._repository().update_asset_geodata(
            rel,
            gps=gps,
            location=location,
            metadata_updates=metadata_updates,
        )

    def _repository(self):
        return get_global_repository(self._library_root)


__all__ = ["IndexStoreLibraryStateRepository"]

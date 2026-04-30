"""Library-scoped asset query surface for session-backed GUI reads."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

from ..application.ports import AssetRepositoryPort
from ..cache.index_store import get_global_repository
from ..path_normalizer import compute_album_path


class _ScopedLocationCacheWriter:
    """Map album-relative cache writes back to library-relative index rows."""

    def __init__(self, service: "LibraryAssetQueryService", root: Path) -> None:
        self._service = service
        self._root = Path(root)

    def update_location(self, rel: str, location: str) -> None:
        self._service.update_location_for_root(self._root, rel, location)


class LibraryAssetQueryService:
    """Own read-only asset index queries for one active library session.

    This migration adapter keeps the current index-store repository as the
    source of truth while preventing GUI modules from importing the concrete
    singleton directly.
    """

    def __init__(
        self,
        library_root: Path,
        *,
        repository_factory: Callable[[Path], AssetRepositoryPort] | None = None,
    ) -> None:
        self.library_root = Path(library_root)
        self._repository_factory = repository_factory or get_global_repository

    def count_assets(
        self,
        root: Path,
        *,
        filter_hidden: bool = True,
        filter_params: dict[str, Any] | None = None,
    ) -> int:
        """Return the number of indexed assets under *root*."""

        return self._repository().count(
            filter_hidden=filter_hidden,
            filter_params=filter_params,
            album_path=self.album_path_for(root),
            include_subalbums=True,
        )

    def read_geometry_rows(
        self,
        root: Path,
        *,
        filter_params: dict[str, Any] | None = None,
        sort_by_date: bool = True,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield lightweight grid rows scoped to *root*."""

        album_path = self.album_path_for(root)
        repository = self._repository()
        read_geometry_only = getattr(repository, "read_geometry_only", None)
        if callable(read_geometry_only):
            rows = read_geometry_only(
                filter_params=filter_params,
                sort_by_date=sort_by_date,
                album_path=album_path,
                include_subalbums=True,
            )
        elif album_path:
            rows = repository.read_album_assets(
                album_path,
                include_subalbums=True,
                sort_by_date=sort_by_date,
                filter_hidden=True,
                filter_params=filter_params,
            )
        else:
            rows = repository.read_all(
                sort_by_date=sort_by_date,
                filter_hidden=True,
            )
        yield from self._scoped_rows(rows, album_path, limit=limit)

    def read_asset_rows(
        self,
        root: Path,
        *,
        filter_hidden: bool = True,
    ) -> Iterator[dict[str, Any]]:
        """Yield full index rows scoped to *root*."""

        album_path = self.album_path_for(root)
        repository = self._repository()
        if album_path:
            rows = repository.read_album_assets(
                album_path,
                include_subalbums=True,
                filter_hidden=filter_hidden,
            )
        else:
            rows = repository.read_all(filter_hidden=filter_hidden)
        yield from self._scoped_rows(rows, album_path)

    def read_library_relative_asset_rows(
        self,
        root: Path,
        *,
        filter_hidden: bool = True,
        sort_by_date: bool = True,
    ) -> Iterator[dict[str, Any]]:
        """Yield full index rows for *root* with library-relative paths."""

        album_path = self.album_path_for(root)
        repository = self._repository()
        if album_path:
            rows = repository.read_album_assets(
                album_path,
                include_subalbums=True,
                sort_by_date=sort_by_date,
                filter_hidden=filter_hidden,
            )
        else:
            rows = repository.read_all(
                sort_by_date=sort_by_date,
                filter_hidden=filter_hidden,
            )
        for row in rows:
            if isinstance(row, dict):
                yield dict(row)

    def read_geotagged_rows(self) -> Iterator[dict[str, Any]]:
        """Yield library-relative rows that contain GPS metadata."""

        repository = self._repository()
        read_geotagged = getattr(repository, "read_geotagged", None)
        if callable(read_geotagged):
            rows = read_geotagged()
        else:
            rows = (
                row
                for row in repository.read_all(filter_hidden=True)
                if isinstance(row, dict) and isinstance(row.get("gps"), dict)
            )
        for row in rows:
            if isinstance(row, dict):
                yield dict(row)

    def location_cache_writer(self, root: Path) -> _ScopedLocationCacheWriter:
        """Return an object compatible with legacy asset-entry location writes."""

        return _ScopedLocationCacheWriter(self, Path(root))

    def update_location_for_root(self, root: Path, rel: str, location: str) -> None:
        """Persist a best-effort cached location for a scoped asset row."""

        library_rel = self._library_relative_rel(Path(root), rel)
        self.update_location(library_rel, location)

    def update_location(self, rel: str, location: str) -> None:
        """Persist a best-effort cached location for a library-relative row."""

        update_location = getattr(self._repository(), "update_location", None)
        if callable(update_location):
            update_location(rel, location)

    def album_path_for(self, root: Path) -> str | None:
        """Return the album path used for index filtering."""

        return compute_album_path(Path(root), self.library_root)

    def _repository(self) -> AssetRepositoryPort:
        return self._repository_factory(self.library_root)

    def _library_relative_rel(self, root: Path, rel: str) -> str:
        album_path = self.album_path_for(root)
        if not album_path:
            return Path(rel).as_posix()
        rel_path = Path(rel).as_posix()
        prefix = album_path.rstrip("/")
        if rel_path == prefix or rel_path.startswith(prefix + "/"):
            return rel_path
        return f"{prefix}/{rel_path}"

    def _scoped_rows(
        self,
        rows: Iterable[dict[str, Any]],
        album_path: str | None,
        *,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        yielded = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            scoped = self._adjust_rel_for_album(dict(row), album_path)
            yield scoped
            yielded += 1
            if limit is not None and yielded >= limit:
                return

    @staticmethod
    def _adjust_rel_for_album(
        row: dict[str, Any],
        album_path: str | None,
    ) -> dict[str, Any]:
        if not album_path:
            return row
        rel = row.get("rel")
        if not isinstance(rel, str) or not rel:
            return row
        prefix = album_path.rstrip("/") + "/"
        if rel.startswith(prefix):
            adjusted = dict(row)
            adjusted["rel"] = rel[len(prefix):]
            return adjusted
        return row


__all__ = ["LibraryAssetQueryService"]

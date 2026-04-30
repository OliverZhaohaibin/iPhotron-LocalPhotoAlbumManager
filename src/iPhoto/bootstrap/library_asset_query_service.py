"""Library-scoped asset query surface for session-backed GUI reads."""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterable, Iterator
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path
from typing import Any

from ..application.ports import AssetRepositoryPort
from ..cache.index_store import get_global_repository
from ..config import RECENTLY_DELETED_DIR_NAME
from ..domain.models.core import MediaType
from ..domain.models.query import AssetQuery, SortOrder
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

    def count_query_assets(self, query: AssetQuery) -> int:
        """Return the number of indexed assets matching an application query."""

        count_query = self._count_query(query)
        if self._requires_in_memory_query(count_query):
            return sum(1 for _row in self._filtered_query_rows(count_query))

        filter_params = self._filter_params_for_query(count_query)
        return self._repository().count(
            filter_hidden=True,
            filter_params=filter_params,
            album_path=count_query.album_path,
            include_subalbums=count_query.include_subalbums,
        )

    def read_query_asset_rows(
        self,
        root: Path,
        query: AssetQuery,
    ) -> Iterator[dict[str, Any]]:
        """Yield view-relative rows matching *query* for gallery collection reads."""

        read_query = copy.deepcopy(query)
        album_path = self.album_path_for(root)
        if self._requires_in_memory_query(read_query):
            rows = self._filtered_query_rows(read_query)
        else:
            filter_params = self._filter_params_for_query(read_query)
            rows = self._read_simple_query_rows(read_query, filter_params)

        yield from self._scoped_rows(rows, album_path)

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

    def favorite_status_for_path(self, path: Path) -> bool | None:
        """Return favorite state for *path*, or None when no indexed row exists."""

        rel = self._library_relative_path(path)
        row = self._repository().get_rows_by_rels([rel]).get(rel)
        if row is None:
            return None
        return bool(row.get("is_favorite"))

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

    def _filtered_query_rows(self, query: AssetQuery) -> Iterator[dict[str, Any]]:
        repository = self._repository()
        if query.asset_ids:
            get_rows_by_ids = getattr(repository, "get_rows_by_ids", None)
            if callable(get_rows_by_ids):
                rows = get_rows_by_ids(query.asset_ids).values()
            else:
                rows = repository.read_all(sort_by_date=False, filter_hidden=False)
        elif query.album_path:
            rows = repository.read_album_assets(
                query.album_path,
                include_subalbums=query.include_subalbums,
                sort_by_date=False,
                filter_hidden=False,
            )
        else:
            rows = repository.read_all(sort_by_date=False, filter_hidden=False)

        filtered = [dict(row) for row in self._filter_rows(rows, query)]
        filtered.sort(
            key=lambda row: self._sort_key_for_query(row, query),
            reverse=self._sort_descending(query),
        )
        return iter(self._slice_materialized_rows(filtered, query))

    def _filter_rows(
        self,
        rows: Iterable[dict[str, Any]],
        query: AssetQuery,
    ) -> Iterator[dict[str, Any]]:
        for row in rows:
            if isinstance(row, dict) and self._row_matches_query(row, query):
                yield dict(row)

    def _read_simple_query_rows(
        self,
        query: AssetQuery,
        filter_params: dict[str, Any] | None,
    ) -> Iterator[dict[str, Any]]:
        repository = self._repository()
        page_offset = max(0, int(query.offset or 0))
        page_limit = None if query.limit is None else max(0, int(query.limit))
        get_assets_page = getattr(repository, "get_assets_page", None)
        if (
            callable(get_assets_page)
            and page_limit is not None
            and self._sort_by_date(query)
        ):
            rows = get_assets_page(
                limit=page_limit,
                album_path=query.album_path,
                include_subalbums=query.include_subalbums,
                filter_hidden=True,
                filter_params=filter_params,
                offset=page_offset,
            )
            return iter(rows)

        if query.album_path:
            rows = repository.read_album_assets(
                query.album_path,
                include_subalbums=query.include_subalbums,
                sort_by_date=self._sort_by_date(query),
                filter_hidden=True,
                filter_params=filter_params,
            )
            return self._slice_rows(rows, query)

        rows = repository.read_all(
            sort_by_date=self._sort_by_date(query),
            filter_hidden=True,
        )
        return self._slice_rows(self._filter_rows(rows, query), query)

    def _row_matches_query(self, row: dict[str, Any], query: AssetQuery) -> bool:
        live_role = self._int_value(row.get("live_role"), default=0)
        if live_role != 0:
            return False

        if query.asset_ids:
            asset_id = row.get("id")
            if not isinstance(asset_id, str) or asset_id not in set(query.asset_ids):
                return False

        if query.album_id:
            row_album_id = row.get("album_id")
            if row_album_id is None or str(row_album_id) != query.album_id:
                return False

        parent_album_path = row.get("parent_album_path")
        parent = str(parent_album_path) if parent_album_path not in (None, "") else None
        if query.album_path:
            if query.include_subalbums:
                if parent != query.album_path and not (
                    isinstance(parent, str)
                    and parent.startswith(query.album_path.rstrip("/") + "/")
                ):
                    return False
            elif parent != query.album_path:
                return False

        if query.album_path != RECENTLY_DELETED_DIR_NAME and self._is_trash_row(row):
            return False

        if query.media_types and not self._row_matches_media_types(row, query.media_types):
            return False

        if query.is_favorite is not None:
            if bool(row.get("is_favorite")) != bool(query.is_favorite):
                return False

        if query.has_gps is not None:
            has_gps = row.get("gps") is not None
            if bool(query.has_gps) != has_gps:
                return False

        row_dt = self._comparable_datetime(self._row_datetime(row))
        if query.date_from is not None:
            date_from = self._comparable_datetime(query.date_from)
            if row_dt is None or date_from is None or row_dt < date_from:
                return False
        if query.date_to is not None:
            date_to = self._comparable_datetime(query.date_to)
            if row_dt is None or date_to is None or row_dt > date_to:
                return False

        return True

    def _filter_params_for_query(self, query: AssetQuery) -> dict[str, Any] | None:
        params: dict[str, Any] = {}
        media_values = {media_type.value for media_type in query.media_types}
        if media_values == {MediaType.VIDEO.value}:
            params["media_type"] = 1
        elif media_values and media_values <= {MediaType.IMAGE.value, MediaType.PHOTO.value}:
            params["media_type"] = 0
        elif media_values == {MediaType.LIVE_PHOTO.value}:
            params["filter_mode"] = "live"

        if query.is_favorite is True:
            params["filter_mode"] = "favorites"

        if query.album_path != RECENTLY_DELETED_DIR_NAME:
            params.setdefault("exclude_path_prefix", RECENTLY_DELETED_DIR_NAME)

        return params or None

    def _requires_in_memory_query(self, query: AssetQuery) -> bool:
        if query.asset_ids or query.album_id or query.has_gps is not None:
            return True
        if query.date_from is not None or query.date_to is not None:
            return True
        if query.is_favorite is False:
            return True
        if query.order != SortOrder.DESC or not self._sort_by_date(query):
            return True
        media_values = {media_type.value for media_type in query.media_types}
        if not media_values:
            return False
        simple_sets = (
            {MediaType.VIDEO.value},
            {MediaType.IMAGE.value},
            {MediaType.PHOTO.value},
            {MediaType.IMAGE.value, MediaType.PHOTO.value},
            {MediaType.LIVE_PHOTO.value},
        )
        if media_values not in simple_sets:
            return True
        return query.is_favorite is True and media_values == {MediaType.LIVE_PHOTO.value}

    @staticmethod
    def _slice_rows(
        rows: Iterable[dict[str, Any]],
        query: AssetQuery,
    ) -> Iterator[dict[str, Any]]:
        start = max(0, int(query.offset or 0))
        stop = None if query.limit is None else start + max(0, int(query.limit))
        return islice(rows, start, stop)

    @staticmethod
    def _slice_materialized_rows(
        rows: list[dict[str, Any]],
        query: AssetQuery,
    ) -> list[dict[str, Any]]:
        start = max(0, int(query.offset or 0))
        if query.limit is None:
            return rows[start:]
        return rows[start : start + max(0, int(query.limit))]

    @staticmethod
    def _count_query(query: AssetQuery) -> AssetQuery:
        count_query = copy.deepcopy(query)
        count_query.offset = 0
        count_query.limit = None
        return count_query

    @staticmethod
    def _sort_by_date(query: AssetQuery) -> bool:
        return query.order_by in {"created_at", "ts", "dt"}

    @staticmethod
    def _sort_descending(query: AssetQuery) -> bool:
        return query.order == SortOrder.DESC

    def _sort_key_for_query(
        self,
        row: dict[str, Any],
        query: AssetQuery,
    ) -> tuple[Any, str]:
        order_col = {
            "created_at": "dt",
            "ts": "dt",
            "size_bytes": "bytes",
            "path": "rel",
        }.get(query.order_by, query.order_by)
        if order_col not in {"dt", "bytes", "id", "rel", "media_type", "is_favorite"}:
            order_col = "dt"
        value = row.get(order_col)
        if value is None:
            value = "" if order_col in {"dt", "id", "rel"} else -1
        return value, str(row.get("id") or "")

    @staticmethod
    def _row_matches_media_types(
        row: dict[str, Any],
        media_types: Iterable[MediaType],
    ) -> bool:
        values = {media_type.value for media_type in media_types}
        includes_live = MediaType.LIVE_PHOTO.value in values
        includes_image = bool(values & {MediaType.IMAGE.value, MediaType.PHOTO.value})
        includes_video = MediaType.VIDEO.value in values
        row_media = LibraryAssetQueryService._int_value(row.get("media_type"), default=-1)
        if includes_live and not includes_image and row.get("live_partner_rel") is not None:
            return True
        if includes_image and row_media == 0:
            return True
        if includes_video and row_media == 1:
            return True
        return False

    @staticmethod
    def _is_trash_row(row: dict[str, Any]) -> bool:
        parent = row.get("parent_album_path")
        if not isinstance(parent, str):
            return False
        trash = RECENTLY_DELETED_DIR_NAME.rstrip("/")
        return parent == trash or parent.startswith(trash + "/")

    @staticmethod
    def _int_value(value: Any, *, default: int) -> int:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                return default
        return default

    @staticmethod
    def _row_datetime(row: dict[str, Any]) -> datetime | None:
        dt_raw = row.get("dt")
        if isinstance(dt_raw, datetime):
            return dt_raw
        if isinstance(dt_raw, str) and dt_raw:
            try:
                return datetime.fromisoformat(dt_raw.replace("Z", "+00:00"))
            except ValueError:
                return None
        return None

    @staticmethod
    def _comparable_datetime(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def _library_relative_rel(self, root: Path, rel: str) -> str:
        album_path = self.album_path_for(root)
        if not album_path:
            return Path(rel).as_posix()
        rel_path = Path(rel).as_posix()
        prefix = album_path.rstrip("/")
        if rel_path == prefix or rel_path.startswith(prefix + "/"):
            return rel_path
        return f"{prefix}/{rel_path}"

    def _library_relative_path(self, path: Path) -> str:
        candidate = Path(path)
        if not candidate.is_absolute():
            return candidate.as_posix()
        try:
            return candidate.resolve().relative_to(self.library_root.resolve()).as_posix()
        except (OSError, ValueError):
            try:
                return candidate.relative_to(self.library_root).as_posix()
            except ValueError:
                return candidate.name

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

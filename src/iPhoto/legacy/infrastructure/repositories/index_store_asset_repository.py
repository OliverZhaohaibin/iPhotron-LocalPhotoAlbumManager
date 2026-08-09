"""Domain-repository compatibility adapter backed by the global index store."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, List, Optional, Tuple

from iPhoto.cache.index_store.queries import ESCAPE_CLAUSE, escape_like_pattern
from iPhoto.cache.index_store.repository import AssetRepository as IndexStoreRepository
from iPhoto.config import RECENTLY_DELETED_DIR_NAME
from iPhoto.domain.models import Asset, MediaType
from iPhoto.domain.models.query import AssetQuery, SortOrder
from iPhoto.legacy.domain.repositories import IAssetRepository


class IndexStoreAssetRepositoryAdapter(IAssetRepository):
    """Expose ``global_index.db`` rows through the legacy domain repository API.

    Runtime code now treats the index-store repository as the source of truth.
    This adapter exists only for callers that still speak ``IAssetRepository``
    while those call sites are migrated to application ports/session queries.
    """

    def __init__(self, index_store: IndexStoreRepository) -> None:
        self._index_store = index_store

    @property
    def index_store(self) -> IndexStoreRepository:
        return self._index_store

    def get(self, id: str) -> Optional[Asset]:
        row = self._index_store.get_rows_by_ids([id]).get(id)
        return self._row_to_asset(row) if row is not None else None

    def get_by_path(self, path: Path) -> Optional[Asset]:
        row = self._fetch_row_by_path(path)
        return self._row_to_asset(row) if row is not None else None

    def get_by_album(self, album_id: str) -> List[Asset]:
        if not self._has_column("album_id"):
            return []
        rows = self._fetch_rows("SELECT * FROM assets WHERE album_id = ?", [album_id])
        return [asset for row in rows if (asset := self._row_to_asset(row)) is not None]

    def find_by_query(self, query: AssetQuery) -> List[Asset]:
        sql, params = self._build_sql(query)
        rows = self._fetch_rows(sql, params)
        return [asset for row in rows if (asset := self._row_to_asset(row)) is not None]

    def count(self, query: AssetQuery) -> int:
        sql, params = self._build_sql(query, count_only=True)
        with self._index_store.transaction() as conn:
            result = conn.execute(sql, params).fetchone()
        return int(result[0]) if result else 0

    def save(self, asset: Asset) -> None:
        self.save_batch([asset])

    def save_batch(self, assets: List[Asset]) -> None:
        rows: list[dict[str, Any]] = []
        for asset in assets:
            rel = asset.path.as_posix()
            existing = self._index_store.get_rows_by_rels([rel]).get(rel, {})
            row = dict(existing)
            row.update(self._asset_to_row(asset))
            rows.append(row)
        if rows:
            self._upsert_rows_preserving_existing_columns(rows)

    def save_all(self, assets: List[Asset]) -> None:
        self.save_batch(assets)

    def delete(self, id: str) -> None:
        rows = self._index_store.get_rows_by_ids([id])
        rels = [str(row["rel"]) for row in rows.values() if row.get("rel")]
        if rels:
            self._index_store.remove_rows(rels)

    def _fetch_row_by_path(self, path: Path) -> dict[str, Any] | None:
        candidates = [path.as_posix(), str(path)]
        try:
            rel = path.expanduser().resolve().relative_to(
                self._index_store.library_root.resolve()
            )
            candidates.append(rel.as_posix())
        except (OSError, ValueError):
            pass

        direct = self._index_store.get_rows_by_rels(dict.fromkeys(candidates))
        if direct:
            return next(iter(direct.values()))

        path_posix = path.as_posix()
        path_windows = str(path).replace("/", "\\")
        sql = """
            SELECT *
            FROM assets
            WHERE (length(?) > length(rel) AND substr(?, -(length(rel) + 1)) = '/' || rel)
               OR (length(?) > length(rel) AND substr(?, -(length(rel) + 1)) = '\\' || REPLACE(rel, '/', '\\'))
            ORDER BY LENGTH(rel) DESC
            LIMIT 1
        """
        rows = self._fetch_rows(
            sql,
            [path_posix, path_posix, path_windows, path_windows],
        )
        return rows[0] if rows else None

    def _fetch_rows(self, sql: str, params: Iterable[Any]) -> list[dict[str, Any]]:
        with self._index_store.transaction() as conn:
            previous_factory = conn.row_factory
            conn.row_factory = sqlite3.Row
            try:
                cursor = conn.execute(sql, list(params))
                return [self._normalize_row(row) for row in cursor.fetchall()]
            finally:
                conn.row_factory = previous_factory

    def _has_column(self, column: str) -> bool:
        with self._index_store.transaction() as conn:
            return column in {row[1] for row in conn.execute("PRAGMA table_info(assets)")}

    def _asset_columns(self) -> list[str]:
        with self._index_store.transaction() as conn:
            return [str(row[1]) for row in conn.execute("PRAGMA table_info(assets)")]

    def _upsert_rows_preserving_existing_columns(
        self,
        rows: list[dict[str, Any]],
    ) -> None:
        table_columns = self._asset_columns()
        table_column_set = set(table_columns)
        with self._index_store.transaction() as conn:
            for row in rows:
                if "rel" not in row or "rel" not in table_column_set:
                    continue
                columns = [column for column in table_columns if column in row]
                if "rel" not in columns:
                    columns.insert(0, "rel")
                quoted_columns = [self._quote_identifier(column) for column in columns]
                placeholders = ", ".join(["?"] * len(columns))
                update_columns = [column for column in columns if column != "rel"]
                assignments = ", ".join(
                    f"{self._quote_identifier(column)} = "
                    f"excluded.{self._quote_identifier(column)}"
                    for column in update_columns
                )
                sql = (
                    f"INSERT INTO assets ({', '.join(quoted_columns)}) "
                    f"VALUES ({placeholders})"
                )
                if assignments:
                    sql += f" ON CONFLICT(rel) DO UPDATE SET {assignments}"
                else:
                    sql += " ON CONFLICT(rel) DO NOTHING"
                conn.execute(
                    sql,
                    [self._db_value(row.get(column)) for column in columns],
                )

    def _build_sql(
        self,
        query: AssetQuery,
        *,
        count_only: bool = False,
    ) -> Tuple[str, list[Any]]:
        select_clause = "SELECT COUNT(*)" if count_only else "SELECT *"
        sql = f"{select_clause} FROM assets WHERE 1=1"
        params: list[Any] = []

        sql += " AND COALESCE(live_role, 0) = 0"

        if query.album_id:
            if self._has_column("album_id"):
                sql += " AND album_id = ?"
                params.append(query.album_id)
            else:
                sql += " AND 0=1"

        if query.asset_ids:
            placeholders = ", ".join(["?"] * len(query.asset_ids))
            sql += f" AND id IN ({placeholders})"
            params.extend(query.asset_ids)

        if query.album_path:
            if query.include_subalbums:
                sql += f" AND (parent_album_path = ? OR parent_album_path LIKE ? {ESCAPE_CLAUSE})"
                escaped = escape_like_pattern(query.album_path)
                params.extend([query.album_path, f"{escaped}/%"])
            else:
                sql += " AND parent_album_path = ?"
                params.append(query.album_path)

        if query.album_path != RECENTLY_DELETED_DIR_NAME:
            escaped_trash = escape_like_pattern(RECENTLY_DELETED_DIR_NAME)
            sql += (
                f" AND (parent_album_path IS NULL OR "
                f"(parent_album_path != ? AND parent_album_path NOT LIKE ? {ESCAPE_CLAUSE}))"
            )
            params.extend([RECENTLY_DELETED_DIR_NAME, f"{escaped_trash}/%"])

        if query.media_types:
            media_clauses: list[str] = []
            includes_live = MediaType.LIVE_PHOTO in query.media_types
            includes_image = (
                MediaType.IMAGE in query.media_types or MediaType.PHOTO in query.media_types
            )
            includes_video = MediaType.VIDEO in query.media_types
            if includes_live and not includes_image:
                media_clauses.append("live_partner_rel IS NOT NULL")
            if includes_image:
                media_clauses.append("CAST(media_type AS INTEGER) = 0")
            if includes_video:
                media_clauses.append("CAST(media_type AS INTEGER) = 1")
            if media_clauses:
                sql += " AND (" + " OR ".join(media_clauses) + ")"

        if query.is_favorite is not None:
            sql += " AND is_favorite = ?"
            params.append(1 if query.is_favorite else 0)

        if query.has_gps is not None:
            sql += " AND gps IS " + ("NOT NULL" if query.has_gps else "NULL")

        if query.date_from:
            sql += " AND dt >= ?"
            params.append(query.date_from.isoformat())

        if query.date_to:
            sql += " AND dt <= ?"
            params.append(query.date_to.isoformat())

        if not count_only:
            order_col = {
                "created_at": "dt",
                "ts": "dt",
                "size_bytes": "bytes",
                "path": "rel",
            }.get(query.order_by, query.order_by)
            if order_col not in {"dt", "bytes", "id", "rel", "media_type", "is_favorite"}:
                order_col = "dt"
            order = query.order.value if isinstance(query.order, SortOrder) else "DESC"
            if order not in {"ASC", "DESC"}:
                order = "DESC"
            sql += f" ORDER BY {order_col} {order}, id {order}"
            if query.limit is not None:
                sql += " LIMIT ? OFFSET ?"
                params.extend([query.limit, query.offset])

        return sql, params

    def _asset_to_row(self, asset: Asset) -> dict[str, Any]:
        created_at = asset.created_at
        metadata = dict(asset.metadata or {})
        row = {
            "rel": asset.path.as_posix(),
            "id": asset.id,
            "parent_album_path": asset.parent_album_path
            if asset.parent_album_path is not None
            else self._parent_album_path(asset.path),
            "bytes": asset.size_bytes,
            "media_type": 1 if asset.media_type == MediaType.VIDEO else 0,
            "is_favorite": 1 if asset.is_favorite else 0,
        }
        if asset.album_id:
            row["album_id"] = asset.album_id
        if created_at:
            row["dt"] = created_at.isoformat()
            row["ts"] = int(created_at.timestamp() * 1_000_000)
        if asset.width is not None:
            row["w"] = asset.width
        if asset.height is not None:
            row["h"] = asset.height
        if asset.duration is not None:
            row["dur"] = asset.duration
        if asset.content_identifier is not None:
            row["content_id"] = asset.content_identifier
            row["content_identifier"] = asset.content_identifier
        if asset.live_photo_group_id is not None:
            row["live_photo_group_id"] = asset.live_photo_group_id
        if asset.face_status is not None:
            row["face_status"] = asset.face_status
        if "gps" in metadata:
            row["gps"] = metadata["gps"]
        if "location" in metadata:
            row["location"] = metadata["location"]
        if "micro_thumbnail" in metadata:
            row["micro_thumbnail"] = metadata["micro_thumbnail"]
        if "live_role" in metadata:
            row["live_role"] = metadata["live_role"]
        if "live_partner_rel" in metadata:
            row["live_partner_rel"] = metadata["live_partner_rel"]
        if metadata:
            row["metadata"] = self._json_safe_metadata(metadata)
        return row

    def _row_to_asset(self, row: dict[str, Any] | None) -> Asset | None:
        if row is None or row.get("id") is None or row.get("rel") is None:
            return None

        metadata = self._metadata_from_row(row)
        return Asset(
            id=str(row["id"]),
            album_id=str(row.get("album_id") or ""),
            path=Path(str(row["rel"])),
            media_type=self._media_type_from_row(row),
            size_bytes=int(row.get("bytes") or row.get("size_bytes") or 0),
            created_at=self._created_at_from_row(row),
            width=row.get("w") or row.get("width"),
            height=row.get("h") or row.get("height"),
            duration=row.get("dur") or row.get("duration"),
            metadata=metadata,
            content_identifier=row.get("content_identifier") or row.get("content_id"),
            live_photo_group_id=row.get("live_photo_group_id") or row.get("live_partner_rel"),
            is_favorite=bool(row.get("is_favorite")),
            parent_album_path=row.get("parent_album_path"),
            face_status=row.get("face_status"),
        )

    def _metadata_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        raw_metadata = row.get("metadata")
        if isinstance(raw_metadata, str) and raw_metadata:
            try:
                decoded = json.loads(raw_metadata)
            except json.JSONDecodeError:
                decoded = None
            if isinstance(decoded, dict):
                metadata.update(decoded)

        gps = row.get("gps")
        if isinstance(gps, str) and gps:
            try:
                gps = json.loads(gps)
            except json.JSONDecodeError:
                gps = None
        if gps is not None:
            metadata["gps"] = gps

        for key in ("location", "micro_thumbnail", "live_role", "live_partner_rel"):
            if row.get(key) is not None:
                metadata[key] = row[key]
        return metadata

    @staticmethod
    def _normalize_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        gps = data.get("gps")
        if isinstance(gps, str) and gps:
            try:
                data["gps"] = json.loads(gps)
            except json.JSONDecodeError:
                data["gps"] = None
        return data

    @staticmethod
    def _created_at_from_row(row: dict[str, Any]) -> datetime | None:
        dt_value = row.get("dt") or row.get("created_at")
        if isinstance(dt_value, str) and dt_value:
            try:
                return datetime.fromisoformat(dt_value.replace("Z", "+00:00"))
            except ValueError:
                pass
        ts_value = row.get("ts")
        if isinstance(ts_value, (int, float)):
            try:
                return datetime.fromtimestamp(float(ts_value) / 1_000_000)
            except (OSError, OverflowError, ValueError):
                return None
        return None

    @staticmethod
    def _media_type_from_row(row: dict[str, Any]) -> MediaType:
        raw = row.get("media_type")
        if raw in (1, "1", "video", MediaType.VIDEO):
            return MediaType.VIDEO
        if raw in ("live", MediaType.LIVE_PHOTO):
            return MediaType.LIVE_PHOTO
        return MediaType.IMAGE

    @staticmethod
    def _parent_album_path(path: Path) -> str:
        parent = path.parent
        return parent.as_posix() if parent != Path(".") else ""

    @staticmethod
    def _quote_identifier(identifier: str) -> str:
        return '"' + identifier.replace('"', '""') + '"'

    @classmethod
    def _db_value(cls, value: Any) -> Any:
        if isinstance(value, Path):
            return value.as_posix()
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, MediaType):
            return value.value
        if isinstance(value, (dict, list, tuple)):
            return json.dumps(cls._json_safe_metadata(value), ensure_ascii=False)
        return value

    @classmethod
    def _json_safe_metadata(cls, value: Any) -> Any:
        if isinstance(value, (bytes, bytearray, memoryview)):
            return None
        if isinstance(value, Path):
            return value.as_posix()
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, MediaType):
            return value.value
        if isinstance(value, dict):
            return {
                str(key): cls._json_safe_metadata(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [cls._json_safe_metadata(item) for item in value]
        try:
            json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(value)
        return value


__all__ = ["IndexStoreAssetRepositoryAdapter"]

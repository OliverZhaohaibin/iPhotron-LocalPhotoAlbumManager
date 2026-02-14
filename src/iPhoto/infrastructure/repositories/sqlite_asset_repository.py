import sqlite3
import json
import logging
from pathlib import Path
from typing import List, Optional, Tuple, Any
from datetime import datetime

from iPhoto.domain.models import Asset, MediaType
from iPhoto.domain.models.query import AssetQuery, SortOrder
from iPhoto.domain.repositories import IAssetRepository
from iPhoto.infrastructure.db.pool import ConnectionPool
from iPhoto.config import RECENTLY_DELETED_DIR_NAME

_logger = logging.getLogger(__name__)

class SQLiteAssetRepository(IAssetRepository):
    def __init__(self, pool: ConnectionPool):
        self._pool = pool
        self._db_path = pool._db_path
        _logger.info("[REPO-INIT] SQLiteAssetRepository created, db_path=%s, pool_id=%s", pool._db_path, id(pool))
        self._init_table()
        self._migrate_schema()
        self._ensure_indices()

    def _init_table(self):
        with self._pool.connection() as conn:
            # Match Legacy Schema: PK is 'rel' (path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS assets (
                    rel TEXT PRIMARY KEY,
                    id TEXT,
                    parent_album_path TEXT,
                    dt TEXT,
                    ts INTEGER,
                    bytes INTEGER,
                    mime TEXT,
                    make TEXT,
                    model TEXT,
                    lens TEXT,
                    iso INTEGER,
                    f_number REAL,
                    exposure_time REAL,
                    exposure_compensation REAL,
                    focal_length REAL,
                    w INTEGER,
                    h INTEGER,
                    gps TEXT,
                    content_id TEXT,
                    frame_rate REAL,
                    codec TEXT,
                    still_image_time REAL,
                    dur REAL,
                    original_rel_path TEXT,
                    original_album_id TEXT,
                    original_album_subpath TEXT,
                    live_role INTEGER DEFAULT 0,
                    live_partner_rel TEXT,
                    aspect_ratio REAL,
                    year INTEGER,
                    month INTEGER,
                    media_type INTEGER,
                    is_favorite INTEGER DEFAULT 0,
                    location TEXT,
                    micro_thumbnail BLOB,
                    album_id TEXT,
                    live_photo_group_id TEXT,
                    metadata TEXT
                )
            """)

    def _migrate_schema(self):
        """Ensure schema has all required columns for existing databases."""
        with self._pool.connection() as conn:
            cursor = conn.execute("PRAGMA table_info(assets)")
            columns = {row["name"] for row in cursor.fetchall()}

            # Critical missing columns
            missing_cols = {
                "album_id": "TEXT",
                "live_photo_group_id": "TEXT",
                "metadata": "TEXT",
                "content_identifier": "TEXT",
                "is_favorite": "INTEGER DEFAULT 0",
                "parent_album_path": "TEXT",
                "media_type": "INTEGER",
                "live_role": "INTEGER DEFAULT 0",
                "live_partner_rel": "TEXT",
            }

            for col, dtype in missing_cols.items():
                if col not in columns:
                    conn.execute(f"ALTER TABLE assets ADD COLUMN {col} {dtype}")

    def _ensure_indices(self):
        """Create indices after table and columns exist."""
        with self._pool.connection() as conn:
            # Safe to create index even if column contains NULLs
            conn.execute("CREATE INDEX IF NOT EXISTS idx_assets_album_id ON assets(album_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_assets_parent_album_path ON assets(parent_album_path)")

    def get(self, id: str) -> Optional[Asset]:
        # Note: legacy schema doesn't force ID uniqueness globally, but practically it's our Entity ID.
        with self._pool.connection() as conn:
            row = conn.execute("SELECT * FROM assets WHERE id = ?", (id,)).fetchone()
            if row:
                return self._map_row_to_asset(row)
            return None

    def get_by_path(self, path: Path) -> Optional[Asset]:
        # Find by 'rel' column which stores the path
        # Try exact string match first (OS specific separator)
        path_str = str(path)
        with self._pool.connection() as conn:
            row = conn.execute("SELECT * FROM assets WHERE rel = ?", (path_str,)).fetchone()

            # Fallback: Try POSIX style (forward slashes) if not found
            # This handles Windows where Path('a/b') becomes 'a\b' but DB has 'a/b'
            if not row and path_str != path.as_posix():
                row = conn.execute("SELECT * FROM assets WHERE rel = ?", (path.as_posix(),)).fetchone()

            if row:
                asset = self._map_row_to_asset(row)
                _logger.info(
                    "[REPO-GET] get_by_path(%s) -> found: id=%s, is_favorite=%s (db=%s)",
                    path_str, asset.id, asset.is_favorite, self._db_path,
                )
                return asset
            _logger.warning("[REPO-GET] get_by_path(%s) -> NOT FOUND (db=%s)", path_str, self._db_path)
            return None

    def get_by_album(self, album_id: str) -> List[Asset]:
        with self._pool.connection() as conn:
            rows = conn.execute("SELECT * FROM assets WHERE album_id = ?", (album_id,)).fetchall()
            return [self._map_row_to_asset(row) for row in rows]

    def find_by_query(self, query: AssetQuery) -> List[Asset]:
        sql, params = self._build_sql(query)
        _logger.info(
            "[REPO-QUERY] is_favorite=%s, album_path=%s, db=%s",
            query.is_favorite, query.album_path, self._db_path,
        )
        with self._pool.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
            assets = [self._map_row_to_asset(row) for row in rows]
            fav_count = sum(1 for a in assets if a.is_favorite)
            _logger.info(
                "[REPO-QUERY] Returned %d assets (%d favorites) from db=%s",
                len(assets), fav_count, self._db_path,
            )
            return assets

    def count(self, query: AssetQuery) -> int:
        sql, params = self._build_sql(query, count_only=True)
        with self._pool.connection() as conn:
            return conn.execute(sql, params).fetchone()[0]

    def save(self, asset: Asset) -> None:
        self.save_batch([asset])

    def save_all(self, assets: List[Asset]) -> None:
        self.save_batch(assets)

    def save_batch(self, assets: List[Asset]) -> None:
        data = []
        for asset in assets:
            # Map Enum to Int (0=Photo, 1=Video)
            mt_int = 1 if asset.media_type == MediaType.VIDEO else 0

            metadata = self._sanitize_metadata(asset.metadata)
            micro_thumbnail = metadata.pop("micro_thumbnail", None)
            if micro_thumbnail is None and asset.metadata:
                micro_thumbnail = asset.metadata.get("micro_thumbnail")

            rel_posix = asset.path.as_posix()
            fav_int = 1 if asset.is_favorite else 0
            _logger.info(
                "[REPO-SAVE] rel=%s, id=%s, is_favorite=%s (int=%s), db=%s",
                rel_posix, asset.id, asset.is_favorite, fav_int, self._db_path,
            )

            data.append((
                rel_posix,  # rel (PK) - always use forward slashes for DB consistency
                asset.id,
                asset.album_id,
                mt_int,  # media_type as int
                asset.size_bytes,
                asset.created_at.isoformat() if asset.created_at else None,
                asset.width,
                asset.height,
                asset.duration,
                json.dumps(metadata),
                asset.content_identifier,
                asset.live_photo_group_id,
                fav_int,
                asset.parent_album_path,
                micro_thumbnail,
            ))

        # Use UPSERT to preserve columns not managed by this repository
        # (e.g. live_role, live_partner_rel, gps, mime, make, model, etc.)
        # that are written by the legacy scanner.
        with self._pool.connection() as conn:
            conn.executemany("""
                INSERT INTO assets
                (rel, id, album_id, media_type, bytes, dt, w, h, dur, metadata,
                 content_identifier, live_photo_group_id, is_favorite, parent_album_path, micro_thumbnail)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(rel) DO UPDATE SET
                    id = excluded.id,
                    album_id = excluded.album_id,
                    media_type = excluded.media_type,
                    bytes = excluded.bytes,
                    dt = excluded.dt,
                    w = excluded.w,
                    h = excluded.h,
                    dur = excluded.dur,
                    metadata = excluded.metadata,
                    content_identifier = excluded.content_identifier,
                    live_photo_group_id = excluded.live_photo_group_id,
                    is_favorite = excluded.is_favorite,
                    parent_album_path = excluded.parent_album_path,
                    micro_thumbnail = excluded.micro_thumbnail
            """, data)

    def _sanitize_metadata(self, metadata: Optional[dict]) -> dict:
        if not metadata:
            return {}

        def _coerce(value: Any) -> Any:
            if isinstance(value, (bytes, bytearray, memoryview)):
                return None
            if isinstance(value, Path):
                return str(value)
            if isinstance(value, dict):
                return {key: _coerce(val) for key, val in value.items()}
            if isinstance(value, (list, tuple)):
                return [_coerce(val) for val in value]
            return value

        sanitized = _coerce(metadata)
        if isinstance(sanitized, dict):
            return sanitized
        return {}

    def delete(self, id: str) -> None:
        with self._pool.connection() as conn:
            conn.execute("DELETE FROM assets WHERE id = ?", (id,))

    def _build_sql(self, query: AssetQuery, count_only: bool = False) -> Tuple[str, List[Any]]:
        if count_only:
            sql = "SELECT COUNT(*) FROM assets WHERE 1=1"
        else:
            sql = "SELECT * FROM assets WHERE 1=1"

        params = []
        sql += (
            " AND ("
            "live_role IS NULL OR live_role != 1"
            ")"
            " AND NOT ("
            "live_role IS NULL AND live_photo_group_id IS NOT NULL AND media_type = 1"
            ")"
        )

        if query.album_id:
            sql += " AND album_id = ?"
            params.append(query.album_id)

        if query.album_path:
            if query.include_subalbums:
                sql += " AND (parent_album_path = ? OR parent_album_path LIKE ?)"
                params.extend([query.album_path, f"{query.album_path}/%"])
            else:
                sql += " AND parent_album_path = ?"
                params.append(query.album_path)
        if query.album_path != RECENTLY_DELETED_DIR_NAME:
            sql += (
                " AND (parent_album_path IS NULL"
                " OR (parent_album_path != ? AND parent_album_path NOT LIKE ?))"
            )
            params.extend([RECENTLY_DELETED_DIR_NAME, f"{RECENTLY_DELETED_DIR_NAME}/%"])

        if query.media_types:
            includes_live = MediaType.LIVE_PHOTO in query.media_types
            includes_image = MediaType.IMAGE in query.media_types or MediaType.PHOTO in query.media_types
            includes_video = MediaType.VIDEO in query.media_types

            media_clauses: list[str] = []
            if includes_live and not includes_image:
                media_clauses.append(
                    "("
                    "(live_role = 0 AND live_partner_rel IS NOT NULL)"
                    " OR "
                    "(live_photo_group_id IS NOT NULL AND media_type != 1)"
                    ")"
                )

            if includes_image:
                media_clauses.append("media_type = 0")
            if includes_video:
                media_clauses.append("media_type = 1")

            if media_clauses:
                sql += " AND (" + " OR ".join(media_clauses) + ")"

        if query.is_favorite is not None:
            sql += " AND is_favorite = ?"
            params.append(int(query.is_favorite))

        if query.date_from:
            sql += " AND dt >= ?"
            params.append(query.date_from.isoformat())

        if query.date_to:
            sql += " AND dt <= ?"
            params.append(query.date_to.isoformat())

        if not count_only:
            # Map 'ts' to 'created_at' if needed, or stick to field names
            # Whitelist validation for order_by
            ALLOWED_SORT_COLUMNS = {'created_at', 'ts', 'size_bytes', 'id', 'path', 'media_type', 'is_favorite'}
            order_col = query.order_by

            if order_col == 'ts': order_col = 'dt' # Correctly map to 'dt' column
            if order_col == 'created_at': order_col = 'dt' # Correctly map to 'dt' column

            if order_col not in ALLOWED_SORT_COLUMNS and order_col != 'dt':
                order_col = 'dt' # Default fallback safe value

            # Whitelist validation for order_by prevents injection, but we must ensure column exists
            sql += f" ORDER BY {order_col} {query.order.value}"

            if query.limit:
                sql += " LIMIT ? OFFSET ?"
                params.extend([query.limit, query.offset])

        return sql, params

    def _map_row_to_asset(self, row) -> Asset:
        # Handle column name differences (rel vs path, dt vs created_at, bytes vs size_bytes)
        keys = row.keys()

        # 1. Path/Rel
        rel_path = row["rel"] if "rel" in keys else row.get("path")

        # 2. DateTime
        created_at = None
        if "dt" in keys and row["dt"]:
             try:
                 created_at = datetime.fromisoformat(row["dt"].replace("Z", "+00:00"))
             except ValueError:
                 pass
        elif "created_at" in keys and row["created_at"]:
             created_at = datetime.fromisoformat(row["created_at"])

        # 3. Media Type (Int -> Enum)
        mt_raw = row["media_type"]
        if isinstance(mt_raw, int):
            media_type = MediaType.VIDEO if mt_raw == 1 else MediaType.IMAGE
        else:
            try:
                media_type = MediaType(mt_raw)
            except (ValueError, TypeError):
                media_type = MediaType.IMAGE

        # 4. JSON Fields
        meta = {}
        if "metadata" in keys and row["metadata"]:
            try:
                meta = json.loads(row["metadata"])
            except json.JSONDecodeError:
                pass

        # 5. Optional columns handling
        is_favorite = bool(row["is_favorite"]) if "is_favorite" in keys and row["is_favorite"] else False
        album_id = row["album_id"] if "album_id" in keys else None
        live_group = row["live_photo_group_id"] if "live_photo_group_id" in keys else None
        live_partner_rel = row["live_partner_rel"] if "live_partner_rel" in keys else None
        live_role = row["live_role"] if "live_role" in keys else None
        content_id = row["content_identifier"] if "content_identifier" in keys else row.get("content_id")
        location = row["location"] if "location" in keys else None
        micro_thumbnail = row["micro_thumbnail"] if "micro_thumbnail" in keys else None
        if micro_thumbnail is None and "thumb_16" in keys:
            micro_thumbnail = row["thumb_16"]

        gps = row["gps"] if "gps" in keys else None
        if isinstance(gps, str) and gps.strip():
            try:
                parsed_gps = json.loads(gps)
                if isinstance(parsed_gps, dict):
                    meta["gps"] = parsed_gps
            except json.JSONDecodeError:
                pass

        if location:
            meta["location"] = location
        if micro_thumbnail and "micro_thumbnail" not in meta:
            meta["micro_thumbnail"] = micro_thumbnail
        if live_partner_rel:
            meta["live_partner_rel"] = live_partner_rel
        if live_role is not None:
            meta["live_role"] = live_role

        if not live_group and live_partner_rel and live_role != 1:
            live_group = live_partner_rel

        return Asset(
            id=row["id"],
            album_id=album_id or "", # Default to empty string if missing? Or Optional
            path=Path(rel_path),
            media_type=media_type,
            size_bytes=row["bytes"] if "bytes" in keys else row.get("size_bytes", 0),
            created_at=created_at,
            width=row["w"] if "w" in keys else row.get("width"),
            height=row["h"] if "h" in keys else row.get("height"),
            duration=row["dur"] if "dur" in keys else row.get("duration"),
            metadata=meta,
            content_identifier=content_id,
            live_photo_group_id=live_group,
            is_favorite=is_favorite,
            parent_album_path=row["parent_album_path"] if "parent_album_path" in keys else None
        )

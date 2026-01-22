import sqlite3
import json
from pathlib import Path
from typing import List, Optional, Tuple, Any
from datetime import datetime

from src.iPhoto.domain.models import Asset, MediaType
from src.iPhoto.domain.models.query import AssetQuery, SortOrder
from src.iPhoto.domain.repositories import IAssetRepository
from src.iPhoto.infrastructure.db.pool import ConnectionPool

class SQLiteAssetRepository(IAssetRepository):
    def __init__(self, pool: ConnectionPool):
        self._pool = pool
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
                "media_type": "INTEGER"
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

    def get_by_album(self, album_id: str) -> List[Asset]:
        with self._pool.connection() as conn:
            rows = conn.execute("SELECT * FROM assets WHERE album_id = ?", (album_id,)).fetchall()
            return [self._map_row_to_asset(row) for row in rows]

    def find_by_query(self, query: AssetQuery) -> List[Asset]:
        sql, params = self._build_sql(query)
        with self._pool.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [self._map_row_to_asset(row) for row in rows]

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

            # Extract micro_thumbnail from metadata if present to avoid JSON serialization error
            # Copy metadata to avoid mutating the object
            meta_copy = asset.metadata.copy()
            micro_thumbnail = meta_copy.pop("micro_thumbnail", None)

            data.append((
                str(asset.path), # rel (PK)
                asset.id,
                asset.album_id,
                mt_int, # media_type as int
                asset.size_bytes,
                asset.created_at.isoformat() if asset.created_at else None,
                asset.width,
                asset.height,
                asset.duration,
                json.dumps(meta_copy),
                asset.content_identifier,
                asset.live_photo_group_id,
                1 if asset.is_favorite else 0,
                asset.parent_album_path,
                micro_thumbnail
            ))

        # Note: Writing to 'rel' as PK
        with self._pool.connection() as conn:
            conn.executemany("""
                INSERT OR REPLACE INTO assets
                (rel, id, album_id, media_type, bytes, dt, w, h, dur, metadata, content_identifier, live_photo_group_id, is_favorite, parent_album_path, micro_thumbnail)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, data)

    def delete(self, id: str) -> None:
        with self._pool.connection() as conn:
            conn.execute("DELETE FROM assets WHERE id = ?", (id,))

    def _build_sql(self, query: AssetQuery, count_only: bool = False) -> Tuple[str, List[Any]]:
        if count_only:
            sql = "SELECT COUNT(*) FROM assets WHERE 1=1"
        else:
            sql = "SELECT * FROM assets WHERE 1=1"

        params = []

        # Album filtering logic (Hybrid Support: Match ID OR Path)
        album_criteria = []

        if query.album_id:
            album_criteria.append("album_id = ?")
            params.append(query.album_id)

        if query.album_path:
            if query.include_subalbums:
                album_criteria.append("(parent_album_path = ? OR parent_album_path LIKE ?)")
                params.extend([query.album_path, f"{query.album_path}/%"])
            else:
                album_criteria.append("parent_album_path = ?")
                params.append(query.album_path)

        if album_criteria:
            sql += " AND (" + " OR ".join(album_criteria) + ")"

        if query.media_types:
            placeholders = ','.join('?' * len(query.media_types))
            sql += f" AND media_type IN ({placeholders})"
            params.extend([mt.value for mt in query.media_types])

        if query.is_favorite is not None:
            sql += " AND is_favorite = ?"
            params.append(int(query.is_favorite))

        if query.date_from:
            sql += " AND created_at >= ?"
            params.append(query.date_from.isoformat())

        if query.date_to:
            sql += " AND created_at <= ?"
            params.append(query.date_to.isoformat())

        if not count_only:
            # Map 'ts' to 'created_at' if needed, or stick to field names
            # Whitelist validation for order_by
            # Mapping: Domain Field -> DB Column
            FIELD_MAP = {
                'created_at': 'dt',
                'size_bytes': 'bytes',
                'path': 'rel'
            }
            ALLOWED_SORT_COLUMNS = {'created_at', 'ts', 'size_bytes', 'id', 'path', 'media_type', 'is_favorite', 'dt', 'bytes', 'rel'}

            order_col = query.order_by
            if order_col in FIELD_MAP:
                order_col = FIELD_MAP[order_col]

            if order_col not in ALLOWED_SORT_COLUMNS:
                order_col = 'dt' # Default fallback safe value

            sql += f" ORDER BY {order_col} {query.order.value}"

            if query.limit:
                sql += " LIMIT ? OFFSET ?"
                params.extend([query.limit, query.offset])

        return sql, params

    def _map_row_to_asset(self, row) -> Asset:
        # Handle column name differences (rel vs path, dt vs created_at, bytes vs size_bytes)
        # Convert sqlite3.Row to dict for .get() access
        row_dict = dict(row)

        # 1. Path/Rel
        rel_path = row_dict.get("rel") or row_dict.get("path")

        # 2. DateTime
        created_at = None
        if row_dict.get("dt"):
             try:
                 created_at = datetime.fromisoformat(row_dict["dt"].replace("Z", "+00:00"))
             except ValueError:
                 pass
        elif row_dict.get("created_at"):
             try:
                 created_at = datetime.fromisoformat(row_dict["created_at"])
             except ValueError:
                 pass

        # 3. Media Type (Int -> Enum)
        mt_raw = row_dict.get("media_type")
        if isinstance(mt_raw, int):
            media_type = MediaType.VIDEO if mt_raw == 1 else MediaType.IMAGE
        else:
            try:
                media_type = MediaType(mt_raw)
            except (ValueError, TypeError):
                media_type = MediaType.IMAGE

        # 4. JSON Fields
        meta = {}
        if row_dict.get("metadata"):
            try:
                meta = json.loads(row_dict["metadata"])
            except json.JSONDecodeError:
                pass

        # 5. Optional columns handling
        is_favorite = bool(row_dict.get("is_favorite")) if row_dict.get("is_favorite") else False
        album_id = row_dict.get("album_id")
        live_group = row_dict.get("live_photo_group_id")
        content_id = row_dict.get("content_identifier") or row_dict.get("content_id")

        return Asset(
            id=row_dict["id"],
            album_id=album_id or "", # Default to empty string if missing? Or Optional
            path=Path(rel_path),
            media_type=media_type,
            size_bytes=row_dict.get("bytes") or row_dict.get("size_bytes", 0),
            created_at=created_at,
            width=row_dict.get("w") or row_dict.get("width"),
            height=row_dict.get("h") or row_dict.get("height"),
            duration=row_dict.get("dur") or row_dict.get("duration"),
            metadata=meta,
            content_identifier=content_id,
            live_photo_group_id=live_group,
            is_favorite=is_favorite,
            parent_album_path=row_dict.get("parent_album_path")
        )

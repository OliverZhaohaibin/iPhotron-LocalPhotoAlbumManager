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
            # Create table with FULL current schema for new DBs
            conn.execute("""
                CREATE TABLE IF NOT EXISTS assets (
                    id TEXT PRIMARY KEY,
                    album_id TEXT,
                    path TEXT,
                    media_type TEXT,
                    size_bytes INTEGER,
                    created_at TEXT,
                    width INTEGER,
                    height INTEGER,
                    duration REAL,
                    metadata TEXT,
                    content_identifier TEXT,
                    live_photo_group_id TEXT,
                    is_favorite INTEGER DEFAULT 0,
                    parent_album_path TEXT
                )
            """)

    def _migrate_schema(self):
        """Ensure schema has all required columns for existing databases."""
        with self._pool.connection() as conn:
            # Check existing columns
            cursor = conn.execute("PRAGMA table_info(assets)")
            columns = {row["name"] for row in cursor.fetchall()}

            if "is_favorite" not in columns:
                conn.execute("ALTER TABLE assets ADD COLUMN is_favorite INTEGER DEFAULT 0")

            if "parent_album_path" not in columns:
                conn.execute("ALTER TABLE assets ADD COLUMN parent_album_path TEXT")

    def _ensure_indices(self):
        """Create indices after table and columns exist."""
        with self._pool.connection() as conn:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_assets_album_id ON assets(album_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_assets_parent_album_path ON assets(parent_album_path)")

    def get(self, id: str) -> Optional[Asset]:
        with self._pool.connection() as conn:
            row = conn.execute("SELECT * FROM assets WHERE id = ?", (id,)).fetchone()
            if row:
                return self._map_row_to_asset(row)
            return None

    def get_by_album(self, album_id: str) -> List[Asset]:
        # Legacy implementation
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
            data.append((
                asset.id,
                asset.album_id,
                str(asset.path),
                asset.media_type.value,
                asset.size_bytes,
                asset.created_at.isoformat() if asset.created_at else None,
                asset.width,
                asset.height,
                asset.duration,
                json.dumps(asset.metadata),
                asset.content_identifier,
                asset.live_photo_group_id,
                1 if asset.is_favorite else 0,
                asset.parent_album_path
            ))

        with self._pool.connection() as conn:
            conn.executemany("""
                INSERT OR REPLACE INTO assets
                (id, album_id, path, media_type, size_bytes, created_at, width, height, duration, metadata, content_identifier, live_photo_group_id, is_favorite, parent_album_path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            ALLOWED_SORT_COLUMNS = {'created_at', 'ts', 'size_bytes', 'id', 'path', 'media_type', 'is_favorite'}
            order_col = query.order_by

            if order_col == 'ts': order_col = 'created_at'

            if order_col not in ALLOWED_SORT_COLUMNS:
                order_col = 'created_at' # Default fallback safe value

            sql += f" ORDER BY {order_col} {query.order.value}"

            if query.limit:
                sql += " LIMIT ? OFFSET ?"
                params.extend([query.limit, query.offset])

        return sql, params

    def _map_row_to_asset(self, row) -> Asset:
        created_at = datetime.fromisoformat(row["created_at"]) if row["created_at"] else None

        keys = row.keys()
        is_favorite = bool(row["is_favorite"]) if "is_favorite" in keys and row["is_favorite"] is not None else False
        parent_album_path = row["parent_album_path"] if "parent_album_path" in keys else None

        return Asset(
            id=row["id"],
            album_id=row["album_id"],
            path=Path(row["path"]),
            media_type=MediaType(row["media_type"]),
            size_bytes=row["size_bytes"],
            created_at=created_at,
            width=row["width"],
            height=row["height"],
            duration=row["duration"],
            metadata=json.loads(row["metadata"]),
            content_identifier=row["content_identifier"],
            live_photo_group_id=row["live_photo_group_id"],
            is_favorite=is_favorite,
            parent_album_path=parent_album_path
        )

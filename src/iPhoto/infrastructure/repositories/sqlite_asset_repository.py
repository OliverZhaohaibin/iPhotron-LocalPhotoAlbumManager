import sqlite3
import json
from pathlib import Path
from typing import List, Optional
from datetime import datetime

from src.iPhoto.domain.models import Asset, MediaType
from src.iPhoto.domain.repositories import IAssetRepository
from src.iPhoto.infrastructure.db.pool import ConnectionPool

class SQLiteAssetRepository(IAssetRepository):
    def __init__(self, pool: ConnectionPool):
        self._pool = pool
        self._init_table()

    def _init_table(self):
        with self._pool.connection() as conn:
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
                    live_photo_group_id TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_assets_album_id ON assets(album_id)")

    def get(self, id: str) -> Optional[Asset]:
        with self._pool.connection() as conn:
            row = conn.execute("SELECT * FROM assets WHERE id = ?", (id,)).fetchone()
            if row:
                return self._map_row_to_asset(row)
            return None

    def get_by_album(self, album_id: str) -> List[Asset]:
        with self._pool.connection() as conn:
            rows = conn.execute("SELECT * FROM assets WHERE album_id = ?", (album_id,)).fetchall()
            return [self._map_row_to_asset(row) for row in rows]

    def save(self, asset: Asset) -> None:
        self.save_all([asset])

    def save_all(self, assets: List[Asset]) -> None:
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
                asset.live_photo_group_id
            ))

        with self._pool.connection() as conn:
            conn.executemany("""
                INSERT OR REPLACE INTO assets
                (id, album_id, path, media_type, size_bytes, created_at, width, height, duration, metadata, content_identifier, live_photo_group_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, data)

    def delete(self, id: str) -> None:
        with self._pool.connection() as conn:
            conn.execute("DELETE FROM assets WHERE id = ?", (id,))

    def _map_row_to_asset(self, row) -> Asset:
        created_at = datetime.fromisoformat(row["created_at"]) if row["created_at"] else None
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
            live_photo_group_id=row["live_photo_group_id"]
        )

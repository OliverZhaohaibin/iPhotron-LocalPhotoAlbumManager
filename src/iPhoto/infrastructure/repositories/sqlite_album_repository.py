import sqlite3
import json
from pathlib import Path
from typing import List, Optional
from datetime import datetime

from iPhoto.domain.models import Album
from iPhoto.domain.repositories import IAlbumRepository
from iPhoto.infrastructure.db.pool import ConnectionPool

class SQLiteAlbumRepository(IAlbumRepository):
    def __init__(self, pool: ConnectionPool):
        self._pool = pool
        self._init_table()

    def _init_table(self):
        with self._pool.connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS albums (
                    id TEXT PRIMARY KEY,
                    path TEXT UNIQUE,
                    title TEXT,
                    created_at TEXT,
                    description TEXT,
                    cover_asset_id TEXT
                )
            """)

    def get(self, id: str) -> Optional[Album]:
        with self._pool.connection() as conn:
            row = conn.execute("SELECT * FROM albums WHERE id = ?", (id,)).fetchone()
            if row:
                return self._map_row_to_album(row)
            return None

    def get_by_path(self, path: Path) -> Optional[Album]:
        path_str = str(path)
        with self._pool.connection() as conn:
            row = conn.execute("SELECT * FROM albums WHERE path = ?", (path_str,)).fetchone()
            if row:
                return self._map_row_to_album(row)
            return None

    def save(self, album: Album) -> None:
        with self._pool.connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO albums
                (id, path, title, created_at, description, cover_asset_id)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                album.id,
                str(album.path),
                album.title,
                album.created_at.isoformat() if album.created_at else None,
                album.description,
                album.cover_asset_id
            ))

    def _map_row_to_album(self, row) -> Album:
        created_at = datetime.fromisoformat(row["created_at"]) if row["created_at"] else None
        return Album(
            id=row["id"],
            path=Path(row["path"]),
            title=row["title"],
            created_at=created_at,
            description=row["description"],
            cover_asset_id=row["cover_asset_id"]
        )

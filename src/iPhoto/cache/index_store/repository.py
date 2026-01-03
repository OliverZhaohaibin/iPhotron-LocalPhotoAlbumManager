"""High-level repository interface for asset persistence.

This module provides the main API for CRUD operations on assets, delegating
infrastructure concerns to specialized components.
"""
from __future__ import annotations

import json
import sqlite3
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from ...config import WORK_DIR_NAME
from ...utils.logging import get_logger
from .engine import DatabaseManager
from .migrations import SchemaMigrator
from .queries import QueryBuilder
from .recovery import RecoveryService

logger = get_logger()

# Database filename for the global index
GLOBAL_INDEX_DB_NAME = "global_index.db"


class AssetRepository:
    """High-level API for asset CRUD operations.
    
    This class focuses purely on domain logic (reading, writing, and querying
    assets) while delegating infrastructure concerns like connection management,
    schema migrations, and recovery to specialized components.
    """

    def __init__(self, album_root: Path, use_global_index: bool = True):
        """Initialize the asset repository.
        
        Args:
            album_root: The root directory of the library or album.
            use_global_index: If True, use the global `global_index.db` at the
                root. If False, use a per-album `index.db` for backward
                compatibility during migration.
        """
        self.album_root = album_root
        if use_global_index:
            self.path = album_root / WORK_DIR_NAME / GLOBAL_INDEX_DB_NAME
        else:
            # Legacy per-album database path
            self.path = album_root / WORK_DIR_NAME / "index.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        
        self._db_manager = DatabaseManager(self.path)
        self._init_db()

    def _init_db(self) -> None:
        """Initialize the database schema."""
        try:
            # Use a transient connection for initialization
            with sqlite3.connect(self.path, timeout=10.0) as conn:
                SchemaMigrator.initialize_schema(conn)
        except sqlite3.DatabaseError as exc:
            logger.warning("Detected index.db corruption at %s: %s", self.path, exc)
            recovery = RecoveryService(
                self.path,
                SchemaMigrator.initialize_schema,
                self._db_row_to_dict,
                self._insert_rows,
            )
            recovery.recover()

    def transaction(self):
        """Context manager for batching multiple operations.
        
        Example:
            >>> with repo.transaction():
            ...     repo.upsert_row("a.jpg", {...})
            ...     repo.upsert_row("b.jpg", {...})
        """
        return self._db_manager.transaction()

    def write_rows(self, rows: Iterable[Dict[str, Any]]) -> None:
        """Rewrite the entire index with *rows*."""
        with self.transaction() as conn:
            conn.execute("DELETE FROM assets")
            self._insert_rows(conn, rows)

    def append_rows(self, rows: Iterable[Dict[str, Any]]) -> None:
        """Merge *rows* into the index, replacing duplicates by ``rel`` key."""
        with self.transaction() as conn:
            self._insert_rows(conn, rows)

    def upsert_row(self, rel: str, row: Dict[str, Any]) -> None:
        """Insert or update a single row identified by *rel*."""
        row_data = row.copy()
        row_data["rel"] = rel
        with self.transaction() as conn:
            self._insert_rows(conn, [row_data])

    def remove_rows(self, rels: Iterable[str]) -> None:
        """Drop any index rows whose ``rel`` key matches *rels*."""
        removable = list(rels)
        if not removable:
            return

        placeholders = ", ".join(["?"] * len(removable))
        query = f"DELETE FROM assets WHERE rel IN ({placeholders})"
        self._db_manager.execute_in_transaction(query, removable)

    def read_all(
        self,
        sort_by_date: bool = False,
        filter_hidden: bool = False,
    ) -> Iterator[Dict[str, Any]]:
        """Yield all rows from the index.
        
        Args:
            sort_by_date: If True, order results by 'dt' descending (newest first).
            filter_hidden: If True, exclude hidden assets (e.g. motion components).
        """
        conn = self._db_manager.get_connection()
        should_close = (conn != self._db_manager._conn)

        try:
            query = "SELECT * FROM assets"
            where_clauses = []

            if filter_hidden:
                where_clauses.append("live_role = 0")

            if where_clauses:
                query += " WHERE " + " AND ".join(where_clauses)

            if sort_by_date:
                query += " ORDER BY dt DESC NULLS LAST, id DESC"

            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(query)
            for row in cursor:
                yield self._db_row_to_dict(row)
        finally:
            if should_close:
                conn.close()

    def read_geotagged(self) -> Iterator[Dict[str, Any]]:
        """Yield only rows that contain GPS metadata."""
        conn = self._db_manager.get_connection()
        should_close = (conn != self._db_manager._conn)

        try:
            query = "SELECT * FROM assets WHERE gps IS NOT NULL"
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(query)
            for row in cursor:
                yield self._db_row_to_dict(row)
        finally:
            if should_close:
                conn.close()

    def get_assets_page(
        self,
        cursor_dt: Optional[str] = None,
        cursor_id: Optional[str] = None,
        limit: int = 100,
        album_path: Optional[str] = None,
        include_subalbums: bool = False,
        filter_hidden: bool = True,
        filter_params: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch a page of assets using cursor-based pagination.
        
        This method uses Seek Pagination (keyset pagination) for efficient
        retrieval of large datasets.
        
        Args:
            cursor_dt: The timestamp of the last item from the previous page.
            cursor_id: The ID of the last item from the previous page.
            limit: Maximum number of items to return (default: 100).
            album_path: If provided, filter to assets in this album path.
            include_subalbums: If True, include assets from sub-albums.
            filter_hidden: If True, exclude hidden assets.
            filter_params: Additional filter parameters.
        
        Returns:
            A list of asset dictionaries for the requested page.
        """
        query, params = QueryBuilder.build_pagination_query(
            album_path=album_path,
            include_subalbums=include_subalbums,
            filter_hidden=filter_hidden,
            filter_params=filter_params,
            cursor_dt=cursor_dt,
            cursor_id=cursor_id,
            limit=limit,
        )

        conn = self._db_manager.get_connection()
        should_close = (conn != self._db_manager._conn)

        try:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(query, params)

            results = []
            for row in cursor:
                results.append(self._db_row_to_dict(row))
            return results
        finally:
            if should_close:
                conn.close()

    def read_geometry_only(
        self,
        filter_params: Optional[Dict[str, Any]] = None,
        sort_by_date: bool = True,
        album_path: Optional[str] = None,
        include_subalbums: bool = True,
    ) -> Iterator[Dict[str, Any]]:
        """Yield lightweight asset rows for fast grid layout.
        
        Fetches only the columns strictly required for grid layout, badges,
        and sorting.
        
        Args:
            filter_params: Optional dictionary of SQL filter criteria.
            sort_by_date: If True, sort results by date descending.
            album_path: If provided, filter to assets in this album path.
            include_subalbums: If True, include assets from sub-albums.
        """
        # Columns needed for the lightweight "viewport-first" loading strategy
        columns = [
            "id", "rel", "aspect_ratio", "media_type", "live_partner_rel",
            "dur", "year", "month", "dt", "ts", "content_id", "bytes",
            "mime", "w", "h", "original_rel_path", "original_album_id",
            "original_album_subpath", "is_favorite", "location", "gps",
            "micro_thumbnail"
        ]

        query, params = QueryBuilder.build_pagination_query(
            select_clause=f"SELECT {', '.join(columns)}",
            base_where=["live_role = 0"],
            album_path=album_path,
            include_subalbums=include_subalbums,
            filter_params=filter_params,
            sort_by_date=sort_by_date,
        )

        conn = self._db_manager.get_connection()
        should_close = (conn != self._db_manager._conn)

        try:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(query, params)
            for row in cursor:
                d = dict(row)
                # Parse GPS if present (stored as JSON string)
                if d.get("gps"):
                    try:
                        d["gps"] = json.loads(d["gps"])
                    except (json.JSONDecodeError, TypeError):
                        d["gps"] = None
                yield d
        finally:
            if should_close:
                conn.close()

    def read_album_assets(
        self,
        album_path: str,
        include_subalbums: bool = False,
        sort_by_date: bool = True,
        filter_hidden: bool = True,
        filter_params: Optional[Dict[str, Any]] = None,
    ) -> Iterator[Dict[str, Any]]:
        """Yield assets belonging to a specific album.
        
        Args:
            album_path: The album path to filter (e.g., "2023/Trip").
            include_subalbums: If True, include assets from sub-albums.
            sort_by_date: If True, order results by date descending.
            filter_hidden: If True, exclude hidden assets.
            filter_params: Additional filter parameters.
        
        Yields:
            Asset dictionaries for the matching album(s).
        """
        query, params = QueryBuilder.build_pagination_query(
            album_path=album_path,
            include_subalbums=include_subalbums,
            filter_hidden=filter_hidden,
            filter_params=filter_params,
            sort_by_date=sort_by_date,
        )

        conn = self._db_manager.get_connection()
        should_close = (conn != self._db_manager._conn)

        try:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(query, params)

            for row in cursor:
                yield self._db_row_to_dict(row)
        finally:
            if should_close:
                conn.close()

    def count(
        self,
        filter_hidden: bool = False,
        filter_params: Optional[Dict[str, Any]] = None,
        album_path: Optional[str] = None,
        include_subalbums: bool = True,
    ) -> int:
        """Return the total number of assets matching the given filters.
        
        Args:
            filter_hidden: If True, exclude hidden assets.
            filter_params: Additional filter parameters.
            album_path: If provided, filter to assets in this album path.
            include_subalbums: If True, include assets from sub-albums.
        
        Returns:
            The number of assets matching the filters.
        """
        query, params = QueryBuilder.build_pagination_query(
            select_clause="SELECT COUNT(*)",
            album_path=album_path,
            include_subalbums=include_subalbums,
            filter_hidden=filter_hidden,
            filter_params=filter_params,
            sort_by_date=False,
        )

        conn = self._db_manager.get_connection()
        should_close = (conn != self._db_manager._conn)

        try:
            cursor = conn.execute(query, params)
            result = cursor.fetchone()
            return result[0] if result else 0
        finally:
            if should_close:
                conn.close()

    def set_favorite_status(self, rel: str, is_favorite: bool) -> None:
        """Toggle the favorite status for a single asset efficiently."""
        val = 1 if is_favorite else 0
        self._db_manager.execute_in_transaction(
            "UPDATE assets SET is_favorite = ? WHERE rel = ?",
            (val, rel),
        )

    def sync_favorites(self, featured_rels: Iterable[str]) -> None:
        """Synchronise the DB 'is_favorite' column with the provided list."""
        featured_rels_list = list(featured_rels)
        
        # Normalize input paths to ensure consistent comparison (NFC)
        input_normalized_map = {
            unicodedata.normalize("NFC", r): r for r in featured_rels_list
        }
        featured_normalized_set = set(input_normalized_map.keys())

        with self.transaction() as conn:
            # Fetch all rels from the DB to build a normalized-to-original mapping
            cursor = conn.execute("SELECT rel FROM assets")
            all_rels_map = {
                unicodedata.normalize("NFC", row[0]): row[0] for row in cursor
            }

            # Fetch currently marked favorites
            current_favs_normalized = {
                unicodedata.normalize("NFC", row[0])
                for row in conn.execute("SELECT rel FROM assets WHERE is_favorite != 0")
            }

            # Determine which rows need updates
            to_remove_normalized = current_favs_normalized - featured_normalized_set
            to_add_normalized = featured_normalized_set - current_favs_normalized

            # Apply updates
            if to_remove_normalized:
                to_remove_original = [
                    all_rels_map[n] for n in to_remove_normalized if n in all_rels_map
                ]
                conn.executemany(
                    "UPDATE assets SET is_favorite = 0 WHERE rel = ?",
                    [(r,) for r in to_remove_original],
                )

            if to_add_normalized:
                to_add_original = [
                    all_rels_map.get(n, input_normalized_map[n]) 
                    for n in to_add_normalized
                ]
                conn.executemany(
                    "UPDATE assets SET is_favorite = 1 WHERE rel = ?",
                    [(r,) for r in to_add_original],
                )

    def update_location(self, rel: str, location: str) -> None:
        """Update the location string for a single asset."""
        self._db_manager.execute_in_transaction(
            "UPDATE assets SET location = ? WHERE rel = ?",
            (location, rel),
        )

    def apply_live_role_updates(
        self,
        updates: List[Tuple[str, int, Optional[str]]],
    ) -> None:
        """Update live_role and live_partner_rel for a batch of assets.
        
        Args:
            updates: List of (rel, live_role, live_partner_rel) tuples.
        """
        if not updates:
            self._db_manager.execute_in_transaction(
                "UPDATE assets SET live_role = 0, live_partner_rel = NULL"
            )
            return

        with self.transaction() as conn:
            conn.execute("UPDATE assets SET live_role = 0, live_partner_rel = NULL")
            query = "UPDATE assets SET live_role = ?, live_partner_rel = ? WHERE rel = ?"
            params = [(role, partner, rel) for rel, role, partner in updates]
            conn.executemany(query, params)

    def list_albums(self) -> List[str]:
        """Return a list of distinct album paths in the index."""
        conn = self._db_manager.get_connection()
        should_close = (conn != self._db_manager._conn)

        try:
            cursor = conn.execute(
                "SELECT DISTINCT parent_album_path FROM assets "
                "WHERE parent_album_path IS NOT NULL "
                "ORDER BY parent_album_path"
            )
            return [row[0] for row in cursor if row[0]]
        finally:
            if should_close:
                conn.close()

    def count_album_assets(
        self,
        album_path: str,
        include_subalbums: bool = False,
        filter_hidden: bool = True,
    ) -> int:
        """Return the count of assets in a specific album.
        
        Args:
            album_path: The album path to count.
            include_subalbums: If True, include assets from sub-albums.
            filter_hidden: If True, exclude hidden assets.
        
        Returns:
            The number of assets matching the criteria.
        """
        return self.count(
            filter_hidden=filter_hidden,
            album_path=album_path,
            include_subalbums=include_subalbums,
        )

    # Helper methods for row conversion
    def _insert_rows(
        self,
        conn: sqlite3.Connection,
        rows: Iterable[Dict[str, Any]],
    ) -> None:
        """Helper to bulk insert rows."""
        data_list = []
        for row in rows:
            data = self._row_to_db_params(row)
            data_list.append(data)

        if not data_list:
            return

        columns = [
            "rel", "id", "parent_album_path", "dt", "ts", "bytes", "mime",
            "make", "model", "lens", "iso", "f_number", "exposure_time",
            "exposure_compensation", "focal_length", "w", "h", "gps",
            "content_id", "frame_rate", "codec", "still_image_time", "dur",
            "original_rel_path", "original_album_id", "original_album_subpath",
            "live_role", "live_partner_rel", "aspect_ratio", "year", "month",
            "media_type", "is_favorite", "location", "micro_thumbnail"
        ]
        placeholders = ", ".join(["?"] * len(columns))
        query = (
            f"INSERT OR REPLACE INTO assets ({', '.join(columns)}) "
            f"VALUES ({placeholders})"
        )

        conn.executemany(query, data_list)

    def _row_to_db_params(self, row: Dict[str, Any]) -> List[Any]:
        """Map a dictionary row to a list of values for the DB."""
        gps_val = row.get("gps")
        gps_str = json.dumps(gps_val) if gps_val is not None else None

        # Compute parent_album_path from rel if not provided
        rel = row.get("rel")
        parent_album_path = row.get("parent_album_path")
        if parent_album_path is None and rel:
            rel_path = Path(rel)
            parent = rel_path.parent
            parent_album_path = parent.as_posix() if parent != Path(".") else ""

        return [
            rel,
            row.get("id"),
            parent_album_path,
            row.get("dt"),
            row.get("ts"),
            row.get("bytes"),
            row.get("mime"),
            row.get("make"),
            row.get("model"),
            row.get("lens"),
            row.get("iso"),
            row.get("f_number"),
            row.get("exposure_time"),
            row.get("exposure_compensation"),
            row.get("focal_length"),
            row.get("w"),
            row.get("h"),
            gps_str,
            row.get("content_id"),
            row.get("frame_rate"),
            row.get("codec"),
            row.get("still_image_time"),
            row.get("dur"),
            row.get("original_rel_path"),
            row.get("original_album_id"),
            row.get("original_album_subpath"),
            row.get("live_role", 0),
            row.get("live_partner_rel"),
            row.get("aspect_ratio"),
            row.get("year"),
            row.get("month"),
            row.get("media_type"),
            row.get("is_favorite", 0),
            row.get("location"),
            row.get("micro_thumbnail"),
        ]

    def _db_row_to_dict(self, db_row: sqlite3.Row) -> Dict[str, Any]:
        """Map a DB row back to a dictionary."""
        d = dict(db_row)
        if d["gps"] is not None:
            try:
                d["gps"] = json.loads(d["gps"])
            except json.JSONDecodeError:
                d["gps"] = None
        return d

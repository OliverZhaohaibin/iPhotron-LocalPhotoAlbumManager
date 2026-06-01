"""High-level repository interface for asset persistence.

This module provides the main API for CRUD operations on assets, delegating
infrastructure concerns to specialized components.

Architecture:
    This module implements a **Single Global Database** pattern where all metadata
    and asset information is stored in one centralized SQLite database at the
    library root. Key architectural principles:
    
    - **Single Write Gateway**: All write operations go through this repository
    - **Idempotent Writes**: Duplicate scans produce identical results (upsert)
    - **Additive-Only Scans**: Scanning never deletes data from the database
    - **Multiple Entry Points**: Scans can be triggered from any location
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from ...domain.models.query import CollectionQuery, PageCursor, PageResult, WindowResult
from ...infrastructure.services.performance_events import (
    audit_full_scan_query,
    emit_perf_event,
    explain_enabled,
    monotonic_ms,
)
from ...people.status import normalize_face_status
from ...utils.logging import get_logger
from ...utils.pathutils import ensure_work_dir
from .engine import DatabaseManager
from .migrations import SchemaMigrator
from .queries import QueryBuilder
from .recovery import RecoveryService
from .row_mapper import db_row_to_dict, insert_rows, row_to_db_params
from .scan_merge import merge_scan_rows as merge_scan_rows_payload

logger = get_logger()

# Database filename for the global index
GLOBAL_INDEX_DB_NAME = "global_index.db"

# SQLite supports at most SQLITE_MAX_VARIABLE_NUMBER bound parameters per query
# (compile-time default 999, raised to 32766 in SQLite ≥3.32).  Use a
# conservative chunk size so large scans never hit the limit regardless of
# the SQLite version in use.
_SQLITE_PARAM_CHUNK_SIZE = 900
_DEEP_OFFSET_LIMIT = 5_000
_DEEP_SEEK_CHUNK_SIZE = 1_024
_MAX_COLLECTION_ANCHORS_PER_QUERY = 64
_OMIT_METADATA_VALUE = object()

# Global singleton instance and lock for thread-safe access
_global_instance: Optional["AssetRepository"] = None
_global_lock = threading.Lock()


def _coerce_json_metadata_value(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return _OMIT_METADATA_VALUE
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, dict):
        sanitized: Dict[str, Any] = {}
        for key, item in value.items():
            sanitized_item = _coerce_json_metadata_value(item)
            if sanitized_item is _OMIT_METADATA_VALUE:
                continue
            sanitized[str(key)] = sanitized_item
        return sanitized
    if isinstance(value, (list, tuple)):
        sanitized_items = []
        for item in value:
            sanitized_item = _coerce_json_metadata_value(item)
            if sanitized_item is _OMIT_METADATA_VALUE:
                continue
            sanitized_items.append(sanitized_item)
        return sanitized_items

    try:
        json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return _OMIT_METADATA_VALUE
    return value


def _sanitize_metadata_for_json(metadata: Dict[str, Any]) -> Dict[str, Any]:
    sanitized = _coerce_json_metadata_value(metadata)
    if isinstance(sanitized, dict):
        return sanitized
    return {}


def _utc_ms() -> int:
    return int(time.time() * 1000)


def get_global_repository(library_root: Path) -> "AssetRepository":
    """Get or create the global AssetRepository singleton for a library.
    
    This function ensures there is only one database instance for the entire
    application lifecycle, regardless of which entry point triggers a scan.
    
    Args:
        library_root: The root directory of the library.
        
    Returns:
        The singleton AssetRepository instance.
    """
    global _global_instance
    
    with _global_lock:
        resolved_root = library_root.resolve()
        
        if _global_instance is not None:
            # Verify the instance matches the requested root
            if _global_instance.library_root.resolve() == resolved_root:
                return _global_instance
            # Different root requested - close old instance and create new one
            logger.info(
                "Switching global database from %s to %s",
                _global_instance.library_root,
                resolved_root,
            )
            _global_instance.close()
        
        _global_instance = AssetRepository(resolved_root)
        return _global_instance


def reset_global_repository() -> None:
    """Reset the global repository singleton.
    
    This is primarily used for testing to ensure clean state between tests.
    """
    global _global_instance
    
    with _global_lock:
        if _global_instance is not None:
            _global_instance.close()
            _global_instance = None


class AssetRepository:
    """High-level API for asset CRUD operations.
    
    This class implements the Single Write Gateway for the global database.
    All asset metadata operations (create, read, update, delete) must go
    through this repository to ensure data consistency.
    
    The repository uses idempotent write operations:
    - `append_rows`: Uses INSERT OR REPLACE (upsert) to avoid duplicates
    - `upsert_row`: Single-row upsert operation
    - Unique constraint on `rel` (file path) prevents duplicate entries
    
    Note: For the global database singleton, use `get_global_repository()`.
    """

    def __init__(self, library_root: Path):
        """Initialize the asset repository.
        
        Args:
            library_root: The root directory of the library. The global database
                will be created at `<library_root>/.iPhoto/global_index.db`.
        """
        self.library_root = library_root
        self.path = ensure_work_dir(library_root) / GLOBAL_INDEX_DB_NAME
        
        self._db_manager = DatabaseManager(self.path)
        self._conn: Optional[sqlite3.Connection] = None
        self._collection_anchor_cache: dict[
            CollectionQuery,
            dict[int, PageCursor | None],
        ] = {}
        self._collection_meta_cache: dict[CollectionQuery, tuple[int, int]] = {}
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

    def transaction(self, *, begin_mode: str | None = None):
        """Context manager for batching multiple operations.
        
        Example:
            >>> with repo.transaction():
            ...     repo.upsert_row("a.jpg", {...})
            ...     repo.upsert_row("b.jpg", {...})
        """
        return self._db_manager.transaction(begin_mode=begin_mode)

    def close(self) -> None:
        """Close any active database connections.
        
        This method should be called when the repository is no longer needed,
        particularly when resetting the global singleton.
        """
        self._db_manager.close()
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception as exc:
                # Best-effort close: do not propagate, but log for observability.
                logger.warning("Error while closing database connection: %s", exc)
            finally:
                self._conn = None

    def write_rows(self, rows: Iterable[Dict[str, Any]]) -> None:
        """Rewrite the entire index with *rows*."""
        with self.transaction() as conn:
            conn.execute("DELETE FROM assets")
            self._insert_rows(conn, rows)
        self._clear_collection_anchor_cache()

    def append_rows(self, rows: Iterable[Dict[str, Any]]) -> None:
        """Merge *rows* into the index, replacing duplicates by ``rel`` key."""
        with self.transaction() as conn:
            self._insert_rows(conn, rows)
        self._clear_collection_anchor_cache()

    def merge_scan_rows(self, rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Merge scanned rows while preserving persisted library-managed state.

        The SELECT for existing rows and the subsequent INSERT are performed
        within the same transaction so the read/write is atomic and cannot
        lose concurrent updates to ``face_status`` or other library-managed
        fields.

        Existing rows are fetched in chunks of at most
        ``_SQLITE_PARAM_CHUNK_SIZE`` to avoid hitting SQLite's bound-parameter
        limit when a large snapshot is passed (e.g. from
        ``index_sync_service.update_index_snapshot``).
        """

        materialized_rows = [dict(row) for row in rows]
        if not materialized_rows:
            return []

        # De-duplicate while preserving insertion order so each rel is queried
        # at most once.
        unique_rels: List[str] = list(
            dict.fromkeys(str(row["rel"]) for row in materialized_rows if row.get("rel"))
        )

        with self.transaction(begin_mode="IMMEDIATE") as conn:
            existing_rows_by_rel: Dict[str, Dict[str, Any]] = {}
            if unique_rels:
                conn.row_factory = sqlite3.Row
                for offset in range(0, len(unique_rels), _SQLITE_PARAM_CHUNK_SIZE):
                    chunk = unique_rels[offset : offset + _SQLITE_PARAM_CHUNK_SIZE]
                    placeholders = ", ".join(["?"] * len(chunk))
                    cursor = conn.execute(
                        f"SELECT * FROM assets WHERE rel IN ({placeholders})",
                        chunk,
                    )
                    for db_row in cursor:
                        d = self._db_row_to_dict(db_row)
                        rel_value = d.get("rel")
                        if rel_value is not None:
                            existing_rows_by_rel[str(rel_value)] = d
                conn.row_factory = None

            merged_rows = merge_scan_rows_payload(materialized_rows, existing_rows_by_rel)
            self._insert_rows(conn, merged_rows)

        self._clear_collection_anchor_cache()
        return merged_rows

    def upsert_row(self, rel: str, row: Dict[str, Any]) -> None:
        """Insert or update a single row identified by *rel*."""
        row_data = row.copy()
        row_data["rel"] = rel
        with self.transaction() as conn:
            self._insert_rows(conn, [row_data])
        self._clear_collection_anchor_cache()

    def remove_rows(self, rels: Iterable[str]) -> None:
        """Drop any index rows whose ``rel`` key matches *rels*."""
        removable = list(rels)
        if not removable:
            return

        placeholders = ", ".join(["?"] * len(removable))
        query = f"DELETE FROM assets WHERE rel IN ({placeholders})"
        self._db_manager.execute_in_transaction(query, removable)
        self._clear_collection_anchor_cache()

    def get_rows_by_rels(self, rels: Iterable[str]) -> Dict[str, Dict[str, Any]]:
        """Return a mapping of ``rel`` → row dict for the given *rels*.

        Missing keys are silently omitted.  This is used by :class:`MoveWorker`
        to cache source rows before deletion so that the destination index can
        reuse existing metadata instead of re-extracting it with ExifTool.
        """
        rels_list = list(rels)
        if not rels_list:
            return {}

        conn = self._db_manager.get_connection()
        should_close = conn != self._db_manager._conn

        try:
            conn.row_factory = sqlite3.Row
            placeholders = ", ".join(["?"] * len(rels_list))
            query = f"SELECT * FROM assets WHERE rel IN ({placeholders})"
            cursor = conn.cursor()
            cursor.execute(query, rels_list)
            result: Dict[str, Dict[str, Any]] = {}
            for row in cursor:
                d = self._db_row_to_dict(row)
                rel_value = d.get("rel")
                if rel_value is not None:
                    result[str(rel_value)] = d
            return result
        finally:
            if should_close:
                conn.close()

    def get_rows_by_ids(self, asset_ids: Iterable[str]) -> Dict[str, Dict[str, Any]]:
        """Return a mapping of ``asset.id`` to asset rows."""

        ids_list = [str(asset_id) for asset_id in asset_ids if asset_id]
        if not ids_list:
            return {}

        conn = self._db_manager.get_connection()
        should_close = conn != self._db_manager._conn

        try:
            conn.row_factory = sqlite3.Row
            placeholders = ", ".join(["?"] * len(ids_list))
            query = f"SELECT * FROM assets WHERE id IN ({placeholders})"
            cursor = conn.cursor()
            cursor.execute(query, ids_list)
            rows: Dict[str, Dict[str, Any]] = {}
            for row in cursor:
                data = self._db_row_to_dict(row)
                asset_id = data.get("id")
                if isinstance(asset_id, str) and asset_id and asset_id not in rows:
                    rows[asset_id] = data
            return rows
        finally:
            if should_close:
                conn.close()

    def read_rows_by_face_status(
        self,
        statuses: Iterable[str],
        *,
        limit: int | None = None,
    ) -> Iterator[Dict[str, Any]]:
        """Yield rows whose ``face_status`` matches one of *statuses*."""

        normalized_statuses = [
            status
            for status in (normalize_face_status(value) for value in statuses)
            if status is not None
        ]
        if not normalized_statuses:
            return

        conn = self._db_manager.get_connection()
        should_close = conn != self._db_manager._conn

        try:
            conn.row_factory = sqlite3.Row
            placeholders = ", ".join(["?"] * len(normalized_statuses))
            query = f"SELECT * FROM assets WHERE face_status IN ({placeholders}) ORDER BY dt DESC, id DESC"
            params: list[Any] = list(normalized_statuses)
            if limit is not None:
                query += " LIMIT ?"
                params.append(int(limit))
            cursor = conn.cursor()
            cursor.execute(query, params)
            for row in cursor:
                yield self._db_row_to_dict(row)
        finally:
            if should_close:
                conn.close()

    def update_face_status(self, asset_id: str, status: str) -> None:
        """Update the ``face_status`` for a single asset row."""

        normalized = normalize_face_status(status)
        if normalized is None or not asset_id:
            return
        self._db_manager.execute_in_transaction(
            "UPDATE assets SET face_status = ? WHERE id = ?",
            (normalized, asset_id),
        )

    def update_face_statuses(self, asset_ids: Iterable[str], status: str) -> None:
        """Update the ``face_status`` for multiple assets."""

        normalized = normalize_face_status(status)
        ids_list = [str(asset_id) for asset_id in asset_ids if asset_id]
        if normalized is None or not ids_list:
            return

        placeholders = ", ".join(["?"] * len(ids_list))
        query = f"UPDATE assets SET face_status = ? WHERE id IN ({placeholders})"
        self._db_manager.execute_in_transaction(query, [normalized, *ids_list])

    def count_by_face_status(self) -> Dict[str, int]:
        """Return a status-to-count mapping for ``assets.face_status``."""

        conn = self._db_manager.get_connection()
        should_close = conn != self._db_manager._conn

        try:
            cursor = conn.execute(
                "SELECT face_status, COUNT(*) AS asset_count FROM assets GROUP BY face_status"
            )
            counts: Dict[str, int] = {}
            for status, asset_count in cursor.fetchall():
                normalized = normalize_face_status(status)
                if normalized is None:
                    continue
                counts[normalized] = int(asset_count or 0)
            return counts
        finally:
            if should_close:
                conn.close()

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
        audit_full_scan_query(
            "read_all_full_scan",
            sort_by_date=sort_by_date,
            filter_hidden=filter_hidden,
        )
        started = monotonic_ms()
        conn = self._db_manager.get_connection()
        should_close = (conn != self._db_manager._conn)
        yielded = 0

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
                yielded += 1
                yield self._db_row_to_dict(row)
        finally:
            emit_perf_event(
                "repository_read_all",
                elapsed_ms=round(monotonic_ms() - started, 3),
                rows=yielded,
                sort_by_date=sort_by_date,
                filter_hidden=filter_hidden,
            )
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
        offset: int = 0,
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
            offset: Number of sorted rows to skip.
        
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
            offset=offset,
        )

        conn = self._db_manager.get_connection()
        should_close = (conn != self._db_manager._conn)
        started = monotonic_ms()

        try:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(query, params)

            results = []
            for row in cursor:
                results.append(self._db_row_to_dict(row))
            emit_perf_event(
                "asset_page_query",
                elapsed_ms=round(monotonic_ms() - started, 3),
                rows=len(results),
                limit=limit,
                offset=offset,
                album_path=album_path,
            )
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
            "dur", "year", "month", "dt", "ts", "sort_ts", "content_id", "bytes",
            "mime", "w", "h", "original_rel_path", "original_album_id",
            "original_album_subpath", "is_favorite", "location", "gps",
            "face_status", "thumbnail_state", "thumb_cache_key",
            "index_revision", "micro_thumbnail"
        ]

        logger.debug(
            "IndexStore.read_geometry_only album_path=%s include_subalbums=%s "
            "sort_by_date=%s filter_params=%s",
            album_path,
            include_subalbums,
            sort_by_date,
            filter_params,
        )

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
        started = monotonic_ms()
        yielded = 0

        try:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(query, params)
            for row in cursor:
                yielded += 1
                d = dict(row)
                # Parse GPS if present (stored as JSON string)
                if d.get("gps"):
                    try:
                        d["gps"] = json.loads(d["gps"])
                    except (json.JSONDecodeError, TypeError):
                        d["gps"] = None
                yield d
        finally:
            emit_perf_event(
                "read_geometry_only",
                elapsed_ms=round(monotonic_ms() - started, 3),
                rows=yielded,
                album_path=album_path,
            )
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
        started = monotonic_ms()
        yielded = 0

        try:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(query, params)

            for row in cursor:
                yielded += 1
                yield self._db_row_to_dict(row)
        finally:
            emit_perf_event(
                "read_album_assets",
                elapsed_ms=round(monotonic_ms() - started, 3),
                rows=yielded,
                album_path=album_path,
                include_subalbums=include_subalbums,
            )
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
        started = monotonic_ms()

        try:
            cursor = conn.execute(query, params)
            result = cursor.fetchone()
            count = result[0] if result else 0
            emit_perf_event(
                "repository_count",
                elapsed_ms=round(monotonic_ms() - started, 3),
                rows=count,
                album_path=album_path,
                include_subalbums=include_subalbums,
                filter_hidden=filter_hidden,
            )
            return count
        finally:
            if should_close:
                conn.close()

    def count_collection(self, query: CollectionQuery) -> int:
        """Return the number of visible rows matching a collection query."""

        sql, params = QueryBuilder.build_collection_query(
            query,
            select_clause="SELECT COUNT(*)",
            include_order=False,
        )
        started = monotonic_ms()
        conn = self._db_manager.get_connection()
        should_close = conn != self._db_manager._conn
        try:
            result = conn.execute(sql, params).fetchone()
            count = int(result[0] if result else 0)
            emit_perf_event(
                "collection_count_query",
                collection=query.collection_type.value,
                elapsed_ms=round(monotonic_ms() - started, 3),
                rows=count,
                query_plan=self._explain_query_plan(conn, sql, params),
            )
            return count
        finally:
            if should_close:
                conn.close()

    def create_scan_job(
        self,
        *,
        job_id: str,
        root: str,
        scope: str,
        status: str = "running",
        stage: str = "discover",
    ) -> None:
        """Create or replace scan job bookkeeping for an active scan."""

        now = _utc_ms()
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO scan_jobs (
                    job_id, root, scope, status, stage, found_count,
                    processed_count, visible_count, failed_count,
                    started_at, updated_at, finished_at
                )
                VALUES (?, ?, ?, ?, ?, 0, 0, 0, 0, ?, ?, NULL)
                """,
                [job_id, root, scope, status, stage, now, now],
            )

    def update_scan_job_stage(
        self,
        job_id: str,
        *,
        stage: str | None = None,
        status: str | None = None,
        found_count: int | None = None,
        processed_count: int | None = None,
        visible_count: int | None = None,
        failed_count: int | None = None,
        finished: bool = False,
    ) -> None:
        """Update scan job progress without requiring callers to know SQL."""

        assignments = ["updated_at = ?"]
        params: list[Any] = [_utc_ms()]
        for column, value in (
            ("stage", stage),
            ("status", status),
            ("found_count", found_count),
            ("processed_count", processed_count),
            ("visible_count", visible_count),
            ("failed_count", failed_count),
        ):
            if value is not None:
                assignments.append(f"{column} = ?")
                params.append(value)
        if finished:
            assignments.append("finished_at = ?")
            params.append(_utc_ms())
        params.append(job_id)
        with self.transaction() as conn:
            conn.execute(
                f"UPDATE scan_jobs SET {', '.join(assignments)} WHERE job_id = ?",
                params,
            )

    def append_scan_event(
        self,
        job_id: str,
        event_type: str,
        payload: Dict[str, Any] | None = None,
    ) -> None:
        """Append a compact scan event payload."""

        payload_json = json.dumps(
            _sanitize_metadata_for_json(payload or {}),
            ensure_ascii=False,
            sort_keys=True,
        )
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO scan_events (job_id, event_type, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                [job_id, event_type, payload_json, _utc_ms()],
            )

    def read_collection_page(
        self,
        query: CollectionQuery,
        cursor: PageCursor | None = None,
        limit: int = 100,
    ) -> PageResult:
        """Return one keyset-paginated collection page."""

        limit = max(0, int(limit))
        sql, params = QueryBuilder.build_collection_query(
            query,
            cursor=cursor,
            limit=limit,
        )
        started = monotonic_ms()
        conn = self._db_manager.get_connection()
        should_close = conn != self._db_manager._conn
        try:
            conn.row_factory = sqlite3.Row
            rows = [self._db_row_to_dict(row) for row in conn.execute(sql, params)]
            next_cursor = self._page_cursor_from_row(rows[-1]) if rows else None
            result = PageResult(
                rows=rows,
                next_cursor=next_cursor,
                total_count=self.count_collection(query),
                collection_revision=self._collection_revision(conn, query),
            )
            emit_perf_event(
                "collection_page_query",
                collection=query.collection_type.value,
                elapsed_ms=round(monotonic_ms() - started, 3),
                rows=len(rows),
                limit=limit,
                cursor="present" if cursor else "none",
                query_plan=self._explain_query_plan(conn, sql, params),
            )
            return result
        finally:
            if should_close:
                conn.close()

    def read_collection_window(
        self,
        query: CollectionQuery,
        first: int,
        limit: int,
    ) -> WindowResult:
        """Return a bounded window for a gallery collection."""

        first = max(0, int(first))
        limit = max(0, int(limit))

        started = monotonic_ms()
        conn = self._db_manager.get_connection()
        should_close = conn != self._db_manager._conn
        try:
            conn.row_factory = sqlite3.Row
            if first > _DEEP_OFFSET_LIMIT:
                sql, params = self._build_deep_collection_window_query(conn, query, first, limit)
                if sql is None:
                    rows: list[dict[str, Any]] = []
                else:
                    rows = [self._db_row_to_dict(row) for row in conn.execute(sql, params)]
            else:
                sql, params = QueryBuilder.build_collection_query(
                    query,
                    limit=limit,
                    offset=first,
                )
                rows = [self._db_row_to_dict(row) for row in conn.execute(sql, params)]
            self._remember_collection_window(query, first, rows)
            total_count, collection_revision = self._collection_count_and_revision(conn, query)
            result = WindowResult(
                first=first,
                rows=rows,
                total_count=total_count,
                collection_revision=collection_revision,
            )
            emit_perf_event(
                "collection_window_reload",
                collection=query.collection_type.value,
                elapsed_ms=round(monotonic_ms() - started, 3),
                first=first,
                limit=limit,
                rows=len(rows),
                total_count=result.total_count,
                query_plan=(
                    self._explain_query_plan(conn, sql, params)
                    if sql is not None
                    else "anchor_not_found"
                ),
            )
            return result
        finally:
            if should_close:
                conn.close()

    def _build_deep_collection_window_query(
        self,
        conn: sqlite3.Connection,
        query: CollectionQuery,
        first: int,
        limit: int,
    ) -> tuple[str | None, list[Any]]:
        if first <= 0:
            return QueryBuilder.build_collection_query(query, limit=limit)

        cursor = self._cursor_for_collection_offset(conn, query, first)
        if cursor is None and first > 0:
            return None, []

        return QueryBuilder.build_collection_query(query, cursor=cursor, limit=limit)

    def read_thumbnail_backfill_candidates(
        self,
        query: CollectionQuery,
        first: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Return stale thumbnail rows matching the same collection scope."""

        stale_query = CollectionQuery(
            collection_type=query.collection_type,
            album_path=query.album_path,
            include_subalbums=query.include_subalbums,
            media_types=query.media_types,
            is_favorite=query.is_favorite,
            has_gps=query.has_gps,
            date_from=query.date_from,
            date_to=query.date_to,
            search_text=query.search_text,
            sort_key=query.sort_key,
            sort_direction=query.sort_direction,
            min_thumbnail_state="stale",
        )
        sql, params = QueryBuilder.build_collection_query(
            stale_query,
            limit=max(0, int(limit)),
            offset=max(0, int(first)),
        )
        conn = self._db_manager.get_connection()
        should_close = conn != self._db_manager._conn
        try:
            conn.row_factory = sqlite3.Row
            return [self._db_row_to_dict(row) for row in conn.execute(sql, params)]
        finally:
            if should_close:
                conn.close()

    def update_thumbnail_ready(
        self,
        rel: str,
        *,
        micro_thumbnail: bytes | None = None,
        thumb_cache_key: str | None = None,
        error: str | None = None,
    ) -> None:
        """Update thumbnail readiness for a single asset row."""

        normalized_rel = unicodedata.normalize("NFC", str(rel))
        if error:
            self._db_manager.execute_in_transaction(
                """
                UPDATE assets
                SET thumbnail_state = 'failed',
                    thumb_error = ?,
                    thumb_updated_at = ?,
                    index_revision = COALESCE(index_revision, 0) + 1
                WHERE rel = ?
                """,
                [str(error), _utc_ms(), normalized_rel],
            )
        else:
            if not str(thumb_cache_key or "").strip():
                raise ValueError("ready thumbnails require thumb_cache_key")
            self._db_manager.execute_in_transaction(
                """
                UPDATE assets
                SET thumbnail_state = 'ready',
                    micro_thumbnail = COALESCE(?, micro_thumbnail),
                    thumb_cache_key = COALESCE(?, thumb_cache_key),
                    thumb_error = NULL,
                    thumb_updated_at = ?,
                    index_revision = COALESCE(index_revision, 0) + 1
                WHERE rel = ?
                """,
                [micro_thumbnail, thumb_cache_key, _utc_ms(), normalized_rel],
            )
        self._clear_collection_anchor_cache()

    def _cursor_for_collection_offset(
        self,
        conn: sqlite3.Connection,
        query: CollectionQuery,
        offset: int,
    ) -> PageCursor | None:
        """Return the keyset cursor immediately before *offset* without deep OFFSET."""

        if offset <= 0:
            return None

        anchors = self._collection_anchor_cache.setdefault(query, {0: None})
        anchor_index = max(index for index in anchors if index <= offset)
        cursor = anchors[anchor_index]

        while anchor_index < offset:
            step = min(_DEEP_SEEK_CHUNK_SIZE, offset - anchor_index)
            sql, params = QueryBuilder.build_collection_query(
                query,
                cursor=cursor,
                limit=step,
            )
            rows = conn.execute(sql, params).fetchall()
            if not rows:
                return None

            anchor_index += len(rows)
            cursor = self._page_cursor_from_row(dict(rows[-1]))
            anchors[anchor_index] = cursor
            self._prune_collection_anchor_cache(query)

            if len(rows) < step:
                return None

        return cursor

    def find_row_by_path(self, query: CollectionQuery, path: Path) -> int | None:
        """Return a path's row number within *query* without scanning rows in Python."""

        rel = self._library_relative_path(path)
        row = self.get_rows_by_rels([rel]).get(rel)
        if row is None:
            return None
        asset_id = str(row.get("id") or "")
        sort_ts = row.get("sort_ts") if row.get("sort_ts") is not None else row.get("ts")
        if not asset_id or sort_ts is None:
            return None

        match_sql, match_params = QueryBuilder.build_collection_query(
            query,
            select_clause="SELECT COUNT(*)",
            include_order=False,
        )
        match_sql += " AND rel = ?" if " WHERE " in match_sql else " WHERE rel = ?"
        match_params.append(rel)

        conn = self._db_manager.get_connection()
        should_close = conn != self._db_manager._conn
        try:
            matched = conn.execute(match_sql, match_params).fetchone()
            if not matched or int(matched[0] or 0) == 0:
                return None

            before_where, before_params = QueryBuilder.build_collection_where(query)
            if query.sort_direction.value == "ASC":
                before_where.append("(sort_ts < ? OR (sort_ts = ? AND id < ?))")
            else:
                before_where.append("(sort_ts > ? OR (sort_ts = ? AND id > ?))")
            before_params.extend([sort_ts, sort_ts, asset_id])
            before_sql = "SELECT COUNT(*) FROM assets WHERE " + " AND ".join(before_where)
            before = conn.execute(before_sql, before_params).fetchone()
            return int(before[0] if before else 0)
        finally:
            if should_close:
                conn.close()

    def find_live_partner(self, asset_id: str) -> Dict[str, Any] | None:
        """Return the row for an asset's Live Photo partner, if indexed."""

        conn = self._db_manager.get_connection()
        should_close = conn != self._db_manager._conn
        try:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT partner.*
                FROM assets AS source
                JOIN assets AS partner ON partner.rel = source.live_partner_rel
                WHERE source.id = ?
                LIMIT 1
                """,
                (asset_id,),
            ).fetchone()
            return self._db_row_to_dict(row) if row is not None else None
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
        self._clear_collection_anchor_cache()

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
        self._clear_collection_anchor_cache()

    def update_location(self, rel: str, location: str) -> None:
        """Update the location string for a single asset."""
        self._db_manager.execute_in_transaction(
            "UPDATE assets SET location = ? WHERE rel = ?",
            (location, rel),
        )

    def update_asset_geodata(
        self,
        rel: str,
        *,
        gps: Dict[str, float] | None,
        location: str | None,
        metadata_updates: Dict[str, Any] | None = None,
    ) -> None:
        """Atomically update GPS/location columns and JSON metadata for one asset."""

        gps_payload = json.dumps(gps) if gps is not None else None
        with self.transaction() as conn:
            update_parts = ["gps = ?", "location = ?"]
            params: list[Any] = [gps_payload, location]

            columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(assets)")
            }
            if "metadata" in columns:
                existing_metadata: Dict[str, Any] = {}
                row = conn.execute(
                    "SELECT metadata FROM assets WHERE rel = ?",
                    (rel,),
                ).fetchone()
                if row is not None and row[0]:
                    try:
                        decoded = json.loads(row[0])
                    except (json.JSONDecodeError, TypeError):
                        decoded = {}
                    if isinstance(decoded, dict):
                        existing_metadata = decoded
                if metadata_updates:
                    existing_metadata.update(
                        _sanitize_metadata_for_json(
                            {
                                key: value
                                for key, value in metadata_updates.items()
                                if value is not None
                            }
                        )
                    )
                if gps is not None:
                    existing_metadata["gps"] = dict(gps)
                else:
                    existing_metadata.pop("gps", None)
                if isinstance(location, str) and location.strip():
                    existing_metadata["location"] = location.strip()
                else:
                    existing_metadata.pop("location", None)
                update_parts.append("metadata = ?")
                params.append(
                    json.dumps(
                        _sanitize_metadata_for_json(existing_metadata),
                        ensure_ascii=False,
                    )
                )

            params.append(rel)
            conn.execute(
                f"UPDATE assets SET {', '.join(update_parts)} WHERE rel = ?",
                params,
            )
        self._clear_collection_anchor_cache()

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
            self._clear_collection_anchor_cache()
            return

        with self.transaction() as conn:
            conn.execute("UPDATE assets SET live_role = 0, live_partner_rel = NULL")
            query = "UPDATE assets SET live_role = ?, live_partner_rel = ? WHERE rel = ?"
            params = [(role, partner, rel) for rel, role, partner in updates]
            conn.executemany(query, params)
        self._clear_collection_anchor_cache()

    def apply_live_role_updates_for_prefix(
        self,
        prefix: str,
        updates: List[Tuple[str, int, Optional[str]]],
    ) -> None:
        """Update live_role/live_partner_rel for assets under *prefix* only."""
        prefix = prefix.rstrip("/")
        prefix_like = f"{prefix}/%"
        with self.transaction() as conn:
            conn.execute(
                "UPDATE assets SET live_role = 0, live_partner_rel = NULL "
                "WHERE rel LIKE ?",
                (prefix_like,),
            )
            query = "UPDATE assets SET live_role = ?, live_partner_rel = ? WHERE rel = ?"
            params = [(role, partner, rel) for rel, role, partner in updates]
            conn.executemany(query, params)
        self._clear_collection_anchor_cache()

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

    def _collection_revision(
        self,
        conn: sqlite3.Connection,
        query: CollectionQuery,
    ) -> int:
        sql, params = QueryBuilder.build_collection_query(
            query,
            select_clause="SELECT COALESCE(MAX(index_revision), 0)",
            include_order=False,
        )
        row = conn.execute(sql, params).fetchone()
        return int(row[0] if row else 0)

    def _collection_count_and_revision(
        self,
        conn: sqlite3.Connection,
        query: CollectionQuery,
    ) -> tuple[int, int]:
        cached = self._collection_meta_cache.get(query)
        if cached is not None:
            return cached
        count = self.count_collection(query)
        revision = self._collection_revision(conn, query)
        self._collection_meta_cache[query] = (count, revision)
        return count, revision

    def _explain_query_plan(
        self,
        conn: sqlite3.Connection,
        query: str,
        params: list[Any],
    ) -> str | None:
        if not explain_enabled():
            return None
        try:
            rows = conn.execute(f"EXPLAIN QUERY PLAN {query}", params).fetchall()
        except sqlite3.Error as exc:
            return f"explain_failed:{exc}"
        return " | ".join(" ".join(str(part) for part in row) for row in rows)

    def _remember_collection_window(
        self,
        query: CollectionQuery,
        first: int,
        rows: list[dict[str, Any]],
    ) -> None:
        if not rows:
            return
        anchors = self._collection_anchor_cache.setdefault(query, {0: None})
        if first <= 0:
            anchors[0] = None
        cursor = self._page_cursor_from_row(rows[-1])
        if cursor is not None:
            anchors[max(0, first) + len(rows)] = cursor
            self._prune_collection_anchor_cache(query)

    def _prune_collection_anchor_cache(self, query: CollectionQuery) -> None:
        anchors = self._collection_anchor_cache.get(query)
        if not anchors or len(anchors) <= _MAX_COLLECTION_ANCHORS_PER_QUERY:
            return
        overflow = len(anchors) - _MAX_COLLECTION_ANCHORS_PER_QUERY
        largest = max(anchors)
        candidates = [index for index in sorted(anchors) if index not in {0, largest}]
        for index in candidates[:overflow]:
            anchors.pop(index, None)

    def _clear_collection_anchor_cache(self) -> None:
        self._collection_anchor_cache.clear()
        self._collection_meta_cache.clear()

    @staticmethod
    def _page_cursor_from_row(row: dict[str, Any]) -> PageCursor | None:
        asset_id = row.get("id")
        sort_ts = row.get("sort_ts") if row.get("sort_ts") is not None else row.get("ts")
        if asset_id is None or sort_ts is None:
            return None
        return PageCursor(sort_ts=int(sort_ts), asset_id=str(asset_id))

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
                return candidate.as_posix()

    # Helper methods delegating to standalone functions in row_mapper
    def _insert_rows(
        self,
        conn: sqlite3.Connection,
        rows: Iterable[Dict[str, Any]],
    ) -> None:
        """Helper to bulk insert rows."""
        insert_rows(conn, rows)

    def _row_to_db_params(self, row: Dict[str, Any]) -> List[Any]:
        """Map a dictionary row to a list of values for the DB."""
        return row_to_db_params(row)

    def _db_row_to_dict(self, db_row: sqlite3.Row) -> Dict[str, Any]:
        """Map a DB row back to a dictionary."""
        return db_row_to_dict(db_row)

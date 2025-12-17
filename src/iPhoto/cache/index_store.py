"""
Persistent storage for album index rows.

This module provides read/write access to the global asset index. The index is
stored in an SQLite database (`global_index.db`) located at the library root
under `.iphoto/`. This centralized architecture replaces the previous per-album
`index.db` design, enabling:

1. **Flat Storage with Path Indexing**: A single `assets` table stores all media
   files with their library-relative paths (`rel` column) and parent album
   paths (`parent_album_path`) for hierarchical queries.

2. **K-Way Merge via SQLite**: Composite indexes on `(parent_album_path, dt, id)`
   allow the database engine to perform efficient sorted retrieval across albums.

3. **Cursor-Based Pagination**: Seek pagination using `(dt, id) < (?, ?)` avoids
   the O(N) cost of OFFSET-based pagination for large datasets.

The `IndexStore` class manages the creation, reading, updating, and deletion
of asset records in the SQLite database.
"""
from __future__ import annotations

import json
import sqlite3
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterable, Iterator, Optional, Any, List, Tuple

from ..config import WORK_DIR_NAME
from ..utils.logging import get_logger

logger = get_logger()

# Database filename for the global index
GLOBAL_INDEX_DB_NAME = "global_index.db"


def normalize_path(path_str: str) -> str:
    """Normalize a path string to use forward slashes (POSIX style).

    This ensures consistent path representation across Windows/Mac/Linux.
    """
    return Path(path_str).as_posix()


def escape_like_pattern(path: str) -> str:
    """Escape special characters in a path for use in SQL LIKE patterns.

    SQLite's LIKE operator treats '%' and '_' as wildcards. This function
    escapes those characters (and backslashes) so they match literally when
    used with 'ESCAPE \\'.

    Args:
        path: The path string to escape.

    Returns:
        The escaped path suitable for use in a LIKE pattern.
    """
    return path.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class IndexStore:
    """Read/write helper for the global ``global_index.db`` SQLite database.

    The global index stores all assets across the entire library in a single
    database, using ``rel`` (library-relative path) as the primary key
    and ``parent_album_path`` for album-based queries.

    .. note::
       Instances of this class are not thread-safe. Each thread should create
       its own instance to avoid race conditions on the shared transaction connection.
    """

    def __init__(self, album_root: Path, use_global_index: bool = True):
        """Initialize the IndexStore.

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
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    # Whitelist of allowed filter modes to prevent injection and logic errors
    _VALID_FILTER_MODES = frozenset({"videos", "live", "favorites"})

    def _init_db(self) -> None:
        """Initialize the database schema."""
        try:
            # Use a transient connection for initialization
            with sqlite3.connect(self.path, timeout=10.0) as conn:
                self._run_init_sql(conn)
        except sqlite3.DatabaseError as exc:
            logger.warning("Detected index.db corruption at %s: %s", self.path, exc)
            self._recover_database()

    def _run_init_sql(self, conn: sqlite3.Connection) -> None:
        """Create or migrate the assets table and indices."""
        # Enable Write-Ahead Logging for concurrency and performance
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
        except sqlite3.OperationalError:
            pass
        conn.execute("PRAGMA synchronous=NORMAL;")

        # Create the assets table with support for global library indexing.
        # Key columns:
        # - rel: Library-relative path (primary key, e.g., "2023/Trip/img.jpg")
        # - parent_album_path: Parent directory path prefix for album queries
        #   (e.g., "2023/Trip" for "2023/Trip/img.jpg")
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
                micro_thumbnail BLOB
            )
        """)

        # Check if columns exist and add them if not (migration)
        cursor = conn.execute("PRAGMA table_info(assets)")
        columns = {row[1] for row in cursor}

        if "micro_thumbnail" not in columns:
            conn.execute("ALTER TABLE assets ADD COLUMN micro_thumbnail BLOB")

        if "live_role" not in columns:
            conn.execute("ALTER TABLE assets ADD COLUMN live_role INTEGER DEFAULT 0")
        if "live_partner_rel" not in columns:
            conn.execute("ALTER TABLE assets ADD COLUMN live_partner_rel TEXT")
        if "aspect_ratio" not in columns:
            conn.execute("ALTER TABLE assets ADD COLUMN aspect_ratio REAL")
        if "year" not in columns:
            conn.execute("ALTER TABLE assets ADD COLUMN year INTEGER")
        if "month" not in columns:
            conn.execute("ALTER TABLE assets ADD COLUMN month INTEGER")
        if "media_type" not in columns:
            conn.execute("ALTER TABLE assets ADD COLUMN media_type INTEGER")
        if "is_favorite" not in columns:
            conn.execute("ALTER TABLE assets ADD COLUMN is_favorite INTEGER DEFAULT 0")
        if "location" not in columns:
            conn.execute("ALTER TABLE assets ADD COLUMN location TEXT")
        # New column for global index: parent album path
        if "parent_album_path" not in columns:
            conn.execute("ALTER TABLE assets ADD COLUMN parent_album_path TEXT")

        # Create indices for common sort/filter operations if needed.
        # 'dt' is used for sorting.
        conn.execute("CREATE INDEX IF NOT EXISTS idx_dt ON assets (dt)")
        # Index for optimized favorites retrieval
        conn.execute("CREATE INDEX IF NOT EXISTS idx_assets_favorite_dt ON assets (is_favorite, dt DESC)")
        # Add specific index for descending sort on dt to optimize streaming query
        # We use a composite index on dt and id to match the ORDER BY clause for optimal streaming.
        conn.execute("CREATE INDEX IF NOT EXISTS idx_assets_dt_id_desc ON assets (dt DESC, id DESC)")
        # Index for timeline grouping (Year/Month headers)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_year_month ON assets(year, month)")
        # Index for timeline optimization (year DESC, month DESC, dt DESC)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_timeline_optimization ON assets(year DESC, month DESC, dt DESC)")
        # Index for media type filtering (Photos/Videos)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_media_type ON assets(media_type)")

        # --- New indexes for K-Way Merge and Cursor Pagination ---
        # Core index for album-scoped pagination (covers album views with date sorting)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_assets_pagination "
            "ON assets (parent_album_path, dt DESC, id DESC)"
        )
        # Global view index (all photos sorted by date, no album filter)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_assets_global_sort "
            "ON assets (dt DESC, id DESC)"
        )
        # Index for album prefix queries (for sub-album filtering with LIKE)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_parent_album_path "
            "ON assets (parent_album_path)"
        )

    def _recover_database(self) -> None:
        """Attempt graded recovery from a corrupted database."""
        # Level 1: REINDEX
        try:
            logger.info("Attempting REINDEX for %s", self.path)
            with sqlite3.connect(self.path, timeout=10.0) as conn:
                conn.execute("REINDEX;")
                self._run_init_sql(conn)
            logger.info("REINDEX succeeded for %s", self.path)
            return
        except sqlite3.DatabaseError as exc:
            logger.warning("REINDEX failed for %s: %s", self.path, exc)

        # Level 2: Salvage readable rows
        salvaged_rows: List[Dict[str, Any]] = []
        try:
            with sqlite3.connect(self.path, timeout=10.0) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("SELECT * FROM assets")
                try:
                    for row in cursor:
                        try:
                            row_dict = self._db_row_to_dict(row)
                            if not row_dict.get("rel"):
                                logger.warning(
                                    "Skipping salvaged row missing required 'rel' field: %s",
                                    row_dict,
                                )
                                continue
                            salvaged_rows.append(row_dict)
                        except sqlite3.DatabaseError as row_exc:
                            logger.warning("Skipping corrupted row during salvage: %s", row_exc)
                except sqlite3.DatabaseError as scan_exc:
                    logger.warning("Encountered error while scanning for salvage: %s", scan_exc)
        except sqlite3.DatabaseError as exc:
            logger.warning("Failed to open corrupted DB for salvage: %s", exc)

        if salvaged_rows:
            logger.info("Salvaged %d rows from corrupted database", len(salvaged_rows))
        else:
            logger.info("No rows salvaged; rebuilding fresh database")

        # Level 3: Force reset and restore salvaged rows
        self._force_reset_db()

        try:
            with sqlite3.connect(self.path, timeout=10.0) as conn:
                self._run_init_sql(conn)
                if salvaged_rows:
                    self._insert_rows(conn, salvaged_rows)
            logger.info("Rebuilt index database at %s", self.path)
        except sqlite3.DatabaseError as exc:
            logger.error("Failed to rebuild index database at %s: %s", self.path, exc)
            raise

    def _force_reset_db(self) -> None:
        """Close any open connection and delete database files."""
        if self._conn:
            try:
                self._conn.close()
            finally:
                self._conn = None

        paths = [
            self.path,
            Path(str(self.path) + "-wal"),
            Path(str(self.path) + "-shm"),
        ]
        for p in paths:
            try:
                if p.exists():
                    p.unlink()
                    logger.info("Deleted corrupted database file %s", p)
            except OSError as exc:
                logger.warning("Failed to delete %s: %s", p, exc)

    def _get_conn(self) -> sqlite3.Connection:
        """Return the active connection or create a new one."""
        if self._conn:
            return self._conn
        return sqlite3.connect(self.path)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Batch multiple updates into a single transaction.

        .. note::
           Nested transactions are not supported via savepoints. If this context
           manager is entered recursively (or while a connection is already active),
           the inner block is effectively flattened into the outer transaction.
           Operations in the inner block will only be committed when the outermost
           transaction exits successfully.
        """
        if self._conn:
            # Nested transaction (conceptually), just yield.
            yield
            return

        self._conn = sqlite3.connect(self.path)
        try:
            with self._conn:
                yield
        finally:
            self._conn.close()
            self._conn = None

    def write_rows(self, rows: Iterable[Dict[str, Any]]) -> None:
        """Rewrite the entire index with *rows*."""
        conn = self._get_conn()
        is_nested = (conn == self._conn)

        try:
            if is_nested:
                # We are in a transaction, do not use `with conn:` as it would commit early.
                conn.execute("DELETE FROM assets")
                self._insert_rows(conn, rows)
            else:
                with conn:
                    conn.execute("DELETE FROM assets")
                    self._insert_rows(conn, rows)
        finally:
            if not is_nested:
                conn.close()

    def _insert_rows(self, conn: sqlite3.Connection, rows: Iterable[Dict[str, Any]]) -> None:
        """Helper to bulk insert rows."""
        # Prepare data
        data_list = []
        for row in rows:
            data = self._row_to_db_params(row)
            data_list.append(data)

        if not data_list:
            return

        columns = [
            "rel", "id", "parent_album_path", "dt", "ts", "bytes", "mime", "make", "model", "lens",
            "iso", "f_number", "exposure_time", "exposure_compensation", "focal_length",
            "w", "h", "gps", "content_id", "frame_rate", "codec",
            "still_image_time", "dur", "original_rel_path",
            "original_album_id", "original_album_subpath",
            "live_role", "live_partner_rel",
            "aspect_ratio", "year", "month", "media_type", "is_favorite",
            "location", "micro_thumbnail"
        ]
        placeholders = ", ".join(["?"] * len(columns))
        query = f"INSERT OR REPLACE INTO assets ({', '.join(columns)}) VALUES ({placeholders})"

        conn.executemany(query, data_list)

    def _row_to_db_params(self, row: Dict[str, Any]) -> List[Any]:
        """Map a dictionary row to a list of values for the DB."""
        gps_val = row.get("gps")
        gps_str = json.dumps(gps_val) if gps_val is not None else None

        # Compute parent_album_path from rel if not provided
        rel = row.get("rel")
        parent_album_path = row.get("parent_album_path")
        if parent_album_path is None and rel:
            # Extract the parent directory from the relative path
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

    def read_all(self, sort_by_date: bool = False, filter_hidden: bool = False) -> Iterator[Dict[str, Any]]:
        """Yield all rows from the index.

        :param sort_by_date: If True, order results by 'dt' descending (newest first).
        :param filter_hidden: If True, exclude hidden assets (e.g. motion components).
        """
        conn = self._get_conn()
        should_close = (conn != self._conn)

        try:
            query = "SELECT * FROM assets"
            where_clauses = []

            if filter_hidden:
                where_clauses.append("live_role = 0")

            if where_clauses:
                query += " WHERE " + " AND ".join(where_clauses)

            if sort_by_date:
                # Optimized sort for streaming:
                # 1. 'dt DESC' puts newest items first.
                # 2. 'NULLS LAST' explicitly ensures items without dates appear at the end.
                #    (Note: SQLite's default for DESC is NULLS FIRST, so 'NULLS LAST' is necessary to achieve the desired order.)
                # 3. 'id DESC' ensures deterministic order for items with same timestamp.
                query += " ORDER BY dt DESC NULLS LAST, id DESC"

            # Set the row factory on the connection before creating the cursor
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(query)
            for row in cursor:
                yield self._db_row_to_dict(row)
        finally:
            if should_close:
                conn.close()

    def _build_filter_clauses(self, filter_params: Optional[Dict[str, Any]]) -> Tuple[List[str], List[Any]]:
        """Helper to build WHERE clauses and parameters from filter params."""
        where_clauses: List[str] = []
        params: List[Any] = []

        if filter_params:
            if "media_type" in filter_params:
                media_type = filter_params["media_type"]
                if not isinstance(media_type, int):
                    raise ValueError(f"Invalid media_type: {media_type} (expected int)")
                where_clauses.append("media_type = ?")
                params.append(media_type)

            if "filter_mode" in filter_params:
                mode = filter_params["filter_mode"]
                # Strict whitelist check for filter mode
                if mode in self._VALID_FILTER_MODES:
                    if mode == "videos":
                        where_clauses.append("media_type = 1")
                    elif mode == "live":
                        where_clauses.append("live_partner_rel IS NOT NULL")
                    elif mode == "favorites":
                        where_clauses.append("is_favorite = 1")
                else:
                    raise ValueError(f"Invalid filter_mode: {mode}")

        return where_clauses, params

    def set_favorite_status(self, rel: str, is_favorite: bool) -> None:
        """Toggle the favorite status for a single asset efficiently."""
        val = 1 if is_favorite else 0
        conn = self._get_conn()
        is_nested = (conn == self._conn)
        try:
            if is_nested:
                conn.execute("UPDATE assets SET is_favorite = ? WHERE rel = ?", (val, rel))
            else:
                with conn:
                    conn.execute("UPDATE assets SET is_favorite = ? WHERE rel = ?", (val, rel))
        finally:
            if not is_nested:
                conn.close()

    def sync_favorites(self, featured_rels: Iterable[str]) -> None:
        """Synchronise the DB 'is_favorite' column with the provided list of featured paths."""
        # Convert to list to avoid consuming iterator multiple times
        featured_rels_list = list(featured_rels)
        
        # Normalize input paths to ensure consistent comparison (NFC)
        # Build mapping from normalized to original input strings
        input_normalized_map = {unicodedata.normalize("NFC", r): r for r in featured_rels_list}
        featured_normalized_set = set(input_normalized_map.keys())

        conn = self._get_conn()
        is_nested = (conn == self._conn)

        def _perform_sync(c: sqlite3.Connection) -> None:
            # 1. Fetch all rels from the DB to build a normalized-to-original mapping.
            #    This ensures we always update the exact key present in the database,
            #    even if the database contains paths with different Unicode normalization.
            cursor = c.execute("SELECT rel FROM assets")
            all_rels_map = {unicodedata.normalize("NFC", row[0]): row[0] for row in cursor}

            # 2. Fetch currently marked favorites from the DB to calculate the diff.
            current_favs_normalized = {
                unicodedata.normalize("NFC", row[0])
                for row in c.execute("SELECT rel FROM assets WHERE is_favorite != 0")
            }

            # 3. Determine which rows actually need updates
            # Items in DB (normalized) but not in input list -> Remove
            to_remove_normalized = current_favs_normalized - featured_normalized_set

            # Items in input list but not in DB (normalized) -> Add
            to_add_normalized = featured_normalized_set - current_favs_normalized

            # 4. Apply updates only where necessary
            if to_remove_normalized:
                # Use the ORIGINAL keys from the DB to ensure the UPDATE succeeds
                to_remove_original = [all_rels_map[n] for n in to_remove_normalized if n in all_rels_map]
                c.executemany("UPDATE assets SET is_favorite = 0 WHERE rel = ?", [(r,) for r in to_remove_original])

            if to_add_normalized:
                # Use the ORIGINAL keys from the DB to ensure the UPDATE succeeds
                # Fall back to input keys if the normalized key is not found in DB
                to_add_original = [
                    all_rels_map.get(n, input_normalized_map[n]) 
                    for n in to_add_normalized
                ]
                c.executemany("UPDATE assets SET is_favorite = 1 WHERE rel = ?", [(r,) for r in to_add_original])

        try:
            if is_nested:
                _perform_sync(conn)
            else:
                with conn:
                    _perform_sync(conn)
        finally:
            if not is_nested:
                conn.close()

    def read_geometry_only(
        self,
        filter_params: Optional[Dict[str, Any]] = None,
        sort_by_date: bool = True,
        album_path: Optional[str] = None,
        include_subalbums: bool = True,
    ) -> Iterator[Dict[str, Any]]:
        """Yield lightweight asset rows (geometry & core metadata) for fast grid layout.

        Fetches only the columns strictly required for:
        1. Calculating the grid layout (id, aspect_ratio).
        2. Drawing section headers (year, month).
        3. Identifying media type & badges (media_type, live_partner_rel, dur).
        4. Sorting (dt, ts).
        5. Location display (location) and fallback resolution (gps).

        :param filter_params: Optional dictionary of SQL filter criteria.
                              Supported keys:
                                - 'media_type' (int): Filter by media type.
                                - 'filter_mode' (str): Filter mode, accepts "videos", "live", or "favorites".
        :param sort_by_date: If True, sort results by date descending.
        :param album_path: If provided, filter to assets in this album path.
        :param include_subalbums: If True, include assets from sub-albums (default True).
        """
        conn = self._get_conn()
        should_close = (conn != self._conn)

        try:
            # Columns needed for the lightweight "viewport-first" loading strategy
            columns = [
                "id",
                "rel",
                "aspect_ratio",
                "media_type",
                "live_partner_rel",
                "dur",
                "year",
                "month",
                "dt",
                "ts",
                "content_id",  # needed for live photo pairing logic if needed
                "bytes",  # needed for panorama detection logic
                "mime",  # needed for classifier
                "w",  # needed for panorama detection logic
                "h",  # needed for panorama detection logic
                "original_rel_path",  # needed for trash restore logic
                "original_album_id",
                "original_album_subpath",
                "is_favorite",
                "location",
                "gps",
                "micro_thumbnail"
            ]
            query = f"SELECT {', '.join(columns)} FROM assets"

            # Always filter hidden assets (live photo components) in grid view
            base_where = ["live_role = 0"]
            params: List[Any] = []

            # Album path filtering
            if album_path is not None:
                if include_subalbums:
                    # Match exact album or any sub-album
                    base_where.append(
                        "(parent_album_path = ? OR parent_album_path LIKE ? ESCAPE '\\')"
                    )
                    params.append(album_path)
                    escaped_path = escape_like_pattern(album_path)
                    params.append(f"{escaped_path}/%")
                else:
                    base_where.append("parent_album_path = ?")
                    params.append(album_path)

            filter_where, filter_params_list = self._build_filter_clauses(filter_params)
            params.extend(filter_params_list)
            where_clauses = base_where + filter_where

            if where_clauses:
                query += " WHERE " + " AND ".join(where_clauses)

            if sort_by_date:
                query += " ORDER BY dt DESC NULLS LAST, id DESC"

            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(query, params)
            for row in cursor:
                # We return a dict, but one that is much lighter than the full row
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

    def read_geotagged(self) -> Iterator[Dict[str, Any]]:
        """Yield only rows that contain GPS metadata."""
        conn = self._get_conn()
        should_close = (conn != self._conn)

        try:
            # We filter for gps IS NOT NULL at the database level
            query = "SELECT * FROM assets WHERE gps IS NOT NULL"

            # Set the row factory on the connection before creating the cursor
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(query)
            for row in cursor:
                yield self._db_row_to_dict(row)
        finally:
            if should_close:
                conn.close()

    def upsert_row(self, rel: str, row: Dict[str, Any]) -> None:
        """Insert or update a single row identified by *rel*."""
        conn = self._get_conn()
        is_nested = (conn == self._conn)

        try:
            # Ensure rel is in the row data
            row_data = row.copy()
            row_data["rel"] = rel

            if is_nested:
                self._insert_rows(conn, [row_data])
            else:
                with conn:
                    self._insert_rows(conn, [row_data])
        finally:
            if not is_nested:
                conn.close()

    def remove_rows(self, rels: Iterable[str]) -> None:
        """Drop any index rows whose ``rel`` key matches *rels*."""
        removable = list(rels)
        if not removable:
            return

        conn = self._get_conn()
        is_nested = (conn == self._conn)

        try:
            placeholders = ", ".join(["?"] * len(removable))
            query = f"DELETE FROM assets WHERE rel IN ({placeholders})"

            if is_nested:
                conn.execute(query, removable)
            else:
                with conn:
                    conn.execute(query, removable)
        finally:
            if not is_nested:
                conn.close()

    def append_rows(self, rows: Iterable[Dict[str, Any]]) -> None:
        """Merge *rows* into the index, replacing duplicates by ``rel`` key."""
        conn = self._get_conn()
        is_nested = (conn == self._conn)

        try:
            if is_nested:
                self._insert_rows(conn, rows)
            else:
                with conn:
                    self._insert_rows(conn, rows)
        finally:
            if not is_nested:
                conn.close()

    def update_location(self, rel: str, location: str) -> None:
        """Update the location string for a single asset."""
        conn = self._get_conn()
        is_nested = (conn == self._conn)
        try:
            if is_nested:
                conn.execute("UPDATE assets SET location = ? WHERE rel = ?", (location, rel))
            else:
                with conn:
                    conn.execute("UPDATE assets SET location = ? WHERE rel = ?", (location, rel))
        finally:
            if not is_nested:
                conn.close()

    def count(self, filter_hidden: bool = False, filter_params: Optional[Dict[str, Any]] = None) -> int:
        """
        Return the total number of assets in the index, optionally filtered by criteria.

        Parameters
        ----------
        filter_hidden : bool, optional
            If True, exclude assets with ``live_role != 0`` (i.e., hidden assets).
        filter_params : dict, optional
            Dictionary of filter criteria to apply. Supported keys include:
                - 'filter_mode': Filter by asset mode (e.g., 'photo', 'video').
                - 'media_type': Filter by media type (e.g., 'image', 'movie').
                - Additional keys may be supported as defined in `_build_filter_clauses`.
            These filters restrict the count to assets matching the specified criteria.

        Returns
        -------
        int
            The number of assets in the index matching the given filters.
        """
        conn = self._get_conn()
        should_close = (conn != self._conn)

        try:
            query = "SELECT COUNT(*) FROM assets"

            base_where = []
            if filter_hidden:
                base_where.append("live_role = 0")

            filter_where, params = self._build_filter_clauses(filter_params)
            where_clauses = base_where + filter_where

            if where_clauses:
                query += " WHERE " + " AND ".join(where_clauses)

            cursor = conn.execute(query, params)
            result = cursor.fetchone()
            return result[0] if result else 0
        finally:
            if should_close:
                conn.close()

    def apply_live_role_updates(self, updates: List[Tuple[str, int, Optional[str]]]) -> None:
        """Update live_role and live_partner_rel for a batch of assets.

        This method first resets live_role and live_partner_rel for all assets
        to ensure consistency (unpaired items revert to role 0), then applies
        the specific updates.

        :param updates: List of (rel, live_role, live_partner_rel) tuples.
        """
        if not updates:
            # Just reset everything if no updates
            conn = self._get_conn()
            is_nested = (conn == self._conn)
            try:
                if is_nested:
                    conn.execute("UPDATE assets SET live_role = 0, live_partner_rel = NULL")
                else:
                    with conn:
                        conn.execute("UPDATE assets SET live_role = 0, live_partner_rel = NULL")
            finally:
                if not is_nested:
                    conn.close()
            return

        conn = self._get_conn()
        is_nested = (conn == self._conn)

        try:
            query = "UPDATE assets SET live_role = ?, live_partner_rel = ? WHERE rel = ?"
            # We arrange params as (live_role, live_partner_rel, rel)
            # Input is (rel, live_role, live_partner_rel)
            params = [(role, partner, rel) for rel, role, partner in updates]

            if is_nested:
                conn.execute("UPDATE assets SET live_role = 0, live_partner_rel = NULL")
                conn.executemany(query, params)
            else:
                with conn:
                    conn.execute("UPDATE assets SET live_role = 0, live_partner_rel = NULL")
                    conn.executemany(query, params)
        finally:
            if not is_nested:
                conn.close()

    # -------------------------------------------------------------------------
    # Cursor-Based Pagination (Seek Pagination)
    # -------------------------------------------------------------------------

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
        retrieval of large datasets. Instead of using OFFSET, it uses the
        `(dt, id) < (?, ?)` pattern to skip directly to the next page.

        Args:
            cursor_dt: The timestamp of the last item from the previous page.
                If None, starts from the beginning.
            cursor_id: The ID of the last item from the previous page.
                Required if cursor_dt is provided for deterministic ordering.
            limit: Maximum number of items to return (default: 100).
            album_path: If provided, filter to assets in this album path.
                Uses exact match unless include_subalbums is True.
            include_subalbums: If True, include assets from sub-albums
                (uses LIKE 'album_path/%' pattern).
            filter_hidden: If True, exclude hidden assets (live photo motion
                components). Default is True.
            filter_params: Additional filter parameters (e.g., media_type,
                filter_mode).

        Returns:
            A list of asset dictionaries for the requested page.
        """
        conn = self._get_conn()
        should_close = (conn != self._conn)

        try:
            where_clauses: List[str] = []
            params: List[Any] = []

            # Cursor condition for seek pagination
            if cursor_dt is not None and cursor_id is not None:
                # Row value comparison for efficient seeking
                where_clauses.append("(dt, id) < (?, ?)")
                params.extend([cursor_dt, cursor_id])

            # Album path filtering
            if album_path is not None:
                if include_subalbums:
                    # Match exact album or any sub-album
                    # Use ESCAPE clause for proper handling of % and _ in paths
                    where_clauses.append(
                        "(parent_album_path = ? OR parent_album_path LIKE ? ESCAPE '\\')"
                    )
                    params.append(album_path)
                    escaped_path = escape_like_pattern(album_path)
                    params.append(f"{escaped_path}/%")
                else:
                    where_clauses.append("parent_album_path = ?")
                    params.append(album_path)

            # Filter hidden assets
            if filter_hidden:
                where_clauses.append("live_role = 0")

            # Additional filters
            if filter_params:
                filter_where, filter_params_list = self._build_filter_clauses(filter_params)
                where_clauses.extend(filter_where)
                params.extend(filter_params_list)

            query = "SELECT * FROM assets"
            if where_clauses:
                query += " WHERE " + " AND ".join(where_clauses)

            query += " ORDER BY dt DESC NULLS LAST, id DESC LIMIT ?"
            params.append(limit)

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

    def read_album_assets(
        self,
        album_path: str,
        include_subalbums: bool = False,
        sort_by_date: bool = True,
        filter_hidden: bool = True,
        filter_params: Optional[Dict[str, Any]] = None,
    ) -> Iterator[Dict[str, Any]]:
        """Yield assets belonging to a specific album.

        This method provides album-scoped queries using the `parent_album_path`
        column for efficient filtering.

        Args:
            album_path: The album path to filter (e.g., "2023/Trip").
            include_subalbums: If True, include assets from sub-albums.
            sort_by_date: If True, order results by date descending.
            filter_hidden: If True, exclude hidden assets.
            filter_params: Additional filter parameters.

        Yields:
            Asset dictionaries for the matching album(s).
        """
        conn = self._get_conn()
        should_close = (conn != self._conn)

        try:
            where_clauses: List[str] = []
            params: List[Any] = []

            if include_subalbums:
                # Use ESCAPE clause for proper handling of % and _ in paths
                where_clauses.append(
                    "(parent_album_path = ? OR parent_album_path LIKE ? ESCAPE '\\')"
                )
                params.append(album_path)
                escaped_path = escape_like_pattern(album_path)
                params.append(f"{escaped_path}/%")
            else:
                where_clauses.append("parent_album_path = ?")
                params.append(album_path)

            if filter_hidden:
                where_clauses.append("live_role = 0")

            if filter_params:
                filter_where, filter_params_list = self._build_filter_clauses(filter_params)
                where_clauses.extend(filter_where)
                params.extend(filter_params_list)

            query = "SELECT * FROM assets WHERE " + " AND ".join(where_clauses)

            if sort_by_date:
                query += " ORDER BY dt DESC NULLS LAST, id DESC"

            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(query, params)

            for row in cursor:
                yield self._db_row_to_dict(row)
        finally:
            if should_close:
                conn.close()

    def list_albums(self) -> List[str]:
        """Return a list of distinct album paths in the index.

        This is useful for discovering all albums without walking the filesystem.
        """
        conn = self._get_conn()
        should_close = (conn != self._conn)

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
        conn = self._get_conn()
        should_close = (conn != self._conn)

        try:
            where_clauses: List[str] = []
            params: List[Any] = []

            if include_subalbums:
                # Use ESCAPE clause for proper handling of % and _ in paths
                where_clauses.append(
                    "(parent_album_path = ? OR parent_album_path LIKE ? ESCAPE '\\')"
                )
                params.append(album_path)
                escaped_path = escape_like_pattern(album_path)
                params.append(f"{escaped_path}/%")
            else:
                where_clauses.append("parent_album_path = ?")
                params.append(album_path)

            if filter_hidden:
                where_clauses.append("live_role = 0")

            query = "SELECT COUNT(*) FROM assets WHERE " + " AND ".join(where_clauses)
            cursor = conn.execute(query, params)
            result = cursor.fetchone()
            return result[0] if result else 0
        finally:
            if should_close:
                conn.close()

"""
Persistent storage for album index rows.

This module provides read/write access to the album index, which was previously
stored as a JSON Lines file (`index.jsonl`). The index is now stored in an
SQLite database (`index.db`) for improved performance, reliability, and
concurrency.

The `IndexStore` class manages the creation, reading, updating, and deletion
of asset records in the SQLite database.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterable, Iterator, Optional, Any, List, Tuple

from ..config import WORK_DIR_NAME


class IndexStore:
    """Read/write helper for ``index.db`` SQLite database.

    .. note::
       Instances of this class are not thread-safe. Each thread should create
       its own instance to avoid race conditions on the shared transaction connection.
    """

    def __init__(self, album_root: Path):
        self.album_root = album_root
        self.path = album_root / WORK_DIR_NAME / "index.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _init_db(self) -> None:
        """Initialize the database schema."""
        # Use a transient connection for initialization
        with sqlite3.connect(self.path, timeout=10.0) as conn:
            # Enable Write-Ahead Logging for concurrency and performance
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
            except sqlite3.OperationalError:
                pass
            conn.execute("PRAGMA synchronous=NORMAL;")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS assets (
                    rel TEXT PRIMARY KEY,
                    id TEXT,
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
                    live_partner_rel TEXT
                )
            """)

            # Check if columns exist and add them if not (migration)
            cursor = conn.execute("PRAGMA table_info(assets)")
            columns = {row[1] for row in cursor}

            if "live_role" not in columns:
                conn.execute("ALTER TABLE assets ADD COLUMN live_role INTEGER DEFAULT 0")
            if "live_partner_rel" not in columns:
                conn.execute("ALTER TABLE assets ADD COLUMN live_partner_rel TEXT")

            # Create indices for common sort/filter operations if needed.
            # 'dt' is used for sorting.
            conn.execute("CREATE INDEX IF NOT EXISTS idx_dt ON assets (dt)")
            # Add specific index for descending sort on dt to optimize streaming query
            # We use a composite index on dt and id to match the ORDER BY clause for optimal streaming.
            conn.execute("CREATE INDEX IF NOT EXISTS idx_assets_dt_id_desc ON assets (dt DESC, id DESC)")
            # 'gps' index might help if we have huge datasets, but IS NOT NULL scan is usually fast enough
            # unless we add partial index. For now, full table scan with filtering is better than loading all to Python.

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
            "rel", "id", "dt", "ts", "bytes", "mime", "make", "model", "lens",
            "iso", "f_number", "exposure_time", "exposure_compensation", "focal_length",
            "w", "h", "gps", "content_id", "frame_rate", "codec",
            "still_image_time", "dur", "original_rel_path",
            "original_album_id", "original_album_subpath",
            "live_role", "live_partner_rel"
        ]
        placeholders = ", ".join(["?"] * len(columns))
        query = f"INSERT OR REPLACE INTO assets ({', '.join(columns)}) VALUES ({placeholders})"

        conn.executemany(query, data_list)

    def _row_to_db_params(self, row: Dict[str, Any]) -> List[Any]:
        """Map a dictionary row to a list of values for the DB."""
        gps_val = row.get("gps")
        gps_str = json.dumps(gps_val) if gps_val is not None else None

        return [
            row.get("rel"),
            row.get("id"),
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

    def count(self, filter_hidden: bool = False) -> int:
        """Return the total number of assets in the index."""
        conn = self._get_conn()
        should_close = (conn != self._conn)

        try:
            query = "SELECT COUNT(*) FROM assets"
            if filter_hidden:
                query += " WHERE live_role = 0"

            cursor = conn.execute(query)
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

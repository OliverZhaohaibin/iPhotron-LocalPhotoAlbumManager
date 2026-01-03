"""Database recovery logic for corrupted SQLite databases.

This module provides graded recovery strategies to handle database corruption
without losing user data when possible.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Callable, Dict, List

from ...utils.logging import get_logger

logger = get_logger()


class RecoveryService:
    """Handles database corruption recovery with graded strategies.
    
    The recovery process follows a graduated approach:
    1. REINDEX: Attempt to rebuild indexes without data loss
    2. Salvage: Extract readable rows before rebuilding
    3. Force Reset: Delete and recreate the database
    """

    def __init__(
        self,
        db_path: Path,
        init_schema_fn: Callable[[sqlite3.Connection], None],
        row_to_dict_fn: Callable[[sqlite3.Row], Dict[str, Any]],
        insert_rows_fn: Callable[[sqlite3.Connection, List[Dict[str, Any]]], None],
    ):
        """Initialize the recovery service.
        
        Args:
            db_path: Path to the database file.
            init_schema_fn: Callback to initialize the schema.
            row_to_dict_fn: Callback to convert DB rows to dicts.
            insert_rows_fn: Callback to insert rows into the database.
        """
        self.db_path = db_path
        self._init_schema = init_schema_fn
        self._row_to_dict = row_to_dict_fn
        self._insert_rows = insert_rows_fn

    def recover(self) -> None:
        """Attempt graded recovery from a corrupted database.
        
        This method tries recovery strategies in order of increasing
        destructiveness:
        1. REINDEX (preserves all data and structure)
        2. Salvage readable rows and rebuild
        3. Force reset (complete data loss)
        """
        # Level 1: REINDEX
        if self._try_reindex():
            return

        # Level 2: Salvage readable rows
        salvaged_rows = self._salvage_rows()
        
        if salvaged_rows:
            logger.info("Salvaged %d rows from corrupted database", len(salvaged_rows))
        else:
            logger.info("No salvageable rows found; rebuilding fresh database")

        # Level 3: Force reset and restore salvaged rows
        self._force_reset()

        try:
            with sqlite3.connect(self.db_path, timeout=10.0) as conn:
                self._init_schema(conn)
                if salvaged_rows:
                    self._insert_rows(conn, salvaged_rows)
            logger.info("Rebuilt index database at %s", self.db_path)
        except sqlite3.DatabaseError as exc:
            logger.error("Failed to rebuild index database at %s: %s", self.db_path, exc)
            raise

    def _try_reindex(self) -> bool:
        """Attempt to repair the database using REINDEX.
        
        Returns:
            True if REINDEX succeeded, False otherwise.
        """
        try:
            logger.info("Attempting REINDEX for %s", self.db_path)
            with sqlite3.connect(self.db_path, timeout=10.0) as conn:
                conn.execute("REINDEX;")
                self._init_schema(conn)
            logger.info("REINDEX succeeded for %s", self.db_path)
            return True
        except sqlite3.DatabaseError as exc:
            logger.warning("REINDEX failed for %s: %s", self.db_path, exc)
            return False

    def _salvage_rows(self) -> List[Dict[str, Any]]:
        """Attempt to extract readable rows from a corrupted database.
        
        Returns:
            List of salvaged row dictionaries.
        """
        salvaged_rows: List[Dict[str, Any]] = []
        try:
            with sqlite3.connect(self.db_path, timeout=10.0) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("SELECT * FROM assets")
                try:
                    for row in cursor:
                        try:
                            row_dict = self._row_to_dict(row)
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

        return salvaged_rows

    def _force_reset(self) -> None:
        """Delete the database and all associated files.
        
        This removes the main database file along with WAL and shared memory files.
        """
        paths = [
            self.db_path,
            Path(str(self.db_path) + "-wal"),
            Path(str(self.db_path) + "-shm"),
        ]
        for p in paths:
            try:
                if p.exists():
                    p.unlink()
                    logger.info("Deleted corrupted database file %s", p)
            except OSError as exc:
                logger.warning("Failed to delete %s: %s", p, exc)

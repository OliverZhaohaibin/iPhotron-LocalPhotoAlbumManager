"""Schema migration logic for the asset index database.

This module handles database schema creation, updates, and version management.
It isolates all schema-related concerns from the main repository logic.
"""
from __future__ import annotations

import sqlite3
from typing import Set

from ...utils.logging import get_logger

logger = get_logger()


class SchemaMigrator:
    """Manages database schema initialization and migrations.
    
    This class is responsible for:
    - Creating the initial schema with all required tables and indexes
    - Adding new columns via ALTER TABLE for schema evolution
    - Maintaining indexes for query performance
    - Enabling SQLite optimizations (WAL mode, synchronous settings)
    """

    @staticmethod
    def initialize_schema(conn: sqlite3.Connection) -> None:
        """Initialize or migrate the database schema.
        
        Args:
            conn: An active SQLite connection to initialize.
        """
        # Enable Write-Ahead Logging for concurrency and performance
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
        except sqlite3.OperationalError:
            logger.warning("Failed to enable WAL mode (read-only filesystem?)")

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

        # Perform incremental schema migration (add columns if missing)
        SchemaMigrator._migrate_columns(conn)

        # Create or update indexes for query optimization
        SchemaMigrator._create_indexes(conn)

    @staticmethod
    def _migrate_columns(conn: sqlite3.Connection) -> None:
        """Add missing columns to the assets table for schema evolution.
        
        This method checks which columns exist and adds any that are missing,
        allowing the database to evolve without requiring a full rebuild.
        
        Args:
            conn: An active SQLite connection.
        """
        cursor = conn.execute("PRAGMA table_info(assets)")
        existing_columns: Set[str] = {row[1] for row in cursor}

        # Define all columns that should exist with their SQL definitions
        required_columns = {
            "micro_thumbnail": "ALTER TABLE assets ADD COLUMN micro_thumbnail BLOB",
            "live_role": "ALTER TABLE assets ADD COLUMN live_role INTEGER DEFAULT 0",
            "live_partner_rel": "ALTER TABLE assets ADD COLUMN live_partner_rel TEXT",
            "aspect_ratio": "ALTER TABLE assets ADD COLUMN aspect_ratio REAL",
            "year": "ALTER TABLE assets ADD COLUMN year INTEGER",
            "month": "ALTER TABLE assets ADD COLUMN month INTEGER",
            "media_type": "ALTER TABLE assets ADD COLUMN media_type INTEGER",
            "is_favorite": "ALTER TABLE assets ADD COLUMN is_favorite INTEGER DEFAULT 0",
            "location": "ALTER TABLE assets ADD COLUMN location TEXT",
            "parent_album_path": "ALTER TABLE assets ADD COLUMN parent_album_path TEXT",
        }

        # Add missing columns
        for col_name, alter_sql in required_columns.items():
            if col_name not in existing_columns:
                logger.info("Adding missing column: %s", col_name)
                conn.execute(alter_sql)

    @staticmethod
    def _create_indexes(conn: sqlite3.Connection) -> None:
        """Create all required indexes for optimal query performance.
        
        Args:
            conn: An active SQLite connection.
        """
        # List of all indexes to create
        indexes = [
            # Basic sorting index
            "CREATE INDEX IF NOT EXISTS idx_dt ON assets (dt)",
            
            # Favorites retrieval optimization
            "CREATE INDEX IF NOT EXISTS idx_assets_favorite_dt ON assets (is_favorite, dt DESC)",
            
            # Streaming query optimization (dt + id for deterministic ordering)
            "CREATE INDEX IF NOT EXISTS idx_assets_dt_id_desc ON assets (dt DESC, id DESC)",
            
            # Timeline grouping (Year/Month headers)
            "CREATE INDEX IF NOT EXISTS idx_year_month ON assets(year, month)",
            
            # Timeline optimization (year DESC, month DESC, dt DESC)
            ("CREATE INDEX IF NOT EXISTS idx_timeline_optimization "
             "ON assets(year DESC, month DESC, dt DESC)"),
            
            # Media type filtering (Photos/Videos)
            "CREATE INDEX IF NOT EXISTS idx_media_type ON assets(media_type)",
            
            # Core index for album-scoped pagination
            ("CREATE INDEX IF NOT EXISTS idx_assets_pagination "
             "ON assets (parent_album_path, dt DESC, id DESC)"),
            
            # Global view index (all photos sorted by date)
            ("CREATE INDEX IF NOT EXISTS idx_assets_global_sort "
             "ON assets (dt DESC, id DESC)"),
            
            # Album prefix queries (for sub-album filtering with LIKE)
            ("CREATE INDEX IF NOT EXISTS idx_parent_album_path "
             "ON assets (parent_album_path)"),
        ]

        for index_sql in indexes:
            try:
                conn.execute(index_sql)
            except sqlite3.OperationalError as exc:
                logger.warning("Failed to create index: %s", exc)

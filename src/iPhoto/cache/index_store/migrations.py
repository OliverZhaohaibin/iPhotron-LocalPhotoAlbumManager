"""Schema migration logic for the asset index database.

This module handles database schema creation, updates, and version management.
It isolates all schema-related concerns from the main repository logic.
"""
from __future__ import annotations

import sqlite3
from typing import Set

from ...config import RECENTLY_DELETED_DIR_NAME
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
                sort_ts INTEGER,
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
                is_deleted INTEGER DEFAULT 0,
                has_gps INTEGER DEFAULT 0,
                thumbnail_state TEXT DEFAULT 'stale',
                location TEXT,
                micro_thumbnail BLOB,
                thumb_cache_key TEXT,
                thumb_updated_at INTEGER DEFAULT 0,
                thumb_error TEXT,
                scan_job_id TEXT,
                index_revision INTEGER DEFAULT 0,
                index_updated_at_ms INTEGER DEFAULT 0,
                face_status TEXT,
                pet_status TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS scan_jobs (
                job_id TEXT PRIMARY KEY,
                root TEXT,
                scope TEXT,
                status TEXT,
                stage TEXT,
                found_count INTEGER DEFAULT 0,
                processed_count INTEGER DEFAULT 0,
                visible_count INTEGER DEFAULT 0,
                failed_count INTEGER DEFAULT 0,
                started_at INTEGER,
                updated_at INTEGER,
                finished_at INTEGER
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS scan_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT,
                event_type TEXT,
                payload_json TEXT,
                created_at INTEGER
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS metadata_write_jobs (
                job_id TEXT PRIMARY KEY,
                asset_rel TEXT NOT NULL,
                asset_path TEXT NOT NULL,
                gps_json TEXT NOT NULL,
                location TEXT,
                media_kind TEXT NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER DEFAULT 0,
                last_error TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
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
            "parent_album_path": "ALTER TABLE assets ADD COLUMN parent_album_path TEXT",
            "ts": "ALTER TABLE assets ADD COLUMN ts INTEGER",
            "sort_ts": "ALTER TABLE assets ADD COLUMN sort_ts INTEGER",
            "bytes": "ALTER TABLE assets ADD COLUMN bytes INTEGER",
            "make": "ALTER TABLE assets ADD COLUMN make TEXT",
            "model": "ALTER TABLE assets ADD COLUMN model TEXT",
            "lens": "ALTER TABLE assets ADD COLUMN lens TEXT",
            "iso": "ALTER TABLE assets ADD COLUMN iso INTEGER",
            "f_number": "ALTER TABLE assets ADD COLUMN f_number REAL",
            "exposure_time": "ALTER TABLE assets ADD COLUMN exposure_time REAL",
            "exposure_compensation": "ALTER TABLE assets ADD COLUMN exposure_compensation REAL",
            "focal_length": "ALTER TABLE assets ADD COLUMN focal_length REAL",
            "w": "ALTER TABLE assets ADD COLUMN w INTEGER",
            "h": "ALTER TABLE assets ADD COLUMN h INTEGER",
            "gps": "ALTER TABLE assets ADD COLUMN gps TEXT",
            "content_id": "ALTER TABLE assets ADD COLUMN content_id TEXT",
            "frame_rate": "ALTER TABLE assets ADD COLUMN frame_rate REAL",
            "codec": "ALTER TABLE assets ADD COLUMN codec TEXT",
            "still_image_time": "ALTER TABLE assets ADD COLUMN still_image_time REAL",
            "dur": "ALTER TABLE assets ADD COLUMN dur REAL",
            "original_rel_path": "ALTER TABLE assets ADD COLUMN original_rel_path TEXT",
            "original_album_id": "ALTER TABLE assets ADD COLUMN original_album_id TEXT",
            "original_album_subpath": "ALTER TABLE assets ADD COLUMN original_album_subpath TEXT",
            "micro_thumbnail": "ALTER TABLE assets ADD COLUMN micro_thumbnail BLOB",
            "live_role": "ALTER TABLE assets ADD COLUMN live_role INTEGER DEFAULT 0",
            "live_partner_rel": "ALTER TABLE assets ADD COLUMN live_partner_rel TEXT",
            "aspect_ratio": "ALTER TABLE assets ADD COLUMN aspect_ratio REAL",
            "year": "ALTER TABLE assets ADD COLUMN year INTEGER",
            "month": "ALTER TABLE assets ADD COLUMN month INTEGER",
            "media_type": "ALTER TABLE assets ADD COLUMN media_type INTEGER",
            "is_favorite": "ALTER TABLE assets ADD COLUMN is_favorite INTEGER DEFAULT 0",
            "is_deleted": "ALTER TABLE assets ADD COLUMN is_deleted INTEGER DEFAULT 0",
            "has_gps": "ALTER TABLE assets ADD COLUMN has_gps INTEGER DEFAULT 0",
            "thumbnail_state": "ALTER TABLE assets ADD COLUMN thumbnail_state TEXT DEFAULT 'ready'",
            "thumb_cache_key": "ALTER TABLE assets ADD COLUMN thumb_cache_key TEXT",
            "thumb_updated_at": "ALTER TABLE assets ADD COLUMN thumb_updated_at INTEGER DEFAULT 0",
            "thumb_error": "ALTER TABLE assets ADD COLUMN thumb_error TEXT",
            "scan_job_id": "ALTER TABLE assets ADD COLUMN scan_job_id TEXT",
            "index_revision": "ALTER TABLE assets ADD COLUMN index_revision INTEGER DEFAULT 0",
            "index_updated_at_ms": "ALTER TABLE assets ADD COLUMN index_updated_at_ms INTEGER DEFAULT 0",
            "location": "ALTER TABLE assets ADD COLUMN location TEXT",
            "face_status": "ALTER TABLE assets ADD COLUMN face_status TEXT",
            "pet_status": "ALTER TABLE assets ADD COLUMN pet_status TEXT",
        }

        # Add missing columns
        for col_name, alter_sql in required_columns.items():
            if col_name not in existing_columns:
                logger.info("Adding missing column: %s", col_name)
                conn.execute(alter_sql)

        conn.execute(
            """
            UPDATE assets
            SET face_status = CASE
                WHEN CAST(media_type AS TEXT) = '1' THEN 'skipped'
                WHEN live_role IS NOT NULL AND CAST(live_role AS INTEGER) != 0 THEN 'skipped'
                WHEN mime LIKE 'video/%' THEN 'skipped'
                ELSE 'pending'
            END
            WHERE face_status IS NULL OR TRIM(face_status) = ''
            """
        )
        conn.execute(
            """
            UPDATE assets
            SET pet_status = CASE
                WHEN CAST(media_type AS TEXT) = '1' THEN 'skipped'
                WHEN live_role IS NOT NULL AND CAST(live_role AS INTEGER) != 0 THEN 'skipped'
                WHEN mime LIKE 'video/%' THEN 'skipped'
                ELSE 'pending'
            END
            WHERE pet_status IS NULL OR TRIM(pet_status) = ''
            """
        )
        conn.execute("UPDATE assets SET sort_ts = ts WHERE sort_ts IS NULL")
        conn.execute(
            """
            UPDATE assets
            SET has_gps = CASE
                WHEN gps IS NOT NULL AND TRIM(CAST(gps AS TEXT)) != '' THEN 1
                ELSE 0
            END
            WHERE has_gps IS NULL
                OR has_gps NOT IN (0, 1)
                OR has_gps != CASE
                    WHEN gps IS NOT NULL AND TRIM(CAST(gps AS TEXT)) != '' THEN 1
                    ELSE 0
                END
            """
        )
        conn.execute(
            """
            UPDATE assets
            SET is_deleted = CASE
                WHEN parent_album_path = ?
                    OR parent_album_path LIKE ? ESCAPE '\\'
                    OR rel = ?
                    OR rel LIKE ? ESCAPE '\\'
                    THEN 1
                ELSE 0
            END
            WHERE is_deleted IS NULL
                OR is_deleted NOT IN (0, 1)
                OR (
                    is_deleted = 0
                    AND (
                        parent_album_path = ?
                        OR parent_album_path LIKE ? ESCAPE '\\'
                        OR rel = ?
                        OR rel LIKE ? ESCAPE '\\'
                    )
                )
            """,
            [
                RECENTLY_DELETED_DIR_NAME,
                f"{RECENTLY_DELETED_DIR_NAME}/%",
                RECENTLY_DELETED_DIR_NAME,
                f"{RECENTLY_DELETED_DIR_NAME}/%",
                RECENTLY_DELETED_DIR_NAME,
                f"{RECENTLY_DELETED_DIR_NAME}/%",
                RECENTLY_DELETED_DIR_NAME,
                f"{RECENTLY_DELETED_DIR_NAME}/%",
            ],
        )
        conn.execute(
            """
            UPDATE assets
            SET thumbnail_state = 'ready'
            WHERE thumbnail_state IS NULL OR TRIM(thumbnail_state) = ''
            """
        )
        conn.execute(
            """
            UPDATE assets
            SET thumbnail_state = 'stale'
            WHERE thumbnail_state = 'ready'
                AND TRIM(COALESCE(thumb_cache_key, '')) = ''
            """
        )

    @staticmethod
    def _create_indexes(conn: sqlite3.Connection) -> None:
        """Create all required indexes for optimal query performance.
        
        Args:
            conn: An active SQLite connection.
        """
        keyset_indexes = {
            "idx_assets_visible_global",
            "idx_assets_visible_album",
            "idx_assets_visible_media",
            "idx_assets_visible_favorite",
            "idx_assets_gps",
            "idx_assets_collection_global",
            "idx_assets_collection_album",
            "idx_assets_collection_media",
            "idx_assets_collection_favorite",
            "idx_assets_collection_gps",
        }
        for index_name in keyset_indexes:
            columns = [row[2] for row in conn.execute(f"PRAGMA index_info({index_name})")]
            if columns and "rel" not in columns:
                conn.execute(f"DROP INDEX {index_name}")

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

            "CREATE INDEX IF NOT EXISTS idx_assets_face_status ON assets (face_status)",
            "CREATE INDEX IF NOT EXISTS idx_assets_pet_status ON assets (pet_status)",

            # Global view index (all photos sorted by date)
            ("CREATE INDEX IF NOT EXISTS idx_assets_global_sort "
             "ON assets (dt DESC, id DESC)"),
            
            # Album prefix queries (for sub-album filtering with LIKE)
            ("CREATE INDEX IF NOT EXISTS idx_parent_album_path "
             "ON assets (parent_album_path)"),

            ("CREATE INDEX IF NOT EXISTS idx_assets_visible_global "
             "ON assets (live_role, is_deleted, thumbnail_state, sort_ts DESC, id DESC, rel DESC)"),
            ("CREATE INDEX IF NOT EXISTS idx_assets_visible_album "
             "ON assets (parent_album_path, live_role, is_deleted, thumbnail_state, "
             "sort_ts DESC, id DESC, rel DESC)"),
            ("CREATE INDEX IF NOT EXISTS idx_assets_visible_media "
             "ON assets (media_type, live_role, is_deleted, thumbnail_state, "
             "sort_ts DESC, id DESC, rel DESC)"),
            ("CREATE INDEX IF NOT EXISTS idx_assets_visible_favorite "
             "ON assets (is_favorite, live_role, is_deleted, thumbnail_state, "
             "sort_ts DESC, id DESC, rel DESC)"),
            ("CREATE INDEX IF NOT EXISTS idx_assets_gps "
             "ON assets (has_gps, live_role, is_deleted, thumbnail_state, "
             "sort_ts DESC, id DESC, rel DESC)"),
            ("CREATE INDEX IF NOT EXISTS idx_assets_collection_global "
             "ON assets (live_role, is_deleted, sort_ts DESC, id DESC, rel DESC)"),
            ("CREATE INDEX IF NOT EXISTS idx_assets_collection_album "
             "ON assets (parent_album_path, live_role, is_deleted, sort_ts DESC, id DESC, rel DESC)"),
            ("CREATE INDEX IF NOT EXISTS idx_assets_collection_media "
             "ON assets (media_type, live_role, is_deleted, sort_ts DESC, id DESC, rel DESC)"),
            ("CREATE INDEX IF NOT EXISTS idx_assets_collection_favorite "
             "ON assets (is_favorite, live_role, is_deleted, sort_ts DESC, id DESC, rel DESC)"),
            ("CREATE INDEX IF NOT EXISTS idx_assets_collection_gps "
             "ON assets (has_gps, live_role, is_deleted, sort_ts DESC, id DESC, rel DESC)"),
            "CREATE INDEX IF NOT EXISTS idx_assets_rel_lookup ON assets (rel)",
            "CREATE INDEX IF NOT EXISTS idx_assets_id_lookup ON assets (id)",
            "CREATE INDEX IF NOT EXISTS idx_assets_revision ON assets (index_revision)",
            "CREATE INDEX IF NOT EXISTS idx_assets_updated_at ON assets (index_updated_at_ms)",
            "CREATE INDEX IF NOT EXISTS idx_scan_jobs_root_scope ON scan_jobs (root, scope, updated_at)",
            "CREATE INDEX IF NOT EXISTS idx_scan_events_job ON scan_events (job_id, event_id)",
            "CREATE INDEX IF NOT EXISTS idx_metadata_write_jobs_status ON metadata_write_jobs (status, updated_at)",
            "CREATE INDEX IF NOT EXISTS idx_metadata_write_jobs_asset ON metadata_write_jobs (asset_rel)",
        ]

        for index_sql in indexes:
            try:
                conn.execute(index_sql)
            except sqlite3.OperationalError as exc:
                logger.warning("Failed to create index: %s", exc)

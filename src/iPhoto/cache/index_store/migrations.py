"""Schema migration logic for the asset index database.

This module handles database schema creation, updates, and version management.
It isolates all schema-related concerns from the main repository logic.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from ...config import RECENTLY_DELETED_DIR_NAME
from ...utils.logging import get_logger

logger = get_logger()

# Version 1 represents the original complete schema and one-time data repairs below.
# Version 2 adds cached video presentation metadata used by Gallery/Detail reads.
# Opening an already migrated database must be O(1); in particular it must not
# revisit every asset row on each desktop launch.
CURRENT_SCHEMA_VERSION = 4
MIGRATION_PROTOCOL_VERSION = 1
MIGRATION_STATE_NAME = "startup-migration.json"

_ASSET_COLUMN_MIGRATIONS = {
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
    "thumb_revision": "ALTER TABLE assets ADD COLUMN thumb_revision TEXT",
    "scan_job_id": "ALTER TABLE assets ADD COLUMN scan_job_id TEXT",
    "index_revision": "ALTER TABLE assets ADD COLUMN index_revision INTEGER DEFAULT 0",
    "index_updated_at_ms": "ALTER TABLE assets ADD COLUMN index_updated_at_ms INTEGER DEFAULT 0",
    "location": "ALTER TABLE assets ADD COLUMN location TEXT",
    "face_status": "ALTER TABLE assets ADD COLUMN face_status TEXT",
    "pet_status": "ALTER TABLE assets ADD COLUMN pet_status TEXT",
    "video_rotation_cw": "ALTER TABLE assets ADD COLUMN video_rotation_cw INTEGER",
    "video_linux_180_hint": "ALTER TABLE assets ADD COLUMN video_linux_180_hint INTEGER",
    "source_mtime_ns": "ALTER TABLE assets ADD COLUMN source_mtime_ns INTEGER DEFAULT 0",
    "image_orientation": "ALTER TABLE assets ADD COLUMN image_orientation INTEGER DEFAULT 0",
}
_V1_REQUIRED_COLUMNS = {
    "assets": frozenset({"rel", "id", "dt", "mime", *_ASSET_COLUMN_MIGRATIONS}),
    "scan_jobs": frozenset(
        {
            "job_id", "root", "scope", "status", "stage", "found_count",
            "processed_count", "visible_count", "failed_count", "started_at",
            "updated_at", "finished_at",
        }
    ),
    "scan_events": frozenset(
        {"event_id", "job_id", "event_type", "payload_json", "created_at"}
    ),
    "metadata_write_jobs": frozenset(
        {
            "job_id", "asset_rel", "asset_path", "gps_json", "location",
            "media_kind", "status", "attempts", "last_error", "created_at",
            "updated_at",
        }
    ),
}


class SchemaPreparationError(RuntimeError):
    """Stable failure raised while preparing a library index."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        operation: str = "unknown",
        native_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.operation = operation
        self.native_code = native_code


@dataclass(frozen=True, slots=True)
class MigrationState:
    protocol_version: int
    migration_id: str
    database_name: str
    backup_name: str
    source_version: int
    target_version: int
    stage: str
    started_at_ms: int

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> MigrationState:
        state = cls(
            protocol_version=int(payload["protocol_version"]),
            migration_id=str(payload["migration_id"]),
            database_name=str(payload["database_name"]),
            backup_name=str(payload["backup_name"]),
            source_version=int(payload["source_version"]),
            target_version=int(payload["target_version"]),
            stage=str(payload["stage"]),
            started_at_ms=int(payload["started_at_ms"]),
        )
        if state.protocol_version != MIGRATION_PROTOCOL_VERSION:
            raise SchemaPreparationError(
                "migration_recovery_failed", "Unsupported migration recovery state"
            )
        if (
            Path(state.database_name).name != state.database_name
            or Path(state.backup_name).name != state.backup_name
            or not state.migration_id
            or state.backup_name
            != f"{state.database_name}.migration-{state.migration_id}.bak"
            or state.stage
            not in {"backup_pending", "migration_pending", "restored_migration_pending"}
        ):
            raise SchemaPreparationError(
                "migration_recovery_failed", "Invalid migration recovery state"
            )
        return state


def _native_error_code(exc: BaseException) -> str | None:
    sqlite_name = getattr(exc, "sqlite_errorname", None)
    if isinstance(sqlite_name, str) and sqlite_name:
        return sqlite_name
    winerror = getattr(exc, "winerror", None)
    if isinstance(winerror, int):
        return f"WinError_{winerror}"
    error_number = getattr(exc, "errno", None)
    if isinstance(error_number, int):
        return f"errno_{error_number}"
    return None


def _classify_filesystem_error(exc: OSError, *, operation: str) -> str:
    error_number = getattr(exc, "errno", None)
    winerror = getattr(exc, "winerror", None)
    if error_number == 28:
        return "disk_full"
    if winerror in {32, 33}:
        return "migration_file_busy"
    if error_number == 30:
        return "db_read_only"
    if error_number in {1, 13}:
        if operation in {"backup_publish", "restore_swap", "cleanup"} and winerror == 5:
            return "migration_file_busy"
        if operation in {"workdir_create", "state_write", "backup_create"}:
            return "workspace_unwritable"
        return "db_read_only"
    return "migration_recovery_failed"


def _raise_filesystem_error(exc: OSError, message: str, *, operation: str) -> None:
    raise SchemaPreparationError(
        _classify_filesystem_error(exc, operation=operation),
        message,
        operation=operation,
        native_code=_native_error_code(exc),
    ) from exc


def _classify_sqlite_operational_error(
    exc: sqlite3.OperationalError, *, fallback: str
) -> str:
    """Map SQLite's platform-dependent operational messages to stable codes."""

    native_code = _native_error_code(exc) or ""
    if native_code.startswith(("SQLITE_BUSY", "SQLITE_LOCKED")):
        return "db_locked"
    if native_code.startswith("SQLITE_READONLY"):
        return "db_read_only"
    if native_code.startswith("SQLITE_FULL"):
        return "disk_full"
    if native_code.startswith("SQLITE_CANTOPEN"):
        return "db_open_failed"
    message = str(exc).casefold()
    if "locked" in message or "busy" in message:
        return "db_locked"
    if "readonly" in message or "read-only" in message:
        return "db_read_only"
    if "full" in message:
        return "disk_full"
    if "unable to open database file" in message:
        return "db_open_failed"
    return fallback


@contextmanager
def _managed_connection(
    database_path: Path, *, timeout: float = 0.5
) -> Iterator[sqlite3.Connection]:
    """Commit or roll back work and always release the OS file handle."""

    connection = sqlite3.connect(database_path, timeout=timeout)
    try:
        yield connection
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise
    else:
        if connection.in_transaction:
            connection.commit()
    finally:
        connection.close()


def _table_info(connection: sqlite3.Connection, table: str) -> list[tuple]:
    cursor = connection.execute(f"PRAGMA table_info({table})")
    try:
        return list(cursor.fetchall())
    finally:
        cursor.close()


def _schema_matches_v1(connection: sqlite3.Connection) -> bool:
    for table, required_columns in _V1_REQUIRED_COLUMNS.items():
        rows = _table_info(connection, table)
        if not rows:
            return False
        columns = {str(row[1]) for row in rows}
        if not required_columns.issubset(columns):
            return False
        if table == "assets":
            rel_row = next((row for row in rows if str(row[1]) == "rel"), None)
            if rel_row is None or int(rel_row[5]) != 1:
                return False
    return True


def _atomic_write_state(path: Path, state: MigrationState) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(asdict(state), sort_keys=True, separators=(",", ":"))
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        _raise_filesystem_error(
            exc,
            "Could not record migration recovery state",
            operation="state_write",
        )


def _read_state(path: Path) -> MigrationState | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise TypeError("migration state is not an object")
        return MigrationState.from_payload(payload)
    except SchemaPreparationError:
        raise
    except Exception as exc:
        raise SchemaPreparationError(
            "migration_recovery_failed", "Migration recovery state is unreadable"
        ) from exc


def _integrity_ok(database_path: Path) -> bool:
    if not database_path.is_file():
        return False
    try:
        with _managed_connection(database_path) as connection:
            row = connection.execute("PRAGMA integrity_check").fetchone()
        return bool(row and str(row[0]).casefold() == "ok")
    except sqlite3.OperationalError as exc:
        code = _classify_sqlite_operational_error(exc, fallback="db_corrupt")
        if code != "db_corrupt":
            raise SchemaPreparationError(
                code,
                "Library index integrity could not be checked",
                operation="integrity_check",
                native_code=_native_error_code(exc),
            ) from exc
        return False
    except sqlite3.DatabaseError:
        return False


def _online_backup(source_path: Path, destination_path: Path) -> None:
    try:
        destination_path.unlink(missing_ok=True)
        with _managed_connection(source_path) as source:
            with _managed_connection(destination_path) as destination:
                source.backup(destination)
    except sqlite3.OperationalError as exc:
        code = _classify_sqlite_operational_error(
            exc, fallback="migration_backup_failed"
        )
        raise SchemaPreparationError(
            code,
            "Could not create a migration backup",
            operation="backup_create",
            native_code=_native_error_code(exc),
        ) from exc
    except OSError as exc:
        _raise_filesystem_error(
            exc,
            "Could not create a migration backup",
            operation="backup_create",
        )


def _remove_database_sidecars(database_path: Path) -> None:
    for suffix in ("-wal", "-shm"):
        database_path.with_name(database_path.name + suffix).unlink(missing_ok=True)


def _restore_backup(database_path: Path, backup_path: Path) -> None:
    restored = database_path.with_name(database_path.name + ".restore-tmp")
    _online_backup(backup_path, restored)
    if not _integrity_ok(restored):
        restored.unlink(missing_ok=True)
        raise SchemaPreparationError(
            "migration_recovery_failed", "Migration backup could not be restored"
        )
    try:
        _remove_database_sidecars(database_path)
        os.replace(restored, database_path)
    except OSError as exc:
        _raise_filesystem_error(
            exc,
            "Could not restore the migration backup",
            operation="restore_swap",
        )


def _cleanup_migration(state_path: Path, backup_path: Path) -> bool:
    # Keep the state file until cleanup is otherwise complete. If the process
    # stops between these operations, the next prepare can safely finish it.
    try:
        backup_path.unlink(missing_ok=True)
        backup_path.with_suffix(backup_path.suffix + ".tmp").unlink(missing_ok=True)
        state_path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning(
            "Migration cleanup remains pending (%s)",
            _native_error_code(exc) or type(exc).__name__,
        )
        return False
    return True


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

        current_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if current_version > CURRENT_SCHEMA_VERSION:
            raise SchemaPreparationError(
                "future_schema",
                "Library index was created by a newer application version",
            )
        if current_version == CURRENT_SCHEMA_VERSION:
            # Index definitions are cheap schema metadata checks and may be
            # repaired independently by older builds or recovery tools. Keep
            # this guard without repeating any full-table data repair.
            SchemaMigrator._create_indexes(conn)
            return

        while current_version < CURRENT_SCHEMA_VERSION:
            next_version = current_version + 1
            migration = getattr(SchemaMigrator, f"_migrate_to_v{next_version}", None)
            if not callable(migration):
                raise SchemaPreparationError(
                    "migration_recovery_failed",
                    f"No migration is available for schema version {next_version}",
                )
            conn.execute("BEGIN IMMEDIATE")
            try:
                migration(conn)
                conn.execute(f"PRAGMA user_version = {next_version}")
            except Exception:
                conn.rollback()
                raise
            else:
                conn.commit()
            current_version = next_version

    @staticmethod
    def _migrate_to_v1(conn: sqlite3.Connection) -> None:
        """Create and repair the version-1 schema inside one transaction."""

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
                thumb_revision TEXT,
                scan_job_id TEXT,
                index_revision INTEGER DEFAULT 0,
                index_updated_at_ms INTEGER DEFAULT 0,
                face_status TEXT,
                pet_status TEXT,
                video_rotation_cw INTEGER,
                video_linux_180_hint INTEGER,
                source_mtime_ns INTEGER DEFAULT 0,
                image_orientation INTEGER DEFAULT 0
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
        # The caller advances user_version in the same transaction.

    @staticmethod
    def _migrate_to_v2(conn: sqlite3.Connection) -> None:
        """Add cached video presentation columns required by Gallery reads."""

        existing_columns = {str(row[1]) for row in _table_info(conn, "assets")}
        for column_name in ("video_rotation_cw", "video_linux_180_hint"):
            if column_name not in existing_columns:
                conn.execute(_ASSET_COLUMN_MIGRATIONS[column_name])

    @staticmethod
    def _migrate_to_v3(conn: sqlite3.Connection) -> None:
        """Add stable source-pixel revision and orientation columns."""

        existing_columns = {str(row[1]) for row in _table_info(conn, "assets")}
        for column_name in ("source_mtime_ns", "image_orientation"):
            if column_name not in existing_columns:
                conn.execute(_ASSET_COLUMN_MIGRATIONS[column_name])

    @staticmethod
    def _migrate_to_v4(conn: sqlite3.Connection) -> None:
        """Add the desired thumbnail render revision without invalidating cache keys."""

        existing_columns = {str(row[1]) for row in _table_info(conn, "assets")}
        if "thumb_revision" not in existing_columns:
            conn.execute(_ASSET_COLUMN_MIGRATIONS["thumb_revision"])
        SchemaMigrator._create_indexes(conn)

    @staticmethod
    def prepare_database(database_path: Path) -> tuple[int, tuple[str, ...]]:
        """Recover an interrupted migration and synchronously prepare the schema."""

        database_path = Path(database_path)
        try:
            database_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            _raise_filesystem_error(
                exc,
                "Could not create the library work directory",
                operation="workdir_create",
            )
        state_path = database_path.parent / MIGRATION_STATE_NAME
        state = _read_state(state_path)
        warnings: list[str] = []

        if state is not None:
            if state.database_name != database_path.name:
                raise SchemaPreparationError(
                    "migration_recovery_failed", "Migration state targets another database"
                )
            backup_path = database_path.parent / state.backup_name
            current_ok = _integrity_ok(database_path)
            current_version = -1
            if current_ok:
                with _managed_connection(database_path) as connection:
                    current_version = int(
                        connection.execute("PRAGMA user_version").fetchone()[0]
                    )
            if current_ok and current_version == state.target_version:
                if state.stage == "restored_migration_pending":
                    warnings.append("migration_restored")
                if not _cleanup_migration(state_path, backup_path):
                    warnings.append("migration_cleanup_pending")
                return current_version, tuple(warnings)
            if not current_ok:
                if not _integrity_ok(backup_path):
                    raise SchemaPreparationError(
                        "migration_recovery_failed",
                        "Both the library index and its migration backup are unusable",
                    )
                _restore_backup(database_path, backup_path)
                warnings.append("migration_restored")
                state = replace(state, stage="restored_migration_pending")
                _atomic_write_state(state_path, state)
            elif not (
                state.source_version <= current_version < state.target_version
            ):
                raise SchemaPreparationError(
                    "migration_recovery_failed",
                    "Interrupted migration state does not match the library index",
                )
        else:
            backup_path = database_path.parent / "unused"

        existed = database_path.is_file() and database_path.stat().st_size > 0
        try:
            with _managed_connection(database_path) as connection:
                source_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                can_adopt_v1 = source_version == 0 and _schema_matches_v1(connection)
        except sqlite3.OperationalError as exc:
            code = _classify_sqlite_operational_error(exc, fallback="db_open_failed")
            raise SchemaPreparationError(
                code,
                "Library index could not be opened",
                operation="database_open",
                native_code=_native_error_code(exc),
            ) from exc
        except sqlite3.DatabaseError as exc:
            raise SchemaPreparationError("db_corrupt", "Library index is corrupt") from exc

        if source_version > CURRENT_SCHEMA_VERSION:
            raise SchemaPreparationError(
                "future_schema", "Library index uses a newer schema version"
            )

        if can_adopt_v1:
            try:
                with _managed_connection(database_path) as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                    if version != 0 or not _schema_matches_v1(connection):
                        connection.rollback()
                        can_adopt_v1 = False
                    else:
                        SchemaMigrator._migrate_columns(connection)
                        SchemaMigrator._create_indexes(connection)
                        connection.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")
            except sqlite3.OperationalError as exc:
                code = _classify_sqlite_operational_error(
                    exc, fallback="migration_failed"
                )
                raise SchemaPreparationError(
                    code,
                    "Library index schema could not be adopted",
                    operation="schema_adopt",
                    native_code=_native_error_code(exc),
                ) from exc
            if can_adopt_v1:
                if state is not None and not _cleanup_migration(state_path, backup_path):
                    warnings.append("migration_cleanup_pending")
                return CURRENT_SCHEMA_VERSION, tuple(warnings)
        requires_integrity_validation = (
            state is not None or source_version < CURRENT_SCHEMA_VERSION
        )

        if source_version < CURRENT_SCHEMA_VERSION and existed:
            if state is None:
                migration_id = uuid.uuid4().hex
                backup_name = f"{database_path.name}.migration-{migration_id}.bak"
                backup_path = database_path.parent / backup_name
                state = MigrationState(
                    protocol_version=MIGRATION_PROTOCOL_VERSION,
                    migration_id=migration_id,
                    database_name=database_path.name,
                    backup_name=backup_name,
                    source_version=source_version,
                    target_version=CURRENT_SCHEMA_VERSION,
                    stage="backup_pending",
                    started_at_ms=int(time.time() * 1000),
                )
                _atomic_write_state(state_path, state)
            temporary_backup = backup_path.with_suffix(backup_path.suffix + ".tmp")
            if not _integrity_ok(backup_path):
                _online_backup(database_path, temporary_backup)
                if not _integrity_ok(temporary_backup):
                    raise SchemaPreparationError(
                        "migration_recovery_failed", "Migration backup failed integrity checking"
                    )
                try:
                    os.replace(temporary_backup, backup_path)
                except OSError as exc:
                    _raise_filesystem_error(
                        exc,
                        "Could not publish the migration backup",
                        operation="backup_publish",
                    )
            if state.stage != "restored_migration_pending":
                state = replace(state, stage="migration_pending")
            _atomic_write_state(state_path, state)

        try:
            with _managed_connection(database_path) as connection:
                SchemaMigrator.initialize_schema(connection)
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        except SchemaPreparationError:
            raise
        except sqlite3.OperationalError as exc:
            code = _classify_sqlite_operational_error(
                exc, fallback="migration_failed"
            )
            raise SchemaPreparationError(
                code,
                "Library index migration failed",
                operation="migration_write",
                native_code=_native_error_code(exc),
            ) from exc
        except sqlite3.DatabaseError as exc:
            raise SchemaPreparationError("db_corrupt", "Library index is corrupt") from exc

        if version != CURRENT_SCHEMA_VERSION or (
            requires_integrity_validation and not _integrity_ok(database_path)
        ):
            raise SchemaPreparationError(
                "migration_failed",
                "Migrated library index failed validation",
                operation="migration_validate",
            )
        if state is not None:
            if not _cleanup_migration(state_path, backup_path):
                warnings.append("migration_cleanup_pending")
        return version, tuple(warnings)

    @staticmethod
    def _migrate_columns(conn: sqlite3.Connection) -> None:
        """Add missing columns to the assets table for schema evolution.
        
        This method checks which columns exist and adds any that are missing,
        allowing the database to evolve without requiring a full rebuild.
        
        Args:
            conn: An active SQLite connection.
        """
        existing_columns = {str(row[1]) for row in _table_info(conn, "assets")}

        # Define all columns that should exist with their SQL definitions
        # Add missing columns
        for col_name, alter_sql in _ASSET_COLUMN_MIGRATIONS.items():
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
            index_sql_row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
                (index_name,),
            ).fetchone()
            index_sql = str(index_sql_row[0] or "") if index_sql_row else ""
            visible_index_needs_refresh = (
                index_name.startswith("idx_assets_visible_")
                or index_name == "idx_assets_gps"
            ) and (
                "thumbnail_state IN ('ready', 'stale')" not in index_sql
                or "TRIM(COALESCE(thumb_cache_key, '')) != ''" not in index_sql
            )
            if columns and (
                "rel" not in columns
                or visible_index_needs_refresh
            ):
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
             "ON assets (live_role, is_deleted, sort_ts DESC, id DESC, rel DESC) "
             "WHERE thumbnail_state IN ('ready', 'stale') "
             "AND TRIM(COALESCE(thumb_cache_key, '')) != ''"),
            ("CREATE INDEX IF NOT EXISTS idx_assets_visible_album "
             "ON assets (parent_album_path, live_role, is_deleted, "
             "sort_ts DESC, id DESC, rel DESC) "
             "WHERE thumbnail_state IN ('ready', 'stale') "
             "AND TRIM(COALESCE(thumb_cache_key, '')) != ''"),
            ("CREATE INDEX IF NOT EXISTS idx_assets_visible_media "
             "ON assets (media_type, live_role, is_deleted, "
             "sort_ts DESC, id DESC, rel DESC) "
             "WHERE thumbnail_state IN ('ready', 'stale') "
             "AND TRIM(COALESCE(thumb_cache_key, '')) != ''"),
            ("CREATE INDEX IF NOT EXISTS idx_assets_visible_favorite "
             "ON assets (is_favorite, live_role, is_deleted, "
             "sort_ts DESC, id DESC, rel DESC) "
             "WHERE thumbnail_state IN ('ready', 'stale') "
             "AND TRIM(COALESCE(thumb_cache_key, '')) != ''"),
            ("CREATE INDEX IF NOT EXISTS idx_assets_gps "
             "ON assets (has_gps, live_role, is_deleted, "
             "sort_ts DESC, id DESC, rel DESC) "
             "WHERE thumbnail_state IN ('ready', 'stale') "
             "AND TRIM(COALESCE(thumb_cache_key, '')) != ''"),
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

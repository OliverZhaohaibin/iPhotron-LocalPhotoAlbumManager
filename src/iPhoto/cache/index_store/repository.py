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
import uuid
from collections.abc import Mapping
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from ...config import GLOBAL_INDEX_DB_NAME
from ...domain.models.core import normalise_content_id
from ...domain.models.query import CollectionQuery, PageCursor, PageResult, WindowResult
from ...infrastructure.services.performance_events import (
    audit_full_scan_query,
    emit_perf_event,
    explain_enabled,
    monotonic_ms,
)
from ...infrastructure.services.thumbnail_cache_keys import (
    thumbnail_cache_file_for_key,
    thumbnail_cache_key,
)
from ...people.status import normalize_face_status
from ...pets.status import normalize_pet_status
from ...utils.logging import get_logger
from ...utils.pathutils import LibraryAssetPathError, ensure_work_dir, resolve_library_asset_path
from .engine import DatabaseManager
from .migrations import MIGRATION_STATE_NAME, SchemaMigrator, SchemaPreparationError
from .queries import QueryBuilder
from .recovery import RecoveryService
from .row_mapper import db_row_to_dict, insert_rows, row_to_db_params
from .scan_merge import conflicting_thumbnail_revisions
from .scan_merge import merge_scan_rows as merge_scan_rows_payload

logger = get_logger()

# SQLite supports at most SQLITE_MAX_VARIABLE_NUMBER bound parameters per query
# (compile-time default 999, raised to 32766 in SQLite ≥3.32).  Use a
# conservative chunk size so large scans never hit the limit regardless of
# the SQLite version in use.
_SQLITE_PARAM_CHUNK_SIZE = 900
_DEEP_OFFSET_LIMIT = 5_000
_DEEP_SEEK_CHUNK_SIZE = 1_024
_MAX_COLLECTION_ANCHORS_PER_QUERY = 64
_OMIT_METADATA_VALUE = object()
_GALLERY_COLUMNS = (
    "rel, id, parent_album_path, dt, ts, sort_ts, bytes, mime, w, h, gps, content_id, "
    "still_image_time, dur, live_role, live_partner_rel, aspect_ratio, media_type, "
    "is_favorite, is_deleted, has_gps, thumbnail_state, location, micro_thumbnail, "
    "thumb_cache_key, face_status, video_rotation_cw, video_linux_180_hint, "
    "source_mtime_ns, image_orientation, thumb_revision, thumb_refresh_pending, index_revision"
)


@dataclass
class _CollectionCache:
    order_revision: int
    counts: dict[CollectionQuery, int] = field(default_factory=dict)
    anchors: dict[CollectionQuery, dict[int, PageCursor | None]] = field(default_factory=dict)

# Global singleton instance and lock for thread-safe access
_global_instance: Optional["AssetRepository"] = None
_global_lock = threading.Lock()
_prepared_roots: dict[Path, Any] = {}


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
        
        credential = _prepared_roots.pop(resolved_root, None)
        if (
            credential is None
            and _global_instance is not None
            and _global_instance.library_root == resolved_root
        ):
            return _global_instance
        if credential is not None:
            matches_current = getattr(credential, "matches_current", None)
            if not callable(matches_current) or not matches_current():
                raise RuntimeError("prepared repository credential is stale")

        repository = AssetRepository(resolved_root, initialize=credential is None)
        if credential is not None and repository.path != getattr(credential, "database_path", None):
            raise RuntimeError("prepared repository credential is stale")
        if _global_instance is not None:
            logger.info(
                "Rebinding global database from %s to %s", _global_instance.path, repository.path
            )
            _global_instance.close()
        _global_instance = repository
        return _global_instance


def mark_repository_prepared(library_root: Path, credential: Any) -> None:
    """Register one consume-on-open schema preparation credential."""

    with _global_lock:
        _prepared_roots[Path(library_root)] = credential


def reset_global_repository() -> None:
    """Reset the global repository singleton.
    
    This is primarily used for testing to ensure clean state between tests.
    """
    global _global_instance
    
    with _global_lock:
        if _global_instance is not None:
            _global_instance.close()
            _global_instance = None
        _prepared_roots.clear()


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

    def __init__(self, library_root: Path, *, initialize: bool = True):
        """Initialize the asset repository.
        
        Args:
            library_root: The root directory of the library. The global database
                will be created at `<library_root>/.iPhoto/global_index.db`.
        """
        self.library_root = library_root
        self.path = ensure_work_dir(library_root) / GLOBAL_INDEX_DB_NAME
        
        self._db_manager = DatabaseManager(self.path)
        self._conn: Optional[sqlite3.Connection] = None
        self._collection_cache = _CollectionCache(0)
        self._collection_cache_lock = threading.Lock()
        if initialize:
            self._init_db()

    def _init_db(self) -> None:
        """Prepare direct opens with the same recovery rules as the startup probe."""
        try:
            _, warnings = SchemaMigrator.prepare_database(self.path)
        except SchemaPreparationError as exc:
            if exc.code != "db_corrupt" or (self.path.parent / MIGRATION_STATE_NAME).exists():
                raise
            logger.warning("Detected index.db corruption at %s: %s", self.path, exc)
            recovery = RecoveryService(
                self.path,
                SchemaMigrator.initialize_schema,
                self._db_row_to_dict,
                self._insert_rows,
            )
            recovery.recover()
        else:
            for warning in warnings:
                logger.warning("Library index preparation for %s reported %s", self.path, warning)

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
        stamped_rows = self._stamp_rows(rows)
        with self.transaction() as conn:
            conn.execute("DELETE FROM assets")
            self._insert_rows(conn, stamped_rows)

    def append_rows(self, rows: Iterable[Dict[str, Any]]) -> None:
        """Merge *rows* into the index, replacing duplicates by ``rel`` key."""
        stamped_rows = self._stamp_rows(rows)
        with self.transaction() as conn:
            self._insert_rows(conn, stamped_rows)

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

        materialized_rows = self._stamp_rows(rows)
        if not materialized_rows:
            return []

        # De-duplicate while preserving insertion order so each rel is queried
        # at most once.
        unique_rels: List[str] = list(
            dict.fromkeys(str(row["rel"]) for row in materialized_rows if row.get("rel"))
        )

        validated_versions: dict[str, tuple[str, int]] = {}
        current_revisions: dict[str, str | None] = {}
        while True:
            with self.transaction(begin_mode="IMMEDIATE") as conn:
                existing_rows = self._scan_merge_existing_rows(conn, unique_rels)
                conflicts = conflicting_thumbnail_revisions(materialized_rows, existing_rows)
                to_validate = {
                    rel: version for rel, version in conflicts.items()
                    if validated_versions.get(rel) != version
                }
                if not to_validate:
                    merged_rows = merge_scan_rows_payload(
                        materialized_rows,
                        existing_rows,
                        current_thumbnail_revisions=current_revisions,
                    )
                    former_partners = self._clear_replaced_live_partners(merged_rows, existing_rows)
                    self._insert_rows(conn, merged_rows)
                    if former_partners:
                        conn.executemany(
                            """
                            UPDATE assets
                            SET live_role = 0, live_partner_rel = NULL,
                                index_revision = COALESCE(index_revision, 0) + 1
                            WHERE rel = ? AND live_partner_rel = ?
                            """,
                            former_partners,
                        )
                    break
            # Filesystem reads must release the SQLite write lock. Recheck the
            # observed thumbnail versions so an edit committed during I/O wins.
            for rel, version in to_validate.items():
                current_revisions[rel] = self._current_thumbnail_revision(rel)
                validated_versions[rel] = version

        return merged_rows

    @staticmethod
    def _clear_replaced_live_partners(
        merged_rows: list[dict[str, Any]], existing_rows: dict[str, dict[str, Any]],
    ) -> set[tuple[str, str]]:
        """Clear reciprocal links in the batch and return the old links for SQL cleanup."""

        former_partners: set[tuple[str, str]] = set()
        for row in merged_rows:
            rel = row.get("rel")
            previous = existing_rows.get(rel)
            if previous is not None:
                partner = previous.get("live_partner_rel")
                if partner and partner != row.get("live_partner_rel"):
                    former_partners.add((partner, rel))
        if former_partners:
            # Some partners may already be in the batch that will be published.
            for row in merged_rows:
                if (row.get("rel"), row.get("live_partner_rel")) in former_partners:
                    row.update(live_role=0, live_partner_rel=None)
        return former_partners

    def _scan_merge_existing_rows(
        self, conn: sqlite3.Connection, rels: list[str]
    ) -> dict[str, dict[str, Any]]:
        rows: dict[str, dict[str, Any]] = {}
        previous_factory = conn.row_factory
        conn.row_factory = sqlite3.Row
        try:
            for offset in range(0, len(rels), _SQLITE_PARAM_CHUNK_SIZE):
                chunk = rels[offset : offset + _SQLITE_PARAM_CHUNK_SIZE]
                placeholders = ", ".join(["?"] * len(chunk))
                cursor = conn.execute(f"SELECT * FROM assets WHERE rel IN ({placeholders})", chunk)
                for db_row in cursor:
                    rows[str(db_row["rel"])] = self._db_row_to_dict(db_row)
        finally:
            conn.row_factory = previous_factory
        return rows

    def _current_thumbnail_revision(self, rel: str) -> str | None:
        from ...infrastructure.services.thumbnail_artifact import thumbnail_revision

        try:
            resolved = resolve_library_asset_path(self.library_root, rel)
            if not resolved.is_file():
                return None
            # Keep the lexical path used by the scanner in the revision digest.
            return thumbnail_revision(self.library_root / rel)
        except (LibraryAssetPathError, OSError, RuntimeError):
            return None

    def upsert_row(self, rel: str, row: Dict[str, Any]) -> None:
        """Insert or update a single row identified by *rel*."""
        row_data = row.copy()
        row_data["rel"] = rel
        row_data["index_updated_at_ms"] = _utc_ms()
        with self.transaction() as conn:
            self._insert_rows(conn, [row_data])

    def _stamp_rows(self, rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        now = _utc_ms()
        stamped: List[Dict[str, Any]] = []
        for row in rows:
            row_data = dict(row)
            row_data["index_updated_at_ms"] = now
            stamped.append(row_data)
        return stamped

    def remove_rows(self, rels: Iterable[str], *, clear_live_partners: bool = False) -> None:
        """Drop any index rows whose ``rel`` key matches *rels*."""
        removable = list(rels)
        if not removable:
            return

        with self.transaction(begin_mode="IMMEDIATE") as conn:
            for offset in range(0, len(removable), _SQLITE_PARAM_CHUNK_SIZE):
                chunk = removable[offset : offset + _SQLITE_PARAM_CHUNK_SIZE]
                placeholders = ", ".join(["?"] * len(chunk))
                if clear_live_partners:
                    conn.execute(
                        f"""
                        UPDATE assets
                        SET live_role = 0, live_partner_rel = NULL,
                            index_revision = COALESCE(index_revision, 0) + 1
                        WHERE rel IN (
                            SELECT live_partner_rel FROM assets WHERE rel IN ({placeholders})
                        )
                          AND live_partner_rel IN ({placeholders})
                        """,
                        [*chunk, *chunk],
                    )
                conn.execute(f"DELETE FROM assets WHERE rel IN ({placeholders})", chunk)

    def relocate_rows(
        self,
        destinations: Mapping[str, str],
        *,
        expected_asset_ids: Mapping[str, str],
    ) -> None:
        """Move verified scan rows without dropping their durable user choices."""

        if not destinations:
            return
        if (
            set(destinations) & set(destinations.values())
            or len(set(destinations.values())) != len(destinations)
        ):
            raise ValueError("Asset relocation paths must be distinct")
        now = _utc_ms()
        with self.transaction(begin_mode="IMMEDIATE") as conn:
            sources = {}
            source_rels = list(destinations)
            for offset in range(0, len(source_rels), _SQLITE_PARAM_CHUNK_SIZE):
                chunk = source_rels[offset : offset + _SQLITE_PARAM_CHUNK_SIZE]
                placeholders = ", ".join("?" for _ in chunk)
                for source, asset_id, partner in conn.execute(
                    f"SELECT rel, id, live_partner_rel FROM assets WHERE rel IN ({placeholders})", chunk
                ):
                    if asset_id != expected_asset_ids[source]:
                        raise ValueError(f"Asset changed during relocation: {source}")
                    sources[source] = partner
            for source in sources:
                destination = destinations[source]
                parent = Path(destination).parent
                existing = conn.execute("SELECT id FROM assets WHERE rel = ?", (destination,)).fetchone()
                if existing is not None and existing[0] != expected_asset_ids[source]:
                    raise ValueError(f"Destination asset changed during relocation: {destination}")
                conn.execute("DELETE FROM assets WHERE rel = ?", (destination,))
                # Update in place so even optional metadata columns survive.
                # Path-based rendering and recognition facts need a fresh scan.
                conn.execute(
                    "UPDATE assets SET rel = ?, parent_album_path = ?, "
                    "thumbnail_state = 'stale', micro_thumbnail = NULL, "
                    "thumb_cache_key = NULL, thumb_revision = NULL, "
                    "thumb_refresh_pending = 0, "
                    "face_status = NULL, pet_status = NULL, "
                    "index_revision = COALESCE(index_revision, 0) + 1, "
                    "index_updated_at_ms = ? WHERE rel = ?",
                    (destination, parent.as_posix() if parent != Path(".") else "", now, source),
                )
                conn.execute(
                    "UPDATE metadata_write_jobs SET asset_rel = ?, asset_path = ?, "
                    "updated_at = ? WHERE asset_rel = ? AND status IN ('queued', 'writing', 'failed')",
                    (destination, str(self.library_root / destination), now, source),
                )
            # Follow the reciprocal pair's primary key, rather than scanning
            # every library row once for each relocated asset.
            for source, partner in sources.items():
                if partner:
                    conn.execute(
                        "UPDATE assets SET live_partner_rel = ?, "
                        "index_revision = COALESCE(index_revision, 0) + 1, "
                        "index_updated_at_ms = ? WHERE rel = ? AND live_partner_rel = ?",
                        (destinations[source], now, destinations.get(partner, partner), source),
                    )

    def remap_album_paths(self, old_root: Path, new_root: Path) -> None:
        """Retarget a known rename while keeping ready thumbnails and user state."""

        old_rel = old_root.relative_to(self.library_root).as_posix()
        new_rel = new_root.relative_to(self.library_root).as_posix()
        if old_rel == "." or new_rel == "." or old_root == new_root:
            raise ValueError("Album rename requires two distinct library subdirectories")
        # '/' sorts immediately before '0'. These binary prefix bounds use the
        # path index and treat case and wildcard characters literally.
        source_bounds = (old_rel + "/", old_rel + "0")
        target_bounds = (new_rel + "/", new_rel + "0")
        suffix_start = len(old_rel) + 1
        now = _utc_ms()
        cache_moves: list[tuple[str, str]] = []
        cache_namespace = uuid.uuid4().hex
        with self.transaction(begin_mode="IMMEDIATE") as conn:
            if conn.execute(
                "SELECT 1 FROM assets WHERE rel >= ? AND rel < ? LIMIT 1",
                target_bounds,
            ).fetchone() is not None:
                raise ValueError(f"Destination album already has indexed assets: {new_root}")
            cached_rows = conn.execute(
                "SELECT rel, thumb_cache_key FROM assets WHERE rel >= ? AND rel < ? "
                "AND thumb_cache_key IS NOT NULL AND thumb_cache_key != ''",
                source_bounds,
            ).fetchall()
            for rel, old_key in cached_rows:
                destination = new_root / Path(rel).relative_to(old_rel)
                # Do not adopt a historical destination JPEG when the source
                # artifact is missing or locked. Each rename owns fresh keys.
                new_key = f"{thumbnail_cache_key(destination)}-{cache_namespace}"
                conn.execute("UPDATE assets SET thumb_cache_key = ? WHERE rel = ?", (new_key, rel))
                cache_moves.append((old_key, new_key))
            # Revisions include the absolute path. Forget that desired revision
            # without hiding ready rows; a worker establishes it on cache miss.
            conn.execute(
                "UPDATE assets SET rel = ? || substr(rel, ?), "
                "parent_album_path = ? || substr(parent_album_path, ?), "
                "thumb_revision = NULL, "
                "index_revision = COALESCE(index_revision, 0) + 1, index_updated_at_ms = ? "
                "WHERE rel >= ? AND rel < ?",
                (new_rel, suffix_start, new_rel, suffix_start, now, *source_bounds),
            )
            conn.execute(
                "UPDATE assets SET live_partner_rel = ? || substr(live_partner_rel, ?), "
                "index_revision = COALESCE(index_revision, 0) + 1, index_updated_at_ms = ? "
                "WHERE live_partner_rel >= ? AND live_partner_rel < ?",
                (new_rel, suffix_start, now, *source_bounds),
            )
            jobs = conn.execute(
                "SELECT job_id, asset_rel FROM metadata_write_jobs "
                "WHERE asset_rel >= ? AND asset_rel < ? AND status IN ('queued', 'writing', 'failed')",
                source_bounds,
            ).fetchall()
            for job_id, asset_rel in jobs:
                destination = new_root / Path(asset_rel).relative_to(old_rel)
                conn.execute(
                    "UPDATE metadata_write_jobs SET asset_rel = ?, asset_path = ?, "
                    "updated_at = ? WHERE job_id = ?",
                    (destination.relative_to(self.library_root).as_posix(),
                     str(destination), now, job_id),
                )
            # Completed album scans remain complete at their new paths. Keep
            # timestamps intact so the latest-job ordering does not change.
            old_scan_root = old_root.as_posix()
            conn.execute(
                "UPDATE scan_jobs SET root = ? || substr(root, ?) "
                "WHERE root = ? OR (root >= ? AND root < ?)",
                (new_root.as_posix(), len(old_scan_root) + 1, old_scan_root,
                 old_scan_root + "/", old_scan_root + "0"),
            )
        # Move only indexed artifacts, without traversing or decoding media.
        # Old path keys must be free for future files created at those paths.
        cache_dir = self.path.parent / "cache" / "thumbs"
        for old_key, new_key in cache_moves:
            try:
                thumbnail_cache_file_for_key(cache_dir, old_key).replace(
                    thumbnail_cache_file_for_key(cache_dir, new_key)
                )
            except FileNotFoundError:
                pass  # Disposable cache misses are repaired by thumbnail workers.
            except OSError as exc:
                logger.warning("Failed to retarget renamed thumbnail %s: %s", old_key, exc)

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

    def get_unpaired_live_candidates(
        self, content_ids: Iterable[str]
    ) -> dict[str, dict[str, Any]]:
        keys = sorted({key for value in content_ids if (key := normalise_content_id(value))})
        if not keys:
            return {}
        conn = self._db_manager.get_connection()
        should_close = conn != self._db_manager._conn
        previous_factory = conn.row_factory
        try:
            conn.row_factory = sqlite3.Row
            result: dict[str, dict[str, Any]] = {}
            for offset in range(0, len(keys), _SQLITE_PARAM_CHUNK_SIZE):
                chunk = keys[offset : offset + _SQLITE_PARAM_CHUNK_SIZE]
                placeholders = ", ".join("?" for _ in chunk)
                cursor = conn.execute(
                    f"""
                    SELECT * FROM assets WHERE content_id_key IN ({placeholders})
                        AND is_deleted = 0
                        AND COALESCE(live_partner_rel, '') = ''
                    """,
                    chunk,
                )
                for row in cursor:
                    result[str(row["rel"])] = self._db_row_to_dict(row)
            return result
        finally:
            conn.row_factory = previous_factory
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

    def update_face_statuses(
        self,
        asset_ids: Iterable[str],
        status: str,
        *,
        expected_statuses: tuple[str | None, ...] | None = None,
    ) -> None:
        """Update face status for matching assets in the expected state."""

        normalized = normalize_face_status(status)
        ids_list = [str(asset_id) for asset_id in asset_ids if asset_id]
        if normalized is None or not ids_list or expected_statuses == ():
            return

        status_guard = ""
        if expected_statuses is not None:
            matches = " OR ".join("face_status IS ?" for _ in expected_statuses)
            status_guard = f" AND ({matches})"
        for start in range(0, len(ids_list), 500):
            chunk = ids_list[start : start + 500]
            placeholders = ", ".join(["?"] * len(chunk))
            query = f"UPDATE assets SET face_status = ? WHERE id IN ({placeholders}){status_guard}"
            self._db_manager.execute_in_transaction(query, [normalized, *chunk, *(expected_statuses or ())])

    def reset_face_statuses_for_pipeline_upgrade(self) -> int:
        return self._reset_ai_statuses_for_pipeline_upgrade("face_status")

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

    def read_rows_by_pet_status(
        self,
        statuses: Iterable[str],
        *,
        limit: int | None = None,
    ) -> Iterator[Dict[str, Any]]:
        """Yield rows whose ``pet_status`` matches one of *statuses*."""

        normalized_statuses = [
            status
            for status in (normalize_pet_status(value) for value in statuses)
            if status is not None
        ]
        if not normalized_statuses:
            return

        conn = self._db_manager.get_connection()
        should_close = conn != self._db_manager._conn

        try:
            conn.row_factory = sqlite3.Row
            placeholders = ", ".join(["?"] * len(normalized_statuses))
            query = f"SELECT * FROM assets WHERE pet_status IN ({placeholders}) ORDER BY dt DESC, id DESC"
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

    def update_pet_status(self, asset_id: str, status: str) -> None:
        """Update the ``pet_status`` for a single asset row."""

        normalized = normalize_pet_status(status)
        if normalized is None or not asset_id:
            return
        self._db_manager.execute_in_transaction(
            "UPDATE assets SET pet_status = ? WHERE id = ?",
            (normalized, asset_id),
        )

    def update_pet_statuses(self, asset_ids: Iterable[str], status: str) -> None:
        """Update the ``pet_status`` for multiple assets."""

        normalized = normalize_pet_status(status)
        ids_list = [str(asset_id) for asset_id in asset_ids if asset_id]
        if normalized is None or not ids_list:
            return

        for start in range(0, len(ids_list), 500):
            chunk = ids_list[start : start + 500]
            placeholders = ", ".join(["?"] * len(chunk))
            query = f"UPDATE assets SET pet_status = ? WHERE id IN ({placeholders})"
            self._db_manager.execute_in_transaction(query, [normalized, *chunk])

    def reset_pet_statuses_for_pipeline_upgrade(self) -> int:
        return self._reset_ai_statuses_for_pipeline_upgrade("pet_status")

    def _reset_ai_statuses_for_pipeline_upgrade(self, column: str) -> int:
        if column not in {"face_status", "pet_status"}:
            raise ValueError("Unsupported AI status column")
        conn = self._db_manager.get_connection()
        should_close = conn != self._db_manager._conn
        try:
            cursor = conn.execute(
                f"""
                UPDATE assets
                SET {column} = 'pending'
                WHERE {column} = 'done'
                  AND COALESCE(CAST(media_type AS TEXT), '0') NOT IN ('1', 'video')
                  AND COALESCE(CAST(live_role AS INTEGER), 0) = 0
                  AND COALESCE(mime, '') NOT LIKE 'video/%'
                """
            )
            conn.commit()
            return int(cursor.rowcount or 0)
        finally:
            if should_close:
                conn.close()

    def count_by_pet_status(self) -> Dict[str, int]:
        """Return a status-to-count mapping for ``assets.pet_status``."""

        conn = self._db_manager.get_connection()
        should_close = conn != self._db_manager._conn

        try:
            cursor = conn.execute(
                "SELECT pet_status, COUNT(*) AS asset_count FROM assets GROUP BY pet_status"
            )
            counts: Dict[str, int] = {}
            for status, asset_count in cursor.fetchall():
                normalized = normalize_pet_status(status)
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
        """Yield the fields needed to build location assets from GPS rows."""
        conn = self._db_manager.get_connection()
        should_close = (conn != self._db_manager._conn)

        try:
            # Older indexes can contain optional location/media fields outside
            # the current schema. Keep those without reading thumbnail blobs.
            legacy = {"metadata", "live_photo_group_id", "type", "is_image", "is_video"}
            columns = [
                "rel", "id", "parent_album_path", "mime", "gps", "location",
                "live_role", "live_partner_rel", "still_image_time", "dur",
            ]
            columns.extend(
                row[1]
                for row in conn.execute("PRAGMA table_info(assets)")
                if row[1] in legacy
            )
            query = f"SELECT {', '.join(columns)} FROM assets WHERE gps IS NOT NULL"
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
            "face_status", "thumbnail_state", "thumb_cache_key", "thumb_revision",
            "thumb_refresh_pending",
            "index_revision", "micro_thumbnail", "source_mtime_ns", "image_orientation"
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

    def latest_scan_job(
        self,
        *,
        root: str,
        scope: str | None = None,
    ) -> Dict[str, Any] | None:
        """Return the newest scan job matching *root* and optional *scope*."""

        sql, params = QueryBuilder.build_latest_scan_job(root, scope)
        conn = self._db_manager.get_connection()
        should_close = conn != self._db_manager._conn
        try:
            conn.row_factory = sqlite3.Row
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row is not None else None
        finally:
            if should_close:
                conn.close()

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
        with self._collection_snapshot() as (conn, revision, cache):
            rows = [self._db_row_to_dict(row) for row in conn.execute(sql, params)]
            next_cursor = self._page_cursor_from_row(query, rows[-1]) if rows else None
            result = PageResult(
                rows=rows,
                next_cursor=next_cursor,
                total_count=self._snapshot_count(query, cache),
                collection_revision=revision,
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
        with self._collection_snapshot() as (conn, revision, cache):
            rows, sql, params = self._read_collection_window_rows(conn, cache, query, first, limit)
            result = WindowResult(
                first=first,
                rows=rows,
                total_count=self._snapshot_count(query, cache),
                collection_revision=revision,
                order_revision=cache.order_revision,
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

    def read_gallery_collection_window(
        self,
        query: CollectionQuery,
        first: int,
        limit: int,
    ) -> WindowResult:
        """Return a Gallery-only window without wide metadata columns."""

        return self.read_gallery_collection_windows(query, ((first, limit),))[0]

    def read_gallery_collection_windows(
        self,
        query: CollectionQuery,
        ranges: tuple[tuple[int, int], ...],
    ) -> tuple[WindowResult, ...]:
        """Read disjoint visible ranges from one SQLite snapshot."""

        with self._collection_snapshot() as (conn, revision, cache):
            count = self._snapshot_count(query, cache)
            select_clause = f"SELECT {_GALLERY_COLUMNS}"
            windows = []
            for first, limit in ranges:
                first = max(0, int(first))
                limit = max(0, int(limit))
                rows, _, _ = self._read_collection_window_rows(
                    conn, cache, query, first, limit, select_clause=select_clause,
                )
                windows.append(WindowResult(first, rows, count, revision, cache.order_revision))
            return tuple(windows)

    def read_thumbnail_hint_window(
        self,
        query: CollectionQuery,
        first: int,
        limit: int,
    ) -> WindowResult:
        """Return a count-free projection used by guard thumbnail reads."""

        first = max(0, int(first))
        limit = max(0, int(limit))
        with self._collection_snapshot() as (conn, revision, cache):
            select_clause = (
                "SELECT rel, thumb_cache_key, thumbnail_state, "
                "CASE WHEN NOT thumb_refresh_pending THEN thumb_revision END AS thumb_revision"
            )
            rows, _, _ = self._read_collection_window_rows(
                conn, cache, query, first, limit, select_clause=select_clause,
                remember=False,
            )
            return WindowResult(
                first=first,
                rows=rows,
                total_count=-1,
                collection_revision=revision,
                order_revision=cache.order_revision,
            )

    def _read_collection_window_rows(
        self,
        conn: sqlite3.Connection,
        cache: _CollectionCache,
        query: CollectionQuery,
        first: int,
        limit: int,
        *,
        select_clause: str = "SELECT *",
        remember: bool = True,
    ) -> tuple[list[dict[str, Any]], str | None, list[Any]]:
        cursor = None
        offset = first
        if first > _DEEP_OFFSET_LIMIT:
            cursor = self._cursor_for_collection_offset(conn, cache, query, first)
            if cursor is None:
                return [], None, []
            offset = 0
        sql, params = QueryBuilder.build_collection_query(
            query,
            select_clause=select_clause,
            cursor=cursor,
            limit=limit,
            offset=offset,
        )
        rows = [self._db_row_to_dict(row) for row in conn.execute(sql, params)]
        if remember and rows:
            anchor = self._page_cursor_from_row(query, rows[-1])
            if anchor is not None:
                self._remember_collection_anchor(cache, query, first + len(rows), anchor)
        return rows, sql, params

    def read_thumbnail_backfill_candidates(
        self,
        query: CollectionQuery,
        first: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Return stale thumbnail rows matching the same collection scope."""

        first = max(0, int(first))
        limit = max(0, int(limit))
        if limit <= 0:
            return []

        stale_query = self._collection_query_with_thumbnail_state(query, "stale")
        with self._collection_snapshot() as (conn, _revision, cache):
            window_rows, _, _ = self._read_collection_window_rows(
                conn, cache, query, first, limit,
            )
            if not window_rows:
                sql, params = QueryBuilder.build_collection_query(
                    stale_query,
                    limit=limit,
                    offset=first,
                )
                return [self._db_row_to_dict(row) for row in conn.execute(sql, params)]

            sql, params = self._build_stale_window_query(
                stale_query,
                window_rows,
                first=first,
                limit=limit,
            )
            if sql is None:
                return []
            candidates = [self._db_row_to_dict(row) for row in conn.execute(sql, params)]
            if (
                query.min_thumbnail_state == "ready"
                and len(candidates) < limit
                and self._collection_window_reaches_end(conn, query, first, window_rows)
            ):
                candidates.extend(
                    self._stale_candidates_after_window(
                        conn,
                        stale_query,
                        window_rows,
                        limit=limit - len(candidates),
                        exclude_rels={
                            str(row.get("rel"))
                            for row in candidates
                            if row.get("rel") is not None
                        },
                    )
                )
            return candidates

    @staticmethod
    def _collection_window_reaches_end(
        conn: sqlite3.Connection,
        query: CollectionQuery,
        first: int,
        window_rows: list[dict[str, Any]],
    ) -> bool:
        sql, params = QueryBuilder.build_collection_query(
            query,
            select_clause="SELECT COUNT(*)",
            include_order=False,
        )
        row = conn.execute(sql, params).fetchone()
        total = int(row[0] if row else 0)
        return first + len(window_rows) >= total

    def _stale_candidates_after_window(
        self,
        conn: sqlite3.Connection,
        stale_query: CollectionQuery,
        window_rows: list[dict[str, Any]],
        *,
        limit: int,
        exclude_rels: set[str],
    ) -> list[dict[str, Any]]:
        if limit <= 0 or not window_rows:
            return []
        sort_col = QueryBuilder._collection_sort_column(stale_query)
        bottom_bound = self._collection_sort_bound(window_rows[-1], sort_col)
        if bottom_bound is None:
            return []

        where_clauses, params = QueryBuilder.build_collection_where(stale_query)
        clause, clause_params = self._collection_range_clause(
            sort_col,
            bottom_bound,
            stale_query.sort_direction.value,
            lower=False,
        )
        where_clauses.append(clause)
        params.extend(clause_params)
        sql = "SELECT * FROM assets WHERE " + " AND ".join(where_clauses)
        sql += " " + QueryBuilder.build_collection_order(stale_query)
        sql += " LIMIT ?"
        params.append(limit + len(exclude_rels))
        rows = [self._db_row_to_dict(row) for row in conn.execute(sql, params)]
        return [
            row
            for row in rows
            if str(row.get("rel")) not in exclude_rels
        ][:limit]

    def _build_stale_window_query(
        self,
        stale_query: CollectionQuery,
        window_rows: list[dict[str, Any]],
        *,
        first: int,
        limit: int,
    ) -> tuple[str | None, list[Any]]:
        if not window_rows:
            return None, []

        sort_col = QueryBuilder._collection_sort_column(stale_query)
        top_bound = self._collection_sort_bound(window_rows[0], sort_col)
        bottom_bound = self._collection_sort_bound(window_rows[-1], sort_col)
        if top_bound is None or bottom_bound is None:
            return None, []

        where_clauses, params = QueryBuilder.build_collection_where(stale_query)
        if first > 0:
            clause, clause_params = self._collection_range_clause(
                sort_col,
                top_bound,
                stale_query.sort_direction.value,
                lower=False,
            )
            where_clauses.append(clause)
            params.extend(clause_params)

        clause, clause_params = self._collection_range_clause(
            sort_col,
            bottom_bound,
            stale_query.sort_direction.value,
            lower=True,
        )
        where_clauses.append(clause)
        params.extend(clause_params)

        sql = "SELECT * FROM assets WHERE " + " AND ".join(where_clauses)
        sql += " " + QueryBuilder.build_collection_order(stale_query)
        sql += " LIMIT ?"
        params.append(limit)
        return sql, params

    @staticmethod
    def _collection_sort_bound(
        row: dict[str, Any],
        sort_col: str,
    ) -> tuple[Any, str, str] | None:
        sort_value = row.get(sort_col)
        asset_id = row.get("id")
        asset_rel = row.get("rel")
        if sort_value is None or asset_id is None or asset_rel is None:
            return None
        return sort_value, str(asset_id), str(asset_rel)

    @staticmethod
    def _collection_range_clause(
        sort_col: str,
        bound: tuple[Any, str, str],
        direction: str,
        *,
        lower: bool,
    ) -> tuple[str, list[Any]]:
        sort_value, asset_id, asset_rel = bound
        if direction == "ASC":
            operator = "<" if lower else ">"
            id_operator = "<" if lower else ">"
            rel_operator = "<=" if lower else ">="
        else:
            operator = ">" if lower else "<"
            id_operator = ">" if lower else "<"
            rel_operator = ">=" if lower else "<="
        return (
            f"({sort_col} {operator} ? OR ({sort_col} = ? AND "
            f"(id {id_operator} ? OR (id = ? AND rel {rel_operator} ?))))",
            [sort_value, sort_value, asset_id, asset_id, asset_rel],
        )

    @staticmethod
    def _collection_query_with_thumbnail_state(
        query: CollectionQuery,
        state: str,
    ) -> CollectionQuery:
        return CollectionQuery(
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
            min_thumbnail_state=state,
        )

    def update_thumbnail_ready(
        self,
        rel: str,
        *,
        micro_thumbnail: bytes | None = None,
        thumb_cache_key: str | None = None,
        error: str | None = None,
        expected_revision: str | None = None,
        expected_asset_id: str | None = None,
        expected_index_revision: int | None = None,
    ) -> bool:
        """Publish readiness only for the currently desired render revision."""

        normalized_rel = unicodedata.normalize("NFC", str(rel))
        guards: list[str] = []
        guard_params: list[str | int] = []
        if expected_revision is not None:
            guards.append(" AND (thumb_revision = ? OR thumb_revision IS NULL)")
            guard_params.append(expected_revision)
        else:
            guards.append(" AND thumb_refresh_pending = 0")
        if expected_asset_id is not None:
            guards.append(" AND COALESCE(id, '') = ?")
            guard_params.append(expected_asset_id)
        if expected_index_revision is not None:
            guards.append(" AND COALESCE(index_revision, 0) = ?")
            guard_params.append(expected_index_revision)
        state_guard = "".join(guards)
        if error:
            error_text = str(error)
            changed = self._db_manager.execute_in_transaction(
                f"""
                UPDATE assets
                SET thumbnail_state = 'failed',
                    thumb_error = ?,
                    thumb_revision = COALESCE(?, thumb_revision),
                    thumb_updated_at = ?,
                    index_revision = COALESCE(index_revision, 0) + 1
                WHERE rel = ?{state_guard}
                    AND COALESCE(TRIM(thumb_cache_key), '') = ''
                    AND (
                        thumbnail_state != 'failed'
                        OR COALESCE(thumb_error, '') != ?
                    )
                """,
                [error_text, expected_revision, _utc_ms(), normalized_rel]
                + guard_params
                + [error_text],
            )
        else:
            if not str(thumb_cache_key or "").strip():
                raise ValueError("ready thumbnails require thumb_cache_key")
            change_guards = [
                "thumbnail_state != 'ready'", "thumb_error IS NOT NULL", "thumb_refresh_pending = 1",
            ]
            change_params: list[Any] = []
            if micro_thumbnail is not None:
                change_guards.append("micro_thumbnail IS NOT ?")
                change_params.append(micro_thumbnail)
            if thumb_cache_key is not None:
                change_guards.append("COALESCE(thumb_cache_key, '') != ?")
                change_params.append(thumb_cache_key)
            if expected_revision is not None:
                change_guards.append("thumb_revision IS NOT ?")
                change_params.append(expected_revision)
            changed = self._db_manager.execute_in_transaction(
                f"""
                UPDATE assets
                SET thumbnail_state = 'ready',
                    thumb_refresh_pending = 0,
                    micro_thumbnail = COALESCE(?, micro_thumbnail),
                    thumb_cache_key = COALESCE(?, thumb_cache_key),
                    thumb_revision = COALESCE(?, thumb_revision),
                    thumb_error = NULL,
                    thumb_updated_at = ?,
                    index_revision = COALESCE(index_revision, 0) + 1
                WHERE rel = ?{state_guard}
                    AND ({' OR '.join(change_guards)})
                """,
                [
                    micro_thumbnail,
                    thumb_cache_key,
                    expected_revision,
                    _utc_ms(),
                    normalized_rel,
                ]
                + guard_params
                + change_params,
            )
        return bool(changed)

    def mark_thumbnail_stale(self, rel: str, *, desired_revision: str) -> bool:
        """Select a desired revision without deleting the last complete artifact."""

        normalized_rel = unicodedata.normalize("NFC", str(rel))
        revision = str(desired_revision).strip()
        if not revision:
            raise ValueError("stale thumbnails require a desired revision")
        changed = self._db_manager.execute_in_transaction(
            """
            UPDATE assets
            SET thumbnail_state = 'stale',
                thumb_refresh_pending = 0,
                micro_thumbnail = NULL,
                thumb_revision = ?,
                thumb_error = NULL,
                thumb_updated_at = ?,
                index_revision = COALESCE(index_revision, 0) + 1
            WHERE rel = ?
                AND (
                    thumbnail_state != 'stale'
                    OR COALESCE(thumb_revision, '') != ?
                )
            """,
            [revision, _utc_ms(), normalized_rel, revision],
        )
        return bool(changed)

    def mark_thumbnail_refresh_pending(
        self,
        rel: str,
        *,
        desired_revision: str,
        cache_key: str,
        expected_asset_id: str,
        expected_index_revision: int,
    ) -> bool:
        """Select replacement inputs while keeping the verified artifact drawable."""

        return bool(self._db_manager.execute_in_transaction(
            """
            UPDATE assets
            SET thumb_refresh_pending = 1, thumb_revision = ?, thumb_error = NULL,
                index_revision = COALESCE(index_revision, 0) + 1
            WHERE rel = ? AND id = ? AND COALESCE(index_revision, 0) = ?
                AND thumbnail_state = 'ready' AND thumb_cache_key = ?
            """,
            (desired_revision, rel, expected_asset_id, expected_index_revision, cache_key),
        ))

    def _cursor_for_collection_offset(
        self,
        conn: sqlite3.Connection,
        cache: _CollectionCache,
        query: CollectionQuery,
        offset: int,
    ) -> PageCursor | None:
        """Return the keyset cursor immediately before *offset* without deep OFFSET."""

        if offset <= 0:
            return None

        with self._collection_cache_lock:
            anchors = cache.anchors.get(query, {0: None})
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
            cursor = self._page_cursor_from_row(query, dict(rows[-1]))
            if cursor is not None:
                self._remember_collection_anchor(cache, query, anchor_index, cursor)

            if len(rows) < step:
                return None

        return cursor

    def find_row_by_path(self, query: CollectionQuery, path: Path) -> int | None:
        """Return a path's row number within *query* without scanning rows in Python."""

        started = monotonic_ms()
        outcome = "missing"
        try:
            rel = self._library_relative_path(path)
            row = self.get_rows_by_rels([rel]).get(rel)
            if row is None:
                return None
            asset_id = str(row.get("id") or "")
            asset_rel = str(row.get("rel") or "")
            sort_col = QueryBuilder._collection_sort_column(query)
            sort_value = row.get(sort_col)
            if sort_value is None and sort_col == "sort_ts":
                sort_value = row.get("ts")
            if not asset_id or not asset_rel or sort_value is None:
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
                    before_where.append(
                        f"({sort_col} < ? OR ({sort_col} = ? AND "
                        "(id < ? OR (id = ? AND rel < ?))))"
                    )
                else:
                    before_where.append(
                        f"({sort_col} > ? OR ({sort_col} = ? AND "
                        "(id > ? OR (id = ? AND rel > ?))))"
                    )
                before_params.extend(
                    [sort_value, sort_value, asset_id, asset_id, asset_rel]
                )
                before_sql = "SELECT COUNT(*) FROM assets WHERE " + " AND ".join(before_where)
                before = conn.execute(before_sql, before_params).fetchone()
                resolved_row = int(before[0] if before else 0)
                outcome = "resolved"
                return resolved_row
            finally:
                if should_close:
                    conn.close()
        except sqlite3.Error:
            outcome = "sqlite_error"
            raise
        except Exception:
            outcome = "error"
            raise
        finally:
            emit_perf_event(
                "find_row_by_path_finished",
                elapsed_ms=round(monotonic_ms() - started, 3),
                outcome=outcome,
                on_main_thread=threading.current_thread() is threading.main_thread(),
                thread=threading.current_thread().name,
            )

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
        has_gps = 1 if gps is not None else 0
        with self.transaction() as conn:
            update_parts = ["gps = ?", "has_gps = ?", "location = ?"]
            params: list[Any] = [gps_payload, has_gps, location]

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

    def update_live_roles(self, updates: Iterable[tuple[str, int, str | None]]) -> None:
        """Update specific pairs and clear affected former partners."""

        params = [(role, partner, rel, role, partner) for rel, role, partner in updates]
        if not params:
            return
        with self.transaction() as conn:
            conn.executemany(
                """
                UPDATE assets
                SET live_role = ?, live_partner_rel = ?,
                    index_revision = COALESCE(index_revision, 0) + 1
                WHERE rel = ? AND (live_role IS NOT ? OR live_partner_rel IS NOT ?)
                """,
                params,
            )

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

    def collection_revision(self) -> int:
        """Return the current committed collection revision for notifications."""

        conn = self._db_manager.get_connection()
        try:
            return int(conn.execute("SELECT revision FROM collection_versions").fetchone()[0])
        finally:
            if conn != self._db_manager._conn:
                conn.close()

    @contextmanager
    def _collection_snapshot(self) -> Iterator[tuple[sqlite3.Connection, int, _CollectionCache]]:
        # A caller's write transaction can roll back and reuse a version. Its
        # counts and cursors must stay private to this read.
        shared = self._db_manager._conn is None
        with self.transaction(begin_mode="DEFERRED") as conn:
            conn.row_factory = sqlite3.Row
            revision, order_revision = conn.execute(
                "SELECT revision, order_revision FROM collection_versions"
            ).fetchone()
            with self._collection_cache_lock:
                current = self._collection_cache
                if shared and order_revision >= current.order_revision:
                    if order_revision > current.order_revision:
                        self._collection_cache = _CollectionCache(order_revision)
                    cache = self._collection_cache
                else:
                    cache = _CollectionCache(order_revision)
            yield conn, revision, cache

    def _snapshot_count(self, query: CollectionQuery, cache: _CollectionCache) -> int:
        with self._collection_cache_lock:
            count = cache.counts.get(query)
        if count is None:
            count = self.count_collection(query)
            with self._collection_cache_lock:
                cache.counts[query] = count
        return count

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

    def _remember_collection_anchor(
        self,
        cache: _CollectionCache,
        query: CollectionQuery,
        index: int,
        cursor: PageCursor,
    ) -> None:
        with self._collection_cache_lock:
            anchors = cache.anchors.setdefault(query, {0: None})
            anchors[index] = cursor
            if len(anchors) > _MAX_COLLECTION_ANCHORS_PER_QUERY:
                oldest = min(index for index in anchors if index > 0)
                del anchors[oldest]

    @staticmethod
    def _page_cursor_from_row(
        query: CollectionQuery,
        row: dict[str, Any],
    ) -> PageCursor | None:
        asset_id = row.get("id")
        asset_rel = row.get("rel")
        sort_ts = row.get("sort_ts") if row.get("sort_ts") is not None else row.get("ts")
        if asset_id is None or asset_rel is None or sort_ts is None:
            return None
        sort_col = QueryBuilder._collection_sort_column(query)
        sort_value = row.get(sort_col)
        if sort_value is None and sort_col == "sort_ts":
            sort_value = sort_ts
        cursor_sort_value = None if sort_col == "sort_ts" else sort_value
        return PageCursor(
            sort_ts=int(sort_ts),
            asset_id=str(asset_id),
            sort_value=cursor_sort_value,
            asset_rel=str(asset_rel),
        )

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

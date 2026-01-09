"""High-level application facade."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import os
import sqlite3
from typing import Callable, Dict, List, Optional, Tuple

from .cache.index_store import IndexStore
from .cache.lock import FileLock
from .config import (
    DEFAULT_EXCLUDE,
    DEFAULT_INCLUDE,
    WORK_DIR_NAME,
    RECENTLY_DELETED_DIR_NAME,
)
from .core.pairing import pair_live
from .models.album import Album
from .models.types import LiveGroup
from .errors import IndexCorruptedError, ManifestInvalidError
from .utils.jsonio import read_json, write_json
from .utils.logging import get_logger

LOGGER = get_logger()


def _is_index_recoverable_error(exc: Exception) -> bool:
    """Return ``True`` when *exc* stems from recoverable index state."""

    return isinstance(exc, (sqlite3.Error, IndexCorruptedError, ManifestInvalidError))


def _compute_album_path(root: Path, library_root: Optional[Path]) -> Optional[str]:
    """Return library-relative album path when root is inside library_root.

    Uses os.path.relpath to tolerate case differences and symlinks. Returns
    None when outside the library or when pointing at the library root.
    """
    if not library_root:
        return None
    try:
        rel = Path(os.path.relpath(root, library_root)).as_posix()
    except (ValueError, OSError):
        return None

    if rel.startswith(".."):
        return None
    if rel in (".", ""):
        return None
    # Debug trace to help diagnose album filtering issues
    LOGGER.debug(
        "Computed album path: root=%s, library_root=%s, rel=%s",
        root,
        library_root,
        rel,
    )
    return rel


def open_album(
    root: Path,
    autoscan: bool = True,
    library_root: Optional[Path] = None,
    *,
    hydrate_index: bool = True,
) -> Album:
    """Open an album directory, scanning and pairing as required.
    
    Args:
        root: The album root directory.
        autoscan: Whether to scan automatically if the index is empty.
        library_root: If provided, use this as the database root (global database).
                     If None, defaults to root for backward compatibility.
        hydrate_index: When ``False``, skip eager index hydration to avoid blocking
                       the caller; still performs a lightweight emptiness check and
                       optional autoscan.
    """

    album = Album.open(root)
    
    # Use library_root for global database if provided, otherwise use album root
    db_root = library_root if library_root else root
    store = IndexStore(db_root)
    
    # If using global DB, we need to filter by album path
    album_path = _compute_album_path(root, library_root)
    
    # Hydrated index rows when available; ``None`` when the lazy path skips loading.
    rows: list[dict] | None = None
    # Read rows from the database, filtered by album if using global DB
    if hydrate_index:
        if album_path:
            rows = list(store.read_album_assets(album_path, include_subalbums=True))
        else:
            rows = list(store.read_all())
    else:
        try:
            existing_count = store.count(
                filter_hidden=True,
                album_path=album_path,
                include_subalbums=True,
            )
        except Exception as exc:
            if not _is_index_recoverable_error(exc):
                raise
            LOGGER.warning(
                "Index count failed for %s [%s]; assuming empty index: %s",
                root,
                type(exc).__name__,
                exc,
            )
            existing_count = 0

        if existing_count == 0 and autoscan:
            include = album.manifest.get("filters", {}).get("include", DEFAULT_INCLUDE)
            exclude = album.manifest.get("filters", {}).get("exclude", DEFAULT_EXCLUDE)
            from .io.scanner import scan_album

            rows = list(scan_album(root, include, exclude))
            
            # If using global DB, convert to library-relative paths before writing
            if library_root and album_path:
                for row in rows:
                    if "rel" in row:
                        row["rel"] = f"{album_path}/{row['rel']}"
            
            store.write_rows(rows)
        elif existing_count == 0:
            # Preserve legacy behavior where an empty index results in empty link payloads.
            rows = []
    
    # For links and sync_favorites, we need album-relative rows
    # If using global DB, adjust rel paths for _ensure_links
    if rows is not None:
        if album_path:
            # Create album-relative rows for _ensure_links
            album_rows = []
            prefix = album_path + "/"
            for row in rows:
                rel = row.get("rel", "")
                if rel.startswith(prefix):
                    adj_row = dict(row)
                    adj_row["rel"] = rel[len(prefix):]
                    album_rows.append(adj_row)
                elif "/" not in rel:
                    # File directly in album root
                    album_rows.append(row)
            _ensure_links(root, album_rows, library_root=library_root)
        else:
            _ensure_links(root, rows, library_root=library_root)
    
    # Keep favorites aligned with the manifest even when we skip hydration.
    try:
        store.sync_favorites(album.manifest.get("featured", []))
    except Exception as exc:
        if not _is_index_recoverable_error(exc):
            raise
        LOGGER.warning(
            "sync_favorites failed for %s [%s]: %s",
            root,
            type(exc).__name__,
            exc,
        )
    
    return album


def _ensure_links(
    root: Path, rows: List[dict], library_root: Optional[Path] = None
) -> None:
    """Ensure links.json and DB are synchronized with the given rows.
    
    Args:
        root: The album root directory.
        rows: List of asset rows (with album-relative paths).
        library_root: If provided, use this as the database root.
    """
    work_dir = root / WORK_DIR_NAME
    links_path = work_dir / "links.json"
    groups, payload = _compute_links_payload(rows)

    # Always sync the DB with the live roles derived from the link structure,
    # even if the payload matches. This ensures that in migration scenarios
    # (where links.json exists but new DB columns for live roles have been added),
    # the DB is updated for consistency.

    if links_path.exists():
        try:
            existing: Dict[str, object] = read_json(links_path)
        except ManifestInvalidError:
            existing = {}
        if existing == payload:
            # Sync DB anyway to ensure migration/consistency
            _sync_live_roles_to_db(root, groups, library_root=library_root)
            return

    LOGGER.info("Updating links.json for %s", root)
    _write_links(root, payload)
    # _write_links writes the file, but we also need to update the DB
    _sync_live_roles_to_db(root, groups, library_root=library_root)


def _compute_links_payload(rows: List[dict]) -> tuple[List[LiveGroup], Dict[str, object]]:
    groups = pair_live(rows)
    payload: Dict[str, object] = {
        "schema": "iPhoto/links@1",
        "live_groups": [asdict(group) for group in groups],
        "clips": [],
    }
    return groups, payload


def _write_links(root: Path, payload: Dict[str, object]) -> None:
    work_dir = root / WORK_DIR_NAME
    with FileLock(root, "links"):
        write_json(work_dir / "links.json", payload, backup_dir=work_dir / "manifest.bak")


def _sync_live_roles_to_db(
    root: Path, groups: List[LiveGroup], library_root: Optional[Path] = None
) -> None:
    """Propagate live photo roles from computed groups to the IndexStore.
    
    Args:
        root: The album root directory.
        groups: List of LiveGroup objects to sync.
        library_root: If provided, use this as the database root (global database).
    """
    updates: List[Tuple[str, int, Optional[str]]] = []
    
    # Compute album path for library-relative paths
    album_prefix = ""
    if library_root:
        rel = _compute_album_path(root, library_root)
        if rel:
            album_prefix = f"{rel}/"

    for group in groups:
        # Still image: Role 0 (Primary), Partner = Motion
        if group.still:
            still_rel = f"{album_prefix}{group.still}" if album_prefix else group.still
            motion_rel = f"{album_prefix}{group.motion}" if album_prefix and group.motion else group.motion
            updates.append((still_rel, 0, motion_rel))

        # Motion component: Role 1 (Hidden), Partner = Still
        if group.motion:
            motion_rel = f"{album_prefix}{group.motion}" if album_prefix else group.motion
            still_rel = f"{album_prefix}{group.still}" if album_prefix and group.still else group.still
            updates.append((motion_rel, 1, still_rel))

    db_root = library_root if library_root else root
    IndexStore(db_root).apply_live_role_updates(updates)


def _normalise_rel_key(rel_value: object) -> Optional[str]:
    """Return a POSIX-formatted representation of *rel_value* when possible.

    The index uses the relative path under the album root as its stable
    identifier.  Callers occasionally pass :class:`pathlib.Path` instances while
    other code paths hand over plain strings.  Normalising via
    :meth:`Path.as_posix` collapses both representations into a single canonical
    form so lookups remain stable regardless of the originating caller or the
    underlying operating system.
    """

    if isinstance(rel_value, str) and rel_value:
        return Path(rel_value).as_posix()
    if isinstance(rel_value, Path):
        return rel_value.as_posix()
    if rel_value:
        return Path(str(rel_value)).as_posix()
    return None


def load_incremental_index_cache(
    root: Path, library_root: Optional[Path] = None
) -> Dict[str, dict]:
    """Load the existing index into a dictionary for incremental scanning.

    This helper encapsulates the logic of reading the index store and normalizing
    keys, allowing it to be reused by both the main application facade and
    background workers.
    
    Args:
        root: The album root directory.
        library_root: If provided, use this as the database root (global database).
    """
    db_root = library_root if library_root else root
    store = IndexStore(db_root)
    existing_index = {}
    
    # If using global DB, filter by album path
    album_path = _compute_album_path(root, library_root)
    
    try:
        if album_path:
            rows = store.read_album_assets(album_path, include_subalbums=True)
        else:
            rows = store.read_all()
        for row in rows:
            rel_key = _normalise_rel_key(row.get("rel"))
            if rel_key:
                existing_index[rel_key] = row
    except IndexCorruptedError:
        pass
    return existing_index


def _update_index_snapshot(
    root: Path,
    materialised_rows: List[dict],
    library_root: Optional[Path] = None,
) -> None:
    """Apply *materialised_rows* to the global database using additive-only updates.

    This function implements **Constraint #4: Additive-Only "Fact Supplementation"**:
    - Scanning is for discovering facts, not removing them
    - Files not found during a partial scan are NOT deleted from the database
    - Deletion is a separate lifecycle event and never occurs during scan
    
    The function uses idempotent upsert operations to ensure duplicate scans
    don't create duplicate data (Constraint #3).
    
    Args:
        root: The album root directory.
        materialised_rows: List of rows to update/insert.
        library_root: If provided, use this as the database root (global database).
    """
    db_root = library_root if library_root else root
    store = IndexStore(db_root)

    corrupted_during_read = False
    try:
        # Just verify we can read the database
        list(store.read_all())
    except IndexCorruptedError:
        corrupted_during_read = True

    fresh_rows: Dict[str, dict] = {}
    for row in materialised_rows:
        rel_key = _normalise_rel_key(row.get("rel"))
        if rel_key is None:
            continue
        fresh_rows[rel_key] = row

    materialised_snapshot = list(fresh_rows.values())

    if corrupted_during_read:
        # On corruption, write all rows to rebuild the database
        store.write_rows(materialised_snapshot)
        return

    if not fresh_rows:
        return

    # Additive-only: only append new/updated rows, never delete
    # append_rows uses INSERT OR REPLACE which is idempotent
    try:
        store.append_rows(materialised_snapshot)
    except IndexCorruptedError:
        store.write_rows(materialised_snapshot)


def rescan(
    root: Path,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    library_root: Optional[Path] = None,
) -> List[dict]:
    """Rescan the album and return the fresh index rows.
    
    Args:
        root: The album root directory.
        progress_callback: Optional callback for progress updates.
        library_root: If provided, use this as the database root (global database).
    """
    db_root = library_root if library_root else root
    store = IndexStore(db_root)
    
    # Compute album path for library-relative paths
    album_path = _compute_album_path(root, library_root)

    # ``original_rel_path`` is only populated for assets in the shared trash
    # album.  Rescanning that directory must therefore preserve the existing
    # mapping so the restore feature still knows where each item originated.
    is_recently_deleted = root.name == RECENTLY_DELETED_DIR_NAME
    preserved_fields = (
        "original_rel_path",
        "original_album_id",
        "original_album_subpath",
    )
    preserved_restore_rows: Dict[str, dict] = {}
    if is_recently_deleted:
        try:
            for row in store.read_all():
                rel_value = row.get("rel")
                if not isinstance(rel_value, str):
                    continue
                if not any(field in row for field in preserved_fields):
                    continue
                rel_key = Path(rel_value).as_posix()
                preserved_restore_rows[rel_key] = row
        except IndexCorruptedError:
            # A corrupted index means we cannot recover historical restore
            # targets.  Emit a warning and continue with a clean rescan so new
            # trash entries still receive restore metadata.
            LOGGER.warning("Unable to read previous trash index for %s", root)

    album = Album.open(root)
    include = album.manifest.get("filters", {}).get("include", DEFAULT_INCLUDE)
    exclude = album.manifest.get("filters", {}).get("exclude", DEFAULT_EXCLUDE)
    from .io.scanner import scan_album

    # Load existing index for incremental scanning
    existing_index = load_incremental_index_cache(root, library_root=library_root)

    rows = list(scan_album(
        root,
        include,
        exclude,
        existing_index=existing_index,
        progress_callback=progress_callback
    ))
    
    # If using global DB, convert to library-relative paths
    if album_path:
        for row in rows:
            if "rel" in row:
                row["rel"] = f"{album_path}/{row['rel']}"
    
    if is_recently_deleted and preserved_restore_rows:
        for new_row in rows:
            rel_value = new_row.get("rel")
            if not isinstance(rel_value, str):
                continue
            rel_key = Path(rel_value).as_posix()
            cached = preserved_restore_rows.get(rel_key)
            if not cached:
                continue
            for field in preserved_fields:
                if field in cached and field not in new_row:
                    new_row[field] = cached[field]

    _update_index_snapshot(root, rows, library_root=library_root)
    
    # For _ensure_links, we need album-relative rows
    if album_path:
        prefix = album_path + "/"
        album_rows = []
        for row in rows:
            rel = row.get("rel", "")
            if rel.startswith(prefix):
                adj_row = dict(row)
                adj_row["rel"] = rel[len(prefix):]
                album_rows.append(adj_row)
            elif "/" not in rel:
                album_rows.append(row)
        _ensure_links(root, album_rows, library_root=library_root)
    else:
        _ensure_links(root, rows, library_root=library_root)
    
    store.sync_favorites(album.manifest.get("featured", []))
    return rows


def scan_specific_files(
    root: Path, files: List[Path], library_root: Optional[Path] = None
) -> None:
    """Generate index rows for specific files and merge them into the index.

    This helper avoids a full directory scan, enabling efficient incremental
    updates during batch import operations.
    
    Args:
        root: The album root directory.
        files: List of files to scan.
        library_root: If provided, use this as the database root (global database).
    """
    from .io.scanner import process_media_paths

    # We need to separate images and videos for process_media_paths, but
    # since we already have the specific file list, we can just split them manually
    # based on extensions or just pass them all to get_metadata_batch which handles mixed types.
    # However, process_media_paths expects separate lists.
    # Let's reuse gather_media_paths logic but applied to our specific list.

    # Reuse scanner constants if possible, or just rely on extension check.
    # Since we can't easily import private constants from scanner, we'll try to use
    # public API or logic similar to gather_media_paths but for a fixed list.
    # Actually, process_media_paths takes image_paths and video_paths.

    # We will just categorize them here.
    image_paths: List[Path] = []
    video_paths: List[Path] = []

    # Minimal set of extensions matching scanner.py
    _IMAGE_EXTENSIONS = {".heic", ".heif", ".heifs", ".heicf", ".jpg", ".jpeg", ".png"}
    _VIDEO_EXTENSIONS = {".mov", ".mp4", ".m4v", ".qt"}

    for f in files:
        if f.suffix.lower() in _IMAGE_EXTENSIONS:
            image_paths.append(f)
        elif f.suffix.lower() in _VIDEO_EXTENSIONS:
            video_paths.append(f)

    rows = list(process_media_paths(root, image_paths, video_paths))
    
    # If using global DB, convert to library-relative paths
    album_path = _compute_album_path(root, library_root)
    
    if album_path:
        for row in rows:
            if "rel" in row:
                row["rel"] = f"{album_path}/{row['rel']}"

    db_root = library_root if library_root else root
    store = IndexStore(db_root)
    # We use append_rows which handles merging/updating based on 'rel' key
    # It also handles locking safely.
    store.append_rows(rows)


def pair(root: Path, library_root: Optional[Path] = None) -> List[LiveGroup]:
    """Rebuild live photo pairings from the current index.
    
    Args:
        root: The album root directory.
        library_root: If provided, use this as the database root (global database).
    
    Returns:
        List of LiveGroup objects representing the pairings.
    """
    db_root = library_root if library_root else root
    
    # If using global DB, filter by album path
    album_path = _compute_album_path(root, library_root)
    
    # Read rows from the database
    if album_path:
        rows = list(IndexStore(db_root).read_album_assets(album_path, include_subalbums=True))
        # Convert to album-relative paths for pairing
        prefix = album_path + "/"
        album_rows = []
        for row in rows:
            rel = row.get("rel", "")
            if rel.startswith(prefix):
                adj_row = dict(row)
                adj_row["rel"] = rel[len(prefix):]
                album_rows.append(adj_row)
            elif "/" not in rel:
                album_rows.append(row)
        rows = album_rows
    else:
        rows = list(IndexStore(db_root).read_all())
    
    groups, payload = _compute_links_payload(rows)
    _write_links(root, payload)

    # Also sync to DB
    _sync_live_roles_to_db(root, groups, library_root=library_root)

    return groups

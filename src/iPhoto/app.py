"""High-level application facade."""

from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Callable, Dict, List, Optional

from .cache.index_store import get_global_repository
from .config import (
    DEFAULT_EXCLUDE,
    DEFAULT_INCLUDE,
    RECENTLY_DELETED_DIR_NAME,
)
from .errors import IndexCorruptedError, ManifestInvalidError
from .index_sync_service import (
    compute_links_payload as _compute_links_payload,
    ensure_links as _ensure_links,
    load_incremental_index_cache,
    sync_live_roles_to_db as _sync_live_roles_to_db,
    update_index_snapshot as _update_index_snapshot,
    write_links as _write_links,
)
from .models.album import Album
from .models.types import LiveGroup
from .path_normalizer import compute_album_path as _compute_album_path
from .path_normalizer import normalise_rel_key as _normalise_rel_key
from .utils.logging import get_logger

LOGGER = get_logger()


def _is_index_recoverable_error(exc: Exception) -> bool:
    """Return ``True`` when *exc* stems from recoverable index state."""

    return isinstance(exc, (sqlite3.Error, IndexCorruptedError, ManifestInvalidError))


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
    store = get_global_repository(db_root)
    
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
            from .io.scanner_adapter import scan_album

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
    # Skip sync when using a global (library-level) database: the new
    # architecture treats the DB as the single source of truth for favorites
    # and `toggle_favorite_by_path` writes directly to the DB without
    # touching the per-album manifest.  Calling sync_favorites with the
    # (inevitably empty) manifest would wipe all DB-level favorites.
    if not library_root:
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
    store = get_global_repository(db_root)
    
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
    from .io.scanner_adapter import scan_album

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
    
    # See comment in open_album(): skip manifest-based sync when using
    # a library-level global DB to avoid wiping DB-managed favorites.
    if not library_root:
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
    from .io.scanner_adapter import process_media_paths

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
    from iPhoto.media_classifier import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
    _IMAGE_EXTENSIONS = IMAGE_EXTENSIONS
    _VIDEO_EXTENSIONS = VIDEO_EXTENSIONS

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
    store = get_global_repository(db_root)
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
        rows = list(
            get_global_repository(db_root).read_album_assets(
                album_path,
                include_subalbums=True,
            )
        )
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
        rows = list(get_global_repository(db_root).read_all())
    
    groups, payload = _compute_links_payload(rows)
    _write_links(root, payload)

    # Also sync to DB
    _sync_live_roles_to_db(root, groups, library_root=library_root)

    return groups

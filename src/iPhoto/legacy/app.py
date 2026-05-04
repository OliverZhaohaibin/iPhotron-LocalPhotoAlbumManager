"""High-level application facade."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ..bootstrap.library_asset_lifecycle_service import LibraryAssetLifecycleService
from ..bootstrap.library_scan_service import LibraryScanService
from ..cache.index_store import get_global_repository
from ..index_sync_service import sync_live_roles_to_db as _sync_live_roles_to_db_impl
from iPhoto.application.services.album_manifest_service import Album
from iPhoto.domain.models.core import LiveGroup
from ..utils.logging import get_logger

LOGGER = get_logger()


def _sync_live_roles_to_db(
    root: Path,
    groups: list[LiveGroup],
    library_root: Path | None = None,
) -> None:
    """Backward-compatible wrapper around the index sync helper."""

    repository_root = library_root if library_root is not None else root
    _sync_live_roles_to_db_impl(
        root,
        groups,
        library_root=library_root,
        repository=get_global_repository(repository_root),
    )


def _scan_service(root: Path, library_root: Path | None = None) -> LibraryScanService:
    """Return the session-style scan service used by legacy app.py wrappers."""

    return LibraryScanService(
        library_root if library_root is not None else root,
        repository_factory=get_global_repository,
    )


def _lifecycle_service(
    root: Path,
    library_root: Path | None = None,
    scan_service: LibraryScanService | None = None,
) -> LibraryAssetLifecycleService:
    """Return the session-style lifecycle service used by legacy wrappers."""

    return LibraryAssetLifecycleService(
        library_root if library_root is not None else root,
        scan_service=scan_service,
        repository_factory=get_global_repository,
    )


def open_album(
    root: Path,
    autoscan: bool = True,
    library_root: Path | None = None,
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
    _scan_service(root, library_root).prepare_album_open(
        root,
        autoscan=autoscan,
        hydrate_index=hydrate_index,
        sync_manifest_favorites=library_root is None,
    )
    return album


def rescan(
    root: Path,
    progress_callback: Callable[[int, int], None] | None = None,
    library_root: Path | None = None,
) -> list[dict]:
    """Rescan the album and return the fresh index rows.

    Args:
        root: The album root directory.
        progress_callback: Optional callback for progress updates.
        library_root: If provided, use this as the database root (global database).
    """
    Album.open(root)
    service = _scan_service(root, library_root)
    result = service.scan_album(
        root,
        progress_callback=progress_callback,
        persist_chunks=False,
    )
    rows = result.rows
    service.finalize_scan(root, rows)
    _lifecycle_service(root, library_root, service).reconcile_missing_scan_rows(
        root,
        rows,
    )
    if library_root is None:
        service.sync_manifest_favorites(root)
    return rows


def scan_specific_files(
    root: Path, files: list[Path], library_root: Path | None = None
) -> None:
    """Generate index rows for specific files and merge them into the index.

    This helper avoids a full directory scan, enabling efficient incremental
    updates during batch import operations.

    Args:
        root: The album root directory.
        files: List of files to scan.
        library_root: If provided, use this as the database root (global database).
    """
    _scan_service(root, library_root).scan_specific_files(root, files)


def pair(root: Path, library_root: Path | None = None) -> list[LiveGroup]:
    """Rebuild live photo pairings from the current index.

    Args:
        root: The album root directory.
        library_root: If provided, use this as the database root (global database).

    Returns:
        List of LiveGroup objects representing the pairings.
    """
    return _scan_service(root, library_root).pair_album(root)

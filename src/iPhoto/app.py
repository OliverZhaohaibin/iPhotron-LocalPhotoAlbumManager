"""
Compatibility backend facade.

This module is deprecated as a business entrypoint.
New business logic must be implemented in application/use_cases/*
and only bridged here temporarily for backward compatibility.

Do NOT add new business rules to this file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, List, Optional

from .cache.index_store import get_global_repository
from .index_sync_service import ensure_links as _ensure_links
from .models.album import Album
from .models.types import LiveGroup
from .path_normalizer import compute_album_path as _compute_album_path
from .utils.logging import get_logger

LOGGER = get_logger()


def open_album(
    root: Path,
    autoscan: bool = True,
    library_root: Optional[Path] = None,
    *,
    hydrate_index: bool = True,
) -> Album:
    """Open *root* and return the populated :class:`~iPhoto.models.album.Album`."""

    import sqlite3

    from .config import DEFAULT_EXCLUDE, DEFAULT_INCLUDE
    from .errors import IndexCorruptedError, ManifestInvalidError

    album = Album.open(root)
    db_root = library_root if library_root else root
    store = get_global_repository(db_root)
    album_path = _compute_album_path(root, library_root)

    def _is_recoverable(exc: Exception) -> bool:
        return isinstance(exc, (sqlite3.Error, IndexCorruptedError, ManifestInvalidError))

    rows: list[dict] | None = None

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
            if not _is_recoverable(exc):
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
            if library_root and album_path:
                for row in rows:
                    if "rel" in row:
                        row["rel"] = f"{album_path}/{row['rel']}"
            store.write_rows(rows)
        elif existing_count == 0:
            rows = []

    if rows is not None:
        if album_path:
            prefix = album_path + "/"
            album_rows = [
                {**row, "rel": row["rel"][len(prefix):]}
                if row.get("rel", "").startswith(prefix)
                else row
                for row in rows
                if row.get("rel", "").startswith(prefix) or "/" not in row.get("rel", "")
            ]
            _ensure_links(root, album_rows, library_root=library_root)
        else:
            _ensure_links(root, rows, library_root=library_root)

    if not library_root:
        try:
            store.sync_favorites(album.manifest.get("featured", []))
        except Exception as exc:
            if not _is_recoverable(exc):
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
    """Compatibility bridge.  Delegates to :class:`RescanAlbumUseCase`.

    The ``progress_callback`` parameter is accepted for backward compatibility
    but is not forwarded; async callers should use the dedicated
    ``ScannerWorker`` / ``RescanWorker`` which have their own progress
    mechanism.
    """

    from .application.use_cases.scan.rescan_album_use_case import RescanAlbumUseCase

    use_case = RescanAlbumUseCase(library_root_getter=lambda: library_root)
    return use_case.execute(root)


def scan_specific_files(
    root: Path, files: List[Path], library_root: Optional[Path] = None
) -> None:
    """Generate index rows for specific files and merge them into the index.

    This helper avoids a full directory scan, enabling efficient incremental
    updates during batch import operations.
    """
    from .io.scanner_adapter import process_media_paths

    image_paths: List[Path] = []
    video_paths: List[Path] = []

    _IMAGE_EXTENSIONS = {".heic", ".heif", ".heifs", ".heicf", ".jpg", ".jpeg", ".png"}
    _VIDEO_EXTENSIONS = {".mov", ".mp4", ".m4v", ".qt"}

    for f in files:
        if f.suffix.lower() in _IMAGE_EXTENSIONS:
            image_paths.append(f)
        elif f.suffix.lower() in _VIDEO_EXTENSIONS:
            video_paths.append(f)

    rows = list(process_media_paths(root, image_paths, video_paths))

    album_path = _compute_album_path(root, library_root)
    if album_path:
        for row in rows:
            if "rel" in row:
                row["rel"] = f"{album_path}/{row['rel']}"

    db_root = library_root if library_root else root
    store = get_global_repository(db_root)
    store.append_rows(rows)


def pair(root: Path, library_root: Optional[Path] = None) -> List[LiveGroup]:
    """Compatibility bridge.  Delegates to :class:`PairLivePhotosUseCaseV2`."""

    from .application.use_cases.scan.pair_live_photos_use_case_v2 import PairLivePhotosUseCaseV2

    use_case = PairLivePhotosUseCaseV2(library_root_getter=lambda: library_root)
    return use_case.execute(root)

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
    """Compatibility bridge.  Delegates to :class:`OpenAlbumLegacyBridge`."""

    from .application.use_cases.album.open_album_legacy_bridge import OpenAlbumLegacyBridge

    return OpenAlbumLegacyBridge().execute(
        root,
        autoscan=autoscan,
        library_root=library_root,
        hydrate_index=hydrate_index,
    )


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

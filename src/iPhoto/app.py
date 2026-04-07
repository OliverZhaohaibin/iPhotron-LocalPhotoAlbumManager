"""
Compatibility backend facade.

This module is deprecated as a business entrypoint.
New business logic must be implemented in application/use_cases/*
and only bridged here temporarily for backward compatibility.

Do NOT add new business rules to this file.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .models.types import LiveGroup
from .utils.logging import get_logger

LOGGER = get_logger()


def open_album(
    root: Path,
    autoscan: bool = True,
    library_root: Path | None = None,
    *,
    hydrate_index: bool = True,
) -> "Album":
    """Open *root* and return the populated :class:`~iPhoto.models.album.Album`.

    Compatibility shim - delegates to
    :class:`~iPhoto.application.use_cases.scan.open_album_workflow_use_case.OpenAlbumWorkflowUseCase`.
    """

    from .application.use_cases.scan.open_album_workflow_use_case import OpenAlbumWorkflowUseCase
    from .models.album import Album  # noqa: F401 - re-exported for type hints

    return OpenAlbumWorkflowUseCase().execute(
        root,
        autoscan=autoscan,
        library_root=library_root,
        hydrate_index=hydrate_index,
    )


def rescan(
    root: Path,
    progress_callback: Callable[[int, int], None] | None = None,
    library_root: Path | None = None,
) -> list[dict]:
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
    root: Path, files: list[Path], library_root: Path | None = None
) -> None:
    """Generate index rows for specific files and merge them into the index.

    Compatibility shim – delegates to
    :class:`~iPhoto.application.use_cases.asset.scan_specific_files_use_case.ScanSpecificFilesUseCase`.
    """

    from .application.use_cases.asset.scan_specific_files_use_case import (
        ScanSpecificFilesUseCase,
    )

    ScanSpecificFilesUseCase().execute(root, files, library_root=library_root)


def pair(root: Path, library_root: Path | None = None) -> list[LiveGroup]:
    """Compatibility bridge.  Delegates to :class:`PairLivePhotosUseCaseV2`."""

    from .application.use_cases.scan.pair_live_photos_use_case_v2 import PairLivePhotosUseCaseV2

    use_case = PairLivePhotosUseCaseV2(library_root_getter=lambda: library_root)
    return use_case.execute(root)


"""Rescan album use case.

Application-layer entry point for album rescanning.

``library_update_service.py`` delegates ``backend.rescan()`` calls here so
that the Qt presentation service is no longer the owner of business logic.

Phase 2 refactoring: this use case is now an **orchestration** entry-point.
The individual sub-steps have been extracted into dedicated helpers:

* ``LoadIncrementalIndexUseCase`` – loads the existing index for incremental
  scanning.
* ``MergeTrashRestoreMetadataUseCase`` – merges restore-metadata fields when
  rescanning the trash album.
* ``AlbumPathPolicy`` – all path-prefix transformations.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, List, Optional

_logger = logging.getLogger(__name__)


class RescanAlbumUseCase:
    """Rescan an album directory and return the refreshed index rows."""

    def __init__(
        self,
        *,
        library_root_getter: Optional[Callable[[], Optional[Path]]] = None,
    ) -> None:
        self._library_root_getter = library_root_getter or (lambda: None)

        from ...policies.album_path_policy import AlbumPathPolicy
        from .load_incremental_index_use_case import LoadIncrementalIndexUseCase
        from .merge_trash_restore_metadata_use_case import MergeTrashRestoreMetadataUseCase

        self._path_policy = AlbumPathPolicy()
        self._load_index_uc = LoadIncrementalIndexUseCase()
        self._merge_trash_uc = MergeTrashRestoreMetadataUseCase()

    def execute(self, album_root: Path) -> List[dict]:
        """Run a full rescan for *album_root* and return fresh index rows.

        Raises :class:`~iPhoto.errors.IPhotoError` on failure so callers can
        choose their own error handling strategy.
        """

        from ....cache.index_store import get_global_repository
        from ....config import DEFAULT_EXCLUDE, DEFAULT_INCLUDE
        from ....index_sync_service import (
            ensure_links as _ensure_links,
            update_index_snapshot as _update_index_snapshot,
        )
        from ....io.scanner_adapter import scan_album
        from ....models.album import Album

        library_root = self._library_root_getter()
        _logger.info(
            "RescanAlbumUseCase: rescanning %s (library_root=%s)", album_root, library_root
        )

        album_path = self._path_policy.compute_album_path(album_root, library_root)

        album = Album.open(album_root)
        include = album.manifest.get("filters", {}).get("include", DEFAULT_INCLUDE)
        exclude = album.manifest.get("filters", {}).get("exclude", DEFAULT_EXCLUDE)

        # Load existing index for incremental scanning.
        existing_index = self._load_index_uc.execute(album_root, library_root)
        rows = list(scan_album(album_root, include, exclude, existing_index=existing_index))

        # Add library-scope prefix so rows can be stored in the global index.
        if album_path:
            rows = self._path_policy.prefix_rows(rows, album_path)

        # Merge restore-metadata fields when rescanning the trash album.
        rows = self._merge_trash_uc.execute(rows, album_root, library_root)

        _update_index_snapshot(album_root, rows, library_root=library_root)

        # Compute album-relative rows for links.json (strip library prefix).
        if album_path:
            album_rows = self._path_policy.strip_album_prefix(rows, album_path)
            _ensure_links(album_root, album_rows, library_root=library_root)
        else:
            _ensure_links(album_root, rows, library_root=library_root)

        if not library_root:
            db_root = album_root
            store = get_global_repository(db_root)
            store.sync_favorites(album.manifest.get("featured", []))

        return rows

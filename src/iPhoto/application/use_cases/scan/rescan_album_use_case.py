"""Rescan album use case.

Application-layer entry point for album rescanning.

``library_update_service.py`` delegates ``backend.rescan()`` calls here so
that the Qt presentation service is no longer the owner of business logic.
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

    def execute(self, album_root: Path) -> List[dict]:
        """Run a full rescan for *album_root* and return fresh index rows.

        Raises :class:`~iPhoto.errors.IPhotoError` on failure so callers can
        choose their own error handling strategy.
        """

        from ....cache.index_store import get_global_repository
        from ....config import DEFAULT_EXCLUDE, DEFAULT_INCLUDE, RECENTLY_DELETED_DIR_NAME
        from ....errors import IndexCorruptedError
        from ....index_sync_service import (
            ensure_links as _ensure_links,
            load_incremental_index_cache,
            update_index_snapshot as _update_index_snapshot,
        )
        from ....io.scanner_adapter import scan_album
        from ....models.album import Album
        from ....path_normalizer import compute_album_path

        library_root = self._library_root_getter()
        _logger.info(
            "RescanAlbumUseCase: rescanning %s (library_root=%s)", album_root, library_root
        )

        db_root = library_root if library_root else album_root
        store = get_global_repository(db_root)
        album_path = compute_album_path(album_root, library_root)

        is_recently_deleted = album_root.name == RECENTLY_DELETED_DIR_NAME
        preserved_fields = (
            "original_rel_path",
            "original_album_id",
            "original_album_subpath",
        )
        preserved_restore_rows: dict[str, dict] = {}
        if is_recently_deleted:
            try:
                for row in store.read_all():
                    rel_value = row.get("rel")
                    if not isinstance(rel_value, str):
                        continue
                    if not any(field in row for field in preserved_fields):
                        continue
                    preserved_restore_rows[Path(rel_value).as_posix()] = row
            except IndexCorruptedError:
                _logger.warning("Unable to read previous trash index for %s", album_root)

        album = Album.open(album_root)
        include = album.manifest.get("filters", {}).get("include", DEFAULT_INCLUDE)
        exclude = album.manifest.get("filters", {}).get("exclude", DEFAULT_EXCLUDE)

        existing_index = load_incremental_index_cache(album_root, library_root=library_root)
        rows = list(scan_album(album_root, include, exclude, existing_index=existing_index))

        if album_path:
            for row in rows:
                if "rel" in row:
                    row["rel"] = f"{album_path}/{row['rel']}"

        if is_recently_deleted and preserved_restore_rows:
            for new_row in rows:
                rel_value = new_row.get("rel")
                if not isinstance(rel_value, str):
                    continue
                cached = preserved_restore_rows.get(Path(rel_value).as_posix())
                if not cached:
                    continue
                for field in preserved_fields:
                    if field in cached and field not in new_row:
                        new_row[field] = cached[field]

        _update_index_snapshot(album_root, rows, library_root=library_root)

        if album_path:
            prefix = album_path + "/"
            album_rows = [
                {**row, "rel": row["rel"][len(prefix):]}
                if row.get("rel", "").startswith(prefix)
                else row
                for row in rows
                if row.get("rel", "").startswith(prefix) or "/" not in row.get("rel", "")
            ]
            _ensure_links(album_root, album_rows, library_root=library_root)
        else:
            _ensure_links(album_root, rows, library_root=library_root)

        if not library_root:
            store.sync_favorites(album.manifest.get("featured", []))

        return rows

"""Open album legacy bridge use case.

Contains the business logic for opening an album directory so that
``app.open_album()`` is reduced to a thin compatibility shim.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from ....models.album import Album

_logger = logging.getLogger(__name__)


class OpenAlbumLegacyBridge:
    """Open an album and optionally hydrate its index."""

    def execute(
        self,
        root: Path,
        *,
        autoscan: bool = False,
        library_root: Optional[Path] = None,
        hydrate_index: bool = False,
    ) -> "Album":
        """Open *root* and return the populated :class:`~iPhoto.models.album.Album`.

        Raises :class:`~iPhoto.errors.IPhotoError` on failure so callers can
        decide on their own error handling strategy.
        """

        from ....cache.index_store import get_global_repository
        from ....config import DEFAULT_EXCLUDE, DEFAULT_INCLUDE
        from ....errors import IndexCorruptedError, ManifestInvalidError
        from ....index_sync_service import ensure_links as _ensure_links
        from ....models.album import Album
        from ....path_normalizer import compute_album_path

        _logger.info("OpenAlbumLegacyBridge: opening %s", root)

        album = Album.open(root)
        db_root = library_root if library_root else root
        store = get_global_repository(db_root)
        album_path = compute_album_path(root, library_root)

        def _is_recoverable(exc: Exception) -> bool:
            import sqlite3
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
                _logger.warning(
                    "Index count failed for %s [%s]; assuming empty index: %s",
                    root,
                    type(exc).__name__,
                    exc,
                )
                existing_count = 0

            if existing_count == 0 and autoscan:
                include = album.manifest.get("filters", {}).get("include", DEFAULT_INCLUDE)
                exclude = album.manifest.get("filters", {}).get("exclude", DEFAULT_EXCLUDE)
                from ....io.scanner_adapter import scan_album
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
                _logger.warning(
                    "sync_favorites failed for %s [%s]: %s",
                    root,
                    type(exc).__name__,
                    exc,
                )

        return album

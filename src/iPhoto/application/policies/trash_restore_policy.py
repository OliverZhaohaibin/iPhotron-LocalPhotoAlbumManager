"""Trash restore policy.

Owns the rules for preserving and merging restore metadata when the
"Recently Deleted" album is rescanned.  Previously this logic was
duplicated in both ``rescan_album_use_case.py`` and
``library_update_service._on_scan_finished``.

The three preserved fields power the quick-restore workflow:
- ``original_rel_path``    – physical path before deletion
- ``original_album_id``    – UUID of the source album
- ``original_album_subpath`` – sub-album path when applicable
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ...utils.logging import get_logger

LOGGER = get_logger()

#: Index row fields that must survive a rescan of the trash album.
PRESERVED_FIELDS: Tuple[str, ...] = (
    "original_rel_path",
    "original_album_id",
    "original_album_subpath",
)


class TrashRestorePolicy:
    """Merge restore-metadata fields back into freshly scanned trash rows."""

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    def collect_preserved_rows(
        self,
        store,  # AbstractIndexStore – avoid import cycle
        *,
        album_path: Optional[str],
        allow_read_all: bool,
    ) -> Dict[str, dict]:
        """Return a mapping of ``rel → row`` for rows that carry restore fields.

        Args:
            store: The index repository to read from.
            album_path: If provided, scope the read to this album path.
            allow_read_all: When ``True`` and *album_path* is ``None``, perform a
                full ``read_all()`` to collect preserved rows.  Set to ``False``
                when using a global library database to avoid reading unrelated
                albums.
        """
        from ...errors import IPhotoError

        try:
            if album_path:
                row_iter = store.read_album_assets(album_path, include_subalbums=True)
            elif allow_read_all:
                row_iter = store.read_all()
            else:
                # No safe scope: skip merge entirely.
                return {}
        except IPhotoError as exc:
            LOGGER.warning("TrashRestorePolicy: unable to read old trash index: %s", exc)
            return {}

        preserved: Dict[str, dict] = {}
        for row in row_iter:
            rel_value = row.get("rel")
            if not isinstance(rel_value, str):
                continue
            if not any(field in row for field in PRESERVED_FIELDS):
                continue
            preserved[Path(rel_value).as_posix()] = row

        return preserved

    # ------------------------------------------------------------------
    # Merge helpers
    # ------------------------------------------------------------------

    def merge_preserved_metadata(
        self,
        new_rows: List[dict],
        preserved_rows: Dict[str, dict],
    ) -> List[dict]:
        """Copy missing restore fields from *preserved_rows* into *new_rows*.

        Each row in *new_rows* is updated in-place when a matching entry exists
        in *preserved_rows* and the target row does not already carry the field.
        The original list object is modified and also returned for convenience.
        """
        if not preserved_rows:
            return new_rows

        for row in new_rows:
            rel_value = row.get("rel")
            if not isinstance(rel_value, str):
                continue
            cached = preserved_rows.get(Path(rel_value).as_posix())
            if cached is None:
                # Try without path normalisation as a fallback.
                cached = preserved_rows.get(rel_value)
            if cached is None:
                continue
            for field in PRESERVED_FIELDS:
                if field in cached and field not in row:
                    row[field] = cached[field]

        return new_rows

    # ------------------------------------------------------------------
    # Convenience: compute the album_path scope for a trash album
    # ------------------------------------------------------------------

    def resolve_trash_album_path(
        self,
        trash_root: Path,
        library_root: Optional[Path],
    ) -> Tuple[Optional[str], bool]:
        """Return ``(album_path, allow_read_all)`` for *trash_root*.

        ``album_path`` is the library-relative POSIX path when *library_root*
        is set and *trash_root* is inside it, otherwise ``None``.
        ``allow_read_all`` is ``True`` only when there is no *library_root*
        (standalone album mode).
        """
        if not library_root:
            return None, True

        try:
            album_path: Optional[str] = (
                trash_root.resolve().relative_to(library_root.resolve()).as_posix()
            )
        except (OSError, ValueError):
            album_path = None

        # When library_root is set but path resolution failed, we must not fall
        # back to a global read_all() to avoid cross-contaminating other albums.
        allow_read_all = False
        return album_path, allow_read_all


__all__ = ["TrashRestorePolicy", "PRESERVED_FIELDS"]

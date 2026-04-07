"""Infrastructure adapter for persisting scan results.

Wraps :func:`~iPhoto.index_sync_service.update_index_snapshot` and
:func:`~iPhoto.index_sync_service.ensure_links` behind a single class so
that application-layer use cases do not import low-level utilities directly.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from ...utils.logging import get_logger

LOGGER = get_logger()


class ScanResultPersister:
    """Persist freshly scanned rows to the global index and links files."""

    def persist(
        self,
        album_root: Path,
        rows: List[dict],
        *,
        library_root: Optional[Path] = None,
        album_rows: Optional[List[dict]] = None,
    ) -> None:
        """Write *rows* to the index store and regenerate links for *album_root*.

        Args:
            album_root: Album directory whose index should be updated.
            rows: Library-scoped rows (i.e., ``rel`` values include the album
                path prefix when a *library_root* is provided).
            library_root: When set, the global library database root.
            album_rows: Optional album-relative rows used for links.json; when
                omitted *rows* is used directly (standalone album mode).
        """
        from ...index_sync_service import (
            ensure_links as _ensure_links,
            update_index_snapshot as _update_index_snapshot,
        )

        LOGGER.debug(
            "ScanResultPersister.persist: %d rows for %s", len(rows), album_root
        )
        _update_index_snapshot(album_root, rows, library_root=library_root)
        links_rows = album_rows if album_rows is not None else rows
        _ensure_links(album_root, links_rows, library_root=library_root)


__all__ = ["ScanResultPersister"]

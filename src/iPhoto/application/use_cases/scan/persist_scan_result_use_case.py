"""Persist scan result use case.

Responsible for writing freshly-scanned index rows to the store, updating
snapshot caches, and computing ``links`` after a scan has completed.

This extracts the post-scan bookkeeping that previously lived inline inside
``library_update_service._on_scan_finished``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional, Sequence

_logger = logging.getLogger(__name__)


class PersistScanResultUseCase:
    """Persist a completed scan batch and trigger downstream index updates.

    The actual write logic (``_update_index_snapshot``, ``_ensure_links``,
    trash-metadata merge) is delegated to the injected callables so this use
    case can be unit-tested without a live database.
    """

    def __init__(
        self,
        *,
        update_index_snapshot: Callable[[Path, Sequence[dict], Optional[Path]], None],
        ensure_links: Callable[[Path, Sequence[dict], Optional[Path]], None],
        library_root_getter: Optional[Callable[[], Optional[Path]]] = None,
    ) -> None:
        self._update_index_snapshot = update_index_snapshot
        self._ensure_links = ensure_links
        self._library_root_getter = library_root_getter or (lambda: None)

    def execute(
        self,
        album_root: Path,
        rows: Sequence[dict],
        *,
        library_root: Optional[Path] = None,
        album_path: Optional[str] = None,
    ) -> None:
        """Persist *rows* for *album_root* and refresh associated caches.

        When *album_path* is provided the global-database model is active: rows
        already carry library-scoped ``rel`` values but ``links.json`` must
        reference album-relative paths.  The strip is handled here so callers
        do not need to know about this difference.
        """

        resolved_library_root = library_root or self._library_root_getter()
        _logger.info(
            "PersistScanResultUseCase: persisting %d rows for %s", len(rows), album_root
        )
        self._update_index_snapshot(album_root, rows, resolved_library_root)

        if album_path:
            from ...policies.album_path_policy import AlbumPathPolicy
            album_rows = AlbumPathPolicy().strip_album_prefix(list(rows), album_path)
            self._ensure_links(album_root, album_rows, resolved_library_root)
        else:
            self._ensure_links(album_root, rows, resolved_library_root)

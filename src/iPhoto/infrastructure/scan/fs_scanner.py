"""Infrastructure adapter for filesystem album scanning.

Wraps :func:`~iPhoto.io.scanner_adapter.scan_album` so that application-layer
use cases depend on a thin adapter interface rather than the low-level scanner
directly.  This makes the boundary explicit and simplifies testing via stubs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Optional

from ...utils.logging import get_logger

LOGGER = get_logger()


class FsScanner:
    """Thin adapter around the low-level filesystem album scanner."""

    def scan(
        self,
        album_root: Path,
        include: Iterable[str],
        exclude: Iterable[str],
        *,
        existing_index: Optional[Dict[str, dict]] = None,
    ) -> List[dict]:
        """Scan *album_root* and return a list of asset rows.

        Args:
            album_root: The directory to scan.
            include: Glob patterns for files to include.
            exclude: Glob patterns for files to exclude.
            existing_index: Optional ``rel → row`` mapping used for incremental
                scanning; unchanged files are carried forward from this dict.
        """
        from ...io.scanner_adapter import scan_album

        rows = list(
            scan_album(
                album_root,
                include,
                exclude,
                existing_index=existing_index or {},
            )
        )
        LOGGER.debug("FsScanner: scanned %d rows from %s", len(rows), album_root)
        return rows


__all__ = ["FsScanner"]

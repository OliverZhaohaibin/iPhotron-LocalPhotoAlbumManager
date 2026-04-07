"""Load incremental index use case.

Encapsulates reading the existing index snapshot into a ``rel → row``
dictionary so that the scanner can skip unchanged files (incremental mode).

Extracted from ``rescan_album_use_case.py`` where this logic lived inline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

from ....path_normalizer import compute_album_path, normalise_rel_key
from ....utils.logging import get_logger

LOGGER = get_logger()


class LoadIncrementalIndexUseCase:
    """Read the current index snapshot for incremental scanning."""

    def execute(
        self,
        album_root: Path,
        library_root: Optional[Path] = None,
    ) -> Dict[str, dict]:
        """Return a ``rel → row`` mapping from the existing database.

        The returned dict is suitable for passing directly to
        :func:`~iPhoto.io.scanner_adapter.scan_album` as *existing_index*.
        """
        from ....cache.index_store import get_global_repository
        from ....errors import IndexCorruptedError

        db_root = library_root if library_root else album_root
        store = get_global_repository(db_root)
        album_path = compute_album_path(album_root, library_root)

        existing: Dict[str, dict] = {}
        try:
            if album_path:
                rows = store.read_album_assets(album_path, include_subalbums=True)
            else:
                rows = store.read_all()
            for row in rows:
                key = normalise_rel_key(row.get("rel"))
                if key:
                    existing[key] = row
        except IndexCorruptedError:
            LOGGER.debug(
                "LoadIncrementalIndexUseCase: index corrupted for %s, starting fresh",
                album_root,
            )

        return existing


__all__ = ["LoadIncrementalIndexUseCase"]

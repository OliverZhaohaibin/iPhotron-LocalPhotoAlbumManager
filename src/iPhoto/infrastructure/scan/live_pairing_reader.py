"""Infrastructure reader for Live Photo pairing.

Encapsulates the scoped database read and rel-path adjustment that
``pair_live_photos_use_case_v2.py`` previously performed inline.

Responsibility:
- Read album-scoped rows from the global index.
- Rewrite ``rel`` values to be album-relative (strip the library prefix).
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from ...utils.logging import get_logger

LOGGER = get_logger()


class LivePairingReader:
    """Read album rows suitable for Live Photo pairing from the global index."""

    def read_album_rows(
        self,
        album_root: Path,
        library_root: Optional[Path] = None,
    ) -> List[dict]:
        """Return album-relative rows for *album_root* from the index.

        When a *library_root* is set the global database is queried with the
        album-scoped filter; otherwise all rows from the standalone album
        database are returned.  In both cases, ``rel`` values are rewritten
        to be album-relative before returning so that downstream pairing
        logic does not need to know about the global path prefix.
        """
        from ...cache.index_store import get_global_repository
        from ...application.policies.album_path_policy import AlbumPathPolicy

        path_policy = AlbumPathPolicy()
        db_root = library_root if library_root else album_root
        album_path = path_policy.compute_album_path(album_root, library_root)

        LOGGER.debug(
            "LivePairingReader.read_album_rows: album_root=%s album_path=%s",
            album_root,
            album_path,
        )

        if album_path:
            rows = list(
                get_global_repository(db_root).read_album_assets(
                    album_path, include_subalbums=True
                )
            )
            # Strip the library prefix so pairing sees album-relative paths.
            rows = path_policy.strip_album_prefix(rows, album_path)
        else:
            rows = list(get_global_repository(db_root).read_all())

        return rows


__all__ = ["LivePairingReader"]

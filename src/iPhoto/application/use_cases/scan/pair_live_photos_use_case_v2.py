"""Pair Live Photos use case (v2).

Application-layer entry point for Live Photo pairing.  This is the v2
counterpart of the existing ``pair_live_photos.py`` use case which works
against domain repositories.  The present module bridges the legacy
``app.pair()`` backend call so that ``library_update_service.py`` no longer
calls it directly.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from ....models.types import LiveGroup

_logger = logging.getLogger(__name__)


class PairLivePhotosUseCaseV2:
    """Rebuild Live Photo pairings for an album using the legacy backend."""

    def __init__(
        self,
        *,
        library_root_getter: Optional[Callable[[], Optional[Path]]] = None,
    ) -> None:
        self._library_root_getter = library_root_getter or (lambda: None)

    def execute(self, album_root: Path) -> "List[LiveGroup]":
        """Pair live photos under *album_root* and return the updated groups.

        Returns a list of :class:`~iPhoto.models.types.LiveGroup` objects.

        Raises :class:`~iPhoto.errors.IPhotoError` on failure.
        """

        from ....cache.index_store import get_global_repository
        from ....index_sync_service import (
            compute_links_payload,
            sync_live_roles_to_db,
            write_links,
        )
        from ....path_normalizer import compute_album_path

        library_root = self._library_root_getter()
        _logger.info(
            "PairLivePhotosUseCaseV2: pairing %s (library_root=%s)", album_root, library_root
        )

        db_root = library_root if library_root else album_root
        album_path = compute_album_path(album_root, library_root)

        if album_path:
            rows = list(
                get_global_repository(db_root).read_album_assets(
                    album_path, include_subalbums=True
                )
            )
            prefix = album_path + "/"
            album_rows = []
            for row in rows:
                rel = row.get("rel", "")
                if rel.startswith(prefix):
                    adj_row = dict(row)
                    adj_row["rel"] = rel[len(prefix):]
                    album_rows.append(adj_row)
                elif "/" not in rel:
                    album_rows.append(row)
            rows = album_rows
        else:
            rows = list(get_global_repository(db_root).read_all())

        groups, payload = compute_links_payload(rows)
        write_links(album_root, payload)
        sync_live_roles_to_db(album_root, groups, library_root=library_root)

        return groups

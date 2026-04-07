"""Pair Live Photos use case (v2).

Application-layer entry point for Live Photo pairing.  This is the v2
counterpart of the existing ``pair_live_photos.py`` use case which works
against domain repositories.  The present module bridges the legacy
``app.pair()`` backend call so that ``library_update_service.py`` no longer
calls it directly.

Phase 2 refactoring: rel-path transformation rules have been moved to
:class:`~iPhoto.application.policies.album_path_policy.AlbumPathPolicy`
and the scoped DB row read is now delegated to
:mod:`~iPhoto.infrastructure.scan.live_pairing_reader`.
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

        from ...policies.album_path_policy import AlbumPathPolicy
        from ....infrastructure.scan.live_pairing_reader import LivePairingReader

        self._path_policy = AlbumPathPolicy()
        self._pairing_reader = LivePairingReader()

    def execute(self, album_root: Path) -> "List[LiveGroup]":
        """Pair live photos under *album_root* and return the updated groups.

        Returns a list of :class:`~iPhoto.models.types.LiveGroup` objects.

        Raises :class:`~iPhoto.errors.IPhotoError` on failure.
        """

        from ....index_sync_service import (
            compute_links_payload,
            sync_live_roles_to_db,
            write_links,
        )

        library_root = self._library_root_getter()
        _logger.info(
            "PairLivePhotosUseCaseV2: pairing %s (library_root=%s)", album_root, library_root
        )

        # Delegate scoped DB read and rel-path adjustment to the reader.
        rows = self._pairing_reader.read_album_rows(album_root, library_root)

        groups, payload = compute_links_payload(rows)
        write_links(album_root, payload)
        sync_live_roles_to_db(album_root, groups, library_root=library_root)

        return groups

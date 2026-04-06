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
from typing import Callable, List, Optional

from .... import app as _backend
from ....errors import IPhotoError

_logger = logging.getLogger(__name__)


class PairLivePhotosUseCaseV2:
    """Rebuild Live Photo pairings for an album using the legacy backend."""

    def __init__(
        self,
        *,
        library_root_getter: Optional[Callable[[], Optional[Path]]] = None,
    ) -> None:
        self._library_root_getter = library_root_getter or (lambda: None)

    def execute(self, album_root: Path) -> List[dict]:
        """Pair live photos under *album_root* and return the updated groups.

        Returns a list of dicts (one per :class:`~iPhoto.models.types.LiveGroup`)
        for backward compatibility with existing callers.

        Raises :class:`~iPhoto.errors.IPhotoError` on failure.
        """

        library_root = self._library_root_getter()
        _logger.info(
            "PairLivePhotosUseCaseV2: pairing %s (library_root=%s)", album_root, library_root
        )
        groups = _backend.pair(album_root, library_root=library_root)
        return [group.__dict__ for group in groups]

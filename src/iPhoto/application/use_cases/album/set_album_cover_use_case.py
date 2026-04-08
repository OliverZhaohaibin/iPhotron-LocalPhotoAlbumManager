"""Set album cover use case.

Application-layer entry point for changing the album's cover image.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from ....models.album import Album
    from ....gui.services.album_metadata_service import AlbumMetadataService

_logger = logging.getLogger(__name__)


class SetAlbumCoverUseCase:
    """Set the album cover to the given relative asset path."""

    def __init__(
        self,
        *,
        metadata_service: "AlbumMetadataService",
        current_album_getter: Callable[[], Optional["Album"]],
    ) -> None:
        self._metadata_service = metadata_service
        self._current_album_getter = current_album_getter

    def execute(self, rel: str) -> bool:
        """Set the album cover to *rel*.

        Returns ``True`` when the operation succeeds.
        """

        album = self._current_album_getter()
        if album is None:
            return False
        return self._metadata_service.set_album_cover(album, rel)

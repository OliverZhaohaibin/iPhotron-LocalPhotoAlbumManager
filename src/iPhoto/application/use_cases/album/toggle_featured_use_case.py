"""Toggle featured (favorite) use case.

Application-layer entry point for toggling whether an asset is featured in
the active album.  Extracts the business intent from the presentation layer.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from ....models.album import Album
    from ....gui.services.album_metadata_service import AlbumMetadataService

_logger = logging.getLogger(__name__)


class ToggleFeaturedUseCase:
    """Toggle an asset's featured status in the current album."""

    def __init__(
        self,
        *,
        metadata_service: "AlbumMetadataService",
        current_album_getter: Callable[[], Optional["Album"]],
    ) -> None:
        self._metadata_service = metadata_service
        self._current_album_getter = current_album_getter

    def execute(self, ref: str) -> bool:
        """Toggle *ref* in the active album.

        Returns ``True`` when the operation succeeds, ``False`` otherwise
        (e.g. no album open or empty ref).
        """

        album = self._current_album_getter()
        if album is None or not ref:
            return False
        return self._metadata_service.toggle_featured(album, ref)

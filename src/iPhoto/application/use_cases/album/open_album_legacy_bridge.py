"""Open album legacy bridge use case.

Thin application-layer wrapper around ``app.open_album()`` so that new code
can depend on an explicit use-case boundary rather than calling the backend
module directly.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from .... import app as _backend
from ....errors import IPhotoError

if TYPE_CHECKING:  # pragma: no cover
    from ....models.album import Album

_logger = logging.getLogger(__name__)


class OpenAlbumLegacyBridge:
    """Bridge ``app.open_album()`` behind an application use-case interface."""

    def execute(
        self,
        root: Path,
        *,
        autoscan: bool = False,
        library_root: Optional[Path] = None,
        hydrate_index: bool = False,
    ) -> "Album":
        """Open *root* and return the populated :class:`~iPhoto.models.album.Album`.

        Raises :class:`~iPhoto.errors.IPhotoError` on failure so callers can
        decide on their own error handling strategy.
        """

        _logger.info("OpenAlbumLegacyBridge: opening %s", root)
        return _backend.open_album(
            root,
            autoscan=autoscan,
            library_root=library_root,
            hydrate_index=hydrate_index,
        )

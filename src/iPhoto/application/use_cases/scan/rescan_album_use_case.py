"""Rescan album use case.

Application-layer entry point for album rescanning.

``library_update_service.py`` delegates ``backend.rescan()`` calls here so
that the Qt presentation service is no longer the owner of business logic.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, List, Optional

from .... import app as _backend
from ....errors import IPhotoError

_logger = logging.getLogger(__name__)


class RescanAlbumUseCase:
    """Rescan an album directory and return the refreshed index rows."""

    def __init__(
        self,
        *,
        library_root_getter: Optional[Callable[[], Optional[Path]]] = None,
    ) -> None:
        self._library_root_getter = library_root_getter or (lambda: None)

    def execute(self, album_root: Path) -> List[dict]:
        """Run a full rescan for *album_root* and return fresh index rows.

        Raises :class:`~iPhoto.errors.IPhotoError` on failure so callers can
        choose their own error handling strategy.
        """

        library_root = self._library_root_getter()
        _logger.info("RescanAlbumUseCase: rescanning %s (library_root=%s)", album_root, library_root)
        return _backend.rescan(album_root, library_root=library_root)

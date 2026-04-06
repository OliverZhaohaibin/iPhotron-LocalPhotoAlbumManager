"""Import assets use case.

Application-layer entry point for importing files into an album.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Iterable, Optional, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from ....gui.services import AssetImportService

_logger = logging.getLogger(__name__)


class ImportAssetsUseCase:
    """Delegate file import to the underlying import service."""

    def __init__(self, *, import_service: "AssetImportService") -> None:
        self._import_service = import_service

    def execute(
        self,
        sources: Iterable[Path],
        *,
        destination: Optional[Path] = None,
        mark_featured: bool = False,
    ) -> None:
        """Asynchronously import *sources* into *destination*."""

        self._import_service.import_files(
            sources,
            destination=destination,
            mark_featured=mark_featured,
        )

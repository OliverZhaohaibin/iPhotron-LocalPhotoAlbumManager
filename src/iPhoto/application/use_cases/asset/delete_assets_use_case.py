"""Delete assets use case.

Application-layer entry point for moving assets to the deleted-items folder.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from ....gui.services import DeletionService

_logger = logging.getLogger(__name__)


class DeleteAssetsUseCase:
    """Delegate asset deletion to the underlying deletion service."""

    def __init__(self, *, deletion_service: "DeletionService") -> None:
        self._deletion_service = deletion_service

    def execute(self, sources: Iterable[Path]) -> None:
        """Move *sources* into the dedicated deleted-items folder."""

        self._deletion_service.delete_assets(sources)

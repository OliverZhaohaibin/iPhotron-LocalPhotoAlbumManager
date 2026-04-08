"""Move assets use case.

Application-layer entry point for moving assets between albums.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from ....gui.services import AssetMoveService

_logger = logging.getLogger(__name__)


class MoveAssetsUseCase:
    """Delegate asset moves to the underlying move service."""

    def __init__(self, *, move_service: "AssetMoveService") -> None:
        self._move_service = move_service

    def execute(self, sources: Iterable[Path], destination: Path) -> None:
        """Move *sources* into *destination*."""

        self._move_service.move_assets(sources, destination)

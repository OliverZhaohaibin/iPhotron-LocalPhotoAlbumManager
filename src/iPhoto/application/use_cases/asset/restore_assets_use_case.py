"""Restore assets use case.

Application-layer entry point for restoring assets from the deleted-items
folder back to their original album locations.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from ....gui.services import RestorationService

_logger = logging.getLogger(__name__)


class RestoreAssetsUseCase:
    """Delegate asset restoration to the underlying restoration service."""

    def __init__(self, *, restoration_service: "RestorationService") -> None:
        self._restoration_service = restoration_service

    def execute(self, sources: Iterable[Path]) -> bool:
        """Schedule restoration for *sources*.

        Returns ``True`` when at least one restore operation was scheduled.
        """

        return self._restoration_service.restore_assets(sources)

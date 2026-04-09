"""Centralized sidecar commit flow for edit and playback updates."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, Signal

from iPhoto.io import sidecar

LOGGER = logging.getLogger(__name__)


class MediaAdjustmentCommitter(QObject):
    """Persist adjustments and notify the rest of the UI about the update."""

    adjustmentsCommitted = Signal(object, str)

    def __init__(
        self,
        *,
        asset_vm,
        pause_watcher: Callable[[], None] | None = None,
        resume_watcher: Callable[[], None] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._asset_vm = asset_vm
        self._pause_watcher = pause_watcher
        self._resume_watcher = resume_watcher

    def commit(self, source: Path, adjustments: dict, *, reason: str) -> bool:
        paused = False
        try:
            if self._pause_watcher is not None:
                self._pause_watcher()
                paused = True
            sidecar.save_adjustments(source, adjustments)
            self._asset_vm.invalidate_thumbnail(str(source))
        except Exception:
            LOGGER.exception("Failed to commit adjustments for %s", source)
            return False
        finally:
            if paused and self._resume_watcher is not None:
                self._resume_watcher()

        self.adjustmentsCommitted.emit(source, reason)
        return True

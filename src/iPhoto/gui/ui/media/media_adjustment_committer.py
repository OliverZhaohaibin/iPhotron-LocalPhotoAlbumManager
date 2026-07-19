"""Centralized sidecar commit flow for edit and playback updates."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, Signal

from iPhoto.application.ports import EditServicePort

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
        edit_service_getter: Callable[[], EditServicePort | None] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._asset_vm = asset_vm
        self._pause_watcher = pause_watcher
        self._resume_watcher = resume_watcher
        self._edit_service_getter = edit_service_getter
        self._adjustment_preparation_invalidator: Callable[[Path], None] | None = None

    def set_adjustment_preparation_invalidator(
        self,
        invalidator: Callable[[Path], None] | None,
    ) -> None:
        """Invalidate prepared sidecar state before publishing a commit."""

        self._adjustment_preparation_invalidator = invalidator

    def commit(self, source: Path, adjustments: dict, *, reason: str) -> bool:
        paused = False
        try:
            if self._pause_watcher is not None:
                self._pause_watcher()
                paused = True
            edit_service = (
                self._edit_service_getter() if self._edit_service_getter is not None else None
            )
            if edit_service is None:
                raise RuntimeError("Edit service is unavailable")
            commit_result = edit_service.write_adjustments(source, adjustments)
            desired_revision = getattr(commit_result, "thumbnail_revision", None)
            if isinstance(desired_revision, str) and desired_revision:
                self._asset_vm.invalidate_thumbnail(
                    str(source),
                    desired_revision=desired_revision,
                )
            else:
                self._asset_vm.invalidate_thumbnail(str(source))
            if self._adjustment_preparation_invalidator is not None:
                try:
                    self._adjustment_preparation_invalidator(source)
                except Exception:
                    LOGGER.exception("Failed to invalidate prepared adjustment state")
        except Exception:
            LOGGER.exception("Failed to commit adjustments for %s", source)
            return False
        finally:
            if paused and self._resume_watcher is not None:
                self._resume_watcher()

        self.adjustmentsCommitted.emit(source, reason)
        return True

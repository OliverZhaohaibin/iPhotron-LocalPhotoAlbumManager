"""Manager for live synchronization and incremental updates."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Dict, List, Optional, TYPE_CHECKING

from PySide6.QtCore import QObject, Signal, QThreadPool, QMutex, QMutexLocker

from ...tasks.incremental_refresh_worker import IncrementalRefreshSignals, IncrementalRefreshWorker
from ..list_diff_calculator import ListDiffCalculator
from .....utils.pathutils import normalise_for_compare, is_descendant_path

if TYPE_CHECKING:
    from ...facade import AppFacade
    from .data_repository import AssetRepository

logger = logging.getLogger(__name__)


class AssetLiveSyncManager(QObject):
    """
    Handles incremental updates triggered by filesystem changes (links.json).
    Calculates diffs and notifies the model.
    """

    diffReady = Signal(object, list)  # diff result, fresh_rows (for reset case)
    error = Signal(str)

    def __init__(
        self,
        parent: QObject,
        repository: "AssetRepository",
        facade: "AppFacade",
        is_loading_callback: Callable[[], bool]
    ) -> None:
        super().__init__(parent)
        self._repo = repository
        self._facade = facade
        self._is_loading = is_loading_callback

        self._album_root: Optional[Path] = None
        self._active_filter: Optional[str] = None

        self._incremental_worker: Optional[IncrementalRefreshWorker] = None
        self._incremental_signals: Optional[IncrementalRefreshSignals] = None
        self._refresh_lock = QMutex()
        self._deferred_incremental_refresh: Optional[Path] = None

        self._facade.linksUpdated.connect(self.handle_links_updated)

    def set_album_root(self, root: Optional[Path]) -> None:
        self._album_root = root
        self._set_deferred_incremental_refresh(None)

    def set_active_filter(self, filter_mode: Optional[str]) -> None:
        self._active_filter = filter_mode

    def is_refreshing(self) -> bool:
        with QMutexLocker(self._refresh_lock):
            return self._incremental_worker is not None

    def _set_deferred_incremental_refresh(self, root: Optional[Path]) -> None:
        if root is None:
            self._deferred_incremental_refresh = None
            return
        self._deferred_incremental_refresh = normalise_for_compare(root)

    def process_deferred_refresh(self) -> None:
        """Check and trigger any deferred refresh if conditions allow."""
        if (
            self._album_root
            and self._deferred_incremental_refresh
            and normalise_for_compare(self._album_root) == self._deferred_incremental_refresh
        ):
            logger.debug(
                "AssetLiveSyncManager: applying deferred incremental refresh for %s.",
                self._album_root,
            )
            pending_root = self._album_root
            self._set_deferred_incremental_refresh(None)
            self.refresh_rows_from_index(pending_root)

    def handle_links_updated(self, root: Path) -> None:
        """React to :mod:`links.json` refreshes triggered by the backend."""
        if not self._album_root:
            return

        album_root = normalise_for_compare(self._album_root)
        updated_root = normalise_for_compare(Path(root))

        if not self._links_update_targets_current_view(album_root, updated_root):
            return

        if self._repo.suppress_virtual_reload():
            if self._repo.virtual_move_requires_revisit():
                logger.debug(
                    "AssetLiveSyncManager: holding reload for %s until the aggregate view is reopened.",
                    updated_root,
                )
                return

            logger.debug(
                "AssetLiveSyncManager: finishing temporary suppression for %s after non-aggregate move.",
                updated_root,
            )
            self._repo.set_virtual_reload_suppressed(False)
            if self._repo.row_count() > 0:
                self.refresh_rows_from_index(self._album_root)
            return

        logger.debug(
            "AssetLiveSyncManager: linksUpdated for %s triggers incremental refresh of %s.",
            updated_root,
            album_root,
        )

        descendant_root = updated_root if updated_root != album_root else None

        if self._repo.row_count() > 0:
             self.refresh_rows_from_index(self._album_root, descendant_root=descendant_root)

        if self._is_loading():
            logger.debug(
                "AssetLiveSyncManager: loader active, postponing incremental refresh for %s.",
                updated_root,
            )
            self._set_deferred_incremental_refresh(self._album_root)
            return

        self._set_deferred_incremental_refresh(None)
        self.refresh_rows_from_index(self._album_root, descendant_root=descendant_root)

    def refresh_rows_from_index(
        self, root: Path, descendant_root: Optional[Path] = None
    ) -> None:
        """Synchronise the model with the latest index snapshot for *root*."""

        with QMutexLocker(self._refresh_lock):
            if self._incremental_worker is not None:
                logger.debug("AssetLiveSyncManager: incremental refresh already in progress, skipping request.")
                return

            manifest = self._facade.current_album.manifest if self._facade.current_album else {}
            featured = manifest.get("featured", []) or []

            filter_params = {}
            if self._active_filter:
                filter_params["filter_mode"] = self._active_filter

            self._incremental_signals = IncrementalRefreshSignals()
            self._incremental_signals.resultsReady.connect(self._on_results_ready)
            self._incremental_signals.error.connect(self._on_incremental_error)

            self._incremental_worker = IncrementalRefreshWorker(
                root,
                featured,
                self._incremental_signals,
                filter_params=filter_params,
                descendant_root=descendant_root
            )

            QThreadPool.globalInstance().start(self._incremental_worker)

    def _on_results_ready(self, root: Path, fresh_rows: List[Dict[str, object]]) -> None:
        if not self._album_root or root != self._album_root:
            self._cleanup_incremental_worker()
            return

        self._cleanup_incremental_worker()

        # Calculate diff
        current_rows = self._repo.rows
        diff = ListDiffCalculator.calculate_diff(current_rows, fresh_rows)

        self.diffReady.emit(diff, fresh_rows)

    def _on_incremental_error(self, root: Path, message: str) -> None:
        logger.error("AssetLiveSyncManager: incremental refresh error for %s: %s", root, message)
        self.error.emit(message)
        self._cleanup_incremental_worker()

    def _cleanup_incremental_worker(self) -> None:
        with QMutexLocker(self._refresh_lock):
            if self._incremental_signals:
                try:
                    self._incremental_signals.resultsReady.disconnect(self._on_results_ready)
                    self._incremental_signals.error.disconnect(self._on_incremental_error)
                except RuntimeError:
                    pass
                self._incremental_signals.deleteLater()
                self._incremental_signals = None
            self._incremental_worker = None

    def _links_update_targets_current_view(
        self, album_root: Path, updated_root: Path
    ) -> bool:
        if album_root == updated_root:
            return True
        return is_descendant_path(updated_root, album_root)

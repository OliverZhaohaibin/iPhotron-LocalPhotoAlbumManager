"""Incremental update handler for asset list model.

This module encapsulates the "Diff & Patch" logic for incremental updates,
isolating the complex list diffing algorithm from Qt Model index management.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from PySide6.QtCore import QMutex, QMutexLocker, QObject, QThreadPool, Signal

from ...tasks.incremental_refresh_worker import (
    IncrementalRefreshSignals,
    IncrementalRefreshWorker,
)
from ..list_diff_calculator import ListDiffCalculator
from .....utils.pathutils import normalise_rel_value

if TYPE_CHECKING:
    from PySide6.QtCore import QModelIndex

logger = logging.getLogger(__name__)


class IncrementalUpdateHandler(QObject):
    """Handles incremental updates via diff calculation and patch application.
    
    This class manages:
    - IncrementalRefreshWorker lifecycle
    - ListDiffCalculator usage
    - Signaling model to perform beginInsertRows/beginRemoveRows
    """
    
    # Signal emitted when rows should be removed
    # Parameters: (index: int, count: int)
    removeRowsRequested = Signal(int, int)
    
    # Signal emitted when rows should be inserted
    # Parameters: (index: int, rows: List[Dict])
    insertRowsRequested = Signal(int, list)
    
    # Signal emitted when a row's data changed
    # Parameters: (index: int, row: Dict)
    rowDataChanged = Signal(int, dict)
    
    # Signal emitted when a full model reset is needed
    # Parameters: (new_rows: List[Dict])
    modelResetRequested = Signal(list)
    
    # Signal for errors
    refreshError = Signal(Path, str)
    
    def __init__(
        self,
        get_current_rows: Callable[[], List[Dict[str, Any]]],
        get_featured: Callable[[], List[str]],
        get_filter_params: Callable[[], Optional[Dict[str, Any]]],
        get_library_root: Optional[Callable[[], Optional[Path]]] = None,
        parent: Optional[QObject] = None,
    ):
        """Initialize the update handler.
        
        Args:
            get_current_rows: Callback to get current model rows.
            get_featured: Callback to get featured assets list.
            get_filter_params: Callback to get current filter parameters.
            get_library_root: Optional callback to get library root for global database filtering.
            parent: Parent QObject.
        """
        super().__init__(parent)
        self._get_current_rows = get_current_rows
        self._get_featured = get_featured
        self._get_filter_params = get_filter_params
        self._get_library_root = get_library_root
        
        self._incremental_worker: Optional[IncrementalRefreshWorker] = None
        self._incremental_signals: Optional[IncrementalRefreshSignals] = None
        self._refresh_lock = QMutex()
    
    def refresh_from_index(
        self,
        root: Path,
        descendant_root: Optional[Path] = None,
    ) -> None:
        """Start an incremental refresh from the index.
        
        Args:
            root: Album root to refresh.
            descendant_root: Optional descendant root for filtered refresh.
        """
        with QMutexLocker(self._refresh_lock):
            if self._incremental_worker is not None:
                logger.debug(
                    "IncrementalUpdateHandler: refresh already in progress, skipping."
                )
                return
            
            featured = self._get_featured()
            filter_params = self._get_filter_params() or {}
            
            # Get library root for global database filtering
            library_root = self._get_library_root() if self._get_library_root else None
            
            self._incremental_signals = IncrementalRefreshSignals()
            self._incremental_signals.resultsReady.connect(self._apply_incremental_results)
            self._incremental_signals.error.connect(self._on_error)
            
            self._incremental_worker = IncrementalRefreshWorker(
                root,
                featured,
                self._incremental_signals,
                filter_params=filter_params,
                descendant_root=descendant_root,
                library_root=library_root,
            )
            
            QThreadPool.globalInstance().start(self._incremental_worker)
    
    def _apply_incremental_results(
        self,
        root: Path,
        fresh_rows: List[Dict[str, object]],
    ) -> None:
        """Apply the fetched rows via diffing."""
        self._cleanup_worker()
        
        current_rows = self._get_current_rows()
        diff = ListDiffCalculator.calculate_diff(current_rows, fresh_rows)
        
        if diff.is_reset:
            # Full reset needed
            self.modelResetRequested.emit(fresh_rows)
            logger.debug(
                "IncrementalUpdateHandler: full reset applied for %s (%d rows).",
                root,
                len(fresh_rows),
            )
            return
        
        if diff.is_empty_to_empty:
            return
        
        # Signal removals
        for index in diff.removed_indices:
            self.removeRowsRequested.emit(index, 1)
        
        # Signal insertions
        for insert_index, row_data, rel_key in diff.inserted_items:
            self.insertRowsRequested.emit(insert_index, [row_data])
        
        # Signal data changes
        for row_data in diff.changed_items:
            rel_key = normalise_rel_value(row_data.get("rel", ""))
            # The model will need to look up the index
            # Signal with -1 to indicate model should find index
            self.rowDataChanged.emit(-1, row_data)
        
        logger.debug(
            "IncrementalUpdateHandler: applied incremental update for %s "
            "(%d removed, %d inserted, %d changed).",
            root,
            len(diff.removed_indices),
            len(diff.inserted_items),
            len(diff.changed_items),
        )
    
    def _on_error(self, root: Path, message: str) -> None:
        """Handle refresh error."""
        logger.error("IncrementalUpdateHandler: refresh error for %s: %s", root, message)
        self._cleanup_worker()
        self.refreshError.emit(root, message)
    
    def _cleanup_worker(self) -> None:
        """Clean up worker and signals."""
        with QMutexLocker(self._refresh_lock):
            if self._incremental_signals:
                try:
                    self._incremental_signals.resultsReady.disconnect(
                        self._apply_incremental_results
                    )
                    self._incremental_signals.error.disconnect(self._on_error)
                except RuntimeError:
                    pass
                self._incremental_signals.deleteLater()
                self._incremental_signals = None
            self._incremental_worker = None

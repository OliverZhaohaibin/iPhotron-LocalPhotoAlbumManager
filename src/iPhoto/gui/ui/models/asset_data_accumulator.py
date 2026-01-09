"""Accumulator for buffering and batching asset updates."""

from __future__ import annotations

from typing import Dict, List, TYPE_CHECKING

from PySide6.QtCore import QObject, QTimer, QModelIndex, Qt

from .roles import Roles

if TYPE_CHECKING:
    from .asset_list.model import AssetListModel
    from .asset_state_manager import AssetListStateManager


class AssetDataAccumulator(QObject):
    """Buffers incoming asset chunks and merges them efficiently into the model."""

    # Accumulate chunks for 250ms to prevent the UI from stuttering during rapid
    # updates.  This strikes a balance between responsiveness and smoothness.
    FLUSH_INTERVAL_MS = 250

    def __init__(
        self,
        model: AssetListModel,
        state_manager: AssetListStateManager,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._model = model
        self._state_manager = state_manager
        self._incoming_buffer: List[Dict[str, object]] = []
        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(self.FLUSH_INTERVAL_MS)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.timeout.connect(self._flush_buffer)

    def add_chunk(self, chunk: List[Dict[str, object]]) -> None:
        """Add a chunk of data to the buffer."""
        if not chunk:
            return
        self._incoming_buffer.extend(chunk)
        if not self._flush_timer.isActive():
            self._flush_timer.start()

    def clear(self) -> None:
        """Clear the buffer and stop the timer."""
        self._flush_timer.stop()
        self._incoming_buffer.clear()

    def flush(self) -> None:
        """Force flush the buffer immediately."""
        if self._flush_timer.isActive():
            self._flush_timer.stop()
        self._flush_buffer()

    def is_active(self) -> bool:
        """Return True if the flush timer is running."""
        return self._flush_timer.isActive()

    def _flush_buffer(self) -> None:
        """Commit accumulated rows to the model."""
        if not self._incoming_buffer:
            return

        # Move items out of the buffer so the list remains consistent if new
        # chunks arrive while we are merging.
        payload = list(self._incoming_buffer)
        self._incoming_buffer.clear()
        self._merge_chunk(payload)

    def _merge_chunk(self, chunk: List[Dict[str, object]]) -> None:
        """Append or update rows in the model efficiently."""

        # Separate new items from updates to minimize signal emission overhead.
        # We append new items to the end and update existing ones in place.
        new_items: List[Dict[str, object]] = []
        updates: List[int] = []

        for row in chunk:
            rel = row.get("rel")
            if not rel:
                continue

            # The row lookup keys are normalized relative paths.
            rel_key = self._state_manager.normalise_key(str(rel))
            if not rel_key:
                continue

            existing_index = self._state_manager.row_lookup.get(rel_key)
            if existing_index is not None:
                # Update the existing row data in place.
                if 0 <= existing_index < len(self._state_manager.rows):
                    self._state_manager.update_row_at_index(existing_index, row)
                    updates.append(existing_index)
            else:
                # Ensure the new row has a normalized rel key before appending
                # so the lookup table populated by append_chunk is consistent
                # with the normalized keys we use for searching.
                row["rel"] = rel_key
                new_items.append(row)

        # Batch inserts for better performance.
        if new_items:
            start_row = self._state_manager.row_count()
            end_row = start_row + len(new_items) - 1
            self._model.beginInsertRows(QModelIndex(), start_row, end_row)
            self._state_manager.append_chunk(new_items)
            self._model.endInsertRows()
            self._state_manager.on_external_row_inserted(start_row, len(new_items))

        # Emit updates for modified rows.
        if updates:
            # Determine the range of roles that might have changed.  Since we
            # replaced the entire row dict, basically everything is fair game.
            affected_roles = [
                Roles.REL,
                Roles.ABS,
                Roles.SIZE,
                Roles.DT,
                Roles.IS_IMAGE,
                Roles.IS_VIDEO,
                Roles.IS_LIVE,
                Qt.DecorationRole,  # Thumbnail might need refresh
            ]
            for row_index in updates:
                index = self._model.index(row_index, 0)
                self._model.dataChanged.emit(index, index, affected_roles)
                # Invalidate cache for updated rows to ensure fresh thumbnails
                # Retrieve the row to get the rel
                row = self._state_manager.rows[row_index]
                rel = row.get("rel")
                if rel:
                    self._model.invalidate_thumbnail(str(rel))

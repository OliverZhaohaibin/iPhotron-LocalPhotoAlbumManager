"""Filtering helpers for album asset views."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QAbstractItemModel, QModelIndex, QSortFilterProxyModel, Qt

from .roles import Roles


class AssetFilterProxyModel(QSortFilterProxyModel):
    """Filter model that exposes convenience helpers for static collections."""

    def __init__(self, parent=None) -> None:  # type: ignore[override]
        super().__init__(parent)
        self._filter_mode: Optional[str] = None
        self._search_text: str = ""
        self._default_sort_role: int = int(Roles.DT)
        self._default_sort_order: Qt.SortOrder = Qt.SortOrder.DescendingOrder
        self._monitored_source: Optional[QAbstractItemModel] = None
        self._fast_source: Optional[object] = None
        self._bypass_sort_optimization: bool = False
        self.setDynamicSortFilter(True)
        self.setFilterCaseSensitivity(Qt.CaseInsensitive)
        # ``configure_default_sort`` applies the sort role and ensures the proxy
        # starts tracking chronological order immediately.  Sorting is
        # reapplied whenever the source model resets so background reloads
        # triggered by move/restore operations keep the UI consistent.
        self.configure_default_sort(Roles.DT, Qt.SortOrder.DescendingOrder)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_filter_mode(self, mode: Optional[str]) -> None:
        normalized = mode.casefold() if isinstance(mode, str) and mode else None
        if normalized == self._filter_mode:
            return

        # --- OPTIMIZATION START ---
        # Temporarily disable dynamic sorting and bypass the expensive comparisons.
        # The source model is likely already sorted by date. Disabling this prevents
        # the expensive O(N log N) sort and Python `lessThan` overhead during the
        # filtering phase.
        was_dynamic = self.dynamicSortFilter()
        if was_dynamic:
            self.setDynamicSortFilter(False)
        self._bypass_sort_optimization = True
        # --- OPTIMIZATION END ---

        self._filter_mode = normalized
        self.invalidateFilter()

        # Restore state.
        self._bypass_sort_optimization = False
        # if was_dynamic:

    def filter_mode(self) -> Optional[str]:
        return self._filter_mode

    def set_search_text(self, text: str) -> None:
        normalized = text.strip().casefold()
        if normalized == self._search_text:
            return
        self._search_text = normalized
        self.invalidateFilter()

    def search_text(self) -> str:
        return self._search_text

    def set_filters(self, *, mode: Optional[str] = None, text: Optional[str] = None) -> None:
        changed = False
        if mode is not None and mode.casefold() != (self._filter_mode or ""):
            self._filter_mode = mode.casefold() if mode else None
            changed = True
        if text is not None and text.strip().casefold() != self._search_text:
            self._search_text = text.strip().casefold()
            changed = True
        if changed:
            self.invalidateFilter()

    def configure_default_sort(
        self,
        role: int | Roles,
        order: Qt.SortOrder = Qt.SortOrder.AscendingOrder,
    ) -> None:
        """Record *role* and *order* as the canonical sort configuration.

        The proxy falls back to the configured values whenever the underlying
        model resets.  This keeps the gallery views stable across background
        reloads such as the ones triggered by move or restore operations.
        """

        normalized_role = int(role)

        # 1. Check if the configuration is actually changing
        config_changed = (
            self._default_sort_role != normalized_role
            or self._default_sort_order != order
        )

        # 2. Update internal storage
        self._default_sort_role = normalized_role
        self._default_sort_order = order

        # 3. Optimization: Only re-sort if the config changed OR
        #    if the current active sort state differs from the target.
        #    This prevents O(N log N) sorting when switching between views
        #    that share the same sort order (e.g., All Photos -> Videos).
        if (
            config_changed
            or self.sortRole() != normalized_role
            or self.sortOrder() != order
        ):
            self._reapply_default_sort()

    def apply_default_sort(self) -> None:
        """Reapply the stored default sort order to the current dataset."""

        self._reapply_default_sort()

    def setSourceModel(self, sourceModel: QAbstractItemModel | None) -> None:  # type: ignore[override]
        """Attach *sourceModel* while keeping default sort hooks in sync."""

        if self._monitored_source is not None:
            try:
                self._monitored_source.modelReset.disconnect(self._on_source_model_reset)
            except (TypeError, RuntimeError):  # pragma: no cover - Qt disconnect quirk
                pass
            try:
                self._monitored_source.layoutChanged.disconnect(
                    self._on_source_layout_changed
                )
            except (TypeError, RuntimeError):  # pragma: no cover - Qt disconnect quirk
                pass
        self._fast_source = (
            sourceModel if sourceModel is not None and hasattr(sourceModel, "get_internal_row") else None
        )
        super().setSourceModel(sourceModel)
        self._monitored_source = sourceModel
        if sourceModel is not None:
            sourceModel.modelReset.connect(self._on_source_model_reset)
            sourceModel.layoutChanged.connect(self._on_source_layout_changed)
        self._reapply_default_sort()

    # ------------------------------------------------------------------
    # QSortFilterProxyModel API
    # ------------------------------------------------------------------
    def filterAcceptsRow(self, row: int, parent) -> bool:  # type: ignore[override]
        if self._filter_mode is None and not self._search_text:
            return True

        source = self.sourceModel()
        if source is None:
            return False

        # Handle virtual header rows
        # If headers are implemented as rows, we generally want to accept them
        # or implement logic to hide empty headers (which is expensive).
        # For instant filtering, we accept headers.
        # We must use index-based lookup for Roles.IS_HEADER because it's a calculated role,
        # not stored in the fast_source dict usually (unless we update get_internal_row to handle it).
        index = source.index(row, 0, parent)
        if index.isValid() and bool(index.data(Roles.IS_HEADER)):
            return True

        if self._fast_source is not None:
            # Bypass Qt index creation and role lookups for raw performance.
            row_data = self._fast_source.get_internal_row(row)  # type: ignore
            if row_data is None:
                return False

            # --- OPTIMIZATION START ---
            # Use integer comparison for media type filtering
            if self._filter_mode == "videos":
                # 1 = Video, 0 = Image
                # We prioritize the integer check, but fallback to boolean if key is missing (legacy safety)
                media_type = row_data.get("media_type")
                if media_type is not None:
                     if media_type != 1:
                         return False
                elif not row_data.get("is_video"):
                    return False
            # --- OPTIMIZATION END ---

            if self._filter_mode == "live" and not row_data["is_live"]:
                return False
            if self._filter_mode == "favorites" and not row_data["featured"]:
                return False
            if self._search_text:
                rel = row_data["rel"]
                name = str(rel).casefold() if rel is not None else ""
                asset_id = row_data["id"]
                identifier = str(asset_id).casefold() if asset_id is not None else ""
                if self._search_text not in name and self._search_text not in identifier:
                    return False
            return True

        # Fallback for standard models
        if not index.isValid():
            return False

        if self._filter_mode == "videos":
             # Try integer role if available (needs custom role mapping or direct data access)
             # But here we are using standard Qt API.
             # We stick to the IS_VIDEO role which should be populated correctly.
             if not bool(index.data(Roles.IS_VIDEO)):
                 return False

        if self._filter_mode == "live" and not bool(index.data(Roles.IS_LIVE)):
            return False
        if self._filter_mode == "favorites" and not bool(index.data(Roles.FEATURED)):
            return False
        if self._search_text:
            rel = index.data(Roles.REL)
            name = str(rel).casefold() if rel is not None else ""
            asset_id = index.data(Roles.ASSET_ID)
            identifier = str(asset_id).casefold() if asset_id is not None else ""
            if self._search_text not in name and self._search_text not in identifier:
                return False
        return True

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:  # type: ignore[override]
        """Apply a timestamp-aware comparison when sorting by :data:`Roles.DT`."""

        if self.sortRole() == int(Roles.DT):
            # Optimization: If we are bulk filtering and the source is known to be
            # sorted by date (newest first), we can bypass expensive lookups.
            # We assume source rows are ordered 0..N (Newest..Oldest).
            # If SortOrder is Descending (Newest First), we want [0, 1, 2].
            # lessThan(0, 1) should be False. (0 > 1 -> False).
            if self._bypass_sort_optimization:
                return left.row() > right.row()

            if self._fast_source is not None:
                left_row = self._fast_source.get_internal_row(left.row())  # type: ignore
                right_row = self._fast_source.get_internal_row(right.row())  # type: ignore

                # Optimization: direct integer comparison for O(1) sorting speed.
                # We retrieve the pre-calculated microsecond timestamp (`ts`)
                # directly from the backing store to avoid parsing overhead.
                left_ts = -1
                if left_row is not None:
                    left_ts = left_row["ts"]

                right_ts = -1
                if right_row is not None:
                    right_ts = right_row["ts"]

                if left_ts == right_ts:
                    left_rel = str(left_row["rel"]) if left_row is not None else ""
                    right_rel = str(right_row["rel"]) if right_row is not None else ""
                    return left_rel < right_rel
                return left_ts < right_ts

            # Fallback for standard models (rarely used in the main grid).
            left_value = float(left.data(Roles.DT_SORT) if left.data(Roles.DT_SORT) is not None else float("-inf"))
            right_value = float(right.data(Roles.DT_SORT) if right.data(Roles.DT_SORT) is not None else float("-inf"))
            if left_value == right_value:
                left_rel = str(left.data(Roles.REL) or "")
                right_rel = str(right.data(Roles.REL) or "")
                return left_rel < right_rel
            return left_value < right_value
        return super().lessThan(left, right)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _reapply_default_sort(self) -> None:
        """Apply the cached default sort settings to the proxy model."""

        self.setSortRole(self._default_sort_role)
        super().sort(0, self._default_sort_order)

    def _on_source_model_reset(self) -> None:
        """Reapply chronological sorting after the source model resets."""

        self._reapply_default_sort()

    def _on_source_layout_changed(self, *_args) -> None:
        """Ensure layout changes keep the proxy aligned with the default sort."""

        self._reapply_default_sort()


"""Pre-configured grid view for the gallery layout."""

from __future__ import annotations

import math

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QAbstractItemView, QListView

from ..styles import modern_scrollbar_style
from .asset_grid import AssetGrid


class GalleryGridView(AssetGrid):
    """Dense icon-mode grid tuned for album browsing."""

    def __init__(self, parent=None) -> None:  # type: ignore[override]
        super().__init__(parent)
        icon_size = QSize(192, 192)
        self._selection_mode_enabled = False
        self.setSelectionMode(QListView.SelectionMode.SingleSelection)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setIconSize(icon_size)
        # We handle layout dynamically in resizeEvent, so we remove the fixed grid size
        # and set the spacing strictly to 4px as requested.
        self.setSpacing(4)
        self.setUniformItemSizes(True)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(True)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setWordWrap(False)
        self.setSelectionRectVisible(False)

        self._updating_style = False
        self._apply_scrollbar_style()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)

        viewport_width = self.viewport().width()
        min_item_width = 192
        gap = 4

        if viewport_width <= 0:
            return

        available_width = viewport_width - gap
        item_footprint = min_item_width + gap

        # Calculate target column count
        # Formula: N = floor((ViewportWidth - Gap) / (MinItemWidth + Gap))
        n_columns = math.floor(available_width / item_footprint)
        if n_columns <= 0:
            n_columns = 1

        # Calculate new item width
        # Formula: W = (ViewportWidth - (N + 1) * Gap) / N
        remaining_space = viewport_width - (n_columns + 1) * gap
        new_width = remaining_space / n_columns

        # Ensure we have an integer size. Using floor prevents overflow.
        new_width_int = int(new_width)

        # Apply new size if it changed
        current_size = self.iconSize()
        if current_size.width() != new_width_int:
            self.setIconSize(QSize(new_width_int, new_width_int))
            # QListView uses gridSize as the cell stride. To ensure 4px spacing between
            # items (centered in the cell), we add the gap to the grid size.
            self.setGridSize(QSize(new_width_int + gap, new_width_int + gap))

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.PaletteChange:
            if not self._updating_style:
                self._apply_scrollbar_style()
        super().changeEvent(event)

    def _apply_scrollbar_style(self) -> None:
        text_color = self.palette().color(QPalette.ColorRole.WindowText)
        style = modern_scrollbar_style(text_color)

        if self.styleSheet() == style:
            return

        self._updating_style = True
        try:
            self.setStyleSheet(style)
        finally:
            self._updating_style = False

    # ------------------------------------------------------------------
    # Selection mode toggling
    # ------------------------------------------------------------------
    def selection_mode_active(self) -> bool:
        """Return ``True`` when multi-selection mode is currently enabled."""

        return self._selection_mode_enabled

    def set_selection_mode_enabled(self, enabled: bool) -> None:
        """Switch between the default single selection and multi-selection mode."""

        desired_state = bool(enabled)
        if self._selection_mode_enabled == desired_state:
            return
        self._selection_mode_enabled = desired_state
        if desired_state:
            self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
            self.setSelectionRectVisible(True)
        else:
            self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            self.setSelectionRectVisible(False)
        # Long-press previews interfere with multi-selection because the delayed
        # activation steals focus from the selection rubber band. Disabling the
        # preview gesture keeps the pointer interactions unambiguous.
        self.set_preview_enabled(not desired_state)

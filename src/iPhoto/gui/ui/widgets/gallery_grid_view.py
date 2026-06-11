"""Pre-configured grid view for the gallery layout."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QModelIndex, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QGuiApplication, QMouseEvent, QPalette
from PySide6.QtWidgets import QAbstractItemView, QLabel, QListView

from iPhoto.gui.i18n import tr

from ..styles import modern_scrollbar_style
from .asset_grid import AssetGrid


class GalleryGridView(AssetGrid):
    """Dense icon-mode grid tuned for album browsing."""

    # Minimum width (and height) for grid items in pixels
    MIN_ITEM_WIDTH = 192

    # Gap between grid items (provides 1px padding on each side)
    ITEM_GAP = 2

    # Safety margin to prevent layout engine from dropping columns due to rounding
    # errors or strict boundary checks. This accounts for frame borders and
    # potential internal margins.
    SAFETY_MARGIN = 10

    def __init__(self, parent=None) -> None:  # type: ignore[override]
        super().__init__(parent)
        self._selection_mode_enabled = False
        self._empty_label = None
        self.setSelectionMode(QListView.SelectionMode.SingleSelection)
        self.setViewMode(QListView.ViewMode.IconMode)
        # Defer initial size calculation to resizeEvent to avoid rendering the
        # default 192px layout before the viewport dimensions are known.
        self.setSpacing(0)
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

        # Ensure the viewport paints an opaque background so the gallery is not
        # transparent when the main window uses WA_TranslucentBackground for
        # frameless chrome.
        vp = self.viewport()
        vp.setAutoFillBackground(True)

        self._empty_label = QLabel(vp)
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setWordWrap(True)
        self._empty_label.setStyleSheet("color: #86868b; font-size: 15px;")
        self._empty_label.hide()
        self.retranslate_ui()

        self._updating_style = False
        self._apply_scrollbar_style()
        self._update_empty_state()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)

        if self._empty_label is not None:
            self._empty_label.setGeometry(self.viewport().rect())

        self._apply_responsive_tile_size()

    def setItemDelegate(self, delegate) -> None:  # type: ignore[override]
        super().setItemDelegate(delegate)
        if self._apply_responsive_tile_size():
            self.doItemsLayout()
            self.viewport().update()

    def _apply_responsive_tile_size(self) -> bool:
        viewport_width = self.viewport().width()
        if viewport_width <= 0:
            return False

        # Determine how many columns can fit with the minimum size constraint.
        # We model the grid cell as (item_width + gap), which provides 1px padding
        # on each side of the item, resulting in a visual 2px gutter between items.
        # We subtract SAFETY_MARGIN to align with the cell_size calculation below,
        # ensuring we don't calculate a column count that immediately fails the
        # minimum size check.
        available_width = viewport_width - self.SAFETY_MARGIN
        num_cols = max(1, int(available_width / (self.MIN_ITEM_WIDTH + self.ITEM_GAP)))

        # Calculate the expanded cell size that will fill the available width.
        # We subtract SAFETY_MARGIN from the viewport width to prevent the layout
        # engine from dropping the last column due to rounding errors or strict
        # boundary checks.
        cell_size = int((viewport_width - self.SAFETY_MARGIN) / num_cols)
        new_item_width = cell_size - self.ITEM_GAP
        if new_item_width < self.MIN_ITEM_WIDTH:
            return False  # Don't update if it would make items too small

        current_size = self.iconSize().width()
        current_grid_width = self.gridSize().width()
        if current_size != new_item_width or current_grid_width != cell_size:
            new_size = QSize(new_item_width, new_item_width)
            self.setIconSize(new_size)
            self.setGridSize(QSize(cell_size, cell_size))

        delegate = self.itemDelegate()
        if hasattr(delegate, "set_base_size"):
            delegate.set_base_size(new_item_width)

        return True

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate_ui()
        elif event.type() == QEvent.Type.PaletteChange:
            if not self._updating_style:
                self._apply_scrollbar_style()
        super().changeEvent(event)

    def retranslate_ui(self) -> None:
        if self._empty_label is not None:
            self._empty_label.setText(
                tr(
                    "GalleryGridView",
                    "No media found. Click Rescan to scan this library.",
                ),
            )

    def _apply_scrollbar_style(self) -> None:
        # Fetch the global application palette to ensure we get the fresh theme colors,
        # ignoring any local stylesheet overrides that self.palette() might reflect.
        app = QGuiApplication.instance()
        palette = app.palette() if app else self.palette()

        text_color = palette.color(QPalette.ColorRole.WindowText)
        base_color = palette.color(QPalette.ColorRole.Base)

        # Propagate the base colour to the viewport palette so that
        # autoFillBackground paints an opaque surface even when the parent
        # window uses WA_TranslucentBackground.
        vp = self.viewport()
        vp_palette = vp.palette()
        vp_palette.setColor(QPalette.ColorRole.Window, base_color)
        vp_palette.setColor(QPalette.ColorRole.Base, base_color)
        vp.setPalette(vp_palette)

        # Enforce the background color on the QListView so it is painted opaque
        # in translucent/frameless window configurations.
        style = modern_scrollbar_style(text_color)
        bg_style = f"QListView {{ background-color: {base_color.name()}; }}"

        full_style = f"{style}\n{bg_style}"

        if self.styleSheet() == full_style:
            return

        self._updating_style = True
        try:
            self.setStyleSheet(full_style)
        finally:
            self._updating_style = False

    def setModel(self, model) -> None:  # type: ignore[override]
        previous = self.model()
        if previous is not None:
            try:
                previous.modelReset.disconnect(self._update_empty_state)
            except (RuntimeError, TypeError):
                pass
            try:
                previous.rowsInserted.disconnect(self._update_empty_state)
            except (RuntimeError, TypeError):
                pass
            try:
                previous.rowsRemoved.disconnect(self._update_empty_state)
            except (RuntimeError, TypeError):
                pass
        super().setModel(model)
        if model is not None:
            model.modelReset.connect(self._update_empty_state)
            model.rowsInserted.connect(self._update_empty_state)
            model.rowsRemoved.connect(self._update_empty_state)
        self._update_empty_state()

    def _update_empty_state(self) -> None:
        model = self.model()
        is_empty = model is None or model.rowCount() == 0
        if self._empty_label is None:
            return
        self._empty_label.setGeometry(self.viewport().rect())
        self._empty_label.setVisible(is_empty)

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

    # ------------------------------------------------------------------
    # Mouse Interaction
    # ------------------------------------------------------------------
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            viewport_pos = self._viewport_pos(event)
            # Check for favorite badge click
            index = self.indexAt(viewport_pos)
            if index.isValid():
                if self._is_favorite_badge_click(index, viewport_pos):
                    self._toggle_favorite(index)
                    return  # Don't propagate (avoids selection/play)

        super().mousePressEvent(event)

    def _is_favorite_badge_click(self, index, pos: QPoint) -> bool:
        # Reconstruct logic from BadgeRenderer.draw_favorite_badge
        rect = self.visualRect(index)
        if not rect.isValid(): return False

        # If rect contains pos, we need to check sub-rect for badge
        # Logic from BadgeRenderer:
        # padding = 5
        # icon_size = 16
        # badge_width = icon_size + padding * 2
        # badge_height = icon_size + padding * 2
        # badge_rect = QRect(
        #     rect.left() + 8,
        #     rect.bottom() - badge_height - 8,
        #     badge_width,
        #     badge_height,
        # )
        padding = 5
        icon_size = 16
        badge_width = icon_size + padding * 2
        badge_height = icon_size + padding * 2

        # Adjust local rect
        badge_rect = QRect(
            rect.left() + 8,
            rect.bottom() - badge_height - 8,
            badge_width,
            badge_height,
        )

        return badge_rect.contains(pos)

    def _toggle_favorite(self, index: QModelIndex) -> None:
        self.favoriteClicked.emit(index)

    favoriteClicked = Signal(QModelIndex)

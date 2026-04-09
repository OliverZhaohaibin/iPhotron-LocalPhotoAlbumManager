"""Pre-configured grid view for the gallery layout."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QRect, QSize, Qt, Signal, QPoint
from PySide6.QtGui import QMouseEvent, QPainter, QPaintEvent, QPalette, QColor, QGuiApplication
from PySide6.QtWidgets import QAbstractItemView, QListView, QLabel, QStyleOptionViewItem

from ..styles import modern_scrollbar_style
from .asset_grid import AssetGrid
from ..models.roles import Roles


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

        self._empty_label = QLabel(
            "No media found. Click Rescan to scan this library.",
            vp,
        )
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setWordWrap(True)
        self._empty_label.setStyleSheet("color: #86868b; font-size: 15px;")
        self._empty_label.hide()

        self._updating_style = False
        self._apply_scrollbar_style()
        self._update_empty_state()

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------
    def paintEvent(self, event: QPaintEvent) -> None:  # type: ignore[override]
        """Paint visible items and one extra row above/below the viewport.

        By pre-rendering items just outside the visible area we prevent the
        blank-flash that occurs when Qt recycles off-screen item widgets and
        the user scrolls them back into view.  The extra row is painted into
        the viewport surface but lies outside the visible region, so it is
        invisible to the user yet ready for immediate display on scroll.
        """
        # Let the base class handle the standard visible items first.
        super().paintEvent(event)

        cell_h = self.gridSize().height()
        cell_w = self.gridSize().width()
        if cell_h <= 0 or cell_w <= 0:
            return

        model = self.model()
        if model is None:
            return
        row_count = model.rowCount()
        if row_count == 0:
            return

        delegate = self.itemDelegate()
        if delegate is None:
            return

        vp = self.viewport()
        vp_rect = vp.rect()

        # Probe *inside* the viewport to find boundary items, then compute
        # adjacent rows via the column count.  Probing outside the viewport
        # (e.g. ``top()-1``) returns invalid indices in QAbstractItemView,
        # so we determine the row above/below arithmetically instead.
        cols = max(1, vp_rect.width() // cell_w)

        first_visible = self.indexAt(QPoint(vp_rect.left(), vp_rect.top()))
        bottom_visible = self.indexAt(QPoint(vp_rect.left(), vp_rect.bottom()))
        if not bottom_visible.isValid():
            # Last row may be partial; try the right edge.
            bottom_visible = self.indexAt(QPoint(vp_rect.right(), vp_rect.bottom()))

        # Determine the range of model rows for each extra band.
        extra_indices = []

        # --- Extra row ABOVE the viewport ---
        if first_visible.isValid():
            vis_row = first_visible.row() // cols
            if vis_row > 0:
                above_start = (vis_row - 1) * cols
                first_above = model.index(above_start, 0)
                above_rect = self.visualRect(first_above)
                if above_rect.isValid():
                    target_y = above_rect.top()
                    for r in range(above_start, min(above_start + cols, row_count)):
                        idx = model.index(r, 0)
                        r_rect = self.visualRect(idx)
                        if r_rect.isValid() and r_rect.top() == target_y:
                            extra_indices.append((idx, r_rect))

        # --- Extra row BELOW the viewport ---
        if bottom_visible.isValid():
            vis_row = bottom_visible.row() // cols
            below_start = (vis_row + 1) * cols
            if below_start < row_count:
                first_below = model.index(below_start, 0)
                below_rect = self.visualRect(first_below)
                if below_rect.isValid():
                    target_y = below_rect.top()
                    for r in range(below_start, min(below_start + cols, row_count)):
                        idx = model.index(r, 0)
                        r_rect = self.visualRect(idx)
                        if r_rect.isValid() and r_rect.top() == target_y:
                            extra_indices.append((idx, r_rect))

        if not extra_indices:
            return

        painter = QPainter(vp)
        try:
            for idx, item_rect in extra_indices:
                opt = QStyleOptionViewItem()
                self.initViewItemOption(opt)
                opt.rect = item_rect
                delegate.paint(painter, opt, idx)
        finally:
            painter.end()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        viewport_width = self.viewport().width()
        if viewport_width <= 0:
            return

        if self._empty_label is not None:
            self._empty_label.setGeometry(self.viewport().rect())


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
            return  # Don't update if it would make items too small

        current_size = self.iconSize().width()
        if current_size != new_item_width:
            new_size = QSize(new_item_width, new_item_width)
            self.setIconSize(new_size)
            self.setGridSize(QSize(cell_size, cell_size))

            delegate = self.itemDelegate()
            if hasattr(delegate, "set_base_size"):
                delegate.set_base_size(new_item_width)

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.PaletteChange:
            if not self._updating_style:
                self._apply_scrollbar_style()
        super().changeEvent(event)

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
            # Check for favorite badge click
            index = self.indexAt(event.pos())
            if index.isValid():
                if self._is_favorite_badge_click(index, event.pos()):
                    self._toggle_favorite(index)
                    return # Don't propagate (avoids selection/play)

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

    def _toggle_favorite(self, index):
        # We need access to the ViewModel to toggle.
        # But this is a View. It shouldn't depend on VM logic directly if possible.
        # However, we can use the model interface.
        # AssetModel (proxy) -> AssetListModel -> update_favorite
        model = self.model()
        # model might be AssetModel (proxy)
        # We need the source model to call update_favorite, or add it to proxy.
        # Let's check if model has toggle_favorite or similar.
        # The active gallery model adapter exposes update_favorite(row, is_favorite).
        # And MainCoordinator calls it.
        # Here we are deep in the view.
        # Ideally, we emit a signal 'favoriteToggled(index)'.
        # But for now, let's try to access the model method if available.

        # The cleanest way is to emit a signal from the view, and have coordinator listen.
        # But GalleryGridView is instantiated inside MainWindow UI setup, wiring is tricky.
        # Alternatively, rely on the delegate or model.

        # Let's assume the model has the method or we can map to source.
        source_model = getattr(model, "source_model", lambda: model)()
        if hasattr(source_model, "update_favorite"):
            is_fav = bool(index.data(Roles.FEATURED))
            # Toggle
            # We need the row in the source model?
            # The gallery adapter update_favorite takes (row, is_favorite).
            # If 'model' is proxy, we need to map index.
            if hasattr(model, "mapToSource"):
                source_index = model.mapToSource(index)
                if hasattr(source_model, "update_favorite"):
                    # We also need to call the service?
                    # The ViewModel update_favorite updates LOCAL state.
                    # The Service call is needed to persist DB.
                    # The View shouldn't call Service.
                    # So View -> Signal -> Coordinator -> Service -> ViewModel.

                    # Given constraints, I'll emit a custom signal on the View?
                    pass

        # Since I cannot easily wire a new signal up to MainCoordinator without changing MainCoordinator (which I can do),
        # I will add a signal 'favoriteClicked' to GalleryGridView.
        # And in MainCoordinator, connect it.
        self.favoriteClicked.emit(index)

    favoriteClicked = Signal(object) # QModelIndex

"""Pre-configured grid view for the gallery layout."""

from __future__ import annotations

from OpenGL import GL as gl
from PySide6.QtCore import QEvent, QRect, QSize, Qt, Signal, QPoint
from PySide6.QtGui import QMouseEvent, QPaintEvent, QPalette, QSurfaceFormat, QColor, QGuiApplication
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QAbstractItemView, QListView

from ..styles import modern_scrollbar_style
from .asset_grid import AssetGrid
from ..models.asset_model import Roles


class GalleryViewport(QOpenGLWidget):
    """OpenGL viewport that ensures an opaque background."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._bg_color: QColor | None = None

        # Disable the alpha buffer to prevent transparency issues with the DWM
        # when using a frameless window configuration.
        gl_format = QSurfaceFormat()
        gl_format.setAlphaBufferSize(0)
        self.setFormat(gl_format)

    def set_background_color(self, color: QColor) -> None:
        """Set the background color for the viewport."""
        self._bg_color = color
        self.update()

    def paintGL(self) -> None:
        """Clear the background to the theme's base color with full opacity."""
        self.clear_background()

    def clear_background(self) -> None:
        """Explicitly clear the viewport background."""
        if self._bg_color:
            base_color = self._bg_color
        else:
            base_color = self.palette().color(QPalette.ColorRole.Base)

        # Ensure we have a context before issuing GL commands
        self.makeCurrent()
        gl.glClearColor(base_color.redF(), base_color.greenF(), base_color.blueF(), 1.0)
        gl.glClear(gl.GL_COLOR_BUFFER_BIT)


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

        # Enable hardware acceleration for the viewport to improve scrolling performance
        gl_viewport = GalleryViewport()
        self.setViewport(gl_viewport)

        self._updating_style = False
        self._apply_scrollbar_style()

    def paintEvent(self, event: QPaintEvent) -> None:  # type: ignore[override]
        """Override paintEvent to force a GL clear before items are drawn."""
        viewport = self.viewport()
        if isinstance(viewport, GalleryViewport):
            viewport.clear_background()
        super().paintEvent(event)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        viewport_width = self.viewport().width()
        if viewport_width <= 0:
            return


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

        # Propagate background color to the viewport
        viewport = self.viewport()
        if isinstance(viewport, GalleryViewport):
            viewport.set_background_color(base_color)

        # We need to enforce the background color on the GalleryGridView (and its viewport)
        # because QOpenGLWidget in a translucent window context defaults to transparent.
        # By adding a background-color rule to the stylesheet, we ensure it's painted opaque.
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
        # In previous steps, I added update_favorite to AssetListViewModel.
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
            # AssetListViewModel.update_favorite takes (row, is_favorite).
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

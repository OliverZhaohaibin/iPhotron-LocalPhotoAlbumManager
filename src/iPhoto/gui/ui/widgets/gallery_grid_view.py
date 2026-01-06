"""Pre-configured grid view for the gallery layout."""

from __future__ import annotations

import logging

from OpenGL import GL as gl
from PySide6.QtCore import QEvent, QModelIndex, QSize, Qt, QTimer
from PySide6.QtGui import QPaintEvent, QPalette, QSurfaceFormat, QColor, QGuiApplication
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QAbstractItemView, QListView

from ..styles import modern_scrollbar_style
from .asset_grid import AssetGrid

logger = logging.getLogger(__name__)


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
    
    # Scroll threshold for triggering prefetch (percentage of scrollbar)
    # When scroll position reaches this threshold, trigger fetchMore
    PREFETCH_THRESHOLD = 0.8  # 80% - trigger load when 80% scrolled
    
    # Hysteresis to prevent rapid toggling when scrolling near threshold
    PREFETCH_HYSTERESIS = 0.1
    
    # Minimum delay between prefetch triggers to avoid flooding
    PREFETCH_DEBOUNCE_MS = 100

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
        
        # Scroll-triggered loading state
        self._last_scroll_value = 0
        self._prefetch_triggered = False
        self._prefetch_timer: QTimer | None = None
        
        # Connect scroll signal for lazy loading
        scrollbar = self.verticalScrollBar()
        if scrollbar:
            scrollbar.valueChanged.connect(self._on_scroll_changed)

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
    # Scroll-triggered lazy loading
    # ------------------------------------------------------------------
    def _on_scroll_changed(self, value: int) -> None:
        """Handle scroll position changes to trigger lazy loading.
        
        When the scroll position reaches PREFETCH_THRESHOLD of the total
        scroll range, trigger fetchMore() on the model to load the next page.
        """
        scrollbar = self.verticalScrollBar()
        if not scrollbar:
            return
        
        max_value = scrollbar.maximum()
        if max_value <= 0:
            # No scrollable content yet
            return
        
        # Calculate current scroll position as percentage
        scroll_ratio = value / max_value
        
        # Check if we've crossed the threshold (scrolling down)
        if scroll_ratio >= self.PREFETCH_THRESHOLD and not self._prefetch_triggered:
            self._prefetch_triggered = True
            logger.debug(
                "Scroll threshold reached (%.1f%%), scheduling fetchMore",
                scroll_ratio * 100,
            )
            
            # Debounce the prefetch trigger to avoid multiple calls
            self._schedule_fetch_more()
        
        # Reset the trigger when scrolling back up (below threshold with hysteresis)
        if scroll_ratio < self.PREFETCH_THRESHOLD - self.PREFETCH_HYSTERESIS:
            self._prefetch_triggered = False
        
        self._last_scroll_value = value

    def _schedule_fetch_more(self) -> None:
        """Schedule a debounced fetchMore call.
        
        Reuses a single timer instance to avoid memory leaks from creating
        new timers on every scroll event.
        """
        # Reuse existing timer if available
        if self._prefetch_timer is not None and self._prefetch_timer.isActive():
            return  # Already scheduled

        if self._prefetch_timer is None:
            self._prefetch_timer = QTimer(self)
            self._prefetch_timer.setSingleShot(True)
            self._prefetch_timer.timeout.connect(self._trigger_fetch_more)

        self._prefetch_timer.start(self.PREFETCH_DEBOUNCE_MS)

    def _trigger_fetch_more(self) -> None:
        """Trigger Qt's fetchMore mechanism on the model."""
        model = self.model()
        if model is None:
            return
        
        # Check if the model can fetch more data
        if model.canFetchMore(QModelIndex()):
            logger.debug("Triggering model.fetchMore() due to scroll")
            model.fetchMore(QModelIndex())
        else:
            logger.debug("Model has no more data to fetch")
        
        # Reset trigger so we can fetch again on next scroll
        self._prefetch_triggered = False

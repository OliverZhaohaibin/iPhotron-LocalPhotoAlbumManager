"""Pre-configured asset grid for the filmstrip view."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QModelIndex, QSize, Qt, Signal
from PySide6.QtGui import QPalette, QResizeEvent, QWheelEvent
from PySide6.QtWidgets import QListView, QSizePolicy, QStyleOptionViewItem

from .asset_grid import AssetGrid
from ..models.asset_model import Roles
from ..styles import modern_scrollbar_style


class FilmstripView(AssetGrid):
    """Horizontal filmstrip configured for quick navigation."""

    nextItemRequested = Signal()
    prevItemRequested = Signal()

    BASE_STYLESHEET = (
        "QListView { border: none; background-color: transparent; }"
        "QListView::item { border: none; padding: 0px; margin: 0px; }"
    )

    def __init__(self, parent=None) -> None:  # type: ignore[override]
        super().__init__(parent)
        self._base_height = 120
        self._spacing = 2
        self._default_ratio = 0.6
        icon_size = QSize(self._base_height, self._base_height)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setSelectionMode(QListView.SelectionMode.SingleSelection)
        self.setIconSize(icon_size)
        self.setSpacing(self._spacing)
        self.setUniformItemSizes(False)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(False)
        self.setHorizontalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setWordWrap(False)

        strip_height = self._base_height + 12
        self.setMinimumHeight(strip_height)
        self.setMaximumHeight(strip_height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._updating_style = False
        self._apply_scrollbar_style()

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.PaletteChange:
            if not self._updating_style:
                self._apply_scrollbar_style()
        super().changeEvent(event)

    def _apply_scrollbar_style(self) -> None:
        text_color = self.palette().color(QPalette.ColorRole.WindowText)
        style = modern_scrollbar_style(text_color, track_alpha=30)

        full_style = self.BASE_STYLESHEET + style

        if self.styleSheet() == full_style:
            return

        self._updating_style = True
        try:
            self.setStyleSheet(full_style)
        finally:
            self._updating_style = False

    def setModel(self, model) -> None:  # type: ignore[override]
        super().setModel(model)
        self.refresh_spacers()

    def resizeEvent(self, event: QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self.refresh_spacers()

    def refresh_spacers(self, current_proxy_index: QModelIndex | None = None) -> None:
        """Recalculate spacer padding and optionally use the provided index.

        Passing the proxy index of the current asset allows the view to
        compute spacing without walking the entire model, which keeps rapid
        navigation smooth when many items are present.
        """

        viewport = self.viewport()
        model = self.model()
        if viewport is None or model is None:
            return

        setter = getattr(model, "set_spacer_width", None)
        if setter is None:
            return

        viewport_width = viewport.width()
        if viewport_width <= 0:
            setter(0)
            return

        current_width = self._current_item_width(current_proxy_index)
        if current_width <= 0:
            current_width = self._narrow_item_width()

        padding = max(0, (viewport_width - current_width) // 2)
        setter(padding)

    def _current_item_width(self, current_proxy_index: QModelIndex | None = None) -> int:
        """Return the width of the active tile, preferring the supplied index."""
        model = self.model()
        delegate = self.itemDelegate()
        if model is None or delegate is None or model.rowCount() == 0:
            return self._narrow_item_width()

        current_index = None
        if (
            current_proxy_index is not None
            and current_proxy_index.isValid()
            and not bool(current_proxy_index.data(Roles.IS_SPACER))
        ):
            current_index = current_proxy_index

        # Optimization: Check the Selection Model directly.
        # This avoids iterating through thousands of rows.
        if current_index is None:
            selection_model = self.selectionModel()
            if selection_model is not None:
                candidate = selection_model.currentIndex()
                if candidate.isValid() and not bool(candidate.data(Roles.IS_SPACER)):
                    current_index = candidate

        # NOTE: The original 'for row in range(model.rowCount())' loop has been removed
        # to prevent UI freezing on large datasets.

        if current_index is None or not current_index.isValid():
            return self._narrow_item_width()

        option = QStyleOptionViewItem()
        option.initFrom(self)
        size = delegate.sizeHint(option, current_index)
        if size.width() > 0:
            return size.width()

        width = self._visual_width(current_index)
        if width > 0:
            return width
        return self._narrow_item_width()

    def _narrow_item_width(self) -> int:
        delegate = self.itemDelegate()
        model = self.model()
        if delegate is None or model is None or model.rowCount() == 0:
            ratio = self._delegate_ratio(delegate)
            return max(1, int(round(self._base_height * ratio)))

        option = QStyleOptionViewItem()
        option.initFrom(self)

        # Prefer any non-current item to approximate the narrow width.
        # We limit the search to avoid linear scans on large datasets.
        limit = min(model.rowCount(), 10)
        for row in range(limit):
            index = model.index(row, 0)
            if not index.isValid():
                continue
            if bool(index.data(Roles.IS_SPACER)):
                continue

            # Use the selection model to skip the current item without O(N) search
            selection_model = self.selectionModel()
            if selection_model is not None:
                current = selection_model.currentIndex()
                if current.isValid() and current == index:
                    continue
            elif bool(index.data(Roles.IS_CURRENT)):
                # Fallback to role check if selection model missing (unlikely but safe for small N)
                continue

            width = self._visual_width(index)
            if width <= 0:
                size = delegate.sizeHint(option, index)
                width = size.width()
            if width > 0:
                return width

        # Fall back to the delegate ratio if needed.
        ratio = self._delegate_ratio(delegate)
        return max(1, int(round(self._base_height * ratio)))

    def _visual_width(self, index) -> int:
        rect = self.visualRect(index)
        width = rect.width()
        return int(width)

    def _delegate_ratio(self, delegate) -> float:
        ratio = self._default_ratio
        candidate = getattr(delegate, "_FILMSTRIP_RATIO", None)
        if isinstance(candidate, (int, float)) and candidate > 0:
            ratio = float(candidate)
        return ratio

    # ------------------------------------------------------------------
    # Event handling
    # ------------------------------------------------------------------
    def wheelEvent(self, event: QWheelEvent) -> None:  # type: ignore[override]
        """Always request navigation when the user scrolls over the filmstrip.

        The filmstrip acts as a lightweight transport control, so the wheel gesture should
        consistently move to the previous or next asset regardless of the global wheel setting.
        We only bypass this logic when the user explicitly performs a Ctrl-modified scroll so the
        platform default zoom gesture can bubble up to other widgets.
        """
        if event.modifiers() & Qt.ControlModifier:
            super().wheelEvent(event)
            return

        model = self.model()
        if model is None or model.rowCount() == 0:
            super().wheelEvent(event)
            return

        # Evaluate the scroll delta as a simple direction indicator so every
        # wheel tick translates to a single navigation step.  This prevents
        # high-resolution trackpads from flooding the controller with requests.
        delta = event.angleDelta().y() or event.angleDelta().x()
        if delta == 0:
            pixel_delta = event.pixelDelta().y() or event.pixelDelta().x()
            delta = pixel_delta
        if delta == 0:
            super().wheelEvent(event)
            return

        if delta < 0:
            self.nextItemRequested.emit()
        else:
            self.prevItemRequested.emit()
        event.accept()

    # ------------------------------------------------------------------
    # Programmatic scrolling helpers
    # ------------------------------------------------------------------
    def center_on_index(self, index: QModelIndex) -> None:
        """Scroll the view so *index* is visually centred in the viewport."""
        if not index.isValid():
            return

        item_rect = self.visualRect(index)
        if not item_rect.isValid():
            return

        viewport_width = self.viewport().width()
        if viewport_width <= 0:
            return

        target_left = (viewport_width - item_rect.width()) / 2.0
        scroll_delta = item_rect.left() - target_left
        scrollbar = self.horizontalScrollBar()
        scrollbar.setValue(scrollbar.value() + int(scroll_delta))

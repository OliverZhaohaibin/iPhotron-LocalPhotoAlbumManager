"""Pre-configured asset grid for the filmstrip view."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QModelIndex, QSize, Qt, Signal, QTimer, QItemSelectionModel
from PySide6.QtGui import QPalette, QResizeEvent, QWheelEvent, QShowEvent
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QListView,
    QSizePolicy,
    QStyleOptionViewItem,
)

from .asset_grid import AssetGrid
from ..models.roles import Roles
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
        self._selection_model = None
        self._center_timer = QTimer(self)
        self._center_timer.setSingleShot(True)
        self._center_timer.timeout.connect(self._apply_centering)
        self._center_attempts = 0
        self._pending_center_reveal = False
        self._recentering = False
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(1.0)
        self.setGraphicsEffect(self._opacity_effect)
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
        old = self.model()
        if old is not None:
            old.dataChanged.disconnect(self._on_data_changed)

        super().setModel(model)
        if model is not None:
            model.dataChanged.connect(self._on_data_changed)
        self._connect_selection_model()
        self.refresh_spacers()

    def _on_data_changed(self, top: QModelIndex, bottom: QModelIndex, roles: list[int] = []) -> None:
        """Handle data changes to trigger layout updates if necessary."""
        # If no roles specified (all changed) or IS_CURRENT changed, we need to relayout
        if not roles or Roles.IS_CURRENT in roles:
            # Re-calculating layout is expensive, so check if we need it.
            # QListView with uniformItemSizes=False might need a nudge.
            self.scheduleDelayedItemsLayout()
            self.refresh_spacers(top)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        # Keep the filmstrip invisible until we have re-centered on the current asset.
        self._set_opacity(0.0)
        self._schedule_center_current(reveal=True)

    def resizeEvent(self, event: QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self.refresh_spacers()
        self._schedule_center_current()

    def scrollContentsBy(self, dx: int, dy: int) -> None:  # type: ignore[override]
        super().scrollContentsBy(dx, dy)
        if self._recentering:
            return
        if dx != 0:
            self._schedule_center_current()

    def _connect_selection_model(self) -> None:
        selection_model = self.selectionModel()
        if selection_model is self._selection_model:
            return

        if self._selection_model is not None:
            try:
                self._selection_model.currentChanged.disconnect(self._on_current_changed)
            except (RuntimeError, TypeError):
                pass

        self._selection_model = selection_model
        if selection_model is not None:
            selection_model.currentChanged.connect(self._on_current_changed)

    def _on_current_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        if current.isValid():
            self.refresh_spacers(current)
        self._schedule_center_current()

    def _schedule_center_current(self, *, reveal: bool = False) -> None:
        if reveal:
            self._pending_center_reveal = True
        if self._center_timer.isActive():
            return
        self._center_attempts = 0
        self._center_timer.start(0)

    def _apply_centering(self) -> None:
        selection_model = self.selectionModel()
        if selection_model is None:
            self._finalize_centering()
            return

        current = self._resolve_current_index(selection_model)
        if not current.isValid():
            self._finalize_centering()
            return

        if self._center_current_index(current):
            self._finalize_centering()
            return

        self._center_attempts += 1
        if self._center_attempts >= 5:
            self._finalize_centering()
            return

        self._center_timer.start(16)

    def _resolve_current_index(self, selection_model) -> QModelIndex:
        current = selection_model.currentIndex()
        if current.isValid():
            return current

        model = self.model()
        if model is None or model.rowCount() <= 0:
            return QModelIndex()

        # Fall back to the view model's current row when the selection has been cleared.
        # This lets the displayed asset drive the filmstrip position even after visibility
        # toggles or model resets.
        start = model.index(0, 0)
        matches = model.match(start, Roles.IS_CURRENT, True, 1, Qt.MatchExactly)
        if matches:
            match = matches[0]
            selection_model.setCurrentIndex(
                match, QItemSelectionModel.ClearAndSelect
            )
            return match
        return QModelIndex()

    def _center_current_index(self, index: QModelIndex) -> bool:
        item_rect = self.visualRect(index)
        if not item_rect.isValid():
            return False
        if self.viewport().width() <= 0:
            return False

        self._recentering = True
        try:
            self.center_on_index(index)
        finally:
            self._recentering = False
        return True

    def _finalize_centering(self) -> None:
        if self._pending_center_reveal:
            self._set_opacity(1.0)
            self._pending_center_reveal = False

    def _set_opacity(self, value: float) -> None:
        if self._opacity_effect.opacity() == value:
            return
        self._opacity_effect.setOpacity(value)

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

        # If we just adjusted spacers, we likely need to re-center
        if current_proxy_index is not None and current_proxy_index.isValid():
            self._schedule_center_current()

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

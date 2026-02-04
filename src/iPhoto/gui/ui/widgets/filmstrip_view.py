"""Pre-configured asset grid for the filmstrip view."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QModelIndex, QSize, Qt, Signal, QTimer, QItemSelectionModel
from PySide6.QtGui import QPalette, QResizeEvent, QWheelEvent
from PySide6.QtWidgets import QListView, QSizePolicy

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
        icon_size = QSize(self._base_height, self._base_height)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setSelectionMode(QListView.SelectionMode.SingleSelection)
        self.setIconSize(icon_size)
        self.setSpacing(self._spacing)
        self.setUniformItemSizes(True)
        self.setResizeMode(QListView.ResizeMode.Fixed)
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
        self._pending_scroll_value: int | None = None
        self._pending_center_row: int | None = None
        self._last_known_center_row: int | None = None
        self._restore_scheduled = False
        self._viewport_padding = 0
        self._apply_item_size()
        self._update_viewport_margins()
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
        old_selection_model = self.selectionModel()
        old_about_to_reset = getattr(old, "modelAboutToBeReset", None)
        old_reset = getattr(old, "modelReset", None)
        old_rows_inserted = getattr(old, "rowsInserted", None)
        old_rows_removed = getattr(old, "rowsRemoved", None)
        old_layout_about = getattr(old, "layoutAboutToBeChanged", None)
        old_layout_changed = getattr(old, "layoutChanged", None)
        if old is not None:
            old.dataChanged.disconnect(self._on_data_changed)
            if old_about_to_reset is not None:
                try:
                    old_about_to_reset.disconnect(self._capture_scroll_state)
                except (RuntimeError, TypeError):  # pragma: no cover - Qt signal glue
                    pass
            if old_reset is not None:
                try:
                    old_reset.disconnect(self._schedule_restore_scroll)
                except (RuntimeError, TypeError):  # pragma: no cover - Qt signal glue
                    pass
            if old_rows_inserted is not None:
                try:
                    old_rows_inserted.disconnect(self._schedule_restore_scroll)
                except (RuntimeError, TypeError):  # pragma: no cover - Qt signal glue
                    pass
            if old_rows_removed is not None:
                try:
                    old_rows_removed.disconnect(self._on_rows_removed)
                except (RuntimeError, TypeError):  # pragma: no cover - Qt signal glue
                    pass
            if old_layout_about is not None:
                try:
                    old_layout_about.disconnect(self._schedule_restore_scroll)
                except (RuntimeError, TypeError):  # pragma: no cover - Qt signal glue
                    pass
            if old_layout_changed is not None:
                try:
                    old_layout_changed.disconnect(self._schedule_restore_scroll)
                except (RuntimeError, TypeError):  # pragma: no cover - Qt signal glue
                    pass
        if old_selection_model is not None:
            try:
                old_selection_model.currentChanged.disconnect(self._on_current_changed)
            except (RuntimeError, TypeError):  # pragma: no cover - Qt signal glue
                pass

        super().setModel(model)
        if model is not None:
            model.dataChanged.connect(self._on_data_changed)
            model.modelAboutToBeReset.connect(self._capture_scroll_state)
            model.modelReset.connect(self._schedule_restore_scroll)
            model.rowsInserted.connect(self._schedule_restore_scroll)
            model.rowsRemoved.connect(self._on_rows_removed)
            model.layoutAboutToBeChanged.connect(self._schedule_restore_scroll)
            model.layoutChanged.connect(self._schedule_restore_scroll)
        selection_model = self.selectionModel()
        if selection_model is not None:
            selection_model.currentChanged.connect(self._on_current_changed)

        self._apply_item_size()
        self._update_viewport_margins()

    def _on_data_changed(
        self,
        top: QModelIndex,
        bottom: QModelIndex,
        roles: list[int] | None = None,
    ) -> None:
        """Handle data changes that should trigger layout updates."""
        return

    def resizeEvent(self, event: QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._update_viewport_margins()
        self._schedule_restore_scroll("resize")

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self._update_viewport_margins()
        self._schedule_restore_scroll("show")

    def hideEvent(self, event) -> None:  # type: ignore[override]
        self._capture_scroll_state()
        super().hideEvent(event)

    def _capture_scroll_state(self) -> None:
        """Remember scroll position and current selection before model/layout changes."""
        scrollbar = self.horizontalScrollBar()
        current_row = None
        selection_model = self.selectionModel()
        if selection_model is not None:
            current = selection_model.currentIndex()
            if current.isValid() and not bool(current.data(Roles.IS_SPACER)):
                current_row = current.row()
        scroll_value = scrollbar.value()
        if current_row is None and self._last_known_center_row is not None:
            current_row = self._last_known_center_row

        if current_row is None and scroll_value == 0:
            return

        self._pending_scroll_value = scroll_value
        self._pending_center_row = current_row
        if current_row is not None:
            self._last_known_center_row = current_row

    def _on_rows_removed(self, parent: QModelIndex, start: int, end: int) -> None:
        self._schedule_restore_scroll("rows_removed")

    def _schedule_restore_scroll(self, reason: str | None = None) -> None:
        if self._restore_scheduled:
            return
        if self._pending_scroll_value is None and self._pending_center_row is None:
            return
        self._restore_scheduled = True
        QTimer.singleShot(0, lambda: self._restore_scroll_state(reason or "unknown"))

    def _restore_scroll_state(self, reason: str) -> None:
        self._restore_scheduled = False
        model = self.model()
        if self._pending_scroll_value is None and self._pending_center_row is None:
            return
        if model is None or model.rowCount() == 0:
            return

        scroll_value = self._pending_scroll_value
        center_row = self._pending_center_row
        scrollbar = self.horizontalScrollBar()
        restored = False
        if center_row is not None and 0 <= center_row < model.rowCount():
            index = model.index(center_row, 0)
            if index.isValid() and not bool(index.data(Roles.IS_SPACER)):
                selection_model = self.selectionModel()
                if selection_model is not None:
                    current = selection_model.currentIndex()
                    if not current.isValid() or current.row() != center_row:
                        selection_model.setCurrentIndex(index, QItemSelectionModel.ClearAndSelect)
                self.center_on_index(index)

        elif scroll_value is not None:
            scrollbar.setValue(scroll_value)

        self._pending_scroll_value = None
        self._pending_center_row = None

    def _update_viewport_margins(self) -> None:
        viewport = self.viewport()
        if viewport is None:
            return
        viewport_width = viewport.width() + (self._viewport_padding * 2)
        if viewport_width <= 0:
            if self._viewport_padding != 0:
                self._viewport_padding = 0
                self.setViewportMargins(0, 0, 0, 0)
            return
        item_width = self._filmstrip_item_width()
        padding = max(0, (viewport_width - item_width) // 2)
        if padding == self._viewport_padding:
            return
        self._viewport_padding = padding
        self.setViewportMargins(padding, 0, padding, 0)

    def _apply_item_size(self) -> None:
        size = self._filmstrip_item_size()
        self.setGridSize(size)

    def _filmstrip_item_size(self) -> QSize:
        width = self._filmstrip_item_width()
        return QSize(width, self._base_height)

    def _filmstrip_item_width(self) -> int:
        ratio = self._delegate_ratio(self.itemDelegate())
        return max(1, int(round(self._base_height * ratio)))

    def _delegate_ratio(self, delegate) -> float:
        ratio = self._default_ratio
        candidate = getattr(delegate, "_FILMSTRIP_RATIO", None)
        if isinstance(candidate, (int, float)) and candidate > 0:
            ratio = float(candidate)
        return ratio

    def _on_current_changed(
        self, current: QModelIndex, previous: QModelIndex
    ) -> None:
        """Track the last non-spacer current row for capture/restore centering logic."""
        if current.isValid() and not bool(current.data(Roles.IS_SPACER)):
            self._last_known_center_row = current.row()


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

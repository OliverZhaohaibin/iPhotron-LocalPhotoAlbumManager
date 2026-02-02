"""Pre-configured asset grid for the filmstrip view."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QModelIndex, QSize, Qt, Signal, QTimer, QItemSelectionModel
from PySide6.QtGui import QPalette, QResizeEvent, QWheelEvent
from PySide6.QtWidgets import QListView, QSizePolicy, QStyleOptionViewItem

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
        self._pending_scroll_value: int | None = None
        self._pending_center_row: int | None = None
        self._restore_scheduled = False
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
        if old_selection_model is not None:
            try:
                old_selection_model.currentChanged.disconnect(self._on_current_changed_debug)
            except (RuntimeError, TypeError):  # pragma: no cover - Qt signal glue
                pass

        super().setModel(model)
        if model is not None:
            model.dataChanged.connect(self._on_data_changed)
            model.modelAboutToBeReset.connect(self._capture_scroll_state)
            model.modelReset.connect(self._schedule_restore_scroll)
            model.rowsInserted.connect(self._schedule_restore_scroll)
        selection_model = self.selectionModel()
        if selection_model is not None:
            selection_model.currentChanged.connect(self._on_current_changed_debug)

        self.refresh_spacers()

    def _on_data_changed(self, top: QModelIndex, bottom: QModelIndex, roles: list[int] = []) -> None:
        """Handle data changes to trigger layout updates if necessary."""
        # If no roles specified (all changed) or IS_CURRENT changed, we need to relayout
        if not roles or Roles.IS_CURRENT in roles:
            # Re-calculating layout is expensive, so check if we need it.
            # QListView with uniformItemSizes=False might need a nudge.
            self.scheduleDelayedItemsLayout()
            self.refresh_spacers(top)

    def resizeEvent(self, event: QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        print(
            "[FilmstripDebug] resize_event",
            {
                "old_size": {
                    "width": event.oldSize().width(),
                    "height": event.oldSize().height(),
                },
                "new_size": {
                    "width": event.size().width(),
                    "height": event.size().height(),
                },
                "viewport_width": self.viewport().width() if self.viewport() else None,
                "scroll_value": self.horizontalScrollBar().value(),
            },
        )
        self.refresh_spacers()
        self._schedule_restore_scroll("resize")

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
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
        if current_row is None and scroll_value == 0:
            print(
                "[FilmstripDebug] capture_scroll_state: skipped (no selection, scroll=0)",
                {"visible": self.isVisible()},
            )
            return

        self._pending_scroll_value = scroll_value
        self._pending_center_row = current_row
        print(
            "[FilmstripDebug] capture_scroll_state",
            {
                "scroll_value": self._pending_scroll_value,
                "center_row": self._pending_center_row,
                "visible": self.isVisible(),
            },
        )

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
            print(
                "[FilmstripDebug] restore_scroll_state: skipped (no pending state)",
                {"reason": reason},
            )
            return
        if model is None or model.rowCount() == 0:
            print(
                "[FilmstripDebug] restore_scroll_state: skipped (no rows)",
                {"reason": reason, "row_count": model.rowCount() if model is not None else None},
            )
            return

        scroll_value = self._pending_scroll_value
        center_row = self._pending_center_row
        scrollbar = self.horizontalScrollBar()
        restored = False
        if center_row is not None and 0 <= center_row < model.rowCount():
            index = model.index(center_row, 0)
            if index.isValid() and not bool(index.data(Roles.IS_SPACER)):
                selection_model = self.selectionModel()
                if selection_model is not None and not selection_model.currentIndex().isValid():
                    selection_model.setCurrentIndex(index, QItemSelectionModel.ClearAndSelect)
                self.center_on_index(index)
                restored = True

        if not restored and scroll_value is not None:
            scrollbar.setValue(scroll_value)
            restored = True

        print(
            "[FilmstripDebug] restore_scroll_state",
            {
                "reason": reason,
                "restored": restored,
                "scroll_value": scrollbar.value(),
                "scroll_min": scrollbar.minimum(),
                "scroll_max": scrollbar.maximum(),
                "center_row": center_row,
            },
        )
        self._pending_scroll_value = None
        self._pending_center_row = None

    def refresh_spacers(self, current_proxy_index: QModelIndex | None = None) -> None:
        """Recalculate spacer padding and optionally use the provided index.

        Passing the proxy index of the current asset allows the view to
        compute spacing without walking the entire model, which keeps rapid
        navigation smooth when many items are present.
        """

        viewport = self.viewport()
        model = self.model()
        if viewport is None or model is None:
            print(
                "[FilmstripDebug] refresh_spacers: skipped (missing viewport/model)",
                {"has_viewport": viewport is not None, "has_model": model is not None},
            )
            return

        setter = getattr(model, "set_spacer_width", None)
        if setter is None:
            print("[FilmstripDebug] refresh_spacers: skipped (model has no set_spacer_width)")
            return

        viewport_width = viewport.width()
        selection_model = self.selectionModel()
        current_selection = selection_model.currentIndex() if selection_model is not None else None
        if viewport_width <= 0:
            print(
                "[FilmstripDebug] refresh_spacers: viewport width <= 0",
                {"viewport_width": viewport_width},
            )
            setter(0)
            return

        current_width = self._current_item_width(current_proxy_index)
        if current_width <= 0:
            current_width = self._narrow_item_width()

        padding = max(0, (viewport_width - current_width) // 2)
        print(
            "[FilmstripDebug] refresh_spacers: computed padding",
            {
                "viewport_width": viewport_width,
                "current_width": current_width,
                "padding": padding,
                "row_count": model.rowCount(),
                "current_proxy_index_valid": (
                    current_proxy_index.isValid() if current_proxy_index is not None else None
                ),
                "selection_index_valid": (
                    current_selection.isValid() if current_selection is not None else None
                ),
                "selection_row": current_selection.row() if current_selection is not None else None,
                "selection_is_spacer": (
                    bool(current_selection.data(Roles.IS_SPACER))
                    if current_selection is not None and current_selection.isValid()
                    else None
                ),
            },
        )
        setter(padding)

    def _current_item_width(self, current_proxy_index: QModelIndex | None = None) -> int:
        """Return the width of the active tile, preferring the supplied index."""
        model = self.model()
        delegate = self.itemDelegate()
        if model is None or delegate is None or model.rowCount() == 0:
            print(
                "[FilmstripDebug] current_item_width: fallback (missing model/delegate)",
                {
                    "has_model": model is not None,
                    "has_delegate": delegate is not None,
                    "row_count": model.rowCount() if model is not None else None,
                },
            )
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
            print("[FilmstripDebug] current_item_width: no valid current index")
            return self._narrow_item_width()

        option = QStyleOptionViewItem()
        option.initFrom(self)
        size = delegate.sizeHint(option, current_index)
        if size.width() > 0:
            print(
                "[FilmstripDebug] current_item_width: sizeHint",
                {"row": current_index.row(), "width": size.width()},
            )
            return size.width()

        width = self._visual_width(current_index)
        if width > 0:
            print(
                "[FilmstripDebug] current_item_width: visualRect width",
                {"row": current_index.row(), "width": width},
            )
            return width
        return self._narrow_item_width()

    def _narrow_item_width(self) -> int:
        delegate = self.itemDelegate()
        model = self.model()
        if delegate is None or model is None or model.rowCount() == 0:
            ratio = self._delegate_ratio(delegate)
            print(
                "[FilmstripDebug] narrow_item_width: fallback ratio",
                {
                    "ratio": ratio,
                    "has_delegate": delegate is not None,
                    "row_count": model.rowCount() if model is not None else None,
                },
            )
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
                print(
                    "[FilmstripDebug] narrow_item_width: sampled width",
                    {"row": index.row(), "width": width},
                )
                return width

        # Fall back to the delegate ratio if needed.
        ratio = self._delegate_ratio(delegate)
        print(
            "[FilmstripDebug] narrow_item_width: delegate ratio fallback",
            {"ratio": ratio},
        )
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

    def _on_current_changed_debug(
        self, current: QModelIndex, previous: QModelIndex
    ) -> None:  # pragma: no cover - debug logging
        scrollbar = self.horizontalScrollBar()
        print(
            "[FilmstripDebug] current_changed",
            {
                "current_valid": current.isValid(),
                "current_row": current.row(),
                "current_is_spacer": bool(current.data(Roles.IS_SPACER))
                if current.isValid()
                else None,
                "previous_valid": previous.isValid(),
                "previous_row": previous.row(),
                "scroll_value": scrollbar.value(),
                "scroll_min": scrollbar.minimum(),
                "scroll_max": scrollbar.maximum(),
                "viewport_width": self.viewport().width() if self.viewport() else None,
            },
        )

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
            print("[FilmstripDebug] center_on_index: invalid index, abort")
            return

        item_rect = self.visualRect(index)
        if not item_rect.isValid():
            print(
                "[FilmstripDebug] center_on_index: invalid item rect, abort",
                {"row": index.row(), "col": index.column()},
            )
            return

        viewport_width = self.viewport().width()
        if viewport_width <= 0:
            print(
                "[FilmstripDebug] center_on_index: viewport width <= 0, abort",
                {"viewport_width": viewport_width},
            )
            return

        target_left = (viewport_width - item_rect.width()) / 2.0
        scroll_delta = item_rect.left() - target_left
        scrollbar = self.horizontalScrollBar()
        print(
            "[FilmstripDebug] center_on_index: applying scroll",
            {
                "row": index.row(),
                "col": index.column(),
                "item_rect": {
                    "x": item_rect.x(),
                    "y": item_rect.y(),
                    "width": item_rect.width(),
                    "height": item_rect.height(),
                },
                "viewport_width": viewport_width,
                "target_left": target_left,
                "scroll_delta": scroll_delta,
                "scroll_before": scrollbar.value(),
                "scroll_min": scrollbar.minimum(),
                "scroll_max": scrollbar.maximum(),
                "page_step": scrollbar.pageStep(),
            },
        )
        scrollbar.setValue(scrollbar.value() + int(scroll_delta))
        print(
            "[FilmstripDebug] center_on_index: scroll applied",
            {"scroll_after": scrollbar.value()},
        )

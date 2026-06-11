"""Interactive asset grid with click, long-press, and drop handling."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, List, Optional

from PySide6.QtCore import QModelIndex, QPoint, QTimer, Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent, QMouseEvent
from PySide6.QtWidgets import QApplication, QListView

from ....config import LONG_PRESS_THRESHOLD_MS

_IS_DARWIN = sys.platform == "darwin"
_IS_LINUX = sys.platform == "linux"


class AssetGrid(QListView):
    """Grid view that distinguishes between clicks and long presses."""

    # ``QModelIndex`` provides a precise Qt meta-type that aligns with all
    # slots consuming the signal.  Nuitka requires this explicit annotation so
    # it can match the signal signature without falling back to ``object``.
    itemClicked = Signal(QModelIndex)
    requestPreview = Signal(QModelIndex)
    previewReleased = Signal()
    previewCancelled = Signal()
    visibleRowsChanged = Signal(int, int)
    modelAboutToChange = Signal(object)
    modelChanged = Signal(object)

    _DRAG_CANCEL_THRESHOLD = 6

    def __init__(self, parent=None) -> None:  # type: ignore[override]
        super().__init__(parent)
        self._press_timer = QTimer(self)
        self._press_timer.setSingleShot(True)
        self._press_timer.timeout.connect(self._on_long_press_timeout)
        self._pressed_index: Optional[QModelIndex] = None
        self._press_pos: Optional[QPoint] = None
        self._long_press_active = False
        self._suppress_next_preview_leave = False
        self._update_timer = QTimer(self)
        self._update_timer.setSingleShot(True)
        self._update_timer.setInterval(16)
        self._update_timer.timeout.connect(self._emit_visible_rows)
        self._viewport_update_timer = QTimer(self)
        self._viewport_update_timer.setSingleShot(True)
        self._viewport_update_timer.setInterval(0)
        self._viewport_update_timer.timeout.connect(self._flush_viewport_update)
        self._viewport_layout_pending = False
        self._visible_range: Optional[tuple[int, int]] = None
        self._model = None
        self._external_drop_enabled = False
        self._drop_handler: Optional[Callable[[List[Path]], None]] = None
        self._drop_validator: Optional[Callable[[List[Path]], bool]] = None
        self._preview_enabled = True

    # ------------------------------------------------------------------
    # Mouse event handling
    # ------------------------------------------------------------------
    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            viewport_pos = self._viewport_pos(event)
            index = self.indexAt(viewport_pos)
            if index.isValid():
                self._pressed_index = index
                self._press_pos = QPoint(viewport_pos)
                self._long_press_active = False
                self._press_timer.stop()
                if self._preview_enabled:
                    self._press_timer.start(LONG_PRESS_THRESHOLD_MS)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if self._press_pos is not None and not self._long_press_active:
            viewport_pos = self._viewport_pos(event)
            if (viewport_pos - self._press_pos).manhattanLength() > self._DRAG_CANCEL_THRESHOLD:
                self._cancel_pending_long_press()
        elif self._long_press_active and self._pressed_index is not None:
            viewport_pos = self._viewport_pos(event)
            index = self.indexAt(viewport_pos)
            if not index.isValid() or index != self._pressed_index:
                self.previewCancelled.emit()
                self._reset_state()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        was_long_press = self._long_press_active
        index = self._pressed_index
        self._cancel_pending_long_press()
        if event.button() == Qt.MouseButton.LeftButton and index is not None:
            if was_long_press:
                self.previewReleased.emit()
            elif index.isValid():
                self.itemClicked.emit(index)
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        if self._long_press_active:
            if self._should_suppress_preview_leave():
                self._suppress_next_preview_leave = False
                super().leaveEvent(event)
                return
            self.previewCancelled.emit()
        self._cancel_pending_long_press()
        super().leaveEvent(event)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        QTimer.singleShot(0, self._schedule_visible_rows_update)

    # ------------------------------------------------------------------
    # Preview configuration
    # ------------------------------------------------------------------
    def set_preview_enabled(self, enabled: bool) -> None:
        """Enable or disable the long-press preview workflow."""

        self._preview_enabled = bool(enabled)
        if not self._preview_enabled:
            self._cancel_pending_long_press()

    def preview_enabled(self) -> bool:
        """Return ``True`` when long-press previews are currently allowed."""

        return self._preview_enabled

    # ------------------------------------------------------------------
    # External file drop configuration
    # ------------------------------------------------------------------
    def configure_external_drop(
        self,
        *,
        handler: Optional[Callable[[List[Path]], None]] = None,
        validator: Optional[Callable[[List[Path]], bool]] = None,
    ) -> None:
        """Enable or disable external drop support for the grid view.

        Parameters
        ----------
        handler:
            Callable invoked when a valid drop operation completes.  When
            ``None`` the grid reverts to its default behaviour and external
            drops are ignored entirely.
        validator:
            Optional callable used to preflight an incoming drag.  The
            validator receives the list of candidate file paths and returns
            ``True`` to accept the drag or ``False`` to reject it.  When left
            unspecified every drop that provides at least one local file is
            considered acceptable.
        """

        self._drop_handler = handler
        self._drop_validator = validator
        self._external_drop_enabled = handler is not None
        self.setAcceptDrops(self._external_drop_enabled)
        # The visual items live inside the viewport object, so we must enable drops there
        # as well; otherwise Qt will refuse to deliver drag/drop events to the grid.
        self.viewport().setAcceptDrops(self._external_drop_enabled)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if _IS_LINUX:
            self._viewport_layout_pending = True
            self._schedule_viewport_update()
        self._schedule_visible_rows_update()

    def scrollContentsBy(self, dx: int, dy: int) -> None:  # type: ignore[override]
        if _IS_LINUX:
            # Avoid the synchronous layout/repaint path used by the old Linux
            # tearing workaround. The scrollbar value is already current, so
            # one deferred update can paint the latest position after bursts.
            self._schedule_viewport_update()
        else:
            super().scrollContentsBy(dx, dy)
        self._schedule_visible_rows_update()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # type: ignore[override]
        if not self._external_drop_enabled:
            super().dragEnterEvent(event)
            return
        paths = self._extract_local_files(event)
        if not paths:
            event.ignore()
            return
        if self._drop_validator is not None and not self._drop_validator(paths):
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:  # type: ignore[override]
        if not self._external_drop_enabled:
            super().dragMoveEvent(event)
            return
        paths = self._extract_local_files(event)
        if not paths:
            event.ignore()
            return
        if self._drop_validator is not None and not self._drop_validator(paths):
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()

    def dropEvent(self, event: QDropEvent) -> None:  # type: ignore[override]
        if not self._external_drop_enabled or self._drop_handler is None:
            super().dropEvent(event)
            return
        paths = self._extract_local_files(event)
        if not paths:
            event.ignore()
            return
        if self._drop_validator is not None and not self._drop_validator(paths):
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        self._drop_handler(paths)

    def setModel(self, model) -> None:  # type: ignore[override]
        previous_model = self._model
        self.modelAboutToChange.emit(previous_model)
        if self._model is not None:
            try:
                self._model.modelReset.disconnect(self._reset_visible_rows_update)
            except (RuntimeError, TypeError):
                pass
            try:
                self._model.rowsInserted.disconnect(self._schedule_visible_rows_update)
            except (RuntimeError, TypeError):
                pass
            try:
                self._model.rowsRemoved.disconnect(self._schedule_visible_rows_update)
            except (RuntimeError, TypeError):
                pass
        super().setModel(model)
        self._model = model
        if model is not None:
            model.modelReset.connect(self._reset_visible_rows_update)
            model.rowsInserted.connect(self._schedule_visible_rows_update)
            model.rowsRemoved.connect(self._schedule_visible_rows_update)
        self._schedule_visible_rows_update()
        self.modelChanged.emit(model)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _cancel_pending_long_press(self) -> None:
        self._press_timer.stop()
        self._reset_state()

    def _reset_state(self) -> None:
        self._long_press_active = False
        self._pressed_index = None
        self._press_pos = None
        self._suppress_next_preview_leave = False

    def _on_long_press_timeout(self) -> None:
        if not self._preview_enabled:
            self._reset_state()
            return
        if self._pressed_index is not None and self._pressed_index.isValid():
            self._long_press_active = True
            self._suppress_next_preview_leave = _IS_DARWIN
            self.requestPreview.emit(self._pressed_index)

    def _should_suppress_preview_leave(self) -> bool:
        if not _IS_DARWIN or not self._suppress_next_preview_leave:
            return False
        buttons = QApplication.mouseButtons()
        return bool(buttons & Qt.MouseButton.LeftButton)

    def _schedule_visible_rows_update(self) -> None:
        if not self._update_timer.isActive():
            self._update_timer.start()

    def _reset_visible_rows_update(self) -> None:
        self._visible_range = None
        self._schedule_visible_rows_update()

    def _schedule_viewport_update(self) -> None:
        if not self._viewport_update_timer.isActive():
            self._viewport_update_timer.start()

    def _flush_viewport_update(self) -> None:
        if self._viewport_layout_pending:
            self._viewport_layout_pending = False
            self.doItemsLayout()
        self.viewport().update()

    def _viewport_pos(self, event: QMouseEvent) -> QPoint:
        """Return the event position mapped into viewport coordinates."""

        viewport = self.viewport()

        def _validated(point: Optional[QPoint]) -> Optional[QPoint]:
            if point is None:
                return None
            if viewport.rect().contains(point):
                return point
            return None

        if hasattr(event, "position"):
            candidate = _validated(event.position().toPoint())
            if candidate is not None:
                return candidate

        if hasattr(event, "pos"):
            candidate = _validated(event.pos())
            if candidate is not None:
                return candidate

        global_point: Optional[QPoint] = None

        global_position = getattr(event, "globalPosition", None)
        if callable(global_position):
            global_point = global_position().toPoint()
        elif global_position is not None:
            global_point = global_position.toPoint()

        if global_point is None and hasattr(event, "globalPos"):
            global_point = event.globalPos()

        if global_point is not None:
            mapped = viewport.mapFromGlobal(global_point)
            candidate = _validated(mapped)
            if candidate is not None:
                return candidate

        # Fallback for any other exotic QMouseEvent implementations. At this point
        # we have no reliable coordinate system information, so best-effort return
        # of the event's integer components is the safest option.
        return QPoint(event.x(), event.y())

    def _emit_visible_rows(self) -> None:
        model = self.model()
        if model is None:
            return
        row_count = model.rowCount()
        if row_count == 0:
            if self._visible_range is not None:
                self._visible_range = None
            return
        viewport_rect = self.viewport().rect()
        if viewport_rect.isEmpty():
            return

        visible_bounds = self._visible_row_bounds(viewport_rect)
        if visible_bounds is None:
            return
        first, last = visible_bounds

        buffer = 20
        first = max(0, first - buffer)
        last = min(row_count - 1, last + buffer)
        if first > last:
            return

        visible_range = (first, last)
        if self._visible_range == visible_range:
            return

        self._visible_range = visible_range
        self.visibleRowsChanged.emit(first, last)

    def _visible_row_bounds(self, viewport_rect) -> Optional[tuple[int, int]]:
        first = self._find_visible_row(viewport_rect, from_bottom=False)
        last = self._find_visible_row(viewport_rect, from_bottom=True)
        if first is None or last is None:
            return None
        if first > last:
            first, last = last, first
        return first, last

    def _find_visible_row(self, viewport_rect, *, from_bottom: bool) -> Optional[int]:
        x_positions = self._sample_viewport_x_positions(viewport_rect)
        top = viewport_rect.top()
        bottom = viewport_rect.bottom()
        step = max(1, min(24, max(1, viewport_rect.height()) // 12 or 1))
        if from_bottom:
            y_values = range(bottom, top - 1, -step)
            row_selector = max
        else:
            y_values = range(top, bottom + 1, step)
            row_selector = min

        for y in y_values:
            rows: list[int] = []
            for x in x_positions:
                index = self.indexAt(QPoint(x, y))
                if index.isValid():
                    rows.append(index.row())
            if rows:
                return row_selector(rows)
        return None

    @staticmethod
    def _sample_viewport_x_positions(viewport_rect) -> list[int]:
        left = viewport_rect.left()
        right = viewport_rect.right()
        center = viewport_rect.center().x()
        width = max(1, viewport_rect.width())
        candidates = [
            left,
            left + width // 4,
            center,
            right - width // 4,
            right,
        ]
        return sorted({max(left, min(right, x)) for x in candidates})

    def _extract_local_files(self, event: QDropEvent | QDragEnterEvent | QDragMoveEvent) -> List[Path]:
        """Return all unique local file paths advertised by *event*.

        The helper normalises the reported URLs, discards remote resources, and
        guarantees deterministic ordering so validators can rely on stable
        inputs.  ``Path.resolve`` is intentionally avoided here to keep the
        method lightweight; callers that require canonical paths can resolve
        them as needed.
        """

        mime = event.mimeData()
        if mime is None:
            return []
        urls = getattr(mime, "urls", None)
        if not callable(urls):
            return []
        seen: set[Path] = set()
        paths: List[Path] = []
        for url in urls():
            if not url.isLocalFile():
                continue
            local = Path(url.toLocalFile()).expanduser()
            if local in seen:
                continue
            seen.add(local)
            paths.append(local)
        return paths

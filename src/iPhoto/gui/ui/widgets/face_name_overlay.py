"""Overlay widgets for face labels and manual face drafting."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QEnterEvent, QIcon, QMouseEvent, QPainter, QPainterPath, QPen, QPixmap, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QCompleter, QLabel, QLineEdit, QListView, QToolTip, QWidget

from iPhoto.people.records import PersonSummary
from iPhoto.people.repository import AssetFaceAnnotation

_FALLBACK_FACE_NAME = "unnamed"
_LABEL_MARGIN_X = 10
_LABEL_MARGIN_Y = 4
_LABEL_GAP = 8
_CIRCLE_PADDING = 10.0
_MIN_CIRCLE_DIAMETER = 36.0
_MANUAL_MIN_DIAMETER = 64.0
_MANUAL_DEFAULT_DIAMETER = 120.0
_MANUAL_HANDLE_DIAMETER = 16.0
_MANUAL_HANDLE_OFFSET = 4.0


@dataclass
class _OverlayFaceState:
    annotation: AssetFaceAnnotation
    chip: "_FaceNameChip"
    face_rect: QRectF = field(default_factory=QRectF)


@dataclass(frozen=True)
class _NameSuggestion:
    person_id: str
    name: str
    thumbnail_path: Path | None


@dataclass
class _ManualFaceDraft:
    center: QPointF
    diameter: float


class _FaceNameChip(QLabel):
    hovered = Signal(str, bool)
    activated = Signal(str)

    def __init__(self, face_id: str, text: str, parent: QWidget | None) -> None:
        super().__init__(text, parent)
        self._face_id = face_id
        self.setCursor(Qt.CursorShape.IBeamCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setContentsMargins(_LABEL_MARGIN_X, _LABEL_MARGIN_Y, _LABEL_MARGIN_X, _LABEL_MARGIN_Y)
        self.setStyleSheet(
            "QLabel { background-color: rgba(255,255,255,230); border: 1px solid rgba(0,0,0,28);"
            " border-radius: 8px; color: rgba(24,24,24,230); font-size: 13px; }"
        )

    def enterEvent(self, event: QEnterEvent) -> None:
        self.hovered.emit(self._face_id, True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        self.hovered.emit(self._face_id, False)
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self._face_id)
            event.accept()
            return
        super().mousePressEvent(event)


class _FaceNameEditor(QLineEdit):
    commitRequested = Signal()
    cancelRequested = Signal()

    def __init__(self, parent: QWidget | None) -> None:
        super().__init__(parent)
        self._closing = False
        self._suppress_cancel_once = False
        self._suggestions: list[_NameSuggestion] = []
        self._model = QStandardItemModel(self)
        self._completer = QCompleter(self._model, self)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchFlag.MatchContains)
        popup = QListView(self)
        popup.setUniformItemSizes(True)
        popup.setStyleSheet(
            "QListView { background-color: rgba(255,255,255,246); border: 1px solid rgba(0,0,0,40);"
            " border-radius: 12px; padding: 6px; outline: none; }"
            "QListView::item { min-height: 40px; padding: 6px 8px; border-radius: 8px; }"
            "QListView::item:selected { background-color: rgba(33,108,255,32); color: rgba(18,18,18,235); }"
        )
        self._completer.setPopup(popup)
        self.setCompleter(self._completer)
        self.setFrame(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setClearButtonEnabled(False)
        self.setStyleSheet(
            "QLineEdit { background-color: rgba(255,255,255,244); border: 1px solid rgba(0,0,0,40);"
            " border-radius: 8px; padding: 4px 10px; color: rgba(16,16,16,235);"
            " selection-background-color: rgba(32,110,255,140); }"
        )

    def set_name_suggestions(self, suggestions: list[_NameSuggestion]) -> None:
        self._suggestions = list(suggestions)
        self._model.clear()
        for suggestion in self._suggestions:
            item = QStandardItem(suggestion.name)
            if suggestion.thumbnail_path is not None and suggestion.thumbnail_path.exists():
                icon = _icon_for_thumbnail(suggestion.thumbnail_path)
                if not icon.isNull():
                    item.setIcon(icon)
            self._model.appendRow(item)

    def suggestion_person_id(self) -> str | None:
        normalized = self.text().strip().casefold()
        matches = [
            suggestion.person_id
            for suggestion in self._suggestions
            if suggestion.name.strip().casefold() == normalized
        ]
        return matches[0] if len(matches) == 1 else None

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._closing = True
            self.commitRequested.emit()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            self._closing = True
            self.cancelRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event) -> None:  # type: ignore[override]
        super().focusOutEvent(event)
        popup = self._completer.popup()
        if popup is not None and popup.isVisible():
            self._closing = False
            return
        if self._suppress_cancel_once:
            self._suppress_cancel_once = False
            self._closing = False
            return
        if self._closing:
            return
        self._closing = True
        self.cancelRequested.emit()

    def reset_closing_state(self) -> None:
        self._closing = False

    def suppress_cancel_once(self) -> None:
        self._suppress_cancel_once = True


class FaceNameOverlayWidget(QWidget):
    renameSubmitted = Signal(str, object)
    manualFaceSubmitted = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("faceNameOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self._viewer: QWidget | None = None
        self._annotations: list[AssetFaceAnnotation] = []
        self._states: dict[str, _OverlayFaceState] = {}
        self._active = False
        self._hovered_face_id: str | None = None
        self._editing_face_id: str | None = None
        self._editor: _FaceNameEditor | None = None
        self._name_suggestions: list[_NameSuggestion] = []
        self._manual_draft: _ManualFaceDraft | None = None
        self._manual_editor: _FaceNameEditor | None = None
        self._manual_busy = False
        self._drag_mode: str | None = None
        self._drag_origin_point = QPointF()
        self._drag_origin_center = QPointF()

    def set_viewer(self, viewer: object | None) -> None:
        previous = self._viewer
        if previous is viewer:
            return
        if isinstance(previous, QWidget):
            previous.removeEventFilter(self)
            signal = getattr(previous, "viewTransformChanged", None)
            if signal is not None:
                try:
                    signal.disconnect(self._relayout)
                except (RuntimeError, TypeError):
                    pass
        self._viewer = viewer if isinstance(viewer, QWidget) else None
        if self._viewer is not None:
            self._viewer.installEventFilter(self)
            signal = getattr(self._viewer, "viewTransformChanged", None)
            if signal is not None:
                signal.connect(self._relayout)
        self._relayout()

    def set_overlay_active(self, active: bool) -> None:
        self._active = bool(active)
        if not self._viewer_has_image_content() and self._manual_draft is None:
            self.setHidden(True)
            for state in self._states.values():
                state.chip.hide()
            return
        if not self._active and self._manual_draft is None:
            self._hovered_face_id = None
            self._cancel_editing()
        self._sync_child_visibility()
        self.update()

    def set_annotations(self, annotations: list[AssetFaceAnnotation]) -> None:
        self._hovered_face_id = None
        self._cancel_editing()
        self.clear_manual_face_draft()
        self._clear_chips()
        self._annotations = list(annotations)
        parent = self.parentWidget() or self
        for annotation in self._annotations:
            chip = _FaceNameChip(annotation.face_id, self._display_name(annotation), parent)
            chip.hide()
            chip.hovered.connect(self._handle_chip_hovered)
            chip.activated.connect(self._start_editing)
            self._states[annotation.face_id] = _OverlayFaceState(annotation=annotation, chip=chip)
        self._relayout()
        if not self._viewer_has_image_content() and self._manual_draft is None:
            self.setHidden(True)

    def clear_annotations(self) -> None:
        self._hovered_face_id = None
        self._cancel_editing()
        self.clear_manual_face_draft()
        self._clear_chips()
        self._annotations = []
        self._sync_child_visibility()
        self.update()

    def set_name_suggestions(self, suggestions: list[PersonSummary]) -> None:
        self._name_suggestions = [
            _NameSuggestion(summary.person_id, summary.name.strip(), summary.thumbnail_path)
            for summary in suggestions
            if isinstance(summary.name, str) and summary.name.strip()
        ]
        if self._editor is not None:
            self._editor.set_name_suggestions(self._name_suggestions)
        if self._manual_editor is not None:
            self._manual_editor.set_name_suggestions(self._name_suggestions)

    def start_manual_face(self) -> None:
        viewer_rect = self._viewer_rect()
        if viewer_rect.isEmpty():
            return
        diameter = min(
            max(_MANUAL_DEFAULT_DIAMETER, _MANUAL_MIN_DIAMETER),
            max(_MANUAL_MIN_DIAMETER, min(viewer_rect.width(), viewer_rect.height()) * 0.28),
        )
        self._manual_draft = _ManualFaceDraft(QPointF(viewer_rect.center()), float(diameter))
        self._manual_busy = False
        self._active = True
        self._ensure_manual_editor()
        if self._manual_editor is not None:
            self._manual_editor.clear()
            self._manual_editor.setPlaceholderText("Click to Name")
            self._manual_editor.reset_closing_state()
            self._manual_editor.show()
        self._relayout()
        self.update()

    def clear_manual_face_draft(self) -> None:
        self._manual_draft = None
        self._manual_busy = False
        self._drag_mode = None
        if self._manual_editor is not None:
            self._manual_editor.deleteLater()
            self._manual_editor = None
        self._sync_child_visibility()
        self.update()

    def set_manual_face_busy(self, busy: bool) -> None:
        self._manual_busy = bool(busy)
        if self._manual_editor is not None:
            self._manual_editor.setEnabled(not self._manual_busy)
        self.update()

    def show_manual_error(self, message: str) -> None:
        if not message:
            return
        target = self._manual_editor.geometry().center() if self._manual_editor is not None else self.rect().center()
        QToolTip.showText(self.mapToGlobal(target), message, self)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._relayout()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        if not self.isVisible():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self._active and self._hovered_face_id:
            state = self._states.get(self._hovered_face_id)
            if state is not None and not state.face_rect.isEmpty():
                self._paint_circle(painter, self._circle_rect_for_face(state.face_rect), 0.72)
        if self._manual_draft is not None:
            self._paint_circle(painter, self._manual_circle_rect(), 0.9)
            self._paint_button(painter, self._manual_cancel_rect(), "x")
            self._paint_button(painter, self._manual_handle_rect(), "")

    def eventFilter(self, watched: object, event: QEvent) -> bool:
        viewer = getattr(self, "_viewer", None)
        if watched is not viewer:
            return super().eventFilter(watched, event)
        if event.type() == QEvent.Type.MouseMove:
            return self._handle_viewer_mouse_move(event)
        if event.type() == QEvent.Type.MouseButtonPress:
            return self._handle_viewer_mouse_press(event)
        if event.type() == QEvent.Type.MouseButtonRelease:
            return self._handle_viewer_mouse_release(event)
        if event.type() == QEvent.Type.Leave:
            self._drag_mode = None
            self._hovered_face_id = None
            self.update()
        return super().eventFilter(watched, event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._handle_manual_mouse_press(QPointF(event.position()), event):
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._handle_manual_mouse_move(QPointF(event.position()), event):
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._handle_manual_mouse_release(event):
            return
        super().mouseReleaseEvent(event)

    def _handle_viewer_mouse_move(self, event: QEvent) -> bool:
        if self._viewer is None or not isinstance(event, QMouseEvent):
            return False
        point = QPointF(self._viewer.mapTo(self, event.position().toPoint()))
        return self._handle_manual_mouse_move(point, event)

    def _handle_manual_mouse_move(self, point: QPointF, event: QMouseEvent) -> bool:
        if self._manual_draft is not None and self._drag_mode == "move" and not self._manual_busy:
            delta = point - self._drag_origin_point
            self._manual_draft.center = self._clamp_manual_center(
                self._drag_origin_center + delta,
                self._manual_draft.diameter,
            )
            self._relayout()
            self.update()
            event.accept()
            return True
        if self._manual_draft is not None and self._drag_mode == "resize" and not self._manual_busy:
            distance = _distance(self._manual_draft.center, point)
            self._manual_draft.diameter = min(
                max(_MANUAL_MIN_DIAMETER, distance * 2.0),
                self._max_manual_diameter_for_center(self._manual_draft.center),
            )
            self._relayout()
            self.update()
            event.accept()
            return True
        self._hovered_face_id = self._hit_face_id(point)
        self.update()
        return False

    def _handle_viewer_mouse_press(self, event: QEvent) -> bool:
        if not isinstance(event, QMouseEvent) or event.button() != Qt.MouseButton.LeftButton:
            return False
        if self._viewer is None or self._manual_draft is None or self._manual_busy:
            return False
        point = QPointF(self._viewer.mapTo(self, event.position().toPoint()))
        return self._handle_manual_mouse_press(point, event)

    def _handle_manual_mouse_press(self, point: QPointF, event: QMouseEvent) -> bool:
        if self._manual_cancel_rect().contains(point):
            self.clear_manual_face_draft()
            event.accept()
            return True
        if self._manual_handle_rect().contains(point):
            if self._manual_editor is not None:
                self._manual_editor.reset_closing_state()
                self._manual_editor.suppress_cancel_once()
            self._drag_mode = "resize"
            event.accept()
            return True
        if self._manual_circle_rect().contains(point):
            if self._manual_editor is not None:
                self._manual_editor.reset_closing_state()
                self._manual_editor.suppress_cancel_once()
            self._drag_mode = "move"
            self._drag_origin_point = point
            self._drag_origin_center = QPointF(self._manual_draft.center)
            event.accept()
            return True
        return False

    def _handle_viewer_mouse_release(self, event: QEvent) -> bool:
        if not isinstance(event, QMouseEvent):
            return False
        return self._handle_manual_mouse_release(event)

    def _handle_manual_mouse_release(self, event: QMouseEvent) -> bool:
        if self._drag_mode is None:
            return False
        self._drag_mode = None
        event.accept()
        return True

    def _paint_circle(self, painter: QPainter, rect: QRectF, opacity: float) -> None:
        path = QPainterPath()
        path.addEllipse(rect)
        glow_pen = QPen()
        glow_pen.setColor(Qt.GlobalColor.white)
        glow_pen.setWidthF(4.0)
        glow_pen.setCosmetic(True)
        painter.setPen(glow_pen)
        painter.setOpacity(0.22)
        painter.drawPath(path)
        stroke_pen = QPen()
        stroke_pen.setColor(Qt.GlobalColor.white)
        stroke_pen.setWidthF(2.0)
        stroke_pen.setCosmetic(True)
        painter.setPen(stroke_pen)
        painter.setOpacity(opacity)
        painter.drawPath(path)
        painter.setOpacity(1.0)

    def _paint_button(self, painter: QPainter, rect: QRectF, text: str) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 236))
        painter.drawEllipse(rect)
        if text:
            painter.setPen(QColor(32, 32, 32, 220))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    def _clear_chips(self) -> None:
        for state in self._states.values():
            state.chip.deleteLater()
        self._states.clear()

    def _display_name(self, annotation: AssetFaceAnnotation) -> str:
        name = annotation.display_name
        return name.strip() if isinstance(name, str) and name.strip() else _FALLBACK_FACE_NAME

    def _sync_child_visibility(self) -> None:
        viewer_ready = self._viewer_has_image_content()
        if not viewer_ready and self._manual_draft is None:
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self.setHidden(True)
            for state in self._states.values():
                state.chip.hide()
            if self._editor is not None:
                self._editor.hide()
            return
        show_saved = self._active and viewer_ready and bool(self._states)
        show_manual = self._manual_draft is not None and viewer_ready
        self.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            not show_manual,
        )
        self.setHidden(not (show_saved or show_manual))
        if self.isVisible():
            self.raise_()
        for state in self._states.values():
            state.chip.setVisible(
                show_saved
                and not state.face_rect.isEmpty()
                and state.annotation.face_id != self._editing_face_id
            )
        if self._editor is not None:
            self._editor.setVisible(show_saved and self._editing_face_id is not None)
        if self._manual_editor is not None:
            self._manual_editor.setVisible(show_manual)
            self._manual_editor.setEnabled(not self._manual_busy)

    def _viewer_has_image_content(self) -> bool:
        viewer = self._viewer
        if viewer is None:
            return False
        has_image_content = getattr(viewer, "has_image_content", None)
        if callable(has_image_content):
            try:
                return bool(has_image_content())
            except (AttributeError, RuntimeError, TypeError):
                return False
        pixmap = getattr(viewer, "pixmap", None)
        if callable(pixmap):
            try:
                current = pixmap()
            except (AttributeError, RuntimeError, TypeError):
                return False
            return current is not None and not current.isNull()
        return True

    def _viewer_rect(self) -> QRect:
        viewer = self._viewer
        if viewer is None:
            return QRect()
        surface = self.parentWidget() or self
        return QRect(viewer.mapTo(surface, QPoint(0, 0)), viewer.size())

    def _relayout(self) -> None:
        self._sync_child_visibility()
        viewer_rect = self._viewer_rect()
        if viewer_rect.isEmpty():
            return
        for face_id, state in self._states.items():
            rect = self._map_annotation_rect(state.annotation)
            state.face_rect = rect
            if rect.isEmpty():
                state.chip.hide()
                continue
            state.chip.setGeometry(
                self._chip_rect_for_face(
                    rect,
                    state.chip.sizeHint().width(),
                    state.chip.sizeHint().height(),
                    viewer_rect,
                )
            )
            if face_id != self._editing_face_id and self.isVisible():
                state.chip.show()
            state.chip.raise_()
        if self._editor is not None and self._editing_face_id is not None:
            state = self._states.get(self._editing_face_id)
            if state is not None and not state.face_rect.isEmpty():
                self._editor.setGeometry(
                    self._chip_rect_for_face(
                        state.face_rect,
                        max(state.chip.sizeHint().width() + 12, 120),
                        state.chip.sizeHint().height(),
                        viewer_rect,
                    )
                )
                self._editor.raise_()
        if self._manual_draft is not None:
            self._manual_draft.center = self._clamp_manual_center(
                self._manual_draft.center,
                self._manual_draft.diameter,
            )
            self._manual_draft.diameter = min(
                self._manual_draft.diameter,
                self._max_manual_diameter_for_center(self._manual_draft.center),
            )
            self._ensure_manual_editor()
            if self._manual_editor is not None:
                self._manual_editor.setGeometry(self._manual_editor_rect())
                self._manual_editor.raise_()
        self.update()

    def _map_annotation_rect(self, annotation: AssetFaceAnnotation) -> QRectF:
        viewer = self._viewer
        if viewer is None or not hasattr(viewer, "image_rect_to_viewport"):
            return QRectF()
        rect = viewer.image_rect_to_viewport(
            annotation.box_x,
            annotation.box_y,
            annotation.box_w,
            annotation.box_h,
            image_width=annotation.image_width,
            image_height=annotation.image_height,
        )
        return rect.translated(self._viewer_rect().topLeft()) if isinstance(rect, QRectF) else QRectF()

    def _chip_rect_for_face(self, face_rect: QRectF, width: int, height: int, bounds: QRect) -> QRect:
        x = int(round(face_rect.center().x() - (width / 2.0)))
        y = int(round(face_rect.bottom() + _LABEL_GAP))
        if y + height > bounds.bottom():
            y = int(round(face_rect.top() - height - _LABEL_GAP))
        return QRect(
            max(bounds.left(), min(x, bounds.right() - width)),
            max(bounds.top(), min(y, bounds.bottom() - height)),
            width,
            height,
        )

    def _circle_rect_for_face(self, face_rect: QRectF) -> QRectF:
        diameter = max(face_rect.width(), face_rect.height(), _MIN_CIRCLE_DIAMETER) + _CIRCLE_PADDING
        return QRectF(face_rect.center().x() - diameter / 2.0, face_rect.center().y() - diameter / 2.0, diameter, diameter)

    def _manual_circle_rect(self) -> QRectF:
        if self._manual_draft is None:
            return QRectF()
        diameter = max(_MANUAL_MIN_DIAMETER, self._manual_draft.diameter)
        return QRectF(self._manual_draft.center.x() - diameter / 2.0, self._manual_draft.center.y() - diameter / 2.0, diameter, diameter)

    def _manual_handle_rect(self) -> QRectF:
        circle = self._manual_circle_rect()
        radius = _MANUAL_HANDLE_DIAMETER / 2.0
        center = QPointF(circle.right() + _MANUAL_HANDLE_OFFSET, circle.center().y())
        return QRectF(center.x() - radius, center.y() - radius, radius * 2.0, radius * 2.0)

    def _manual_cancel_rect(self) -> QRectF:
        circle = self._manual_circle_rect()
        radius = _MANUAL_HANDLE_DIAMETER * 0.8
        center = QPointF(circle.left() - _MANUAL_HANDLE_OFFSET, circle.top() + radius)
        return QRectF(center.x() - radius, center.y() - radius, radius * 2.0, radius * 2.0)

    def _manual_editor_rect(self) -> QRect:
        viewer_rect = self._viewer_rect()
        width = max(120, self._manual_editor.sizeHint().width() if self._manual_editor is not None else 120)
        height = self._manual_editor.sizeHint().height() if self._manual_editor is not None else 32
        return self._chip_rect_for_face(self._manual_circle_rect(), width, height, viewer_rect)

    def _max_manual_diameter_for_center(self, center: QPointF) -> float:
        viewer_rect = self._viewer_rect()
        return max(
            _MANUAL_MIN_DIAMETER,
            min(
                (center.x() - viewer_rect.left()) * 2.0,
                (viewer_rect.right() - center.x()) * 2.0,
                (center.y() - viewer_rect.top()) * 2.0,
                (viewer_rect.bottom() - center.y()) * 2.0,
            ),
        ) if not viewer_rect.isEmpty() else _MANUAL_MIN_DIAMETER

    def _clamp_manual_center(self, center: QPointF, diameter: float) -> QPointF:
        viewer_rect = self._viewer_rect()
        if viewer_rect.isEmpty():
            return center
        radius = max(_MANUAL_MIN_DIAMETER, diameter) / 2.0
        return QPointF(
            min(max(center.x(), viewer_rect.left() + radius), viewer_rect.right() - radius),
            min(max(center.y(), viewer_rect.top() + radius), viewer_rect.bottom() - radius),
        )

    def _hit_face_id(self, point: QPointF) -> str | None:
        hits = [
            (_distance(self._circle_rect_for_face(state.face_rect).center(), point), face_id)
            for face_id, state in self._states.items()
            if not state.face_rect.isEmpty()
            and self._circle_rect_for_face(state.face_rect).adjusted(-10.0, -10.0, 10.0, 10.0).contains(point)
        ]
        hits.sort(key=lambda item: item[0])
        return hits[0][1] if hits else None

    def _handle_chip_hovered(self, face_id: str, hovered: bool) -> None:
        self._hovered_face_id = face_id if hovered else None
        self.update()

    def _start_editing(self, face_id: str) -> None:
        state = self._states.get(face_id)
        if state is None or not state.annotation.person_id:
            return
        self._cancel_editing()
        self._editing_face_id = face_id
        editor = _FaceNameEditor(self.parentWidget() or self)
        editor.set_name_suggestions(self._name_suggestions)
        editor.setText(state.annotation.display_name or "")
        editor.commitRequested.connect(self._commit_editing)
        editor.cancelRequested.connect(self._cancel_editing)
        self._editor = editor
        state.chip.hide()
        self._relayout()
        editor.show()
        editor.setFocus(Qt.FocusReason.MouseFocusReason)
        editor.selectAll()

    def _commit_editing(self) -> None:
        if self._editing_face_id is None or self._editor is None:
            return
        state = self._states.get(self._editing_face_id)
        if state is None or not state.annotation.person_id:
            self._cancel_editing()
            return
        new_name = self._editor.text().strip() or None
        state.annotation = replace(state.annotation, display_name=new_name)
        state.chip.setText(self._display_name(state.annotation))
        person_id = state.annotation.person_id
        self._teardown_editor(show_chip=True)
        if person_id:
            self.renameSubmitted.emit(person_id, new_name)

    def _cancel_editing(self) -> None:
        if self._editing_face_id is None and self._editor is None:
            return
        self._teardown_editor(show_chip=True)

    def _teardown_editor(self, *, show_chip: bool) -> None:
        face_id = self._editing_face_id
        editor = self._editor
        self._editing_face_id = None
        self._editor = None
        if editor is not None:
            editor.deleteLater()
        if face_id is not None:
            state = self._states.get(face_id)
            if state is not None and show_chip and self.isVisible():
                state.chip.show()
                state.chip.raise_()
        self._relayout()

    def _ensure_manual_editor(self) -> None:
        if self._manual_draft is None:
            return
        if self._manual_editor is None:
            editor = _FaceNameEditor(self.parentWidget() or self)
            editor.set_name_suggestions(self._name_suggestions)
            editor.setPlaceholderText("Click to Name")
            editor.commitRequested.connect(self._submit_manual_face)
            editor.cancelRequested.connect(self.clear_manual_face_draft)
            self._manual_editor = editor

    def _submit_manual_face(self) -> None:
        if self._manual_draft is None or self._manual_editor is None or self._manual_busy:
            return
        trimmed = self._manual_editor.text().strip()
        if not trimmed:
            self.show_manual_error("Please enter a name before saving the face.")
            self._manual_editor.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        requested_box = self._manual_requested_box()
        if requested_box is None:
            self.show_manual_error("Please place the circle on the face before saving.")
            return
        self._manual_busy = True
        self._manual_editor.setEnabled(False)
        self.manualFaceSubmitted.emit(
            {
                "name": trimmed,
                "person_id": self._manual_editor.suggestion_person_id(),
                "requested_box": requested_box,
            }
        )

    def _manual_requested_box(self) -> tuple[int, int, int, int] | None:
        if self._manual_draft is None or self._viewer is None:
            return None
        circle = self._manual_circle_rect()
        viewer_rect = self._viewer_rect()
        viewport_to_image = getattr(self._viewer, "viewport_to_image", None)
        if callable(viewport_to_image):
            top_left = viewport_to_image(QPointF(circle.left() - viewer_rect.left(), circle.top() - viewer_rect.top()))
            bottom_right = viewport_to_image(QPointF(circle.right() - viewer_rect.left(), circle.bottom() - viewer_rect.top()))
        else:
            top_left = QPointF(circle.left() - viewer_rect.left(), circle.top() - viewer_rect.top())
            bottom_right = QPointF(circle.right() - viewer_rect.left(), circle.bottom() - viewer_rect.top())
        left = int(round(min(top_left.x(), bottom_right.x())))
        top = int(round(min(top_left.y(), bottom_right.y())))
        right = int(round(max(top_left.x(), bottom_right.x())))
        bottom = int(round(max(top_left.y(), bottom_right.y())))
        return (left, top, max(1, right - left), max(1, bottom - top))


def _distance(left: QPointF, right: QPointF) -> float:
    dx = float(left.x() - right.x())
    dy = float(left.y() - right.y())
    return (dx * dx + dy * dy) ** 0.5


def _icon_for_thumbnail(path: Path) -> QIcon:
    pixmap = QPixmap(str(path))
    if pixmap.isNull():
        return QIcon()
    size = 34
    scaled = pixmap.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
    rounded = QPixmap(size, size)
    rounded.fill(Qt.GlobalColor.transparent)
    painter = QPainter(rounded)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    clip = QPainterPath()
    clip.addEllipse(QRectF(0.0, 0.0, float(size), float(size)))
    painter.setClipPath(clip)
    painter.drawPixmap(0, 0, scaled)
    painter.end()
    return QIcon(rounded)


__all__ = ["FaceNameOverlayWidget"]

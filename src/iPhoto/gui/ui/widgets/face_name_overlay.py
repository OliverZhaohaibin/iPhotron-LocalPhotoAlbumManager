"""Overlay widgets for face labels and inline renaming in detail playback."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QEnterEvent, QMouseEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QLabel, QLineEdit, QWidget

from iPhoto.people.repository import AssetFaceAnnotation

_FALLBACK_FACE_NAME = "unnamed"
_LABEL_MARGIN_X = 10
_LABEL_MARGIN_Y = 4
_LABEL_GAP = 8
_CIRCLE_PADDING = 10.0
_MIN_CIRCLE_DIAMETER = 36.0


@dataclass
class _OverlayFaceState:
    annotation: AssetFaceAnnotation
    chip: "_FaceNameChip"
    face_rect: QRectF = field(default_factory=QRectF)


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
            "QLabel {"
            "  background-color: rgba(255, 255, 255, 230);"
            "  border: 1px solid rgba(0, 0, 0, 28);"
            "  border-radius: 8px;"
            "  color: rgba(24, 24, 24, 230);"
            "  font-size: 13px;"
            "}"
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
        self.setFrame(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setClearButtonEnabled(False)
        self.setStyleSheet(
            "QLineEdit {"
            "  background-color: rgba(255, 255, 255, 244);"
            "  border: 1px solid rgba(0, 0, 0, 40);"
            "  border-radius: 8px;"
            "  padding: 4px 10px;"
            "  color: rgba(16, 16, 16, 235);"
            "  selection-background-color: rgba(32, 110, 255, 140);"
            "}"
        )

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
        if self._closing:
            return
        self._closing = True
        self.cancelRequested.emit()


class FaceNameOverlayWidget(QWidget):
    """Paint hover circles while managing face-label chips over the viewer."""

    renameSubmitted = Signal(str, object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("faceNameOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(False)

        self._viewer: object | None = None
        self._annotations: list[AssetFaceAnnotation] = []
        self._states: dict[str, _OverlayFaceState] = {}
        self._active = False
        self._hovered_face_id: str | None = None
        self._editing_face_id: str | None = None
        self._editor: _FaceNameEditor | None = None

    def set_viewer(self, viewer: object | None) -> None:
        previous = self._viewer
        if previous is viewer:
            return
        signal = getattr(previous, "viewTransformChanged", None)
        if signal is not None:
            try:
                signal.disconnect(self._relayout)
            except (RuntimeError, TypeError):
                pass
        self._viewer = viewer
        signal = getattr(viewer, "viewTransformChanged", None)
        if signal is not None:
            signal.connect(self._relayout)
        self._relayout()

    def set_overlay_active(self, active: bool) -> None:
        if self._active == bool(active):
            self._sync_child_visibility()
            return
        self._active = bool(active)
        if not self._active:
            self._hovered_face_id = None
            self._cancel_editing()
        self._sync_child_visibility()
        self.update()

    def set_annotations(self, annotations: list[AssetFaceAnnotation]) -> None:
        self._cancel_editing()
        self._clear_chips()
        self._annotations = list(annotations)
        parent = self.parentWidget() or self
        for annotation in self._annotations:
            chip = _FaceNameChip(annotation.face_id, self._display_name(annotation), parent)
            chip.hovered.connect(self._handle_chip_hovered)
            chip.activated.connect(self._start_editing)
            self._states[annotation.face_id] = _OverlayFaceState(annotation=annotation, chip=chip)
        self._sync_child_visibility()
        self._relayout()

    def clear_annotations(self) -> None:
        self._hovered_face_id = None
        self._cancel_editing()
        self._clear_chips()
        self._annotations = []
        self._sync_child_visibility()
        self.update()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._relayout()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        if not self.isVisible() or not self._active or not self._hovered_face_id:
            return
        state = self._states.get(self._hovered_face_id)
        if state is None or state.face_rect.isEmpty():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        circle_rect = self._circle_rect_for_face(state.face_rect)
        path = QPainterPath()
        path.addEllipse(circle_rect)
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
        painter.setOpacity(0.72)
        painter.drawPath(path)

    def _clear_chips(self) -> None:
        for state in self._states.values():
            state.chip.deleteLater()
        self._states.clear()

    def _display_name(self, annotation: AssetFaceAnnotation) -> str:
        name = annotation.display_name
        if not isinstance(name, str) or not name.strip():
            return _FALLBACK_FACE_NAME
        return name.strip()

    def _sync_child_visibility(self) -> None:
        show_overlay = self._active and bool(self._states) and self._viewer_is_visible()
        self.setVisible(show_overlay)
        for state in self._states.values():
            state.chip.setVisible(show_overlay and state.annotation.face_id != self._editing_face_id)
        if self._editor is not None:
            self._editor.setVisible(show_overlay and self._editing_face_id is not None)
        if not show_overlay:
            self._hovered_face_id = None

    def _viewer_is_visible(self) -> bool:
        viewer = self._viewer
        return bool(viewer and hasattr(viewer, "isVisible") and viewer.isVisible())

    def _viewer_rect(self) -> QRect:
        viewer = self._viewer
        if viewer is None or not isinstance(viewer, QWidget):
            return QRect()
        surface = self.parentWidget() or self
        top_left = viewer.mapTo(surface, QPoint(0, 0))
        return QRect(top_left, viewer.size())

    def _relayout(self) -> None:
        if not self._states:
            return
        self._sync_child_visibility()
        viewer = self._viewer
        if viewer is None or not isinstance(viewer, QWidget):
            return
        viewer_rect = self._viewer_rect()
        if not viewer_rect.isValid() or viewer_rect.isEmpty():
            return
        for face_id, state in self._states.items():
            rect = self._map_annotation_rect(state.annotation)
            state.face_rect = rect
            chip = state.chip
            if rect.isEmpty():
                chip.hide()
                continue
            chip_rect = self._chip_rect_for_face(rect, chip.sizeHint().width(), chip.sizeHint().height(), viewer_rect)
            chip.setGeometry(chip_rect)
            if face_id != self._editing_face_id and self.isVisible():
                chip.show()
            chip.raise_()
        if self._editor is not None and self._editing_face_id is not None:
            state = self._states.get(self._editing_face_id)
            if state is not None and not state.face_rect.isEmpty():
                editor_rect = self._chip_rect_for_face(
                    state.face_rect,
                    max(state.chip.sizeHint().width() + 12, 120),
                    state.chip.sizeHint().height(),
                    viewer_rect,
                )
                self._editor.setGeometry(editor_rect)
                self._editor.raise_()
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
        if not isinstance(rect, QRectF) or rect.isEmpty():
            return QRectF()
        viewer_rect = self._viewer_rect()
        return rect.translated(viewer_rect.topLeft())

    def _chip_rect_for_face(
        self,
        face_rect: QRectF,
        width: int,
        height: int,
        bounds: QRect,
    ) -> QRect:
        x = int(round(face_rect.center().x() - (width / 2.0)))
        preferred_bottom = int(round(face_rect.bottom() + _LABEL_GAP))
        y = preferred_bottom
        if y + height > bounds.bottom():
            y = int(round(face_rect.top() - height - _LABEL_GAP))
        max_x = bounds.right() - width
        max_y = bounds.bottom() - height
        x = max(bounds.left(), min(x, max_x))
        y = max(bounds.top(), min(y, max_y))
        return QRect(x, y, width, height)

    def _circle_rect_for_face(self, face_rect: QRectF) -> QRectF:
        diameter = max(
            face_rect.width(),
            face_rect.height(),
            _MIN_CIRCLE_DIAMETER,
        ) + _CIRCLE_PADDING
        center = face_rect.center()
        return QRectF(
            center.x() - (diameter / 2.0),
            center.y() - (diameter / 2.0),
            diameter,
            diameter,
        )

    def _handle_chip_hovered(self, face_id: str, hovered: bool) -> None:
        next_face_id = face_id if hovered else None
        if self._hovered_face_id == next_face_id:
            return
        self._hovered_face_id = next_face_id
        self.update()

    def _start_editing(self, face_id: str) -> None:
        state = self._states.get(face_id)
        if state is None or not state.annotation.person_id:
            return
        self._cancel_editing()
        self._hovered_face_id = face_id
        self._editing_face_id = face_id
        editor_parent = self.parentWidget() or self
        editor = _FaceNameEditor(editor_parent)
        editor.setText(state.annotation.display_name or "")
        editor.commitRequested.connect(self._commit_editing)
        editor.cancelRequested.connect(self._cancel_editing)
        self._editor = editor
        state.chip.hide()
        self._relayout()
        editor.show()
        editor.setFocus(Qt.FocusReason.MouseFocusReason)
        editor.selectAll()
        self.update()

    def _commit_editing(self) -> None:
        face_id = self._editing_face_id
        editor = self._editor
        if face_id is None or editor is None:
            return
        state = self._states.get(face_id)
        if state is None or not state.annotation.person_id:
            self._cancel_editing()
            return
        trimmed = editor.text().strip()
        new_name = trimmed or None
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


__all__ = ["FaceNameOverlayWidget"]

"""Overlay widgets for face labels and manual face drafting."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFontMetrics,
    QIcon,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QStandardItem,
    QStandardItemModel,
)
from PySide6.QtWidgets import QApplication, QCompleter, QLineEdit, QListView, QToolTip, QWidget

from iPhoto.gui.i18n import tr
from iPhoto.people.records import PersonSummary
from iPhoto.people.repository import AssetFaceAnnotation
from iPhoto.utils.image_loader import load_qpixmap

from .recognition_annotations import RecognitionIdentitySuggestion

_LABEL_MARGIN_X = 10
_LABEL_MARGIN_Y = 4
_LABEL_GAP = 8
_CHIP_HOVER_MARGIN = 6.0
_CIRCLE_PADDING = 10.0
_MIN_CIRCLE_DIAMETER = 36.0
_MANUAL_MIN_DIAMETER = 64.0
_MANUAL_DEFAULT_DIAMETER = 120.0
_MANUAL_HANDLE_DIAMETER = 16.0
_MANUAL_HANDLE_OFFSET = 4.0


@dataclass
class _SavedFaceLayout:
    face_rect: QRectF = field(default_factory=QRectF)
    chip_rect: QRectF = field(default_factory=QRectF)
    hover_rect: QRectF = field(default_factory=QRectF)
    circle_rect: QRectF = field(default_factory=QRectF)
    label_text: str = ""


@dataclass(frozen=True)
class _NameSuggestion:
    identity_key: str
    name: str
    thumbnail_path: Path | None

    @classmethod
    def from_identity(cls, suggestion: RecognitionIdentitySuggestion) -> "_NameSuggestion":
        return cls(
            identity_key=suggestion.identity_key,
            name=suggestion.name,
            thumbnail_path=suggestion.thumbnail_path,
        )


@dataclass
class _ManualFaceDraft:
    center: QPointF
    diameter: float


@dataclass
class _OverlayFaceState:
    annotation: AssetFaceAnnotation
    layout: _SavedFaceLayout = field(default_factory=_SavedFaceLayout)


class _FaceNameEditor(QLineEdit):
    commitRequested = Signal()
    cancelRequested = Signal()

    def __init__(self, parent: QWidget | None) -> None:
        super().__init__(parent)
        self._closing = False
        self._suppress_cancel_once = False
        self._suggestions: list[_NameSuggestion] = []
        self._selected_identity_key: str | None = None
        self._selected_identity_name: str | None = None
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
        self._completer.activated.connect(self._handle_completion_activated)
        self.textEdited.connect(self._clear_selected_identity)
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
        self._clear_selected_identity()
        self._model.clear()
        for suggestion in self._suggestions:
            item = QStandardItem(suggestion.name)
            item.setData(suggestion.identity_key, Qt.ItemDataRole.UserRole)
            if suggestion.thumbnail_path is not None and suggestion.thumbnail_path.exists():
                icon = _icon_for_thumbnail(suggestion.thumbnail_path)
                if not icon.isNull():
                    item.setIcon(icon)
            self._model.appendRow(item)

    def suggestion_person_id(self) -> str | None:
        identity_key = self.suggestion_identity_key()
        if identity_key is None:
            return None
        if identity_key.startswith("person:"):
            return identity_key.removeprefix("person:")
        if identity_key.startswith("pet:"):
            return None
        return identity_key

    def suggestion_identity_key(self) -> str | None:
        text = self.text().strip()
        if (
            self._selected_identity_key
            and self._selected_identity_name is not None
            and self._selected_identity_name.strip().casefold() == text.casefold()
        ):
            return self._selected_identity_key
        normalized = self.text().strip().casefold()
        matches = [
            suggestion.identity_key
            for suggestion in self._suggestions
            if suggestion.name.strip().casefold() == normalized
        ]
        return matches[0] if len(matches) == 1 else None

    def _handle_completion_activated(self, _completion: object) -> None:
        index = self._completer.currentIndex()
        if not index.isValid():
            return
        identity_key = index.data(Qt.ItemDataRole.UserRole)
        if not isinstance(identity_key, str) or not identity_key:
            return
        name = str(index.data() or "").strip()
        self._selected_identity_key = identity_key
        self._selected_identity_name = name

    def _clear_selected_identity(self) -> None:
        self._selected_identity_key = None
        self._selected_identity_name = None

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
    unassignedRenameSubmitted = Signal(object, str)
    manualFaceSubmitted = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("faceNameOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self._viewer: QWidget | None = None
        self._event_surface: QWidget | None = None
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
        self._saved_hover_sync_queued = False
        self._saved_hover_sync_generation = 0
        self._queued_saved_hover_reason = ""
        self._queued_saved_hover_generation = 0
        self._saved_hover_app_filter_installed = False
        self._saved_hover_sync_timer = QTimer(self)
        self._saved_hover_sync_timer.setSingleShot(True)
        self._saved_hover_sync_timer.setInterval(0)
        self._saved_hover_sync_timer.timeout.connect(
            self._flush_queued_saved_hover_sync
        )
        self._saved_hover_poll_timer = QTimer(self)
        self._saved_hover_poll_timer.setInterval(40)
        self._saved_hover_poll_timer.timeout.connect(self._poll_saved_hover_from_cursor)
        self._saved_press_face_id: str | None = None
        self._cursor_override_active = False
        self._cursor_guard_widgets: dict[QWidget, QCursor | None] = {}

    def set_viewer(self, viewer: object | None) -> None:
        previous = self._viewer
        if previous is viewer:
            return
        self._teardown_saved_hover_tracking()
        if isinstance(previous, QWidget):
            previous.removeEventFilter(self)
            self._disconnect_viewer_signal(previous, "viewTransformChanged")
            self._disconnect_viewer_signal(previous, "firstFrameReady")
        self._viewer = viewer if isinstance(viewer, QWidget) else None
        if self._viewer is not None:
            self._viewer.setMouseTracking(True)
            self._viewer.installEventFilter(self)
            self._connect_viewer_signal(self._viewer, "viewTransformChanged")
            self._connect_viewer_signal(self._viewer, "firstFrameReady")
        self._refresh_event_surface_filter()
        self.refresh_view_state()

    def refresh_view_state(self) -> None:
        self._relayout()
        self._sync_saved_hover_from_cursor("refresh")

    def set_overlay_active(self, active: bool) -> None:
        self._active = bool(active)
        if not self._active and self._manual_draft is None:
            self._set_hovered_face_id(None)
            self._cancel_editing()
        if not self._viewer_has_image_content() and self._manual_draft is None:
            self._set_hovered_face_id(None)
            self.setHidden(True)
            self._set_saved_hover_tracking_enabled(False)
            return
        self._sync_child_visibility()
        self.update()

    def set_annotations(self, annotations: list[AssetFaceAnnotation]) -> None:
        self._set_hovered_face_id(None)
        self._cancel_editing()
        self.clear_manual_face_draft()
        self._clear_saved_states()
        self._annotations = list(annotations)
        for annotation in self._annotations:
            self._states[annotation.face_id] = _OverlayFaceState(annotation=annotation)
        self._refresh_event_surface_filter()
        self._relayout()
        if not self._viewer_has_image_content() and self._manual_draft is None:
            self.setHidden(True)

    def clear_annotations(self) -> None:
        self._set_hovered_face_id(None)
        self._cancel_editing()
        self.clear_manual_face_draft()
        self._clear_saved_states()
        self._annotations = []
        self._sync_child_visibility()
        self.update()

    def set_name_suggestions(self, suggestions: list[PersonSummary]) -> None:
        self.set_identity_suggestions(
            [
                RecognitionIdentitySuggestion(
                    identity_key=(
                        summary.person_id
                        if str(summary.person_id).startswith(("person:", "pet:"))
                        else f"person:{summary.person_id}"
                    ),
                    name=summary.name.strip(),
                    thumbnail_path=summary.thumbnail_path,
                    count=int(getattr(summary, "face_count", 0) or 0),
                )
                for summary in suggestions
                if isinstance(summary.name, str) and summary.name.strip()
            ]
        )

    def set_identity_suggestions(
        self,
        suggestions: list[RecognitionIdentitySuggestion],
    ) -> None:
        self._name_suggestions = [
            _NameSuggestion.from_identity(suggestion)
            for suggestion in suggestions
            if isinstance(suggestion.name, str) and suggestion.name.strip()
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
            self._manual_editor.setPlaceholderText(tr("FaceNameOverlay", "Click to Name"))
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
        self._sync_saved_hover_from_cursor("resize")

    def hideEvent(self, event) -> None:  # type: ignore[override]
        self._teardown_saved_hover_tracking()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._teardown_saved_hover_tracking()
        super().closeEvent(event)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        if not self.isVisible():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self._active and self._hovered_face_id:
            state = self._states.get(self._hovered_face_id)
            if state is not None and not state.layout.circle_rect.isEmpty():
                self._paint_circle(painter, state.layout.circle_rect, 0.72)
        if self._active and self._viewer_has_image_content():
            for face_id, state in self._states.items():
                if face_id == self._editing_face_id:
                    continue
                if state.layout.chip_rect.isEmpty():
                    continue
                self._paint_saved_chip(
                    painter,
                    state.layout.chip_rect,
                    state.layout.label_text,
                )
        if self._manual_draft is not None:
            self._paint_circle(painter, self._manual_circle_rect(), 0.9)
            self._paint_button(painter, self._manual_cancel_rect(), "x")
            self._paint_button(painter, self._manual_handle_rect(), "")

    def eventFilter(self, watched: object, event: QEvent) -> bool:
        if self._handle_saved_pointer_event(watched, event):
            return True

        viewer = getattr(self, "_viewer", None)
        if watched is viewer:
            if event.type() == QEvent.Type.MouseMove:
                return self._handle_viewer_mouse_move(event)
            if event.type() == QEvent.Type.MouseButtonPress:
                return self._handle_viewer_mouse_press(event)
            if event.type() == QEvent.Type.MouseButtonRelease:
                return self._handle_viewer_mouse_release(event)
            if event.type() == QEvent.Type.Leave:
                self._drag_mode = None
            return super().eventFilter(watched, event)

        return super().eventFilter(watched, event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._handle_saved_pointer_event(self, event):
            return
        if self._handle_manual_mouse_press(QPointF(event.position()), event):
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._handle_manual_mouse_move(QPointF(event.position()), event):
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._handle_saved_pointer_event(self, event):
            return
        if self._handle_manual_mouse_release(event):
            return
        super().mouseReleaseEvent(event)

    def _handle_viewer_mouse_move(self, event: QEvent) -> bool:
        if self._viewer is None or not isinstance(event, QMouseEvent):
            return False
        point = QPointF(
            self.mapFromGlobal(self._viewer.mapToGlobal(event.position().toPoint()))
        )
        handled = self._handle_manual_mouse_move(point, event)
        return handled

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
        return False

    def _handle_viewer_mouse_press(self, event: QEvent) -> bool:
        if not isinstance(event, QMouseEvent) or event.button() != Qt.MouseButton.LeftButton:
            return False
        if self._viewer is None or self._manual_draft is None or self._manual_busy:
            return False
        point = QPointF(
            self.mapFromGlobal(self._viewer.mapToGlobal(event.position().toPoint()))
        )
        return self._handle_manual_mouse_press(point, event)

    def _handle_manual_mouse_press(self, point: QPointF, event: QMouseEvent) -> bool:
        if self._manual_draft is None or self._manual_busy:
            return False
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

    def _paint_saved_chip(self, painter: QPainter, rect: QRectF, text: str) -> None:
        path = QPainterPath()
        path.addRoundedRect(rect, 8.0, 8.0)
        painter.setPen(QPen(QColor(0, 0, 0, 28), 1.0))
        painter.setBrush(QColor(255, 255, 255, 230))
        painter.drawPath(path)
        painter.setFont(self._saved_chip_font())
        painter.setPen(QColor(24, 24, 24, 230))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    def _clear_saved_states(self) -> None:
        self._saved_press_face_id = None
        self._states.clear()

    def _refresh_event_surface_filter(self) -> None:
        surface = self.parentWidget()
        if surface is self._event_surface:
            return
        if self._event_surface is not None:
            try:
                self._event_surface.removeEventFilter(self)
            except RuntimeError:
                pass
        self._event_surface = surface if isinstance(surface, QWidget) else None
        if self._event_surface is not None:
            self._event_surface.setMouseTracking(True)
            self._event_surface.installEventFilter(self)

    def _connect_viewer_signal(self, viewer: QWidget, name: str) -> None:
        signal = getattr(viewer, name, None)
        if signal is not None:
            signal.connect(self.refresh_view_state)

    def _disconnect_viewer_signal(self, viewer: QWidget, name: str) -> None:
        signal = getattr(viewer, name, None)
        if signal is None:
            return
        try:
            signal.disconnect(self.refresh_view_state)
        except (RuntimeError, TypeError):
            pass

    def _display_name(self, annotation: AssetFaceAnnotation) -> str:
        if getattr(annotation, "promotion_state", "legacy_visible") == "candidate":
            display_name = tr("FaceNameOverlay", "Pending confirmation")
        else:
            name = getattr(annotation, "canonical_display_name", None) or annotation.display_name
            display_name = (
                name.strip()
                if isinstance(name, str) and name.strip()
                else tr("FaceNameOverlay", "unnamed")
            )
        if bool(getattr(annotation, "is_stale", False)):
            return tr(
                "FaceNameOverlay",
                "%1 · previous generation",
            ).replace("%1", display_name)
        return display_name

    def retranslate_ui(self) -> None:
        """Refresh overlay labels after the application language changes."""

        self._relayout()
        if self._manual_editor is not None:
            self._manual_editor.setPlaceholderText(tr("FaceNameOverlay", "Click to Name"))

    def _sync_child_visibility(self) -> None:
        viewer_ready = self._viewer_has_image_content()
        if not viewer_ready and self._manual_draft is None:
            self._set_hovered_face_id(None)
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self.setHidden(True)
            if self._editor is not None:
                self._editor.hide()
            self._set_saved_hover_tracking_enabled(False)
            return
        show_saved = self._active and viewer_ready and bool(self._states)
        show_manual = self._manual_draft is not None and viewer_ready
        if not show_saved:
            self._set_hovered_face_id(None)
        elif self._editing_face_id is None and self._manual_draft is None:
            self._ensure_viewer_allows_overlay_stacking()
        self.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            not show_manual,
        )
        self.setHidden(not (show_saved or show_manual))
        if self._editor is not None:
            self._editor.setVisible(show_saved and self._editing_face_id is not None)
        if self._manual_editor is not None:
            self._manual_editor.setVisible(show_manual)
            self._manual_editor.setEnabled(not self._manual_busy)
        self._set_saved_hover_tracking_enabled(
            show_saved and self._editing_face_id is None and self._manual_draft is None
        )
        self.raise_interactive_controls()

    def raise_interactive_controls(self) -> None:
        if self.isVisible():
            self.raise_()
        if self._editor is not None and self._editor.isVisible():
            self._editor.raise_()
        if self._manual_editor is not None and self._manual_editor.isVisible():
            self._manual_editor.raise_()

    def _viewer_has_image_content(self) -> bool:
        viewer = getattr(self, "_viewer", None)
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
        if viewer_rect.isEmpty() or (
            not self._viewer_has_image_content() and self._manual_draft is None
        ):
            for state in self._states.values():
                state.layout = _SavedFaceLayout(label_text=self._display_name(state.annotation))
            return
        for face_id, state in self._states.items():
            rect = self._map_annotation_rect(state.annotation)
            label_text = self._display_name(state.annotation)
            if rect.isEmpty():
                state.layout = _SavedFaceLayout(label_text=label_text)
                continue
            chip_size = self._saved_chip_size(label_text)
            chip_rect = QRectF(
                self._chip_rect_for_face(
                    rect,
                    chip_size.width(),
                    chip_size.height(),
                    viewer_rect,
                )
            )
            hover_rect = chip_rect.adjusted(
                -_CHIP_HOVER_MARGIN,
                -_CHIP_HOVER_MARGIN,
                _CHIP_HOVER_MARGIN,
                _CHIP_HOVER_MARGIN,
            ).intersected(QRectF(viewer_rect))
            state.layout = _SavedFaceLayout(
                face_rect=rect,
                chip_rect=chip_rect,
                hover_rect=hover_rect,
                circle_rect=self._circle_rect_for_face(rect),
                label_text=label_text,
            )
        if self._editor is not None and self._editing_face_id is not None:
            state = self._states.get(self._editing_face_id)
            if state is not None and not state.layout.face_rect.isEmpty():
                chip_size = self._saved_chip_size(self._display_name(state.annotation))
                editor_rect = self._chip_rect_for_face(
                    state.layout.face_rect,
                    max(chip_size.width() + 12, 120),
                    chip_size.height(),
                    viewer_rect,
                )
                self._editor.setGeometry(editor_rect)
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
        self.raise_interactive_controls()
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

    def _saved_chip_font(self):
        font = self.font()
        font.setPixelSize(13)
        return font

    def _saved_chip_size(self, text: str) -> QSize:
        metrics = QFontMetrics(self._saved_chip_font())
        return QSize(
            metrics.horizontalAdvance(text) + (_LABEL_MARGIN_X * 2),
            metrics.height() + (_LABEL_MARGIN_Y * 2),
        )

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

    def _chip_hover_rect(self, face_id: str) -> QRectF:
        state = self._states.get(face_id)
        return QRectF(state.layout.hover_rect) if state is not None else QRectF()

    def _hit_chip_id(self, point: QPointF) -> str | None:
        hits = [
            (_distance(state.layout.hover_rect.center(), point), face_id)
            for face_id, state in self._states.items()
            if face_id != self._editing_face_id
            and not state.layout.hover_rect.isEmpty()
            and state.layout.hover_rect.contains(point)
        ]
        hits.sort(key=lambda item: item[0])
        return hits[0][1] if hits else None

    def _handle_saved_pointer_event(self, watched: object, event: QEvent) -> bool:
        event_type = event.type()
        if event_type == QEvent.Type.MouseButtonRelease and self._saved_press_face_id is not None:
            if isinstance(event, QMouseEvent) and event.button() == Qt.MouseButton.LeftButton:
                self._saved_press_face_id = None
                event.accept()
                return True
        if event_type == QEvent.Type.MouseButtonPress and isinstance(event, QMouseEvent):
            if event.button() != Qt.MouseButton.LeftButton:
                return False
            if not self._saved_hit_testing_enabled(allow_editing=True):
                return False
            global_pos = self._global_pos_from_event(watched, event)
            if not self._saved_chip_layer_available(global_pos):
                self._sync_saved_hover_from_global_pos(global_pos, "blocked-press")
                return False
            face_id = self._saved_face_id_at_global_pos(global_pos)
            self._sync_saved_hover_from_global_pos(global_pos, "pointer-press")
            if face_id is None:
                self._saved_press_face_id = None
                return False
            event.accept()
            self._start_editing(face_id)
            self._saved_press_face_id = face_id
            return True
        if not self._saved_hover_enabled():
            return False
        if event_type == QEvent.Type.MouseMove and isinstance(event, QMouseEvent):
            if event.buttons() == Qt.MouseButton.NoButton:
                self._sync_saved_hover_from_mouse_event(watched, event, "pointer-move")
            return False
        if event_type in (QEvent.Type.Enter, QEvent.Type.HoverEnter, QEvent.Type.HoverMove):
            if not self._sync_saved_hover_from_position_event(watched, event, "pointer-hover"):
                self._queue_saved_hover_sync_from_cursor("pointer-hover")
            return False
        if event_type == QEvent.Type.Leave:
            self._queue_saved_hover_sync_from_cursor("pointer-leave")
            return False
        return False

    def _saved_hover_enabled(self) -> bool:
        return self._saved_hit_testing_enabled(allow_editing=False)

    def _saved_hit_testing_enabled(self, *, allow_editing: bool) -> bool:
        return (
            (allow_editing or getattr(self, "_editing_face_id", None) is None)
            and getattr(self, "_manual_draft", None) is None
            and getattr(self, "_active", False)
            and bool(getattr(self, "_states", {}))
            and self._viewer_has_image_content()
            and self.isVisible()
        )

    def _global_pos_from_event(self, watched: object, event: QEvent) -> QPoint:
        position = getattr(event, "position", None)
        if isinstance(watched, QWidget) and callable(position):
            return watched.mapToGlobal(position().toPoint())
        global_position = getattr(event, "globalPosition", None)
        if callable(global_position):
            return global_position().toPoint()
        return QCursor.pos()

    def _saved_face_id_at_global_pos(self, global_pos: QPoint) -> str | None:
        return self._hit_chip_id(QPointF(self.mapFromGlobal(global_pos)))

    def _set_hovered_face_id(self, face_id: str | None, global_pos: QPoint | None = None) -> None:
        states = getattr(self, "_states", {})
        if face_id is not None and face_id not in states:
            face_id = None
        if getattr(self, "_hovered_face_id", None) == face_id:
            self._apply_saved_hover_cursor(face_id is not None, global_pos)
            return
        self._hovered_face_id = face_id
        self._apply_saved_hover_cursor(face_id is not None, global_pos)
        self.update()

    def _update_saved_hover_from_point(self, point: QPointF) -> None:
        self._sync_saved_hover_from_global_pos(
            self.mapToGlobal(point.toPoint()),
            "local-point",
        )

    def _sync_saved_hover_from_mouse_event(
        self,
        watched: object,
        event: QMouseEvent,
        reason: str,
    ) -> None:
        self._sync_saved_hover_from_global_pos(
            self._global_pos_from_event(watched, event),
            reason,
        )

    def _sync_saved_hover_from_position_event(
        self,
        watched: object,
        event: QEvent,
        reason: str,
    ) -> bool:
        if not callable(getattr(event, "position", None)) and not callable(
            getattr(event, "globalPosition", None)
        ):
            return False
        self._sync_saved_hover_from_global_pos(
            self._global_pos_from_event(watched, event),
            reason,
        )
        return True

    def _sync_saved_hover_from_cursor(self, reason: str) -> None:
        self._sync_saved_hover_from_global_pos(QCursor.pos(), reason)

    def _sync_saved_hover_from_global_pos(
        self,
        global_pos: QPoint,
        reason: str,
        *,
        queued: bool = False,
    ) -> None:
        del reason
        if not queued:
            self._saved_hover_sync_generation = (
                getattr(self, "_saved_hover_sync_generation", 0) + 1
            )
        if (
            getattr(self, "_editing_face_id", None) is not None
            or getattr(self, "_manual_draft", None) is not None
            or not getattr(self, "_active", False)
            or not self._viewer_has_image_content()
            or not self.isVisible()
        ):
            self._set_hovered_face_id(None)
            return
        if not self._saved_chip_layer_available(global_pos):
            self._set_hovered_face_id(None)
            return
        local_pos = QPointF(self.mapFromGlobal(global_pos))
        self._set_hovered_face_id(self._hit_chip_id(local_pos), global_pos)

    def _saved_chip_layer_available(self, global_pos: QPoint) -> bool:
        top_widget = QApplication.widgetAt(global_pos)
        if top_widget is None:
            return self._point_is_inside_viewer(global_pos)
        if self._is_viewer_layer_widget(top_widget):
            return True
        if top_widget is self:
            return True
        if top_widget.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents):
            return True
        return False

    def _point_is_inside_viewer(self, global_pos: QPoint) -> bool:
        viewer = self._viewer
        if viewer is None:
            return False
        viewer_rect = QRect(viewer.mapToGlobal(QPoint(0, 0)), viewer.size())
        return viewer_rect.contains(global_pos)

    def _is_viewer_layer_widget(self, widget: QWidget) -> bool:
        viewer = self._viewer
        if viewer is None:
            return False
        current: QWidget | None = widget
        while current is not None:
            if current is viewer:
                return True
            current = current.parentWidget()
        current = viewer
        surface = self.parentWidget()
        while current is not None and current is not surface:
            if widget is current:
                return True
            current = current.parentWidget()
        return False

    def _queue_saved_hover_sync_from_cursor(
        self,
        reason: str,
        source: object | None = None,
    ) -> None:
        del source
        if getattr(self, "_saved_hover_sync_queued", False):
            return
        self._saved_hover_sync_queued = True
        self._queued_saved_hover_reason = reason
        self._queued_saved_hover_generation = getattr(
            self,
            "_saved_hover_sync_generation",
            0,
        )
        self._saved_hover_sync_timer.start()

    def _flush_queued_saved_hover_sync(self) -> None:
        self._saved_hover_sync_queued = False
        generation = self._queued_saved_hover_generation
        if generation != getattr(self, "_saved_hover_sync_generation", 0):
            return
        self._sync_saved_hover_from_global_pos(
            QCursor.pos(),
            self._queued_saved_hover_reason,
            queued=True,
        )

    def _prune_stale_hover(self) -> None:
        self._sync_saved_hover_from_cursor("prune")

    def _poll_saved_hover_from_cursor(self) -> None:
        self._sync_saved_hover_from_cursor("poll")

    def _set_saved_hover_tracking_enabled(self, enabled: bool) -> None:
        app = QApplication.instance()
        target = bool(enabled and app is not None)
        if target and not self._saved_hover_app_filter_installed:
            app.installEventFilter(self)
            self._saved_hover_app_filter_installed = True
        elif not target and self._saved_hover_app_filter_installed:
            try:
                app.removeEventFilter(self) if app is not None else None
            except RuntimeError:
                pass
            self._saved_hover_app_filter_installed = False
        if target:
            if not self._saved_hover_poll_timer.isActive():
                self._saved_hover_poll_timer.start()
        else:
            self._saved_hover_poll_timer.stop()
            self._saved_hover_sync_timer.stop()
            self._saved_hover_sync_queued = False
            self._saved_press_face_id = None
            self._apply_saved_hover_cursor(False)

    def _apply_saved_hover_cursor(
        self,
        active: bool,
        global_pos: QPoint | None = None,
    ) -> None:
        app = QApplication.instance()
        if active:
            self._guard_widget_cursors(global_pos or QCursor.pos())
            if app is not None:
                cursor = QCursor(Qt.CursorShape.IBeamCursor)
                if self._cursor_override_active:
                    QApplication.changeOverrideCursor(cursor)
                else:
                    QApplication.setOverrideCursor(cursor)
                    self._cursor_override_active = True
            return
        self._restore_guarded_widget_cursors()
        if self._cursor_override_active and app is not None:
            try:
                QApplication.restoreOverrideCursor()
            except RuntimeError:
                pass
        self._cursor_override_active = False

    def _guard_widget_cursors(self, global_pos: QPoint) -> None:
        for widget in self._cursor_guard_candidates(global_pos):
            if widget not in self._cursor_guard_widgets:
                self._cursor_guard_widgets[widget] = (
                    QCursor(widget.cursor())
                    if widget.testAttribute(Qt.WidgetAttribute.WA_SetCursor)
                    else None
                )
            widget.setCursor(Qt.CursorShape.IBeamCursor)

    def _cursor_guard_candidates(self, global_pos: QPoint) -> list[QWidget]:
        candidates: list[QWidget] = []

        def add(widget: QWidget | None) -> None:
            if widget is not None and widget not in candidates:
                candidates.append(widget)

        add(QApplication.widgetAt(global_pos))
        add(self._viewer)
        add(self.parentWidget())
        add(self)
        surface = self.parentWidget()
        if surface is not None:
            for child in surface.findChildren(QWidget):
                if not child.isVisible():
                    continue
                child_rect = QRect(child.mapToGlobal(QPoint(0, 0)), child.size())
                if child_rect.contains(global_pos):
                    add(child)
        return candidates

    def _restore_guarded_widget_cursors(self) -> None:
        for widget, cursor in list(self._cursor_guard_widgets.items()):
            try:
                if cursor is None:
                    widget.unsetCursor()
                else:
                    widget.setCursor(cursor)
            except RuntimeError:
                pass
        self._cursor_guard_widgets.clear()

    def _ensure_viewer_allows_overlay_stacking(self) -> None:
        viewer = self._viewer
        if viewer is None:
            return
        transparent_clip = bool(getattr(viewer, "_transparent_rounded_clip_enabled", False))
        always_on_top = viewer.testAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop)
        if not transparent_clip and not always_on_top:
            return
        set_transparent_rounded_clip = getattr(viewer, "set_transparent_rounded_clip", None)
        if callable(set_transparent_rounded_clip):
            set_transparent_rounded_clip(0.0)
        viewer.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, False)

    def _teardown_saved_hover_tracking(self) -> None:
        self._set_hovered_face_id(None)
        self._set_saved_hover_tracking_enabled(False)

    def _start_editing(self, face_id: str) -> None:
        state = self._states.get(face_id)
        if state is None:
            return
        if (
            not state.annotation.person_id
            and getattr(state.annotation, "promotion_state", "legacy_visible")
            != "candidate"
        ):
            return
        self._cancel_editing()
        self._set_hovered_face_id(None)
        self._set_saved_hover_tracking_enabled(False)
        self._editing_face_id = face_id
        editor = _FaceNameEditor(self.parentWidget() or self)
        editor.set_name_suggestions(self._name_suggestions)
        editor.setText(
            getattr(state.annotation, "canonical_display_name", None)
            or state.annotation.display_name
            or ""
        )
        editor.commitRequested.connect(self._commit_editing)
        editor.cancelRequested.connect(self._cancel_editing)
        self._editor = editor
        self._relayout()
        editor.show()
        editor.setFocus(Qt.FocusReason.MouseFocusReason)
        editor.selectAll()

    def _commit_editing(self) -> None:
        if self._editing_face_id is None or self._editor is None:
            return
        state = self._states.get(self._editing_face_id)
        if state is None:
            self._cancel_editing()
            return
        new_name = self._editor.text().strip() or None
        person_id = state.annotation.person_id
        if not person_id and not new_name:
            self._cancel_editing()
            return
        if hasattr(state.annotation, "canonical_display_name"):
            fields = getattr(state.annotation, "__dataclass_fields__", {})
            changes = {"canonical_display_name": new_name}
            if "display_name" in fields:
                changes["display_name"] = new_name
            if new_name and "promotion_state" in fields:
                changes["promotion_state"] = "confirmed"
            state.annotation = replace(state.annotation, **changes)
        else:
            state.annotation = replace(state.annotation, display_name=new_name)
        updated_annotation = state.annotation
        self._teardown_editor(show_chip=True)
        if person_id:
            self.renameSubmitted.emit(person_id, new_name)
        elif new_name:
            self.unassignedRenameSubmitted.emit(updated_annotation, new_name)

    def _cancel_editing(self) -> None:
        if self._editing_face_id is None and self._editor is None:
            return
        self._teardown_editor(show_chip=True)

    def _teardown_editor(self, *, show_chip: bool) -> None:
        del show_chip
        face_id = self._editing_face_id
        editor = self._editor
        self._editing_face_id = None
        self._editor = None
        if editor is not None:
            editor.hide()
            editor.deleteLater()
        if face_id is not None:
            self._saved_press_face_id = None
        self._relayout()

    def _ensure_manual_editor(self) -> None:
        if self._manual_draft is None:
            return
        if self._manual_editor is None:
            editor = _FaceNameEditor(self.parentWidget() or self)
            editor.set_name_suggestions(self._name_suggestions)
            editor.setPlaceholderText(tr("FaceNameOverlay", "Click to Name"))
            editor.commitRequested.connect(self._submit_manual_face)
            editor.cancelRequested.connect(self.clear_manual_face_draft)
            self._manual_editor = editor

    def _submit_manual_face(self) -> None:
        if self._manual_draft is None or self._manual_editor is None or self._manual_busy:
            return
        trimmed = self._manual_editor.text().strip()
        if not trimmed:
            self.show_manual_error(
                tr("FaceNameOverlay", "Please enter a name before saving the face.")
            )
            self._manual_editor.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        requested_box = self._manual_requested_box()
        if requested_box is None:
            self.show_manual_error(
                tr("FaceNameOverlay", "Please place the circle on the face before saving.")
            )
            return
        self._manual_busy = True
        self._manual_editor.setEnabled(False)
        self.manualFaceSubmitted.emit(
            {
                "name": trimmed,
                "person_id": self._manual_editor.suggestion_person_id(),
                "identity_key": self._manual_editor.suggestion_identity_key(),
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
    pixmap = load_qpixmap(path)
    if pixmap is None or pixmap.isNull():
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

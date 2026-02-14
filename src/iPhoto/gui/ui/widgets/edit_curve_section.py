"""Curve adjustment section for the edit sidebar."""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

import numpy as np
from PySide6.QtCore import QPointF, Qt, Signal, Slot
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ....core.curve_resolver import DEFAULT_CURVE_POINTS
from ..models.edit_session import EditSession
from ..icon import load_icon

# Import extracted classes from their new modules
from ._styled_combo_box import _StyledComboBox
from .input_level_sliders import InputLevelSliders
from .curve_graph import CurveGraph

_LOGGER = logging.getLogger(__name__)


class EditCurveSection(QWidget):
    """Expose the curve adjustment controls as a section in the edit sidebar."""

    curveParamsPreviewed = Signal(object)
    """Emitted while the user drags a control so the viewer can update live."""

    curveParamsCommitted = Signal(object)
    """Emitted once the interaction ends and the session should persist the change."""

    interactionStarted = Signal()
    interactionFinished = Signal()
    eyedropperModeChanged = Signal(object)

    EDGE_INSET = 8
    MIN_CONTENT_WIDTH = 240
    TOOL_GAP = 8
    TOOL_HEIGHT_RATIO = 0.55
    TOOL_HEIGHT = 32

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._session: Optional[EditSession] = None
        self._updating_ui = False
        self._eyedropper_mode: Optional[str] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(self.EDGE_INSET, 0, self.EDGE_INSET, 0)
        layout.setSpacing(8)

        # Channel selector
        self.channel_combo = _StyledComboBox(self)
        self.channel_combo.addItems(["RGB", "Red", "Green", "Blue"])
        self.channel_combo.currentTextChanged.connect(self._on_channel_changed)
        self.channel_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.channel_combo, alignment=Qt.AlignLeft)

        # Tools container (eyedropper + add point)
        self.tools_container = QWidget()
        self.tools_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        tools_layout = QHBoxLayout(self.tools_container)
        tools_layout.setContentsMargins(0, 0, 0, 0)
        tools_layout.setSpacing(self.TOOL_GAP)

        eyedropper_btn_width = self.MIN_CONTENT_WIDTH // 4
        eyedropper_btn_height = min(
            self.TOOL_HEIGHT,
            int(eyedropper_btn_width * self.TOOL_HEIGHT_RATIO),
        )

        tools_frame = QFrame()
        tools_frame.setFixedWidth(eyedropper_btn_width * 3)
        tools_frame.setStyleSheet(
            ".QFrame { background-color: #383838; border-radius: 5px; border: 1px solid #555; }"
        )
        eyedropper_layout = QHBoxLayout(tools_frame)
        eyedropper_layout.setContentsMargins(0, 0, 0, 0)
        eyedropper_layout.setSpacing(0)

        self.btn_black = QToolButton()
        self.btn_black.setIcon(load_icon("eyedropper.full.svg", color="white"))
        self.btn_black.setToolTip("Set Black Point - Click to pick darkest point in image")
        self.btn_black.setCheckable(True)
        self.btn_black.setFixedSize(eyedropper_btn_width, eyedropper_btn_height)

        self.btn_gray = QToolButton()
        self.btn_gray.setIcon(load_icon("eyedropper.halffull.svg", color="white"))
        self.btn_gray.setToolTip("Set Gray Point - Click to pick mid-tone in image")
        self.btn_gray.setCheckable(True)
        self.btn_gray.setFixedSize(eyedropper_btn_width, eyedropper_btn_height)

        self.btn_white = QToolButton()
        self.btn_white.setIcon(load_icon("eyedropper.svg", color="white"))
        self.btn_white.setToolTip("Set White Point - Click to pick brightest point in image")
        self.btn_white.setCheckable(True)
        self.btn_white.setFixedSize(eyedropper_btn_width, eyedropper_btn_height)

        eyedropper_style = """
            QToolButton {
                background-color: transparent;
                border: none;
                border-radius: 0px;
            }
            QToolButton:hover {
                background-color: #444;
            }
            QToolButton:pressed {
                background-color: #222;
            }
            QToolButton:checked {
                background-color: #4a90e2;
            }
        """
        self.btn_black.setStyleSheet(eyedropper_style + "border-right: 1px solid #555;")
        self.btn_gray.setStyleSheet(eyedropper_style + "border-right: 1px solid #555;")
        self.btn_white.setStyleSheet(eyedropper_style)

        self.btn_black.clicked.connect(self._activate_black_eyedropper)
        self.btn_gray.clicked.connect(self._activate_gray_eyedropper)
        self.btn_white.clicked.connect(self._activate_white_eyedropper)

        eyedropper_layout.addWidget(self.btn_black)
        eyedropper_layout.addWidget(self.btn_gray)
        eyedropper_layout.addWidget(self.btn_white)
        tools_layout.addWidget(tools_frame)

        self.btn_add_point = QToolButton()
        self.btn_add_point.setIcon(load_icon("circle.cross.svg"))
        self.btn_add_point.setToolTip("Add Point to Curve")
        self.btn_add_point.setFixedSize(eyedropper_btn_width, eyedropper_btn_height)
        self.btn_add_point.clicked.connect(self._on_add_point_clicked)
        self.btn_add_point.setStyleSheet("""
            QToolButton { background-color: #383838; border: 1px solid #555; border-radius: 4px; }
            QToolButton:hover { background-color: #444; }
        """)
        tools_layout.addWidget(self.btn_add_point)

        layout.addWidget(self.tools_container, alignment=Qt.AlignLeft)

        # Graph + sliders container
        graph_sliders_layout = QVBoxLayout()
        graph_sliders_layout.setSpacing(0)
        graph_sliders_layout.setContentsMargins(0, 0, 0, 0)

        self.curve_graph = CurveGraph(size=self.MIN_CONTENT_WIDTH)
        self.curve_graph.curveChanged.connect(self._on_curve_changed)
        self.curve_graph.startPointMoved.connect(self._on_start_point_moved)
        self.curve_graph.endPointMoved.connect(self._on_end_point_moved)
        self.curve_graph.interactionStarted.connect(self.interactionStarted)
        self.curve_graph.interactionFinished.connect(self._on_curve_interaction_finished)
        graph_sliders_layout.addWidget(self.curve_graph, alignment=Qt.AlignLeft)

        self.input_sliders = InputLevelSliders(size=self.MIN_CONTENT_WIDTH)
        self.input_sliders.blackPointChanged.connect(self._on_black_point_changed)
        self.input_sliders.whitePointChanged.connect(self._on_white_point_changed)
        graph_sliders_layout.addWidget(self.input_sliders, alignment=Qt.AlignLeft)

        layout.addLayout(graph_sliders_layout)
        layout.addStretch(1)
        self._update_control_sizes(self.width())

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._update_control_sizes(event.size().width())

    def _update_control_sizes(self, available_width: int) -> None:
        content_width = max(
            self.MIN_CONTENT_WIDTH,
            int(available_width - self.EDGE_INSET * 2),
        )
        self.tools_container.setFixedWidth(content_width)
        self.channel_combo.setFixedWidth(content_width)
        self.curve_graph.setFixedSize(content_width, content_width)
        self.input_sliders.setFixedWidth(content_width)

        eyedropper_btn_width = max(44, int((content_width - self.TOOL_GAP) / 4))
        eyedropper_btn_height = min(
            self.TOOL_HEIGHT,
            int(eyedropper_btn_width * self.TOOL_HEIGHT_RATIO),
        )

        self.btn_black.setFixedSize(eyedropper_btn_width, eyedropper_btn_height)
        self.btn_gray.setFixedSize(eyedropper_btn_width, eyedropper_btn_height)
        self.btn_white.setFixedSize(eyedropper_btn_width, eyedropper_btn_height)
        icon_size = self.btn_black.size() * 0.65
        self.btn_black.setIconSize(icon_size)
        self.btn_gray.setIconSize(icon_size)
        self.btn_white.setIconSize(icon_size)
        self.btn_add_point.setFixedSize(eyedropper_btn_width, eyedropper_btn_height)
        self.btn_add_point.setIconSize(self.btn_add_point.size() * 0.65)

        tools_frame = self.btn_black.parentWidget()
        if isinstance(tools_frame, QFrame):
            tools_frame.setFixedWidth(eyedropper_btn_width * 3)

    def bind_session(self, session: Optional[EditSession]) -> None:
        """Attach *session* so curve updates are persisted and reflected."""
        if self._session is session:
            return

        if self._session is not None:
            try:
                self._session.valueChanged.disconnect(self._on_session_value_changed)
            except (TypeError, RuntimeError):
                # Signal may already be disconnected or was never connected; safe to ignore.
                pass
            try:
                self._session.resetPerformed.disconnect(self._on_session_reset)
            except (TypeError, RuntimeError):
                # Signal may already be disconnected or was never connected; safe to ignore.
                pass

        self._session = session

        if session is not None:
            session.valueChanged.connect(self._on_session_value_changed)
            session.resetPerformed.connect(self._on_session_reset)
            self.refresh_from_session()
        else:
            self._reset_to_defaults()

    def refresh_from_session(self) -> None:
        """Synchronise the curve state with the active session."""
        if self._session is None:
            self._reset_to_defaults()
            return

        self._updating_ui = True
        try:
            # Load curve data from session
            curve_data = {}
            for session_key, graph_key in [
                ("Curve_RGB", "RGB"),
                ("Curve_Red", "Red"),
                ("Curve_Green", "Green"),
                ("Curve_Blue", "Blue"),
            ]:
                raw = self._session.value(session_key)
                if raw and isinstance(raw, list):
                    curve_data[graph_key] = raw
                else:
                    curve_data[graph_key] = list(DEFAULT_CURVE_POINTS)

            self.curve_graph.set_curve_data(curve_data)

            # Update sliders for current channel
            points = self.curve_graph.channels[self.curve_graph.active_channel]
            if points:
                self.input_sliders.setBlackPoint(points[0].x())
                self.input_sliders.setWhitePoint(points[-1].x())
        finally:
            self._updating_ui = False

    def _reset_to_defaults(self) -> None:
        self._updating_ui = True
        try:
            self.curve_graph.reset_curves()
            self.input_sliders.setBlackPoint(0.0)
            self.input_sliders.setWhitePoint(1.0)
        finally:
            self._updating_ui = False

    def set_preview_image(self, image) -> None:
        """Forward histogram data to the curve graph."""
        if image is None:
            self.curve_graph.set_histogram(None)
            return
        histogram = self._compute_histogram(image)
        self.curve_graph.set_histogram(histogram)

    def _compute_histogram(self, image) -> Optional[np.ndarray]:
        """Return normalized histogram data for *image*."""

        if image is None or image.isNull():
            return None

        try:
            preview = image.convertToFormat(QImage.Format.Format_RGBA8888)
        except Exception:
            return None

        width = preview.width()
        height = preview.height()
        if width <= 0 or height <= 0:
            return None

        bytes_per_line = preview.bytesPerLine()
        buffer = preview.constBits()
        byte_count = bytes_per_line * height
        try:
            buffer.setsize(byte_count)
        except AttributeError:
            pass
        view = memoryview(buffer)
        buffer_array = np.frombuffer(view, dtype=np.uint8, count=byte_count)
        try:
            surface = buffer_array.reshape((height, bytes_per_line))
        except ValueError:
            return None

        pixels = surface[:, : width * 4].reshape((height, width, 4))
        rgb = pixels[:, :, :3].reshape(-1, 3)
        if rgb.size == 0:
            return None

        hist = np.zeros((3, 256), dtype=np.float32)
        for channel in range(3):
            counts = np.bincount(rgb[:, channel], minlength=256).astype(np.float32)
            hist[channel] = counts

        max_val = float(hist.max())
        if max_val > 0.0:
            hist /= max_val
        return hist

    @Slot(str, object)
    def _on_session_value_changed(self, key: str, _value: object) -> None:
        if key.startswith("Curve_"):
            self.refresh_from_session()

    @Slot()
    def _on_session_reset(self) -> None:
        self.refresh_from_session()

    def _on_channel_changed(self, channel: str) -> None:
        self.curve_graph.set_channel(channel)

    def _on_add_point_clicked(self) -> None:
        self.interactionStarted.emit()
        self.curve_graph.add_point_smart()
        self._commit_curve_changes()
        self.interactionFinished.emit()

    def deactivate_eyedropper(self) -> None:
        """Public interface to turn off all eyedropper buttons."""

        self._deactivate_all_eyedroppers()

    def _deactivate_all_eyedroppers(self) -> None:
        self._eyedropper_mode = None
        self.btn_black.setChecked(False)
        self.btn_gray.setChecked(False)
        self.btn_white.setChecked(False)
        self.eyedropperModeChanged.emit(None)

    def _activate_black_eyedropper(self) -> None:
        if self.btn_black.isChecked():
            self._eyedropper_mode = "black"
            self.btn_gray.setChecked(False)
            self.btn_white.setChecked(False)
            self.eyedropperModeChanged.emit("black")
        else:
            self._deactivate_all_eyedroppers()

    def _activate_gray_eyedropper(self) -> None:
        if self.btn_gray.isChecked():
            self._eyedropper_mode = "gray"
            self.btn_black.setChecked(False)
            self.btn_white.setChecked(False)
            self.eyedropperModeChanged.emit("gray")
        else:
            self._deactivate_all_eyedroppers()

    def _activate_white_eyedropper(self) -> None:
        if self.btn_white.isChecked():
            self._eyedropper_mode = "white"
            self.btn_black.setChecked(False)
            self.btn_gray.setChecked(False)
            self.eyedropperModeChanged.emit("white")
        else:
            self._deactivate_all_eyedroppers()

    def handle_color_picked(self, r: float, g: float, b: float) -> None:
        """Handle the color picked by the eyedropper."""

        if self._eyedropper_mode is None:
            return

        luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
        luminance = max(0.0, min(1.0, luminance))

        self.interactionStarted.emit()
        if self._eyedropper_mode == "black":
            self._apply_black_point(luminance)
        elif self._eyedropper_mode == "gray":
            self._apply_gray_point(luminance)
        elif self._eyedropper_mode == "white":
            self._apply_white_point(luminance)
        self._commit_curve_changes()
        self.interactionFinished.emit()
        self._deactivate_all_eyedroppers()

    def _apply_black_point(self, luminance: float) -> None:
        channel = self.curve_graph.active_channel
        points = self.curve_graph.channels[channel]
        if not points:
            return

        new_x = luminance
        if len(points) > 1:
            new_x = min(new_x, points[1].x() - self.curve_graph.MIN_DISTANCE_THRESHOLD)
        new_x = max(0.0, new_x)

        points[0] = QPointF(new_x, 0.0)
        self.input_sliders.setBlackPoint(new_x)
        self.curve_graph._update_spline_and_emit()

    def _apply_white_point(self, luminance: float) -> None:
        channel = self.curve_graph.active_channel
        points = self.curve_graph.channels[channel]
        if not points:
            return

        new_x = luminance
        if len(points) > 1:
            new_x = max(new_x, points[-2].x() + self.curve_graph.MIN_DISTANCE_THRESHOLD)
        new_x = min(1.0, new_x)

        points[-1] = QPointF(new_x, 1.0)
        self.input_sliders.setWhitePoint(new_x)
        self.curve_graph._update_spline_and_emit()

    def _apply_gray_point(self, luminance: float) -> None:
        channel = self.curve_graph.active_channel
        points = self.curve_graph.channels[channel]
        if len(points) < 2:
            return

        start_x = points[0].x()
        end_x = points[-1].x()
        min_threshold = self.curve_graph.MIN_DISTANCE_THRESHOLD
        if luminance <= start_x + min_threshold or luminance >= end_x - min_threshold:
            return

        target_y = 0.5
        existing_idx = -1
        for i in range(1, len(points) - 1):
            if abs(points[i].x() - luminance) < min_threshold * 2:
                existing_idx = i
                break

        if existing_idx != -1:
            points[existing_idx] = QPointF(luminance, target_y)
            self.curve_graph.selected_index = existing_idx
        else:
            insert_idx = len(points) - 1
            for i in range(1, len(points)):
                if points[i].x() > luminance:
                    insert_idx = i
                    break

            prev_x = points[insert_idx - 1].x()
            next_x = points[insert_idx].x()
            if luminance > prev_x + min_threshold and luminance < next_x - min_threshold:
                points.insert(insert_idx, QPointF(luminance, target_y))
                self.curve_graph.selected_index = insert_idx

        self.curve_graph._update_spline_and_emit()

    def _on_curve_changed(self) -> None:
        if self._updating_ui:
            return
        self._preview_curve_changes()

    def _on_curve_interaction_finished(self) -> None:
        if self._updating_ui:
            return
        self._commit_curve_changes()
        self.interactionFinished.emit()

    def _on_start_point_moved(self, x: float) -> None:
        self.input_sliders.setBlackPoint(x)

    def _on_end_point_moved(self, x: float) -> None:
        self.input_sliders.setWhitePoint(x)

    def _on_black_point_changed(self, val: float) -> None:
        if self._updating_ui:
            return
        points = self.curve_graph.channels[self.curve_graph.active_channel]
        if not points:
            return

        if len(points) > 1:
            val = min(val, points[1].x() - self.curve_graph.MIN_DISTANCE_THRESHOLD)
        p0 = points[0]
        points[0] = QPointF(val, p0.y())
        self.curve_graph._update_spline_and_emit()

    def _on_white_point_changed(self, val: float) -> None:
        if self._updating_ui:
            return
        points = self.curve_graph.channels[self.curve_graph.active_channel]
        if not points:
            return

        p_end = points[-1]
        if len(points) > 1:
            val = max(val, points[-2].x() + self.curve_graph.MIN_DISTANCE_THRESHOLD)
        points[-1] = QPointF(val, p_end.y())
        self.curve_graph._update_spline_and_emit()

    def _gather_curve_params(self) -> Dict[str, List[Tuple[float, float]]]:
        """Gather current curve data from the graph."""
        return self.curve_graph.get_curve_data()

    def _preview_curve_changes(self) -> None:
        """Emit preview signal for live update."""
        curve_data = self._gather_curve_params()
        self.curveParamsPreviewed.emit(curve_data)

    def _commit_curve_changes(self) -> None:
        """Commit curve changes to the session."""
        if self._session is None:
            return

        curve_data = self._gather_curve_params()

        updates = {
            "Curve_Enabled": True,
            "Curve_RGB": curve_data.get("RGB", list(DEFAULT_CURVE_POINTS)),
            "Curve_Red": curve_data.get("Red", list(DEFAULT_CURVE_POINTS)),
            "Curve_Green": curve_data.get("Green", list(DEFAULT_CURVE_POINTS)),
            "Curve_Blue": curve_data.get("Blue", list(DEFAULT_CURVE_POINTS)),
        }
        self._session.set_values(updates)
        self.curveParamsCommitted.emit(curve_data)


__all__ = ["EditCurveSection"]

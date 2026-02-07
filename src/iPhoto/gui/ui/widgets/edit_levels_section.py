"""Levels adjustment section for the edit sidebar."""

from __future__ import annotations

import logging
import math
from typing import List, Optional

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal, Slot
from PySide6.QtGui import (
    QColor,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ....core.levels_resolver import DEFAULT_LEVELS_HANDLES
from ..models.edit_session import EditSession

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LevelsComposite – the interactive histogram + handle widget
# ---------------------------------------------------------------------------

def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


class LevelsComposite(QWidget):
    """Interactive levels widget with histogram backdrop and draggable handles."""

    valuesChanged = Signal(list)
    interactionStarted = Signal()
    interactionFinished = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(140)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet("background-color: #2b2b2b; border-radius: 6px;")

        self.histogram_data: Optional[np.ndarray] = None
        self.active_channel = "RGB"

        self.handles: List[float] = list(DEFAULT_LEVELS_HANDLES)

        self.hover_index = -1
        self.drag_index = -1
        self._drag_start_handles: Optional[List[float]] = None

        self.margin_side = 8
        self.hist_height = 120
        self.base_handle_width = 12

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def set_histogram(self, hist_data: Optional[np.ndarray]) -> None:
        self.histogram_data = hist_data
        self.update()

    def set_channel(self, channel: str) -> None:
        self.active_channel = channel
        self.update()

    # ------------------------------------------------------------------
    # Paint
    # ------------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()

        painter.fillRect(self.rect(), QColor("#2b2b2b"))

        axis_y = self.hist_height
        track_left = self.margin_side
        track_width = w - 2 * self.margin_side

        painter.save()
        hist_rect = QRectF(self.margin_side, 0, track_width, axis_y)
        painter.setClipRect(hist_rect)
        if self.histogram_data is not None:
            self._draw_real_histogram(painter, hist_rect)
        else:
            self._draw_fake_histogram(painter, hist_rect)
        painter.restore()

        self._draw_smart_guides(painter, w, axis_y)

        painter.setPen(QPen(QColor(80, 80, 80), 1))
        painter.drawLine(QPointF(track_left, axis_y), QPointF(w - self.margin_side, axis_y))

        self._draw_handles(painter, w, axis_y)

    # -- guides --

    def _draw_smart_guides(self, painter: QPainter, w: int, axis_y: int) -> None:
        track_width = w - 2 * self.margin_side
        line_color = QColor(255, 255, 255, 60)
        triangle_color = QColor(100, 100, 100)
        anchor_map = {1: 0.25, 2: 0.50, 3: 0.75}

        for i, handle_val in enumerate(self.handles):
            handle_x = self.margin_side + handle_val * track_width

            if i in anchor_map:
                anchor_x = self.margin_side + anchor_map[i] * track_width
                painter.setPen(QPen(line_color, 1))
                painter.drawLine(QPointF(anchor_x, 0), QPointF(handle_x, axis_y))
                self._draw_inverted_triangle(painter, anchor_x, 0, 6, triangle_color)
            elif i in (0, 4):
                painter.setPen(QPen(line_color, 1, Qt.PenStyle.DashLine))
                painter.drawLine(QPointF(handle_x, 0), QPointF(handle_x, axis_y))

    # -- handles --

    def _draw_handles(self, painter: QPainter, w: int, axis_y: int) -> None:
        track_width = w - 2 * self.margin_side

        for i, val in enumerate(self.handles):
            cx = self.margin_side + val * track_width
            is_active = (i == self.drag_index) or (i == self.hover_index)

            scale = 1.0
            draw_dot = False
            fill_color = QColor("#888888")
            dot_color = None

            if i == 0:
                scale, draw_dot, fill_color, dot_color = 1.0, True, QColor("#666666"), QColor("#000000")
            elif i == 1:
                scale, fill_color = 0.75, QColor("#444444")
            elif i == 2:
                fill_color = QColor("#888888")
            elif i == 3:
                scale, fill_color = 0.75, QColor("#BBBBBB")
            elif i == 4:
                scale, draw_dot, fill_color, dot_color = 1.0, True, QColor("#AAAAAA"), QColor("#FFFFFF")

            self._draw_teardrop(painter, cx, axis_y, scale, fill_color, draw_dot, dot_color, is_active)

    def _draw_teardrop(
        self, painter: QPainter, x_pos: float, y_top: float,
        scale: float, fill_color: QColor, draw_dot: bool,
        dot_color: Optional[QColor], is_active: bool,
    ) -> None:
        w = self.base_handle_width * scale
        h = 18 * scale
        hw = w / 2.0
        bezier_ctrl_y = h * 0.4
        radius = hw
        cy_circle = y_top + h - radius

        path = QPainterPath()
        path.moveTo(x_pos, y_top)
        path.cubicTo(
            x_pos + hw * 0.5, y_top + bezier_ctrl_y,
            x_pos + hw, cy_circle - radius * 0.5,
            x_pos + hw, cy_circle,
        )
        path.arcTo(x_pos - radius, cy_circle - radius, 2 * radius, 2 * radius, 0, -180)
        path.cubicTo(
            x_pos - hw, cy_circle - radius * 0.5,
            x_pos - hw * 0.5, y_top + bezier_ctrl_y,
            x_pos, y_top,
        )

        painter.setBrush(fill_color)
        if is_active:
            painter.setPen(QPen(QColor("#4a90e2"), 1.5))
        else:
            painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(path)

        if draw_dot and dot_color:
            painter.setBrush(dot_color)
            painter.setPen(Qt.PenStyle.NoPen)
            dot_r = 2.5 * scale
            painter.drawEllipse(QPointF(x_pos, cy_circle), dot_r, dot_r)

    @staticmethod
    def _draw_inverted_triangle(painter: QPainter, x: float, y: float, size: float, color: QColor) -> None:
        half = size / 2
        polygon = QPolygonF([QPointF(x - half, y), QPointF(x + half, y), QPointF(x, y + size)])
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawPolygon(polygon)

    # -- histogram drawing --

    def _draw_fake_histogram(self, painter: QPainter, rect: QRectF) -> None:
        x_start = rect.left()
        width = rect.width()
        height = rect.height()
        bottom = rect.bottom()

        path = QPainterPath()
        path.moveTo(x_start, bottom)

        steps = 100
        for i in range(steps + 1):
            tt = i / steps
            x = x_start + tt * width
            v1 = 0.6 * math.exp(-((tt - 0.25) ** 2) / 0.03)
            v2 = 0.5 * math.exp(-((tt - 0.55) ** 2) / 0.05)
            v3 = 0.8 * math.exp(-((tt - 0.85) ** 2) / 0.015)
            y_norm = min(1.0, max(0.0, v1 + v2 + v3))
            y = bottom - (y_norm * height * 0.9)
            path.lineTo(x, y)

        path.lineTo(x_start + width, bottom)
        path.closeSubpath()

        fill_color = QColor(160, 160, 160, 80)
        if self.active_channel == "Red":
            fill_color = QColor(255, 50, 50, 100)
        elif self.active_channel == "Green":
            fill_color = QColor(50, 255, 50, 100)
        elif self.active_channel == "Blue":
            fill_color = QColor(50, 50, 255, 100)

        painter.setBrush(fill_color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(path)
        painter.setPen(QPen(fill_color.lighter(150), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

    def _draw_real_histogram(self, painter: QPainter, rect: QRectF) -> None:
        if self.histogram_data is None:
            return

        c_idx = -1
        if self.active_channel == "Red":
            c_idx = 0
        elif self.active_channel == "Green":
            c_idx = 1
        elif self.active_channel == "Blue":
            c_idx = 2

        x_start = rect.left()
        width = rect.width()
        height = rect.height()
        bottom = rect.bottom()
        bin_w = width / 256.0

        channels_to_draw: list[tuple[int, QColor]] = []
        if c_idx != -1:
            col = QColor(
                255 if c_idx == 0 else 0,
                255 if c_idx == 1 else 0,
                255 if c_idx == 2 else 0,
                150,
            )
            channels_to_draw.append((c_idx, col))
        else:
            channels_to_draw.append((0, QColor(255, 0, 0, 80)))
            channels_to_draw.append((1, QColor(0, 255, 0, 80)))
            channels_to_draw.append((2, QColor(0, 0, 255, 80)))

        for ch_idx, color in channels_to_draw:
            path = QPainterPath()
            path.moveTo(x_start, bottom)
            data = self.histogram_data[ch_idx]
            for i, val in enumerate(data):
                x = x_start + i * bin_w
                y = bottom - (val * height * 0.95)
                if i == 0:
                    path.lineTo(x, y)
                path.lineTo(x + bin_w, y)
            path.lineTo(x_start + width, bottom)
            painter.setBrush(color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPath(path)

    # ------------------------------------------------------------------
    # Mouse interaction
    # ------------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        pos = event.position()
        if pos.y() < self.hist_height - 10:
            return

        w = self.width()
        track_width = w - 2 * self.margin_side

        threshold = self.base_handle_width * 1.5
        candidates: list[tuple[int, float]] = []
        for i, val in enumerate(self.handles):
            cx = self.margin_side + val * track_width
            dist = abs(pos.x() - cx)
            if dist < threshold:
                candidates.append((i, dist))

        if not candidates:
            return

        # Find the minimum distance among candidates.
        min_dist = min(d for _, d in candidates)
        # Keep only handles whose distance is within a tiny tolerance of the
        # closest so that overlapping handles are all considered equally close.
        best = [idx for idx, d in candidates if d - min_dist < 1.0]

        if len(best) == 1:
            clicked_idx = best[0]
        else:
            # Multiple handles overlap at the same position.  Pick the one
            # that the user can actually drag in the click direction so
            # overlapping handles can always be separated.
            click_x = pos.x()
            cluster_x = self.margin_side + self.handles[best[0]] * track_width
            if click_x >= cluster_x:
                # Click is to the right (or on top): grab the highest-index
                # handle so it can be dragged rightward.
                clicked_idx = best[-1]
            else:
                # Click is to the left: grab the lowest-index handle so it
                # can be dragged leftward.
                clicked_idx = best[0]

        self.drag_index = clicked_idx
        self._drag_start_handles = list(self.handles)
        self.interactionStarted.emit()
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        pos = event.position()
        w = self.width()
        track_width = w - 2 * self.margin_side
        if track_width <= 0:
            return

        val = _clamp01((pos.x() - self.margin_side) / track_width)

        if self.drag_index != -1:
            idx = self.drag_index

            if idx == 2 and self._drag_start_handles is not None:
                s = self._drag_start_handles
                bound_lo = self.handles[0]
                bound_hi = self.handles[4]
                val = max(bound_lo, min(bound_hi, val))
                self.handles[2] = val

                delta = val - s[2]

                if delta < 0:
                    orig_span = s[2] - s[0]
                    orig_ratio = (s[1] - s[0]) / orig_span if orig_span > 1e-9 else 0.0
                    cur_span = val - bound_lo
                    new1 = bound_lo + orig_ratio * cur_span
                else:
                    new1 = s[1] + delta
                new1 = _clamp01(new1)
                new1 = max(bound_lo, min(new1, val))
                self.handles[1] = new1

                if delta > 0:
                    orig_span = s[4] - s[2]
                    orig_ratio = (s[4] - s[3]) / orig_span if orig_span > 1e-9 else 0.0
                    cur_span = bound_hi - val
                    new3 = bound_hi - orig_ratio * cur_span
                else:
                    new3 = s[3] + delta
                new3 = _clamp01(new3)
                new3 = max(val, min(new3, bound_hi))
                self.handles[3] = new3
            else:
                min_val = self.handles[idx - 1] if idx > 0 else 0.0
                max_val = self.handles[idx + 1] if idx < 4 else 1.0
                val = max(min_val, min(max_val, val))
                self.handles[idx] = val

            self.valuesChanged.emit(self.handles)
            self.update()
        else:
            hover_idx = -1
            if pos.y() >= self.hist_height - 10:
                for i, h_val in enumerate(self.handles):
                    cx = self.margin_side + h_val * track_width
                    if abs(pos.x() - cx) < self.base_handle_width:
                        hover_idx = i
                        break
            if hover_idx != self.hover_index:
                self.hover_index = hover_idx
                self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        was_dragging = self.drag_index != -1
        self.drag_index = -1
        self._drag_start_handles = None
        self.update()
        if was_dragging:
            self.interactionFinished.emit()


# ---------------------------------------------------------------------------
# EditLevelsSection – wrapper exposed to the sidebar
# ---------------------------------------------------------------------------

class EditLevelsSection(QWidget):
    """Expose the levels adjustment controls as a section in the edit sidebar."""

    levelsParamsPreviewed = Signal(object)
    """Emitted while the user drags a handle so the viewer can update live."""

    levelsParamsCommitted = Signal(object)
    """Emitted once the interaction ends and the session should persist the change."""

    interactionStarted = Signal()
    interactionFinished = Signal()

    EDGE_INSET = 8

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._session: Optional[EditSession] = None
        self._updating_ui = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(self.EDGE_INSET, 0, self.EDGE_INSET, 0)
        layout.setSpacing(8)

        self.levels_comp = LevelsComposite(self)
        layout.addWidget(self.levels_comp)

        # Wire internal signals
        self.levels_comp.valuesChanged.connect(self._on_levels_changed)
        self.levels_comp.interactionStarted.connect(self.interactionStarted)
        self.levels_comp.interactionFinished.connect(self._on_levels_interaction_finished)

    # ------------------------------------------------------------------
    # Session binding
    # ------------------------------------------------------------------

    def bind_session(self, session: Optional[EditSession]) -> None:
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
        if self._session is None:
            self._reset_to_defaults()
            return

        self._updating_ui = True
        try:
            raw = self._session.value("Levels_Handles")
            if isinstance(raw, list) and len(raw) == 5:
                self.levels_comp.handles = [float(v) for v in raw]
            else:
                self.levels_comp.handles = list(DEFAULT_LEVELS_HANDLES)
            self.levels_comp.update()
        finally:
            self._updating_ui = False

    def _reset_to_defaults(self) -> None:
        self._updating_ui = True
        try:
            self.levels_comp.handles = list(DEFAULT_LEVELS_HANDLES)
            self.levels_comp.update()
        finally:
            self._updating_ui = False

    def set_preview_image(self, image) -> None:
        """Forward histogram data to the levels composite."""
        if image is None:
            self.levels_comp.set_histogram(None)
            return
        histogram = self._compute_histogram(image)
        self.levels_comp.set_histogram(histogram)

    def _compute_histogram(self, image) -> Optional[np.ndarray]:
        from PySide6.QtGui import QImage

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
        for ch in range(3):
            counts = np.bincount(rgb[:, ch], minlength=256).astype(np.float32)
            hist[ch] = counts

        max_val = float(hist.max())
        if max_val > 0.0:
            hist /= max_val
        return hist

    # ------------------------------------------------------------------
    # Signal handlers
    # ------------------------------------------------------------------

    def _on_levels_changed(self, handles: list) -> None:
        if self._updating_ui:
            return
        self._preview_levels_changes()

    def _on_levels_interaction_finished(self) -> None:
        if self._updating_ui:
            return
        self._commit_levels_changes()
        self.interactionFinished.emit()

    @Slot(str, object)
    def _on_session_value_changed(self, key: str, _value: object) -> None:
        if key.startswith("Levels_"):
            self.refresh_from_session()

    @Slot()
    def _on_session_reset(self) -> None:
        self.refresh_from_session()

    # ------------------------------------------------------------------
    # Preview / commit
    # ------------------------------------------------------------------

    def _gather_levels_params(self) -> dict:
        return {"Handles": list(self.levels_comp.handles)}

    def _preview_levels_changes(self) -> None:
        levels_data = self._gather_levels_params()
        self.levelsParamsPreviewed.emit(levels_data)

    def _commit_levels_changes(self) -> None:
        if self._session is None:
            return
        updates = {
            "Levels_Enabled": True,
            "Levels_Handles": list(self.levels_comp.handles),
        }
        self._session.set_values(updates)
        self.levelsParamsCommitted.emit(self._gather_levels_params())

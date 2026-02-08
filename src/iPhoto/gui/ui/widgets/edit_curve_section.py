"""Curve adjustment section for the edit sidebar."""

from __future__ import annotations

import logging
import math
from typing import Dict, List, Optional, Tuple

import numpy as np
from PySide6.QtCore import QPointF, Qt, Signal, Slot
from PySide6.QtGui import (
    QColor,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen, QImage,
)
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ....core.spline import MonotoneCubicSpline
from ....core.curve_resolver import DEFAULT_CURVE_POINTS
from ..models.edit_session import EditSession
from ..icon import load_icon

_LOGGER = logging.getLogger(__name__)


class _StyledComboBox(QComboBox):
    """Styled combo box matching the edit sidebar theme."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setStyleSheet("""
            QComboBox {
                background-color: #383838;
                color: white;
                border-radius: 4px;
                padding: 4px 10px;
                font-size: 13px;
                border: 1px solid #555;
            }
            QComboBox::drop-down {
                border: 0px;
                width: 25px;
            }
            QComboBox::down-arrow {
                image: none;
                border: none;
            }
            QComboBox QAbstractItemView {
                background-color: #383838;
                color: white;
                selection-background-color: #505050;
                border: 1px solid #555;
            }
        """)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        arrow_color = QColor("#4a90e2")
        rect = self.rect()
        cx = rect.width() - 15
        cy = rect.height() / 2
        size = 4
        p1 = QPointF(cx - size, cy - size / 2)
        p2 = QPointF(cx, cy + size / 2)
        p3 = QPointF(cx + size, cy - size / 2)
        pen = QPen(arrow_color, 2)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawLine(p1, p2)
        painter.drawLine(p2, p3)
        painter.end()


class InputLevelSliders(QWidget):
    """Interactive slider widget for setting black and white input level points."""

    blackPointChanged = Signal(float)
    whitePointChanged = Signal(float)

    def __init__(self, parent: Optional[QWidget] = None, size: int = 240) -> None:
        super().__init__(parent)
        self.setFixedSize(size, 24)
        self.setStyleSheet("background-color: #222222;")

        self._black_val = 0.0
        self._white_val = 1.0
        self._dragging: Optional[str] = None

        # Style constants
        self.handle_width = 12
        self.handle_height = 18
        self.hit_radius = 15
        self.limit_gap = 0.01
        self.inner_circle_radius = 3
        self.hit_padding_y = 5
        self.bezier_ctrl_y_factor = 0.4
        self.bezier_ctrl_x_factor = 0.5
        self.margin_side = 8

    def setBlackPoint(self, val: float) -> None:
        self._black_val = max(0.0, min(val, self._white_val - self.limit_gap))
        self.update()

    def setWhitePoint(self, val: float) -> None:
        self._white_val = max(self._black_val + self.limit_gap, min(val, 1.0))
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        # Background
        painter.fillRect(self.rect(), QColor("#222222"))

        # Draw handles
        track_width = max(1, w - 2 * self.margin_side)
        self._draw_handle(
            painter,
            self.margin_side + self._black_val * track_width,
            is_black=True,
        )
        self._draw_handle(
            painter,
            self.margin_side + self._white_val * track_width,
            is_black=False,
        )

    def _draw_handle(self, painter: QPainter, x_pos: float, is_black: bool) -> None:
        y_top = 0
        y_bottom = self.handle_height
        hw = self.handle_width / 2.0
        radius = hw
        cy = y_bottom - radius

        path = QPainterPath()
        path.moveTo(x_pos, y_top)

        # Teardrop shape
        path.cubicTo(
            x_pos + hw * self.bezier_ctrl_x_factor,
            y_top + self.handle_height * self.bezier_ctrl_y_factor,
            x_pos + hw,
            cy - radius * self.bezier_ctrl_x_factor,
            x_pos + hw,
            cy,
        )
        path.arcTo(x_pos - radius, cy - radius, 2 * radius, 2 * radius, 0, -180)
        path.cubicTo(
            x_pos - hw,
            cy - radius * self.bezier_ctrl_x_factor,
            x_pos - hw * self.bezier_ctrl_x_factor,
            y_top + self.handle_height * self.bezier_ctrl_y_factor,
            x_pos,
            y_top,
        )

        # Fill
        painter.setBrush(QColor("#BBBBBB"))
        painter.setPen(Qt.NoPen)
        painter.drawPath(path)

        # Inner circle
        inner_color = QColor("black") if is_black else QColor("white")
        painter.setBrush(inner_color)
        painter.drawEllipse(QPointF(x_pos, cy), self.inner_circle_radius, self.inner_circle_radius)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        pos = event.position()
        w = self.width()
        track_width = w - 2 * self.margin_side
        if track_width <= 0:
            return

        bx = self.margin_side + self._black_val * track_width
        wx = self.margin_side + self._white_val * track_width

        dist_b = abs(pos.x() - bx)
        dist_w = abs(pos.x() - wx)

        if pos.y() <= self.handle_height + self.hit_padding_y:
            if dist_b < self.hit_radius and dist_b <= dist_w:
                self._dragging = "black"
            elif dist_w < self.hit_radius:
                self._dragging = "white"

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self._dragging:
            return

        w = self.width()
        track_width = w - 2 * self.margin_side
        if track_width <= 0:
            return

        val = (event.position().x() - self.margin_side) / track_width
        val = max(0.0, min(1.0, val))

        if self._dragging == "black":
            limit = self._white_val - self.limit_gap
            if val > limit:
                val = limit
            self._black_val = val
            self.blackPointChanged.emit(val)
        elif self._dragging == "white":
            limit = self._black_val + self.limit_gap
            if val < limit:
                val = limit
            self._white_val = val
            self.whitePointChanged.emit(val)

        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._dragging = None


class CurveGraph(QWidget):
    """Interactive curve graph widget for editing tone curves."""

    curveChanged = Signal()
    startPointMoved = Signal(float)
    endPointMoved = Signal(float)
    interactionStarted = Signal()
    interactionFinished = Signal()

    HIT_DETECTION_RADIUS = 15
    MIN_DISTANCE_THRESHOLD = 0.01

    def __init__(self, parent: Optional[QWidget] = None, size: int = 240) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setStyleSheet("background-color: #2b2b2b;")
        self.edge_padding = 8

        # Independent data models for each channel
        self.active_channel = "RGB"
        self.channels: Dict[str, List[QPointF]] = {
            "RGB": [QPointF(0.0, 0.0), QPointF(1.0, 1.0)],
            "Red": [QPointF(0.0, 0.0), QPointF(1.0, 1.0)],
            "Green": [QPointF(0.0, 0.0), QPointF(1.0, 1.0)],
            "Blue": [QPointF(0.0, 0.0), QPointF(1.0, 1.0)],
        }
        self.splines: Dict[str, MonotoneCubicSpline] = {}

        self.selected_index = -1
        self.dragging = False
        self.histogram_data: Optional[np.ndarray] = None

        self._recalculate_splines_all()

    def set_channel(self, channel_name: str) -> None:
        if channel_name in self.channels and channel_name != self.active_channel:
            self.active_channel = channel_name
            self.selected_index = -1
            points = self.channels[self.active_channel]
            if points:
                self.startPointMoved.emit(points[0].x())
                self.endPointMoved.emit(points[-1].x())
            self.update()

    def set_histogram(self, hist_data: Optional[np.ndarray]) -> None:
        self.histogram_data = hist_data
        self.update()

    def get_curve_data(self) -> Dict[str, List[Tuple[float, float]]]:
        """Return all curve channel data as lists of (x, y) tuples."""
        result = {}
        for name, points in self.channels.items():
            result[name] = [(p.x(), p.y()) for p in points]
        return result

    def set_curve_data(self, data: Dict[str, List[Tuple[float, float]]]) -> None:
        """Set curve data from lists of (x, y) tuples."""
        for name, points in data.items():
            if name in self.channels:
                valid_points: List[QPointF] = []
                for idx, point in enumerate(points):
                    try:
                        x, y = point  # type: ignore[misc]
                    except (TypeError, ValueError):
                        _LOGGER.warning(
                            "Invalid curve point for channel '%s' at index %d: %r "
                            "- expected iterable of two values; skipping",
                            name,
                            idx,
                            point,
                        )
                        continue
                    if not (isinstance(x, (int, float)) and isinstance(y, (int, float))):
                        _LOGGER.warning(
                            "Non-numeric curve point for channel '%s' at index %d: %r "
                            "- expected numeric x and y; skipping",
                            name,
                            idx,
                            point,
                        )
                        continue
                    try:
                        valid_points.append(QPointF(float(x), float(y)))
                    except (TypeError, ValueError):
                        _LOGGER.warning(
                            "Failed to create QPointF for channel '%s' at index %d "
                            "from values (%r, %r); skipping",
                            name,
                            idx,
                            x,
                            y,
                        )
                        continue
                if valid_points:
                    self.channels[name] = valid_points
        self._recalculate_splines_all()
        self.update()

    def reset_curves(self) -> None:
        """Reset all curves to identity."""
        for name in self.channels:
            self.channels[name] = [QPointF(0.0, 0.0), QPointF(1.0, 1.0)]
        self._recalculate_splines_all()
        self.curveChanged.emit()
        self.update()

    def _recalculate_splines_all(self) -> None:
        for name in self.channels:
            self._recalculate_spline(name)

    def _recalculate_spline(self, channel_name: str) -> None:
        points = self.channels[channel_name]
        points.sort(key=lambda p: p.x())
        x = [p.x() for p in points]
        y = [p.y() for p in points]
        try:
            self.splines[channel_name] = MonotoneCubicSpline(x, y)
        except ValueError:
            # Ignore invalid point configurations that cannot form a monotone spline,
            # but log for debugging purposes.
            _LOGGER.debug(
                "Failed to recalculate spline for channel %s with points %s",
                channel_name,
                points,
            )

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        inner_w = w - 2 * self.edge_padding
        inner_h = h - 2 * self.edge_padding

        painter.fillRect(self.rect(), QColor("#222222"))

        if inner_w <= 0 or inner_h <= 0:
            return

        painter.save()
        painter.translate(self.edge_padding, self.edge_padding)
        w, h = inner_w, inner_h

        # Grid
        painter.setPen(QPen(QColor("#444444"), 1))
        for i in range(1, 4):
            painter.drawLine(i * w // 4, 0, i * w // 4, h)
            painter.drawLine(0, i * h // 4, w, i * h // 4)

        # Histogram
        if self.histogram_data is not None:
            self._draw_histogram(painter, w, h)
        else:
            self._draw_fake_histogram(painter, w, h)

        # Curve
        self._draw_curve(painter, w, h)

        # Control points
        point_radius = 5
        current_points = self.channels[self.active_channel]

        if self.active_channel == "Red":
            pt_color = QColor("#FF4444")
        elif self.active_channel == "Green":
            pt_color = QColor("#44FF44")
        elif self.active_channel == "Blue":
            pt_color = QColor("#4444FF")
        else:
            pt_color = QColor("white")

        for i, p in enumerate(current_points):
            sx = p.x() * w
            sy = h - (p.y() * h)

            if i == self.selected_index:
                painter.setBrush(Qt.NoBrush)
                painter.setPen(QPen(pt_color, 2))
                painter.drawEllipse(QPointF(sx, sy), point_radius + 2, point_radius + 2)

            painter.setBrush(pt_color)
            painter.setPen(QPen(QColor("#000000"), 1))
            painter.drawEllipse(QPointF(sx, sy), point_radius, point_radius)

        painter.restore()

    def _draw_fake_histogram(self, painter: QPainter, w: int, h: int) -> None:
        if self.active_channel != "RGB":
            return

        path = QPainterPath()
        path.moveTo(0, h)
        step = 4
        for x in range(0, w + step, step):
            nx = x / w
            val = math.exp(-((nx - 0.35) ** 2) / 0.04) * 0.6 + math.exp(-((nx - 0.75) ** 2) / 0.08) * 0.4
            noise = math.sin(x * 0.1) * 0.05
            h_val = (val + abs(noise)) * h * 0.8
            path.lineTo(x, h - h_val)
        path.lineTo(w, h)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(120, 120, 120, 60))
        painter.drawPath(path)

    def _draw_histogram(self, painter: QPainter, w: int, h: int) -> None:
        if self.histogram_data is None:
            return

        is_gray = len(self.histogram_data.shape) == 1

        if is_gray:
            self._draw_hist_channel(painter, self.histogram_data, QColor(120, 120, 120, 128), w, h)
            return

        if self.active_channel == "RGB":
            self._draw_hist_channel(painter, self.histogram_data[0], QColor(255, 0, 0, 100), w, h)
            self._draw_hist_channel(painter, self.histogram_data[1], QColor(0, 255, 0, 100), w, h)
            self._draw_hist_channel(painter, self.histogram_data[2], QColor(0, 0, 255, 100), w, h)
        elif self.active_channel == "Red":
            self._draw_hist_channel(painter, self.histogram_data[0], QColor(255, 0, 0, 150), w, h)
        elif self.active_channel == "Green":
            self._draw_hist_channel(painter, self.histogram_data[1], QColor(0, 255, 0, 150), w, h)
        elif self.active_channel == "Blue":
            self._draw_hist_channel(painter, self.histogram_data[2], QColor(0, 0, 255, 150), w, h)

    def _draw_hist_channel(self, painter: QPainter, data: np.ndarray, color: QColor, w: int, h: int) -> None:
        path = QPainterPath()
        path.moveTo(0, h)
        bin_width = w / 256.0
        for i, val in enumerate(data):
            x = i * bin_width
            y = h - (val * h * 0.9)
            if i == 0:
                path.lineTo(x, y)
            path.lineTo((i + 0.5) * bin_width, y)
        path.lineTo(w, h)
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawPath(path)

    def _draw_curve(self, painter: QPainter, w: int, h: int) -> None:
        spline = self.splines.get(self.active_channel)
        if spline is None:
            return

        points = self.channels[self.active_channel]
        start_pt = points[0]
        end_pt = points[-1]

        color_map = {
            "RGB": QColor("#FFFFFF"),
            "Red": QColor("#FF4444"),
            "Green": QColor("#44FF44"),
            "Blue": QColor("#4444FF"),
        }
        pen_color = color_map.get(self.active_channel, QColor("white"))

        steps = max(w // 2, 100)
        xs = np.linspace(0, 1, steps)
        ys = spline.evaluate(xs)

        path = QPainterPath()
        first_pt = True

        for i in range(len(xs)):
            x_val = xs[i]

            if x_val < start_pt.x():
                y_val = start_pt.y()
            elif x_val > end_pt.x():
                y_val = end_pt.y()
            else:
                y_val = ys[i]

            cx = x_val * w
            cy = h - y_val * h

            if first_pt:
                path.moveTo(cx, cy)
                first_pt = False
            else:
                path.lineTo(cx, cy)

        painter.setPen(QPen(pen_color, 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.interactionStarted.emit()
        pos = event.position()
        w, h = self.width() - 2 * self.edge_padding, self.height() - 2 * self.edge_padding
        if w <= 0 or h <= 0:
            return
        local_x = pos.x() - self.edge_padding
        local_y = pos.y() - self.edge_padding

        points = self.channels[self.active_channel]

        hit_radius_sq = self.HIT_DETECTION_RADIUS**2
        click_idx = -1
        min_dist_sq = float("inf")

        for i, p in enumerate(points):
            sx, sy = p.x() * w, h - p.y() * h
            dist_sq = (local_x - sx) ** 2 + (local_y - sy) ** 2
            if dist_sq < hit_radius_sq:
                if dist_sq < min_dist_sq:
                    min_dist_sq = dist_sq
                    click_idx = i

        if click_idx != -1:
            self.selected_index = click_idx
            if event.button() == Qt.RightButton and 0 < click_idx < len(points) - 1:
                points.pop(click_idx)
                self.selected_index = -1
                self._update_spline_and_emit()
        else:
            # Add point
            nx = max(0.0, min(1.0, local_x / w))
            insert_i = len(points)
            for i, p in enumerate(points):
                if p.x() > nx:
                    insert_i = i
                    break

            ny = max(0.0, min(1.0, (h - local_y) / h))

            prev_x = points[insert_i - 1].x() if insert_i > 0 else 0
            next_x = points[insert_i].x() if insert_i < len(points) else 1

            if nx > prev_x + self.MIN_DISTANCE_THRESHOLD and nx < next_x - self.MIN_DISTANCE_THRESHOLD:
                points.insert(insert_i, QPointF(nx, ny))
                self.selected_index = insert_i
                self._update_spline_and_emit()

        self.dragging = True

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.dragging and self.selected_index != -1:
            points = self.channels[self.active_channel]
            pos = event.position()
            w, h = self.width() - 2 * self.edge_padding, self.height() - 2 * self.edge_padding
            if w <= 0 or h <= 0:
                return
            local_x = pos.x() - self.edge_padding
            local_y = pos.y() - self.edge_padding

            nx = max(0.0, min(1.0, local_x / w))
            ny = max(0.0, min(1.0, (h - local_y) / h))

            if self.selected_index == 0:
                if len(points) > 1:
                    max_x = points[1].x() - self.MIN_DISTANCE_THRESHOLD
                    if nx > max_x:
                        nx = max_x
                nx = max(0.0, nx)
            elif self.selected_index == len(points) - 1:
                if len(points) > 1:
                    min_x = points[self.selected_index - 1].x() + self.MIN_DISTANCE_THRESHOLD
                    if nx < min_x:
                        nx = min_x
                nx = min(1.0, nx)
            else:
                prev_p = points[self.selected_index - 1]
                next_p = points[self.selected_index + 1]
                min_x = prev_p.x() + self.MIN_DISTANCE_THRESHOLD
                max_x = next_p.x() - self.MIN_DISTANCE_THRESHOLD

                if nx < min_x:
                    nx = min_x
                if nx > max_x:
                    nx = max_x

            points[self.selected_index] = QPointF(nx, ny)
            self._update_spline_and_emit()

            if self.selected_index == 0:
                self.startPointMoved.emit(nx)
            elif self.selected_index == len(points) - 1:
                self.endPointMoved.emit(nx)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self.dragging = False
        self.interactionFinished.emit()

    def add_point_smart(self) -> None:
        """Add a point at the largest gap in the curve."""
        points = self.channels[self.active_channel]
        if len(points) < 2:
            return

        max_gap = -1.0
        max_gap_idx = -1

        for i in range(len(points) - 1):
            gap = points[i + 1].x() - points[i].x()
            if gap > max_gap:
                max_gap = gap
                max_gap_idx = i

        if max_gap_idx != -1:
            p0 = points[max_gap_idx]
            p1 = points[max_gap_idx + 1]
            mid_x = p0.x() + (p1.x() - p0.x()) * 0.5

            spline = self.splines.get(self.active_channel)
            if spline:
                mid_y = float(spline.evaluate(mid_x))
            else:
                mid_y = p0.y() + (p1.y() - p0.y()) * 0.5
            mid_y = max(0.0, min(1.0, mid_y))

            new_point = QPointF(mid_x, mid_y)
            insert_at = max_gap_idx + 1
            points.insert(insert_at, new_point)

            self.selected_index = insert_at
            self._update_spline_and_emit()

    def _update_spline_and_emit(self) -> None:
        self._recalculate_spline(self.active_channel)
        self.curveChanged.emit()
        self.update()


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

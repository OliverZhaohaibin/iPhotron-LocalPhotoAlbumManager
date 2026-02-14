"""Interactive curve graph widget for editing tone curves."""

from __future__ import annotations

import logging
import math
from typing import Dict, List, Optional, Tuple

import numpy as np
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from ....core.spline import MonotoneCubicSpline

_LOGGER = logging.getLogger(__name__)
_HANDLE_EDGE_PADDING = 8


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
        self.edge_padding = _HANDLE_EDGE_PADDING

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
        pos = event.position()
        w, h = self.width() - 2 * self.edge_padding, self.height() - 2 * self.edge_padding
        if w <= 0 or h <= 0:
            return
        self.interactionStarted.emit()
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

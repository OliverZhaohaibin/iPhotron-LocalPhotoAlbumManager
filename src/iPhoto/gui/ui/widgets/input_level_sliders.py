"""Interactive slider widget for setting black and white input level points."""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPainterPath
from PySide6.QtWidgets import QWidget

_LOGGER = logging.getLogger(__name__)
_HANDLE_EDGE_PADDING = 8


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
        self.margin_side = _HANDLE_EDGE_PADDING

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
        track_width = w - 2 * self.margin_side
        if track_width <= 0:
            return
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

"""Custom slider widgets for the White Balance section.

Ported from the standalone demo (``demo/white balance/white balance.py``).
Contains gradient-background sliders with tick marks, a mode-selection
combo-box, and an eyedropper (pipette) button.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QComboBox,
    QPushButton,
    QWidget,
)

from ..icon import load_icon


class _StyledComboBox(QComboBox):
    """Dark-themed combo-box matching the demo appearance."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(
            """
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
                outline: 0px;
            }
            """
        )

    def paintEvent(self, event):  # type: ignore[override]
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        arrow_color = QColor("#4a90e2")
        rect = self.rect()
        cx = rect.width() - 15
        cy = rect.height() / 2.0
        size = 4

        p1 = QPointF(cx - size, cy - 2)
        p2 = QPointF(cx, cy - 6)
        p3 = QPointF(cx + size, cy - 2)

        p4 = QPointF(cx - size, cy + 2)
        p5 = QPointF(cx, cy + 6)
        p6 = QPointF(cx + size, cy + 2)

        pen = QPen(arrow_color, 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawPolyline([p1, p2, p3])
        painter.drawPolyline([p4, p5, p6])
        painter.end()


class _PipetteButton(QPushButton):
    """Eyedropper toggle button matching the demo appearance."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setIcon(load_icon("eyedropper.svg"))
        self.setIconSize(QSize(22, 22))
        self.setFixedSize(36, 32)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setCheckable(True)
        self.setStyleSheet(
            """
            QPushButton {
                background-color: #383838;
                border: 1px solid #555;
                border-radius: 6px;
                color: #ddd;
            }
            QPushButton:hover { background-color: #444; }
            QPushButton:checked { background-color: #4a90e2; border-color: #4a90e2; }
            """
        )


# ── Custom gradient sliders ──────────────────────────────────────────


class _WarmthSlider(QWidget):
    """Gradient blue→orange slider with tick marks and fill highlight."""

    valueChanged = Signal(float)
    interactionStarted = Signal()
    interactionFinished = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._min = -100.0
        self._max = 100.0
        self._value = 0.0
        self._dragging = False
        self.setFixedHeight(34)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

        self.c_blue_track = QColor(44, 62, 74)
        self.c_orange_track = QColor(74, 62, 32)
        self.c_indicator = QColor(255, 255, 255)
        self.c_tick = QColor(255, 255, 255, 60)
        self.c_fill_blue = QColor(74, 144, 180)
        self.c_fill_warm = QColor(180, 150, 60)

    # -- public API --
    def value(self) -> float:
        return self._value

    def setValue(self, v: float) -> None:
        self._value = max(self._min, min(self._max, float(v)))
        self.update()

    def normalizedValue(self) -> float:
        """Return current value mapped to ``[-1, 1]``."""
        return self._value / 100.0

    # -- painting --
    def paintEvent(self, _):  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()

        grad = QLinearGradient(rect.left(), 0, rect.right(), 0)
        grad.setColorAt(0, self.c_blue_track)
        grad.setColorAt(1, self.c_orange_track)
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), 4, 4)
        painter.fillPath(path, grad)

        zero_x = self._value_to_x(0)
        curr_x = self._value_to_x(self._value)
        fill_color = self.c_fill_blue if self._value < 0 else self.c_fill_warm
        fill_rect = QRectF(min(zero_x, curr_x), 0, abs(curr_x - zero_x), self.height())
        painter.setOpacity(0.8)
        painter.fillRect(fill_rect, fill_color)
        painter.setOpacity(1.0)

        painter.setPen(QPen(self.c_tick, 1))
        ticks = 50
        for i in range(ticks):
            x = (i / ticks) * rect.width()
            h = 6 if i % 5 == 0 else 3
            painter.drawLine(QPointF(x, 0), QPointF(x, h))

        painter.setPen(QPen(QColor(255, 255, 255, 100), 1))
        painter.drawLine(QPointF(zero_x, 0), QPointF(zero_x, rect.bottom()))

        font = QFont("Inter", 12, QFont.Weight.Medium)
        painter.setFont(font)
        painter.setPen(QColor(240, 240, 240))
        painter.drawText(QRectF(rect).adjusted(12, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, "Warmth")
        painter.setPen(QColor(255, 255, 255, 160))
        painter.drawText(QRectF(rect).adjusted(0, 0, -12, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, str(int(self._value)))

        handle_x = self._norm() * rect.width()
        painter.setPen(QPen(self.c_indicator, 2))
        painter.drawLine(QPointF(handle_x, 0), QPointF(handle_x, rect.bottom()))

    # -- interaction --
    def mousePressEvent(self, event):  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.interactionStarted.emit()
            self._update_from_pos(event.position().x())

    def mouseMoveEvent(self, event):  # type: ignore[override]
        if self._dragging:
            self._update_from_pos(event.position().x())

    def mouseReleaseEvent(self, event):  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.valueChanged.emit(self._value)
            self.interactionFinished.emit()

    # -- helpers --
    def _norm(self) -> float:
        return (self._value - self._min) / (self._max - self._min)

    def _value_to_x(self, val: float) -> float:
        return ((val - self._min) / (self._max - self._min)) * self.width()

    def _update_from_pos(self, x: float) -> None:
        ratio = max(0.0, min(1.0, x / self.width()))
        self._value = self._min + ratio * (self._max - self._min)
        self.valueChanged.emit(self._value)
        self.update()


class _TemperatureSlider(QWidget):
    """Gradient blue→amber Kelvin slider with tick marks and gradient fill."""

    valueChanged = Signal(float)
    interactionStarted = Signal()
    interactionFinished = Signal()

    KELVIN_MIN = 2000.0
    KELVIN_MAX = 10000.0
    KELVIN_DEFAULT = 6500.0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._value = self.KELVIN_DEFAULT
        self._dragging = False
        self.setFixedHeight(34)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

        self.c_blue = QColor(44, 62, 74)
        self.c_orange = QColor(94, 72, 32)
        self.c_indicator = QColor(255, 255, 255)
        self.c_tick = QColor(255, 255, 255, 40)
        self.c_fill_blue = QColor(74, 144, 180)
        self.c_fill_orange = QColor(220, 160, 50)

    # -- public API --
    def value(self) -> float:
        return self._value

    def setValue(self, v: float) -> None:
        self._value = max(self.KELVIN_MIN, min(self.KELVIN_MAX, float(v)))
        self.update()

    def normalizedValue(self) -> float:
        """Return ``[-1, 1]`` relative to the centre of the Kelvin range."""
        half = (self.KELVIN_MAX - self.KELVIN_MIN) / 2.0
        centre = (self.KELVIN_MAX + self.KELVIN_MIN) / 2.0
        return (self._value - centre) / half

    # -- painting --
    def paintEvent(self, _):  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()

        grad = QLinearGradient(rect.left(), 0, rect.right(), 0)
        grad.setColorAt(0, self.c_blue)
        grad.setColorAt(1, self.c_orange)
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), 4, 4)
        painter.fillPath(path, grad)

        ratio = self._norm()
        curr_x = ratio * rect.width()
        fill_rect = QRectF(0, 0, curr_x, self.height())
        fill_grad = QLinearGradient(0, 0, curr_x, 0)
        fill_grad.setColorAt(0, self.c_fill_blue)
        fill_grad.setColorAt(1, self._interpolate_color(ratio))
        painter.setOpacity(0.75)
        painter.fillRect(fill_rect, fill_grad)
        painter.setOpacity(1.0)

        painter.setPen(QPen(self.c_tick, 1))
        for i in range(51):
            x = (i / 50) * rect.width()
            h = 6 if i % 5 == 0 else 3
            painter.drawLine(QPointF(x, 0), QPointF(x, h))

        font = QFont("Inter", 12, QFont.Weight.Medium)
        painter.setFont(font)
        painter.setPen(QColor(220, 220, 220))
        painter.drawText(QRectF(rect).adjusted(12, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, "Temperature")
        painter.setPen(QColor(200, 200, 200, 180))
        painter.drawText(QRectF(rect).adjusted(0, 0, -12, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, f"{int(self._value):,}")

        painter.setPen(QPen(self.c_indicator, 2))
        painter.drawLine(QPointF(curr_x, 0), QPointF(curr_x, rect.bottom()))

    # -- interaction --
    def mousePressEvent(self, event):  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.interactionStarted.emit()
            self._update_from_pos(event.position().x())

    def mouseMoveEvent(self, event):  # type: ignore[override]
        if self._dragging:
            self._update_from_pos(event.position().x())

    def mouseReleaseEvent(self, event):  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.valueChanged.emit(self._value)
            self.interactionFinished.emit()

    # -- helpers --
    def _norm(self) -> float:
        return (self._value - self.KELVIN_MIN) / (self.KELVIN_MAX - self.KELVIN_MIN)

    def _interpolate_color(self, ratio: float) -> QColor:
        r = int(self.c_fill_blue.red() + (self.c_fill_orange.red() - self.c_fill_blue.red()) * ratio)
        g = int(self.c_fill_blue.green() + (self.c_fill_orange.green() - self.c_fill_blue.green()) * ratio)
        b = int(self.c_fill_blue.blue() + (self.c_fill_orange.blue() - self.c_fill_blue.blue()) * ratio)
        return QColor(r, g, b)

    def _update_from_pos(self, x: float) -> None:
        ratio = max(0.0, min(1.0, x / self.width()))
        self._value = self.KELVIN_MIN + ratio * (self.KELVIN_MAX - self.KELVIN_MIN)
        self.valueChanged.emit(self._value)
        self.update()


class _TintSlider(QWidget):
    """Gradient green→magenta slider with tick marks and centre-fill."""

    valueChanged = Signal(float)
    interactionStarted = Signal()
    interactionFinished = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._min = -100.0
        self._max = 100.0
        self._value = 0.0
        self._dragging = False
        self.setFixedHeight(34)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

        self.c_green = QColor(44, 74, 54)
        self.c_magenta = QColor(84, 44, 84)
        self.c_indicator = QColor(255, 255, 255)
        self.c_tick = QColor(255, 255, 255, 40)
        self.c_fill_green = QColor(80, 180, 80)
        self.c_fill_magenta = QColor(200, 80, 180)

    # -- public API --
    def value(self) -> float:
        return self._value

    def setValue(self, v: float) -> None:
        self._value = max(self._min, min(self._max, float(v)))
        self.update()

    def normalizedValue(self) -> float:
        """Return current value mapped to ``[-1, 1]``."""
        return self._value / 100.0

    # -- painting --
    def paintEvent(self, _):  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()

        grad = QLinearGradient(rect.left(), 0, rect.right(), 0)
        grad.setColorAt(0, self.c_green)
        grad.setColorAt(1, self.c_magenta)
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), 4, 4)
        painter.fillPath(path, grad)

        zero_x = self._value_to_x(0)
        curr_x = self._value_to_x(self._value)
        fill_color = self.c_fill_green if self._value < 0 else self.c_fill_magenta
        fill_rect = QRectF(min(zero_x, curr_x), 0, abs(curr_x - zero_x), self.height())
        painter.setOpacity(0.8)
        painter.fillRect(fill_rect, fill_color)
        painter.setOpacity(1.0)

        painter.setPen(QPen(self.c_tick, 1))
        for i in range(51):
            x = (i / 50) * rect.width()
            h = 6 if i % 5 == 0 else 3
            painter.drawLine(QPointF(x, 0), QPointF(x, h))

        painter.setPen(QPen(QColor(255, 255, 255, 100), 1))
        painter.drawLine(QPointF(zero_x, 0), QPointF(zero_x, rect.bottom()))

        font = QFont("Inter", 12, QFont.Weight.Medium)
        painter.setFont(font)
        painter.setPen(QColor(220, 220, 220))
        painter.drawText(QRectF(rect).adjusted(12, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, "Tint")
        painter.setPen(QColor(200, 200, 200, 180))
        val_str = f"{self._value:.2f}"
        painter.drawText(QRectF(rect).adjusted(0, 0, -12, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, val_str)

        handle_x = self._norm() * rect.width()
        painter.setPen(QPen(self.c_indicator, 2))
        painter.drawLine(QPointF(handle_x, 0), QPointF(handle_x, rect.bottom()))

    # -- interaction --
    def mousePressEvent(self, event):  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.interactionStarted.emit()
            self._update_from_pos(event.position().x())

    def mouseMoveEvent(self, event):  # type: ignore[override]
        if self._dragging:
            self._update_from_pos(event.position().x())

    def mouseReleaseEvent(self, event):  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.valueChanged.emit(self._value)
            self.interactionFinished.emit()

    # -- helpers --
    def _norm(self) -> float:
        return (self._value - self._min) / (self._max - self._min)

    def _value_to_x(self, val: float) -> float:
        return ((val - self._min) / (self._max - self._min)) * self.width()

    def _update_from_pos(self, x: float) -> None:
        ratio = max(0.0, min(1.0, x / self.width()))
        self._value = self._min + ratio * (self._max - self._min)
        self.valueChanged.emit(self._value)
        self.update()


__all__ = [
    "_StyledComboBox",
    "_PipetteButton",
    "_WarmthSlider",
    "_TemperatureSlider",
    "_TintSlider",
]

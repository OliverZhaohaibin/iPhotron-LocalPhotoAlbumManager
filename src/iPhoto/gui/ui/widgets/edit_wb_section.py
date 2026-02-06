"""White Balance adjustment section for the edit sidebar.

Ports the custom slider widgets from the demo (``demo/white balance/white balance.py``)
into the edit panel, including gradient-background sliders with tick marks,
a mode-selection combo-box, and an eyedropper (pipette) button.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt, Signal, Slot
from PySide6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ....core.wb_resolver import WBParams
from ..models.edit_session import EditSession
from ..icon import icon_path

_LOGGER = logging.getLogger(__name__)

# =====================================================================
# Styled sub-widgets – ported from the standalone demo
# =====================================================================


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
        self._icon_path: str | None = None
        eyedropper = icon_path("eyedropper.svg")
        if eyedropper.exists():
            self._icon_path = str(eyedropper)
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

    def paintEvent(self, event):  # type: ignore[override]
        super().paintEvent(event)
        if self._icon_path and Path(self._icon_path).exists():
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            pixmap = QPixmap(self._icon_path)
            if not pixmap.isNull():
                scaled = pixmap.scaled(
                    24, 24, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
                )
                x = (self.width() - scaled.width()) // 2
                y = (self.height() - scaled.height()) // 2
                painter.drawPixmap(x, y, scaled)


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
        val_str = f"{self._value:+.2f}" if self._value != 0 else "0.00"
        painter.drawText(QRectF(rect).adjusted(0, 0, -12, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, val_str.replace("+", ""))

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


# =====================================================================
# Main section widget
# =====================================================================

# Mode identifiers matching the demo combo-box items.
_MODE_NEUTRAL = "Neutral Gray"
_MODE_SKIN = "Skin Tone"
_MODE_TEMP_TINT = "Temp & Tint"


class EditWBSection(QWidget):
    """White-balance section with demo-style custom gradient sliders,
    a mode combo-box, and an eyedropper button.
    """

    wbParamsPreviewed = Signal(WBParams)
    """Emitted while the user drags a control so the viewer can update live."""

    wbParamsCommitted = Signal(WBParams)
    """Emitted once the interaction ends and the session should persist the change."""

    eyedropperModeChanged = Signal(object)
    """Emitted when the eyedropper toggle is clicked (True/False/None)."""

    interactionStarted = Signal()
    interactionFinished = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._session: Optional[EditSession] = None
        self._updating_ui = False
        self._current_mode: str = _MODE_NEUTRAL

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(10)

        # Tool row: pipette + combo box
        tool_row = QHBoxLayout()
        tool_row.setSpacing(6)
        self._pipette = _PipetteButton(self)
        self._pipette.toggled.connect(self._on_eyedropper_toggled)
        tool_row.addWidget(self._pipette)

        self._combo = _StyledComboBox(self)
        self._combo.addItems([_MODE_NEUTRAL, _MODE_SKIN, _MODE_TEMP_TINT])
        self._combo.currentTextChanged.connect(self._on_mode_changed)
        tool_row.addWidget(self._combo, 1)
        layout.addLayout(tool_row)

        # Warmth slider (shown for Neutral Gray / Skin Tone)
        self._warmth_slider = _WarmthSlider(self)
        self._warmth_slider.valueChanged.connect(self._on_warmth_changed)
        self._warmth_slider.interactionStarted.connect(self.interactionStarted)
        self._warmth_slider.interactionFinished.connect(self._on_slider_committed)
        self._warmth_slider.interactionFinished.connect(self.interactionFinished)
        layout.addWidget(self._warmth_slider)

        # Temperature slider (shown for Temp & Tint)
        self._temp_slider = _TemperatureSlider(self)
        self._temp_slider.valueChanged.connect(self._on_temp_changed)
        self._temp_slider.interactionStarted.connect(self.interactionStarted)
        self._temp_slider.interactionFinished.connect(self._on_slider_committed)
        self._temp_slider.interactionFinished.connect(self.interactionFinished)
        self._temp_slider.setVisible(False)
        layout.addWidget(self._temp_slider)

        # Tint slider (shown for Temp & Tint)
        self._tint_slider = _TintSlider(self)
        self._tint_slider.valueChanged.connect(self._on_tint_changed)
        self._tint_slider.interactionStarted.connect(self.interactionStarted)
        self._tint_slider.interactionFinished.connect(self._on_slider_committed)
        self._tint_slider.interactionFinished.connect(self.interactionFinished)
        self._tint_slider.setVisible(False)
        layout.addWidget(self._tint_slider)

        # Opacity effect for disabled state
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity_effect)
        self._opacity_effect.setOpacity(1.0)

    # ------------------------------------------------------------------
    # Session binding
    # ------------------------------------------------------------------
    def bind_session(self, session: Optional[EditSession]) -> None:
        """Attach *session* so slider updates are persisted and reflected."""

        if self._session is session:
            return

        if self._session is not None:
            try:
                self._session.valueChanged.disconnect(self._on_session_value_changed)
            except (TypeError, RuntimeError):
                pass
            try:
                self._session.resetPerformed.disconnect(self._on_session_reset)
            except (TypeError, RuntimeError):
                pass

        self._session = session

        if session is not None:
            session.valueChanged.connect(self._on_session_value_changed)
            session.resetPerformed.connect(self._on_session_reset)
            self.refresh_from_session()
        else:
            self._reset_slider_values()
            self._apply_enabled_state(False)

    def refresh_from_session(self) -> None:
        """Synchronise slider positions with the active session state."""

        if self._session is None:
            self._reset_slider_values()
            self._apply_enabled_state(False)
            return

        enabled = bool(self._session.value("WB_Enabled"))
        self._updating_ui = True
        try:
            self._apply_enabled_state(enabled)
            warmth = float(self._session.value("WB_Warmth"))
            temperature = float(self._session.value("WB_Temperature"))
            tint = float(self._session.value("WB_Tint"))

            # Update sliders from session (session stores normalised values)
            self._warmth_slider.setValue(warmth * 100.0)
            self._temp_slider.setValue(
                _TemperatureSlider.KELVIN_DEFAULT
                + temperature
                * (((_TemperatureSlider.KELVIN_MAX - _TemperatureSlider.KELVIN_MIN) / 2.0))
            )
            self._tint_slider.setValue(tint * 100.0)
        finally:
            self._updating_ui = False

    def handle_color_picked(self, r: float, g: float, b: float) -> None:
        """Process an eyedropper color pick from the viewer.

        Called by the coordinator when the GL viewer reports a picked colour.
        """

        import numpy as np

        eps = 1e-6
        rgb = np.clip(np.array([r, g, b], dtype=np.float32), eps, 1.0)

        if self._current_mode == _MODE_TEMP_TINT:
            # Calculate temperature from R/B ratio
            temp_ratio = float(rgb[0]) / max(float(rgb[2]), eps)
            if temp_ratio > 1.0:
                temp_offset = -np.clip((temp_ratio - 1.0) * 0.5, 0, 1)
            else:
                temp_offset = float(np.clip((1.0 - temp_ratio) * 0.5, 0, 1))

            kelvin_centre = (_TemperatureSlider.KELVIN_MAX + _TemperatureSlider.KELVIN_MIN) / 2.0
            kelvin_half = (_TemperatureSlider.KELVIN_MAX - _TemperatureSlider.KELVIN_MIN) / 2.0
            kelvin_temp = kelvin_centre + float(temp_offset) * kelvin_half
            kelvin_temp = float(np.clip(kelvin_temp, _TemperatureSlider.KELVIN_MIN, _TemperatureSlider.KELVIN_MAX))

            avg_rb = (float(rgb[0]) + float(rgb[2])) / 2.0
            tint_ratio = float(rgb[1]) / max(avg_rb, eps)
            if tint_ratio > 1.0:
                tint_offset = float(np.clip((tint_ratio - 1.0) * 100.0, 0, 100))
            else:
                tint_offset = float(-np.clip((1.0 - tint_ratio) * 100.0, 0, 100))

            self._temp_slider.setValue(kelvin_temp)
            self._tint_slider.setValue(tint_offset)
        else:
            # Neutral Gray / Skin Tone → derive a warmth shift from R/B imbalance
            warmth_ratio = float(rgb[0]) / max(float(rgb[2]), eps)
            if warmth_ratio > 1.0:
                warmth_val = float(-np.clip((warmth_ratio - 1.0) * 50.0, 0, 100))
            else:
                warmth_val = float(np.clip((1.0 - warmth_ratio) * 50.0, 0, 100))
            self._warmth_slider.setValue(warmth_val)

        # Turn off eyedropper
        self._pipette.setChecked(False)

        # Emit committed params
        params = self._gather_params()
        self.wbParamsCommitted.emit(params)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _reset_slider_values(self) -> None:
        self._updating_ui = True
        try:
            self._warmth_slider.setValue(0)
            self._temp_slider.setValue(_TemperatureSlider.KELVIN_DEFAULT)
            self._tint_slider.setValue(0)
        finally:
            self._updating_ui = False

    def _apply_enabled_state(self, enabled: bool) -> None:
        self._opacity_effect.setOpacity(1.0 if enabled else 0.5)
        self._warmth_slider.setEnabled(enabled)
        self._temp_slider.setEnabled(enabled)
        self._tint_slider.setEnabled(enabled)
        self._combo.setEnabled(enabled)
        self._pipette.setEnabled(enabled)

    def _gather_params(self) -> WBParams:
        if self._current_mode == _MODE_TEMP_TINT:
            return WBParams(
                warmth=0.0,
                temperature=self._temp_slider.normalizedValue(),
                tint=self._tint_slider.normalizedValue(),
            )
        else:
            return WBParams(
                warmth=self._warmth_slider.normalizedValue(),
                temperature=0.0,
                tint=0.0,
            )

    # ------------------------------------------------------------------
    # Signal handlers
    # ------------------------------------------------------------------
    @Slot(str, object)
    def _on_session_value_changed(self, key: str, _value: object) -> None:
        if key == "WB_Enabled":
            self._apply_enabled_state(bool(self._session.value("WB_Enabled")))  # type: ignore[union-attr]
            return
        if key.startswith("WB_"):
            self.refresh_from_session()

    @Slot()
    def _on_session_reset(self) -> None:
        self.refresh_from_session()

    def _on_warmth_changed(self, _v: float) -> None:
        if self._updating_ui:
            return
        self.wbParamsPreviewed.emit(self._gather_params())

    def _on_temp_changed(self, _v: float) -> None:
        if self._updating_ui:
            return
        self.wbParamsPreviewed.emit(self._gather_params())

    def _on_tint_changed(self, _v: float) -> None:
        if self._updating_ui:
            return
        self.wbParamsPreviewed.emit(self._gather_params())

    def _on_slider_committed(self) -> None:
        """Emit committed params when any slider interaction finishes."""
        if self._updating_ui:
            return
        self.wbParamsCommitted.emit(self._gather_params())

    def _on_mode_changed(self, text: str) -> None:
        self._current_mode = text
        is_temp_tint = text == _MODE_TEMP_TINT
        self._warmth_slider.setVisible(not is_temp_tint)
        self._temp_slider.setVisible(is_temp_tint)
        self._tint_slider.setVisible(is_temp_tint)

        # Reset slider values when switching modes
        self._updating_ui = True
        try:
            if is_temp_tint:
                self._warmth_slider.setValue(0)
            else:
                self._temp_slider.setValue(_TemperatureSlider.KELVIN_DEFAULT)
                self._tint_slider.setValue(0)
        finally:
            self._updating_ui = False

        # Commit the new (reset) params for the new mode
        if self._session is not None and bool(self._session.value("WB_Enabled")):
            self.wbParamsCommitted.emit(self._gather_params())

    def _on_eyedropper_toggled(self, checked: bool) -> None:
        self.eyedropperModeChanged.emit(checked if checked else None)

    # Allow click-to-enable when WB is disabled
    def mousePressEvent(self, event):  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            if self._session is not None and not self._session.value("WB_Enabled"):
                self._session.set_value("WB_Enabled", True)
        super().mousePressEvent(event)


__all__ = ["EditWBSection"]

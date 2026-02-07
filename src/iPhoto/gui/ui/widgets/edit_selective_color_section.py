"""Selective Color adjustment section used inside the edit sidebar."""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

from PySide6.QtCore import Qt, QRectF, QPointF, Signal
from PySide6.QtGui import (
    QColor,
    QPainter,
    QPainterPath,
    QPen,
    QLinearGradient,
    QFont,
)
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QFrame,
    QButtonGroup,
)

from ..models.edit_session import EditSession
from ....core.selective_color_resolver import (
    DEFAULT_SELECTIVE_COLOR_RANGES,
    NUM_RANGES,
)

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helper widgets (adapted from demo, stripped of placeholders)
# ---------------------------------------------------------------------------


class _ColorSelectButton(QPushButton):
    """Small colour swatch button used to pick one of the six hue ranges."""

    def __init__(self, color_hex: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(26, 26)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.color = QColor(color_hex)
        self.setStyleSheet("background: transparent; border: none;")

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()

        if self.isChecked():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#444444"))
            painter.drawRoundedRect(rect, 4, 4)

        block_size = 12
        center_x = rect.width() / 2
        center_y = rect.height() / 2
        color_rect = QRectF(
            center_x - block_size / 2,
            center_y - block_size / 2,
            block_size,
            block_size,
        )

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.color)
        painter.drawRoundedRect(color_rect, 3, 3)


class _SelectiveSlider(QWidget):
    """Custom horizontal slider matching the demo's visual style."""

    valueChanged = Signal(float)

    def __init__(
        self,
        name: str,
        parent: Optional[QWidget] = None,
        minimum: float = -100.0,
        maximum: float = 100.0,
        initial: float = 0.0,
        bg_start: str = "#2c3e4a",
        bg_end: str = "#4a3e20",
        fill_neg: str = "#4a90b4",
        fill_pos: str = "#b4963c",
        fill_opacity: float = 0.9,
    ) -> None:
        super().__init__(parent)
        self._name = name
        self._min = float(minimum)
        self._max = float(maximum)
        self._value = float(initial)
        self._dragging = False
        self.setFixedHeight(30)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._fill_opacity = float(fill_opacity)
        self.set_colors(bg_start, bg_end, fill_neg, fill_pos)
        self.c_indicator = QColor(255, 255, 255)

    def set_colors(
        self,
        bg_start: str,
        bg_end: str,
        fill_neg: str,
        fill_pos: str,
        fill_opacity: float | None = None,
    ) -> None:
        self.c_bg_start = QColor(bg_start)
        self.c_bg_end = QColor(bg_end)
        self.c_fill_neg = QColor(fill_neg)
        self.c_fill_pos = QColor(fill_pos)
        if fill_opacity is not None:
            self._fill_opacity = float(fill_opacity)
        self.update()

    def _value_to_x(self, val: float) -> float:
        ratio = (val - self._min) / (self._max - self._min)
        return ratio * self.width()

    def paintEvent(self, _event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0, 0, self.width(), self.height())

        gradient = QLinearGradient(rect.left(), 0, rect.right(), 0)
        gradient.setColorAt(0, self.c_bg_start)
        gradient.setColorAt(1, self.c_bg_end)
        path = QPainterPath()
        path.addRoundedRect(rect, 4, 4)
        painter.fillPath(path, gradient)

        zero_x = self._value_to_x(0) if self._min < 0 else 0
        curr_x = self._value_to_x(self._value)

        current_fill_color = self.c_fill_neg if self._value < 0 else self.c_fill_pos

        if self._min < 0:
            fill_rect = QRectF(
                min(zero_x, curr_x), 0, abs(curr_x - zero_x), self.height()
            )
        else:
            fill_rect = QRectF(0, 0, curr_x, self.height())

        painter.setOpacity(self._fill_opacity)
        painter.setClipPath(path)
        painter.fillRect(fill_rect, current_fill_color)
        painter.setClipping(False)
        painter.setOpacity(1.0)

        if self._min < 0 < self._max:
            painter.setPen(QPen(QColor(255, 255, 255, 60), 1))
            painter.drawLine(QPointF(zero_x, 0), QPointF(zero_x, rect.bottom()))

        font = QFont("Segoe UI", 10)
        painter.setFont(font)
        painter.setPen(QColor(230, 230, 230))
        painter.drawText(
            rect.adjusted(10, 0, 0, 0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self._name,
        )
        painter.setPen(QColor(255, 255, 255, 200))
        painter.drawText(
            rect.adjusted(0, 0, -10, 0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
            f"{self._value:.2f}",
        )

        painter.setPen(QPen(self.c_indicator, 2))
        painter.drawLine(QPointF(curr_x, 0), QPointF(curr_x, rect.bottom()))

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self._update_from_pos(event.position().x())

    def mouseMoveEvent(self, event):  # noqa: N802
        if self._dragging:
            self._update_from_pos(event.position().x())

    def mouseReleaseEvent(self, event):  # noqa: N802
        self._dragging = False
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def _update_from_pos(self, x: float) -> None:
        ratio = max(0.0, min(1.0, x / max(1, self.width())))
        self._value = self._min + ratio * (self._max - self._min)
        self.valueChanged.emit(self._value)
        self.update()

    def setValue(self, v: float) -> None:  # noqa: N802
        clamped = float(max(self._min, min(self._max, v)))
        self._value = clamped
        self.update()

    def value(self) -> float:
        return float(self._value)


# ---------------------------------------------------------------------------
# Main section widget
# ---------------------------------------------------------------------------


class EditSelectiveColorSection(QWidget):
    """Container widget hosting the *Selective Color* adjustment controls."""

    selectiveColorParamsPreviewed = Signal(object)
    """Emitted while the user drags a slider so the viewer can update live."""

    selectiveColorParamsCommitted = Signal(object)
    """Emitted once the interaction ends and the session should persist the change."""

    interactionStarted = Signal()
    interactionFinished = Signal()

    # Colour hex codes matching the demo's six-range palette.
    COLOR_HEXES = ["#FF3B30", "#FFCC00", "#28CD41", "#5AC8FA", "#007AFF", "#AF52DE"]

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._session: Optional[EditSession] = None
        self._updating_ui = False

        # Per-colour stored UI values: [hue, sat, lum, range] for 6 ranges.
        # hue/sat/lum are in [-100, 100], range is in [0, 1].
        self._ui_store = np.zeros((NUM_RANGES, 4), dtype=np.float32)
        self._ui_store[:, 3] = 0.5  # default range

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(8)

        # --- Tools: colour buttons ---
        tools_layout = QHBoxLayout()
        tools_layout.setContentsMargins(0, 5, 0, 5)
        tools_layout.setSpacing(0)

        colors_bg = QFrame()
        colors_bg.setStyleSheet(
            "background-color: #222; border-radius: 6px; border: 1px solid #333;"
        )
        colors_bg.setFixedHeight(34)
        c_layout = QHBoxLayout(colors_bg)
        c_layout.setContentsMargins(4, 4, 4, 4)
        c_layout.setSpacing(4)

        self.btn_group = QButtonGroup(self)
        self.btn_group.setExclusive(True)
        self.btn_group.idClicked.connect(self._on_color_clicked)

        for i, c_hex in enumerate(self.COLOR_HEXES):
            btn = _ColorSelectButton(c_hex)
            self.btn_group.addButton(btn, i)
            c_layout.addWidget(btn)

        tools_layout.addStretch(1)
        tools_layout.addWidget(colors_bg, 0)
        tools_layout.addStretch(1)
        layout.addLayout(tools_layout)

        # --- Sliders ---
        self.slider_hue = _SelectiveSlider("Hue")
        self.slider_sat = _SelectiveSlider("Saturation")
        self.slider_lum = _SelectiveSlider("Luminance")
        self.slider_range = _SelectiveSlider(
            "Range",
            minimum=0,
            maximum=1.0,
            initial=0.5,
            bg_start="#353535",
            bg_end="#252525",
            fill_neg="#666",
            fill_pos="#808080",
        )

        layout.addWidget(self.slider_hue)
        layout.addWidget(self.slider_sat)
        layout.addWidget(self.slider_lum)
        layout.addWidget(self.slider_range)

        # Signal wiring
        self.slider_hue.valueChanged.connect(self._on_slider_changed)
        self.slider_sat.valueChanged.connect(self._on_slider_changed)
        self.slider_lum.valueChanged.connect(self._on_slider_changed)
        self.slider_range.valueChanged.connect(self._on_slider_changed)

        # Initialise first colour
        self.btn_group.button(0).setChecked(True)
        self._update_theme(0)

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

    def refresh_from_session(self) -> None:
        if self._session is None:
            return
        self._updating_ui = True
        try:
            ranges = self._session.value("SelectiveColor_Ranges")
            if isinstance(ranges, list) and len(ranges) == NUM_RANGES:
                for i, rng in enumerate(ranges):
                    if isinstance(rng, (list, tuple)) and len(rng) >= 5:
                        # Convert normalised values back to UI slider values
                        self._ui_store[i, 0] = float(rng[2]) * 100.0  # hue_shift
                        self._ui_store[i, 1] = float(rng[3]) * 100.0  # sat_adj
                        self._ui_store[i, 2] = float(rng[4]) * 100.0  # lum_adj
                        self._ui_store[i, 3] = float(rng[1])  # range_slider
            idx = self.btn_group.checkedId()
            if idx < 0:
                idx = 0
            self._load_sliders_for_color(idx)
        finally:
            self._updating_ui = False

    def set_preview_image(self, image, **_kwargs) -> None:
        """Accept but ignore a preview image (no thumbnails needed)."""

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _on_session_value_changed(self, key: str, _value: object) -> None:
        if key in ("SelectiveColor_Enabled", "SelectiveColor_Ranges"):
            self.refresh_from_session()

    def _on_session_reset(self) -> None:
        self._ui_store[:] = 0.0
        self._ui_store[:, 3] = 0.5
        self.refresh_from_session()

    def _on_color_clicked(self, idx: int) -> None:
        self._load_sliders_for_color(idx)
        self._update_theme(idx)

    def _load_sliders_for_color(self, idx: int) -> None:
        self._updating_ui = True
        try:
            h, s, l, r = self._ui_store[idx]
            self.slider_hue.setValue(h)
            self.slider_sat.setValue(s)
            self.slider_lum.setValue(l)
            self.slider_range.setValue(r)
        finally:
            self._updating_ui = False

    def _on_slider_changed(self, _value: float) -> None:
        if self._updating_ui:
            return
        idx = self.btn_group.checkedId()
        if idx < 0:
            idx = 0
        self._ui_store[idx, 0] = self.slider_hue.value()
        self._ui_store[idx, 1] = self.slider_sat.value()
        self._ui_store[idx, 2] = self.slider_lum.value()
        self._ui_store[idx, 3] = self.slider_range.value()

        ranges_data = self._build_ranges_data()

        self.interactionStarted.emit()
        self.selectiveColorParamsPreviewed.emit({"Ranges": ranges_data})
        self._commit_to_session(ranges_data)
        self.interactionFinished.emit()

    def _build_ranges_data(self):
        """Return the current ranges in the normalised format expected by the resolver."""
        ranges = []
        for i in range(NUM_RANGES):
            center = DEFAULT_SELECTIVE_COLOR_RANGES[i][0]
            range_slider = float(self._ui_store[i, 3])
            hue_shift = float(np.clip(self._ui_store[i, 0] / 100.0, -1.0, 1.0))
            sat_adj = float(np.clip(self._ui_store[i, 1] / 100.0, -1.0, 1.0))
            lum_adj = float(np.clip(self._ui_store[i, 2] / 100.0, -1.0, 1.0))
            ranges.append([center, range_slider, hue_shift, sat_adj, lum_adj])
        return ranges

    def _commit_to_session(self, ranges_data) -> None:
        if self._session is None:
            return
        self._session.set_values({
            "SelectiveColor_Enabled": True,
            "SelectiveColor_Ranges": ranges_data,
        })

    def _update_theme(self, color_idx: int) -> None:
        """Update slider gradient colours based on the active colour range."""

        def _mix_channel(channel: float, target: float, weight: float) -> float:
            return channel * (1.0 - weight) + target * weight

        def _mix_color(color: QColor, target: QColor, weight: float) -> QColor:
            return QColor.fromRgbF(
                _mix_channel(color.redF(), target.redF(), weight),
                _mix_channel(color.greenF(), target.greenF(), weight),
                _mix_channel(color.blueF(), target.blueF(), weight),
            )

        base_c = QColor(self.COLOR_HEXES[color_idx])
        muted_anchor = QColor("#2a2a2a")

        # Saturation slider
        sat_bg_start = "#3f3f3f"
        sat_bg_end = _mix_color(base_c, muted_anchor, 0.65).name()
        sat_fill_neg = "#58636b"
        sat_fill_pos = _mix_color(base_c, muted_anchor, 0.5).name()

        # Luminance slider
        lum_bg_start = QColor.fromRgbF(
            base_c.redF() * 0.18,
            base_c.greenF() * 0.18,
            base_c.blueF() * 0.18,
        ).name()
        lum_bg_end = _mix_color(base_c, muted_anchor, 0.45).name()
        lum_fill_neg = QColor.fromRgbF(
            base_c.redF() * 0.35,
            base_c.greenF() * 0.35,
            base_c.blueF() * 0.35,
        ).name()
        lum_fill_pos = _mix_color(base_c, muted_anchor, 0.38).name()

        # Hue slider – neighbours on the colour wheel
        n = len(self.COLOR_HEXES)
        left_hue = self.COLOR_HEXES[(color_idx - 1) % n]
        right_hue = self.COLOR_HEXES[(color_idx + 1) % n]
        c_left = QColor(left_hue)
        c_left.setAlpha(100)
        c_right = QColor(right_hue)
        c_right.setAlpha(100)

        self.slider_hue.set_colors(
            c_left.name(QColor.NameFormat.HexArgb),
            c_right.name(QColor.NameFormat.HexArgb),
            left_hue,
            right_hue,
        )
        self.slider_sat.set_colors(
            sat_bg_start, sat_bg_end, sat_fill_neg, sat_fill_pos, fill_opacity=0.55
        )
        self.slider_lum.set_colors(
            lum_bg_start, lum_bg_end, lum_fill_neg, lum_fill_pos, fill_opacity=0.55
        )

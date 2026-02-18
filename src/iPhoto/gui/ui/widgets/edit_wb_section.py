"""White Balance adjustment section for the edit sidebar.

Ports the custom slider widgets from the demo (``demo/white balance/white balance.py``)
into the edit panel, including gradient-background sliders with tick marks,
a mode-selection combo-box, and an eyedropper (pipette) button.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)

from ....core.wb_resolver import WBParams
from ..models.edit_session import EditSession
from .wb_sliders import (
    _PipetteButton,
    _StyledComboBox,
    _TemperatureSlider,
    _TintSlider,
    _WarmthSlider,
)

_LOGGER = logging.getLogger(__name__)

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
                # Signal may already be disconnected or the session object may be gone.
                _LOGGER.debug("Failed to disconnect 'valueChanged' signal", exc_info=True)
            try:
                self._session.resetPerformed.disconnect(self._on_session_reset)
            except (TypeError, RuntimeError):
                # Signal may already be disconnected or the session object may be gone.
                _LOGGER.debug("Failed to disconnect 'resetPerformed' signal", exc_info=True)

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
        if not enabled and self._pipette.isChecked():
            # Deactivate eyedropper mode when WB is disabled. setChecked(False)
            # emits the toggled signal so connected handlers exit eyedropper mode.
            self._pipette.setChecked(False)
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

    def deactivate_eyedropper(self) -> None:
        """Turn off the pipette button without triggering mode-change loops."""

        if self._pipette.isChecked():
            self._pipette.setChecked(False)

    # Allow click-to-enable when WB is disabled
    def mousePressEvent(self, event):  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            if self._session is not None and not self._session.value("WB_Enabled"):
                self._session.set_value("WB_Enabled", True)
        super().mousePressEvent(event)


__all__ = ["EditWBSection"]

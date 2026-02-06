"""White Balance adjustment section for the edit sidebar."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import partial
from typing import Dict, Optional

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QFrame, QGraphicsOpacityEffect, QVBoxLayout, QWidget

from ....core.wb_resolver import WBParams
from ..models.edit_session import EditSession
from .collapsible_section import CollapsibleSubSection
from .edit_strip import BWSlider

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class _SliderSpec:
    label: str
    key: str
    minimum: float
    maximum: float
    initial: float


class EditWBSection(QWidget):
    """Expose the white-balance adjustments as a set of sliders."""

    wbParamsPreviewed = Signal(WBParams)
    """Emitted while the user drags a control so the viewer can update live."""

    wbParamsCommitted = Signal(WBParams)
    """Emitted once the interaction ends and the session should persist the change."""

    interactionStarted = Signal()
    interactionFinished = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._session: Optional[EditSession] = None
        self._sliders: Dict[str, BWSlider] = {}
        self._rows: Dict[str, _SliderRow] = {}
        self._slider_specs: Dict[str, _SliderSpec] = {}
        self._updating_ui = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        specs = [
            _SliderSpec("Warmth", "WB_Warmth", -1.0, 1.0, 0.0),
            _SliderSpec("Temperature", "WB_Temperature", -1.0, 1.0, 0.0),
            _SliderSpec("Tint", "WB_Tint", -1.0, 1.0, 0.0),
        ]
        for spec in specs:
            row = _SliderRow(spec, self)
            slider = row.slider
            slider.valueChanged.connect(partial(self._handle_slider_changed, spec.key))
            slider.valueCommitted.connect(partial(self._handle_slider_committed, spec.key))
            slider.interactionStarted.connect(self.interactionStarted)
            slider.interactionFinished.connect(self.interactionFinished)
            row.clickedWhenDisabled.connect(self._handle_disabled_slider_click)
            layout.addWidget(row)
            self._sliders[spec.key] = slider
            self._rows[spec.key] = row
            self._slider_specs[spec.key] = spec

        layout.addStretch(1)

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
            for key, slider in self._sliders.items():
                slider.setValue(float(self._session.value(key)), emit=False)
        finally:
            self._updating_ui = False

    # ------------------------------------------------------------------
    def _reset_slider_values(self) -> None:
        self._updating_ui = True
        try:
            for key, slider in self._sliders.items():
                spec = self._slider_specs.get(key)
                initial = spec.initial if spec is not None else 0.0
                slider.setValue(initial, emit=False)
        finally:
            self._updating_ui = False

    def _apply_enabled_state(self, enabled: bool) -> None:
        for row in self._rows.values():
            row.setEnabled(enabled)

    def _gather_slider_params(self) -> WBParams:
        return WBParams(
            warmth=self._sliders["WB_Warmth"].value(),
            temperature=self._sliders["WB_Temperature"].value(),
            tint=self._sliders["WB_Tint"].value(),
        )

    # ------------------------------------------------------------------
    @Slot(str, object)
    def _on_session_value_changed(self, key: str, _value: object) -> None:
        if key == "WB_Enabled":
            self._apply_enabled_state(bool(self._session.value("WB_Enabled")))
            return
        if key.startswith("WB_"):
            self.refresh_from_session()

    @Slot()
    def _on_session_reset(self) -> None:
        self.refresh_from_session()

    def _handle_slider_changed(self, key: str, _value: float) -> None:
        if self._updating_ui:
            return
        params = self._gather_slider_params()
        self.wbParamsPreviewed.emit(params)

    def _handle_slider_committed(self, key: str, value: float) -> None:
        params = self._gather_slider_params()
        self.wbParamsCommitted.emit(params)

    @Slot()
    def _handle_disabled_slider_click(self) -> None:
        if self._session is not None and not self._session.value("WB_Enabled"):
            self._session.set_value("WB_Enabled", True)


class _SliderRow(QFrame):
    """Mirror the light/color slider row behaviour for the WB panel."""

    clickedWhenDisabled = Signal()

    def __init__(self, spec: _SliderSpec, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.slider = BWSlider(
            spec.label,
            self,
            minimum=spec.minimum,
            maximum=spec.maximum,
            initial=spec.initial,
        )
        layout.addWidget(self.slider)

        self._opacity_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity_effect)

    def setEnabled(self, enabled: bool) -> None:  # type: ignore[override]
        super().setEnabled(True)
        self.slider.setEnabled(enabled)
        self._opacity_effect.setOpacity(1.0 if enabled else 0.5)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            local_point = event.position().toPoint()
            if not self.slider.isEnabled() and self.slider.geometry().contains(local_point):
                self.clickedWhenDisabled.emit()
                forwarded = QMouseEvent(
                    event.type(),
                    self.slider.mapFrom(self, local_point),
                    event.globalPosition(),
                    event.button(),
                    event.buttons(),
                    event.modifiers(),
                )
                QApplication.sendEvent(self.slider, forwarded)
                event.accept()
                return
        super().mousePressEvent(event)


__all__ = ["EditWBSection"]

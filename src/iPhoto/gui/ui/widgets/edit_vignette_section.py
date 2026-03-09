"""Vignette adjustment section for the edit sidebar."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import QVBoxLayout, QWidget

from ..models.edit_session import EditSession
from .edit_strip import BWSlider


class EditVignetteSection(QWidget):
    """Expose the vignette adjustment as a section in the edit sidebar."""

    vignetteParamsPreviewed = Signal(object)
    """Emitted while the user drags a slider so the viewer can update live."""

    vignetteParamsCommitted = Signal(object)
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

        self._strength_slider = BWSlider(
            "Strength",
            parent=self,
            minimum=0.0,
            maximum=1.0,
            initial=0.0,
        )
        layout.addWidget(self._strength_slider)

        self._radius_slider = BWSlider(
            "Radius",
            parent=self,
            minimum=0.0,
            maximum=1.0,
            initial=0.50,
        )
        layout.addWidget(self._radius_slider)

        self._softness_slider = BWSlider(
            "Softness",
            parent=self,
            minimum=0.0,
            maximum=1.0,
            initial=0.0,
        )
        layout.addWidget(self._softness_slider)

        # Wire internal signals
        for slider in (self._strength_slider, self._radius_slider, self._softness_slider):
            slider.valueChanged.connect(self._on_slider_changed)
            slider.interactionStarted.connect(self.interactionStarted)
            slider.interactionFinished.connect(self._on_slider_interaction_finished)

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
        else:
            self._reset_to_defaults()

    def refresh_from_session(self) -> None:
        if self._session is None:
            self._reset_to_defaults()
            return

        self._updating_ui = True
        try:
            self._strength_slider.setValue(
                float(self._session.value("Vignette_Strength")), emit=False
            )
            self._radius_slider.setValue(
                float(self._session.value("Vignette_Radius")), emit=False
            )
            self._softness_slider.setValue(
                float(self._session.value("Vignette_Softness")), emit=False
            )
        finally:
            self._updating_ui = False

    def _reset_to_defaults(self) -> None:
        self._updating_ui = True
        try:
            self._strength_slider.setValue(0.0, emit=False)
            self._radius_slider.setValue(0.50, emit=False)
            self._softness_slider.setValue(0.0, emit=False)
        finally:
            self._updating_ui = False

    # ------------------------------------------------------------------
    # Signal handlers
    # ------------------------------------------------------------------

    def _on_slider_changed(self, _value: float) -> None:
        if self._updating_ui:
            return
        self._preview_vignette_changes()

    def _on_slider_interaction_finished(self) -> None:
        if self._updating_ui:
            return
        self._commit_vignette_changes()
        self.interactionFinished.emit()

    @Slot(str, object)
    def _on_session_value_changed(self, key: str, _value: object) -> None:
        if key.startswith("Vignette_"):
            self.refresh_from_session()

    @Slot()
    def _on_session_reset(self) -> None:
        self.refresh_from_session()

    # ------------------------------------------------------------------
    # Preview / commit
    # ------------------------------------------------------------------

    def _gather_vignette_params(self) -> dict:
        return {
            "Strength": self._strength_slider.value(),
            "Radius": self._radius_slider.value(),
            "Softness": self._softness_slider.value(),
        }

    def _preview_vignette_changes(self) -> None:
        data = self._gather_vignette_params()
        self.vignetteParamsPreviewed.emit(data)

    def _commit_vignette_changes(self) -> None:
        if self._session is None:
            return
        updates = {
            "Vignette_Enabled": True,
            "Vignette_Strength": self._strength_slider.value(),
            "Vignette_Radius": self._radius_slider.value(),
            "Vignette_Softness": self._softness_slider.value(),
        }
        self._session.set_values(updates)
        self.vignetteParamsCommitted.emit(self._gather_vignette_params())

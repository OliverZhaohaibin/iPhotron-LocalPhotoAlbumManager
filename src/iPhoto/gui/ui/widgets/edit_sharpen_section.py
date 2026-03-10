"""Sharpen adjustment section for the edit sidebar."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import QVBoxLayout, QWidget

from ..models.edit_session import EditSession
from .edit_strip import BWSlider


class EditSharpenSection(QWidget):
    """Expose the sharpen adjustment as a section in the edit sidebar."""

    sharpenParamsPreviewed = Signal(object)
    """Emitted while the user drags a slider so the viewer can update live."""

    sharpenParamsCommitted = Signal(object)
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

        self._intensity_slider = BWSlider(
            "Intensity",
            parent=self,
            minimum=0.0,
            maximum=1.0,
            initial=0.0,
        )
        layout.addWidget(self._intensity_slider)

        self._edges_slider = BWSlider(
            "Edges",
            parent=self,
            minimum=0.0,
            maximum=1.0,
            initial=0.0,
        )
        layout.addWidget(self._edges_slider)

        self._falloff_slider = BWSlider(
            "Falloff",
            parent=self,
            minimum=0.0,
            maximum=1.0,
            initial=0.0,
        )
        layout.addWidget(self._falloff_slider)

        # Wire internal signals
        for slider in (self._intensity_slider, self._edges_slider, self._falloff_slider):
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
            self._intensity_slider.setValue(
                float(self._session.value("Sharpen_Intensity")), emit=False
            )
            self._edges_slider.setValue(
                float(self._session.value("Sharpen_Edges")), emit=False
            )
            self._falloff_slider.setValue(
                float(self._session.value("Sharpen_Falloff")), emit=False
            )
        finally:
            self._updating_ui = False

    def _reset_to_defaults(self) -> None:
        self._updating_ui = True
        try:
            self._intensity_slider.setValue(0.0, emit=False)
            self._edges_slider.setValue(0.0, emit=False)
            self._falloff_slider.setValue(0.0, emit=False)
        finally:
            self._updating_ui = False

    # ------------------------------------------------------------------
    # Signal handlers
    # ------------------------------------------------------------------

    def _on_slider_changed(self, value: float) -> None:
        if self._updating_ui:
            return
        self._preview_sharpen_changes()

    def _on_slider_interaction_finished(self) -> None:
        if self._updating_ui:
            return
        self._commit_sharpen_changes()
        self.interactionFinished.emit()

    @Slot(str, object)
    def _on_session_value_changed(self, key: str, _value: object) -> None:
        if key.startswith("Sharpen_"):
            self.refresh_from_session()

    @Slot()
    def _on_session_reset(self) -> None:
        self.refresh_from_session()

    # ------------------------------------------------------------------
    # Preview / commit
    # ------------------------------------------------------------------

    def _gather_sharpen_params(self) -> dict:
        return {
            "Intensity": self._intensity_slider.value(),
            "Edges": self._edges_slider.value(),
            "Falloff": self._falloff_slider.value(),
        }

    def _preview_sharpen_changes(self) -> None:
        data = self._gather_sharpen_params()
        self.sharpenParamsPreviewed.emit(data)

    def _commit_sharpen_changes(self) -> None:
        if self._session is None:
            return
        updates = {
            "Sharpen_Enabled": True,
            "Sharpen_Intensity": self._intensity_slider.value(),
            "Sharpen_Edges": self._edges_slider.value(),
            "Sharpen_Falloff": self._falloff_slider.value(),
        }
        self._session.set_values(updates)
        self.sharpenParamsCommitted.emit(self._gather_sharpen_params())

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
    """Emitted while the user drags the slider so the viewer can update live."""

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

        self._slider = BWSlider(
            "Amount",
            parent=self,
            minimum=0.0,
            maximum=3.0,
            initial=0.0,
        )
        layout.addWidget(self._slider)

        # Wire internal signals
        self._slider.valueChanged.connect(self._on_slider_changed)
        self._slider.interactionStarted.connect(self.interactionStarted)
        self._slider.interactionFinished.connect(self._on_slider_interaction_finished)

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
            value = float(self._session.value("Sharpen_Amount"))
            self._slider.setValue(value, emit=False)
        finally:
            self._updating_ui = False

    def _reset_to_defaults(self) -> None:
        self._updating_ui = True
        try:
            self._slider.setValue(0.0, emit=False)
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
        return {"Amount": self._slider.value()}

    def _preview_sharpen_changes(self) -> None:
        data = self._gather_sharpen_params()
        self.sharpenParamsPreviewed.emit(data)

    def _commit_sharpen_changes(self) -> None:
        if self._session is None:
            return
        updates = {
            "Sharpen_Enabled": True,
            "Sharpen_Amount": self._slider.value(),
        }
        self._session.set_values(updates)
        self.sharpenParamsCommitted.emit(self._gather_sharpen_params())

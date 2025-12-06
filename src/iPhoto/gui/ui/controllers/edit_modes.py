"""State pattern implementation for edit modes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Mapping

from PySide6.QtCore import QObject

if TYPE_CHECKING:
    from ..ui_main_window import Ui_MainWindow
class EditModeState(QObject):
    """Abstract base state for edit modes."""

    def __init__(self, ui: Ui_MainWindow, session_provider, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._ui = ui
        # session_provider is a callable that returns the current EditSession or None
        self._session_provider = session_provider

    @property
    def mode_name(self) -> str:
        """Return the unique identifier for this mode."""
        raise NotImplementedError

    def enter(self) -> None:
        """Configure the UI for this mode."""
        pass

    def exit(self) -> None:
        """Clean up the UI when leaving this mode."""
        pass


class AdjustModeState(EditModeState):
    """State for the standard 'Adjust' mode."""

    @property
    def mode_name(self) -> str:
        return "adjust"

    def enter(self) -> None:
        self._ui.edit_adjust_action.setChecked(True)
        self._ui.edit_crop_action.setChecked(False)
        self._ui.edit_sidebar.set_mode("adjust")
        self._ui.edit_image_viewer.setCropMode(False)

        # Block signals to prevent recursion if the control emits signal on same index
        was_blocked = self._ui.edit_mode_control.blockSignals(True)
        self._ui.edit_mode_control.setCurrentIndex(0, animate=True)
        self._ui.edit_mode_control.blockSignals(was_blocked)


class CropModeState(EditModeState):
    """State for the 'Crop' mode."""

    @property
    def mode_name(self) -> str:
        return "crop"

    def enter(self) -> None:
        self._ui.edit_adjust_action.setChecked(False)
        self._ui.edit_crop_action.setChecked(True)
        self._ui.edit_sidebar.set_mode("crop")

        session = self._session_provider()
        crop_values: Mapping[str, float] | None = None
        if session is not None:
            crop_values = {
                "Crop_CX": float(session.value("Crop_CX")),
                "Crop_CY": float(session.value("Crop_CY")),
                "Crop_W": float(session.value("Crop_W")),
                "Crop_H": float(session.value("Crop_H")),
                "Crop_Rotate90": float(session.value("Crop_Rotate90")),
                "Crop_FlipH": float(session.value("Crop_FlipH")),
            }
        self._ui.edit_image_viewer.setCropMode(True, crop_values)

        # Block signals to prevent recursion if the control emits signal on same index
        was_blocked = self._ui.edit_mode_control.blockSignals(True)
        self._ui.edit_mode_control.setCurrentIndex(1)
        self._ui.edit_mode_control.blockSignals(was_blocked)

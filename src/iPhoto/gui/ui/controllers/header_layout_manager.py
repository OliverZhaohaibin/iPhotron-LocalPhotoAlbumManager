"""Manages the layout of header widgets during edit mode."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QObject

if TYPE_CHECKING:
    from ..ui_main_window import Ui_MainWindow


class HeaderLayoutManager(QObject):
    """Handles reparenting widgets between the main header and the edit header."""

    def __init__(self, ui: Ui_MainWindow, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._ui = ui

    def switch_to_edit_mode(self) -> None:
        """Reparent shared toolbar widgets into the edit header."""
        ui = self._ui

        # Move Zoom Widget
        if ui.edit_zoom_host_layout.indexOf(ui.zoom_widget) == -1:
            ui.edit_zoom_host_layout.addWidget(ui.zoom_widget)
        ui.zoom_widget.show()

        # Move Info and Favorite buttons
        right_layout = ui.edit_right_controls_layout
        if right_layout.indexOf(ui.info_button) == -1:
            # Insert at beginning to match desired order
            right_layout.insertWidget(0, ui.info_button)
        if right_layout.indexOf(ui.favorite_button) == -1:
            right_layout.insertWidget(1, ui.favorite_button)

    def restore_detail_mode(self) -> None:
        """Return shared toolbar widgets to the detail header layout."""
        ui = self._ui

        # Restore widgets to their original positions in detail_actions_layout
        # We rely on indices captured/stored in UI setup or assume they are static
        ui.detail_actions_layout.insertWidget(ui.detail_info_button_index, ui.info_button)
        ui.detail_actions_layout.insertWidget(ui.detail_favorite_button_index, ui.favorite_button)

        # Restore Zoom Widget
        ui.detail_header_layout.insertWidget(ui.detail_zoom_widget_index, ui.zoom_widget)

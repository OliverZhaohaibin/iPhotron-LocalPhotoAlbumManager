"""Coordinator for managing the main view stack (Gallery, Detail, Edit, Map)."""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING
from PySide6.QtCore import QObject, Signal

if TYPE_CHECKING:
    from src.iPhoto.gui.ui.main_window import Ui_MainWindow
    from src.iPhoto.gui.ui.widgets.detail_page import DetailPage
    from src.iPhoto.gui.ui.widgets.edit_view import EditView
    from src.iPhoto.gui.ui.widgets.photo_map_view import PhotoMapView

class ViewRouter(QObject):
    """
    Manages the central QStackedWidget to switch between different views.
    Replaces the legacy ViewControllerManager.
    """

    # Signals for view changes
    galleryViewShown = Signal()
    detailViewShown = Signal()
    editViewShown = Signal()
    mapViewShown = Signal()

    def __init__(self, ui: Ui_MainWindow):
        super().__init__()
        self._ui = ui
        self._stack = ui.stack_widget

        # Store view indices (assuming order from Ui_MainWindow setup)
        # Typically: 0=Gallery, 1=Detail, 2=Edit, 3=Map (Check setupUi)
        # We'll discover them dynamically or enforce them.
        self._gallery_idx = self._stack.indexOf(ui.gallery_page)
        self._detail_idx = self._stack.indexOf(ui.detail_page)

        # Edit View and Map View might be lazy loaded or pre-inserted
        # If they exist in UI:
        self._edit_idx = -1
        if hasattr(ui, 'edit_page'):
            self._edit_idx = self._stack.indexOf(ui.edit_page)

        self._map_idx = -1
        if hasattr(ui, 'map_page'):
            self._map_idx = self._stack.indexOf(ui.map_page)

    def show_gallery(self):
        """Switch to the Gallery (Grid) view."""
        if self._stack.currentIndex() != self._gallery_idx:
            self._stack.setCurrentIndex(self._gallery_idx)
            self.galleryViewShown.emit()

    def show_detail(self):
        """Switch to the Detail (Single Asset) view."""
        if self._stack.currentIndex() != self._detail_idx:
            self._stack.setCurrentIndex(self._detail_idx)
            self.detailViewShown.emit()

    def show_edit(self):
        """Switch to the Edit view."""
        if self._edit_idx != -1 and self._stack.currentIndex() != self._edit_idx:
            self._stack.setCurrentIndex(self._edit_idx)
            self.editViewShown.emit()

    def show_map(self):
        """Switch to the Map view."""
        if self._map_idx != -1 and self._stack.currentIndex() != self._map_idx:
            self._stack.setCurrentIndex(self._map_idx)
            self.mapViewShown.emit()

    def is_detail_view_active(self) -> bool:
        return self._stack.currentIndex() == self._detail_idx

    def is_edit_view_active(self) -> bool:
        return self._stack.currentIndex() == self._edit_idx

    def current_view(self):
        return self._stack.currentWidget()

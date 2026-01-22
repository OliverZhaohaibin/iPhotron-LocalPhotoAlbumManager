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
        self._stack = ui.view_stack

        # Store view indices (assuming order from Ui_MainWindow setup)
        # Typically: 0=Gallery, 1=Map, 2=Detail, 3=Dashboard
        self._gallery_idx = self._stack.indexOf(ui.gallery_page)
        self._detail_idx = self._stack.indexOf(ui.detail_page)

        # Map View
        self._map_idx = -1
        if hasattr(ui, 'map_page'):
            self._map_idx = self._stack.indexOf(ui.map_page)

        # Edit View: Currently part of Detail Page structure
        # There is no separate edit_page in the main stack.
        # We reuse detail index or handle it as a state of detail page.
        # If edit_page existed, we'd look it up.
        self._edit_idx = -1
        if hasattr(ui, 'edit_page'):
            self._edit_idx = self._stack.indexOf(ui.edit_page)

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
        # If there is a dedicated edit page in the stack, switch to it.
        if self._edit_idx != -1:
            if self._stack.currentIndex() != self._edit_idx:
                self._stack.setCurrentIndex(self._edit_idx)
                self.editViewShown.emit()
        else:
            # Fallback: Editing usually happens in Detail Page (overlay/mode)
            # Ensure Detail Page is visible.
            if self._stack.currentIndex() != self._detail_idx:
                self._stack.setCurrentIndex(self._detail_idx)
            # Emit signal so Coordinators know we are "in edit mode context"
            self.editViewShown.emit()

    def show_map(self):
        """Switch to the Map view."""
        if self._map_idx != -1 and self._stack.currentIndex() != self._map_idx:
            self._stack.setCurrentIndex(self._map_idx)
            self.mapViewShown.emit()

    def is_detail_view_active(self) -> bool:
        return self._stack.currentIndex() == self._detail_idx

    def is_edit_view_active(self) -> bool:
        if self._edit_idx != -1:
            return self._stack.currentIndex() == self._edit_idx
        # If editing is a mode of Detail Page, checking index isn't enough.
        # But for this Router, checking if we are on the page that supports editing is the baseline.
        # EditCoordinator tracks the actual 'mode'.
        return self._stack.currentIndex() == self._detail_idx

    def current_view(self):
        return self._stack.currentWidget()

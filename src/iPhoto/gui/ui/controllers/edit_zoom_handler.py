"""Handler for edit zoom controls."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QPushButton, QSlider

if TYPE_CHECKING:
    from ..widgets.gl_image_viewer import GLImageViewer
    from ..widgets.video_area import VideoArea

class EditZoomHandler(QObject):
    """Manages the connection between the global zoom toolbar and the edit viewer."""

    def __init__(
        self,
        viewer: GLImageViewer | "VideoArea",
        zoom_in_button: QPushButton,
        zoom_out_button: QPushButton,
        zoom_slider: QSlider,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._viewer = viewer
        self._zoom_in_button = zoom_in_button
        self._zoom_out_button = zoom_out_button
        self._zoom_slider = zoom_slider
        self._connected = False
        self._zoom_in_slot = None
        self._zoom_out_slot = None

    def connect_controls(self) -> None:
        """Connect the shared zoom toolbar to the edit image viewer."""
        if self._connected:
            return

        # Cache the bound methods we connect so disconnect uses the exact same
        # callable object when the active viewer changes.
        self._zoom_in_slot = self._viewer.zoom_in
        self._zoom_out_slot = self._viewer.zoom_out
        self._zoom_in_button.clicked.connect(self._zoom_in_slot)
        self._zoom_out_button.clicked.connect(self._zoom_out_slot)
        self._zoom_slider.valueChanged.connect(self._handle_slider_changed)
        self._viewer.zoomChanged.connect(self._handle_viewer_zoom_changed)
        self._connected = True

    def disconnect_controls(self) -> None:
        """Detach the shared zoom toolbar from the edit image viewer."""
        if not self._connected:
            return

        try:
            if self._zoom_in_slot is not None:
                self._zoom_in_button.clicked.disconnect(self._zoom_in_slot)
            if self._zoom_out_slot is not None:
                self._zoom_out_button.clicked.disconnect(self._zoom_out_slot)
            self._zoom_slider.valueChanged.disconnect(self._handle_slider_changed)
            self._viewer.zoomChanged.disconnect(self._handle_viewer_zoom_changed)
        finally:
            self._connected = False
            self._zoom_in_slot = None
            self._zoom_out_slot = None

    def _handle_slider_changed(self, value: int) -> None:
        """Translate slider *value* percentages into edit viewer zoom factors."""
        clamped = max(self._zoom_slider.minimum(), min(self._zoom_slider.maximum(), value))
        factor = float(clamped) / 100.0
        self._viewer.set_zoom(factor, anchor=self._viewer.viewport_center())

    def _handle_viewer_zoom_changed(self, factor: float) -> None:
        """Synchronise the slider position when the edit viewer reports a new zoom *factor*."""
        slider_value = max(
            self._zoom_slider.minimum(),
            min(self._zoom_slider.maximum(), int(round(factor * 100.0)))
        )
        if slider_value == self._zoom_slider.value():
            return
        self._zoom_slider.blockSignals(True)
        self._zoom_slider.setValue(slider_value)
        self._zoom_slider.blockSignals(False)

    def set_viewer(self, viewer: GLImageViewer | "VideoArea") -> None:
        """Retarget the shared zoom controls to *viewer*."""

        if self._viewer is viewer:
            return
        was_connected = self._connected
        if was_connected:
            self.disconnect_controls()
        self._viewer = viewer
        if was_connected:
            self.connect_controls()

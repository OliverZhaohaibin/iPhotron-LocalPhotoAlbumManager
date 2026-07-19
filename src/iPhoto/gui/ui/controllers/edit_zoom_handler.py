"""Handler for edit zoom controls."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QMetaObject, QObject
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
        self._zoom_in_connection: QMetaObject.Connection | None = None
        self._zoom_out_connection: QMetaObject.Connection | None = None
        self._slider_connection: QMetaObject.Connection | None = None
        self._viewer_connection: QMetaObject.Connection | None = None

    def connect_controls(self) -> None:
        """Connect the shared zoom toolbar to the edit image viewer."""
        if self._connected:
            return

        # Keep Qt's connection handles instead of disconnecting by Python
        # callable. PySide removes every duplicate connection matching a bound
        # method, which can invalidate a second controller's connection.
        self._zoom_in_connection = self._zoom_in_button.clicked.connect(
            self._handle_zoom_in_clicked
        )
        self._zoom_out_connection = self._zoom_out_button.clicked.connect(
            self._handle_zoom_out_clicked
        )
        self._slider_connection = self._zoom_slider.valueChanged.connect(
            self._handle_slider_changed
        )
        self._viewer_connection = self._viewer.zoomChanged.connect(
            self._handle_viewer_zoom_changed
        )
        self._connected = True

    def disconnect_controls(self) -> None:
        """Detach the shared zoom toolbar from the edit image viewer."""
        if not self._connected:
            return

        connections = (
            self._zoom_in_connection,
            self._zoom_out_connection,
            self._slider_connection,
            self._viewer_connection,
        )
        self._connected = False
        self._zoom_in_connection = None
        self._zoom_out_connection = None
        self._slider_connection = None
        self._viewer_connection = None

        for connection in connections:
            if connection is not None:
                QObject.disconnect(connection)

    def _handle_zoom_in_clicked(self, _checked: bool = False) -> None:
        """Zoom the currently active viewer in."""
        self._viewer.zoom_in()

    def _handle_zoom_out_clicked(self, _checked: bool = False) -> None:
        """Zoom the currently active viewer out."""
        self._viewer.zoom_out()

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

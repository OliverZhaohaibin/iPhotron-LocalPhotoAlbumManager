"""Unit tests for EditZoomHandler."""

from __future__ import annotations

import os
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from src.iPhoto.gui.ui.controllers.edit_zoom_handler import EditZoomHandler

@pytest.fixture()
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app

class MockViewer(QObject):
    zoomChanged = Signal(float)

    def __init__(self):
        super().__init__()
        self.zoom_in = Mock()
        self.zoom_out = Mock()
        self.set_zoom = Mock()
        self.viewport_center = Mock()

class MockButton(QObject):
    clicked = Signal()

class MockSlider(QObject):
    valueChanged = Signal(int)

    def __init__(self):
        super().__init__()
        self.minimum = Mock(return_value=10)
        self.maximum = Mock(return_value=500)
        self.value = Mock(return_value=100)
        self.setValue = Mock()

@pytest.fixture
def zoom_handler_setup(qapp):
    viewer = MockViewer()
    btn_in = MockButton()
    btn_out = MockButton()
    slider = MockSlider()

    handler = EditZoomHandler(viewer, btn_in, btn_out, slider)
    return handler, viewer, btn_in, btn_out, slider

def test_connect_controls(zoom_handler_setup):
    """Verify signals are connected."""
    handler, viewer, btn_in, btn_out, slider = zoom_handler_setup

    handler.connect_controls()
    assert handler._connected

    # Test Button Clicks
    btn_in.clicked.emit()
    viewer.zoom_in.assert_called_once()

    btn_out.clicked.emit()
    viewer.zoom_out.assert_called_once()

    # Test Slider
    slider.valueChanged.emit(150)
    # 150 -> 1.5 zoom
    viewer.set_zoom.assert_called_with(1.5, anchor=viewer.viewport_center())

    # Test Viewer Zoom Change
    viewer.zoomChanged.emit(2.0)
    slider.setValue.assert_called_with(200)

def test_disconnect_controls(zoom_handler_setup):
    """Verify signals are disconnected."""
    handler, viewer, btn_in, btn_out, slider = zoom_handler_setup

    handler.connect_controls()
    handler.disconnect_controls()
    assert not handler._connected

    # Emitting signals should not trigger mocks now (or trigger errors if disconnected)
    # Since we use actual signals on mock objects, disconnected signals won't call slots.

    # But strictly speaking, we want to ensure methods are NOT called.
    viewer.zoom_in.reset_mock()
    btn_in.clicked.emit()
    viewer.zoom_in.assert_not_called()

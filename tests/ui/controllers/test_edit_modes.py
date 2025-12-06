"""Unit tests for edit mode states."""

from __future__ import annotations

import os
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication

from src.iPhoto.gui.ui.controllers.edit_modes import AdjustModeState, CropModeState

@pytest.fixture()
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app

class MockUi:
    def __init__(self):
        self.edit_adjust_action = Mock()
        self.edit_crop_action = Mock()
        self.edit_sidebar = Mock()
        self.edit_image_viewer = Mock()
        self.edit_mode_control = Mock()

def test_adjust_mode_enter(qapp):
    ui = MockUi()
    session_provider = Mock(return_value=None)
    state = AdjustModeState(ui, session_provider)

    assert state.mode_name == "adjust"

    state.enter()

    ui.edit_adjust_action.setChecked.assert_called_with(True)
    ui.edit_crop_action.setChecked.assert_called_with(False)
    ui.edit_sidebar.set_mode.assert_called_with("adjust")
    ui.edit_image_viewer.setCropMode.assert_called_with(False)
    ui.edit_mode_control.setCurrentIndex.assert_called_with(0, animate=True)

def test_crop_mode_enter_with_session(qapp):
    ui = MockUi()

    # Mock session values
    session = Mock()
    session.value.side_effect = lambda k: {
        "Crop_CX": 0.5,
        "Crop_CY": 0.5,
        "Crop_W": 1.0,
        "Crop_H": 1.0,
        "Crop_Rotate90": 0.0,
        "Crop_FlipH": 0.0,
    }[k]

    session_provider = Mock(return_value=session)
    state = CropModeState(ui, session_provider)

    assert state.mode_name == "crop"

    state.enter()

    ui.edit_adjust_action.setChecked.assert_called_with(False)
    ui.edit_crop_action.setChecked.assert_called_with(True)
    ui.edit_sidebar.set_mode.assert_called_with("crop")

    expected_crop = {
        "Crop_CX": 0.5,
        "Crop_CY": 0.5,
        "Crop_W": 1.0,
        "Crop_H": 1.0,
        "Crop_Rotate90": 0.0,
        "Crop_FlipH": 0.0,
    }
    ui.edit_image_viewer.setCropMode.assert_called_with(True, expected_crop)
    ui.edit_mode_control.setCurrentIndex.assert_called_with(1)

def test_crop_mode_enter_no_session(qapp):
    ui = MockUi()
    session_provider = Mock(return_value=None)
    state = CropModeState(ui, session_provider)

    state.enter()

    ui.edit_image_viewer.setCropMode.assert_called_with(True, None)

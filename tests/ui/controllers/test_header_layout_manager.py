"""Unit tests for HeaderLayoutManager."""

from __future__ import annotations

import os
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication

from src.iPhoto.gui.ui.controllers.header_layout_manager import HeaderLayoutManager

@pytest.fixture()
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app

def test_switch_to_edit_mode(qapp):
    ui = Mock()
    manager = HeaderLayoutManager(ui)

    # Setup mocks
    ui.edit_zoom_host_layout.indexOf.return_value = -1
    ui.edit_right_controls_layout.indexOf.return_value = -1

    manager.switch_to_edit_mode()

    # Verification
    ui.edit_zoom_host_layout.addWidget.assert_called_with(ui.zoom_widget)
    ui.zoom_widget.show.assert_called_once()

    ui.edit_right_controls_layout.insertWidget.assert_any_call(0, ui.info_button)
    ui.edit_right_controls_layout.insertWidget.assert_any_call(1, ui.favorite_button)

def test_restore_detail_mode(qapp):
    ui = Mock()
    manager = HeaderLayoutManager(ui)

    ui.detail_info_button_index = 5
    ui.detail_favorite_button_index = 6
    ui.detail_zoom_widget_index = 2

    manager.restore_detail_mode()

    ui.detail_actions_layout.insertWidget.assert_any_call(5, ui.info_button)
    ui.detail_actions_layout.insertWidget.assert_any_call(6, ui.favorite_button)
    ui.detail_header_layout.insertWidget.assert_any_call(2, ui.zoom_widget)

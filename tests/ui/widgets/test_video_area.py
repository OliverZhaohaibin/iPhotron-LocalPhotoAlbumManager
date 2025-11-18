"""Tests for VideoArea widget."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for GUI tests")

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from iPhotos.src.iPhoto.gui.ui.widgets.video_area import VideoArea


@pytest.fixture
def qapp():
    """Create QApplication instance for Qt tests."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def test_video_area_show_event_calls_update_bar_geometry(qapp, mocker):
    """Test that showEvent calls _update_bar_geometry to fix initial position."""
    # Create a VideoArea widget
    video_area = VideoArea()
    
    # Mock the _update_bar_geometry method to track if it's called
    mock_update = mocker.patch.object(video_area, '_update_bar_geometry')
    
    # Create a show event
    show_event = QEvent(QEvent.Type.Show)
    
    # Call showEvent
    video_area.showEvent(show_event)
    
    # Verify that _update_bar_geometry was called
    mock_update.assert_called_once()


def test_video_area_show_event_calls_super(qapp, mocker):
    """Test that showEvent calls the parent class's showEvent."""
    # Create a VideoArea widget
    video_area = VideoArea()
    
    # Mock the parent showEvent
    mock_super_show = mocker.patch('PySide6.QtWidgets.QWidget.showEvent')
    
    # Create a show event
    show_event = QEvent(QEvent.Type.Show)
    
    # Call showEvent
    video_area.showEvent(show_event)
    
    # Verify that super().showEvent was called with the event
    mock_super_show.assert_called_once_with(show_event)

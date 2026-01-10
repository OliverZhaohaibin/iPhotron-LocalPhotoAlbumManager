"""Unit tests for GalleryQuickWidget theme functionality."""

from unittest.mock import MagicMock, patch
import pytest

from PySide6.QtCore import QUrl
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from src.iPhoto.gui.ui.widgets.gallery_grid_view import GalleryQuickWidget
from src.iPhoto.gui.ui.theme_manager import LIGHT_THEME, DARK_THEME


@pytest.fixture(scope="session")
def qapp():
    """Create QApplication instance for tests."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture
def gallery_widget(qapp):
    """Create a GalleryQuickWidget for testing."""
    widget = GalleryQuickWidget()
    # Mock the QML engine to avoid needing actual QML files
    widget.engine = MagicMock()
    yield widget
    widget.deleteLater()


def test_apply_theme_stores_colors(gallery_widget):
    """Test that apply_theme correctly stores theme colors."""
    # Initially, theme colors should be None
    assert gallery_widget._theme_colors is None
    
    # Apply light theme
    gallery_widget.apply_theme(LIGHT_THEME)
    
    # Verify colors are stored
    assert gallery_widget._theme_colors == LIGHT_THEME


def test_apply_theme_updates_palette(gallery_widget):
    """Test that apply_theme updates the widget palette."""
    gallery_widget.apply_theme(LIGHT_THEME)
    
    palette = gallery_widget.palette()
    assert palette.color(QPalette.ColorRole.Window) == LIGHT_THEME.window_background
    assert palette.color(QPalette.ColorRole.Base) == LIGHT_THEME.window_background


def test_apply_theme_sets_clear_color(gallery_widget):
    """Test that apply_theme sets the clear color."""
    with patch.object(gallery_widget, 'setClearColor') as mock_set_clear:
        gallery_widget.apply_theme(LIGHT_THEME)
        mock_set_clear.assert_called_with(LIGHT_THEME.window_background)


def test_apply_background_color_sets_palette(gallery_widget):
    """Test that _apply_background_color correctly sets palette colors."""
    test_color = QColor("#123456")
    gallery_widget._apply_background_color(test_color)
    
    palette = gallery_widget.palette()
    assert palette.color(QPalette.ColorRole.Window) == test_color
    assert palette.color(QPalette.ColorRole.Base) == test_color


def test_apply_background_color_enables_autofill(gallery_widget):
    """Test that _apply_background_color enables auto fill background."""
    test_color = QColor("#123456")
    gallery_widget._apply_background_color(test_color)
    
    assert gallery_widget.autoFillBackground() is True


def test_sync_theme_to_qml_light_theme(gallery_widget):
    """Test that _sync_theme_to_qml correctly calculates colors for light theme."""
    # Mock rootObject
    mock_root = MagicMock()
    gallery_widget.rootObject = MagicMock(return_value=mock_root)
    
    # Apply light theme
    gallery_widget.apply_theme(LIGHT_THEME)
    
    # Verify root object properties were set
    assert mock_root.setProperty.call_count >= 4
    
    # Check background color
    calls = mock_root.setProperty.call_args_list
    bg_call = [call for call in calls if call[0][0] == "backgroundColor"][0]
    assert bg_call[0][1] == LIGHT_THEME.window_background
    
    # Check item background color (should be darker for light theme)
    item_bg_call = [call for call in calls if call[0][0] == "itemBackgroundColor"][0]
    item_bg = item_bg_call[0][1]
    expected_item_bg = QColor(LIGHT_THEME.window_background).darker(105)
    assert item_bg.red() == expected_item_bg.red()
    assert item_bg.green() == expected_item_bg.green()
    assert item_bg.blue() == expected_item_bg.blue()
    
    # Check selection border color
    selection_call = [call for call in calls if call[0][0] == "selectionBorderColor"][0]
    assert selection_call[0][1] == LIGHT_THEME.accent_color
    
    # Check current border color
    current_call = [call for call in calls if call[0][0] == "currentBorderColor"][0]
    assert current_call[0][1] == LIGHT_THEME.text_primary


def test_sync_theme_to_qml_dark_theme(gallery_widget):
    """Test that _sync_theme_to_qml correctly calculates colors for dark theme."""
    # Mock rootObject
    mock_root = MagicMock()
    gallery_widget.rootObject = MagicMock(return_value=mock_root)
    
    # Apply dark theme
    gallery_widget.apply_theme(DARK_THEME)
    
    # Verify root object properties were set
    assert mock_root.setProperty.call_count >= 4
    
    # Check item background color (should be darker for dark theme, different factor)
    calls = mock_root.setProperty.call_args_list
    item_bg_call = [call for call in calls if call[0][0] == "itemBackgroundColor"][0]
    item_bg = item_bg_call[0][1]
    expected_item_bg = QColor(DARK_THEME.window_background).darker(115)
    assert item_bg.red() == expected_item_bg.red()
    assert item_bg.green() == expected_item_bg.green()
    assert item_bg.blue() == expected_item_bg.blue()


def test_sync_theme_to_qml_no_root_object(gallery_widget):
    """Test that _sync_theme_to_qml handles missing root object gracefully."""
    # Mock rootObject to return None
    gallery_widget.rootObject = MagicMock(return_value=None)
    
    # Apply theme - should not raise an exception
    gallery_widget.apply_theme(LIGHT_THEME)
    
    # Verify theme colors are still stored
    assert gallery_widget._theme_colors == LIGHT_THEME


def test_sync_theme_to_qml_no_theme_colors(gallery_widget):
    """Test that _sync_theme_to_qml handles missing theme colors gracefully."""
    # Mock rootObject
    mock_root = MagicMock()
    gallery_widget.rootObject = MagicMock(return_value=mock_root)
    
    # Call _sync_theme_to_qml without setting theme colors first
    gallery_widget._sync_theme_to_qml()
    
    # Verify no properties were set since there are no theme colors
    mock_root.setProperty.assert_not_called()


def test_theme_switch_updates_colors(gallery_widget):
    """Test that switching themes updates all colors correctly."""
    mock_root = MagicMock()
    gallery_widget.rootObject = MagicMock(return_value=mock_root)
    
    # Apply light theme
    gallery_widget.apply_theme(LIGHT_THEME)
    light_palette = gallery_widget.palette()
    light_bg = light_palette.color(QPalette.ColorRole.Window)
    
    # Reset mock to clear previous calls
    mock_root.setProperty.reset_mock()
    
    # Apply dark theme
    gallery_widget.apply_theme(DARK_THEME)
    dark_palette = gallery_widget.palette()
    dark_bg = dark_palette.color(QPalette.ColorRole.Window)
    
    # Verify colors changed
    assert light_bg != dark_bg
    assert dark_bg == DARK_THEME.window_background
    
    # Verify QML properties were updated
    assert mock_root.setProperty.call_count >= 4

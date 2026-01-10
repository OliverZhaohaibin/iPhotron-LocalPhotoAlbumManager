"""Unit tests for GalleryQuickWidget theme functionality."""

from unittest.mock import MagicMock, patch, call

import pytest

IMPORT_ERROR = None
try:
    from PySide6.QtGui import QColor, QPalette
    from PySide6.QtWidgets import QApplication
except Exception as exc:  # pragma: no cover - missing Qt dependencies
    IMPORT_ERROR = exc
    QColor = QPalette = QApplication = None

try:
    from src.iPhoto.gui.ui.widgets.gallery_grid_view import GalleryQuickWidget
except Exception as exc:  # pragma: no cover - missing optional deps
    IMPORT_ERROR = exc
    GalleryQuickWidget = None

try:
    from src.iPhoto.gui.ui.theme_manager import LIGHT_THEME, DARK_THEME
except Exception as exc:  # pragma: no cover - missing optional deps
    IMPORT_ERROR = IMPORT_ERROR or exc
    LIGHT_THEME = DARK_THEME = None


@pytest.fixture(scope="session", autouse=True)
def _skip_if_unavailable():
    if IMPORT_ERROR:
        pytest.skip(f"GalleryQuickWidget unavailable: {IMPORT_ERROR}")


@pytest.fixture(scope="session")
def qapp():
    """Create QApplication instance for tests."""
    if IMPORT_ERROR:
        pytest.skip(f"GalleryQuickWidget unavailable: {IMPORT_ERROR}")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture
def gallery_widget(qapp):
    """Create a GalleryQuickWidget for testing."""
    if IMPORT_ERROR:
        pytest.skip(f"GalleryQuickWidget unavailable: {IMPORT_ERROR}")
    widget = GalleryQuickWidget()
    widget._engine = MagicMock()
    yield widget
    widget.deleteLater()


def test_apply_theme_stores_colors(gallery_widget):
    """Test that apply_theme correctly stores theme colors."""
    assert gallery_widget._theme_colors is None

    gallery_widget.apply_theme(LIGHT_THEME)

    assert gallery_widget._theme_colors == LIGHT_THEME


def test_apply_theme_updates_palette(gallery_widget):
    """Test that apply_theme updates the widget palette."""
    gallery_widget.apply_theme(LIGHT_THEME)

    palette = gallery_widget.palette()
    assert palette.color(QPalette.ColorRole.Window) == LIGHT_THEME.window_background
    assert palette.color(QPalette.ColorRole.Base) == LIGHT_THEME.window_background


def test_apply_theme_sets_clear_color(gallery_widget):
    """Test that apply_theme sets the clear color."""
    with patch.object(gallery_widget, "setClearColor") as mock_set_clear:
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


def test_apply_background_color_forces_opaque_alpha(gallery_widget):
    """Background application should normalise alpha to prevent transparency."""
    translucent = QColor(10, 20, 30, 0)

    gallery_widget._apply_background_color(translucent)

    palette = gallery_widget.palette()
    applied = palette.color(QPalette.ColorRole.Window)
    assert applied.alpha() == 255


def test_sync_theme_to_qml_light_theme(gallery_widget):
    """Test that _sync_theme_to_qml correctly calculates colors for light theme."""
    mock_root = MagicMock()
    gallery_widget.rootObject = MagicMock(return_value=mock_root)

    gallery_widget.apply_theme(LIGHT_THEME)

    calls = mock_root.setProperty.call_args_list
    bg_call = [call for call in calls if call[0][0] == "backgroundColor"][0]
    assert bg_call[0][1] == LIGHT_THEME.window_background

    item_bg_call = [call for call in calls if call[0][0] == "itemBackgroundColor"][0]
    item_bg = item_bg_call[0][1]
    expected_item_bg = QColor(LIGHT_THEME.window_background).darker(105)
    assert item_bg.red() == expected_item_bg.red()
    assert item_bg.green() == expected_item_bg.green()
    assert item_bg.blue() == expected_item_bg.blue()

    selection_call = [call for call in calls if call[0][0] == "selectionBorderColor"][0]
    assert selection_call[0][1] == LIGHT_THEME.accent_color

    current_call = [call for call in calls if call[0][0] == "currentBorderColor"][0]
    assert current_call[0][1] == LIGHT_THEME.text_primary


def test_sync_theme_to_qml_dark_theme(gallery_widget):
    """Test that _sync_theme_to_qml correctly calculates colors for dark theme."""
    mock_root = MagicMock()
    gallery_widget.rootObject = MagicMock(return_value=mock_root)

    gallery_widget.apply_theme(DARK_THEME)

    calls = mock_root.setProperty.call_args_list
    item_bg_call = [call for call in calls if call[0][0] == "itemBackgroundColor"][0]
    item_bg = item_bg_call[0][1]
    expected_item_bg = QColor(DARK_THEME.window_background).darker(115)
    assert item_bg.red() == expected_item_bg.red()
    assert item_bg.green() == expected_item_bg.green()
    assert item_bg.blue() == expected_item_bg.blue()


def test_sync_theme_to_qml_no_root_object(gallery_widget):
    """Test that _sync_theme_to_qml handles missing root object gracefully."""
    gallery_widget.rootObject = MagicMock(return_value=None)

    gallery_widget.apply_theme(LIGHT_THEME)

    assert gallery_widget._theme_colors == LIGHT_THEME


def test_sync_theme_to_qml_no_theme_colors(gallery_widget):
    """Test that _sync_theme_to_qml handles missing theme colors gracefully."""
    mock_root = MagicMock()
    gallery_widget.rootObject = MagicMock(return_value=mock_root)

    gallery_widget._sync_theme_to_qml()

    mock_root.setProperty.assert_not_called()


def test_theme_switch_updates_colors(gallery_widget):
    """Test that switching themes updates all colors correctly."""
    mock_root = MagicMock()
    gallery_widget.rootObject = MagicMock(return_value=mock_root)

    gallery_widget.apply_theme(LIGHT_THEME)
    light_palette = gallery_widget.palette()
    light_bg = light_palette.color(QPalette.ColorRole.Window)

    mock_root.setProperty.reset_mock()

    gallery_widget.apply_theme(DARK_THEME)
    dark_palette = gallery_widget.palette()
    dark_bg = dark_palette.color(QPalette.ColorRole.Window)

    assert light_bg != dark_bg
    assert dark_bg == DARK_THEME.window_background

    assert mock_root.setProperty.call_count >= 4

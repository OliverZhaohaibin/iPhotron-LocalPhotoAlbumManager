"""Tests for the ThemeManager."""

from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette

from iPhotos.src.iPhoto.gui.ui.theme_manager import (
    DARK_THEME,
    LIGHT_THEME,
    ThemeManager,
    ThemeColors,
)
from iPhotos.src.iPhoto.settings.manager import SettingsManager


@pytest.fixture
def mock_settings():
    """Mock the SettingsManager."""
    settings = MagicMock(spec=SettingsManager)
    # Default behavior
    settings.get.return_value = "system"
    return settings


@pytest.fixture
def theme_manager(mock_settings, qapp):
    """Create a ThemeManager instance with mocked settings."""
    return ThemeManager(settings=mock_settings)


def test_theme_colors_initialization():
    """Verify ThemeColors data class holds data correctly."""
    colors = ThemeColors(
        window_background=QColor("#000000"),
        text_primary=QColor("#ffffff"),
        text_secondary=QColor("#aaaaaa"),
        text_disabled=QColor("#555555"),
        accent_color=QColor("#ff0000"),
        border_color=QColor("#333333"),
        sidebar_background=QColor("#111111"),
        sidebar_text=QColor("#eeeeee"),
        sidebar_section_text=QColor("#bbbbbb"),
        sidebar_hover=QColor("#222222"),
        sidebar_selected=QColor("#444444"),
        scrollbar_handle=QColor("#666666"),
        scrollbar_track=QColor("#000000"),
    )
    assert colors.window_background.name() == "#000000"
    assert colors.is_dark is True

    light_colors = ThemeColors(
        window_background=QColor("#ffffff"),
        text_primary=QColor("#000000"),
        text_secondary=QColor("#aaaaaa"),
        text_disabled=QColor("#555555"),
        accent_color=QColor("#ff0000"),
        border_color=QColor("#333333"),
        sidebar_background=QColor("#eeeeee"),
        sidebar_text=QColor("#111111"),
        sidebar_section_text=QColor("#bbbbbb"),
        sidebar_hover=QColor("#dddddd"),
        sidebar_selected=QColor("#cccccc"),
        scrollbar_handle=QColor("#999999"),
        scrollbar_track=QColor("#ffffff"),
    )
    assert light_colors.window_background.name() == "#ffffff"
    assert light_colors.is_dark is False


def test_initial_state(theme_manager, mock_settings):
    """Test initial state of ThemeManager."""
    # Should default to LIGHT_THEME if system detection defaults to light in test env
    # or depends on what we mock.
    # The constructor calls _settings.get("ui.theme", "system").
    # It does NOT automatically apply theme in __init__, but it sets _current_colors = LIGHT_THEME initially
    assert theme_manager.current_colors() == LIGHT_THEME
    assert theme_manager._force_dark_mode is False


def test_get_effective_theme_mode_settings(theme_manager, mock_settings):
    """Test explicit settings override system theme."""
    mock_settings.get.return_value = "dark"
    assert theme_manager.get_effective_theme_mode() == "dark"

    mock_settings.get.return_value = "light"
    assert theme_manager.get_effective_theme_mode() == "light"


@patch("iPhotos.src.iPhoto.gui.ui.theme_manager.QGuiApplication")
def test_get_effective_theme_mode_system(mock_qgui_app, theme_manager, mock_settings):
    """Test system theme detection when setting is 'system'."""
    mock_settings.get.return_value = "system"

    # Mock System to be Dark
    mock_inst = MagicMock()
    mock_qgui_app.instance.return_value = mock_inst
    mock_inst.styleHints().colorScheme.return_value = Qt.ColorScheme.Dark

    assert theme_manager.get_effective_theme_mode() == "dark"

    # Mock System to be Light
    mock_inst.styleHints().colorScheme.return_value = Qt.ColorScheme.Light
    assert theme_manager.get_effective_theme_mode() == "light"


def test_apply_theme_signal(theme_manager, mock_settings):
    """Test apply_theme updates colors and emits signal."""
    # Force "dark" via settings
    mock_settings.get.return_value = "dark"

    with patch.object(theme_manager, "themeChanged") as mock_signal:
        theme_manager.apply_theme()

        assert theme_manager.current_colors() == DARK_THEME
        mock_signal.emit.assert_called_with(True)  # True for dark

    # Force "light" via settings
    mock_settings.get.return_value = "light"

    with patch.object(theme_manager, "themeChanged") as mock_signal:
        theme_manager.apply_theme()

        assert theme_manager.current_colors() == LIGHT_THEME
        mock_signal.emit.assert_called_with(False)  # False for light


def test_set_force_dark(theme_manager, mock_settings):
    """Test forcing dark mode overrides light setting."""
    # Setup: User prefers Light
    mock_settings.get.return_value = "light"
    theme_manager.apply_theme()
    assert theme_manager.current_colors() == LIGHT_THEME

    # Enable force dark
    theme_manager.set_force_dark(True)
    assert theme_manager._force_dark_mode is True
    assert theme_manager.current_colors() == DARK_THEME

    # Disable force dark
    theme_manager.set_force_dark(False)
    assert theme_manager._force_dark_mode is False
    assert theme_manager.current_colors() == LIGHT_THEME


def test_on_settings_changed(theme_manager, mock_settings):
    """Test that settings changes trigger theme updates."""
    # Verify signal connection
    mock_settings.settingsChanged.connect.assert_called()

    # Manually trigger the callback
    # We want to switch to dark
    mock_settings.get.return_value = "dark"

    with patch.object(theme_manager, "apply_theme") as mock_apply:
        theme_manager._on_settings_changed("ui.theme", "dark")
        mock_apply.assert_called_once()

    # Irrelevant setting shouldn't trigger apply
    with patch.object(theme_manager, "apply_theme") as mock_apply:
        theme_manager._on_settings_changed("some.other.setting", 123)
        mock_apply.assert_not_called()


def test_base_colors(theme_manager, mock_settings):
    """Test that base_colors ignores force_dark."""
    mock_settings.get.return_value = "light"
    theme_manager.set_force_dark(True)

    # Current colors should be dark (because forced)
    assert theme_manager.current_colors() == DARK_THEME

    # Base colors should remain light (user preference)
    assert theme_manager.base_colors() == LIGHT_THEME


@patch("iPhotos.src.iPhoto.gui.ui.theme_manager.QGuiApplication")
def test_apply_palette(mock_qgui_app, theme_manager, mock_settings):
    """Test that QPalette is set on the application."""
    mock_inst = MagicMock()
    mock_qgui_app.instance.return_value = mock_inst

    mock_settings.get.return_value = "dark"
    theme_manager.apply_theme()

    mock_inst.setPalette.assert_called_once()
    args, _ = mock_inst.setPalette.call_args
    palette = args[0]
    assert isinstance(palette, QPalette)
    # Check a color to confirm it's from DARK_THEME
    # We can check Window color role
    assert palette.color(QPalette.ColorRole.Window) == DARK_THEME.window_background

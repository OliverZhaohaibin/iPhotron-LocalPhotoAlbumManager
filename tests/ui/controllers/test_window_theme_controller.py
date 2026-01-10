"""Unit tests for WindowThemeController."""

from unittest.mock import MagicMock
import pytest

from PySide6.QtCore import QObject
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QWidget, QToolButton, QLabel, QPushButton

from src.iPhoto.gui.ui.controllers.window_theme_controller import WindowThemeController
from src.iPhoto.gui.ui.theme_manager import ThemeManager, LIGHT_THEME, DARK_THEME
from src.iPhoto.gui.ui.widgets.collapsible_section import CollapsibleSection
from src.iPhoto.gui.ui.widgets.gallery_grid_view import GalleryQuickWidget
from src.iPhoto.gui.ui.window_manager import RoundedWindowShell

# Mock load_icon globally to avoid disk/resource access and crashes during tests
import sys
sys.modules["src.iPhoto.gui.ui.icon"] = MagicMock()
sys.modules["src.iPhoto.gui.ui.icon"].load_icon = MagicMock()
sys.modules["src.iPhoto.gui.ui.icons"] = MagicMock()
sys.modules["src.iPhoto.gui.ui.icons"].load_icon = MagicMock()
# Also mock the relative import inside window_theme_controller
from src.iPhoto.gui.ui.controllers import window_theme_controller as wtc_module
wtc_module.load_icon = MagicMock()


class StubUi(QObject):
    """Stub for Ui_MainWindow, exposing widget mocks for verification."""

    def __init__(self):
        super().__init__()

        # Helper to create styled widgets
        def make_widget(name, cls=QWidget):
            w = MagicMock(spec=cls)
            w.objectName.return_value = name
            w.palette.return_value = QPalette()
            return w

        # --- Window Chrome ---
        self.window_shell = make_widget("windowShell")
        # parentWidget for window_shell is tricky to mock directly on the shell attr,
        # so we handle it by mocking the actual parent linkage or passing it in tests.
        # But controller does: shell_parent = ui.window_shell.parentWidget()
        # So we need self.window_shell.parentWidget() to return the RoundedWindowShell mock.

        self.window_chrome = make_widget("windowChrome")
        self.title_bar = make_widget("windowTitleBar")
        self.title_separator = make_widget("windowTitleSeparator")
        self.menu_bar_container = make_widget("menuBarContainer")
        self.window_title_label = make_widget("windowTitleLabel", QLabel)
        self.rescan_button = make_widget("rescanButton", QToolButton)
        self.selection_button = make_widget("selectionButton", QToolButton)
        self.status_bar = make_widget("chromeStatusBar")

        # --- Sidebar ---
        self.sidebar = make_widget("albumSidebar")

        # --- Detail/Edit View ---
        self.detail_page = MagicMock()
        self.detail_page.edit_container = make_widget("editPage")

        self.image_viewer = MagicMock()
        self.image_viewer.set_surface_color_override = MagicMock()

        self.edit_button = make_widget("editButton", QPushButton)

        # --- Icons / Toolbar ---
        self.edit_sidebar = make_widget("editSidebar")
        self.edit_sidebar.findChildren = MagicMock(return_value=[])
        # Add the method that is called by the controller but missing from QWidget
        self.edit_sidebar.set_control_icon_tint = MagicMock()

        self.zoom_out_button = make_widget("zoomOutButton", QToolButton)
        self.zoom_in_button = make_widget("zoomInButton", QToolButton)
        self.back_button = make_widget("backButton", QToolButton)
        self.edit_compare_button = make_widget("editCompareButton", QToolButton)

        self.info_button = make_widget("infoButton", QToolButton)
        self.favorite_button = make_widget("favoriteButton", QToolButton)
        self.share_button = make_widget("shareButton", QToolButton)
        self.rotate_left_button = make_widget("rotateLeftButton", QToolButton)


@pytest.fixture
def mock_theme_manager():
    """Create a mocked ThemeManager."""
    manager = MagicMock(spec=ThemeManager)
    manager.current_colors.return_value = LIGHT_THEME
    manager.base_colors.return_value = LIGHT_THEME
    manager.themeChanged = MagicMock()
    return manager


@pytest.fixture
def mock_window_shell():
    """Mock the RoundedWindowShell."""
    shell = MagicMock(spec=RoundedWindowShell)
    shell.corner_radius.return_value = 10
    shell.palette.return_value = QPalette()
    return shell


@pytest.fixture
def window_theme_controller(mock_theme_manager, mock_window_shell):
    """Create a WindowThemeController with mocks."""
    ui = StubUi()

    # Link window shell parent
    ui.window_shell.parentWidget.return_value = mock_window_shell

    # Mock window object
    # QObject parent must be QObject or None, not MagicMock.
    # Pass None as parent for testing.
    window_obj = MagicMock()
    window_obj.window_manager = MagicMock()

    controller = WindowThemeController(ui, None, mock_theme_manager)
    # Inject our mock window explicitly since we passed None to super()
    controller._window = window_obj

    return controller, ui, window_obj


def test_initialization(window_theme_controller, mock_theme_manager):
    """Test controller connects to theme manager and applies initial colors."""
    controller, ui, _ = window_theme_controller

    # Verify signal connection
    mock_theme_manager.themeChanged.connect.assert_called_with(controller._on_theme_changed)

    # Verify initial application (we mocked LIGHT_THEME)
    # Check a few distinct stylesheets to confirm _apply_colors ran

    # 1. Sidebar background should match LIGHT_THEME.sidebar_background
    expected_bg = LIGHT_THEME.sidebar_background.name()
    assert f"background-color: {expected_bg}" in ui.sidebar.setStyleSheet.call_args[0][0]

    # 2. Window title label color
    expected_fg = LIGHT_THEME.text_primary.name()
    assert f"color: {expected_fg}" in ui.window_title_label.setStyleSheet.call_args[0][0]


def test_theme_change_handler(window_theme_controller, mock_theme_manager):
    """Test that _on_theme_changed re-applies colors."""
    controller, ui, _ = window_theme_controller

    # Switch to Dark Theme
    mock_theme_manager.current_colors.return_value = DARK_THEME

    # Trigger the handler
    controller._on_theme_changed(True)

    # Check if updated to dark colors
    expected_bg = DARK_THEME.sidebar_background.name()
    stylesheet_arg = ui.sidebar.setStyleSheet.call_args[0][0]
    assert f"background-color: {expected_bg}" in stylesheet_arg


def test_apply_edit_theme(window_theme_controller, mock_theme_manager):
    """Test forcing edit mode (dark mode)."""
    controller, _, _ = window_theme_controller

    controller.apply_edit_theme()
    mock_theme_manager.set_force_dark.assert_called_with(True)


def test_restore_global_theme(window_theme_controller, mock_theme_manager):
    """Test restoring global theme."""
    controller, _, _ = window_theme_controller

    controller.restore_global_theme()
    mock_theme_manager.set_force_dark.assert_called_with(False)


def test_set_detail_ui_controller_updates_icons(window_theme_controller, mock_theme_manager):
    """Test that setting detail controller refreshes icon tints."""
    controller, ui, _ = window_theme_controller
    mock_detail_controller = MagicMock()

    # Set the detail controller
    controller.set_detail_ui_controller(mock_detail_controller)

    # Verify it stored the controller
    assert controller._detail_ui_controller == mock_detail_controller

    # Verify it called set_toolbar_icon_tint on the detail controller
    # current_colors is LIGHT_THEME, so text_primary is used
    mock_detail_controller.set_toolbar_icon_tint.assert_called_with(LIGHT_THEME.text_primary)


def test_get_shell_animation_colors_entering(window_theme_controller, mock_theme_manager, mock_window_shell):
    """Test animation colors when entering edit mode."""
    controller, _, _ = window_theme_controller

    # Scenario: Current theme is LIGHT
    mock_theme_manager.base_colors.return_value = LIGHT_THEME

    shell, start_color, end_color = controller.get_shell_animation_colors(entering=True)

    assert shell == mock_window_shell
    assert start_color == LIGHT_THEME.window_background
    assert end_color == DARK_THEME.window_background


def test_get_shell_animation_colors_exiting(window_theme_controller, mock_theme_manager, mock_window_shell):
    """Test animation colors when exiting edit mode."""
    controller, _, _ = window_theme_controller

    # Scenario: Current theme is LIGHT
    mock_theme_manager.base_colors.return_value = LIGHT_THEME

    shell, start_color, end_color = controller.get_shell_animation_colors(entering=False)

    assert shell == mock_window_shell
    assert start_color == DARK_THEME.window_background
    assert end_color == LIGHT_THEME.window_background


def test_ui_component_styling(window_theme_controller, mock_theme_manager):
    """Verify specific UI components receive correct stylesheets."""
    controller, ui, _ = window_theme_controller

    # Using LIGHT_THEME
    colors = LIGHT_THEME
    fg = colors.text_primary.name()
    bg = colors.window_background.name()
    sidebar_bg = colors.sidebar_background.name()

    # 1. Status Bar
    status_style = ui.status_bar.setStyleSheet.call_args[0][0]
    assert "QWidget#chromeStatusBar" in status_style
    assert f"color: {fg}" in status_style

    # 2. Window Chrome
    chrome_style = ui.window_chrome.setStyleSheet.call_args[0][0]
    assert f"color: {fg}" in chrome_style

    # 3. Edit Container
    # Edit container styles are complex, verify key parts
    edit_style = ui.detail_page.edit_container.setStyleSheet.call_args[0][0]
    assert f"background-color: {bg}" in edit_style
    # Should use sidebar_background for panels
    assert f"background-color: {sidebar_bg}" in edit_style

    # 4. Image Viewer Surface
    # In Light Mode, surface should be window background
    ui.image_viewer.set_surface_color_override.assert_called_with(bg)


def test_icon_tinting(window_theme_controller, mock_theme_manager):
    """Test that icons are re-tinted."""
    controller, ui, _ = window_theme_controller

    # Mock some collapsible sections in edit sidebar
    section1 = MagicMock(spec=CollapsibleSection)
    section1._icon_label = MagicMock()
    section1._icon_name = "adjust.svg"

    section2 = MagicMock(spec=CollapsibleSection)
    # section without icon components to test safety

    ui.edit_sidebar.findChildren.return_value = [section1, section2]

    # Re-trigger update
    controller._update_icon_tints(LIGHT_THEME)

    # Verify section tinting
    section1.set_toggle_icon_tint.assert_called_with(LIGHT_THEME.text_primary)

    # Verify main toolbar icons
    # Since load_icon is mocked internally or we rely on setIcon calls
    # We just check setIcon was called
    ui.zoom_out_button.setIcon.assert_called()
    ui.zoom_in_button.setIcon.assert_called()
    ui.back_button.setIcon.assert_called()


def test_refresh_menu_style(window_theme_controller):
    """Test that menu styles are refreshed via WindowManager."""
    controller, _, window_obj = window_theme_controller

    # The initialization calls _apply_colors, which calls _refresh_menu_styles.
    # We verify that it was called during init.
    # We can also call it explicitly to be sure.
    controller._refresh_menu_styles()

    window_obj.window_manager._apply_menu_styles.assert_called()


def test_sidebar_palette_application(window_theme_controller, mock_theme_manager):
    """Test that the sidebar palette is correctly configured."""
    controller, ui, _ = window_theme_controller

    colors = LIGHT_THEME

    # Verify setPalette was called on sidebar
    ui.sidebar.setPalette.assert_called()

    # Get the palette passed to the mock
    palette = ui.sidebar.setPalette.call_args[0][0]

    # Since we use a real QPalette in the stub, we can check it,
    # but our StubUi returns a NEW mock palette every time palette() is called
    # unless we manipulate the mock behavior more deeply.
    # However, in _apply_colors:
    # sidebar_palette = self._ui.sidebar.palette()  <-- returns Mock or QPalette
    # sidebar_palette.setColor(...)
    # self._ui.sidebar.setPalette(sidebar_palette)

    # In StubUi we did: w.palette.return_value = QPalette()
    # So the controller modified that specific QPalette instance.

    # Let's verify the Highlight color was set to the constant
    from src.iPhoto.gui.ui.palette import SIDEBAR_SELECTED_BACKGROUND, SIDEBAR_ICON_COLOR

    # We need to capture the palette that was set
    assert palette.color(QPalette.ColorRole.Highlight) == SIDEBAR_SELECTED_BACKGROUND
    assert palette.color(QPalette.ColorRole.HighlightedText) == colors.sidebar_text
    assert palette.color(QPalette.ColorRole.Link) == SIDEBAR_ICON_COLOR


def test_window_shell_palette_update(window_theme_controller, mock_window_shell):
    """Test that the window shell palette is updated."""
    controller, ui, _ = window_theme_controller

    # Verify window shell palette update
    ui.window_shell.setPalette.assert_called()
    shell_palette = ui.window_shell.setPalette.call_args[0][0]
    assert shell_palette.color(QPalette.ColorRole.Window) == LIGHT_THEME.window_background

    # Verify rounded shell palette update
    mock_window_shell.setPalette.assert_called()
    rounded_palette = mock_window_shell.setPalette.call_args[0][0]
    assert rounded_palette.color(QPalette.ColorRole.Window) == LIGHT_THEME.window_background

    # Verify override color
    mock_window_shell.set_override_color.assert_called_with(LIGHT_THEME.window_background)


def test_gallery_grid_view_theme_application(window_theme_controller, mock_theme_manager):
    """Test that GalleryQuickWidget theme is applied correctly."""
    controller, ui, _ = window_theme_controller
    
    # Create a mock GalleryQuickWidget
    mock_grid_view = MagicMock(spec=GalleryQuickWidget)
    ui.grid_view = mock_grid_view
    
    # Trigger theme application
    controller._apply_colors(LIGHT_THEME)
    
    # Verify apply_theme was called with correct colors
    mock_grid_view.apply_theme.assert_called_with(LIGHT_THEME)


def test_gallery_grid_view_attribute_check(window_theme_controller, mock_theme_manager):
    """Test that code correctly checks for grid_view attribute."""
    controller, ui, _ = window_theme_controller
    
    # Case 1: grid_view attribute doesn't exist
    if hasattr(ui, 'grid_view'):
        delattr(ui, 'grid_view')
    
    # Should not raise an exception
    controller._apply_colors(LIGHT_THEME)
    
    # Case 2: grid_view is not a GalleryQuickWidget
    ui.grid_view = MagicMock()  # Not a GalleryQuickWidget
    controller._apply_colors(LIGHT_THEME)
    
    # Verify apply_theme was not called (since it's not a GalleryQuickWidget)
    if hasattr(ui.grid_view, 'apply_theme'):
        assert not ui.grid_view.apply_theme.called


def test_gallery_grid_view_theme_on_theme_change(window_theme_controller, mock_theme_manager):
    """Test that GalleryQuickWidget theme updates when theme changes."""
    controller, ui, _ = window_theme_controller
    
    # Create a mock GalleryQuickWidget
    mock_grid_view = MagicMock(spec=GalleryQuickWidget)
    ui.grid_view = mock_grid_view
    
    # Switch to dark theme
    mock_theme_manager.current_colors.return_value = DARK_THEME
    controller._on_theme_changed(True)
    
    # Verify apply_theme was called with dark theme colors
    # The call_args_list will have multiple calls, we need the most recent one
    calls = mock_grid_view.apply_theme.call_args_list
    assert len(calls) > 0
    # Check that one of the calls used DARK_THEME
    assert any(call[0][0] == DARK_THEME for call in calls)


def test_gallery_grid_view_isinstance_check(window_theme_controller, mock_theme_manager):
    """Test that code correctly uses isinstance check for GalleryQuickWidget."""
    controller, ui, _ = window_theme_controller
    
    # Create a real-looking mock that passes isinstance check
    mock_grid_view = MagicMock(spec=GalleryQuickWidget)
    mock_grid_view.__class__ = GalleryQuickWidget
    ui.grid_view = mock_grid_view
    
    # Apply colors
    controller._apply_colors(LIGHT_THEME)
    
    # Verify the method was called
    mock_grid_view.apply_theme.assert_called_with(LIGHT_THEME)

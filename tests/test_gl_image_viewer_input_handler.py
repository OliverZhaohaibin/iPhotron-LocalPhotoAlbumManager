"""
Unit tests for gl_image_viewer input event handler.

Tests the input event routing logic without requiring Qt GUI infrastructure.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, Mock


# Direct module import to avoid Qt dependencies in test environment
# Add project root (parent of 'src') to sys.path for package imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Mock Qt modules before importing
sys.modules['PySide6'] = MagicMock()
sys.modules['PySide6.QtCore'] = MagicMock()
sys.modules['PySide6.QtGui'] = MagicMock()

from iPhoto.gui.ui.widgets.gl_image_viewer import input_handler  # noqa: E402

InputEventHandler = input_handler.InputEventHandler


class TestInputEventHandler:
    """Test input event routing."""

    def setup_method(self):
        """Set up test fixtures."""
        self.crop_controller = Mock()
        self.transform_controller = Mock()
        self.on_replay = Mock()
        self.on_fullscreen_exit = Mock()
        self.on_fullscreen_toggle = Mock()
        self.on_cancel_crop_lock = Mock()
        
        self.handler = InputEventHandler(
            crop_controller=self.crop_controller,
            transform_controller=self.transform_controller,
            on_replay_requested=self.on_replay,
            on_fullscreen_exit=self.on_fullscreen_exit,
            on_fullscreen_toggle=self.on_fullscreen_toggle,
            on_cancel_auto_crop_lock=self.on_cancel_crop_lock,
        )

    def test_routes_to_crop_when_active(self):
        """Events should route to crop controller when crop mode is active."""
        self.crop_controller.is_active.return_value = True
        
        event = Mock()
        event.button.return_value = 1  # Qt.LeftButton
        
        result = self.handler.handle_mouse_press(event)
        
        assert result is True
        self.crop_controller.handle_mouse_press.assert_called_once_with(event)
        self.transform_controller.handle_mouse_press.assert_not_called()

    def test_routes_to_transform_when_crop_inactive(self):
        """Events should route to transform controller when crop is inactive."""
        self.crop_controller.is_active.return_value = False
        
        event = Mock()
        event.button.return_value = 1  # Qt.LeftButton
        
        result = self.handler.handle_mouse_press(event)
        
        assert result is False
        self.on_cancel_crop_lock.assert_called_once()
        self.transform_controller.handle_mouse_press.assert_called_once_with(event)

    def test_replay_mode_triggers_callback(self):
        """Left click in replay mode should trigger replay callback."""
        self.crop_controller.is_active.return_value = False
        self.handler.set_live_replay_enabled(True)
        
        event = Mock()
        event.button.return_value = 1  # Qt.LeftButton
        
        self.handler.handle_mouse_press(event)
        
        self.on_replay.assert_called_once()
        self.transform_controller.handle_mouse_press.assert_not_called()

    def test_mouse_move_routes_to_crop_when_active(self):
        """Mouse move should route to crop controller when active."""
        self.crop_controller.is_active.return_value = True
        
        event = Mock()
        result = self.handler.handle_mouse_move(event)
        
        assert result is True
        self.crop_controller.handle_mouse_move.assert_called_once_with(event)

    def test_mouse_move_routes_to_transform_when_inactive(self):
        """Mouse move should route to transform controller when crop inactive."""
        self.crop_controller.is_active.return_value = False
        self.handler.set_live_replay_enabled(False)
        
        event = Mock()
        result = self.handler.handle_mouse_move(event)
        
        assert result is False
        self.transform_controller.handle_mouse_move.assert_called_once_with(event)

    def test_wheel_routes_to_crop_when_active(self):
        """Wheel events should route to crop controller when active."""
        self.crop_controller.is_active.return_value = True
        
        event = Mock()
        self.handler.handle_wheel(event)
        
        self.crop_controller.handle_wheel.assert_called_once_with(event)
        self.transform_controller.handle_wheel.assert_not_called()

    def test_wheel_cancels_crop_lock_when_inactive(self):
        """Wheel events should cancel crop lock when crop inactive."""
        self.crop_controller.is_active.return_value = False
        
        event = Mock()
        self.handler.handle_wheel(event)
        
        self.on_cancel_crop_lock.assert_called_once()
        self.transform_controller.handle_wheel.assert_called_once_with(event)

    def test_double_click_with_fullscreen_window(self):
        """Double-click should exit fullscreen when window is fullscreen."""
        event = Mock()
        event.button.return_value = 1
        
        window = Mock()
        window.isFullScreen.return_value = True
        
        result = self.handler.handle_double_click_with_window(event, window)
        
        assert result is True
        self.on_fullscreen_exit.assert_called_once()

    def test_double_click_with_normal_window(self):
        """Double-click should toggle fullscreen when window is normal."""
        event = Mock()
        event.button.return_value = 1
        
        window = Mock()
        window.isFullScreen.return_value = False
        
        result = self.handler.handle_double_click_with_window(event, window)
        
        assert result is True
        self.on_fullscreen_toggle.assert_called_once()

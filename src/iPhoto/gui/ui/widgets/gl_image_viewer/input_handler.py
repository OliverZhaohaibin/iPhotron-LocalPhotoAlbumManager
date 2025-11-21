"""
Input event routing for GL image viewer.

This module handles the dispatching of mouse and wheel events to the appropriate
controllers (crop or transform) based on the current viewer state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QMouseEvent, QWheelEvent

# Qt.LeftButton constant
_LEFT_BUTTON = Qt.LeftButton if hasattr(Qt, 'LeftButton') else 1

if TYPE_CHECKING:
    from ..gl_crop_controller import CropInteractionController
    from ..view_transform_controller import ViewTransformController


class InputEventHandler:
    """Routes input events to appropriate controllers based on viewer state.
    
    This handler follows a priority-based dispatch strategy:
    1. If crop mode is active, route events to crop controller
    2. If live replay is enabled, route left-click to replay
    3. Otherwise, route to transform controller
    
    Parameters
    ----------
    crop_controller:
        Controller for crop interaction handling
    transform_controller:
        Controller for pan/zoom transformations
    on_replay_requested:
        Callback to invoke when replay is requested
    on_fullscreen_exit:
        Callback to invoke when fullscreen exit is requested
    on_fullscreen_toggle:
        Callback to invoke when fullscreen toggle is requested
    on_cancel_auto_crop_lock:
        Callback to cancel auto-crop view locking
    """
    
    def __init__(
        self,
        crop_controller: CropInteractionController,
        transform_controller: ViewTransformController,
        *,
        on_replay_requested: callable,
        on_fullscreen_exit: callable,
        on_fullscreen_toggle: callable,
        on_cancel_auto_crop_lock: callable,
    ) -> None:
        self._crop_controller = crop_controller
        self._transform_controller = transform_controller
        self._on_replay_requested = on_replay_requested
        self._on_fullscreen_exit = on_fullscreen_exit
        self._on_fullscreen_toggle = on_fullscreen_toggle
        self._on_cancel_auto_crop_lock = on_cancel_auto_crop_lock
        self._live_replay_enabled = False
    
    def set_live_replay_enabled(self, enabled: bool) -> None:
        """Enable or disable live replay mode."""
        self._live_replay_enabled = bool(enabled)
    
    def handle_mouse_press(self, event: QMouseEvent) -> bool:
        """Handle mouse press events.
        
        Returns
        -------
        bool
            True if the event was handled, False if it should propagate to parent
        """
        button = event.button()
        if self._crop_controller.is_active() and button == _LEFT_BUTTON:
            self._crop_controller.handle_mouse_press(event)
            return True
        
        if button == _LEFT_BUTTON:
            if self._live_replay_enabled:
                self._on_replay_requested()
            else:
                self._on_cancel_auto_crop_lock()
                self._transform_controller.handle_mouse_press(event)
        
        return False
    
    def handle_mouse_move(self, event: QMouseEvent) -> bool:
        """Handle mouse move events.
        
        Returns
        -------
        bool
            True if the event was handled, False if it should propagate to parent
        """
        if self._crop_controller.is_active():
            self._crop_controller.handle_mouse_move(event)
            return True
        
        if not self._live_replay_enabled:
            self._transform_controller.handle_mouse_move(event)
        
        return False
    
    def handle_mouse_release(self, event: QMouseEvent) -> bool:
        """Handle mouse release events.
        
        Returns
        -------
        bool
            True if the event was handled, False if it should propagate to parent
        """
        if self._crop_controller.is_active() and event.button() == _LEFT_BUTTON:
            self._crop_controller.handle_mouse_release(event)
            return True
        
        if not self._live_replay_enabled:
            self._transform_controller.handle_mouse_release(event)
        
        return False
    
    def handle_double_click(self, event: QMouseEvent) -> bool:
        """Handle mouse double-click events.
        
        Returns
        -------
        bool
            True if the event was handled (and should be accepted)
        """
        if event.button() == Qt.LeftButton:
            # Check if we're in fullscreen mode by examining the top-level window
            # This requires access to the widget, which we'll need to pass
            # For now, return False to indicate this needs widget context
            return False
        return False
    
    def handle_double_click_with_window(
        self, event: QMouseEvent, window
    ) -> bool:
        """Handle double-click with window context for fullscreen detection.
        
        Returns
        -------
        bool
            True if the event was handled and accepted
        """
        if event.button() == _LEFT_BUTTON:
            if window is not None and window.isFullScreen():
                self._on_fullscreen_exit()
            else:
                self._on_fullscreen_toggle()
            return True
        return False
    
    def handle_wheel(self, event: QWheelEvent) -> None:
        """Handle wheel events."""
        if self._crop_controller.is_active():
            self._crop_controller.handle_wheel(event)
            return
        
        self._on_cancel_auto_crop_lock()
        self._transform_controller.handle_wheel(event)

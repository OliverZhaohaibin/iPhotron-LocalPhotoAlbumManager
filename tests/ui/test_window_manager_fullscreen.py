"""Regression tests for Playback fullscreen state convergence."""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QEvent

from iPhoto.gui.ui.window_manager import FramelessWindowManager


def test_reconcile_native_exit_finishes_playback_without_requesting_window_change() -> None:
    manager = FramelessWindowManager.__new__(FramelessWindowManager)
    manager._immersive_active = True
    manager._window = MagicMock()
    manager._window.isFullScreen.return_value = False
    manager._finish_immersive_exit = MagicMock()

    manager._reconcile_playback_fullscreen_state()

    manager._finish_immersive_exit.assert_called_once_with(request_window_change=False)


def test_window_state_change_defers_playback_reconciliation() -> None:
    manager = FramelessWindowManager.__new__(FramelessWindowManager)
    manager._reconcile_playback_fullscreen_state = MagicMock()
    event = MagicMock(spec=QEvent)
    event.type.return_value = QEvent.Type.WindowStateChange

    with patch("iPhoto.gui.ui.window_manager.QTimer.singleShot") as single_shot:
        manager.handle_change_event(event)

    single_shot.assert_called_once_with(0, manager._reconcile_playback_fullscreen_state)


def test_reconcile_does_not_adopt_non_playback_fullscreen() -> None:
    manager = FramelessWindowManager.__new__(FramelessWindowManager)
    manager._immersive_active = False
    manager._window = MagicMock()
    manager._window.isFullScreen.return_value = True
    manager._finish_immersive_exit = MagicMock()

    manager._reconcile_playback_fullscreen_state()

    manager._finish_immersive_exit.assert_not_called()


def test_native_exit_restores_playback_without_calling_show_normal() -> None:
    manager = FramelessWindowManager.__new__(FramelessWindowManager)
    manager._immersive_active = True
    manager._detail_coordinator = MagicMock()
    manager._detail_coordinator.suspend_playback_for_transition.return_value = False
    manager._window = MagicMock()
    manager._window.updatesEnabled.return_value = True
    manager._ui = MagicMock()
    manager._splitter_sizes = [200, 800]
    manager._hidden_widget_states = []
    manager._video_controls_enabled_before = False
    manager._restore_default_backdrop = MagicMock()
    manager._update_fullscreen_button_icon = MagicMock()
    manager._schedule_playback_header_shadow_restore = MagicMock()
    manager._schedule_playback_resume = MagicMock()

    manager._finish_immersive_exit(request_window_change=False)

    assert manager._immersive_active is False
    manager._restore_default_backdrop.assert_called_once_with()
    manager._window.showNormal.assert_not_called()
    manager._window.restoreGeometry.assert_not_called()
    manager._window.setWindowState.assert_not_called()
    manager._ui.splitter.setSizes.assert_called_once_with([200, 800])
    manager._schedule_playback_header_shadow_restore.assert_called_once_with()


def test_stale_shadow_restore_is_ignored_after_fullscreen_reentry() -> None:
    manager = FramelessWindowManager.__new__(FramelessWindowManager)
    manager._immersive_active = False
    manager._shadow_restore_generation = 0
    manager._window = MagicMock()
    manager._window.isFullScreen.return_value = False
    manager._set_playback_header_shadow_suppressed = MagicMock()
    callbacks: list[Callable[[], None]] = []

    with patch(
        "iPhoto.gui.ui.window_manager.QTimer.singleShot",
        side_effect=lambda _delay, callback: callbacks.append(callback),
    ):
        manager._schedule_playback_header_shadow_restore()
        manager._suppress_playback_header_shadow()

    manager._set_playback_header_shadow_suppressed.reset_mock()
    callbacks[0]()

    manager._set_playback_header_shadow_suppressed.assert_not_called()

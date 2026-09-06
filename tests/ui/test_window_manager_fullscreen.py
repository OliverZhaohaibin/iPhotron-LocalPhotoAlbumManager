"""Regression tests for Playback fullscreen state convergence."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
from unittest.mock import MagicMock, call, patch

from PySide6.QtCore import QEvent

from iPhoto.gui.ui.window_manager import FramelessWindowManager


def _make_windows_enter_manager() -> FramelessWindowManager:
    manager = FramelessWindowManager.__new__(FramelessWindowManager)
    manager._window = MagicMock()
    manager._window.updatesEnabled.return_value = True
    manager._window.isFullScreen.return_value = False
    manager._ui = MagicMock()
    manager._ui.splitter.signalsBlocked.return_value = False
    manager._ui.video_area.controls_enabled.return_value = True
    manager._immersive_visibility_targets = (MagicMock(),)
    manager._splitter_sizes = [200, 800]
    manager._hidden_widget_states = []
    manager._immersive_active = False
    manager._fullscreen_transition_generation = 0
    manager._fullscreen_enter_pending = None
    manager._fullscreen_enter_updates_enabled_before = None
    manager._fullscreen_enter_resume = False
    manager._fullscreen_enter_timeout_timer = None
    manager._fullscreen_transition_backend = "unknown"
    manager._suppress_playback_header_shadow = MagicMock()
    manager._override_visibility = MagicMock(return_value=[])
    manager._apply_immersive_backdrop = MagicMock()
    manager._emit_fullscreen_transition_event = MagicMock()
    manager._start_fullscreen_enter_timeout = MagicMock()
    return manager


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
    manager._ui.image_viewer.request_viewport_relayout.assert_called_once_with(reset_view=True)
    manager._ui.video_area.request_viewport_relayout.assert_called_once_with(reset_view=True)
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


def test_windows_enter_suppresses_updates_before_visible_mutations() -> None:
    manager = _make_windows_enter_manager()
    events: list[str] = []
    manager._window.setUpdatesEnabled.side_effect = lambda enabled: events.append(
        f"updates:{enabled}"
    )
    manager._suppress_playback_header_shadow.side_effect = lambda: events.append("shadow")
    manager._override_visibility.side_effect = lambda *_args, **_kwargs: (
        events.append("visibility") or []
    )
    manager._apply_immersive_backdrop.side_effect = lambda: events.append("backdrop")
    manager._window.showFullScreen.side_effect = lambda: events.append("fullscreen")

    with patch(
        "iPhoto.gui.ui.window_manager.selected_rhi_backend_name",
        return_value="opengl",
    ):
        manager._begin_windows_fullscreen_enter(True)

    assert events == [
        "updates:False",
        "shadow",
        "visibility",
        "backdrop",
        "fullscreen",
    ]
    assert manager._fullscreen_enter_pending == 1
    assert manager._fullscreen_enter_resume is True
    assert manager._fullscreen_transition_backend == "opengl"
    assert manager._immersive_active is True
    manager._window.update.assert_not_called()
    manager._start_fullscreen_enter_timeout.assert_called_once_with(1)
    assert manager._ui.splitter.blockSignals.call_args_list == [call(True), call(False)]


def test_windows_enter_completes_once_after_fullscreen_confirmation() -> None:
    manager = _make_windows_enter_manager()
    events: list[str] = []
    manager._fullscreen_enter_pending = 7
    manager._fullscreen_enter_updates_enabled_before = True
    manager._fullscreen_enter_resume = True
    manager._immersive_active = True
    manager._window.isFullScreen.return_value = True
    manager._cancel_fullscreen_enter_timeout = MagicMock()
    manager._request_media_viewport_relayout = MagicMock()
    manager._update_fullscreen_button_icon = MagicMock()
    manager._schedule_playback_resume = MagicMock()
    manager._request_media_viewport_relayout.side_effect = lambda: events.append("relayout")
    manager._update_fullscreen_button_icon.side_effect = lambda: events.append("icon")
    manager._window.setUpdatesEnabled.side_effect = lambda enabled: events.append(
        f"updates:{enabled}"
    )
    manager._window.update.side_effect = lambda: events.append("update")

    manager._reconcile_playback_fullscreen_state()
    manager._reconcile_playback_fullscreen_state()

    assert manager._fullscreen_enter_pending is None
    manager._cancel_fullscreen_enter_timeout.assert_called_once_with()
    manager._request_media_viewport_relayout.assert_called_once_with()
    manager._window.setUpdatesEnabled.assert_called_once_with(True)
    manager._window.update.assert_called_once_with()
    manager._update_fullscreen_button_icon.assert_called_once_with()
    manager._schedule_playback_resume.assert_called_once_with(
        expect_immersive=True,
        resume=True,
        transition_id=7,
    )
    assert events == ["relayout", "icon", "updates:True", "update"]


def test_windows_enter_ignores_stale_timeout() -> None:
    manager = _make_windows_enter_manager()
    manager._fullscreen_enter_pending = 8
    manager._complete_windows_fullscreen_enter = MagicMock()
    manager._finish_immersive_exit = MagicMock()

    manager._handle_fullscreen_enter_timeout(7)

    manager._complete_windows_fullscreen_enter.assert_not_called()
    manager._finish_immersive_exit.assert_not_called()


def test_windows_enter_timeout_completes_when_native_state_landed() -> None:
    manager = _make_windows_enter_manager()
    manager._fullscreen_enter_pending = 8
    manager._window.isFullScreen.return_value = True
    manager._complete_windows_fullscreen_enter = MagicMock()

    manager._handle_fullscreen_enter_timeout(8)

    manager._complete_windows_fullscreen_enter.assert_called_once_with(8)


def test_windows_enter_timeout_rolls_back_when_native_state_failed() -> None:
    manager = _make_windows_enter_manager()
    manager._fullscreen_enter_pending = 8
    manager._window.isFullScreen.return_value = False
    manager._finish_immersive_exit = MagicMock()

    manager._handle_fullscreen_enter_timeout(8)

    manager._finish_immersive_exit.assert_called_once_with(request_window_change=True)


def test_exit_during_pending_windows_enter_restores_updates_once() -> None:
    manager = _make_windows_enter_manager()
    manager._immersive_active = True
    manager._fullscreen_enter_pending = 9
    manager._fullscreen_enter_updates_enabled_before = True
    manager._fullscreen_enter_resume = True
    manager._detail_coordinator = MagicMock()
    manager._edit_controller = MagicMock(return_value=None)
    manager._window.updatesEnabled.return_value = False
    manager._previous_geometry = MagicMock()
    manager._previous_window_state = MagicMock()
    manager._hidden_widget_states = [(MagicMock(), True)]
    manager._video_controls_enabled_before = True
    manager._restore_default_backdrop = MagicMock()
    manager._cancel_fullscreen_enter_timeout = MagicMock()
    manager._update_fullscreen_button_icon = MagicMock()
    manager._request_media_viewport_relayout = MagicMock()
    manager._schedule_playback_header_shadow_restore = MagicMock()
    manager._schedule_playback_resume = MagicMock()
    manager._ui.view_stack.currentWidget.return_value = object()

    manager.exit_fullscreen()

    assert manager._fullscreen_enter_pending is None
    assert manager._immersive_active is False
    manager._cancel_fullscreen_enter_timeout.assert_called_once_with()
    manager._window.showNormal.assert_called_once_with()
    assert manager._window.setUpdatesEnabled.call_args_list[-1] == call(True)
    manager._window.update.assert_called_once_with()
    manager._request_media_viewport_relayout.assert_called_once_with(reset_view=True)
    manager._detail_coordinator.suspend_playback_for_transition.assert_not_called()
    manager._schedule_playback_resume.assert_called_once_with(
        expect_immersive=False,
        resume=True,
        transition_id=9,
    )


def test_non_windows_enter_keeps_existing_eager_update_path() -> None:
    manager = _make_windows_enter_manager()
    manager._detail_coordinator = MagicMock()
    manager._detail_coordinator.suspend_playback_for_transition.return_value = True
    manager._detail_coordinator.prepare_fullscreen_asset.return_value = True
    manager._edit_controller = MagicMock(return_value=None)
    manager._window.saveGeometry.return_value = MagicMock()
    manager._window.windowState.return_value = MagicMock()
    manager._ui.splitter.sizes.return_value = [200, 800]
    manager._suspend_layout_updates = MagicMock(return_value=nullcontext())
    manager._update_fullscreen_button_icon = MagicMock()
    manager._schedule_playback_resume = MagicMock()
    manager._begin_windows_fullscreen_enter = MagicMock()

    with patch("iPhoto.gui.ui.window_manager.sys.platform", "linux"):
        manager.enter_fullscreen()

    manager._begin_windows_fullscreen_enter.assert_not_called()
    manager._apply_immersive_backdrop.assert_called_once_with()
    manager._window.showFullScreen.assert_called_once_with()
    manager._schedule_playback_resume.assert_called_once_with(
        expect_immersive=True,
        resume=True,
    )

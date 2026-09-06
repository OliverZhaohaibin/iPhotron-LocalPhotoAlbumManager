"""Regression tests for Playback fullscreen state convergence."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject
from PySide6.QtWidgets import QWidget

from iPhoto.gui.ui.window_manager import (
    FULLSCREEN_ENTER_TIMEOUT_MS,
    FULLSCREEN_FINAL_UPDATE_TIMEOUT_MS,
    FramelessWindowManager,
    _FullscreenTransitionPhase,
    _WindowsFullscreenTransition,
)


def _transition(
    transition_id: int,
    *,
    phase: _FullscreenTransitionPhase = _FullscreenTransitionPhase.AWAITING_NATIVE,
    updates_enabled_before: bool = True,
    playback_generation: int = 1,
    resume_playback: bool = True,
) -> _WindowsFullscreenTransition:
    return _WindowsFullscreenTransition(
        transition_id=transition_id,
        phase=phase,
        updates_enabled_before=updates_enabled_before,
        playback_generation=playback_generation,
        resume_playback=resume_playback,
    )


def _make_windows_enter_manager() -> FramelessWindowManager:
    manager = FramelessWindowManager.__new__(FramelessWindowManager)
    manager._window = MagicMock()
    manager._window.updatesEnabled.return_value = True
    manager._window.isFullScreen.return_value = False
    manager._ui = MagicMock()
    manager._ui.splitter.signalsBlocked.return_value = False
    manager._ui.video_area.controls_enabled.return_value = True
    target = MagicMock()
    target.isVisible.return_value = True
    manager._immersive_visibility_targets = (target,)
    manager._splitter_sizes = [200, 800]
    manager._previous_geometry = MagicMock()
    manager._previous_window_state = MagicMock()
    manager._hidden_widget_states = []
    manager._video_controls_enabled_before = False
    manager._immersive_active = False
    manager._immersive_background_applied = False
    manager._fullscreen_transition_generation = 0
    manager._fullscreen_transition = None
    manager._playback_transition_generation = 0
    manager._playback_resume_pending = False
    manager._shadow_restore_generation = 0
    manager._detail_coordinator = MagicMock()
    manager._edit_controller = MagicMock(return_value=None)
    manager._suppress_playback_header_shadow = MagicMock()
    manager._apply_immersive_backdrop = MagicMock()
    manager._restore_default_backdrop = MagicMock()
    manager._update_fullscreen_button_icon = MagicMock()
    manager._request_media_viewport_relayout = MagicMock()
    manager._schedule_playback_header_shadow_restore = MagicMock()
    manager._emit_fullscreen_transition_event = MagicMock()
    manager._start_fullscreen_transition_deadline = MagicMock()
    return manager


def test_reconcile_native_exit_finishes_playback_without_requesting_window_change() -> None:
    manager = FramelessWindowManager.__new__(FramelessWindowManager)
    manager._immersive_active = True
    manager._fullscreen_transition = None
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
    manager._fullscreen_transition = None
    manager._window = MagicMock()
    manager._window.isFullScreen.return_value = True
    manager._finish_immersive_exit = MagicMock()

    manager._reconcile_playback_fullscreen_state()

    manager._finish_immersive_exit.assert_not_called()


def test_native_exit_restores_playback_without_calling_show_normal() -> None:
    manager = _make_windows_enter_manager()
    manager._immersive_active = True
    manager._detail_coordinator.suspend_playback_for_transition.return_value = False
    manager._window.updatesEnabled.return_value = True
    manager._ui.view_stack.currentWidget.return_value = object()
    manager._suspend_layout_updates = MagicMock(return_value=nullcontext())
    manager._schedule_playback_resume = MagicMock()

    manager._finish_immersive_exit(request_window_change=False)

    assert manager._immersive_active is False
    manager._restore_default_backdrop.assert_called_once_with()
    manager._window.showNormal.assert_not_called()
    manager._window.restoreGeometry.assert_not_called()
    manager._window.setWindowState.assert_not_called()
    manager._ui.splitter.setSizes.assert_called_once_with([200, 800])
    manager._request_media_viewport_relayout.assert_called_once_with(reset_view=True)
    manager._schedule_playback_header_shadow_restore.assert_called_once_with()
    manager._schedule_playback_resume.assert_called_once_with(
        expect_immersive=False,
        resume=False,
        playback_generation=1,
    )


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
    target = manager._immersive_visibility_targets[0]
    manager._window.setUpdatesEnabled.side_effect = lambda enabled: events.append(
        f"updates:{enabled}"
    )
    manager._start_fullscreen_transition_deadline.side_effect = lambda *_args, **_kwargs: (
        events.append("deadline")
    )
    manager._suppress_playback_header_shadow.side_effect = lambda: events.append("shadow")
    target.setVisible.side_effect = lambda _visible: events.append("visibility")
    manager._apply_immersive_backdrop.side_effect = lambda: events.append("backdrop")
    manager._window.showFullScreen.side_effect = lambda: events.append("fullscreen")

    manager._begin_windows_fullscreen_enter(True, playback_generation=4)

    assert events == [
        "updates:False",
        "deadline",
        "shadow",
        "visibility",
        "backdrop",
        "fullscreen",
    ]
    transition = manager._fullscreen_transition
    assert transition is not None
    assert transition.transition_id == 1
    assert transition.phase is _FullscreenTransitionPhase.AWAITING_NATIVE
    assert transition.playback_generation == 4
    assert transition.resume_playback is True
    assert manager._immersive_active is True
    manager._window.update.assert_not_called()
    manager._start_fullscreen_transition_deadline.assert_called_once_with(
        transition,
        timeout_ms=FULLSCREEN_ENTER_TIMEOUT_MS,
        callback=manager._handle_fullscreen_enter_timeout,
    )
    assert manager._ui.splitter.blockSignals.call_args_list == [call(True), call(False)]


@pytest.mark.parametrize(
    "failure_stage",
    (
        "updates",
        "deadline",
        "shadow",
        "visibility",
        "controls",
        "splitter",
        "backdrop",
        "native",
    ),
)
def test_windows_enter_restores_updates_when_mutation_raises(failure_stage: str) -> None:
    manager = _make_windows_enter_manager()
    target = manager._immersive_visibility_targets[0]
    error = RuntimeError(failure_stage)

    if failure_stage == "updates":
        manager._window.setUpdatesEnabled.side_effect = lambda enabled: (
            (_ for _ in ()).throw(error) if not enabled else None
        )
    elif failure_stage == "deadline":
        manager._start_fullscreen_transition_deadline.side_effect = error
    elif failure_stage == "shadow":
        manager._suppress_playback_header_shadow.side_effect = error
    elif failure_stage == "visibility":
        target.setVisible.side_effect = error
    elif failure_stage == "controls":
        manager._ui.video_area.hide_controls.side_effect = error
    elif failure_stage == "splitter":
        manager._ui.splitter.setSizes.side_effect = error
    elif failure_stage == "backdrop":
        manager._apply_immersive_backdrop.side_effect = error
    else:
        manager._window.showFullScreen.side_effect = error

    with pytest.raises(RuntimeError, match=failure_stage):
        manager._begin_windows_fullscreen_enter(True, playback_generation=3)

    assert manager._fullscreen_transition is None
    assert manager._immersive_active is False
    assert manager._window.setUpdatesEnabled.call_args_list[-1] == call(True)
    manager._window.showNormal.assert_called_once_with()


def test_windows_completion_restores_updates_when_relayout_raises() -> None:
    manager = _make_windows_enter_manager()
    transition = _transition(6)
    manager._fullscreen_transition = transition
    manager._immersive_active = True
    manager._window.isFullScreen.return_value = True
    manager._request_media_viewport_relayout.side_effect = RuntimeError("relayout")

    with pytest.raises(RuntimeError, match="relayout"):
        manager._complete_windows_fullscreen_enter(6)

    assert manager._fullscreen_transition is None
    assert manager._immersive_active is False
    manager._window.setUpdatesEnabled.assert_called_with(True)
    manager._window.showNormal.assert_called_once_with()


@pytest.mark.parametrize(
    "failure_source",
    ("enabled", "geometry", "backend", "render_target", "writer"),
)
def test_fullscreen_diagnostics_are_best_effort(failure_source: str) -> None:
    manager = _make_windows_enter_manager()
    manager._fullscreen_transition = _transition(12)
    manager._emit_fullscreen_transition_event = (
        FramelessWindowManager._emit_fullscreen_transition_event.__get__(manager)
    )

    enabled = MagicMock(return_value=True)
    writer = MagicMock()
    if failure_source == "enabled":
        enabled.side_effect = RuntimeError("enabled")
    elif failure_source == "geometry":
        manager._window.geometry.side_effect = RuntimeError("geometry")
    elif failure_source == "backend":
        manager._ui.image_viewer.render_backend_name.side_effect = RuntimeError("backend")
    elif failure_source == "render_target":
        manager._ui.image_viewer._render_target_device_size.side_effect = RuntimeError(
            "render_target"
        )
    else:
        writer.side_effect = RuntimeError("writer")

    with (
        patch("iPhoto.gui.ui.window_manager.detail_profile_enabled", enabled),
        patch("iPhoto.gui.ui.window_manager.emit_detail_event", writer),
    ):
        manager._emit_fullscreen_transition_event("fullscreen_test", 12)


def test_windows_enter_survives_diagnostic_writer_failure() -> None:
    manager = _make_windows_enter_manager()
    manager._emit_fullscreen_transition_event = (
        FramelessWindowManager._emit_fullscreen_transition_event.__get__(manager)
    )

    with (
        patch(
            "iPhoto.gui.ui.window_manager.detail_profile_enabled",
            return_value=True,
        ),
        patch(
            "iPhoto.gui.ui.window_manager.emit_detail_event",
            side_effect=RuntimeError("writer"),
        ),
    ):
        manager._begin_windows_fullscreen_enter(True, playback_generation=2)
        transition = manager._fullscreen_transition
        assert transition is not None
        manager._window.isFullScreen.return_value = True
        manager._complete_windows_fullscreen_enter(transition.transition_id)

    assert manager._fullscreen_transition is transition
    assert transition.phase is _FullscreenTransitionPhase.AWAITING_FINAL_UPDATE
    manager._window.setUpdatesEnabled.assert_called_with(True)


def test_windows_enter_waits_for_implicit_final_update() -> None:
    manager = _make_windows_enter_manager()
    transition = _transition(7, playback_generation=5)
    manager._fullscreen_transition = transition
    manager._fullscreen_transition_generation = 7
    manager._immersive_active = True
    manager._window.isFullScreen.return_value = True
    manager._schedule_playback_resume = MagicMock()

    manager._complete_windows_fullscreen_enter(7)
    manager._complete_windows_fullscreen_enter(7)

    assert manager._fullscreen_transition is transition
    assert transition.phase is _FullscreenTransitionPhase.AWAITING_FINAL_UPDATE
    manager._request_media_viewport_relayout.assert_called_once_with()
    manager._window.setUpdatesEnabled.assert_called_once_with(True)
    manager._window.update.assert_not_called()
    manager._update_fullscreen_button_icon.assert_called_once_with()
    manager._schedule_playback_resume.assert_called_once_with(
        expect_immersive=True,
        resume=True,
        playback_generation=5,
        transition_id=7,
    )
    manager._start_fullscreen_transition_deadline.assert_called_once_with(
        transition,
        timeout_ms=FULLSCREEN_FINAL_UPDATE_TIMEOUT_MS,
        callback=manager._handle_fullscreen_final_update_timeout,
    )


def test_final_update_timeout_cannot_finish_new_transition() -> None:
    manager = _make_windows_enter_manager()
    old_transition = _transition(
        8,
        phase=_FullscreenTransitionPhase.AWAITING_FINAL_UPDATE,
    )
    manager._fullscreen_transition = _transition(9)
    manager._finish_fullscreen_transition_observation = MagicMock()

    manager._handle_fullscreen_final_update_timeout(old_transition.transition_id)

    manager._finish_fullscreen_transition_observation.assert_not_called()


def test_final_update_timeout_is_diagnostic_only() -> None:
    manager = _make_windows_enter_manager()
    transition = _transition(
        8,
        phase=_FullscreenTransitionPhase.AWAITING_FINAL_UPDATE,
    )
    manager._fullscreen_transition = transition
    manager._immersive_active = True

    manager._handle_fullscreen_final_update_timeout(8)

    assert manager._fullscreen_transition is None
    assert manager._immersive_active is True
    manager._window.showNormal.assert_not_called()
    manager._emit_fullscreen_transition_event.assert_any_call(
        "fullscreen_final_update_unobserved",
        8,
    )


def test_qt_event_loop_attributes_implicit_update_to_transition(qapp) -> None:
    window = QWidget()
    manager = FramelessWindowManager.__new__(FramelessWindowManager)
    QObject.__init__(manager, window)
    manager._window = window
    manager._ui = SimpleNamespace(badge_host=None)
    manager._drag_sources = set()
    transition = _transition(
        10,
        phase=_FullscreenTransitionPhase.AWAITING_FINAL_UPDATE,
    )
    manager._fullscreen_transition = transition
    manager._emit_fullscreen_transition_event = MagicMock()
    window.installEventFilter(manager)
    window.resize(160, 90)
    window.show()
    qapp.processEvents()

    window.setUpdatesEnabled(False)
    manager._restore_windows_fullscreen_updates(transition)
    QCoreApplication.sendPostedEvents(window, QEvent.Type.UpdateRequest)
    qapp.processEvents()

    assert manager._fullscreen_transition is None
    assert call("fullscreen_update_requested", 10) in (
        manager._emit_fullscreen_transition_event.call_args_list
    )
    manager._emit_fullscreen_transition_event.assert_any_call(
        "fullscreen_transition_finished",
        10,
        reason="final_update_observed",
    )
    window.close()


def test_windows_enter_timeout_rolls_back_when_native_state_failed() -> None:
    manager = _make_windows_enter_manager()
    transition = _transition(8, playback_generation=3)
    manager._fullscreen_transition = transition
    manager._window.isFullScreen.return_value = False
    manager._schedule_playback_resume = MagicMock()

    manager._handle_fullscreen_enter_timeout(8)

    assert manager._fullscreen_transition is None
    assert manager._immersive_active is False
    manager._window.setUpdatesEnabled.assert_called_with(True)
    manager._schedule_playback_resume.assert_called_once_with(
        expect_immersive=False,
        resume=True,
        playback_generation=3,
        transition_id=8,
    )


def test_exit_during_pending_windows_enter_uses_rollback() -> None:
    manager = _make_windows_enter_manager()
    transition = _transition(9, playback_generation=1)
    manager._fullscreen_transition = transition
    manager._immersive_active = True
    manager._playback_resume_pending = True
    manager._window.updatesEnabled.return_value = False
    manager._ui.view_stack.currentWidget.return_value = object()
    manager._schedule_playback_resume = MagicMock()

    manager.exit_fullscreen()

    assert manager._fullscreen_transition is None
    assert manager._immersive_active is False
    manager._window.showNormal.assert_called_once_with()
    manager._window.setUpdatesEnabled.assert_called_with(True)
    manager._detail_coordinator.suspend_playback_for_transition.assert_not_called()
    manager._schedule_playback_resume.assert_called_once_with(
        expect_immersive=False,
        resume=True,
        playback_generation=1,
        transition_id=9,
    )


def test_exit_after_confirmation_finishes_observation_without_rollback() -> None:
    manager = _make_windows_enter_manager()
    transition = _transition(
        11,
        phase=_FullscreenTransitionPhase.AWAITING_FINAL_UPDATE,
    )
    manager._fullscreen_transition = transition
    manager._immersive_active = True
    manager._playback_resume_pending = True
    manager._ui.view_stack.currentWidget.return_value = object()
    manager._suspend_layout_updates = MagicMock(return_value=nullcontext())
    manager._rollback_windows_fullscreen_enter = MagicMock()
    manager._schedule_playback_resume = MagicMock()

    manager.exit_fullscreen()

    manager._rollback_windows_fullscreen_enter.assert_not_called()
    assert manager._fullscreen_transition is None
    manager._window.showNormal.assert_called_once_with()


def test_rapid_fullscreen_toggles_only_latest_callback_resumes_playback() -> None:
    manager = _make_windows_enter_manager()
    manager._detail_coordinator.suspend_playback_for_transition.return_value = True
    callbacks: list[Callable[[], None]] = []

    with patch(
        "iPhoto.gui.ui.window_manager.QTimer.singleShot",
        side_effect=lambda _delay, callback: callbacks.append(callback),
    ):
        for expect_immersive in (True, False, True, False):
            generation, resume = manager._begin_playback_transition()
            manager._schedule_playback_resume(
                expect_immersive=expect_immersive,
                resume=resume,
                playback_generation=generation,
            )

    manager._immersive_active = False
    for callback in callbacks:
        callback()

    manager._detail_coordinator.suspend_playback_for_transition.assert_called_once_with()
    manager._detail_coordinator.resume_playback_after_transition.assert_called_once_with()
    assert manager._playback_resume_pending is False


def test_non_windows_enter_keeps_existing_eager_update_path() -> None:
    manager = _make_windows_enter_manager()
    manager._detail_coordinator.suspend_playback_for_transition.return_value = True
    manager._detail_coordinator.prepare_fullscreen_asset.return_value = True
    manager._window.saveGeometry.return_value = MagicMock()
    manager._window.windowState.return_value = MagicMock()
    manager._ui.splitter.sizes.return_value = [200, 800]
    manager._suspend_layout_updates = MagicMock(return_value=nullcontext())
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
        playback_generation=1,
    )

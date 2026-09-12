"""Regression tests for Playback fullscreen state convergence."""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QSize, Qt
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget

from iPhoto.gui.ui.window_manager import (
    FULLSCREEN_ENTER_TIMEOUT_MS,
    FULLSCREEN_MEDIA_FRAME_TIMEOUT_MS,
    PLAYBACK_RESUME_DELAY_MS,
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
    manager._ui.image_viewer._render_target_device_size.return_value = (1200, 800)
    manager._ui.video_area.video_viewport.return_value = manager._ui.video_area
    manager._ui.video_area._render_target_device_size.return_value = (1200, 800)
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


@pytest.mark.parametrize(
    "failure_stage",
    ("asset", "placeholder", "geometry", "window_state", "splitter"),
)
def test_enter_preflight_failure_resumes_suspended_playback(
    failure_stage: str,
) -> None:
    manager = _make_windows_enter_manager()
    manager._detail_coordinator.suspend_playback_for_transition.return_value = True
    manager._detail_coordinator.prepare_fullscreen_asset.return_value = True
    error = RuntimeError(failure_stage)
    callbacks: list[Callable[[], None]] = []
    single_shot = MagicMock(side_effect=lambda _delay, callback: callbacks.append(callback))

    if failure_stage == "asset":
        manager._detail_coordinator.prepare_fullscreen_asset.side_effect = error
    elif failure_stage == "placeholder":
        manager._detail_coordinator.prepare_fullscreen_asset.return_value = False
        manager._detail_coordinator.show_placeholder_in_viewer.side_effect = error
    elif failure_stage == "geometry":
        manager._window.saveGeometry.side_effect = error
    elif failure_stage == "window_state":
        manager._window.windowState.side_effect = error
    else:
        manager._ui.splitter.sizes.side_effect = error

    with (
        patch("iPhoto.gui.ui.window_manager.sys.platform", "win32"),
        patch(
            "iPhoto.gui.ui.window_manager.QTimer.singleShot",
            single_shot,
        ),
        pytest.raises(RuntimeError, match=failure_stage),
    ):
        manager.enter_fullscreen()

    assert manager._fullscreen_transition is None
    assert manager._playback_resume_pending is True
    manager._window.setUpdatesEnabled.assert_not_called()
    manager._window.showFullScreen.assert_not_called()
    assert len(callbacks) == 1
    assert single_shot.call_args.args[0] == PLAYBACK_RESUME_DELAY_MS

    callbacks[0]()

    manager._detail_coordinator.resume_playback_after_transition.assert_called_once_with()
    assert manager._playback_resume_pending is False


def test_windows_enter_suppresses_updates_before_visible_mutations() -> None:
    manager = _make_windows_enter_manager()
    events: list[str] = []
    target = manager._immersive_visibility_targets[0]
    manager._window.setUpdatesEnabled.side_effect = lambda enabled: events.append(
        f"updates:{enabled}"
    )
    manager._detail_coordinator.begin_fullscreen_viewport_transition.side_effect = lambda: (
        events.append("lod-gate")
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
        "lod-gate",
        "deadline",
        "shadow",
        "visibility",
        "backdrop",
        "fullscreen",
        "updates:True",
    ]
    transition = manager._fullscreen_transition
    assert transition is not None
    assert transition.transition_id == 1
    assert transition.phase is _FullscreenTransitionPhase.AWAITING_NATIVE
    assert transition.playback_generation == 4
    assert transition.resume_playback is True
    assert transition.updates_restored is True
    assert manager._immersive_active is True
    manager._detail_coordinator.begin_fullscreen_viewport_transition.assert_called_once_with()
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
        "updates_restore",
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
    elif failure_stage == "updates_restore":
        manager._window.setUpdatesEnabled.side_effect = lambda enabled: (
            (_ for _ in ()).throw(error) if enabled else None
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
    manager._detail_coordinator.request_fullscreen_viewport_frame.side_effect = RuntimeError(
        "relayout"
    )

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
    assert transition.phase is _FullscreenTransitionPhase.AWAITING_MEDIA_FRAME
    manager._window.setUpdatesEnabled.assert_called_with(True)


def test_windows_enter_waits_for_matching_media_frame() -> None:
    manager = _make_windows_enter_manager()
    transition = _transition(7, playback_generation=5, updates_enabled_before=True)
    transition.updates_restored = True
    manager._fullscreen_transition = transition
    manager._fullscreen_transition_generation = 7
    manager._immersive_active = True
    manager._window.isFullScreen.return_value = True
    manager._schedule_playback_resume = MagicMock()

    manager._complete_windows_fullscreen_enter(7)
    manager._complete_windows_fullscreen_enter(7)

    assert manager._fullscreen_transition is transition
    assert transition.phase is _FullscreenTransitionPhase.AWAITING_MEDIA_FRAME
    manager._detail_coordinator.request_fullscreen_viewport_frame.assert_called_once_with(
        7,
        1,
    )
    manager._window.setUpdatesEnabled.assert_not_called()
    manager._window.update.assert_not_called()
    manager._update_fullscreen_button_icon.assert_called_once_with()
    manager._schedule_playback_resume.assert_not_called()
    manager._start_fullscreen_transition_deadline.assert_called_once_with(
        transition,
        timeout_ms=FULLSCREEN_MEDIA_FRAME_TIMEOUT_MS,
        callback=manager._handle_fullscreen_media_frame_timeout,
    )


def test_media_frame_timeout_cannot_finish_new_transition() -> None:
    manager = _make_windows_enter_manager()
    old_transition = _transition(
        8,
        phase=_FullscreenTransitionPhase.AWAITING_MEDIA_FRAME,
    )
    manager._fullscreen_transition = _transition(9)
    manager._finish_fullscreen_transition_observation = MagicMock()

    manager._handle_fullscreen_media_frame_timeout(old_transition.transition_id)

    manager._finish_fullscreen_transition_observation.assert_not_called()


def test_first_media_frame_timeout_keeps_gate_and_starts_degraded_wait() -> None:
    manager = _make_windows_enter_manager()
    transition = _transition(
        8,
        phase=_FullscreenTransitionPhase.AWAITING_MEDIA_FRAME,
    )
    manager._fullscreen_transition = transition
    manager._immersive_active = True
    manager._schedule_playback_resume = MagicMock()

    manager._handle_fullscreen_media_frame_timeout(8)

    assert manager._fullscreen_transition is transition
    assert manager._immersive_active is True
    assert transition.first_frame_timeout_seen is True
    assert transition.allow_automatic_lod is False
    manager._window.showNormal.assert_not_called()
    manager._emit_fullscreen_transition_event.assert_any_call(
        "fullscreen_first_frame_timeout",
        8,
    )
    assert call("fullscreen_first_frame_submitted", 8, reason="timeout") not in (
        manager._emit_fullscreen_transition_event.call_args_list
    )
    manager._detail_coordinator.complete_fullscreen_viewport_transition.assert_not_called()
    manager._detail_coordinator.request_fullscreen_viewport_frame.assert_called_once_with(
        8,
        1,
    )
    manager._schedule_playback_resume.assert_called_once_with(
        expect_immersive=True,
        resume=True,
        playback_generation=1,
        transition_id=8,
    )


def test_qt_update_request_is_diagnostic_and_does_not_finish_transition(qapp) -> None:
    window = QWidget()
    manager = FramelessWindowManager.__new__(FramelessWindowManager)
    QObject.__init__(manager, window)
    manager._window = window
    manager._ui = SimpleNamespace(badge_host=None)
    manager._drag_sources = set()
    transition = _transition(
        10,
        phase=_FullscreenTransitionPhase.AWAITING_MEDIA_FRAME,
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

    assert manager._fullscreen_transition is transition
    assert call("fullscreen_update_requested", 10) in (
        manager._emit_fullscreen_transition_event.call_args_list
    )
    window.close()


@pytest.mark.parametrize(
    ("surface", "reason"),
    (("image", "image_submission"), ("video", "video_submission")),
)
def test_matching_media_submission_completes_transition(
    surface: str,
    reason: str,
) -> None:
    manager = _make_windows_enter_manager()
    transition = _transition(
        13,
        phase=_FullscreenTransitionPhase.AWAITING_MEDIA_FRAME,
        playback_generation=6,
    )
    manager._fullscreen_transition = transition
    manager._immersive_active = True
    manager._schedule_playback_resume = MagicMock()
    if surface == "image":
        manager._ui.player_stack.currentWidget.return_value = manager._ui.image_viewer
        submit = manager._on_fullscreen_image_frame_submitted
    else:
        manager._ui.player_stack.currentWidget.return_value = manager._ui.video_area
        submit = manager._on_fullscreen_video_frame_submitted

    identity = ("still", "content", 1) if surface == "image" else ("video", 3, 1)
    submit(13, 1, QSize(1200, 800), identity)

    assert manager._fullscreen_transition is transition
    assert transition.phase is _FullscreenTransitionPhase.AWAITING_STABLE_MEDIA_FRAME
    manager._detail_coordinator.complete_fullscreen_viewport_transition.assert_not_called()

    submit(13, 2, QSize(1200, 800), identity)

    assert manager._fullscreen_transition is None
    assert transition.phase is _FullscreenTransitionPhase.COMPLETE
    manager._detail_coordinator.complete_fullscreen_viewport_transition.assert_called_once_with(
        reason=f"{surface}_stable_submission",
        allow_automatic_lod=True,
    )
    manager._schedule_playback_resume.assert_called_once_with(
        expect_immersive=True,
        resume=True,
        playback_generation=6,
        transition_id=13,
    )
    manager._emit_fullscreen_transition_event.assert_any_call(
        "fullscreen_first_frame_submitted",
        13,
        reason=reason,
    )


def test_stable_media_barrier_restarts_when_render_target_changes() -> None:
    manager = _make_windows_enter_manager()
    transition = _transition(
        31,
        phase=_FullscreenTransitionPhase.AWAITING_MEDIA_FRAME,
    )
    manager._fullscreen_transition = transition
    manager._ui.player_stack.currentWidget.return_value = manager._ui.image_viewer

    manager._on_fullscreen_image_frame_submitted(
        31,
        1,
        QSize(1200, 800),
        ("still", "content", 1),
    )
    manager._ui.image_viewer._render_target_device_size.return_value = (1400, 900)
    manager._on_fullscreen_image_frame_submitted(
        31,
        2,
        QSize(1400, 900),
        ("still", "content", 1),
    )

    assert manager._fullscreen_transition is transition
    assert transition.media_target == (1400, 900)
    manager._detail_coordinator.complete_fullscreen_viewport_transition.assert_not_called()
    manager._detail_coordinator.request_fullscreen_viewport_frame.assert_called_with(31, 2)

    manager._on_fullscreen_image_frame_submitted(
        31,
        2,
        QSize(1400, 900),
        ("still", "content", 1),
    )

    assert manager._fullscreen_transition is None
    manager._detail_coordinator.complete_fullscreen_viewport_transition.assert_called_once()


def test_stable_frame_rejects_changed_still_identity_and_rearms() -> None:
    manager = _make_windows_enter_manager()
    transition = _transition(
        18,
        phase=_FullscreenTransitionPhase.AWAITING_MEDIA_FRAME,
    )
    manager._fullscreen_transition = transition
    manager._ui.player_stack.currentWidget.return_value = manager._ui.image_viewer

    manager._on_fullscreen_image_frame_submitted(
        18,
        1,
        QSize(1200, 800),
        ("still", "lod-a", 4),
    )
    manager._detail_coordinator.request_fullscreen_viewport_frame.reset_mock()
    manager._on_fullscreen_image_frame_submitted(
        18,
        2,
        QSize(1200, 800),
        ("still", "lod-b", 4),
    )

    assert manager._fullscreen_transition is transition
    assert transition.phase is _FullscreenTransitionPhase.AWAITING_STABLE_MEDIA_FRAME
    manager._detail_coordinator.request_fullscreen_viewport_frame.assert_called_once_with(
        18,
        2,
    )
    manager._detail_coordinator.complete_fullscreen_viewport_transition.assert_not_called()


def test_stable_video_frame_allows_advancing_serial_in_same_generation() -> None:
    manager = _make_windows_enter_manager()
    transition = _transition(
        20,
        phase=_FullscreenTransitionPhase.AWAITING_MEDIA_FRAME,
    )
    manager._fullscreen_transition = transition
    manager._ui.player_stack.currentWidget.return_value = manager._ui.video_area

    manager._on_fullscreen_video_frame_submitted(
        20,
        1,
        QSize(1200, 800),
        ("video", 6, 11),
    )
    manager._on_fullscreen_video_frame_submitted(
        20,
        2,
        QSize(1200, 800),
        ("video", 6, 12),
    )

    assert manager._fullscreen_transition is None
    manager._detail_coordinator.complete_fullscreen_viewport_transition.assert_called_once()


def test_media_submission_finishes_when_lod_gate_release_raises() -> None:
    manager = _make_windows_enter_manager()
    transition = _transition(
        14,
        phase=_FullscreenTransitionPhase.AWAITING_MEDIA_FRAME,
    )
    manager._fullscreen_transition = transition
    manager._ui.player_stack.currentWidget.return_value = manager._ui.image_viewer
    manager._detail_coordinator.complete_fullscreen_viewport_transition.side_effect = RuntimeError(
        "gate"
    )
    manager._schedule_playback_resume = MagicMock()

    manager._on_fullscreen_image_frame_submitted(
        14,
        1,
        QSize(1200, 800),
        ("still", "content", 1),
    )
    manager._on_fullscreen_image_frame_submitted(
        14,
        2,
        QSize(1200, 800),
        ("still", "content", 1),
    )

    assert manager._fullscreen_transition is None
    assert transition.phase is _FullscreenTransitionPhase.COMPLETE
    manager._schedule_playback_resume.assert_called_once_with(
        expect_immersive=True,
        resume=True,
        playback_generation=1,
        transition_id=14,
    )


def test_stable_frame_timeout_handoffs_without_automatic_lod() -> None:
    manager = _make_windows_enter_manager()
    transition = _transition(
        15,
        phase=_FullscreenTransitionPhase.AWAITING_STABLE_MEDIA_FRAME,
    )
    manager._fullscreen_transition = transition
    manager._schedule_playback_resume = MagicMock()

    manager._handle_fullscreen_media_frame_timeout(15)

    assert manager._fullscreen_transition is None
    manager._detail_coordinator.complete_fullscreen_viewport_transition.assert_called_once_with(
        reason="stable_frame_timeout",
        allow_automatic_lod=False,
    )
    manager._schedule_playback_resume.assert_called_once()


def test_handoff_waits_for_both_media_and_animation() -> None:
    manager = _make_windows_enter_manager()
    transition = _transition(
        16,
        phase=_FullscreenTransitionPhase.AWAITING_HANDOFF,
    )
    transition.media_stable = True
    transition.animation_finished = False
    manager._fullscreen_transition = transition
    manager._schedule_playback_resume = MagicMock()

    manager._maybe_finish_fullscreen_handoff(transition, reason="media_ready")

    manager._detail_coordinator.complete_fullscreen_viewport_transition.assert_not_called()
    assert manager._fullscreen_transition is transition

    transition.animation_finished = True
    manager._maybe_finish_fullscreen_handoff(transition, reason="animation_ready")

    assert manager._fullscreen_transition is None
    manager._detail_coordinator.complete_fullscreen_viewport_transition.assert_called_once_with(
        reason="animation_ready",
        allow_automatic_lod=True,
    )


def test_magic_zoom_overlay_tracks_target_and_cleans_up(qapp) -> None:
    window = QMainWindow()
    host = QWidget(window)
    window.setCentralWidget(host)
    container = QWidget(host)
    container.setGeometry(20, 30, 320, 180)
    window.resize(640, 400)
    window.show()
    qapp.processEvents()
    manager = FramelessWindowManager.__new__(FramelessWindowManager)
    QObject.__init__(manager, window)
    manager._window = window
    manager._ui = SimpleNamespace(player_container=container)
    manager._fullscreen_hold_overlay = None
    manager._fullscreen_hold_animation = None
    manager._fullscreen_hold_fade = None
    manager._fullscreen_hold_transition_id = None
    manager._emit_fullscreen_transition_event = MagicMock()
    transition = _transition(17)
    manager._fullscreen_transition = transition

    manager._prepare_fullscreen_magic_zoom(transition)

    assert manager._fullscreen_hold_overlay is not None
    assert transition.animation_finished is False
    container.setGeometry(0, 0, 640, 400)
    manager._start_fullscreen_magic_zoom(17)
    manager._finish_fullscreen_magic_animation(17)

    assert transition.animation_finished is True
    assert manager._fullscreen_hold_overlay.geometry() == container.geometry()

    manager._cancel_fullscreen_magic_zoom()
    assert manager._fullscreen_hold_overlay is None
    window.close()


@pytest.mark.gpu
@pytest.mark.windows_compositor
def test_visible_translucent_window_magic_zoom_never_exposes_backdrop(qapp) -> None:
    if sys.platform != "win32":
        pytest.skip("requires a visible Windows compositor integration runner")
    if QApplication.platformName().lower() in {"offscreen", "minimal"}:
        pytest.skip("requires a visible platform compositor")

    desktop_sentinel = QWidget()
    desktop_sentinel.setStyleSheet("background-color: #00ff00;")
    desktop_sentinel.setGeometry(100, 100, 640, 400)
    desktop_sentinel.show()

    window = QMainWindow()
    window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    host = QWidget(window)
    host.setStyleSheet("background-color: #000000;")
    window.setCentralWidget(host)
    container = QWidget(host)
    container.setStyleSheet("background-color: #d02020;")
    container.setGeometry(120, 90, 400, 220)
    window.setGeometry(100, 100, 640, 400)
    window.show()
    window.raise_()
    qapp.processEvents()

    manager = FramelessWindowManager.__new__(FramelessWindowManager)
    QObject.__init__(manager, window)
    manager._window = window
    manager._ui = SimpleNamespace(player_container=container)
    manager._detail_coordinator = MagicMock()
    manager._fullscreen_hold_overlay = None
    manager._fullscreen_hold_animation = None
    manager._fullscreen_hold_fade = None
    manager._fullscreen_hold_transition_id = None
    manager._emit_fullscreen_transition_event = MagicMock()
    manager._schedule_playback_resume = MagicMock()
    transition = _transition(
        41,
        phase=_FullscreenTransitionPhase.AWAITING_HANDOFF,
    )
    transition.media_stable = True
    manager._fullscreen_transition = transition

    manager._prepare_fullscreen_magic_zoom(transition)
    container.setStyleSheet("background-color: #2050d0;")
    container.setGeometry(host.rect())
    manager._start_fullscreen_magic_zoom(41)

    screen = window.screen()
    assert screen is not None
    sampled = []
    deadline = time.monotonic() + 2.0
    while manager._fullscreen_transition is not None and time.monotonic() < deadline:
        qapp.processEvents()
        global_center = window.mapToGlobal(window.rect().center())
        capture_point = global_center - screen.geometry().topLeft()
        image = screen.grabWindow(
            0,
            capture_point.x(),
            capture_point.y(),
            1,
            1,
        ).toImage()
        if not image.isNull():
            pixel = image.pixelColor(image.width() // 2, image.height() // 2)
            sampled.append(pixel)
            assert pixel.alpha() == 255
            assert max(pixel.red(), pixel.green(), pixel.blue()) >= 32
            assert not (pixel.green() > pixel.red() * 1.5 and pixel.green() > pixel.blue() * 1.5)
        time.sleep(0.005)

    assert sampled
    assert manager._fullscreen_transition is None
    manager._detail_coordinator.complete_fullscreen_viewport_transition.assert_called_once_with(
        reason="animation_finished",
        allow_automatic_lod=True,
    )
    window.close()
    desktop_sentinel.close()


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


def test_exit_while_waiting_for_media_frame_cancels_gate_and_rolls_back() -> None:
    manager = _make_windows_enter_manager()
    transition = _transition(
        11,
        phase=_FullscreenTransitionPhase.AWAITING_MEDIA_FRAME,
    )
    manager._fullscreen_transition = transition
    manager._immersive_active = True
    manager._playback_resume_pending = True
    manager._ui.view_stack.currentWidget.return_value = object()
    manager._suspend_layout_updates = MagicMock(return_value=nullcontext())
    manager._schedule_playback_resume = MagicMock()

    manager.exit_fullscreen()

    assert manager._fullscreen_transition is None
    manager._window.showNormal.assert_called_once_with()
    manager._detail_coordinator.cancel_fullscreen_viewport_transition.assert_called_once_with()


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

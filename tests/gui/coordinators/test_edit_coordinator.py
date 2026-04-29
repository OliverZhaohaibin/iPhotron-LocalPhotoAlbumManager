from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for edit coordinator tests", exc_type=ImportError)
pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets not available", exc_type=ImportError)

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QSlider

from iPhoto.core.adjustment_mapping import VIDEO_TRIM_IN_KEY, VIDEO_TRIM_OUT_KEY
from iPhoto.gui.coordinators.edit_coordinator import EditCoordinator
from iPhoto.gui.ui.tasks.video_sidebar_preview_worker import VideoSidebarPreviewResult
from iPhoto.gui.ui.media import MediaRestoreRequest


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def test_handle_done_clicked_delegates_to_adjustment_committer() -> None:
    coordinator = EditCoordinator.__new__(EditCoordinator)
    session = SimpleNamespace(
        set_values=Mock(),
        values=Mock(return_value={"Crop_W": 0.8}),
    )
    viewport = Mock(crop_values=Mock(return_value={"Crop_W": 0.8}))
    coordinator._session = session
    coordinator._current_source = Path("/fake/photo.jpg")
    coordinator._active_edit_viewport = Mock(return_value=viewport)
    coordinator._adjustment_committer = Mock(commit=Mock(return_value=True))
    coordinator.leave_edit_mode = Mock()

    EditCoordinator._handle_done_clicked(coordinator)

    session.set_values.assert_called_once_with({"Crop_W": 0.8}, emit_individual=False)
    coordinator._adjustment_committer.commit.assert_called_once_with(
        Path("/fake/photo.jpg"),
        {"Crop_W": 0.8},
        reason="edit_done",
    )
    coordinator.leave_edit_mode.assert_called_once_with(restore_reason="edit_done")


def test_leave_edit_mode_requests_video_restore_with_probed_duration() -> None:
    coordinator = EditCoordinator.__new__(EditCoordinator)
    source = Path("/fake/video.mp4")
    viewport = Mock()
    viewport.setCropMode = Mock()
    viewport.set_eyedropper_mode = Mock()
    coordinator._active_edit_viewport = Mock(return_value=viewport)
    coordinator._session = SimpleNamespace(values=lambda: {"Crop_W": 0.8})
    coordinator._current_source = source
    coordinator._media_session = SimpleNamespace(request_restore=Mock())
    coordinator._fullscreen_manager = SimpleNamespace(is_in_fullscreen=lambda: False)
    coordinator._preview_manager = Mock(stop_session=Mock())
    coordinator._zoom_handler = Mock(disconnect_controls=Mock())
    coordinator._header_controller = Mock(restore_detail_mode=Mock())
    coordinator._theme_controller = None
    coordinator._router = Mock(show_detail=Mock())
    coordinator._transition_manager = Mock(leave_edit_mode=Mock())
    coordinator._pending_video_duration_sec = 4.5
    coordinator._video_trim_thumbnail_timer = Mock(stop=Mock())
    coordinator._video_sidebar_preview_timer = Mock(stop=Mock())
    coordinator._video_thumbnail_generation = 0
    coordinator._video_sidebar_generation = 0
    coordinator._video_trim_worker = None
    coordinator._video_trim_diag = {}
    coordinator._video_frame_step_ms = 33
    coordinator._video_color_stats = None
    coordinator._ui = SimpleNamespace(
        video_area=Mock(
            _diag_surface_name=Mock(return_value="video"),
            adjusted_preview_enabled=Mock(return_value=False),
            set_edit_mode_active=Mock(),
            set_controls_enabled=Mock(),
        ),
        edit_image_viewer=Mock(set_surface_color_override=Mock()),
        edit_sidebar=Mock(set_session=Mock(), set_video_edit_mode=Mock()),
        video_trim_bar=Mock(hide=Mock()),
        toggle_filmstrip_action=SimpleNamespace(isChecked=lambda: True),
    )

    with patch(
        "iPhoto.gui.coordinators.edit_coordinator.viewer_surface_color",
        return_value=None,
    ):
        EditCoordinator.leave_edit_mode(coordinator)

    coordinator._media_session.request_restore.assert_called_once_with(
        MediaRestoreRequest(
            path=source,
            reason="edit_exit",
            duration_sec=4.5,
        )
    )
    coordinator._router.show_detail.assert_called_once_with()


def test_leave_edit_mode_still_requests_restore_for_non_video_assets() -> None:
    coordinator = EditCoordinator.__new__(EditCoordinator)
    source = Path("/fake/photo.jpg")
    viewport = Mock()
    viewport.setCropMode = Mock()
    viewport.set_eyedropper_mode = Mock()
    coordinator._active_edit_viewport = Mock(return_value=viewport)
    coordinator._session = SimpleNamespace(values=lambda: {"Crop_W": 0.8})
    coordinator._current_source = source
    coordinator._media_session = SimpleNamespace(request_restore=Mock())
    coordinator._fullscreen_manager = SimpleNamespace(is_in_fullscreen=lambda: False)
    coordinator._preview_manager = Mock(stop_session=Mock())
    coordinator._zoom_handler = Mock(disconnect_controls=Mock())
    coordinator._header_controller = Mock(restore_detail_mode=Mock())
    coordinator._theme_controller = None
    coordinator._router = Mock(show_detail=Mock())
    coordinator._transition_manager = Mock(leave_edit_mode=Mock())
    coordinator._pending_video_duration_sec = None
    coordinator._video_trim_thumbnail_timer = Mock(stop=Mock())
    coordinator._video_sidebar_preview_timer = Mock(stop=Mock())
    coordinator._video_thumbnail_generation = 0
    coordinator._video_sidebar_generation = 0
    coordinator._video_trim_worker = None
    coordinator._video_trim_diag = {}
    coordinator._video_frame_step_ms = 33
    coordinator._video_color_stats = None
    coordinator._ui = SimpleNamespace(
        video_area=Mock(
            _diag_surface_name=Mock(return_value="video"),
            adjusted_preview_enabled=Mock(return_value=False),
            set_edit_mode_active=Mock(),
            set_controls_enabled=Mock(),
        ),
        edit_image_viewer=Mock(set_surface_color_override=Mock()),
        edit_sidebar=Mock(set_session=Mock(), set_video_edit_mode=Mock()),
        video_trim_bar=Mock(hide=Mock()),
        toggle_filmstrip_action=SimpleNamespace(isChecked=lambda: True),
    )

    with patch(
        "iPhoto.gui.coordinators.edit_coordinator.viewer_surface_color",
        return_value=None,
    ):
        EditCoordinator.leave_edit_mode(coordinator)

    coordinator._media_session.request_restore.assert_called_once_with(
        MediaRestoreRequest(
            path=source,
            reason="edit_exit",
            duration_sec=None,
        )
    )


def test_leave_edit_mode_can_emit_edit_done_restore_reason() -> None:
    coordinator = EditCoordinator.__new__(EditCoordinator)
    source = Path("/fake/video.mp4")
    viewport = Mock()
    viewport.setCropMode = Mock()
    viewport.set_eyedropper_mode = Mock()
    coordinator._active_edit_viewport = Mock(return_value=viewport)
    coordinator._session = SimpleNamespace(values=lambda: {"Crop_W": 0.8})
    coordinator._current_source = source
    coordinator._media_session = SimpleNamespace(request_restore=Mock())
    coordinator._fullscreen_manager = SimpleNamespace(is_in_fullscreen=lambda: False)
    coordinator._preview_manager = Mock(stop_session=Mock())
    coordinator._zoom_handler = Mock(disconnect_controls=Mock())
    coordinator._header_controller = Mock(restore_detail_mode=Mock())
    coordinator._theme_controller = None
    coordinator._router = Mock(show_detail=Mock())
    coordinator._transition_manager = Mock(leave_edit_mode=Mock())
    coordinator._pending_video_duration_sec = 7.25
    coordinator._video_trim_thumbnail_timer = Mock(stop=Mock())
    coordinator._video_sidebar_preview_timer = Mock(stop=Mock())
    coordinator._video_thumbnail_generation = 0
    coordinator._video_sidebar_generation = 0
    coordinator._video_trim_worker = None
    coordinator._video_trim_diag = {}
    coordinator._video_frame_step_ms = 33
    coordinator._video_color_stats = None
    coordinator._ui = SimpleNamespace(
        video_area=Mock(
            _diag_surface_name=Mock(return_value="video"),
            adjusted_preview_enabled=Mock(return_value=False),
            set_edit_mode_active=Mock(),
            set_controls_enabled=Mock(),
        ),
        edit_image_viewer=Mock(set_surface_color_override=Mock()),
        edit_sidebar=Mock(set_session=Mock(), set_video_edit_mode=Mock()),
        video_trim_bar=Mock(hide=Mock()),
        toggle_filmstrip_action=SimpleNamespace(isChecked=lambda: True),
    )

    with patch(
        "iPhoto.gui.coordinators.edit_coordinator.viewer_surface_color",
        return_value=None,
    ):
        EditCoordinator.leave_edit_mode(coordinator, restore_reason="edit_done")

    coordinator._media_session.request_restore.assert_called_once_with(
        MediaRestoreRequest(
            path=source,
            reason="edit_done",
            duration_sec=7.25,
        )
    )


def test_queue_video_trim_thumbnails_accepts_missing_duration() -> None:
    coordinator = EditCoordinator.__new__(EditCoordinator)
    trim_bar = Mock()
    trim_bar.thumbnail_view_width.return_value = 0
    coordinator._ui = SimpleNamespace(video_trim_bar=trim_bar)
    coordinator._current_source = Path("/fake/video.mp4")
    coordinator._video_thumbnail_generation = 0
    coordinator._video_trim_diag = {}
    coordinator._video_trim_worker = None
    coordinator._emit_video_trim_diag = Mock()

    class _SignalStub:
        def connect(self, *args, **kwargs) -> None:
            return None

    worker_instance = SimpleNamespace(
        signals=SimpleNamespace(
            thumbnail=_SignalStub(),
            ready=_SignalStub(),
            error=_SignalStub(),
            finished=_SignalStub(),
        )
    )
    pool = Mock()
    with patch(
        "iPhoto.gui.coordinators.edit_coordinator.VideoTrimThumbnailWorker",
        return_value=worker_instance,
    ) as worker_cls, patch(
        "iPhoto.gui.coordinators.edit_coordinator.QThreadPool.globalInstance",
        return_value=pool,
    ), patch(
        "iPhoto.gui.coordinators.edit_coordinator.probe_video_rotation",
        return_value=(0, 0, 0),
    ):
        EditCoordinator._queue_video_trim_thumbnails(coordinator, None)

    worker_cls.assert_called_once_with(
        Path("/fake/video.mp4"),
        generation=1,
        duration_sec=None,
        target_height=72,
        target_width=96,
        count=10,
    )
    assert coordinator._video_trim_diag[1]["duration_sec"] is None
    trim_bar.clear.assert_called_once_with()
    pool.start.assert_called_once_with(worker_instance, -1)


def test_refresh_video_sidebar_preview_uses_inline_sidebar_loader(qapp) -> None:
    """Video sidebar frame delivery should not queue a second preview worker."""

    coordinator = EditCoordinator.__new__(EditCoordinator)
    coordinator._current_source = Path("/fake/video.mp4")
    coordinator._session = SimpleNamespace(set_color_stats=Mock())
    coordinator._video_sidebar_generation = 0
    coordinator._video_sidebar_worker = None
    coordinator._video_sidebar_workers = []
    coordinator._video_duration_sec = Mock(return_value=10.0)
    coordinator._normalised_video_trim = Mock(return_value=(2.0, 8.0))
    coordinator._is_video_source = Mock(return_value=True)
    coordinator._pipeline_loader = Mock()
    coordinator._apply_session_adjustments_to_viewer = Mock()
    coordinator._ui = SimpleNamespace(
        edit_sidebar=Mock(preview_thumbnail_height=Mock(return_value=72)),
    )

    class _SignalStub:
        def __init__(self) -> None:
            self.callback = None

        def connect(self, callback, *args, **kwargs) -> None:
            self.callback = callback

    ready_signal = _SignalStub()
    finished_signal = _SignalStub()
    worker_instance = SimpleNamespace(
        signals=SimpleNamespace(
            ready=ready_signal,
            error=_SignalStub(),
            finished=finished_signal,
        )
    )
    pool = Mock()

    with patch(
        "iPhoto.gui.coordinators.edit_coordinator.VideoSidebarPreviewWorker",
        return_value=worker_instance,
    ), patch(
        "iPhoto.gui.coordinators.edit_coordinator.QThreadPool.globalInstance",
        return_value=pool,
    ):
        EditCoordinator._refresh_video_sidebar_preview(coordinator)

    assert coordinator._video_sidebar_worker is worker_instance
    assert coordinator._video_sidebar_workers == [worker_instance]
    pool.start.assert_called_once_with(worker_instance, -1)

    image = QImage(216, 144, QImage.Format.Format_ARGB32)
    image.fill(0xFF336699)
    ready_signal.callback(VideoSidebarPreviewResult(image=image, stats=None), 1)

    coordinator._session.set_color_stats.assert_called_once_with(None)
    coordinator._pipeline_loader.prepare_sidebar_preview_inline.assert_called_once_with(
        image,
        target_height=72,
        full_res_image_for_fallback=image,
    )
    coordinator._apply_session_adjustments_to_viewer.assert_called_once_with()

    finished_signal.callback(1)
    assert coordinator._video_sidebar_worker is None
    assert coordinator._video_sidebar_workers == []


def test_estimate_video_trim_thumbnail_request_scales_for_portrait_video() -> None:
    coordinator = EditCoordinator.__new__(EditCoordinator)
    trim_bar = Mock()
    trim_bar.thumbnail_view_width.return_value = 1000
    coordinator._ui = SimpleNamespace(video_trim_bar=trim_bar)

    with patch(
        "iPhoto.gui.coordinators.edit_coordinator.probe_video_rotation",
        return_value=(0, 540, 960),
    ):
        width, count = EditCoordinator._estimate_video_trim_thumbnail_request(
            coordinator,
            Path("/fake/video.mp4"),
        )

    assert width == 1000
    assert count == 45


def test_probe_video_frame_step_ms_uses_metadata_frame_rate() -> None:
    coordinator = EditCoordinator.__new__(EditCoordinator)

    with patch(
        "iPhoto.gui.coordinators.edit_coordinator.read_video_meta",
        return_value={"frame_rate": 59.94},
    ):
        step_ms = EditCoordinator._probe_video_frame_step_ms(
            coordinator,
            Path("/fake/video.mp4"),
        )

    assert step_ms == 17


def test_start_video_edit_load_sets_trim_before_queueing_thumbnails() -> None:
    coordinator = EditCoordinator.__new__(EditCoordinator)
    video_area = Mock()
    trim_bar = Mock()
    order: list[object] = []
    trim_bar.clear.side_effect = lambda: order.append("clear")
    trim_bar.set_trim_ratios.side_effect = lambda *_: order.append("trim")
    trim_bar.set_playhead_ratio.side_effect = lambda *_: order.append("playhead")
    trim_bar.set_playing.side_effect = lambda *_: order.append("playing")
    coordinator._ui = SimpleNamespace(video_area=video_area, video_trim_bar=trim_bar)
    coordinator._session = SimpleNamespace(
        values=lambda: {
            VIDEO_TRIM_IN_KEY: 1.0,
            VIDEO_TRIM_OUT_KEY: 4.0,
        },
        set_values=Mock(),
    )
    coordinator._emit_video_trim_diag = Mock()
    coordinator._probe_video_frame_step_ms = Mock(return_value=17)
    coordinator._probe_video_duration_sec = Mock(return_value=5.0)
    coordinator._resolve_session_adjustments = Mock(return_value={})
    coordinator._queue_video_trim_thumbnails = Mock(
        side_effect=lambda duration: order.append(("queue", duration))
    )
    coordinator._video_trim_thumbnail_timer = Mock(stop=Mock())
    coordinator._video_sidebar_preview_timer = Mock(stop=Mock())
    coordinator._video_color_stats = None
    coordinator._pending_video_duration_sec = None

    EditCoordinator._start_video_edit_load(coordinator, Path("/fake/video.mp4"))

    video_area.load_video.assert_called_once_with(
        Path("/fake/video.mp4"),
        adjustments={},
        trim_range_ms=(1000, 4000),
        adjusted_preview=True,
    )
    trim_bar.set_trim_ratios.assert_called_once_with(0.2, 0.8)
    trim_bar.set_playhead_ratio.assert_called_once_with(0.2)
    coordinator._queue_video_trim_thumbnails.assert_called_once_with(5.0)
    assert order.index("trim") < order.index(("queue", 5.0))


def test_thumbnail_image_handler_applies_trim_before_adding(qapp) -> None:
    coordinator = EditCoordinator.__new__(EditCoordinator)
    trim_bar = Mock()
    order: list[str] = []
    trim_bar.add_thumbnail.side_effect = lambda *_: order.append("add")
    coordinator._ui = SimpleNamespace(video_trim_bar=trim_bar)
    coordinator._video_thumbnail_generation = 1
    coordinator._video_trim_diag = {}
    coordinator._current_source = Path("/fake/video.mp4")
    coordinator._emit_video_trim_diag = Mock()
    coordinator._apply_video_trim_from_session = Mock(side_effect=lambda: order.append("trim"))
    coordinator._session = object()
    image = QImage(8, 8, QImage.Format.Format_ARGB32)

    with patch("iPhoto.gui.coordinators.edit_coordinator.QPixmap.fromImage", return_value=Mock()):
        EditCoordinator._handle_video_trim_thumbnail_image(coordinator, image, 1)

    assert order == ["trim", "add"]


def test_thumbnail_ready_handler_applies_trim_before_setting(qapp) -> None:
    coordinator = EditCoordinator.__new__(EditCoordinator)
    trim_bar = Mock()
    order: list[str] = []
    trim_bar.set_thumbnails.side_effect = lambda *_: order.append("thumbs")
    coordinator._ui = SimpleNamespace(video_trim_bar=trim_bar)
    coordinator._video_thumbnail_generation = 1
    coordinator._video_trim_diag = {}
    coordinator._current_source = Path("/fake/video.mp4")
    coordinator._emit_video_trim_diag = Mock()
    coordinator._apply_video_trim_from_session = Mock(side_effect=lambda: order.append("trim"))
    coordinator._session = object()
    image = QImage(8, 8, QImage.Format.Format_ARGB32)

    with patch("iPhoto.gui.coordinators.edit_coordinator.QPixmap.fromImage", return_value=Mock()):
        EditCoordinator._handle_video_trim_thumbnails_ready(coordinator, [image], 1)

    assert order == ["trim", "thumbs"]


def test_video_play_pause_shortcut_toggles_edit_video_transport() -> None:
    coordinator = EditCoordinator.__new__(EditCoordinator)
    video_area = Mock()
    video_area.is_playing.return_value = True
    coordinator._ui = SimpleNamespace(video_area=video_area)
    coordinator._session = object()
    coordinator._current_source = Path("/fake/video.mp4")
    coordinator._router = SimpleNamespace(is_edit_view_active=lambda: True)

    EditCoordinator._handle_video_play_pause_shortcut(coordinator)

    video_area.pause.assert_called_once_with()
    video_area.note_activity.assert_called_once_with()


def test_video_frame_step_shortcut_pauses_and_seeks() -> None:
    coordinator = EditCoordinator.__new__(EditCoordinator)
    player_bar = Mock()
    player_bar.position.return_value = 1000
    video_area = Mock()
    video_area.player_bar = player_bar
    coordinator._ui = SimpleNamespace(video_area=video_area)
    coordinator._session = object()
    coordinator._current_source = Path("/fake/video.mp4")
    coordinator._router = SimpleNamespace(is_edit_view_active=lambda: True)
    coordinator._video_frame_step_ms = 17

    with patch(
        "iPhoto.gui.coordinators.edit_coordinator.QApplication.focusWidget",
        return_value=None,
    ):
        EditCoordinator._handle_video_frame_step_shortcut(coordinator, 1)

    video_area.pause.assert_called_once_with()
    video_area.seek.assert_called_once_with(1017)
    video_area.note_activity.assert_called_once_with()


def test_video_frame_step_shortcut_yields_to_slider_focus(qapp) -> None:
    coordinator = EditCoordinator.__new__(EditCoordinator)
    player_bar = Mock()
    player_bar.position.return_value = 1000
    video_area = Mock()
    video_area.player_bar = player_bar
    coordinator._ui = SimpleNamespace(video_area=video_area)
    coordinator._session = object()
    coordinator._current_source = Path("/fake/video.mp4")
    coordinator._router = SimpleNamespace(is_edit_view_active=lambda: True)
    coordinator._video_frame_step_ms = 17
    slider = QSlider()

    with patch(
        "iPhoto.gui.coordinators.edit_coordinator.QApplication.focusWidget",
        return_value=slider,
    ):
        EditCoordinator._handle_video_frame_step_shortcut(coordinator, 1)

    video_area.pause.assert_not_called()
    video_area.seek.assert_not_called()


def test_handle_trim_in_ratio_changed_keeps_existing_out_point() -> None:
    coordinator = EditCoordinator.__new__(EditCoordinator)
    session = SimpleNamespace(set_values=Mock())
    trim_bar = Mock()
    trim_bar.trim_ratios.return_value = (0.3, 0.8)
    coordinator._ui = SimpleNamespace(video_trim_bar=trim_bar)
    coordinator._session = session
    coordinator._is_video_source = Mock(return_value=True)
    coordinator._video_duration_sec = Mock(return_value=10.0)
    coordinator._canonical_trim_updates = Mock(
        return_value={
            VIDEO_TRIM_IN_KEY: 3.0,
            VIDEO_TRIM_OUT_KEY: 8.0,
        }
    )

    EditCoordinator._handle_trim_in_ratio_changed(coordinator, 0.3)

    coordinator._canonical_trim_updates.assert_called_once_with(3.0, 8.0, 10.0)
    session.set_values.assert_called_once_with(
        {
            VIDEO_TRIM_IN_KEY: 3.0,
            VIDEO_TRIM_OUT_KEY: 8.0,
        },
        emit_individual=False,
    )


def test_handle_trim_out_ratio_changed_uses_ratio_before_seconds() -> None:
    coordinator = EditCoordinator.__new__(EditCoordinator)
    session = SimpleNamespace(set_values=Mock())
    trim_bar = Mock()
    trim_bar.trim_ratios.return_value = (0.2, 0.7)
    coordinator._ui = SimpleNamespace(video_trim_bar=trim_bar)
    coordinator._session = session
    coordinator._is_video_source = Mock(return_value=True)
    coordinator._video_duration_sec = Mock(return_value=10.0)
    coordinator._canonical_trim_updates = Mock(
        return_value={
            VIDEO_TRIM_IN_KEY: 2.0,
            VIDEO_TRIM_OUT_KEY: 7.0,
        }
    )

    EditCoordinator._handle_trim_out_ratio_changed(coordinator, 0.7)

    coordinator._canonical_trim_updates.assert_called_once_with(2.0, 7.0, 10.0)
    session.set_values.assert_called_once_with(
        {
            VIDEO_TRIM_IN_KEY: 2.0,
            VIDEO_TRIM_OUT_KEY: 7.0,
        },
        emit_individual=False,
    )


def test_leave_edit_mode_restores_transition_height_flow() -> None:
    """Leaving edit mode should call transition manager with animation and filmstrip flag."""

    coordinator = EditCoordinator.__new__(EditCoordinator)
    source = Path("/fake/image.jpg")
    video_area = Mock()
    video_area.adjusted_preview_enabled.return_value = False
    video_area._diag_surface_name.return_value = "stub"
    toggle_action = Mock()
    toggle_action.isChecked.return_value = False

    coordinator._current_source = source
    coordinator._session = None
    coordinator._fullscreen_manager = Mock()
    coordinator._fullscreen_manager.is_in_fullscreen.return_value = False
    coordinator._active_edit_viewport = Mock(return_value=Mock())
    coordinator._preview_manager = Mock()
    coordinator._zoom_handler = Mock()
    coordinator._header_controller = Mock()
    coordinator._theme_controller = None
    coordinator._media_session = None
    coordinator._router = Mock()
    coordinator._transition_manager = Mock()
    coordinator._ui = SimpleNamespace(
        video_area=video_area,
        video_trim_bar=Mock(),
        edit_sidebar=Mock(),
        edit_image_viewer=Mock(),
        toggle_filmstrip_action=toggle_action,
    )
    coordinator._video_color_stats = None
    coordinator._pending_video_duration_sec = None
    coordinator._video_trim_thumbnail_timer = Mock()
    coordinator._video_sidebar_preview_timer = Mock()
    coordinator._video_thumbnail_generation = 0
    coordinator._video_sidebar_generation = 0
    coordinator._video_trim_worker = None
    coordinator._video_trim_diag = {}
    coordinator._video_frame_step_ms = 42

    with patch(
        "iPhoto.gui.coordinators.edit_coordinator.viewer_surface_color",
        return_value=None,
    ):
        EditCoordinator.leave_edit_mode(coordinator)

    video_area.set_edit_mode_active.assert_called_once_with(False)
    coordinator._router.show_detail.assert_called_once_with()
    coordinator._transition_manager.leave_edit_mode.assert_called_once_with(
        animate=True,
        show_filmstrip=False,
    )

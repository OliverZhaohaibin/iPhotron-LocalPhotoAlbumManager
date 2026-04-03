from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QSlider

from iPhoto.core.adjustment_mapping import VIDEO_TRIM_IN_KEY, VIDEO_TRIM_OUT_KEY
from iPhoto.gui.coordinators.edit_coordinator import EditCoordinator


@pytest.fixture
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def test_restore_detail_video_preview_reloads_and_restarts_playback() -> None:
    coordinator = EditCoordinator.__new__(EditCoordinator)
    video_area = Mock()
    coordinator._ui = SimpleNamespace(video_area=video_area)

    source = Path("/fake/video.mp4")
    raw_adjustments = {"Crop_W": 0.8}
    render_adjustments = {"Crop_W": 0.8, "Exposure": 0.2}

    with patch(
        "iPhoto.gui.coordinators.edit_coordinator.sidecar.load_adjustments",
        return_value=raw_adjustments,
    ), patch(
        "iPhoto.gui.coordinators.edit_coordinator.sidecar.trim_is_non_default",
        return_value=True,
    ), patch(
        "iPhoto.gui.coordinators.edit_coordinator.sidecar.has_non_default_adjustments",
        return_value=True,
    ), patch(
        "iPhoto.gui.coordinators.edit_coordinator.sidecar.normalise_video_trim",
        return_value=(1.25, 4.5),
    ), patch(
        "iPhoto.gui.coordinators.edit_coordinator.sidecar.resolve_render_adjustments",
        return_value=render_adjustments,
    ):
        EditCoordinator._restore_detail_video_preview(coordinator, source)

    video_area.load_video.assert_called_once_with(
        source,
        adjustments=render_adjustments,
        trim_range_ms=(1250, 4500),
        adjusted_preview=True,
    )
    video_area.play.assert_called_once_with()


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

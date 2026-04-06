from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for playback coordinator tests", exc_type=ImportError)
pytest.importorskip("PySide6.QtCore", reason="QtCore not available", exc_type=ImportError)

from iPhoto.gui.coordinators.playback_coordinator import PlaybackCoordinator
from iPhoto.gui.ui.models.roles import Roles


def test_do_play_asset_keeps_rotate_enabled_for_video() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._asset_vm = Mock()
    coordinator._asset_vm.rowCount.return_value = 1
    coordinator._asset_vm.index.return_value = object()

    source = Path("/fake/video.mp4")
    info = {"duration": 3.5}

    def _data(_idx, role):
        if role == Roles.ABS:
            return str(source)
        if role == Roles.IS_VIDEO:
            return True
        if role == Roles.IS_LIVE:
            return False
        if role == Roles.FEATURED:
            return False
        if role == Roles.INFO:
            return info
        return None

    coordinator._asset_vm.data.side_effect = _data
    coordinator._player_view = SimpleNamespace(
        show_video_surface=Mock(),
        video_area=Mock(),
        show_live_badge=Mock(),
        set_live_replay_enabled=Mock(),
    )
    coordinator._player_bar = Mock()
    coordinator._zoom_slider = Mock()
    coordinator._zoom_widget = Mock()
    coordinator._favorite_button = Mock()
    coordinator._info_button = Mock()
    coordinator._share_button = Mock()
    coordinator._edit_button = Mock()
    coordinator._rotate_button = Mock()
    coordinator._update_favorite_icon = Mock()
    coordinator._info_panel = None
    coordinator._active_live_motion = None
    coordinator._active_live_still = None
    coordinator._is_playing = False

    with patch(
        "iPhoto.gui.coordinators.playback_coordinator.sidecar.load_adjustments",
        return_value={},
    ), patch(
        "iPhoto.gui.coordinators.playback_coordinator.sidecar.trim_is_non_default",
        return_value=False,
    ), patch(
        "iPhoto.gui.coordinators.playback_coordinator.sidecar.video_requires_adjusted_preview",
        return_value=False,
    ), patch(
        "iPhoto.gui.coordinators.playback_coordinator.sidecar.normalise_video_trim",
        return_value=(0.0, 3.5),
    ):
        PlaybackCoordinator._do_play_asset(coordinator, 0)

    coordinator._rotate_button.setEnabled.assert_called_with(True)


def test_do_play_asset_keeps_rotate_only_video_on_native_playback_surface() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._asset_vm = Mock()
    coordinator._asset_vm.rowCount.return_value = 1
    coordinator._asset_vm.index.return_value = object()

    source = Path("/fake/video.mp4")
    info = {"duration": 3.5}

    def _data(_idx, role):
        if role == Roles.ABS:
            return str(source)
        if role == Roles.IS_VIDEO:
            return True
        if role == Roles.IS_LIVE:
            return False
        if role == Roles.FEATURED:
            return False
        if role == Roles.INFO:
            return info
        return None

    coordinator._asset_vm.data.side_effect = _data
    coordinator._player_view = SimpleNamespace(
        show_video_surface=Mock(),
        video_area=Mock(),
        show_live_badge=Mock(),
        set_live_replay_enabled=Mock(),
    )
    coordinator._player_bar = Mock()
    coordinator._zoom_slider = Mock()
    coordinator._zoom_widget = Mock()
    coordinator._favorite_button = Mock()
    coordinator._info_button = Mock()
    coordinator._share_button = Mock()
    coordinator._edit_button = Mock()
    coordinator._rotate_button = Mock()
    coordinator._update_favorite_icon = Mock()
    coordinator._info_panel = None
    coordinator._active_live_motion = None
    coordinator._active_live_still = None
    coordinator._is_playing = False

    with patch(
        "iPhoto.gui.coordinators.playback_coordinator.sidecar.load_adjustments",
        return_value={"Crop_Rotate90": 3.0},
    ), patch(
        "iPhoto.gui.coordinators.playback_coordinator.sidecar.trim_is_non_default",
        return_value=False,
    ), patch(
        "iPhoto.gui.coordinators.playback_coordinator.sidecar.video_requires_adjusted_preview",
        return_value=False,
    ), patch(
        "iPhoto.gui.coordinators.playback_coordinator.sidecar.normalise_video_trim",
        return_value=(0.0, 3.5),
    ):
        PlaybackCoordinator._do_play_asset(coordinator, 0)

    coordinator._player_view.video_area.load_video.assert_called_once_with(
        source,
        adjustments={"Crop_Rotate90": 3.0},
        trim_range_ms=None,
        adjusted_preview=False,
    )


def test_rotate_current_asset_routes_video_rotation_through_video_area() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._current_row = 0
    coordinator._asset_vm = Mock()
    coordinator._asset_vm.index.return_value = object()

    source = Path("/fake/video.mp4")

    def _data(_idx, role):
        if role == Roles.ABS:
            return str(source)
        if role == Roles.IS_VIDEO:
            return True
        return None

    coordinator._asset_vm.data.side_effect = _data
    coordinator._asset_vm.invalidate_thumbnail = Mock()
    coordinator._player_view = SimpleNamespace(
        video_area=Mock(rotate_image_ccw=Mock(return_value={"Crop_Rotate90": 3.0})),
        image_viewer=Mock(rotate_image_ccw=Mock()),
    )
    coordinator._navigation = None

    with patch(
        "iPhoto.gui.coordinators.playback_coordinator.sidecar.load_adjustments",
        return_value={"Exposure": 0.2},
    ), patch(
        "iPhoto.gui.coordinators.playback_coordinator.sidecar.save_adjustments",
    ) as save_mock:
        PlaybackCoordinator.rotate_current_asset(coordinator)

    coordinator._player_view.video_area.rotate_image_ccw.assert_called_once_with()
    coordinator._player_view.image_viewer.rotate_image_ccw.assert_not_called()
    save_mock.assert_called_once_with(
        source,
        {"Exposure": 0.2, "Crop_Rotate90": 3.0},
    )
    coordinator._asset_vm.invalidate_thumbnail.assert_called_once_with(str(source))


# ---------------------------------------------------------------------------
# Trim remapping tests for _on_video_duration_changed / _on_video_position_changed
# and _on_seek
# ---------------------------------------------------------------------------

def make_playback_coordinator(trim_in_ms: int = 0, trim_out_ms: int = 0) -> PlaybackCoordinator:
    """Return a partially-constructed PlaybackCoordinator with mocked dependencies."""
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._player_bar = Mock()
    coordinator._trim_in_ms = trim_in_ms
    coordinator._trim_out_ms = trim_out_ms
    video_area = Mock()
    video_area.is_edit_mode_active.return_value = False
    video_area.trim_range_ms.return_value = (trim_in_ms, trim_out_ms)
    coordinator._player_view = SimpleNamespace(video_area=video_area)
    return coordinator


def test_on_video_duration_changed_with_trim_shows_trimmed_duration() -> None:
    """When a trim range is active the player bar should show the trimmed duration."""
    coordinator = make_playback_coordinator(trim_in_ms=2000, trim_out_ms=7000)
    coordinator._player_view.video_area.trim_range_ms.return_value = (2000, 7000)

    PlaybackCoordinator._on_video_duration_changed(coordinator, 10_000)

    coordinator._player_bar.set_duration.assert_called_once_with(5000)  # 7000 - 2000


def test_on_video_duration_changed_without_trim_shows_full_duration() -> None:
    """When no trim is active (in == out == 0) the full clip duration is displayed."""
    coordinator = make_playback_coordinator(trim_in_ms=0, trim_out_ms=0)
    coordinator._player_view.video_area.trim_range_ms.return_value = (0, 0)

    PlaybackCoordinator._on_video_duration_changed(coordinator, 10_000)

    coordinator._player_bar.set_duration.assert_called_once_with(10_000)


def test_on_video_duration_changed_skipped_in_edit_mode() -> None:
    """In edit mode _on_video_duration_changed must return without touching the player bar."""
    coordinator = make_playback_coordinator()
    coordinator._player_view.video_area.is_edit_mode_active.return_value = True

    PlaybackCoordinator._on_video_duration_changed(coordinator, 10_000)

    coordinator._player_bar.set_duration.assert_not_called()


def test_on_video_position_changed_remaps_position_relative_to_trim_in() -> None:
    """Position is displayed relative to trim-in so the bar always starts at 0."""
    coordinator = make_playback_coordinator(trim_in_ms=3000, trim_out_ms=8000)

    PlaybackCoordinator._on_video_position_changed(coordinator, 5000)

    coordinator._player_bar.set_position.assert_called_once_with(2000)  # 5000 - 3000


def test_on_video_position_changed_clamps_to_zero_when_before_trim_in() -> None:
    """Positions before the trim-in point are clamped to 0 (not negative)."""
    coordinator = make_playback_coordinator(trim_in_ms=3000, trim_out_ms=8000)

    PlaybackCoordinator._on_video_position_changed(coordinator, 1000)

    coordinator._player_bar.set_position.assert_called_once_with(0)


def test_on_video_position_changed_skipped_in_edit_mode() -> None:
    """In edit mode position changes must not propagate to the player bar."""
    coordinator = make_playback_coordinator(trim_in_ms=1000, trim_out_ms=5000)
    coordinator._player_view.video_area.is_edit_mode_active.return_value = True

    PlaybackCoordinator._on_video_position_changed(coordinator, 2000)

    coordinator._player_bar.set_position.assert_not_called()


def test_on_seek_adds_trim_in_offset_before_forwarding_to_video_area() -> None:
    """Seeking from the bar's 0-based position must add the trim-in offset."""
    coordinator = make_playback_coordinator(trim_in_ms=4000, trim_out_ms=9000)

    PlaybackCoordinator._on_seek(coordinator, 1000)

    coordinator._player_view.video_area.seek.assert_called_once_with(5000)  # 1000 + 4000

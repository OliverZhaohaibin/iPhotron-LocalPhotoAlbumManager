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
        "iPhoto.gui.coordinators.playback_coordinator.sidecar.has_non_default_adjustments",
        return_value=False,
    ), patch(
        "iPhoto.gui.coordinators.playback_coordinator.sidecar.normalise_video_trim",
        return_value=(0.0, 3.5),
    ):
        PlaybackCoordinator._do_play_asset(coordinator, 0)

    coordinator._rotate_button.setEnabled.assert_called_with(True)


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

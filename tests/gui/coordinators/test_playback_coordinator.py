from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for playback coordinator tests", exc_type=ImportError)

from iPhoto.gui.coordinators.playback_coordinator import PlaybackCoordinator
from iPhoto.gui.viewmodels.detail_viewmodel import DetailPresentation


def _make_presentation(*, path: str = "/fake/video.mp4", is_video: bool = True, is_favorite: bool = False):
    return DetailPresentation(
        row=0,
        path=Path(path),
        is_video=is_video,
        is_live=False,
        is_favorite=is_favorite,
        info={"dur": 3.5, "abs": path, "is_video": is_video},
        location="Paris",
        timestamp=None,
        can_edit=True,
        can_rotate=True,
        can_share=True,
        can_toggle_favorite=True,
        info_panel_visible=False,
        live_motion_rel=None,
        live_motion_abs=None,
    )


def test_play_asset_delegates_to_detail_vm() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._asset_model = Mock(rowCount=Mock(return_value=3))
    coordinator._detail_vm = Mock()
    coordinator._pending_play_row = None
    coordinator._play_debounce = Mock(start=Mock())

    PlaybackCoordinator.play_asset(coordinator, 2)

    assert coordinator._pending_play_row == 2
    coordinator._play_debounce.start.assert_called_once_with()


def test_handle_presentation_changed_renders_video_and_updates_header() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._current_presentation = None
    coordinator._asset_model = Mock(index=Mock(return_value=Mock(isValid=Mock(return_value=True))))
    coordinator._asset_model.set_current_row = Mock()
    coordinator.assetChanged = Mock(emit=Mock())
    coordinator._update_header = Mock()
    coordinator._sync_filmstrip_selection = Mock()
    coordinator._render_presentation = Mock()

    presentation = _make_presentation()
    PlaybackCoordinator._handle_presentation_changed(coordinator, presentation)

    coordinator._asset_model.set_current_row.assert_called_once_with(0)
    coordinator.assetChanged.emit.assert_called_once_with(0)
    coordinator._update_header.assert_called_once_with(presentation)
    coordinator._sync_filmstrip_selection.assert_called_once_with(0)
    coordinator._render_presentation.assert_called_once_with(presentation)


def test_handle_presentation_changed_skips_full_rerender_for_same_asset() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    presentation = _make_presentation(is_favorite=False)
    updated = _make_presentation(is_favorite=True)
    coordinator._current_presentation = presentation
    coordinator._asset_model = Mock()
    coordinator._asset_model.set_current_row = Mock()
    coordinator.assetChanged = Mock(emit=Mock())
    coordinator._update_header = Mock()
    coordinator._sync_filmstrip_selection = Mock()
    coordinator._render_presentation = Mock()
    coordinator._update_favorite_icon = Mock()
    coordinator._info_panel = None

    PlaybackCoordinator._handle_presentation_changed(coordinator, updated)

    coordinator._render_presentation.assert_not_called()
    coordinator._update_favorite_icon.assert_called_once_with(True)


def test_handle_rotate_requested_routes_video_rotation_through_video_area() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._adjustment_committer = Mock(commit=Mock(return_value=True))
    coordinator._player_view = SimpleNamespace(
        video_area=Mock(rotate_image_ccw=Mock(return_value={"Crop_Rotate90": 3.0})),
        image_viewer=Mock(rotate_image_ccw=Mock()),
    )

    with patch(
        "iPhoto.gui.coordinators.playback_coordinator.sidecar.load_adjustments",
        return_value={"Exposure": 0.2},
    ):
        PlaybackCoordinator._handle_rotate_requested(coordinator, Path("/fake/video.mp4"), True)

    coordinator._player_view.video_area.rotate_image_ccw.assert_called_once_with()
    coordinator._adjustment_committer.commit.assert_called_once_with(
        Path("/fake/video.mp4"),
        {"Exposure": 0.2, "Crop_Rotate90": 3.0},
        reason="rotate",
    )


def test_adjustment_restore_is_deferred_while_edit_session_is_active() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._pending_restore_path = None
    coordinator._pending_restore_reason = None
    coordinator._detail_vm = Mock()
    coordinator._is_edit_session_active = Mock(return_value=True)
    source = Path("/fake/video.mp4")

    PlaybackCoordinator._handle_restore_requested(coordinator, source, "edit_done")

    assert coordinator._pending_restore_path == source
    assert coordinator._pending_restore_reason == "edit_done"
    coordinator._detail_vm.restore_after_adjustment.assert_not_called()

    PlaybackCoordinator._handle_detail_view_shown(coordinator)

    coordinator._detail_vm.restore_after_adjustment.assert_called_once_with(source, "edit_done")

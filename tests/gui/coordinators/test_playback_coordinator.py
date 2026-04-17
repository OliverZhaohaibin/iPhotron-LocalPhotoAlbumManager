from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for playback coordinator tests", exc_type=ImportError)

from iPhoto.gui.coordinators.playback_coordinator import PlaybackCoordinator
from iPhoto.gui.ui.tasks.info_panel_metadata_worker import InfoPanelMetadataResult
from iPhoto.gui.viewmodels.detail_viewmodel import DetailPresentation


def _make_presentation(
    *,
    path: str = "/fake/video.mp4",
    asset_id: str = "asset-1",
    is_video: bool = True,
    is_live: bool = False,
    is_favorite: bool = False,
    reload_token: int = 0,
):
    return DetailPresentation(
        row=0,
        asset_id=asset_id,
        path=Path(path),
        is_video=is_video,
        is_live=is_live,
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
        video_adjustments={"Exposure": 0.2} if is_video else None,
        video_trim_range_ms=(1000, 3000) if is_video else None,
        video_adjusted_preview=is_video,
        reload_token=reload_token,
    )


def test_play_asset_dispatches_immediately_when_idle() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._asset_model = Mock(rowCount=Mock(return_value=3))
    coordinator._detail_vm = Mock()
    coordinator._pending_play_row = None
    coordinator._play_debounce = Mock(isActive=Mock(return_value=False), start=Mock())
    coordinator._dispatch_play_row = Mock()
    coordinator._play_profile_started_at = None
    coordinator._play_profile_row = None

    PlaybackCoordinator.play_asset(coordinator, 2)

    assert coordinator._pending_play_row is None
    coordinator._dispatch_play_row.assert_called_once_with(2, reason="immediate")
    coordinator._play_debounce.start.assert_called_once_with()


def test_play_asset_queues_latest_row_while_cooldown_is_active() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._asset_model = Mock(rowCount=Mock(return_value=3))
    coordinator._detail_vm = Mock()
    coordinator._pending_play_row = None
    coordinator._play_debounce = Mock(isActive=Mock(return_value=True), start=Mock())
    coordinator._dispatch_play_row = Mock()
    coordinator._play_profile_started_at = None
    coordinator._play_profile_row = None

    PlaybackCoordinator.play_asset(coordinator, 1)

    assert coordinator._pending_play_row == 1
    coordinator._dispatch_play_row.assert_not_called()
    coordinator._play_debounce.start.assert_not_called()


def test_execute_pending_play_flushes_row_and_restarts_cooldown() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._pending_play_row = 2
    coordinator._play_debounce = Mock(start=Mock())
    coordinator._dispatch_play_row = Mock()

    PlaybackCoordinator._execute_pending_play(coordinator)

    assert coordinator._pending_play_row is None
    coordinator._dispatch_play_row.assert_called_once_with(2, reason="debounced")
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


def test_handle_presentation_changed_rerenders_same_asset_when_reload_token_changes() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._current_presentation = _make_presentation(
        path="/fake/video.mp4",
        reload_token=1,
    )
    presentation = _make_presentation(
        path="/fake/video.mp4",
        reload_token=2,
    )
    coordinator._asset_model = Mock()
    coordinator._asset_model.set_current_row = Mock()
    coordinator.assetChanged = Mock(emit=Mock())
    coordinator._update_header = Mock()
    coordinator._sync_filmstrip_selection = Mock()
    coordinator._render_presentation = Mock()
    coordinator._update_favorite_icon = Mock()
    coordinator._clear_play_profile = Mock()
    coordinator._info_panel = None

    PlaybackCoordinator._handle_presentation_changed(coordinator, presentation)

    coordinator._render_presentation.assert_called_once_with(presentation)
    coordinator._update_favorite_icon.assert_not_called()
    coordinator._clear_play_profile.assert_not_called()


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


def test_render_presentation_uses_viewmodel_video_state() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    video_area = Mock(load_video=Mock(), play=Mock(), reset_zoom=Mock())
    coordinator._player_view = Mock(
        show_video_surface=Mock(),
        video_area=video_area,
    )
    coordinator._favorite_button = Mock(setEnabled=Mock())
    coordinator._info_button = Mock(setEnabled=Mock())
    coordinator._share_button = Mock(setEnabled=Mock())
    coordinator._edit_button = Mock(setEnabled=Mock())
    coordinator._rotate_button = Mock(setEnabled=Mock())
    coordinator._update_favorite_icon = Mock()
    coordinator._zoom_slider = Mock(blockSignals=Mock(), setValue=Mock())
    coordinator._player_bar = Mock(setEnabled=Mock(), set_playback_state=Mock(), set_position=Mock())
    coordinator._zoom_handler = Mock(set_viewer=Mock())
    coordinator._zoom_widget = Mock(show=Mock())
    coordinator._info_panel = None
    coordinator._clear_play_profile = Mock()

    presentation = _make_presentation()

    PlaybackCoordinator._render_presentation(coordinator, presentation)

    video_area.load_video.assert_called_once_with(
        Path("/fake/video.mp4"),
        adjustments={"Exposure": 0.2},
        trim_range_ms=(1000, 3000),
        adjusted_preview=True,
    )
    assert coordinator._trim_in_ms == 1000
    assert coordinator._trim_out_ms == 3000


def test_reset_for_gallery_closes_info_panel_and_clears_viewmodel_state() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._player_view = Mock(
        video_area=Mock(stop=Mock()),
        show_placeholder=Mock(),
    )
    coordinator._player_bar = Mock(setEnabled=Mock())
    coordinator._is_playing = True
    coordinator._current_presentation = _make_presentation()
    coordinator._detail_vm = Mock(hide_info_panel=Mock())
    coordinator._update_header = Mock()
    coordinator._info_panel = Mock(close=Mock())
    coordinator._hide_face_name_overlay = Mock()

    PlaybackCoordinator.reset_for_gallery(coordinator)

    coordinator._player_view.video_area.stop.assert_called_once_with()
    coordinator._player_view.show_placeholder.assert_called_once_with()
    coordinator._player_bar.setEnabled.assert_called_once_with(False)
    coordinator._detail_vm.hide_info_panel.assert_called_once_with(refresh_presentation=False)
    coordinator._update_header.assert_called_once_with(None)
    coordinator._info_panel.close.assert_called_once_with()
    coordinator._hide_face_name_overlay.assert_called_once_with(clear_annotations=True)


def test_set_face_name_display_enabled_refreshes_current_presentation() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._current_presentation = _make_presentation(
        path="/fake/photo.jpg",
        asset_id="asset-photo",
        is_video=False,
    )
    coordinator._refresh_face_name_overlay_for_current_presentation = Mock()

    PlaybackCoordinator.set_face_name_display_enabled(coordinator, True)

    assert coordinator._show_face_names is True
    coordinator._refresh_face_name_overlay_for_current_presentation.assert_called_once_with()


def test_refresh_face_name_overlay_loads_annotations_for_still_image() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    overlay = Mock()
    coordinator._face_name_overlay = overlay
    coordinator._show_face_names = True
    coordinator._active_live_motion = None
    coordinator._player_view = SimpleNamespace(
        video_area=SimpleNamespace(is_edit_mode_active=lambda: False),
    )
    coordinator._load_face_name_annotations = Mock(return_value=[Mock(face_id="face-1")])

    PlaybackCoordinator._refresh_face_name_overlay_for_presentation(
        coordinator,
        _make_presentation(
            path="/fake/photo.jpg",
            asset_id="asset-photo",
            is_video=False,
        ),
    )

    coordinator._load_face_name_annotations.assert_called_once_with("asset-photo")
    overlay.set_annotations.assert_called_once()
    overlay.set_overlay_active.assert_called_once_with(True)


def test_refresh_face_name_overlay_hides_for_video() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._face_name_overlay = Mock()
    coordinator._hide_face_name_overlay = Mock()
    coordinator._show_face_names = True

    PlaybackCoordinator._refresh_face_name_overlay_for_presentation(
        coordinator,
        _make_presentation(is_video=True),
    )

    coordinator._hide_face_name_overlay.assert_called_once_with(clear_annotations=True)


def test_handle_face_name_rename_submitted_updates_overlay_and_dashboard() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._people_service = Mock(rename_cluster=Mock())
    coordinator._current_presentation = _make_presentation(
        path="/fake/photo.jpg",
        asset_id="asset-photo",
        is_video=False,
    )
    coordinator._refresh_face_name_overlay_for_current_presentation = Mock()
    coordinator._people_dashboard_refresh_callback = Mock()

    PlaybackCoordinator._handle_face_name_rename_submitted(
        coordinator,
        "person-a",
        "  Alice  ",
    )

    coordinator._people_service.rename_cluster.assert_called_once_with("person-a", "Alice")
    coordinator._refresh_face_name_overlay_for_current_presentation.assert_called_once_with()
    coordinator._people_dashboard_refresh_callback.assert_called_once_with()


def test_handle_people_snapshot_committed_refreshes_current_overlay() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._current_presentation = _make_presentation(
        path="/fake/photo.jpg",
        asset_id="asset-photo",
        is_video=False,
    )
    coordinator._refresh_face_name_overlay_for_presentation = Mock()

    PlaybackCoordinator.handle_people_snapshot_committed(coordinator, object())

    coordinator._refresh_face_name_overlay_for_presentation.assert_called_once_with(
        coordinator._current_presentation
    )


def test_handle_info_panel_dismissed_clears_viewmodel_state() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._detail_vm = Mock(hide_info_panel=Mock())

    PlaybackCoordinator._handle_info_panel_dismissed(coordinator)

    coordinator._detail_vm.hide_info_panel.assert_called_once_with(refresh_presentation=False)


def test_refresh_info_panel_sets_loading_state_and_queues_background_enrichment() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._info_panel = Mock()
    coordinator._queue_info_panel_metadata_enrichment = Mock()

    PlaybackCoordinator._refresh_info_panel(
        coordinator,
        {
            "abs": "/fake/image.jpg",
            "rel": "image.jpg",
            "name": "image.jpg",
            "is_video": False,
        },
    )

    coordinator._info_panel.set_asset_metadata.assert_called_once()
    displayed = coordinator._info_panel.set_asset_metadata.call_args.args[0]
    assert displayed["_metadata_loading"] is True
    coordinator._queue_info_panel_metadata_enrichment.assert_called_once_with(
        Path("/fake/image.jpg"),
        is_video=False,
    )


def test_refresh_info_panel_uses_cached_metadata_without_queueing_worker() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._info_panel = Mock()
    coordinator._info_panel_metadata_cache = {
        str(Path("/fake/image.jpg")): {
            "iso": 320,
            "f_number": 2.8,
        },
    }
    coordinator._info_panel_metadata_inflight = set()
    coordinator._queue_info_panel_metadata_enrichment = Mock()

    PlaybackCoordinator._refresh_info_panel(
        coordinator,
        {
            "abs": "/fake/image.jpg",
            "rel": "image.jpg",
            "name": "image.jpg",
            "is_video": False,
        },
    )

    coordinator._info_panel.set_asset_metadata.assert_called_once()
    displayed = coordinator._info_panel.set_asset_metadata.call_args.args[0]
    assert displayed["iso"] == 320
    assert "_metadata_loading" not in displayed
    coordinator._queue_info_panel_metadata_enrichment.assert_not_called()


def test_refresh_info_panel_does_not_retry_after_session_attempt() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._info_panel = Mock()
    coordinator._info_panel_metadata_cache = {
        str(Path("/fake/video.mp4")): {"codec": "hevc"},
    }
    coordinator._info_panel_metadata_inflight = set()
    coordinator._info_panel_metadata_attempted = {str(Path("/fake/video.mp4"))}
    coordinator._queue_info_panel_metadata_enrichment = Mock()

    PlaybackCoordinator._refresh_info_panel(
        coordinator,
        {
            "abs": "/fake/video.mp4",
            "rel": "video.mp4",
            "name": "video.mp4",
            "is_video": True,
        },
    )

    displayed = coordinator._info_panel.set_asset_metadata.call_args.args[0]
    assert "_metadata_loading" not in displayed
    coordinator._queue_info_panel_metadata_enrichment.assert_not_called()


def test_ready_enrichment_updates_visible_panel_for_current_asset() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._info_panel = Mock(isVisible=Mock(return_value=True))
    coordinator._current_presentation = _make_presentation(path="/fake/video.mp4")

    PlaybackCoordinator._handle_info_panel_metadata_ready(
        coordinator,
        InfoPanelMetadataResult(
            path=Path("/fake/video.mp4"),
            metadata={"frame_rate": 59.94, "lens": "Wide Camera"},
        ),
    )

    coordinator._info_panel.set_asset_metadata.assert_called_once()
    displayed = coordinator._info_panel.set_asset_metadata.call_args.args[0]
    assert displayed["frame_rate"] == 59.94
    assert displayed["lens"] == "Wide Camera"
    assert coordinator._info_panel_metadata_cache[str(Path("/fake/video.mp4"))]["lens"] == "Wide Camera"


def test_ready_enrichment_is_cached_without_touching_other_asset_panel() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._info_panel = Mock(isVisible=Mock(return_value=True))
    coordinator._current_presentation = _make_presentation(path="/fake/other.mp4")

    PlaybackCoordinator._handle_info_panel_metadata_ready(
        coordinator,
        InfoPanelMetadataResult(
            path=Path("/fake/video.mp4"),
            metadata={"frame_rate": 59.94, "lens": "Wide Camera"},
        ),
    )

    coordinator._info_panel.set_asset_metadata.assert_not_called()
    assert coordinator._info_panel_metadata_cache[str(Path("/fake/video.mp4"))]["frame_rate"] == 59.94

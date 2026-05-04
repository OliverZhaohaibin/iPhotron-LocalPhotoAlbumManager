from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for playback coordinator tests", exc_type=ImportError)

from iPhoto.application.services.assign_location_service import AssignedLocationResult
from iPhoto.gui.coordinators import playback_coordinator as playback_coordinator_module
from iPhoto.gui.coordinators.playback_coordinator import PlaybackCoordinator
from iPhoto.gui.ui.tasks.info_panel_metadata_worker import InfoPanelMetadataResult
from iPhoto.gui.viewmodels.detail_viewmodel import DetailPresentation
from iPhoto.people.repository import AssetFaceAnnotation


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
    coordinator._router = Mock(is_detail_view_active=Mock(return_value=True))
    coordinator._asset_model = Mock(index=Mock(return_value=Mock(isValid=Mock(return_value=True))))
    coordinator._asset_model.set_current_row = Mock()
    coordinator.assetChanged = Mock(emit=Mock())
    coordinator._update_header = Mock()
    coordinator._sync_filmstrip_selection = Mock()
    coordinator._render_presentation = Mock()
    coordinator._clear_play_profile = Mock()

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
    coordinator._router = Mock(is_detail_view_active=Mock(return_value=True))
    coordinator._asset_model = Mock()
    coordinator._asset_model.set_current_row = Mock()
    coordinator.assetChanged = Mock(emit=Mock())
    coordinator._update_header = Mock()
    coordinator._sync_filmstrip_selection = Mock()
    coordinator._render_presentation = Mock()
    coordinator._update_favorite_icon = Mock()
    coordinator._clear_play_profile = Mock()
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
    coordinator._router = Mock(is_detail_view_active=Mock(return_value=True))
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


def test_handle_presentation_changed_skips_hidden_detail_updates() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._current_presentation = None
    coordinator._router = Mock(is_detail_view_active=Mock(return_value=False))
    coordinator._asset_model = Mock()
    coordinator._asset_model.set_current_row = Mock()
    coordinator.assetChanged = Mock(emit=Mock())
    coordinator._update_header = Mock()
    coordinator._sync_filmstrip_selection = Mock()
    coordinator._render_presentation = Mock()
    coordinator._clear_play_profile = Mock()

    presentation = _make_presentation()

    PlaybackCoordinator._handle_presentation_changed(coordinator, presentation)

    assert coordinator._current_presentation is None
    coordinator._asset_model.set_current_row.assert_not_called()
    coordinator.assetChanged.emit.assert_not_called()
    coordinator._update_header.assert_not_called()
    coordinator._sync_filmstrip_selection.assert_not_called()
    coordinator._render_presentation.assert_not_called()
    coordinator._clear_play_profile.assert_called_once_with(presentation.row)


def test_handle_route_requested_gallery_resets_before_showing_gallery() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    parent = Mock()
    coordinator.reset_for_gallery = Mock()
    coordinator._router = Mock(show_gallery=Mock(), show_detail=Mock())
    parent.attach_mock(coordinator.reset_for_gallery, "reset_for_gallery")
    parent.attach_mock(coordinator._router.show_gallery, "show_gallery")

    PlaybackCoordinator._handle_route_requested(coordinator, "gallery")

    assert parent.mock_calls == [
        call.reset_for_gallery(),
        call.show_gallery(),
    ]


def test_hidden_presentation_then_explicit_open_of_same_asset_still_renders() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._current_presentation = None
    coordinator._router = Mock(is_detail_view_active=Mock(return_value=False))
    coordinator._asset_model = Mock()
    coordinator._asset_model.set_current_row = Mock()
    coordinator.assetChanged = Mock(emit=Mock())
    coordinator._update_header = Mock()
    coordinator._sync_filmstrip_selection = Mock()
    coordinator._render_presentation = Mock()
    coordinator._clear_play_profile = Mock()
    coordinator._info_panel = None

    presentation = _make_presentation()
    PlaybackCoordinator._handle_presentation_changed(coordinator, presentation)

    coordinator._render_presentation.assert_not_called()
    coordinator._router.is_detail_view_active.return_value = True
    PlaybackCoordinator._handle_presentation_changed(coordinator, presentation)

    coordinator._render_presentation.assert_called_once_with(presentation)


def test_preserve_live_presentation_keeps_existing_motion_during_same_asset_refresh(
    tmp_path: Path,
) -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    motion_path = tmp_path / "motion.mov"
    motion_path.write_bytes(b"\x00")

    previous = DetailPresentation(
        **{
            **_make_presentation(path="/fake/photo.heic", is_video=False, is_live=True).__dict__,
            "live_motion_rel": Path("motion.mov"),
            "live_motion_abs": motion_path,
        }
    )
    current = _make_presentation(path="/fake/photo.heic", is_video=False, is_live=False)

    preserved = PlaybackCoordinator._preserve_live_presentation(
        coordinator,
        previous,
        current,
    )

    assert preserved.is_live is True
    assert preserved.live_motion_abs == motion_path
    assert preserved.live_motion_rel == Path("motion.mov")
    assert preserved.info["live_partner_rel"] == "motion.mov"


def test_handle_rotate_requested_routes_video_rotation_through_video_area() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._adjustment_committer = Mock(commit=Mock(return_value=True))
    coordinator._library_manager = SimpleNamespace(
        edit_service=Mock(read_adjustments=Mock(return_value={"Exposure": 0.2}))
    )
    coordinator._player_view = SimpleNamespace(
        video_area=Mock(rotate_image_ccw=Mock(return_value={"Crop_Rotate90": 3.0})),
        image_viewer=Mock(rotate_image_ccw=Mock()),
    )

    PlaybackCoordinator._handle_rotate_requested(coordinator, Path("/fake/video.mp4"), True)

    coordinator._player_view.video_area.rotate_image_ccw.assert_called_once_with()
    coordinator._library_manager.edit_service.read_adjustments.assert_called_once_with(
        Path("/fake/video.mp4")
    )
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


def test_render_presentation_stops_video_area_before_showing_still() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    video_area = Mock(has_video=Mock(return_value=True), stop=Mock())
    image_viewer = Mock(reset_zoom=Mock())
    player_view = Mock(
        show_image_surface=Mock(),
        display_image=Mock(),
        hide_live_badge=Mock(),
        set_live_replay_enabled=Mock(),
        video_area=video_area,
        image_viewer=image_viewer,
    )
    parent = Mock()
    parent.attach_mock(video_area.stop, "stop")
    parent.attach_mock(player_view.show_image_surface, "show_image_surface")

    coordinator._player_view = player_view
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
    coordinator._refresh_face_name_overlay_for_presentation = Mock()

    presentation = _make_presentation(path="/fake/photo.heic", is_video=False)

    PlaybackCoordinator._render_presentation(coordinator, presentation)

    assert parent.mock_calls[:2] == [call.stop(), call.show_image_surface()]
    player_view.display_image.assert_called_once_with(Path("/fake/photo.heic"))
    coordinator._player_bar.setEnabled.assert_called_once_with(False)


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


def test_set_people_library_root_prefers_bound_library_manager_service() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._people_service = playback_coordinator_module.PeopleService()
    library_root = Path("/fake/library")
    recreated_service = playback_coordinator_module.PeopleService(
        library_root,
        asset_repository=Mock(),
    )
    coordinator._library_manager = SimpleNamespace(people_service=recreated_service)
    coordinator._refresh_face_name_overlay_for_current_presentation = Mock()

    PlaybackCoordinator.set_people_library_root(coordinator, library_root)

    assert coordinator._people_service is recreated_service
    assert coordinator._people_service.asset_repository is not None
    coordinator._refresh_face_name_overlay_for_current_presentation.assert_called_once_with()


def test_refresh_location_extension_state_uses_bound_map_runtime_capabilities() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    location_search_service = object()
    coordinator._map_runtime = SimpleNamespace(
        capabilities=lambda: SimpleNamespace(location_search_available=True),
        package_root=lambda: Path("/fake/maps"),
    )
    coordinator._location_search_cache = {}
    coordinator._location_search_timer = Mock(stop=Mock())
    coordinator._pending_location_query = ""
    coordinator._location_search_target_path = None
    coordinator._location_search_service = location_search_service

    enabled = PlaybackCoordinator._refresh_location_extension_state(coordinator)

    assert enabled is True
    assert coordinator._location_search_service is location_search_service


def test_refresh_location_extension_state_initializes_search_service_with_runtime_package_root(
    monkeypatch,
) -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._map_runtime = SimpleNamespace(
        capabilities=lambda: SimpleNamespace(location_search_available=True),
        package_root=lambda: Path("/fake/maps"),
    )
    coordinator._location_search_cache = {}
    coordinator._location_search_timer = Mock(stop=Mock())
    coordinator._pending_location_query = ""
    coordinator._location_search_target_path = None
    coordinator._location_search_service = None

    created_kwargs: dict[str, object] = {}

    class _FakeSearchService:
        def __init__(self, *args, **kwargs) -> None:
            del args
            created_kwargs.update(kwargs)

    monkeypatch.setattr(playback_coordinator_module, "OsmAndSearchService", _FakeSearchService)

    enabled = PlaybackCoordinator._refresh_location_extension_state(coordinator)

    assert enabled is True
    assert created_kwargs["package_root"] == Path("/fake/maps")


def test_refresh_location_extension_state_falls_back_to_session_runtime_when_unbound(
    monkeypatch,
) -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._map_runtime = None
    coordinator._library_manager = SimpleNamespace(map_runtime=None)
    coordinator._location_search_cache = {}
    coordinator._location_search_timer = Mock(stop=Mock())
    coordinator._pending_location_query = ""
    coordinator._location_search_target_path = None
    coordinator._location_search_service = None

    fallback_runtime = SimpleNamespace(
        capabilities=lambda: SimpleNamespace(location_search_available=True),
        package_root=lambda: Path("/fallback/maps"),
    )
    monkeypatch.setattr(
        playback_coordinator_module,
        "SessionMapRuntimeService",
        lambda: fallback_runtime,
    )

    created_kwargs: dict[str, object] = {}

    class _FakeSearchService:
        def __init__(self, *args, **kwargs) -> None:
            del args
            created_kwargs.update(kwargs)

    monkeypatch.setattr(playback_coordinator_module, "OsmAndSearchService", _FakeSearchService)

    enabled = PlaybackCoordinator._refresh_location_extension_state(coordinator)

    assert enabled is True
    assert coordinator._map_runtime is fallback_runtime
    assert created_kwargs["package_root"] == Path("/fallback/maps")


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


def test_set_info_panel_connects_face_action_signals() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    panel = SimpleNamespace(
        dismissed=Mock(connect=Mock()),
        manualFaceAddRequested=Mock(connect=Mock()),
        faceDeleteRequested=Mock(connect=Mock()),
        faceMoveRequested=Mock(connect=Mock()),
        faceMoveToNewPersonRequested=Mock(connect=Mock()),
        locationQueryChanged=Mock(connect=Mock()),
        locationConfirmRequested=Mock(connect=Mock()),
    )

    PlaybackCoordinator.set_info_panel(coordinator, panel)

    panel.faceDeleteRequested.connect.assert_called_once_with(
        coordinator._handle_info_panel_face_delete_requested
    )
    panel.faceMoveRequested.connect.assert_called_once_with(
        coordinator._handle_info_panel_face_move_requested
    )
    panel.faceMoveToNewPersonRequested.connect.assert_called_once_with(
        coordinator._handle_info_panel_face_move_to_new_person_requested
    )


def test_handle_info_panel_face_delete_requested_refreshes_views() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._people_service = Mock(delete_face=Mock(return_value=True))
    coordinator._current_presentation = _make_presentation(
        path="/fake/photo.jpg",
        asset_id="asset-photo",
        is_video=False,
    )
    coordinator._refresh_face_name_overlay_for_current_presentation = Mock()
    coordinator._refresh_info_panel_faces = Mock()
    coordinator._people_dashboard_refresh_callback = Mock()
    annotation = AssetFaceAnnotation(
        face_id="face-1",
        person_id="person-a",
        display_name="Alice",
        box_x=0,
        box_y=0,
        box_w=10,
        box_h=10,
        image_width=100,
        image_height=100,
    )

    PlaybackCoordinator._handle_info_panel_face_delete_requested(coordinator, annotation)

    coordinator._people_service.delete_face.assert_called_once_with("face-1")
    coordinator._refresh_face_name_overlay_for_current_presentation.assert_called_once_with()
    coordinator._refresh_info_panel_faces.assert_called_once_with("asset-photo")
    coordinator._people_dashboard_refresh_callback.assert_called_once_with()


def test_handle_info_panel_face_move_requested_refreshes_views() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._people_service = Mock(move_face_to_person=Mock(return_value=True))
    coordinator._current_presentation = _make_presentation(
        path="/fake/photo.jpg",
        asset_id="asset-photo",
        is_video=False,
    )
    coordinator._refresh_face_name_overlay_for_current_presentation = Mock()
    coordinator._refresh_info_panel_faces = Mock()
    coordinator._people_dashboard_refresh_callback = Mock()
    annotation = AssetFaceAnnotation(
        face_id="face-1",
        person_id="person-a",
        display_name="Alice",
        box_x=0,
        box_y=0,
        box_w=10,
        box_h=10,
        image_width=100,
        image_height=100,
    )

    PlaybackCoordinator._handle_info_panel_face_move_requested(
        coordinator,
        annotation,
        "person-b",
    )

    coordinator._people_service.move_face_to_person.assert_called_once_with("face-1", "person-b")
    coordinator._refresh_face_name_overlay_for_current_presentation.assert_called_once_with()
    coordinator._refresh_info_panel_faces.assert_called_once_with("asset-photo")
    coordinator._people_dashboard_refresh_callback.assert_called_once_with()


def test_handle_info_panel_face_move_to_new_person_requested_refreshes_views() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._people_service = Mock(move_face_to_new_person=Mock(return_value="person-new"))
    coordinator._current_presentation = _make_presentation(
        path="/fake/photo.jpg",
        asset_id="asset-photo",
        is_video=False,
    )
    coordinator._refresh_face_name_overlay_for_current_presentation = Mock()
    coordinator._refresh_info_panel_faces = Mock()
    coordinator._people_dashboard_refresh_callback = Mock()
    annotation = AssetFaceAnnotation(
        face_id="face-1",
        person_id="person-a",
        display_name="Alice",
        box_x=0,
        box_y=0,
        box_w=10,
        box_h=10,
        image_width=100,
        image_height=100,
    )

    PlaybackCoordinator._handle_info_panel_face_move_to_new_person_requested(
        coordinator,
        annotation,
        "Alice 2",
    )

    coordinator._people_service.move_face_to_new_person.assert_called_once_with("face-1", "Alice 2")
    coordinator._refresh_face_name_overlay_for_current_presentation.assert_called_once_with()
    coordinator._refresh_info_panel_faces.assert_called_once_with("asset-photo")
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


def test_refresh_info_panel_keeps_download_prompt_when_only_legacy_map_runtime_is_available() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._info_panel = Mock()
    coordinator._map_runtime = SimpleNamespace(
        capabilities=lambda: SimpleNamespace(
            display_available=True,
            location_search_available=False,
            osmand_extension_available=False,
        ),
        package_root=lambda: Path("/fake/maps"),
    )
    coordinator._location_search_cache = {}
    coordinator._location_search_timer = Mock(stop=Mock())
    coordinator._pending_location_query = ""
    coordinator._location_search_target_path = None
    coordinator._location_search_service = None
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

    coordinator._info_panel.set_location_capability.assert_called_once_with(
        enabled=False,
        preview_enabled=False,
        fallback_text=playback_coordinator_module._LOCATION_EXTENSION_PROMPT,
    )


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


def test_location_assignment_ready_with_file_write_error_still_updates_library_state(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    asset_path = Path("/fake/photo.jpg")
    metadata = {
        "gps": {"lat": 48.8566, "lon": 2.3522},
        "location": "Paris",
        "location_name": "Paris",
    }
    store = Mock()
    coordinator._asset_model = Mock(row_for_path=Mock(return_value=4), store=store)
    coordinator._info_panel_metadata_cache = {}
    coordinator._info_panel_metadata_attempted = set()
    coordinator._info_panel_metadata_inflight = {str(asset_path)}
    coordinator._location_preview_path = asset_path
    coordinator._location_preview_metadata = dict(metadata)
    coordinator._detail_vm = Mock(refresh_current=Mock())
    coordinator._library_manager = None
    coordinator._location_session_invalidator = None
    popup_parent = Mock()
    coordinator._info_panel = Mock(parentWidget=Mock(return_value=popup_parent))
    show_warning = Mock()
    monkeypatch.setattr(playback_coordinator_module.dialogs, "show_warning", show_warning)
    coordinator._queue_location_exiftool_missing_warning = Mock(
        side_effect=lambda: PlaybackCoordinator._show_location_exiftool_missing_warning(
            coordinator
        )
    )

    result = AssignedLocationResult(
        asset_path=asset_path,
        asset_rel="photo.jpg",
        display_name="Paris",
        gps={"lat": 48.8566, "lon": 2.3522},
        metadata=metadata,
        file_write_error="exiftool executable not found",
    )

    with caplog.at_level(logging.WARNING, logger="iPhoto.gui.coordinators.playback_coordinator"):
        PlaybackCoordinator._handle_location_assignment_ready(coordinator, result)

    store.update_asset_metadata.assert_called_once_with(4, metadata)
    assert coordinator._info_panel_metadata_cache[str(asset_path)] == metadata
    assert coordinator._info_panel_metadata_attempted == {str(asset_path)}
    assert coordinator._info_panel_metadata_inflight == set()
    assert coordinator._location_preview_path is None
    assert coordinator._location_preview_metadata is None
    coordinator._detail_vm.refresh_current.assert_called_once_with()
    assert "GPS metadata was not written" in caplog.text
    coordinator._queue_location_exiftool_missing_warning.assert_called_once_with()
    show_warning.assert_called_once_with(
        popup_parent,
        playback_coordinator_module._LOCATION_EXIFTOOL_LIMITED_MESSAGE,
        title=playback_coordinator_module._LOCATION_EXIFTOOL_LIMITED_TITLE,
    )


def test_location_assignment_ready_with_non_missing_file_write_error_warns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    asset_path = Path("/fake/photo.jpg")
    metadata = {
        "gps": {"lat": 48.8566, "lon": 2.3522},
        "location": "Paris",
        "location_name": "Paris",
    }
    store = Mock()
    coordinator._asset_model = Mock(row_for_path=Mock(return_value=4), store=store)
    coordinator._info_panel_metadata_cache = {}
    coordinator._info_panel_metadata_attempted = set()
    coordinator._info_panel_metadata_inflight = {str(asset_path)}
    coordinator._location_preview_path = asset_path
    coordinator._location_preview_metadata = dict(metadata)
    coordinator._detail_vm = Mock(refresh_current=Mock())
    coordinator._library_manager = None
    coordinator._location_session_invalidator = None
    popup_parent = Mock()
    coordinator._info_panel = Mock(parentWidget=Mock(return_value=popup_parent))
    show_warning = Mock()
    monkeypatch.setattr(playback_coordinator_module.dialogs, "show_warning", show_warning)
    coordinator._queue_location_exiftool_missing_warning = Mock()
    coordinator._queue_location_file_write_warning = Mock(
        side_effect=lambda message: PlaybackCoordinator._show_location_file_write_warning(
            coordinator,
            message,
        )
    )

    result = AssignedLocationResult(
        asset_path=asset_path,
        asset_rel="photo.jpg",
        display_name="Paris",
        gps={"lat": 48.8566, "lon": 2.3522},
        metadata=metadata,
        file_write_error="ExifTool failed with an error: permission denied",
    )

    PlaybackCoordinator._handle_location_assignment_ready(coordinator, result)

    store.update_asset_metadata.assert_called_once_with(4, metadata)
    coordinator._queue_location_exiftool_missing_warning.assert_not_called()
    coordinator._queue_location_file_write_warning.assert_called_once_with(
        "ExifTool failed with an error: permission denied"
    )
    show_warning.assert_called_once_with(
        popup_parent,
        playback_coordinator_module._LOCATION_FILE_WRITE_LIMITED_MESSAGE_TEMPLATE.format(
            reason="ExifTool failed with an error: permission denied"
        ),
        title=playback_coordinator_module._LOCATION_FILE_WRITE_LIMITED_TITLE,
    )


def test_handle_manual_face_submitted_queues_background_worker() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._manual_face_add_inflight = False
    coordinator._pending_manual_face_annotations = {}
    coordinator._pending_manual_face_sequence = 0
    coordinator._current_presentation = _make_presentation(
        path="/fake/photo.jpg",
        asset_id="asset-photo",
        is_video=False,
    )
    coordinator._face_name_overlay = Mock()
    coordinator._people_service = Mock(library_root=Mock(return_value=Path("/fake/library")))

    fake_worker = SimpleNamespace(
        signals=SimpleNamespace(
            ready=Mock(connect=Mock()),
            error=Mock(connect=Mock()),
            finished=Mock(connect=Mock()),
        )
    )
    fake_pool = Mock(start=Mock())

    with patch(
        "iPhoto.gui.coordinators.playback_coordinator.ManualFaceAddWorker",
        return_value=fake_worker,
    ) as worker_cls, patch(
        "iPhoto.gui.coordinators.playback_coordinator.QThreadPool.globalInstance",
        return_value=fake_pool,
    ):
        PlaybackCoordinator._handle_manual_face_submitted(
            coordinator,
            {
                "requested_box": (10, 20, 30, 40),
                "name": "Alice",
                "person_id": "person-a",
            },
        )

    coordinator._face_name_overlay.set_manual_face_busy.assert_called_once_with(True)
    assert coordinator._manual_face_add_inflight is True
    worker_cls.assert_called_once_with(
        library_root=Path("/fake/library"),
        asset_id="asset-photo",
        requested_box=(10, 20, 30, 40),
        name_or_none="Alice",
        person_id="person-a",
        people_service=coordinator._people_service,
    )
    fake_pool.start.assert_called_once_with(fake_worker, -1)


def test_handle_manual_face_submitted_immediately_refreshes_info_panel_with_pending_face() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._manual_face_add_inflight = False
    coordinator._pending_manual_face_annotations = {}
    coordinator._pending_manual_face_sequence = 0
    coordinator._current_presentation = _make_presentation(
        path="/fake/photo.jpg",
        asset_id="asset-photo",
        is_video=False,
    )
    coordinator._face_name_overlay = Mock()
    coordinator._people_service = Mock(library_root=Mock(return_value=Path("/fake/library")))
    coordinator._info_panel = Mock()
    existing_face = AssetFaceAnnotation(
        face_id="existing-face",
        person_id="person-existing",
        display_name="Existing",
        box_x=1,
        box_y=2,
        box_w=3,
        box_h=4,
        image_width=100,
        image_height=80,
    )
    coordinator._load_face_name_annotations = Mock(return_value=[existing_face])

    fake_worker = SimpleNamespace(
        signals=SimpleNamespace(
            ready=Mock(connect=Mock()),
            error=Mock(connect=Mock()),
            finished=Mock(connect=Mock()),
        )
    )
    fake_pool = Mock(start=Mock())

    with patch(
        "iPhoto.gui.coordinators.playback_coordinator.ManualFaceAddWorker",
        return_value=fake_worker,
    ), patch(
        "iPhoto.gui.coordinators.playback_coordinator.QThreadPool.globalInstance",
        return_value=fake_pool,
    ):
        PlaybackCoordinator._handle_manual_face_submitted(
            coordinator,
            {
                "requested_box": (10, 20, 30, 40),
                "name": "Alice",
                "person_id": "person-a",
            },
        )

    displayed_faces = coordinator._info_panel.set_asset_faces.call_args.args[0]
    assert len(displayed_faces) == 2
    assert displayed_faces[0] == existing_face
    assert displayed_faces[1].face_id == "pending-manual-1"
    assert displayed_faces[1].display_name == "Alice"
    assert displayed_faces[1].person_id == "person-a"
    assert displayed_faces[1].is_manual is True


def test_handle_manual_face_error_removes_pending_info_panel_face() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._current_presentation = _make_presentation(
        path="/fake/photo.jpg",
        asset_id="asset-photo",
        is_video=False,
    )
    coordinator._face_name_overlay = Mock()
    coordinator._info_panel = Mock()
    coordinator._pending_manual_face_annotations = {
        "asset-photo": [
            AssetFaceAnnotation(
                face_id="pending-manual-1",
                person_id="person-a",
                display_name="Alice",
                box_x=10,
                box_y=20,
                box_w=30,
                box_h=40,
                image_width=100,
                image_height=80,
                is_manual=True,
            )
        ]
    }
    coordinator._load_face_name_annotations = Mock(return_value=[])

    PlaybackCoordinator._handle_manual_face_error(coordinator, "No face detected")

    coordinator._info_panel.set_asset_faces.assert_called_once_with([])
    assert coordinator._pending_manual_face_annotations == {}
    coordinator._face_name_overlay.set_manual_face_busy.assert_called_once_with(False)
    coordinator._face_name_overlay.show_manual_error.assert_called_once_with("No face detected")

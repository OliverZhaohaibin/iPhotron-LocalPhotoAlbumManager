from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for playback coordinator tests", exc_type=ImportError)

from iPhoto.application.ports import LocationWriteJobRecord
from iPhoto.gui.coordinators import playback_coordinator as playback_coordinator_module
from iPhoto.gui.coordinators.playback_coordinator import PlaybackCoordinator
from iPhoto.gui.detail_pipeline import (
    AssetSourceIdentity,
    DetailPrefetchDescriptor,
    DetailRenderTransaction,
    PlaybackAsyncToken,
    VideoPresentationState,
)
from iPhoto.gui.detail_render_coordinator import (
    DetailRenderCoordinator,
    DetailRenderState,
    DetailSurfacePresentationResult,
)
from iPhoto.gui.services.location_file_write_queue import LocationFileWriteResult
from iPhoto.gui.ui.media.media_selection_session import MediaSelectionState
from iPhoto.gui.ui.tasks.info_panel_metadata_worker import InfoPanelMetadataResult
from iPhoto.gui.ui.widgets.recognition_annotations import RecognitionAnnotation
from iPhoto.gui.viewmodels.detail_viewmodel import DetailPresentation
from iPhoto.people.repository import AssetFaceAnnotation
from iPhoto.people.service import ManualFaceAddResult, PeopleService
from maps.osmand_search import SearchSuggestion


def _make_presentation(
    *,
    path: str = "/fake/video.mp4",
    asset_id: str = "asset-1",
    is_video: bool = True,
    is_live: bool = False,
    is_favorite: bool = False,
    info_panel_visible: bool = False,
    reload_token: int = 0,
    request_generation: int = 1,
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
        info_panel_visible=info_panel_visible,
        live_motion_rel=None,
        live_motion_abs=None,
        video_adjustments={"Exposure": 0.2} if is_video else None,
        video_trim_range_ms=(1000, 3000) if is_video else None,
        video_adjusted_preview=is_video,
        reload_token=reload_token,
        request_generation=request_generation,
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


def test_relative_navigation_accumulates_from_pending_target() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._asset_model = Mock(rowCount=Mock(return_value=8))
    coordinator._pending_play_row = 4
    coordinator.current_row = Mock(return_value=2)
    coordinator.play_asset = Mock()

    PlaybackCoordinator.select_next(coordinator)

    coordinator.play_asset.assert_called_once_with(5)
    coordinator.current_row.assert_not_called()


def test_rapid_relative_navigation_coalesces_to_the_accumulated_target() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._asset_model = Mock(rowCount=Mock(return_value=8))
    coordinator._pending_play_row = None
    coordinator.current_row = Mock(return_value=2)
    coordinator._play_debounce = Mock(isActive=Mock(return_value=True), start=Mock())
    coordinator._dispatch_play_row = Mock()
    coordinator._play_profile_started_at = None
    coordinator._play_profile_row = None

    PlaybackCoordinator.select_next(coordinator)
    PlaybackCoordinator.select_next(coordinator)
    PlaybackCoordinator.select_previous(coordinator)

    assert coordinator._pending_play_row == 3
    coordinator._dispatch_play_row.assert_not_called()


def test_relative_navigation_starts_at_first_row_without_a_current_selection() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._asset_model = Mock(rowCount=Mock(return_value=5))
    coordinator._pending_play_row = None
    coordinator._requested_play_row = None
    coordinator.current_row = Mock(return_value=-1)
    coordinator.play_asset = Mock()

    PlaybackCoordinator.select_next(coordinator)

    coordinator.play_asset.assert_called_once_with(0)


def test_rapid_navigation_accumulates_while_immediate_target_is_loading() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._asset_model = Mock(rowCount=Mock(return_value=8))
    coordinator._pending_play_row = None
    coordinator._requested_play_row = None
    coordinator.current_row = Mock(return_value=2)
    coordinator._play_debounce = Mock(
        isActive=Mock(side_effect=[False, True, True]),
        start=Mock(),
    )
    coordinator._dispatch_play_row = Mock()
    coordinator._play_profile_started_at = None
    coordinator._play_profile_row = None

    PlaybackCoordinator.select_next(coordinator)
    PlaybackCoordinator.select_next(coordinator)

    coordinator._dispatch_play_row.assert_called_once_with(3, reason="immediate")
    assert coordinator._pending_play_row == 4
    assert coordinator._requested_play_row == 4


@pytest.mark.parametrize(
    ("current_row", "delta"),
    [
        (-1, -1),
        (0, -1),
        (4, 1),
    ],
)
def test_relative_navigation_stops_at_model_boundaries(current_row: int, delta: int) -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._asset_model = Mock(rowCount=Mock(return_value=5))
    coordinator._pending_play_row = None
    coordinator.current_row = Mock(return_value=current_row)
    coordinator.play_asset = Mock()

    PlaybackCoordinator._request_relative_asset(coordinator, delta)

    coordinator.play_asset.assert_not_called()


def test_relative_navigation_preserves_single_step_behavior() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._asset_model = Mock(rowCount=Mock(return_value=5))
    coordinator._pending_play_row = None
    coordinator.current_row = Mock(return_value=3)
    coordinator.play_asset = Mock()

    PlaybackCoordinator.select_previous(coordinator)

    coordinator.play_asset.assert_called_once_with(2)


def test_fullscreen_keeps_active_still_transaction() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    presentation = _make_presentation(path="/fake/photo.jpg", is_video=False)
    transaction = PlaybackCoordinator._transaction_for_presentation(presentation)
    lifecycle = DetailRenderCoordinator()
    lifecycle.begin(transaction)
    lifecycle.mark_preparing(transaction.generation)
    coordinator._asset_model = Mock(rowCount=Mock(return_value=1))
    coordinator.current_row = Mock(return_value=0)
    coordinator._router = Mock(is_detail_view_active=Mock(return_value=True))
    coordinator._current_presentation = presentation
    coordinator._active_live_motion = None
    coordinator._player_view = Mock(has_current_render_session=Mock(return_value=False))
    coordinator._detail_render_coordinator = lifecycle
    coordinator._detail_vm = Mock(show_current=Mock())

    assert PlaybackCoordinator.prepare_fullscreen_asset(coordinator) is True

    coordinator._detail_vm.show_current.assert_not_called()


def test_fullscreen_restarts_still_after_terminal_without_render_session() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    presentation = _make_presentation(path="/fake/photo.jpg", is_video=False)
    transaction = PlaybackCoordinator._transaction_for_presentation(presentation)
    lifecycle = DetailRenderCoordinator()
    lifecycle.begin(transaction)
    lifecycle.mark_failed(transaction.generation, "abandoned")
    coordinator._asset_model = Mock(rowCount=Mock(return_value=1))
    coordinator.current_row = Mock(return_value=0)
    coordinator._router = Mock(is_detail_view_active=Mock(return_value=True))
    coordinator._current_presentation = presentation
    coordinator._active_live_motion = None
    coordinator._player_view = Mock(has_current_render_session=Mock(return_value=False))
    coordinator._detail_render_coordinator = lifecycle
    coordinator._detail_vm = Mock(show_current=Mock())

    assert PlaybackCoordinator.prepare_fullscreen_asset(coordinator) is True

    coordinator._detail_vm.show_current.assert_called_once_with()


def test_fullscreen_keeps_stable_pending_anchor_instead_of_opening_row_zero() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    presentation = replace(
        _make_presentation(path="/fake/photo.jpg", is_video=False),
        row=-1,
    )
    coordinator._asset_model = Mock(rowCount=Mock(return_value=5))
    coordinator.current_row = Mock(return_value=-1)
    coordinator._router = Mock(is_detail_view_active=Mock(return_value=True))
    coordinator._current_presentation = presentation
    coordinator._active_live_motion = None
    coordinator._player_view = Mock(has_current_render_session=Mock(return_value=True))
    coordinator.play_asset = Mock()

    assert PlaybackCoordinator.prepare_fullscreen_asset(coordinator) is True

    coordinator.play_asset.assert_not_called()


def test_fullscreen_restarts_terminal_pending_still_from_stable_identity() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    presentation = replace(
        _make_presentation(path="/fake/photo.jpg", is_video=False),
        row=-1,
    )
    transaction = PlaybackCoordinator._transaction_for_presentation(presentation)
    lifecycle = DetailRenderCoordinator()
    lifecycle.begin(transaction)
    lifecycle.mark_failed(transaction.generation, "abandoned")
    coordinator._asset_model = Mock(rowCount=Mock(return_value=5))
    coordinator.current_row = Mock(return_value=-1)
    coordinator._router = Mock(is_detail_view_active=Mock(return_value=True))
    coordinator._current_presentation = presentation
    coordinator._active_live_motion = None
    coordinator._player_view = Mock(has_current_render_session=Mock(return_value=False))
    coordinator._detail_render_coordinator = lifecycle
    coordinator._detail_vm = Mock()
    coordinator._detail_vm.selection_state.value = MediaSelectionState.ANCHOR_RESOLVING
    coordinator._detail_vm.recover_current_presentation.return_value = True
    coordinator.play_asset = Mock()

    assert PlaybackCoordinator.prepare_fullscreen_asset(coordinator) is True

    coordinator._detail_vm.recover_current_presentation.assert_called_once_with()
    coordinator._detail_vm.show_current.assert_not_called()
    coordinator.play_asset.assert_not_called()


@pytest.mark.parametrize(
    ("delta", "method_name"),
    [(1, "next"), (-1, "previous")],
)
def test_relative_navigation_defers_while_selection_anchor_is_resolving(
    delta: int,
    method_name: str,
) -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._asset_model = Mock(rowCount=Mock(return_value=5))
    coordinator._detail_vm = Mock()
    coordinator._detail_vm.selection_state.value = MediaSelectionState.ANCHOR_RESOLVING
    coordinator.play_asset = Mock()

    PlaybackCoordinator._request_relative_asset(coordinator, delta)

    getattr(coordinator._detail_vm, method_name).assert_called_once_with()
    coordinator.play_asset.assert_not_called()


def test_handle_presentation_changed_renders_video_and_updates_header() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._current_presentation = None
    coordinator._router = Mock(is_detail_view_active=Mock(return_value=True))
    coordinator._asset_model = Mock(index=Mock(return_value=Mock(isValid=Mock(return_value=True))))
    coordinator._asset_model.set_current_row = Mock()
    coordinator.assetChanged = Mock(emit=Mock())
    coordinator._update_header = Mock()
    coordinator._select_filmstrip_row = Mock()
    coordinator._center_filmstrip_if_current = Mock()
    coordinator._player_view = Mock(show_placeholder=Mock())
    coordinator._render_presentation = Mock()
    coordinator._clear_play_profile = Mock()

    presentation = _make_presentation()
    with patch.object(
        playback_coordinator_module.QTimer,
        "singleShot",
        side_effect=lambda _delay, callback: callback(),
    ):
        PlaybackCoordinator._handle_presentation_changed(coordinator, presentation)

    coordinator._asset_model.set_current_asset.assert_called_once_with(
        0,
        presentation.path,
    )
    coordinator.assetChanged.emit.assert_called_once_with(0)
    coordinator._update_header.assert_called_once_with(presentation)
    coordinator._select_filmstrip_row.assert_called_once_with(0)
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
    coordinator._select_filmstrip_row = Mock()
    coordinator._center_filmstrip_if_current = Mock()
    coordinator._player_view = Mock(show_placeholder=Mock())
    coordinator._render_presentation = Mock()
    coordinator._update_favorite_icon = Mock()
    coordinator._clear_play_profile = Mock()
    coordinator._info_panel = None

    PlaybackCoordinator._handle_presentation_changed(coordinator, updated)

    coordinator._render_presentation.assert_not_called()
    coordinator._update_favorite_icon.assert_called_once_with(True)


def test_handle_presentation_changed_hides_info_panel_without_closing_it() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    visible = _make_presentation(info_panel_visible=True)
    hidden = replace(visible, info_panel_visible=False)
    coordinator._current_presentation = visible
    coordinator._router = Mock(is_detail_view_active=Mock(return_value=True))
    coordinator._asset_model = Mock()
    coordinator.assetChanged = Mock(emit=Mock())
    coordinator._update_header = Mock()
    coordinator._select_filmstrip_row = Mock()
    coordinator._player_view = Mock(show_placeholder=Mock())
    coordinator._render_presentation = Mock()
    coordinator._update_favorite_icon = Mock()
    coordinator._clear_play_profile = Mock()
    coordinator._info_panel = Mock(isVisible=Mock(return_value=True))

    PlaybackCoordinator._handle_presentation_changed(coordinator, hidden)

    coordinator._info_panel.hide.assert_called_once_with()
    coordinator._info_panel.close.assert_not_called()
    coordinator._render_presentation.assert_not_called()


def test_same_render_pending_keeps_visual_row_and_reconciles_all_capabilities() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    resolved = _make_presentation(path="/fake/photo.jpg", is_video=False)
    pending = replace(
        resolved,
        row=-1,
        can_edit=False,
        can_rotate=False,
        can_share=False,
        can_toggle_favorite=False,
    )
    coordinator._current_presentation = resolved
    coordinator._presented_still_source = resolved.path
    coordinator._presented_still_generation = resolved.request_generation
    coordinator._router = Mock(is_detail_view_active=Mock(return_value=True))
    coordinator._asset_model = Mock(
        set_current_asset=Mock(side_effect=[4, 0]),
    )
    coordinator.assetChanged = Mock(emit=Mock())
    coordinator._update_header = Mock()
    coordinator._select_filmstrip_row = Mock()
    coordinator._player_view = Mock(show_placeholder=Mock())
    coordinator._render_presentation = Mock()
    coordinator._update_favorite_icon = Mock()
    coordinator._clear_play_profile = Mock()
    coordinator._info_panel = None
    coordinator._favorite_button = Mock(setEnabled=Mock())
    coordinator._edit_button = Mock(setEnabled=Mock())
    coordinator._rotate_button = Mock(setEnabled=Mock())
    coordinator._share_button = Mock(setEnabled=Mock())

    PlaybackCoordinator._handle_presentation_changed(coordinator, pending)

    coordinator._asset_model.set_current_asset.assert_called_once_with(
        None,
        resolved.path,
    )
    coordinator.assetChanged.emit.assert_not_called()
    coordinator._select_filmstrip_row.assert_called_once_with(4)
    coordinator._favorite_button.setEnabled.assert_called_once_with(False)
    coordinator._edit_button.setEnabled.assert_called_once_with(False)
    coordinator._rotate_button.setEnabled.assert_called_once_with(False)
    coordinator._share_button.setEnabled.assert_called_once_with(False)
    coordinator._render_presentation.assert_not_called()
    coordinator._player_view.show_placeholder.assert_not_called()

    PlaybackCoordinator._handle_presentation_changed(coordinator, resolved)

    coordinator.assetChanged.emit.assert_called_once_with(0)
    assert coordinator._select_filmstrip_row.call_args_list == [call(4), call(0)]
    coordinator._favorite_button.setEnabled.assert_called_with(True)
    coordinator._edit_button.setEnabled.assert_called_with(True)
    coordinator._rotate_button.setEnabled.assert_called_with(True)
    coordinator._share_button.setEnabled.assert_called_with(True)


def test_filmstrip_selection_cancels_old_restore_before_deferred_center() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    source_index = Mock()
    visual_index = Mock(isValid=Mock(return_value=True))
    proxy_model = Mock(mapFromSource=Mock(return_value=visual_index))
    coordinator._asset_model = Mock(index=Mock(return_value=source_index))
    coordinator._filmstrip_view = Mock(
        model=Mock(return_value=proxy_model),
        select_index_for_centering=Mock(return_value=True),
    )

    result = PlaybackCoordinator._select_filmstrip_row(coordinator, 24)

    assert result is visual_index
    proxy_model.mapFromSource.assert_called_once_with(source_index)
    coordinator._filmstrip_view.select_index_for_centering.assert_called_once_with(
        visual_index
    )


def test_scan_row_relocation_reuses_preparing_still_without_covering_it() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    previous = replace(
        _make_presentation(path="/fake/photo.jpg", is_video=False),
        row=0,
        source_identity=AssetSourceIdentity.create(
            Path("/fake/photo.jpg"),
            source_mtime_ns=10,
        ),
    )
    relocated = replace(previous, row=7, is_favorite=True)
    transaction = PlaybackCoordinator._transaction_for_presentation(previous)
    lifecycle = DetailRenderCoordinator()
    lifecycle.begin(transaction)
    lifecycle.mark_preparing(transaction.generation)
    active_token = object()
    coordinator._current_presentation = previous
    coordinator._detail_render_coordinator = lifecycle
    coordinator._detail_render_transaction = transaction
    coordinator._active_async_token = active_token
    coordinator._router = Mock(is_detail_view_active=Mock(return_value=True))
    coordinator._asset_model = Mock(set_current_row=Mock())
    coordinator.assetChanged = Mock(emit=Mock())
    coordinator._update_header = Mock()
    coordinator._select_filmstrip_row = Mock()
    coordinator._player_view = Mock(show_placeholder=Mock())
    coordinator._render_presentation = Mock()
    coordinator._update_favorite_icon = Mock()
    coordinator._clear_play_profile = Mock()
    coordinator._info_panel = None

    PlaybackCoordinator._handle_presentation_changed(coordinator, relocated)

    assert coordinator._current_presentation == relocated
    assert coordinator._active_async_token is active_token
    assert lifecycle.snapshot is not None
    assert lifecycle.snapshot.state is DetailRenderState.PREPARING
    coordinator._select_filmstrip_row.assert_called_once_with(7)
    coordinator._player_view.show_placeholder.assert_not_called()
    coordinator._render_presentation.assert_not_called()


def test_duplicate_lifecycle_transaction_never_covers_existing_surface() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    presentation = _make_presentation(path="/fake/photo.jpg", is_video=False)
    transaction = PlaybackCoordinator._transaction_for_presentation(presentation)
    lifecycle = DetailRenderCoordinator()
    lifecycle.begin(transaction)
    lifecycle.mark_preparing(transaction.generation)
    active_token = object()
    coordinator._current_presentation = None
    coordinator._detail_render_coordinator = lifecycle
    coordinator._detail_render_transaction = transaction
    coordinator._active_async_token = active_token
    coordinator._router = Mock(is_detail_view_active=Mock(return_value=True))
    coordinator._asset_model = Mock(set_current_row=Mock())
    coordinator.assetChanged = Mock(emit=Mock())
    coordinator._update_header = Mock()
    coordinator._select_filmstrip_row = Mock()
    coordinator._player_view = Mock(show_placeholder=Mock())
    coordinator._render_presentation = Mock()
    coordinator._clear_play_profile = Mock()

    PlaybackCoordinator._handle_presentation_changed(coordinator, presentation)

    assert coordinator._active_async_token is active_token
    coordinator._player_view.show_placeholder.assert_not_called()
    coordinator._render_presentation.assert_not_called()


def test_handle_presentation_changed_rerenders_same_asset_for_new_generation() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    previous = _make_presentation(request_generation=1)
    presentation = _make_presentation(request_generation=2)
    coordinator._current_presentation = previous
    coordinator._router = Mock(is_detail_view_active=Mock(return_value=True))
    coordinator._asset_model = Mock(set_current_row=Mock())
    coordinator.assetChanged = Mock(emit=Mock())
    coordinator._update_header = Mock()
    coordinator._select_filmstrip_row = Mock()
    coordinator._center_filmstrip_if_current = Mock()
    coordinator._player_view = Mock(show_placeholder=Mock())
    coordinator._render_presentation = Mock()
    coordinator._update_favorite_icon = Mock()
    coordinator._clear_play_profile = Mock()
    coordinator._info_panel = None

    with patch.object(
        playback_coordinator_module.QTimer,
        "singleShot",
        side_effect=lambda _delay, callback: callback(),
    ):
        PlaybackCoordinator._handle_presentation_changed(coordinator, presentation)

    coordinator._render_presentation.assert_called_once_with(presentation)
    coordinator._update_favorite_icon.assert_not_called()


def test_handle_presentation_changed_marks_live_photo_transaction_as_motion() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    presentation = replace(
        _make_presentation(
            path="/fake/photo.heic",
            is_video=False,
            is_live=True,
            request_generation=3,
        ),
        live_motion_abs=Path("/fake/photo.mov"),
    )
    coordinator._current_presentation = None
    coordinator._router = Mock(is_detail_view_active=Mock(return_value=True))
    coordinator._asset_model = Mock(set_current_row=Mock())
    coordinator.assetChanged = Mock(emit=Mock())
    coordinator._update_header = Mock()
    coordinator._select_filmstrip_row = Mock()
    coordinator._center_filmstrip_if_current = Mock()
    coordinator._player_view = Mock(show_placeholder=Mock())
    coordinator._render_presentation = Mock()
    coordinator._clear_play_profile = Mock()

    with patch.object(
        playback_coordinator_module.QTimer,
        "singleShot",
        side_effect=lambda _delay, callback: callback(),
    ):
        PlaybackCoordinator._handle_presentation_changed(coordinator, presentation)

    assert coordinator._detail_render_transaction.media_kind == "live_motion"


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
    coordinator._select_filmstrip_row = Mock()
    coordinator._center_filmstrip_if_current = Mock()
    coordinator._player_view = Mock(show_placeholder=Mock())
    coordinator._render_presentation = Mock()
    coordinator._update_favorite_icon = Mock()
    coordinator._clear_play_profile = Mock()
    coordinator._info_panel = None

    with patch.object(
        playback_coordinator_module.QTimer,
        "singleShot",
        side_effect=lambda _delay, callback: callback(),
    ):
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
    coordinator._select_filmstrip_row = Mock()
    coordinator._center_filmstrip_if_current = Mock()
    coordinator._player_view = Mock(show_placeholder=Mock())
    coordinator._render_presentation = Mock()
    coordinator._clear_play_profile = Mock()

    presentation = _make_presentation()

    with patch.object(
        playback_coordinator_module.QTimer,
        "singleShot",
        side_effect=lambda _delay, callback: callback(),
    ):
        PlaybackCoordinator._handle_presentation_changed(coordinator, presentation)

    assert coordinator._current_presentation is None
    coordinator._asset_model.set_current_row.assert_not_called()
    coordinator.assetChanged.emit.assert_not_called()
    coordinator._update_header.assert_not_called()
    coordinator._select_filmstrip_row.assert_not_called()
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
    coordinator._select_filmstrip_row = Mock()
    coordinator._center_filmstrip_if_current = Mock()
    coordinator._player_view = Mock(show_placeholder=Mock())
    coordinator._render_presentation = Mock()
    coordinator._clear_play_profile = Mock()
    coordinator._info_panel = None

    presentation = _make_presentation()
    with patch.object(
        playback_coordinator_module.QTimer,
        "singleShot",
        side_effect=lambda _delay, callback: callback(),
    ):
        PlaybackCoordinator._handle_presentation_changed(coordinator, presentation)

    coordinator._render_presentation.assert_not_called()
    coordinator._router.is_detail_view_active.return_value = True
    with patch.object(
        playback_coordinator_module.QTimer,
        "singleShot",
        side_effect=lambda _delay, callback: callback(),
    ):
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
    coordinator._edit_service_getter = None
    coordinator._adjustment_committer = Mock(commit=Mock(return_value=True))
    coordinator._library_manager = SimpleNamespace(
        edit_service=Mock(read_adjustments=Mock(return_value={"Exposure": 0.2}))
    )
    coordinator._player_view = SimpleNamespace(
        video_area=Mock(rotate_image_ccw=Mock(return_value={"Crop_Rotate90": 3.0})),
        image_viewer=Mock(rotate_image_ccw=Mock()),
        apply_committed_adjustments=Mock(return_value=True),
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
    coordinator._player_view.apply_committed_adjustments.assert_called_once_with(
        Path("/fake/video.mp4"),
        {"Exposure": 0.2, "Crop_Rotate90": 3.0},
        "rotate",
    )


def test_handle_rotate_requested_uses_injected_edit_service_for_still() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    edit_service = Mock(read_adjustments=Mock(return_value={"Exposure": 0.2}))
    coordinator._edit_service_getter = Mock(return_value=edit_service)
    coordinator._library_manager = None
    coordinator._adjustment_committer = Mock(commit=Mock(return_value=True))
    coordinator._player_view = SimpleNamespace(
        video_area=Mock(rotate_image_ccw=Mock()),
        image_viewer=Mock(
            rotate_image_ccw=Mock(return_value={"Crop_Rotate90": 3.0})
        ),
        apply_committed_adjustments=Mock(return_value=True),
    )
    source = Path("/fake/photo.jpg")

    PlaybackCoordinator._handle_rotate_requested(coordinator, source, False)

    coordinator._edit_service_getter.assert_called_once_with()
    edit_service.read_adjustments.assert_called_once_with(source)
    coordinator._adjustment_committer.commit.assert_called_once_with(
        source,
        {"Exposure": 0.2, "Crop_Rotate90": 3.0},
        reason="rotate",
    )
    coordinator._player_view.apply_committed_adjustments.assert_called_once_with(
        source,
        {"Exposure": 0.2, "Crop_Rotate90": 3.0},
        "rotate",
    )


def test_handle_rotate_requested_rolls_back_still_when_commit_fails() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    source = Path("/fake/photo.jpg")
    previous = {
        "Crop_Rotate90": 1.0,
        "Perspective_Vertical": 0.2,
        "Perspective_Horizontal": -0.1,
    }
    coordinator._edit_service_getter = Mock(
        return_value=Mock(read_adjustments=Mock(return_value=previous))
    )
    coordinator._library_manager = None
    coordinator._adjustment_committer = Mock(commit=Mock(return_value=False))
    coordinator._player_view = SimpleNamespace(
        video_area=Mock(),
        image_viewer=Mock(
            rotate_image_ccw=Mock(
                return_value={
                    "Crop_Rotate90": 0.0,
                    "Perspective_Vertical": -0.1,
                    "Perspective_Horizontal": -0.2,
                }
            )
        ),
        apply_committed_adjustments=Mock(return_value=True),
    )

    PlaybackCoordinator._handle_rotate_requested(coordinator, source, False)

    coordinator._player_view.apply_committed_adjustments.assert_called_once_with(
        source,
        previous,
        "rotate_rollback",
    )
    coordinator._player_view.image_viewer.set_adjustments.assert_not_called()


def test_twenty_rapid_rotations_accumulate_modulo_four_without_reload() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    source = Path("/fake/photo.jpg")
    persisted = {"Crop_Rotate90": 0.0}
    edit_service = Mock(
        read_adjustments=Mock(side_effect=lambda _path: dict(persisted))
    )

    def rotate() -> dict[str, float]:
        return {
            "Crop_Rotate90": float(
                (int(float(persisted.get("Crop_Rotate90", 0.0))) - 1) % 4
            )
        }

    def commit(_path, adjustments, *, reason):
        assert reason == "rotate"
        persisted.clear()
        persisted.update(adjustments)
        return True

    coordinator._edit_service_getter = Mock(return_value=edit_service)
    coordinator._library_manager = None
    coordinator._adjustment_committer = Mock(commit=Mock(side_effect=commit))
    coordinator._player_view = SimpleNamespace(
        video_area=Mock(),
        image_viewer=Mock(rotate_image_ccw=Mock(side_effect=rotate)),
        apply_committed_adjustments=Mock(return_value=True),
    )

    for _click in range(20):
        PlaybackCoordinator._handle_rotate_requested(coordinator, source, False)

    assert persisted["Crop_Rotate90"] == 0.0
    assert coordinator._adjustment_committer.commit.call_count == 20
    assert coordinator._player_view.apply_committed_adjustments.call_count == 20


def test_render_presentation_uses_viewmodel_video_state() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    video_area = Mock(begin_load=Mock(), play=Mock(), reset_zoom=Mock())
    coordinator._player_view = Mock(
        begin_video_transition=Mock(),
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
    coordinator._schedule_video_preparation = Mock()

    presentation = _make_presentation()

    PlaybackCoordinator._render_presentation(coordinator, presentation)

    video_area.begin_load.assert_called_once_with(Path("/fake/video.mp4"), 1)
    coordinator._schedule_video_preparation.assert_called_once_with(presentation)
    coordinator._player_view.begin_video_transition.assert_called_once_with(
        1,
        interactive_when_ready=True,
    )
    assert coordinator._trim_in_ms == 1000
    assert coordinator._trim_out_ms == 3000


def test_render_presentation_defers_video_load_during_location_file_write() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    video_area = Mock(
        has_video=Mock(return_value=True),
        stop=Mock(),
        present_video=Mock(),
        play=Mock(),
    )
    coordinator._player_view = Mock(
        show_placeholder=Mock(),
        video_area=video_area,
    )
    parent = Mock()
    parent.attach_mock(coordinator._player_view.show_placeholder, "show_placeholder")
    parent.attach_mock(video_area.stop, "stop")
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
    coordinator._location_video_write_inflight_paths = {Path("/fake/video.mp4")}

    presentation = _make_presentation()

    PlaybackCoordinator._render_presentation(coordinator, presentation)

    video_area.stop.assert_called_once_with()
    coordinator._player_view.show_placeholder.assert_called_once_with(
        playback_coordinator_module._LOCATION_VIDEO_WRITE_PLACEHOLDER
    )
    assert parent.mock_calls[:2] == [
        call.show_placeholder(playback_coordinator_module._LOCATION_VIDEO_WRITE_PLACEHOLDER),
        call.stop(),
    ]
    video_area.present_video.assert_not_called()
    video_area.play.assert_not_called()
    coordinator._player_bar.setEnabled.assert_called_once_with(False)


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

    assert parent.mock_calls == [call.stop()]
    player_view.display_image.assert_called_once_with(
        Path("/fake/photo.heic"),
        asset_id="asset-1",
        request_generation=1,
        transaction=None,
    )
    coordinator._player_bar.setEnabled.assert_called_once_with(False)
    coordinator._edit_button.setEnabled.assert_called_once_with(False)
    coordinator._refresh_face_name_overlay_for_presentation.assert_not_called()


def test_still_edit_enables_only_after_surface_is_presented() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    still = Path("/fake/photo.jpg")
    presentation = _make_presentation(path=str(still), is_video=False)
    transaction = PlaybackCoordinator._transaction_for_presentation(presentation)
    lifecycle = DetailRenderCoordinator()
    lifecycle.begin(transaction)
    lifecycle.mark_preparing(transaction.generation)
    coordinator._current_presentation = presentation
    coordinator._detail_render_transaction = transaction
    coordinator._detail_render_coordinator = lifecycle
    coordinator._active_live_motion = None
    coordinator._edit_button = Mock(setEnabled=Mock())
    coordinator._schedule_recognition_overlay = Mock()
    coordinator._prefetch_neighbor_stills = Mock()

    PlaybackCoordinator._on_still_frame_presented(
        coordinator,
        still,
        transaction.generation,
    )

    coordinator._edit_button.setEnabled.assert_called_once_with(True)
    assert lifecycle.snapshot is not None
    assert lifecycle.snapshot.state is DetailRenderState.PRESENTED


def test_still_loading_failure_marks_transaction_terminal_and_disables_edit() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    still = Path("/fake/photo.jpg")
    presentation = _make_presentation(path=str(still), is_video=False)
    transaction = PlaybackCoordinator._transaction_for_presentation(presentation)
    lifecycle = DetailRenderCoordinator()
    lifecycle.begin(transaction)
    lifecycle.mark_preparing(transaction.generation)
    coordinator._current_presentation = presentation
    coordinator._detail_render_transaction = transaction
    coordinator._detail_render_coordinator = lifecycle
    coordinator._edit_button = Mock(setEnabled=Mock())

    PlaybackCoordinator._on_still_loading_failed(
        coordinator,
        still,
        "decoder failed",
    )

    assert lifecycle.snapshot is not None
    assert lifecycle.snapshot.state is DetailRenderState.FAILED
    coordinator._edit_button.setEnabled.assert_called_once_with(False)


def test_live_photo_fallback_reuses_the_asset_identity() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    still = Path("/fake/photo.heic")
    motion = Path("/fake/photo.mov")
    coordinator._detail_request_generation = 7
    coordinator._active_live_motion = motion
    coordinator._active_live_still = still
    coordinator._active_live_asset_id = "asset-1"
    coordinator._active_live_media_generation = 11
    transaction = DetailRenderTransaction(
        generation=7,
        asset_id="asset-1",
        media_kind="live_motion",
        source_identity=AssetSourceIdentity.create(still),
    )
    coordinator._detail_render_transaction = transaction
    render_coordinator = Mock(owns_generation=Mock(return_value=True))
    coordinator._render_transaction_coordinator = Mock(return_value=render_coordinator)
    coordinator._player_view = Mock(
        defer_still_updates=Mock(),
        apply_pending_still=Mock(return_value=False),
        display_image=Mock(),
        show_live_badge=Mock(),
        set_live_replay_enabled=Mock(),
    )
    coordinator._player_bar = Mock(setEnabled=Mock())
    coordinator._refresh_face_name_overlay_for_current_presentation = Mock()

    PlaybackCoordinator._handle_playback_finished(coordinator, 7, motion, 11)

    coordinator._player_view.display_image.assert_called_once_with(
        still,
        transaction=transaction,
    )
    assert coordinator._active_live_asset_id == ""
    assert coordinator._active_live_still is None
    assert coordinator._active_live_media_generation is None


def test_live_photo_finish_rejects_stale_rapid_navigation_events() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    motion_a = Path("/fake/a.mov")
    motion_b = Path("/fake/b.mov")
    motion_c = Path("/fake/c.mov")
    coordinator._detail_request_generation = 23
    coordinator._active_live_motion = motion_c
    coordinator._active_live_media_generation = 103
    coordinator._restore_live_still = Mock(return_value=True)

    PlaybackCoordinator._handle_playback_finished(coordinator, 21, motion_a, 101)
    PlaybackCoordinator._handle_playback_finished(coordinator, 22, motion_b, 102)

    coordinator._restore_live_still.assert_not_called()

    PlaybackCoordinator._handle_playback_finished(coordinator, 23, motion_c, 103)

    coordinator._restore_live_still.assert_called_once_with()


def test_live_photo_finish_rejects_previous_replay_of_same_motion() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    motion = Path("/fake/photo.mov")
    coordinator._detail_request_generation = 7
    coordinator._active_live_motion = motion
    coordinator._active_live_media_generation = 12
    coordinator._restore_live_still = Mock(return_value=True)

    PlaybackCoordinator._handle_playback_finished(coordinator, 7, motion, 11)

    coordinator._restore_live_still.assert_not_called()

    PlaybackCoordinator._handle_playback_finished(coordinator, 7, motion, 12)

    coordinator._restore_live_still.assert_called_once_with()


def test_live_photo_motion_preparation_failure_restores_pending_still() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    still = Path("/fake/photo.heic")
    motion = Path("/fake/photo.mov")
    transaction = DetailRenderTransaction(
        generation=7,
        asset_id="asset-1",
        media_kind="live_motion",
        source_identity=AssetSourceIdentity.create(still),
    )
    lifecycle = DetailRenderCoordinator()
    lifecycle.begin(transaction)
    lifecycle.mark_preparing(7)
    token = PlaybackAsyncToken.create(
        library_epoch=1,
        asset_generation=2,
        asset_id="asset-1",
        source_identity=AssetSourceIdentity.create(motion),
    )
    presentation = replace(
        _make_presentation(
            path=str(still),
            asset_id="asset-1",
            is_video=False,
            is_live=True,
            request_generation=7,
        ),
        live_motion_abs=motion,
    )
    coordinator._pending_video_token = token
    coordinator._async_token_is_current = Mock(return_value=True)
    coordinator._render_transaction_coordinator = Mock(return_value=lifecycle)
    coordinator._detail_render_transaction = transaction
    coordinator._current_presentation = presentation
    coordinator._active_live_motion = motion
    coordinator._active_live_still = still
    coordinator._active_live_asset_id = "asset-1"
    coordinator._active_live_media_generation = 11
    coordinator._player_view = Mock(
        video_area=Mock(stop=Mock()),
        defer_still_updates=Mock(),
        apply_pending_still=Mock(return_value=True),
        display_image=Mock(),
        show_live_badge=Mock(),
        set_live_replay_enabled=Mock(),
        show_placeholder=Mock(),
    )
    coordinator._player_bar = Mock(setEnabled=Mock())
    coordinator._schedule_recognition_overlay = Mock()
    coordinator._prefetch_neighbor_stills = Mock()
    coordinator._is_playing = True

    PlaybackCoordinator._on_video_preparation_failed(
        coordinator,
        token,
        RuntimeError("broken motion metadata"),
    )

    assert coordinator._pending_video_token is None
    assert coordinator._active_live_motion is None
    assert coordinator._active_live_asset_id == ""
    assert coordinator._is_playing is False
    assert lifecycle.snapshot is not None
    assert lifecycle.snapshot.state is DetailRenderState.PREPARING
    coordinator._player_view.video_area.stop.assert_called_once_with()
    coordinator._player_view.defer_still_updates.assert_called_once_with(False)
    coordinator._player_view.apply_pending_still.assert_called_once_with()
    coordinator._player_view.display_image.assert_not_called()
    coordinator._player_view.show_placeholder.assert_not_called()
    coordinator._player_view.show_live_badge.assert_called_once_with()
    coordinator._player_view.set_live_replay_enabled.assert_called_once_with(True)

    PlaybackCoordinator._on_still_frame_presented(coordinator, still, 7)

    assert lifecycle.snapshot.state is DetailRenderState.PRESENTED
    assert lifecycle.snapshot.presented_surfaces == ("live_still",)
    coordinator._schedule_recognition_overlay.assert_called_once_with(presentation, 7)
    coordinator._prefetch_neighbor_stills.assert_called_once_with(0)


def test_regular_video_preparation_failure_remains_terminal() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    video = Path("/fake/video.mov")
    transaction = DetailRenderTransaction(
        generation=7,
        asset_id="asset-1",
        media_kind="video",
        source_identity=AssetSourceIdentity.create(video),
    )
    lifecycle = DetailRenderCoordinator()
    lifecycle.begin(transaction)
    lifecycle.mark_preparing(7)
    token = PlaybackAsyncToken.create(
        library_epoch=1,
        asset_generation=2,
        asset_id="asset-1",
        source_identity=AssetSourceIdentity.create(video),
    )
    coordinator._pending_video_token = token
    coordinator._async_token_is_current = Mock(return_value=True)
    coordinator._render_transaction_coordinator = Mock(return_value=lifecycle)
    coordinator._detail_render_transaction = transaction
    coordinator._active_live_motion = None
    coordinator._player_view = Mock(
        video_area=Mock(stop=Mock()),
        defer_still_updates=Mock(),
        show_placeholder=Mock(),
    )

    PlaybackCoordinator._on_video_preparation_failed(
        coordinator,
        token,
        RuntimeError("broken video metadata"),
    )

    assert coordinator._pending_video_token is None
    assert lifecycle.snapshot is not None
    assert lifecycle.snapshot.state is DetailRenderState.FAILED
    coordinator._player_view.video_area.stop.assert_called_once_with()
    coordinator._player_view.defer_still_updates.assert_not_called()
    coordinator._player_view.show_placeholder.assert_called_once_with(
        "Unable to load this video."
    )


def test_live_motion_first_frame_completes_current_transaction() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    render_coordinator = Mock(
        mark_surface_presented=Mock(
            return_value=DetailSurfacePresentationResult.NEW_SURFACE
        )
    )
    coordinator._render_transaction_coordinator = Mock(return_value=render_coordinator)
    coordinator._detail_request_generation = 7
    coordinator._active_live_motion = Path("/fake/photo.mov")
    coordinator._current_presentation = replace(
        _make_presentation(
            path="/fake/photo.heic",
            is_video=False,
            is_live=True,
            request_generation=7,
        ),
        live_motion_abs=Path("/fake/photo.mov"),
    )
    coordinator._player_view = Mock()

    PlaybackCoordinator._on_video_first_frame_presented(coordinator, 7)

    render_coordinator.mark_surface_presented.assert_called_once_with(
        7,
        "live_motion_frame",
    )


def test_regular_video_first_frame_enables_interactive_controls() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    render_coordinator = Mock(
        mark_surface_presented=Mock(
            return_value=DetailSurfacePresentationResult.NEW_SURFACE
        )
    )
    coordinator._render_transaction_coordinator = Mock(return_value=render_coordinator)
    coordinator._detail_request_generation = 7
    coordinator._active_live_motion = None
    coordinator._current_presentation = _make_presentation(request_generation=7)
    coordinator._player_view = Mock()

    PlaybackCoordinator._on_video_first_frame_presented(coordinator, 7)

    render_coordinator.mark_surface_presented.assert_called_once_with(
        7,
        "video_frame",
    )


def test_live_motion_deferred_still_frame_does_not_complete_transaction() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    render_coordinator = Mock(
        mark_surface_presented=Mock(
            return_value=DetailSurfacePresentationResult.NEW_SURFACE
        )
    )
    coordinator._render_transaction_coordinator = Mock(return_value=render_coordinator)
    still = Path("/fake/photo.heic")
    coordinator._active_live_motion = Path("/fake/photo.mov")
    coordinator._current_presentation = _make_presentation(
        path=str(still),
        is_video=False,
        is_live=True,
        request_generation=7,
    )

    PlaybackCoordinator._on_still_frame_presented(coordinator, still, 7)

    render_coordinator.mark_surface_presented.assert_not_called()


def test_rejected_still_surface_does_not_refresh_overlay_or_prefetch() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    still = Path("/fake/photo.heic")
    transaction = DetailRenderTransaction(
        generation=7,
        asset_id="asset-1",
        media_kind="live_motion",
        source_identity=AssetSourceIdentity.create(still),
    )
    render_coordinator = Mock(
        owns_generation=Mock(return_value=True),
        mark_surface_presented=Mock(
            return_value=DetailSurfacePresentationResult.REJECTED_STALE
        ),
    )
    coordinator._render_transaction_coordinator = Mock(return_value=render_coordinator)
    coordinator._detail_render_transaction = transaction
    coordinator._active_live_motion = None
    coordinator._current_presentation = _make_presentation(
        path=str(still),
        asset_id="asset-1",
        is_video=False,
        is_live=True,
        request_generation=7,
    )
    coordinator._presented_still_source = Path("/fake/previous.heic")
    coordinator._presented_still_generation = 6
    coordinator._schedule_recognition_overlay = Mock()
    coordinator._prefetch_neighbor_stills = Mock()

    PlaybackCoordinator._on_still_frame_presented(coordinator, still, 7)

    assert coordinator._presented_still_source == Path("/fake/previous.heic")
    assert coordinator._presented_still_generation == 6
    coordinator._schedule_recognition_overlay.assert_not_called()
    coordinator._prefetch_neighbor_stills.assert_not_called()


@pytest.mark.parametrize("has_pending_still", [False, True])
def test_live_photo_motion_to_still_runs_overlay_and_prefetch(
    qapp,
    has_pending_still: bool,
) -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    still = Path("/fake/photo.heic")
    motion = Path("/fake/photo.mov")
    transaction = DetailRenderTransaction(
        generation=7,
        asset_id="asset-1",
        media_kind="live_motion",
        source_identity=AssetSourceIdentity.create(still),
    )
    lifecycle = DetailRenderCoordinator()
    lifecycle.begin(transaction)
    lifecycle.mark_preparing(7)
    coordinator._render_transaction_coordinator = Mock(return_value=lifecycle)
    coordinator._detail_render_transaction = transaction
    coordinator._detail_request_generation = 7
    coordinator._active_live_motion = motion
    coordinator._active_live_still = still
    coordinator._active_live_asset_id = "asset-1"
    coordinator._active_live_media_generation = 11
    coordinator._current_presentation = _make_presentation(
        path=str(still),
        asset_id="asset-1",
        is_video=False,
        is_live=True,
        request_generation=7,
    )
    coordinator._player_view = Mock(
        defer_still_updates=Mock(),
        apply_pending_still=Mock(return_value=has_pending_still),
        display_image=Mock(),
        show_live_badge=Mock(),
        set_live_replay_enabled=Mock(),
    )
    coordinator._player_bar = Mock(setEnabled=Mock())
    coordinator._schedule_recognition_overlay = Mock()
    coordinator._prefetch_neighbor_stills = Mock()

    PlaybackCoordinator._on_video_first_frame_presented(coordinator, 7)
    PlaybackCoordinator._handle_playback_finished(coordinator, 7, motion, 11)
    PlaybackCoordinator._on_still_frame_presented(coordinator, still, 7)

    if has_pending_still:
        coordinator._player_view.display_image.assert_not_called()
    else:
        coordinator._player_view.display_image.assert_called_once_with(
            still,
            transaction=transaction,
        )
    assert lifecycle.snapshot is not None
    assert lifecycle.snapshot.presented_surfaces == (
        "live_motion_frame",
        "live_still",
    )
    coordinator._schedule_recognition_overlay.assert_called_once_with(
        coordinator._current_presentation,
        7,
    )
    coordinator._prefetch_neighbor_stills.assert_called_once_with(0)


def test_live_photo_second_replay_restores_overlay_without_reopening_transaction(
    qapp,
) -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    still = Path("/fake/photo.heic")
    motion = Path("/fake/photo.mov")
    transaction = DetailRenderTransaction(
        generation=7,
        asset_id="asset-1",
        media_kind="live_motion",
        source_identity=AssetSourceIdentity.create(still),
    )
    lifecycle = DetailRenderCoordinator()
    terminal_presentations = []
    lifecycle_surfaces = []
    lifecycle.presented.connect(terminal_presentations.append)
    lifecycle.surfacePresented.connect(
        lambda _snapshot, kind: lifecycle_surfaces.append(kind)
    )
    lifecycle.begin(transaction)
    lifecycle.mark_preparing(7)
    presentation = replace(
        _make_presentation(
            path=str(still),
            asset_id="asset-1",
            is_video=False,
            is_live=True,
            request_generation=7,
        ),
        live_motion_abs=motion,
    )
    coordinator._render_transaction_coordinator = Mock(return_value=lifecycle)
    coordinator._detail_render_transaction = transaction
    coordinator._detail_request_generation = 7
    coordinator._current_presentation = presentation
    coordinator._face_name_overlay = Mock()
    coordinator._player_view = Mock(
        video_area=Mock(begin_load=Mock(side_effect=[11, 12])),
        defer_still_updates=Mock(),
        apply_pending_still=Mock(return_value=False),
        display_image=Mock(),
        show_live_badge=Mock(),
        set_live_replay_enabled=Mock(),
    )
    coordinator._player_bar = Mock(setEnabled=Mock())
    coordinator._schedule_video_preparation = Mock()
    coordinator._schedule_recognition_overlay = Mock()
    coordinator._prefetch_neighbor_stills = Mock()
    coordinator._is_playing = False

    PlaybackCoordinator._autoplay_live_motion(coordinator, presentation)
    PlaybackCoordinator._on_video_first_frame_presented(coordinator, 7)
    PlaybackCoordinator._handle_playback_finished(coordinator, 7, motion, 11)
    PlaybackCoordinator._on_still_frame_presented(coordinator, still, 7)

    PlaybackCoordinator.replay_live_photo(coordinator)
    PlaybackCoordinator._on_video_first_frame_presented(coordinator, 7)
    PlaybackCoordinator._handle_playback_finished(coordinator, 7, motion, 12)
    PlaybackCoordinator._on_still_frame_presented(coordinator, still, 7)

    assert len(terminal_presentations) == 1
    assert lifecycle_surfaces == ["live_motion_frame", "live_still"]
    assert lifecycle.snapshot is not None
    assert lifecycle.snapshot.presented_surfaces == (
        "live_motion_frame",
        "live_still",
    )
    assert coordinator._presented_still_source == still
    assert coordinator._presented_still_generation == 7
    assert coordinator._schedule_recognition_overlay.call_count == 2
    assert coordinator._prefetch_neighbor_stills.call_args_list == [call(0), call(0)]
    assert coordinator._player_view.display_image.call_args_list == [
        call(still, transaction=transaction),
        call(still, transaction=transaction),
    ]
    assert coordinator._face_name_overlay.set_overlay_active.call_args_list == [
        call(False),
        call(False),
    ]


def test_live_photo_replay_preparation_failure_restores_overlay() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    still = Path("/fake/photo.heic")
    motion = Path("/fake/photo.mov")
    transaction = DetailRenderTransaction(
        generation=7,
        asset_id="asset-1",
        media_kind="live_motion",
        source_identity=AssetSourceIdentity.create(still),
    )
    lifecycle = DetailRenderCoordinator()
    lifecycle.begin(transaction)
    lifecycle.mark_preparing(7)
    lifecycle.mark_surface_presented(7, "live_motion_frame")
    lifecycle.mark_surface_presented(7, "live_still")
    presentation = replace(
        _make_presentation(
            path=str(still),
            asset_id="asset-1",
            is_video=False,
            is_live=True,
            request_generation=7,
        ),
        live_motion_abs=motion,
    )
    token = PlaybackAsyncToken.create(
        library_epoch=1,
        asset_generation=2,
        asset_id="asset-1",
        source_identity=AssetSourceIdentity.create(motion),
    )
    coordinator._render_transaction_coordinator = Mock(return_value=lifecycle)
    coordinator._detail_render_transaction = transaction
    coordinator._detail_request_generation = 7
    coordinator._current_presentation = presentation
    coordinator._face_name_overlay = Mock()
    coordinator._player_view = Mock(
        video_area=Mock(begin_load=Mock(), stop=Mock()),
        defer_still_updates=Mock(),
        apply_pending_still=Mock(return_value=False),
        display_image=Mock(),
        show_live_badge=Mock(),
        set_live_replay_enabled=Mock(),
        show_placeholder=Mock(),
    )
    coordinator._player_bar = Mock(setEnabled=Mock())
    coordinator._schedule_video_preparation = Mock()
    coordinator._schedule_recognition_overlay = Mock()
    coordinator._prefetch_neighbor_stills = Mock()
    coordinator._async_token_is_current = Mock(return_value=True)
    coordinator._active_live_motion = None
    coordinator._active_live_still = still
    coordinator._active_live_asset_id = ""
    coordinator._is_playing = False

    PlaybackCoordinator._on_still_frame_presented(coordinator, still, 7)
    PlaybackCoordinator.replay_live_photo(coordinator)
    coordinator._pending_video_token = token
    PlaybackCoordinator._on_video_preparation_failed(
        coordinator,
        token,
        RuntimeError("replay metadata failed"),
    )
    PlaybackCoordinator._on_still_frame_presented(coordinator, still, 7)

    assert lifecycle.snapshot is not None
    assert lifecycle.snapshot.state is DetailRenderState.PRESENTED
    assert lifecycle.snapshot.presented_surfaces == (
        "live_motion_frame",
        "live_still",
    )
    coordinator._player_view.video_area.stop.assert_called_once_with()
    coordinator._player_view.display_image.assert_called_once_with(
        still,
        transaction=transaction,
    )
    coordinator._player_view.show_placeholder.assert_not_called()
    assert coordinator._schedule_recognition_overlay.call_count == 2
    assert coordinator._prefetch_neighbor_stills.call_args_list == [call(0), call(0)]


def test_old_video_preparation_result_is_rejected_after_rebind() -> None:
    path = Path("/shared/video.mov")
    identity = AssetSourceIdentity.create(path, size_bytes=10, source_mtime_ns=11)
    old_token = PlaybackAsyncToken.create(
        library_epoch=1,
        asset_generation=4,
        asset_id="asset-1",
        source_identity=identity,
    )
    new_token = PlaybackAsyncToken.create(
        library_epoch=2,
        asset_generation=5,
        asset_id="asset-1",
        source_identity=identity,
    )
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._library_epoch = 2
    coordinator._library_epoch_getter = lambda: 2
    coordinator._active_async_token = new_token
    coordinator._pending_video_token = old_token
    coordinator._current_presentation = _make_presentation(path=str(path))
    coordinator._active_live_motion = None
    coordinator._player_view = Mock(video_area=Mock(commit_presentation=Mock()))

    PlaybackCoordinator._on_video_preparation_ready(
        coordinator,
        old_token,
        object(),
    )

    coordinator._player_view.video_area.commit_presentation.assert_not_called()


def test_current_video_preparation_token_commits_result() -> None:
    path = Path("/shared/video.mov")
    identity = AssetSourceIdentity.create(path, size_bytes=10, source_mtime_ns=11)
    token = PlaybackAsyncToken.create(
        library_epoch=2,
        asset_generation=5,
        asset_id="asset-1",
        source_identity=identity,
    )
    transaction = DetailRenderTransaction(
        generation=7,
        asset_id="asset-1",
        media_kind="video",
        source_identity=identity,
    )
    state = VideoPresentationState(
        request_generation=7,
        adjustments={},
        trim_range_ms=None,
        adjusted_preview=False,
        rotation_cw=0,
        raw_width=1920,
        raw_height=1080,
        linux_180_hint=False,
    )
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._library_epoch = 2
    coordinator._library_epoch_getter = lambda: 2
    coordinator._active_async_token = token
    coordinator._pending_video_token = token
    coordinator._current_presentation = _make_presentation(
        path=str(path),
        request_generation=7,
    )
    coordinator._active_live_motion = None
    coordinator._detail_render_transaction = transaction
    coordinator._player_view = Mock(
        video_area=Mock(commit_presentation=Mock(return_value=True), play=Mock())
    )

    PlaybackCoordinator._on_video_preparation_ready(coordinator, token, state)

    committed = coordinator._player_view.video_area.commit_presentation.call_args.args[0]
    assert committed.transaction is transaction
    coordinator._player_view.video_area.play.assert_called_once_with()


def test_old_deferred_geocode_result_is_rejected_after_rebind() -> None:
    path = Path("/shared/photo.jpg")
    identity = AssetSourceIdentity.create(path, size_bytes=10, source_mtime_ns=11)
    old_token = PlaybackAsyncToken.create(
        library_epoch=1,
        asset_generation=4,
        asset_id="asset-1",
        source_identity=identity,
    )
    new_token = PlaybackAsyncToken.create(
        library_epoch=2,
        asset_generation=5,
        asset_id="asset-1",
        source_identity=identity,
    )
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._library_epoch = 2
    coordinator._library_epoch_getter = lambda: 2
    coordinator._active_async_token = new_token
    coordinator._pending_location_token = old_token
    coordinator._deferred_locations = {}
    coordinator._current_presentation = _make_presentation(
        path=str(path),
        is_video=False,
    )
    coordinator._update_header = Mock()

    PlaybackCoordinator._on_deferred_location_ready(
        coordinator,
        old_token,
        "Berlin",
    )

    assert coordinator._deferred_locations == {}
    coordinator._update_header.assert_not_called()


def test_current_deferred_geocode_token_updates_current_header() -> None:
    path = Path("/shared/photo.jpg")
    identity = AssetSourceIdentity.create(path, size_bytes=10, source_mtime_ns=11)
    token = PlaybackAsyncToken.create(
        library_epoch=2,
        asset_generation=5,
        asset_id="asset-1",
        source_identity=identity,
    )
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._library_epoch = 2
    coordinator._library_epoch_getter = lambda: 2
    coordinator._active_async_token = token
    coordinator._pending_location_token = token
    coordinator._deferred_locations = {}
    coordinator._current_presentation = _make_presentation(
        path=str(path),
        is_video=False,
    )
    coordinator._update_header = Mock()

    PlaybackCoordinator._on_deferred_location_ready(coordinator, token, "Berlin")

    assert coordinator._deferred_locations == {path: "Berlin"}
    assert coordinator._current_presentation.location == "Berlin"
    coordinator._update_header.assert_called_once_with(
        coordinator._current_presentation
    )


def test_same_library_tree_refresh_does_not_clear_render_session() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._player_view = Mock(clear_frame_cache=Mock())
    coordinator._invalidate_overlay_requests = Mock()

    PlaybackCoordinator.rebind_library(
        coordinator,
        7,
        session_changed=False,
    )

    coordinator._player_view.clear_frame_cache.assert_not_called()
    coordinator._invalidate_overlay_requests.assert_not_called()


def test_session_rebind_invalidates_tokens_and_render_state() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._library_epoch = 1
    coordinator._library_epoch_getter = lambda: 2
    coordinator._asset_generation = 4
    coordinator._detail_request_generation = 9
    coordinator._active_async_token = object()
    coordinator._pending_video_token = object()
    coordinator._pending_location_token = object()
    coordinator._invalidate_overlay_requests = Mock()
    coordinator._video_prepare_pool = Mock(clear=Mock())
    coordinator._deferred_location_pool = Mock(clear=Mock())
    coordinator._deferred_locations = {Path("/old.jpg"): "Old"}
    lifecycle = Mock(reset=Mock())
    coordinator._render_transaction_coordinator = Mock(return_value=lifecycle)
    coordinator._detail_render_transaction = object()
    coordinator._current_presentation = object()
    coordinator._active_live_motion = Path("/old.mov")
    coordinator._active_live_still = Path("/old.jpg")
    coordinator._active_live_asset_id = "old"
    coordinator._presented_still_generation = 9
    coordinator._presented_still_source = Path("/old.jpg")
    coordinator._player_view = Mock(
        video_area=Mock(stop=Mock()),
        defer_still_updates=Mock(),
        cancel_pending_image_requests=Mock(),
        clear_frame_cache=Mock(),
        show_placeholder=Mock(),
    )
    coordinator._player_bar = Mock(setEnabled=Mock())
    coordinator._is_playing = True
    coordinator._update_header = Mock()
    coordinator._info_panel = None
    coordinator._clear_info_panel_metadata_state = Mock()
    coordinator._clear_confirmed_location_metadata = Mock()

    PlaybackCoordinator.rebind_library(coordinator, 2, session_changed=True)

    assert coordinator._library_epoch == 2
    assert coordinator._asset_generation == 5
    assert coordinator._detail_request_generation == 10
    assert coordinator._active_async_token is None
    assert coordinator._pending_video_token is None
    assert coordinator._pending_location_token is None
    assert coordinator._current_presentation is None
    lifecycle.reset.assert_called_once_with()
    coordinator._player_view.video_area.stop.assert_called_once_with()
    coordinator._player_view.clear_frame_cache.assert_called_once_with()


def test_neighbor_prefetch_preserves_asset_descriptors() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    previous = DetailPrefetchDescriptor(
        row=1,
        asset_id="asset-previous",
        path=Path("/fake/previous.heic"),
        is_video=False,
    )
    following = DetailPrefetchDescriptor(
        row=3,
        asset_id="asset-following",
        path=Path("/fake/following.heic"),
        is_video=False,
    )
    coordinator._asset_model = Mock(
        detail_prefetch_descriptor=Mock(side_effect=[previous, following]),
    )
    coordinator._player_view = Mock(prefetch_images=Mock())

    PlaybackCoordinator._prefetch_neighbor_stills(coordinator, 2)

    coordinator._player_view.prefetch_images.assert_called_once_with(
        [previous, following]
    )


def test_reset_for_gallery_hides_info_panel_and_clears_viewmodel_state() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._player_view = Mock(
        video_area=Mock(stop=Mock(), has_video=Mock(return_value=True)),
        show_placeholder=Mock(),
    )
    coordinator._player_bar = Mock(setEnabled=Mock())
    coordinator._is_playing = True
    coordinator._current_presentation = _make_presentation()
    coordinator._detail_vm = Mock(hide_info_panel=Mock())
    coordinator._update_header = Mock()
    coordinator._info_panel = Mock(hide=Mock())
    coordinator._hide_face_name_overlay = Mock()
    coordinator._confirmed_location_metadata = {
        Path("/fake/video.mp4"): {"location": "Munich"}
    }

    PlaybackCoordinator.reset_for_gallery(coordinator)

    coordinator._player_view.video_area.stop.assert_called_once_with()
    coordinator._player_view.show_placeholder.assert_called_once_with()
    coordinator._player_bar.setEnabled.assert_called_once_with(False)
    coordinator._detail_vm.hide_info_panel.assert_called_once_with(refresh_presentation=False)
    coordinator._update_header.assert_called_once_with(None)
    coordinator._info_panel.hide.assert_called_once_with()
    coordinator._hide_face_name_overlay.assert_called_once_with(clear_annotations=True)
    assert coordinator._confirmed_location_metadata == {}


def test_shutdown_releases_info_panel_once_and_hides_it() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._invalidate_overlay_requests = Mock()
    coordinator._overlay_pool = Mock(waitForDone=Mock())
    coordinator._clear_play_request_state = Mock()
    coordinator._location_search_controller = None
    coordinator._player_view = Mock(video_area=Mock(stop=Mock()), shutdown=Mock())
    coordinator._hide_face_name_overlay = Mock()
    coordinator._detail_vm = Mock(hide_info_panel=Mock())
    coordinator._update_header = Mock()
    coordinator._info_panel = Mock(shutdown=Mock(), hide=Mock())
    coordinator._clear_info_panel_metadata_state = Mock()
    coordinator._clear_confirmed_location_metadata = Mock()

    PlaybackCoordinator.shutdown(coordinator)

    coordinator._info_panel.shutdown.assert_called_once_with()
    coordinator._info_panel.hide.assert_called_once_with()
    coordinator._info_panel.close.assert_not_called()
    coordinator._detail_vm.hide_info_panel.assert_called_once_with(
        refresh_presentation=False,
    )


def test_reset_for_gallery_skips_media_cleanup_when_idle() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._player_view = Mock(
        video_area=Mock(stop=Mock(), has_video=Mock(return_value=False)),
        show_placeholder=Mock(),
    )
    coordinator._player_bar = Mock(setEnabled=Mock())
    coordinator._is_playing = False
    coordinator._current_presentation = None
    coordinator._router = Mock(is_detail_view_active=Mock(return_value=False))
    coordinator._detail_vm = Mock(hide_info_panel=Mock())
    coordinator._update_header = Mock()
    coordinator._info_panel = None
    coordinator._hide_face_name_overlay = Mock()
    coordinator._confirmed_location_metadata = {
        Path("/fake/video.mp4"): {"location": "Munich"}
    }

    PlaybackCoordinator.reset_for_gallery(coordinator)

    coordinator._player_view.video_area.stop.assert_not_called()
    coordinator._player_view.show_placeholder.assert_not_called()
    coordinator._hide_face_name_overlay.assert_not_called()
    coordinator._player_bar.setEnabled.assert_called_once_with(False)
    coordinator._detail_vm.hide_info_panel.assert_called_once_with(refresh_presentation=False)
    coordinator._update_header.assert_called_once_with(None)
    assert coordinator._confirmed_location_metadata == {}


def test_reset_for_gallery_releases_loaded_video_source_even_without_presentation() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._player_view = Mock(
        video_area=Mock(stop=Mock(), has_video=Mock(return_value=True)),
        show_placeholder=Mock(),
    )
    coordinator._player_bar = Mock(setEnabled=Mock())
    coordinator._is_playing = False
    coordinator._current_presentation = None
    coordinator._router = Mock(is_detail_view_active=Mock(return_value=False))
    coordinator._detail_vm = Mock(hide_info_panel=Mock())
    coordinator._update_header = Mock()
    coordinator._info_panel = None
    coordinator._hide_face_name_overlay = Mock()
    coordinator._confirmed_location_metadata = {}

    PlaybackCoordinator.reset_for_gallery(coordinator)

    coordinator._player_view.video_area.stop.assert_called_once_with()
    coordinator._player_view.show_placeholder.assert_called_once_with()
    coordinator._hide_face_name_overlay.assert_called_once_with(clear_annotations=True)


def test_reset_for_gallery_clears_detail_view_without_video_stop_for_photo() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._player_view = Mock(
        video_area=Mock(stop=Mock(), has_video=Mock(return_value=False)),
        show_placeholder=Mock(),
    )
    coordinator._player_bar = Mock(setEnabled=Mock())
    coordinator._is_playing = False
    coordinator._current_presentation = _make_presentation(path="/fake/photo.jpg")
    coordinator._router = Mock(is_detail_view_active=Mock(return_value=True))
    coordinator._detail_vm = Mock(hide_info_panel=Mock())
    coordinator._update_header = Mock()
    coordinator._info_panel = None
    coordinator._hide_face_name_overlay = Mock()
    coordinator._confirmed_location_metadata = {}

    PlaybackCoordinator.reset_for_gallery(coordinator)

    coordinator._player_view.video_area.stop.assert_not_called()
    coordinator._player_view.show_placeholder.assert_called_once_with()
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
    coordinator._people_service = PeopleService()
    library_root = Path("/fake/library")
    recreated_service = PeopleService(
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
    coordinator._map_runtime = SimpleNamespace(
        capabilities=lambda: SimpleNamespace(location_search_available=True),
        package_root=lambda: Path("/fake/maps"),
    )
    coordinator._location_search_controller = Mock(warm_up=Mock())

    enabled = PlaybackCoordinator._refresh_location_extension_state(coordinator)

    assert enabled is True
    coordinator._location_search_controller.warm_up.assert_called_once()
    assert coordinator._location_search_controller.warm_up.call_args.kwargs["package_root"] == Path(
        "/fake/maps"
    )


def test_refresh_location_extension_state_uses_runtime_package_root() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._map_runtime = SimpleNamespace(
        capabilities=lambda: SimpleNamespace(location_search_available=True),
        package_root=lambda: Path("/fake/maps"),
    )
    coordinator._location_search_controller = Mock(warm_up=Mock())

    enabled = PlaybackCoordinator._refresh_location_extension_state(coordinator)

    assert enabled is True
    assert PlaybackCoordinator._map_runtime_package_root(coordinator) == Path("/fake/maps")


def test_refresh_location_extension_state_falls_back_to_session_runtime_when_unbound(
    monkeypatch,
) -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._map_runtime = None
    coordinator._library_manager = SimpleNamespace(map_runtime=None)
    coordinator._location_search_controller = Mock(warm_up=Mock())

    fallback_runtime = SimpleNamespace(
        capabilities=lambda: SimpleNamespace(location_search_available=True),
        package_root=lambda: Path("/fallback/maps"),
    )
    monkeypatch.setattr(
        playback_coordinator_module,
        "SessionMapRuntimeService",
        lambda: fallback_runtime,
    )

    enabled = PlaybackCoordinator._refresh_location_extension_state(coordinator)

    assert enabled is True
    assert coordinator._map_runtime is fallback_runtime
    assert PlaybackCoordinator._map_runtime_package_root(coordinator) == Path("/fallback/maps")


def test_refresh_face_name_overlay_schedules_only_after_still_is_presented() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    overlay = Mock()
    coordinator._face_name_overlay = overlay
    coordinator._show_face_names = True
    coordinator._active_live_motion = None
    coordinator._player_view = SimpleNamespace(
        video_area=SimpleNamespace(is_edit_mode_active=lambda: False),
    )
    coordinator._presented_still_source = Path("/fake/photo.jpg")
    coordinator._presented_still_generation = 17
    coordinator._schedule_recognition_overlay = Mock()
    presentation = _make_presentation(
        path="/fake/photo.jpg",
        asset_id="asset-photo",
        is_video=False,
    )

    PlaybackCoordinator._refresh_face_name_overlay_for_presentation(
        coordinator,
        presentation,
    )

    coordinator._schedule_recognition_overlay.assert_called_once_with(presentation, 17)
    overlay.set_annotations.assert_not_called()


def test_disabled_face_names_never_start_overlay_query() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._show_face_names = False
    coordinator._active_live_motion = None
    coordinator._face_name_overlay = Mock()
    coordinator._recognition_query_service = Mock()
    coordinator._overlay_pool = Mock()
    coordinator._player_view = SimpleNamespace(
        video_area=SimpleNamespace(is_edit_mode_active=lambda: False),
    )

    PlaybackCoordinator._schedule_recognition_overlay(
        coordinator,
        _make_presentation(
            path="/fake/photo.jpg",
            asset_id="asset-photo",
            is_video=False,
        ),
        3,
    )

    coordinator._overlay_pool.start.assert_not_called()
    coordinator._recognition_query_service.load_overlay.assert_not_called()


def test_recognition_overlay_worker_loads_identity_candidates_with_annotations() -> None:
    snapshot = SimpleNamespace(
        asset_id="asset-photo",
        faces=(),
        pets=(),
        candidates=(SimpleNamespace(identity_key="person:person-a", name="Alice"),),
    )
    query_service = Mock(load_overlay=Mock(return_value=snapshot))
    signals = Mock()
    worker = playback_coordinator_module._RecognitionOverlayWorker(
        request_generation=8,
        still_generation=4,
        asset_id="asset-photo",
        query_service=query_service,
        signals=signals,
    )

    worker.run()

    query_service.load_overlay.assert_called_once_with("asset-photo")
    signals.ready.emit.assert_called_once_with(8, 4, snapshot)
    signals.failed.emit.assert_not_called()


def test_stale_overlay_generation_is_not_applied() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._overlay_request_generation = 8
    coordinator._show_face_names = True
    coordinator._face_name_overlay = Mock()

    PlaybackCoordinator._on_recognition_overlay_ready(
        coordinator,
        7,
        4,
        SimpleNamespace(),
    )

    coordinator._face_name_overlay.set_annotations.assert_not_called()


def test_load_recognition_identity_suggestions_mixes_people_and_pets() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._people_service = Mock(
        list_clusters=Mock(
            return_value=[
                SimpleNamespace(
                    person_id="person-a",
                    name="Alice",
                    thumbnail_path=Path("/tmp/alice.jpg"),
                    face_count=4,
                )
            ]
        )
    )
    coordinator._pet_service = Mock(
        list_pets=Mock(
            return_value=[
                SimpleNamespace(
                    pet_id="pet-a",
                    name="Miso",
                    thumbnail_path=Path("/tmp/miso.jpg"),
                    detection_count=2,
                )
            ]
        )
    )

    suggestions = PlaybackCoordinator._load_recognition_identity_suggestions(
        coordinator,
        include_hidden=False,
    )

    assert [(item.identity_key, item.name, item.count) for item in suggestions] == [
        ("person:person-a", "Alice", 4),
        ("pet:pet-a", "Miso", 2),
    ]
    coordinator._people_service.list_clusters.assert_called_once_with(include_hidden=False)
    coordinator._pet_service.list_pets.assert_called_once_with(include_hidden=False)


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
    coordinator._recognition_query_service = Mock()
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
    coordinator._recognition_query_service.invalidate.assert_called_once_with(None)
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


def test_unassigned_pending_face_rename_creates_confirmed_person() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._people_service = Mock(
        move_face_to_new_person=Mock(return_value="person-new")
    )
    coordinator._current_presentation = _make_presentation(
        path="/fake/photo.jpg",
        asset_id="asset-photo",
        is_video=False,
    )
    coordinator._refresh_face_name_overlay_for_current_presentation = Mock()
    coordinator._refresh_info_panel_faces = Mock()
    coordinator._people_dashboard_refresh_callback = Mock()
    annotation = AssetFaceAnnotation(
        face_id="noise-face",
        person_id=None,
        display_name=None,
        box_x=0,
        box_y=0,
        box_w=10,
        box_h=10,
        image_width=100,
        image_height=100,
        promotion_state="candidate",
    )

    PlaybackCoordinator._handle_info_panel_face_move_to_new_person_requested(
        coordinator,
        annotation,
        "Alice",
    )

    coordinator._people_service.move_face_to_new_person.assert_called_once_with(
        "noise-face",
        "Alice",
    )
    coordinator._refresh_face_name_overlay_for_current_presentation.assert_called_once_with()
    coordinator._refresh_info_panel_faces.assert_called_once_with("asset-photo")
    coordinator._people_dashboard_refresh_callback.assert_called_once_with()


def test_unassigned_pending_face_dropdown_moves_detection_to_existing_identity() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._handle_info_panel_face_move_requested = Mock()
    annotation = AssetFaceAnnotation(
        face_id="noise-face",
        person_id=None,
        display_name=None,
        box_x=0,
        box_y=0,
        box_w=10,
        box_h=10,
        image_width=100,
        image_height=100,
        promotion_state="candidate",
    )

    PlaybackCoordinator._handle_face_name_existing_identity_submitted(
        coordinator,
        annotation,
        "person:person-existing",
    )

    coordinator._handle_info_panel_face_move_requested.assert_called_once_with(
        annotation,
        "person:person-existing",
    )


def test_assigned_name_dropdown_merges_entire_identity_into_selection() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._recognition_merge_service = Mock(
        merge=Mock(return_value=SimpleNamespace(merged=True, failure=None))
    )
    coordinator._recognition_query_service = Mock()
    coordinator._refresh_face_name_overlay_for_current_presentation = Mock()
    coordinator._current_presentation = None
    coordinator._people_dashboard_refresh_callback = Mock()
    annotation = AssetFaceAnnotation(
        face_id="face-1",
        person_id="person-candidate",
        display_name=None,
        box_x=0,
        box_y=0,
        box_w=10,
        box_h=10,
        image_width=100,
        image_height=100,
        promotion_state="candidate",
    )

    PlaybackCoordinator._handle_face_name_existing_identity_submitted(
        coordinator,
        annotation,
        "person:person-existing",
    )

    coordinator._recognition_merge_service.merge.assert_called_once_with(
        "person:person-candidate",
        "person:person-existing",
    )
    coordinator._recognition_query_service.invalidate.assert_called_once_with(None)
    coordinator._refresh_face_name_overlay_for_current_presentation.assert_called_once_with()
    coordinator._people_dashboard_refresh_callback.assert_called_once_with()


def test_pet_name_dropdown_reports_same_asset_merge_conflict() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._recognition_merge_service = Mock(
        merge=Mock(
            return_value=SimpleNamespace(
                merged=False,
                failure=SimpleNamespace(value="same_asset_conflict"),
            )
        )
    )
    coordinator._face_name_overlay = Mock()
    annotation = RecognitionAnnotation(
        source_detection_kind="pet",
        source_annotation_id="det-1",
        source_identity_kind="pet",
        source_identity_id="pet-candidate",
        canonical_identity_kind="pet",
        canonical_identity_id="pet-candidate",
        canonical_display_name=None,
        box_x=0,
        box_y=0,
        box_w=10,
        box_h=10,
        image_width=100,
        image_height=100,
        promotion_state="candidate",
    )

    PlaybackCoordinator._handle_face_name_existing_identity_submitted(
        coordinator,
        annotation,
        "pet:pet-existing",
    )

    coordinator._face_name_overlay.show_name_error.assert_called_once_with(
        "A pet identity cannot contain two detections from the same photo. "
        "Delete a duplicate detection instead of merging it."
    )


def test_unassigned_pet_name_dropdown_reports_same_asset_move_conflict() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._pet_service = Mock(
        move_detection_to_pet_with_outcome=Mock(
            return_value=SimpleNamespace(
                succeeded=False,
                failure=SimpleNamespace(value="same_asset_conflict"),
            )
        )
    )
    coordinator._face_name_overlay = Mock()
    annotation = RecognitionAnnotation(
        source_detection_kind="pet",
        source_annotation_id="det-unassigned",
        source_identity_kind="pet",
        source_identity_id=None,
        canonical_identity_kind="pet",
        canonical_identity_id=None,
        canonical_display_name=None,
        box_x=0,
        box_y=0,
        box_w=10,
        box_h=10,
        image_width=100,
        image_height=100,
        promotion_state="candidate",
    )

    PlaybackCoordinator._handle_face_name_existing_identity_submitted(
        coordinator,
        annotation,
        "pet:pet-existing",
    )

    coordinator._pet_service.move_detection_to_pet_with_outcome.assert_called_once_with(
        "det-unassigned",
        "pet-existing",
    )
    coordinator._face_name_overlay.show_name_error.assert_called_once_with(
        "A pet identity cannot contain two detections from the same photo. "
        "Delete a duplicate detection instead of merging it."
    )


@pytest.mark.parametrize(
    "failure",
    ["recovery_pending", "shutting_down", "rejected"],
)
def test_unassigned_pet_name_dropdown_reports_generic_move_failure(failure: str) -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._pet_service = Mock(
        move_detection_to_pet_with_outcome=Mock(
            return_value=SimpleNamespace(
                succeeded=False,
                failure=SimpleNamespace(value=failure),
            )
        )
    )
    coordinator._face_name_overlay = Mock()
    annotation = RecognitionAnnotation(
        source_detection_kind="pet",
        source_annotation_id="det-unassigned",
        source_identity_kind="pet",
        source_identity_id=None,
        canonical_identity_kind="pet",
        canonical_identity_id=None,
        canonical_display_name=None,
        box_x=0,
        box_y=0,
        box_w=10,
        box_h=10,
        image_width=100,
        image_height=100,
        promotion_state="candidate",
    )

    PlaybackCoordinator._handle_face_name_existing_identity_submitted(
        coordinator,
        annotation,
        "pet:pet-existing",
    )

    coordinator._face_name_overlay.show_name_error.assert_called_once_with(
        "The name could not be assigned. The identities may have changed."
    )


def test_handle_info_panel_pet_detection_actions_use_pet_service() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._pet_service = Mock(
        delete_detection=Mock(return_value=True),
        move_detection_to_pet_with_outcome=Mock(
            return_value=SimpleNamespace(succeeded=True, failure=None)
        ),
        move_detection_to_new_pet=Mock(return_value="pet-new"),
    )
    coordinator._current_presentation = _make_presentation(
        path="/fake/photo.jpg",
        asset_id="asset-photo",
        is_video=False,
    )
    coordinator._refresh_face_name_overlay_for_current_presentation = Mock()
    coordinator._refresh_info_panel_faces = Mock()
    coordinator._people_dashboard_refresh_callback = Mock()
    annotation = RecognitionAnnotation(
        source_detection_kind="pet",
        source_annotation_id="det-1",
        source_identity_kind="pet",
        source_identity_id="pet-a",
        canonical_identity_kind="pet",
        canonical_identity_id="pet-a",
        canonical_display_name="Miso",
        box_x=0,
        box_y=0,
        box_w=10,
        box_h=10,
        image_width=100,
        image_height=100,
    )

    PlaybackCoordinator._handle_info_panel_face_delete_requested(coordinator, annotation)
    PlaybackCoordinator._handle_info_panel_face_move_requested(
        coordinator,
        annotation,
        "pet:pet-b",
    )
    PlaybackCoordinator._handle_info_panel_face_move_to_new_person_requested(
        coordinator,
        annotation,
        "Nori",
    )

    coordinator._pet_service.delete_detection.assert_called_once_with("det-1")
    coordinator._pet_service.move_detection_to_pet_with_outcome.assert_called_once_with(
        "det-1",
        "pet-b",
    )
    coordinator._pet_service.move_detection_to_new_pet.assert_called_once_with("det-1", "Nori")
    assert coordinator._refresh_info_panel_faces.call_count == 3


def test_cross_kind_annotation_routes_identity_and_detection_mutations_separately() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._people_service = Mock(
        rename_cluster=Mock(),
        delete_face=Mock(return_value=True),
        reassign_detection_identity=Mock(return_value=True),
    )
    coordinator._pet_service = Mock(
        rename_pet=Mock(),
        delete_detection=Mock(return_value=True),
    )
    coordinator._current_presentation = _make_presentation(
        path="/fake/photo.jpg",
        asset_id="asset-photo",
        is_video=False,
    )
    coordinator._recognition_query_service = Mock()
    coordinator._refresh_face_name_overlay_for_current_presentation = Mock()
    coordinator._refresh_info_panel_faces = Mock()
    coordinator._people_dashboard_refresh_callback = Mock()
    pet_source_person_identity = RecognitionAnnotation(
        source_detection_kind="pet",
        source_annotation_id="det-1",
        source_identity_kind="pet",
        source_identity_id="pet-a",
        canonical_identity_kind="person",
        canonical_identity_id="person-a",
        canonical_display_name="Alice",
        box_x=0,
        box_y=0,
        box_w=10,
        box_h=10,
        image_width=100,
        image_height=100,
    )

    PlaybackCoordinator._handle_face_name_rename_submitted(
        coordinator,
        pet_source_person_identity.person_id,
        "Alice Updated",
    )
    PlaybackCoordinator._handle_info_panel_face_delete_requested(
        coordinator,
        pet_source_person_identity,
    )
    PlaybackCoordinator._handle_info_panel_face_move_requested(
        coordinator,
        pet_source_person_identity,
        "person:person-b",
    )

    coordinator._people_service.rename_cluster.assert_called_once_with(
        "person-a", "Alice Updated"
    )
    coordinator._pet_service.delete_detection.assert_called_once_with("det-1")
    coordinator._people_service.reassign_detection_identity.assert_called_once_with(
        source_kind="pet",
        source_annotation_id="det-1",
        target_identity="person:person-b",
    )
    coordinator._people_service.delete_face.assert_not_called()


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


def test_refresh_info_panel_batches_visible_panel_updates() -> None:
    class _FakePanelUpdate:
        def __init__(self, calls: list[str]) -> None:
            self._calls = calls

        def __enter__(self):
            self._calls.append("enter")
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            self._calls.append("exit")
            return False

    class _FakeInfoPanel:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def content_update(self):
            return _FakePanelUpdate(self.calls)

        def set_location_capability(self, **_kwargs) -> None:
            self.calls.append("location")

        def set_asset_metadata(self, _metadata) -> None:
            self.calls.append("metadata")

        def set_location_busy(self, _busy: bool) -> None:
            self.calls.append("busy")

        def set_asset_faces(self, _faces) -> None:
            self.calls.append("faces")

    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    panel = _FakeInfoPanel()
    coordinator._info_panel = panel
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

    assert panel.calls == ["enter", "location", "metadata", "busy", "faces", "exit"]


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
    coordinator._location_search_controller = Mock(reset=Mock())
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


def test_refresh_info_panel_shows_download_prompt_when_extension_search_is_unavailable() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._info_panel = Mock()
    coordinator._map_runtime = SimpleNamespace(
        capabilities=lambda: SimpleNamespace(
            display_available=True,
            location_search_available=False,
            osmand_extension_available=True,
        ),
        package_root=lambda: Path("/fake/maps"),
    )
    coordinator._location_search_controller = Mock(reset=Mock())
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


def test_ready_enrichment_is_cached_without_updating_hidden_panel() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._info_panel = Mock(isVisible=Mock(return_value=False))
    coordinator._current_presentation = _make_presentation(path="/fake/video.mp4")

    PlaybackCoordinator._handle_info_panel_metadata_ready(
        coordinator,
        InfoPanelMetadataResult(
            path=Path("/fake/video.mp4"),
            metadata={"frame_rate": 59.94, "lens": "Wide Camera"},
        ),
    )

    coordinator._info_panel.set_asset_metadata.assert_not_called()
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


def test_location_assignment_releases_current_video_source_before_write() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    video_area = Mock(
        current_source=Mock(return_value=Path("/fake/video.mp4")),
        is_playing=Mock(return_value=False),
        current_position=Mock(return_value=1234),
        stop=Mock(),
    )
    coordinator._player_view = Mock(video_area=video_area, show_placeholder=Mock())
    coordinator._player_bar = Mock(setEnabled=Mock())
    parent = Mock()
    parent.attach_mock(coordinator._player_view.show_placeholder, "show_placeholder")
    parent.attach_mock(video_area.stop, "stop")
    coordinator._location_released_video_path = None
    coordinator._location_released_video_was_playing = False
    coordinator._location_released_video_position_ms = None
    presentation = _make_presentation(path="/fake/video.mp4", is_video=True)

    PlaybackCoordinator._release_current_video_for_location_write(coordinator, presentation)

    video_area.stop.assert_called_once_with()
    coordinator._player_view.show_placeholder.assert_called_once_with(
        playback_coordinator_module._LOCATION_VIDEO_WRITE_PLACEHOLDER
    )
    assert parent.mock_calls[:2] == [
        call.show_placeholder(playback_coordinator_module._LOCATION_VIDEO_WRITE_PLACEHOLDER),
        call.stop(),
    ]
    coordinator._player_bar.setEnabled.assert_called_once_with(False)
    assert coordinator._location_released_video_path == Path("/fake/video.mp4")
    assert coordinator._location_released_video_was_playing is False
    assert coordinator._location_released_video_position_ms == 1234


def test_location_file_write_started_defers_recovered_current_video() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    asset_path = Path("/fake/video.mp4")
    presentation = _make_presentation(path=str(asset_path), is_video=True)
    coordinator._current_presentation = presentation
    coordinator._location_write_jobs_by_path = {}
    coordinator._location_video_write_inflight_paths = set()
    coordinator._release_current_video_for_location_write = Mock()

    job = LocationWriteJobRecord(
        job_id="job-1",
        asset_rel="video.mp4",
        asset_path=asset_path,
        gps={"lat": 48.137154, "lon": 11.576124},
        location="Munich",
        media_kind="video",
        status="writing",
    )

    PlaybackCoordinator._handle_location_file_write_started(coordinator, job)

    assert coordinator._location_write_jobs_by_path[asset_path] == "job-1"
    assert asset_path in coordinator._location_video_write_inflight_paths
    coordinator._release_current_video_for_location_write.assert_called_once_with(presentation)


def test_location_file_write_started_does_not_release_already_deferred_video() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    asset_path = Path("/fake/video.mp4")
    coordinator._current_presentation = _make_presentation(path=str(asset_path), is_video=True)
    coordinator._location_write_jobs_by_path = {}
    coordinator._location_video_write_inflight_paths = {asset_path}
    coordinator._release_current_video_for_location_write = Mock()

    job = LocationWriteJobRecord(
        job_id="job-1",
        asset_rel="video.mp4",
        asset_path=asset_path,
        gps={"lat": 48.137154, "lon": 11.576124},
        location="Munich",
        media_kind="video",
        status="queued",
    )

    PlaybackCoordinator._handle_location_file_write_started(coordinator, job)

    assert coordinator._location_write_jobs_by_path[asset_path] == "job-1"
    coordinator._release_current_video_for_location_write.assert_not_called()


def test_location_assignment_restore_reloads_video_when_same_asset_remains() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    presentation = _make_presentation(path="/fake/video.mp4", is_video=True)
    video_area = Mock(pause=Mock(), seek=Mock())
    coordinator._player_view = Mock(video_area=video_area)
    coordinator._router = Mock(is_detail_view_active=Mock(return_value=True))
    coordinator._current_presentation = presentation
    coordinator._location_released_video_path = Path("/fake/video.mp4")
    coordinator._location_released_video_was_playing = False
    coordinator._location_released_video_position_ms = 1234
    coordinator._render_presentation = Mock()

    PlaybackCoordinator._restore_video_released_for_location_write(coordinator)

    coordinator._render_presentation.assert_called_once_with(presentation)
    video_area.seek.assert_called_once_with(1234)
    video_area.pause.assert_called_once_with()
    assert coordinator._location_released_video_path is None
    assert coordinator._location_released_video_position_ms is None


@pytest.mark.parametrize(
    ("asset_path", "library_root", "presentation_rel", "expected_rel"),
    [
        (
            Path("/fake/library/Album/photo.jpg"),
            Path("/fake/library"),
            "photo.jpg",
            "Album/photo.jpg",
        ),
        (
            Path("/fake/library/photo.jpg"),
            Path("/fake/library"),
            "photo.jpg",
            "photo.jpg",
        ),
        (
            Path("/external/photo.jpg"),
            Path("/fake/library"),
            "photo.jpg",
            "photo.jpg",
        ),
    ],
)
def test_location_confirm_passes_library_relative_rel_to_assignment_service(
    monkeypatch: pytest.MonkeyPatch,
    asset_path: Path,
    library_root: Path,
    presentation_rel: str,
    expected_rel: str,
) -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    presentation = _make_presentation(
        path=str(asset_path),
        is_video=False,
        info_panel_visible=True,
    )
    presentation.info["rel"] = presentation_rel
    coordinator._current_presentation = presentation
    coordinator._refresh_location_extension_state = Mock(return_value=True)
    coordinator._location_assign_inflight = False
    coordinator._library_manager = None
    store = Mock(library_root=Mock(return_value=library_root))
    coordinator._asset_model = Mock(
        metadata_for_path=Mock(return_value={}),
        store=store,
    )
    coordinator._location_search_controller = Mock(reset=Mock())
    coordinator._info_panel = Mock()
    coordinator._location_write_queue = Mock(enqueue=Mock())
    coordinator._location_write_jobs_by_path = {}
    coordinator._event_bus = None
    coordinator._project_location_assignment = Mock()

    suggestion = SearchSuggestion(
        display_name="Munich",
        secondary_text="Germany",
        longitude=11.576124,
        latitude=48.137154,
        source_kind="test",
        match_kind="exact",
    )
    write_job = SimpleNamespace(job_id="job-1")
    captured_kwargs: dict[str, object] = {}

    class _FakeLocationAssignmentService:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def assign(self, **kwargs) -> SimpleNamespace:
            captured_kwargs.update(kwargs)
            return SimpleNamespace(
                asset_path=kwargs["asset_path"],
                display_name=kwargs["display_name"],
                metadata={},
                write_job=write_job,
            )

    monkeypatch.setattr(
        playback_coordinator_module,
        "IndexStoreLocationAssignmentRepository",
        lambda _root: Mock(),
    )
    monkeypatch.setattr(
        playback_coordinator_module,
        "LocationAssignmentService",
        _FakeLocationAssignmentService,
    )

    PlaybackCoordinator._handle_location_confirm_requested(
        coordinator,
        "Munich",
        suggestion,
    )

    assert captured_kwargs["asset_path"] == asset_path
    assert captured_kwargs["asset_rel"] == expected_rel
    coordinator._project_location_assignment.assert_called_once()
    coordinator._location_write_queue.enqueue.assert_called_once_with(write_job)


def test_location_confirm_updates_current_header_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    asset_path = Path("/fake/video.mp4")
    presentation = _make_presentation(
        path=str(asset_path),
        is_video=True,
        info_panel_visible=True,
    )
    presentation.info["rel"] = "video.mp4"
    coordinator._current_presentation = presentation
    coordinator._refresh_location_extension_state = Mock(return_value=True)
    coordinator._location_assign_inflight = False
    coordinator._library_manager = None
    store = Mock(library_root=Mock(return_value=Path("/fake/library")))
    coordinator._asset_model = Mock(
        metadata_for_path=Mock(return_value={"codec": "hevc"}),
        row_for_path=Mock(return_value=0),
        store=store,
    )
    coordinator._location_search_controller = Mock(reset=Mock())
    coordinator._info_panel = Mock()
    coordinator._update_header = Mock()
    coordinator._refresh_info_panel = Mock()
    coordinator._location_video_write_inflight_paths = set()
    coordinator._location_write_jobs_by_path = {}
    coordinator._location_write_queue = Mock(enqueue=Mock())
    coordinator._event_bus = None
    coordinator._location_session_invalidator = None
    coordinator._player_view = Mock(
        video_area=Mock(current_source=Mock(return_value=None), stop=Mock())
    )
    suggestion = SearchSuggestion(
        display_name="Munich",
        secondary_text="Germany",
        longitude=11.576124,
        latitude=48.137154,
        source_kind="test",
        match_kind="exact",
    )
    write_job = SimpleNamespace(job_id="job-1")

    class _FakeLocationAssignmentService:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def assign(self, **kwargs) -> SimpleNamespace:
            metadata = dict(kwargs["existing_metadata"])
            metadata.update(
                {
                    "gps": {"lat": kwargs["latitude"], "lon": kwargs["longitude"]},
                    "location": kwargs["display_name"],
                    "location_name": kwargs["display_name"],
                }
            )
            return SimpleNamespace(
                asset_path=kwargs["asset_path"],
                display_name=kwargs["display_name"],
                metadata=metadata,
                write_job=write_job,
            )

    monkeypatch.setattr(
        playback_coordinator_module,
        "IndexStoreLocationAssignmentRepository",
        lambda _root: Mock(),
    )
    monkeypatch.setattr(
        playback_coordinator_module,
        "LocationAssignmentService",
        _FakeLocationAssignmentService,
    )

    PlaybackCoordinator._handle_location_confirm_requested(
        coordinator,
        "Munich",
        suggestion,
    )

    updated = coordinator._current_presentation
    assert updated.location == "Munich"
    assert updated.info["location"] == "Munich"
    assert updated.info["gps"] == {"lat": 48.137154, "lon": 11.576124}
    coordinator._update_header.assert_called_with(updated)
    coordinator._refresh_info_panel.assert_called_once_with(updated.info)
    store.update_asset_metadata.assert_called_once_with(0, updated.info)
    coordinator._location_write_queue.enqueue.assert_called_once_with(write_job)
    assert coordinator._location_write_jobs_by_path[asset_path] == "job-1"


def test_confirmed_location_protects_repeated_stale_presentations_for_detail_session() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    asset_path = Path("/fake/video.mp4")
    coordinator._current_presentation = None
    coordinator._router = Mock(is_detail_view_active=Mock(return_value=True))
    coordinator._asset_model = Mock(set_current_row=Mock())
    coordinator.assetChanged = Mock(emit=Mock())
    coordinator._update_header = Mock()
    coordinator._select_filmstrip_row = Mock()
    coordinator._center_filmstrip_if_current = Mock()
    coordinator._player_view = Mock(show_placeholder=Mock())
    coordinator._render_presentation = Mock()
    coordinator._update_favorite_icon = Mock()
    coordinator._clear_play_profile = Mock()
    coordinator._info_panel = None
    coordinator._confirmed_location_metadata = {
        asset_path: {"location": "Munich"}
    }

    stale_presentation = replace(
        _make_presentation(path=str(asset_path), is_video=True),
        location="Paris",
    )
    with patch.object(
        playback_coordinator_module.QTimer,
        "singleShot",
        side_effect=lambda _delay, callback: callback(),
    ):
        PlaybackCoordinator._handle_presentation_changed(coordinator, stale_presentation)
        PlaybackCoordinator._handle_presentation_changed(coordinator, stale_presentation)

    assert coordinator._current_presentation.location == "Munich"
    assert coordinator._current_presentation.info["location"] == "Munich"
    assert coordinator._confirmed_location_metadata[asset_path]["location"] == "Munich"


def test_confirmed_location_does_not_apply_to_another_asset() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._current_presentation = None
    coordinator._router = Mock(is_detail_view_active=Mock(return_value=True))
    coordinator._asset_model = Mock(set_current_row=Mock())
    coordinator.assetChanged = Mock(emit=Mock())
    coordinator._update_header = Mock()
    coordinator._select_filmstrip_row = Mock()
    coordinator._center_filmstrip_if_current = Mock()
    coordinator._player_view = Mock(show_placeholder=Mock())
    coordinator._render_presentation = Mock()
    coordinator._clear_play_profile = Mock()
    coordinator._confirmed_location_metadata = {
        Path("/fake/video.mp4"): {"location": "Munich"}
    }
    other_presentation = replace(
        _make_presentation(path="/fake/other-video.mp4", is_video=True),
        location=None,
    )

    with patch.object(
        playback_coordinator_module.QTimer,
        "singleShot",
        side_effect=lambda _delay, callback: callback(),
    ):
        PlaybackCoordinator._handle_presentation_changed(coordinator, other_presentation)

    assert coordinator._current_presentation.location is None
    assert "location" not in coordinator._current_presentation.info


def test_location_file_write_finished_restores_released_current_video() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    asset_path = Path("/fake/video.mp4")
    presentation = _make_presentation(path=str(asset_path), is_video=True)
    video_area = Mock(pause=Mock(), seek=Mock())
    coordinator._player_view = Mock(video_area=video_area)
    coordinator._router = Mock(is_detail_view_active=Mock(return_value=True))
    coordinator._current_presentation = presentation
    coordinator._render_presentation = Mock()
    coordinator._location_video_write_inflight_paths = {asset_path}
    coordinator._location_released_video_path = asset_path
    coordinator._location_released_video_was_playing = False
    coordinator._location_released_video_position_ms = 1234
    coordinator._location_write_jobs_by_path = {asset_path: "job-1"}

    result = LocationFileWriteResult(
        job_id="job-1",
        asset_path=asset_path,
        gps={"lat": 48.137154, "lon": 11.576124},
        location="Munich",
    )

    PlaybackCoordinator._handle_location_file_write_verified(coordinator, result)

    assert coordinator._location_video_write_inflight_paths == set()
    assert coordinator._location_write_jobs_by_path == {}
    coordinator._render_presentation.assert_called_once_with(presentation)
    video_area.seek.assert_called_once_with(1234)
    video_area.pause.assert_called_once_with()


def test_location_file_write_finished_renders_current_video_when_user_returned() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    asset_path = Path("/fake/video.mp4")
    presentation = _make_presentation(path=str(asset_path), is_video=True)
    coordinator._router = Mock(is_detail_view_active=Mock(return_value=True))
    coordinator._current_presentation = presentation
    coordinator._render_presentation = Mock()
    coordinator._location_video_write_inflight_paths = {asset_path}
    coordinator._location_released_video_path = None
    coordinator._location_write_jobs_by_path = {asset_path: "job-1"}

    result = LocationFileWriteResult(
        job_id="job-1",
        asset_path=asset_path,
        gps={"lat": 48.137154, "lon": 11.576124},
        location="Munich",
    )

    PlaybackCoordinator._handle_location_file_write_verified(coordinator, result)

    assert coordinator._location_video_write_inflight_paths == set()
    assert coordinator._location_write_jobs_by_path == {}
    coordinator._render_presentation.assert_called_once_with(presentation)


def test_location_file_write_error_warns_and_allows_video_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    asset_path = Path("/fake/video.mp4")
    popup_parent = Mock()
    coordinator._info_panel = Mock(parentWidget=Mock(return_value=popup_parent))
    coordinator._router = Mock(is_detail_view_active=Mock(return_value=True))
    coordinator._current_presentation = _make_presentation(path=str(asset_path), is_video=True)
    coordinator._render_presentation = Mock()
    coordinator._location_video_write_inflight_paths = {asset_path}
    coordinator._location_released_video_path = None
    coordinator._location_write_jobs_by_path = {asset_path: "job-1"}
    show_warning = Mock()
    monkeypatch.setattr(playback_coordinator_module.dialogs, "show_warning", show_warning)
    coordinator._queue_location_file_write_warning = Mock(
        side_effect=lambda message: PlaybackCoordinator._show_location_file_write_warning(
            coordinator,
            message,
        )
    )

    result = LocationFileWriteResult(
        job_id="job-1",
        asset_path=asset_path,
        gps={"lat": 48.137154, "lon": 11.576124},
        location="Munich",
        error="permission denied",
    )

    PlaybackCoordinator._handle_location_file_write_failed(coordinator, result)

    assert coordinator._location_video_write_inflight_paths == set()
    assert coordinator._location_write_jobs_by_path == {}
    coordinator._queue_location_file_write_warning.assert_called_once_with("permission denied")
    coordinator._render_presentation.assert_called_once_with(coordinator._current_presentation)
    show_warning.assert_called_once_with(
        popup_parent,
        playback_coordinator_module._LOCATION_FILE_WRITE_LIMITED_MESSAGE_TEMPLATE.format(
            reason="permission denied"
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


def test_handle_manual_face_submitted_defers_pet_identity_to_merge() -> None:
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
                "name": "Miso",
                "identity_key": "pet:pet-a",
                "person_id": None,
            },
        )

    assert coordinator._manual_face_pending_merge_target == "pet:pet-a"
    worker_cls.assert_called_once()
    assert worker_cls.call_args.kwargs["person_id"] is None
    assert worker_cls.call_args.kwargs["name_or_none"] == "Miso"
    fake_pool.start.assert_called_once_with(fake_worker, -1)


def test_handle_manual_face_ready_merges_selected_pet_identity() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._manual_face_inflight_asset_id = "asset-photo"
    coordinator._manual_face_pending_merge_target = "pet:pet-a"
    coordinator._pending_manual_face_annotations = {"asset-photo": []}
    coordinator._people_service = Mock(
        list_clusters=Mock(
            return_value=[SimpleNamespace(person_id="person-new", is_hidden=False)]
        ),
        cluster_asset_ids=Mock(return_value=["asset-photo"]),
        merge_identities=Mock(return_value=SimpleNamespace(merged=True, group_redirects={})),
    )
    coordinator._pet_service = Mock(
        list_pets=Mock(return_value=[SimpleNamespace(pet_id="pet-a", is_hidden=False)]),
        pet_asset_ids=Mock(return_value=[]),
    )
    coordinator._recognition_merge_service = Mock(
        merge=Mock(return_value=SimpleNamespace(merged=True, group_redirects={}))
    )
    coordinator._current_presentation = _make_presentation(
        path="/fake/photo.jpg",
        asset_id="asset-photo",
        is_video=False,
    )
    coordinator._refresh_face_name_overlay_for_current_presentation = Mock()
    coordinator._refresh_info_panel_faces = Mock()
    coordinator._people_dashboard_refresh_callback = Mock()

    PlaybackCoordinator._handle_manual_face_ready(
        coordinator,
        ManualFaceAddResult(
            asset_id="asset-photo",
            face_id="face-manual",
            person_id="person-new",
            created_new_person=True,
        ),
    )

    coordinator._recognition_merge_service.merge.assert_called_once_with(
        "person:person-new",
        "pet:pet-a",
    )
    coordinator._refresh_face_name_overlay_for_current_presentation.assert_called_once_with()
    coordinator._refresh_info_panel_faces.assert_called_once_with("asset-photo")
    coordinator._people_dashboard_refresh_callback.assert_called_once_with()


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

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

from iPhoto.application.ports import EditRenderingState
from iPhoto.application.dtos import AssetDTO
from iPhoto.gui.ui.media.media_restore_request import MediaRestoreRequest
from iPhoto.gui.viewmodels.detail_viewmodel import DetailViewModel


def _make_dto(path: str, *, is_video: bool = False, is_favorite: bool = False) -> AssetDTO:
    return AssetDTO(
        id=path,
        abs_path=Path(path),
        rel_path=Path(Path(path).name),
        media_type="video" if is_video else "image",
        created_at=None,
        width=100,
        height=100,
        duration=5.0 if is_video else 0.0,
        size_bytes=100,
        metadata={},
        is_favorite=is_favorite,
    )


def _make_vm(*, edit_service=None):
    store = Mock()
    session = Mock()
    asset_service = Mock()
    vm = DetailViewModel(
        collection_store=store,
        media_session=session,
        asset_service=asset_service,
        adjustment_commit_port=None,
        edit_service_getter=(lambda: edit_service) if edit_service is not None else None,
    )
    return vm, store, session, asset_service


def test_show_row_builds_presentation_and_requests_detail_route():
    vm, store, session, _ = _make_vm()
    dto = _make_dto("/tmp/photo.jpg", is_favorite=True)
    store.asset_at.return_value = dto
    session.set_current_row.return_value = dto.abs_path

    requested = []
    received = []
    vm.route_requested.connect(requested.append)
    vm.presentation_changed.connect(received.append)

    vm.show_row(0)

    assert vm.current_row.value == 0
    assert vm.current_path.value == dto.abs_path
    assert requested == ["detail"]
    assert received[0].asset_id == dto.id
    assert received[0].path == dto.abs_path
    assert received[0].is_favorite is True


def test_next_and_previous_delegate_to_session():
    vm, store, session, _ = _make_vm()
    dto = _make_dto("/tmp/photo.jpg")
    store.asset_at.return_value = dto
    session.set_current_row.return_value = dto.abs_path
    session.next_row.return_value = 3
    session.previous_row.return_value = 1

    vm.next()
    session.set_current_row.assert_called_with(3)

    session.set_current_row.reset_mock()
    vm.previous()
    session.set_current_row.assert_called_with(1)


def test_toggle_favorite_updates_store_and_presentation():
    vm, store, session, asset_service = _make_vm()
    dto = _make_dto("/tmp/photo.jpg")
    store.asset_at.return_value = dto
    session.set_current_row.return_value = dto.abs_path
    vm.show_row(0)
    asset_service.toggle_favorite_by_path.return_value = True

    vm.toggle_favorite()

    asset_service.toggle_favorite_by_path.assert_called_once_with(dto.abs_path)
    store.update_favorite_status.assert_called_once_with(0, True)


def test_toggle_favorite_uses_visible_asset_path_not_playback_source():
    vm, store, session, asset_service = _make_vm()
    dto = _make_dto("/tmp/photo.jpg")
    store.asset_at.return_value = dto
    session.set_current_row.return_value = Path("/tmp/photo.mov")
    vm.show_row(0)
    asset_service.toggle_favorite_by_path.return_value = True

    vm.toggle_favorite()

    asset_service.toggle_favorite_by_path.assert_called_once_with(dto.abs_path)
    store.update_favorite_status.assert_called_once_with(0, True)


def test_toggle_info_flips_presentation_flag():
    vm, store, session, _ = _make_vm()
    dto = _make_dto("/tmp/photo.jpg")
    store.asset_at.return_value = dto
    session.set_current_row.return_value = dto.abs_path
    vm.show_row(0)

    vm.toggle_info()
    assert vm.presentation.value.info_panel_visible is True
    vm.toggle_info()
    assert vm.presentation.value.info_panel_visible is False


def test_request_edit_emits_current_path():
    vm, store, session, _ = _make_vm()
    dto = _make_dto("/tmp/photo.jpg")
    store.asset_at.return_value = dto
    session.set_current_row.return_value = dto.abs_path
    vm.show_row(0)

    emitted = []
    vm.edit_requested.connect(emitted.append)
    vm.request_edit()

    assert emitted == [dto.abs_path]
    assert vm.current_asset_path() == dto.abs_path


def test_back_to_gallery_clears_info_panel_state_for_next_detail_entry():
    vm, store, session, _ = _make_vm()
    dto = _make_dto("/tmp/photo.jpg")
    store.asset_at.return_value = dto
    session.set_current_row.return_value = dto.abs_path

    requested = []
    vm.route_requested.connect(requested.append)

    vm.show_row(0)
    vm.toggle_info()

    assert vm.presentation.value.info_panel_visible is True

    vm.back_to_gallery()
    vm.show_row(0)

    assert requested == ["detail", "gallery", "detail"]
    assert vm.presentation.value.info_panel_visible is False


def test_request_edit_clears_info_panel_state_before_returning_to_detail():
    vm, store, session, _ = _make_vm()
    dto = _make_dto("/tmp/photo.jpg")
    store.asset_at.return_value = dto
    session.set_current_row.return_value = dto.abs_path

    emitted = []
    vm.edit_requested.connect(emitted.append)

    vm.show_row(0)
    vm.toggle_info()

    assert vm.presentation.value.info_panel_visible is True

    vm.request_edit()
    vm.show_row(0)

    assert emitted == [dto.abs_path]
    assert vm.presentation.value.info_panel_visible is False


def test_restore_after_adjustment_rebinds_current_path():
    vm, store, session, _ = _make_vm()
    dto = _make_dto("/tmp/photo.jpg")
    store.asset_at.return_value = dto
    session.set_current_row.return_value = dto.abs_path
    session.set_current_by_path.return_value = True
    session.current_row.return_value = 0

    received = []
    vm.presentation_changed.connect(received.append)
    vm.restore_after_adjustment(dto.abs_path, "edit_done")

    session.set_current_by_path.assert_called_once_with(dto.abs_path)
    assert received[0].path == dto.abs_path
    assert received[0].reload_token == 1


def test_show_row_builds_video_state_from_sidecar():
    edit_service = Mock()
    edit_service.describe_adjustments.return_value = EditRenderingState(
        sidecar_exists=True,
        raw_adjustments={"Exposure": 0.2},
        resolved_adjustments={"Exposure": 0.3},
        adjusted_preview=True,
        has_visible_edits=True,
        trim_range_ms=(1000, 4000),
        effective_duration_sec=3.0,
    )
    vm, store, session, _ = _make_vm(edit_service=edit_service)
    dto = _make_dto("/tmp/video.mp4", is_video=True)
    store.asset_at.return_value = dto
    session.set_current_row.return_value = dto.abs_path

    vm.show_row(0)

    presentation = vm.presentation.value
    assert presentation.video_adjusted_preview is True
    assert presentation.video_adjustments == {"Exposure": 0.3}
    assert presentation.video_trim_range_ms == (1000, 4000)


def test_restore_request_prefers_duration_hint_for_video_trim():
    edit_service = Mock()
    edit_service.describe_adjustments.return_value = EditRenderingState(
        sidecar_exists=True,
        raw_adjustments={},
        resolved_adjustments={},
        adjusted_preview=False,
        has_visible_edits=True,
        trim_range_ms=(2000, 7250),
        effective_duration_sec=5.25,
    )
    vm, store, session, _ = _make_vm(edit_service=edit_service)
    dto = _make_dto("/tmp/video.mp4", is_video=True)
    store.asset_at.return_value = dto
    session.current_row.return_value = 0
    session.set_current_by_path.return_value = True
    session.set_current_row.return_value = dto.abs_path

    vm._handle_restore_requested(
        MediaRestoreRequest(
            path=dto.abs_path,
            reason="edit_done",
            duration_sec=7.25,
        )
    )

    presentation = vm.presentation.value
    assert presentation.video_trim_range_ms == (2000, 7250)
    assert presentation.reload_token == 1


def test_store_row_change_refreshes_current_presentation():
    vm, store, session, _ = _make_vm()
    first = _make_dto("/tmp/photo.jpg", is_favorite=False)
    updated = _make_dto("/tmp/photo.jpg", is_favorite=True)
    store.asset_at.side_effect = [first, updated]
    session.set_current_row.return_value = first.abs_path

    vm.show_row(0)
    vm._handle_row_changed(0)

    assert vm.presentation.value.is_favorite is True

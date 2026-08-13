from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

from iPhoto.application.dtos import AssetDTO
from iPhoto.application.ports import EditRenderingState
from iPhoto.gui.ui.media.media_restore_request import MediaRestoreRequest
from iPhoto.gui.ui.media.media_selection_session import MediaSelectionSession
from iPhoto.gui.viewmodels.detail_viewmodel import DetailViewModel
from iPhoto.gui.viewmodels.signal import Signal

_UNSET = object()


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


def _make_vm(*, edit_service=None, asset_state_service=_UNSET):
    store = Mock()
    session = Mock()
    if asset_state_service is _UNSET:
        asset_state_service = Mock()
    vm = DetailViewModel(
        collection_store=store,
        media_session=session,
        asset_state_service=asset_state_service,
        adjustment_commit_port=None,
        edit_service_getter=(lambda: edit_service) if edit_service is not None else None,
    )
    return vm, store, session, asset_state_service


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


def test_presentation_carries_indexed_source_identity() -> None:
    vm, store, session, _ = _make_vm()
    dto = _make_dto("/tmp/photo.jpg")
    dto.width = 6000
    dto.height = 4000
    dto.size_bytes = 123
    dto.metadata.update({"source_mtime_ns": 456, "index_revision": 9})
    store.asset_at.return_value = dto
    session.set_current_row.return_value = dto.abs_path

    vm.show_row(0)

    identity = vm.presentation.value.source_identity
    assert identity is not None
    assert identity.revision == ("mtime", 123, 456)
    assert (identity.width, identity.height) == (6000, 4000)


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


class _LazyCollection:
    def __init__(self) -> None:
        self.data_changed = Signal()
        self.row_changed = Signal()
        self._dtos = [
            _make_dto("/tmp/visible.jpg"),
            _make_dto("/tmp/deep.jpg"),
        ]
        self._loaded_rows = {0}

    def count(self) -> int:
        return len(self._dtos)

    def asset_at(self, row: int):
        if row not in self._loaded_rows:
            return None
        return self._dtos[row]

    def ensure_row_loaded(self, row: int, *, emit_signals: bool = True) -> bool:
        del emit_signals
        if 0 <= row < len(self._dtos):
            self._loaded_rows.add(row)
            return True
        return False

    def pin_row(self, row: int) -> None:
        self.ensure_row_loaded(row)

    def row_for_path(self, path: Path) -> int | None:
        for row, dto in enumerate(self._dtos):
            if dto.abs_path == path:
                return row
        return None


class _AsyncLazyCollection(_LazyCollection):
    def __init__(self) -> None:
        super().__init__()
        self.row_loaded = Signal()

    def ensure_row_loaded(self, row: int, *, emit_signals: bool = True) -> bool:
        del emit_signals
        return row in self._loaded_rows

    def complete_row_load(self, row: int) -> None:
        self._loaded_rows.add(row)
        self.row_loaded.emit(row)


def test_next_can_open_row_outside_current_store_window() -> None:
    store = _LazyCollection()
    session = MediaSelectionSession()
    session.bind_collection(store)
    vm = DetailViewModel(
        collection_store=store,
        media_session=session,
        asset_state_service=Mock(),
        adjustment_commit_port=None,
        edit_service_getter=None,
    )

    vm.show_row(0)
    vm.next()

    assert vm.current_row.value == 1
    assert vm.current_path.value == Path("/tmp/deep.jpg")
    assert vm.presentation.value.path == Path("/tmp/deep.jpg")


def test_show_row_retries_after_async_placeholder_load() -> None:
    store = _AsyncLazyCollection()
    session = MediaSelectionSession()
    session.bind_collection(store)
    vm = DetailViewModel(
        collection_store=store,
        media_session=session,
        asset_state_service=Mock(),
        adjustment_commit_port=None,
        edit_service_getter=None,
    )
    requested = []
    vm.route_requested.connect(requested.append)

    vm.show_row(1)

    assert vm.current_row.value == -1
    assert requested == []

    store.complete_row_load(1)

    assert vm.current_row.value == 1
    assert vm.current_path.value == Path("/tmp/deep.jpg")
    assert requested == ["detail"]


def test_toggle_favorite_updates_store_and_presentation():
    vm, store, session, asset_state_service = _make_vm()
    dto = _make_dto("/tmp/photo.jpg")
    store.asset_at.return_value = dto
    session.set_current_row.return_value = dto.abs_path
    vm.show_row(0)
    asset_state_service.toggle_favorite.return_value = True

    vm.toggle_favorite()

    asset_state_service.toggle_favorite.assert_called_once_with(dto.abs_path)
    store.update_favorite_status.assert_called_once_with(0, True)


def test_toggle_favorite_uses_visible_asset_path_not_playback_source():
    vm, store, session, asset_state_service = _make_vm()
    dto = _make_dto("/tmp/photo.jpg")
    store.asset_at.return_value = dto
    session.set_current_row.return_value = Path("/tmp/photo.mov")
    vm.show_row(0)
    asset_state_service.toggle_favorite.return_value = True

    vm.toggle_favorite()

    asset_state_service.toggle_favorite.assert_called_once_with(dto.abs_path)
    store.update_favorite_status.assert_called_once_with(0, True)


def test_show_row_disables_favorite_action_without_asset_state_service():
    vm, store, session, _ = _make_vm(asset_state_service=None)
    dto = _make_dto("/tmp/photo.jpg")
    store.asset_at.return_value = dto
    session.set_current_row.return_value = dto.abs_path

    vm.show_row(0)

    assert vm.presentation.value.can_toggle_favorite is False


def test_binding_asset_state_service_refreshes_favorite_action_state():
    vm, store, session, _ = _make_vm(asset_state_service=None)
    dto = _make_dto("/tmp/photo.jpg")
    store.asset_at.return_value = dto
    session.set_current_row.return_value = dto.abs_path

    vm.show_row(0)
    assert vm.presentation.value.can_toggle_favorite is False

    asset_state_service = Mock()
    vm.bind_asset_state_service(asset_state_service)
    assert vm.presentation.value.can_toggle_favorite is True

    vm.bind_asset_state_service(None)
    assert vm.presentation.value.can_toggle_favorite is False


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


def test_rotate_commit_does_not_reload_current_detail() -> None:
    vm = DetailViewModel.__new__(DetailViewModel)
    vm.restore_after_adjustment = Mock()

    DetailViewModel._handle_adjustments_committed(
        vm,
        Path("/fake/photo.jpg"),
        "rotate",
    )

    vm.restore_after_adjustment.assert_not_called()


def test_show_row_defers_video_state_sidecar_read():
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
    assert presentation.video_adjusted_preview is False
    assert presentation.video_adjustments is None
    assert presentation.video_trim_range_ms is None
    edit_service.describe_adjustments.assert_not_called()


def test_restore_request_keeps_video_sidecar_out_of_route_path():
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
    assert presentation.video_trim_range_ms is None
    assert presentation.reload_token == 1
    assert presentation.video_duration_hint == 7.25
    edit_service.describe_adjustments.assert_not_called()


def test_store_row_change_refreshes_current_presentation():
    vm, store, session, _ = _make_vm()
    first = _make_dto("/tmp/photo.jpg", is_favorite=False)
    updated = _make_dto("/tmp/photo.jpg", is_favorite=True)
    store.asset_at.side_effect = [first, updated]
    session.set_current_row.return_value = first.abs_path

    vm.show_row(0)
    vm._handle_row_changed(0)

    assert vm.presentation.value.is_favorite is True


def test_scan_row_relocation_keeps_render_generation() -> None:
    vm, store, session, _ = _make_vm()
    first = _make_dto("/tmp/photo.jpg", is_favorite=False)
    relocated = _make_dto("/tmp/photo.jpg", is_favorite=True)
    first.metadata["source_mtime_ns"] = 100
    relocated.metadata["source_mtime_ns"] = 100
    store.asset_at.side_effect = [first, relocated]
    session.set_current_row.return_value = first.abs_path
    session.current_row.return_value = 7
    session.current_source.return_value = first.abs_path

    vm.show_row(0)
    initial = vm.presentation.value
    vm._handle_store_changed()
    refreshed = vm.presentation.value

    assert initial.render_key == refreshed.render_key
    assert refreshed.row == 7
    assert refreshed.is_favorite is True
    assert refreshed.request_generation == initial.request_generation


def test_source_revision_refresh_allocates_new_render_generation() -> None:
    vm, store, session, _ = _make_vm()
    first = _make_dto("/tmp/photo.jpg")
    revised = _make_dto("/tmp/photo.jpg")
    first.metadata["source_mtime_ns"] = 100
    revised.metadata["source_mtime_ns"] = 200
    store.asset_at.side_effect = [first, revised]
    session.set_current_row.return_value = first.abs_path
    session.current_row.return_value = 0
    session.current_source.return_value = first.abs_path

    vm.show_row(0)
    initial = vm.presentation.value
    vm._handle_store_changed()
    refreshed = vm.presentation.value

    assert initial.render_key != refreshed.render_key
    assert refreshed.request_generation == initial.request_generation + 1

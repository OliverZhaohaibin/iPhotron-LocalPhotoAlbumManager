from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

from iPhoto.application.dtos import AssetDTO
from iPhoto.application.ports import EditRenderingState
from iPhoto.gui.ui.media.media_restore_request import MediaRestoreRequest
from iPhoto.gui.ui.media.media_selection_session import (
    MediaSelectionChangeReason,
    MediaSelectionSession,
    MediaSelectionSnapshot,
    MediaSelectionState,
)
from iPhoto.gui.viewmodels.detail_viewmodel import DetailPresentation, DetailViewModel
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


class _AnchoredLazyCollection(_LazyCollection):
    def __init__(self) -> None:
        super().__init__()
        self.row_loaded = Signal()
        self.anchor_path: Path | None = None
        self.anchor_status: str | None = None
        self.pin_calls: list[tuple[Path, str, int | None]] = []
        self.favorite_updates: list[tuple[int, bool]] = []

    def cached_row_for_path(self, path: Path) -> int | None:
        for row in self._loaded_rows:
            if self._dtos[row].abs_path == path:
                return row
        return None

    def selection_anchor_status(self, path: Path) -> str | None:
        return self.anchor_status if path == self.anchor_path else None

    def pin_path(
        self,
        path: Path,
        *,
        asset_id: str = "",
        previous_row: int | None = None,
    ) -> None:
        self.pin_calls.append((path, asset_id, previous_row))
        self.anchor_path = path
        self.anchor_status = "resolved"

    def update_favorite_status(self, row: int, value: bool) -> None:
        self._dtos[row].is_favorite = value
        self.favorite_updates.append((row, value))
        self.row_changed.emit(row)


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


def test_show_row_hot_path_pins_once_and_never_looks_up_path() -> None:
    store = _AnchoredLazyCollection()
    session = MediaSelectionSession()
    session.bind_collection(store)
    store.pin_calls.clear()
    store.row_for_path = Mock(side_effect=AssertionError("hot path path lookup"))
    vm = DetailViewModel(
        collection_store=store,
        media_session=session,
        asset_state_service=Mock(),
        adjustment_commit_port=None,
        edit_service_getter=None,
    )

    vm.show_row(0)

    dto = store.asset_at(0)
    assert dto is not None
    assert store.pin_calls == [(dto.abs_path, str(dto.id), 0)]
    store.row_for_path.assert_not_called()


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
    assert vm.presentation.value.request_generation == 1


def test_async_fallback_resolution_atomically_converges_detail_and_actions() -> None:
    store = _AnchoredLazyCollection()
    session = MediaSelectionSession()
    session.bind_collection(store)
    asset_state_service = Mock()
    asset_state_service.toggle_favorite.return_value = True
    vm = DetailViewModel(
        collection_store=store,
        media_session=session,
        asset_state_service=asset_state_service,
        adjustment_commit_port=None,
        edit_service_getter=None,
    )
    edits: list[Path] = []
    rotations: list[tuple[Path, bool]] = []
    vm.edit_requested.connect(edits.append)
    vm.rotate_requested.connect(lambda path, is_video: rotations.append((path, is_video)))

    vm.show_row(1)
    original = vm.presentation.value
    assert original.path == Path("/tmp/deep.jpg")

    replacement = _make_dto("/tmp/replacement.jpg")
    store._dtos = [_make_dto("/tmp/visible.jpg"), replacement]
    store._loaded_rows = {0}
    store.anchor_status = "missing"
    store.data_changed.emit()

    assert session.selection_state() is MediaSelectionState.FALLBACK_PENDING
    assert vm.selection_state.value is MediaSelectionState.FALLBACK_PENDING
    assert vm.current_row.value == -1
    assert vm.current_path.value == original.path
    assert vm.presentation.value.path == original.path
    assert vm.presentation.value.request_generation == original.request_generation

    vm.toggle_favorite()
    vm.request_edit()
    vm.rotate_current()
    assert vm.current_asset_path() is None
    asset_state_service.toggle_favorite.assert_not_called()
    assert edits == []
    assert rotations == []

    store._loaded_rows.add(1)
    store.row_loaded.emit(1)

    snapshot = session.selection_snapshot()
    assert snapshot.state is MediaSelectionState.RESOLVED
    assert snapshot.row == 1
    assert snapshot.path == replacement.abs_path
    assert snapshot.asset_id == str(replacement.id)
    assert vm.current_row.value == 1
    assert vm.current_path.value == replacement.abs_path
    assert vm.presentation.value.path == replacement.abs_path
    assert vm.presentation.value.request_generation == original.request_generation + 1

    vm.toggle_favorite()
    vm.request_edit()
    vm.rotate_current()
    assert vm.current_asset_path() == replacement.abs_path
    asset_state_service.toggle_favorite.assert_called_once_with(replacement.abs_path)
    assert store.favorite_updates == [(1, True)]
    assert edits == [replacement.abs_path]
    assert rotations == [(replacement.abs_path, False)]


def test_pending_selection_anchor_keeps_existing_detail_presentation() -> None:
    store = _AnchoredLazyCollection()
    session = MediaSelectionSession()
    session.bind_collection(store)
    vm = DetailViewModel(
        collection_store=store,
        media_session=session,
        asset_state_service=Mock(),
        adjustment_commit_port=None,
        edit_service_getter=None,
    )
    changes = []
    vm.presentation_changed.connect(changes.append)
    vm.show_row(0)
    original = vm.presentation.value
    assert original is not None

    store.anchor_status = "retry"
    store.data_changed.emit()

    assert session.current_row() == -1
    assert session.current_source() == Path("/tmp/visible.jpg")
    assert vm.current_row.value == -1
    assert vm.selection_state.value is MediaSelectionState.ANCHOR_RESOLVING
    pending = vm.presentation.value
    assert pending is not original
    assert pending.path == original.path
    assert pending.row == -1
    assert pending.can_toggle_favorite is False
    assert pending.render_key == original.render_key
    assert changes == [original, pending]

    store.anchor_status = "resolved"
    store.data_changed.emit()

    assert session.current_row() == 0
    assert vm.current_row.value == 0
    assert vm.selection_state.value is MediaSelectionState.RESOLVED
    assert vm.presentation.value.request_generation == original.request_generation
    assert vm.presentation.value.render_key == original.render_key


def test_pending_still_can_restart_render_from_stable_identity() -> None:
    store = _AnchoredLazyCollection()
    session = MediaSelectionSession()
    session.bind_collection(store)
    vm = DetailViewModel(
        collection_store=store,
        media_session=session,
        asset_state_service=Mock(),
        adjustment_commit_port=None,
        edit_service_getter=None,
    )
    changes: list[DetailPresentation] = []
    vm.presentation_changed.connect(changes.append)
    vm.show_row(0)
    original = vm.presentation.value
    assert original is not None

    store.anchor_status = "retry"
    store.data_changed.emit()
    pending = vm.presentation.value
    assert pending is not None
    assert pending.row == -1

    assert vm.recover_current_presentation() is True

    recovered = vm.presentation.value
    assert recovered.path == original.path
    assert recovered.asset_id == original.asset_id
    assert recovered.row == -1
    assert recovered.render_key == original.render_key
    assert recovered.request_generation == original.request_generation + 1
    assert changes[-1] == recovered


def test_retry_favorite_never_targets_the_asset_at_the_stale_row() -> None:
    store = _AnchoredLazyCollection()
    session = MediaSelectionSession()
    session.bind_collection(store)
    asset_state_service = Mock()
    vm = DetailViewModel(
        collection_store=store,
        media_session=session,
        asset_state_service=asset_state_service,
        adjustment_commit_port=None,
        edit_service_getter=None,
    )
    vm.show_row(0)
    visible = vm.presentation.value
    assert visible is not None

    wrong = _make_dto("/tmp/wrong.jpg")
    store._dtos = [wrong, store._dtos[0]]
    store._loaded_rows = {0, 1}
    store.anchor_status = "retry"
    store.data_changed.emit()

    assert store.asset_at(0).abs_path == wrong.abs_path
    assert vm.presentation.value.path == visible.path
    assert vm.current_row.value == -1
    vm.toggle_favorite()

    asset_state_service.toggle_favorite.assert_not_called()

    store.anchor_status = "resolved"
    store.data_changed.emit()

    assert vm.current_row.value == 1
    assert vm.presentation.value.path == visible.path
    assert vm.presentation.value.can_toggle_favorite is True


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


def test_user_dismissal_publishes_hidden_state_and_one_toggle_reopens_panel():
    vm, store, session, _ = _make_vm()
    dto = _make_dto("/tmp/photo.jpg")
    store.asset_at.return_value = dto
    session.set_current_row.return_value = dto.abs_path
    vm.show_row(0)
    vm.toggle_info()
    assert vm.presentation.value.info_panel_visible is True

    vm.hide_info_panel(refresh_presentation=True)

    assert vm._info_panel_visible is False
    assert vm.presentation.value.info_panel_visible is False

    vm.toggle_info()

    assert vm._info_panel_visible is True
    assert vm.presentation.value.info_panel_visible is True


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

    vm.show_row(0)
    initial = vm.presentation.value
    vm._handle_selection_changed(
        MediaSelectionSnapshot(
            version=2,
            state=MediaSelectionState.RESOLVED,
            row=7,
            path=relocated.abs_path,
            asset_id=str(relocated.id),
        ),
        MediaSelectionChangeReason.ANCHOR_RESOLVED,
    )
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

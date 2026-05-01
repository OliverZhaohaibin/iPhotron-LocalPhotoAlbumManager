from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from iPhoto.gui.coordinators.main_coordinator import MainCoordinator
from iPhoto.people.service import PeopleService


def test_on_library_tree_updated_rebinds_asset_list_vm_and_reloads_selection() -> None:
    coordinator = MainCoordinator.__new__(MainCoordinator)
    root = Path("/library")

    coordinator._context = MagicMock()
    coordinator._context.library.root.return_value = root
    coordinator._context.library.state_repository = MagicMock()
    coordinator._context.library.asset_query_service = MagicMock()
    coordinator._context.asset_runtime.repository = MagicMock()
    coordinator._context.asset_runtime.bind_library_root = MagicMock()
    coordinator._asset_service = MagicMock()
    coordinator._asset_list_vm = MagicMock()
    coordinator._gallery_vm = MagicMock()
    coordinator._logger = MagicMock()
    coordinator._playback = MagicMock()
    coordinator._window = MagicMock(ui=MagicMock(people_page=MagicMock()))

    coordinator._on_library_tree_updated()

    coordinator._context.asset_runtime.bind_library_root.assert_called_once_with(root)
    coordinator._asset_service.set_repository.assert_called_once_with(
        coordinator._context.asset_runtime.repository
    )
    coordinator._asset_service.bind_library_surfaces.assert_called_once_with(
        library_root=root,
        state_repository=coordinator._context.library.state_repository,
        favorite_query=coordinator._context.library.asset_query_service,
    )
    coordinator._asset_list_vm.rebind_asset_query_service.assert_called_once_with(
        coordinator._context.library.asset_query_service,
        root,
    )
    coordinator._gallery_vm.on_library_tree_updated.assert_called_once_with()
    coordinator._playback.set_map_runtime.assert_called_once_with(
        coordinator._context.library.map_runtime
    )
    coordinator._playback.set_people_library_root.assert_called_once_with(root)
    coordinator._window.ui.map_view.set_map_runtime.assert_called_once_with(
        coordinator._context.library.map_runtime
    )
    coordinator._window.ui.info_panel.set_map_runtime.assert_called_once_with(
        coordinator._context.library.map_runtime
    )


def test_on_library_tree_updated_skips_selection_reload_in_location_context() -> None:
    coordinator = MainCoordinator.__new__(MainCoordinator)
    root = Path("/library")

    coordinator._context = MagicMock()
    coordinator._context.library.root.return_value = root
    coordinator._context.asset_runtime.repository = MagicMock()
    coordinator._context.asset_runtime.bind_library_root = MagicMock()
    coordinator._asset_service = MagicMock()
    coordinator._asset_list_vm = MagicMock()
    coordinator._gallery_vm = MagicMock()
    coordinator._logger = MagicMock()
    coordinator._playback = MagicMock()
    coordinator._window = MagicMock(ui=MagicMock(people_page=MagicMock()))

    coordinator._on_library_tree_updated()

    coordinator._asset_list_vm.rebind_asset_query_service.assert_called_once_with(
        coordinator._context.library.asset_query_service,
        root,
    )
    coordinator._gallery_vm.on_library_tree_updated.assert_called_once_with()
    coordinator._playback.set_map_runtime.assert_called_once_with(
        coordinator._context.library.map_runtime
    )


def test_on_library_tree_updated_uses_bound_people_service_when_available() -> None:
    coordinator = MainCoordinator.__new__(MainCoordinator)
    root = Path("/library")
    people_service = PeopleService(root)
    people_page = MagicMock()

    coordinator._context = MagicMock()
    coordinator._context.library.root.return_value = root
    coordinator._context.library.people_service = people_service
    coordinator._context.library.state_repository = MagicMock()
    coordinator._context.library.asset_query_service = MagicMock()
    coordinator._context.asset_runtime.repository = MagicMock()
    coordinator._context.asset_runtime.bind_library_root = MagicMock()
    coordinator._asset_service = MagicMock()
    coordinator._asset_list_vm = MagicMock()
    coordinator._gallery_vm = MagicMock()
    coordinator._logger = MagicMock()
    coordinator._playback = MagicMock()
    coordinator._window = MagicMock(ui=MagicMock(people_page=people_page))

    coordinator._on_library_tree_updated()

    people_page.set_people_service.assert_called_once_with(people_service)
    coordinator._playback.set_map_runtime.assert_called_once_with(
        coordinator._context.library.map_runtime
    )
    coordinator._playback.set_people_service.assert_called_once_with(people_service)
    coordinator._playback.set_people_library_root.assert_not_called()


def test_handle_face_name_toggle_changed_persists_setting_and_updates_playback() -> None:
    coordinator = MainCoordinator.__new__(MainCoordinator)
    coordinator._context = MagicMock()
    coordinator._context.settings.get.return_value = False
    coordinator._context.settings.set = MagicMock()
    coordinator._playback = MagicMock()

    coordinator._handle_face_name_toggle_changed(True)

    coordinator._context.settings.set.assert_called_once_with("ui.show_face_names_in_detail", True)
    coordinator._playback.set_face_name_display_enabled.assert_called_once_with(True)


def test_on_map_asset_activated_delegates_to_navigation() -> None:
    coordinator = MainCoordinator.__new__(MainCoordinator)
    coordinator._navigation = MagicMock()

    coordinator._on_map_asset_activated("nested/photo.jpg")

    coordinator._navigation.open_location_asset.assert_called_once_with("nested/photo.jpg")


def test_connect_signals_wires_location_scan_updates_from_library() -> None:
    coordinator = MainCoordinator.__new__(MainCoordinator)
    coordinator._window = MagicMock()
    coordinator._window.ui = MagicMock()
    coordinator._context = MagicMock()
    coordinator._facade = MagicMock()
    coordinator._gallery_store = MagicMock()
    coordinator._gallery_vm = MagicMock()
    coordinator._status_bar = MagicMock()
    coordinator._asset_list_vm = MagicMock()
    coordinator._playback = MagicMock()
    coordinator._player_view_controller = MagicMock()
    coordinator._detail_vm = MagicMock()
    coordinator._navigation = MagicMock()
    coordinator._dialog = MagicMock()
    coordinator._edit = MagicMock()
    coordinator._restore_preferences = MagicMock()
    coordinator._on_library_tree_updated = MagicMock()
    coordinator._on_asset_clicked = MagicMock()
    coordinator._on_favorite_clicked = MagicMock()
    coordinator._sync_selection = MagicMock()
    coordinator._on_map_asset_activated = MagicMock()
    coordinator._on_cluster_activated = MagicMock()
    coordinator._handle_open_album_dialog = MagicMock()
    coordinator._handle_face_name_toggle_changed = MagicMock()
    coordinator.open_album_from_path = MagicMock()
    coordinator._on_people_cluster_activated = MagicMock()
    coordinator._on_people_group_activated = MagicMock()
    coordinator._handle_wheel_action_changed = MagicMock()

    coordinator._connect_signals()

    coordinator._context.library.scanChunkReady.connect.assert_any_call(
        coordinator._gallery_vm.handle_location_scan_chunk
    )
    coordinator._context.library.scanFinished.connect.assert_any_call(
        coordinator._gallery_vm.handle_location_scan_finished
    )
    coordinator._facade.move_service.moveFinished.connect.assert_any_call(
        coordinator._status_bar.handle_move_finished
    )
    coordinator._facade.move_service.moveFinished.connect.assert_any_call(
        coordinator._handle_move_finished_toast
    )


def _make_move_toast_coordinator(tmp_path: Path) -> tuple[MainCoordinator, Path, MagicMock]:
    coordinator = MainCoordinator.__new__(MainCoordinator)
    toast = MagicMock()
    coordinator._window = MagicMock(ui=MagicMock(notification_toast=toast))
    coordinator._context = MagicMock()
    trash_root = tmp_path / "Recently Deleted"
    coordinator._context.library.deleted_directory.return_value = trash_root
    return coordinator, trash_root, toast


def test_handle_move_finished_toast_shows_for_successful_plain_move(
    tmp_path: Path,
) -> None:
    coordinator, _trash_root, toast = _make_move_toast_coordinator(tmp_path)

    coordinator._handle_move_finished_toast(
        tmp_path / "Album A",
        tmp_path / "Album B",
        True,
        "Moved 1 item.",
    )

    toast.show_toast.assert_called_once_with("Moved")


def test_handle_move_finished_toast_skips_failed_move(tmp_path: Path) -> None:
    coordinator, _trash_root, toast = _make_move_toast_coordinator(tmp_path)

    coordinator._handle_move_finished_toast(
        tmp_path / "Album A",
        tmp_path / "Album B",
        False,
        "No files were moved.",
    )

    toast.show_toast.assert_not_called()


def test_handle_move_finished_toast_skips_delete_and_restore(
    tmp_path: Path,
) -> None:
    coordinator, trash_root, toast = _make_move_toast_coordinator(tmp_path)
    album_root = tmp_path / "Album A"

    coordinator._handle_move_finished_toast(
        album_root,
        trash_root,
        True,
        "Deleted 1 item.",
    )
    coordinator._handle_move_finished_toast(
        trash_root,
        album_root,
        True,
        "Restored 1 item.",
    )

    toast.show_toast.assert_not_called()


def test_handle_media_load_failed_prunes_row_and_refreshes_collection(tmp_path: Path) -> None:
    coordinator = MainCoordinator.__new__(MainCoordinator)
    failed_path = tmp_path / "library" / "Album" / "motion.mov"
    failed_path.parent.mkdir(parents=True)
    updates = MagicMock()
    updates.handle_media_load_failure.return_value = failed_path.parent

    coordinator._media_failure_cleanup_paths = set()
    coordinator._dialog = MagicMock()
    coordinator._gallery_store = MagicMock()
    coordinator._logger = MagicMock()
    coordinator._facade = MagicMock(library_updates=updates)

    coordinator._handle_media_load_failed(failed_path, "decoder failed")

    coordinator._dialog.show_error.assert_called_once()
    updates.handle_media_load_failure.assert_called_once_with(failed_path)
    coordinator._gallery_store.reload_current_selection.assert_called_once_with()


def test_handle_people_snapshot_sidebar_refresh_prunes_people_pins_before_refresh() -> None:
    coordinator = MainCoordinator.__new__(MainCoordinator)
    root = Path("/library")
    coordinator._context = MagicMock()
    coordinator._context.library.root.return_value = root
    coordinator._pinned_items_service = MagicMock()
    coordinator._window = MagicMock(ui=MagicMock(sidebar=MagicMock()))

    event = SimpleNamespace(
        library_root=root,
        changed_person_ids=("person-a",),
        changed_group_ids=("group-a",),
        person_redirects={"person-a": "person-b"},
        group_redirects={"group-a": "group-b"},
    )

    coordinator._handle_people_snapshot_sidebar_refresh(event)

    coordinator._pinned_items_service.prune_missing_people_entities.assert_called_once_with(
        root,
        person_ids=("person-a",),
        group_ids=("group-a",),
        person_redirects={"person-a": "person-b"},
        group_redirects={"group-a": "group-b"},
    )
    coordinator._window.ui.sidebar.refresh_tree_model.assert_called_once_with()

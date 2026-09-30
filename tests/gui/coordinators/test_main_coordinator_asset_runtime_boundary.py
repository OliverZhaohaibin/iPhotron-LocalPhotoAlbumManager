from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from iPhoto.domain.models.query import AssetQuery
from iPhoto.gui.coordinators import (
    desktop_coordinator_runtime as main_coordinator_module,
)
from iPhoto.gui.coordinators.gallery_coordinator import GalleryCoordinator
from iPhoto.gui.coordinators.desktop_coordinator_runtime import DesktopCoordinatorRuntime

MainCoordinator = DesktopCoordinatorRuntime


def test_on_library_tree_updated_rebinds_core_domains_only() -> None:
    coordinator = MainCoordinator.__new__(MainCoordinator)
    root = Path("/library")
    map_runtime = SimpleNamespace(package_root=lambda: Path("/session/maps"))
    map_interaction_service = SimpleNamespace()

    coordinator._context = MagicMock()
    coordinator._context.library_session = None
    coordinator._context.library.root.return_value = root
    coordinator._context.library.map_runtime = map_runtime
    coordinator._context.library.map_interaction_service = map_interaction_service
    coordinator.gallery = MagicMock()
    coordinator.detail = MagicMock()
    coordinator._recognition = None
    coordinator._location_info = None
    coordinator._logger = MagicMock()
    coordinator._map_extension_download = MagicMock()
    coordinator._window = MagicMock(ui=MagicMock())

    coordinator._on_library_tree_updated()

    coordinator.gallery.rebind_library.assert_called_once_with()
    coordinator.detail.rebind_library.assert_called_once_with(
        0,
        session_changed=False,
    )
    coordinator._map_extension_download.set_package_root.assert_called_once_with(
        Path("/session/maps").resolve()
    )
    coordinator._window.ui.map_view.set_map_runtime.assert_called_once_with(
        map_runtime
    )
    coordinator._window.ui.map_view.set_map_interaction_service.assert_called_once_with(
        map_interaction_service
    )


def test_shutdown_is_idempotent_and_does_not_request_app_quit(monkeypatch) -> None:
    coordinator = MainCoordinator.__new__(MainCoordinator)
    coordinator._is_shutting_down = False
    coordinator._shutdown_complete = False
    coordinator._logger = MagicMock()
    coordinator._location_info = MagicMock()
    coordinator._facade = MagicMock()
    coordinator._playback = MagicMock()
    coordinator._edit = MagicMock()
    coordinator._window = MagicMock()
    coordinator._window.ui = SimpleNamespace(
        preview_window=MagicMock(),
        map_view=MagicMock(),
    )
    coordinator._context = MagicMock()
    coordinator._context.event_bus.shutdown = MagicMock()
    coordinator._context._asset_runtime.shutdown = MagicMock()

    thread_pool = MagicMock()
    thread_pool.waitForDone.return_value = True
    monkeypatch.setattr(
        main_coordinator_module.QThreadPool,
        "globalInstance",
        lambda: thread_pool,
    )
    original_qapp_instance = main_coordinator_module.QCoreApplication.instance
    qapp_instance_calls: list[bool] = []
    monkeypatch.setattr(
        main_coordinator_module.QCoreApplication,
        "instance",
        lambda: qapp_instance_calls.append(True) or original_qapp_instance(),
    )

    coordinator.shutdown()
    coordinator.shutdown()

    coordinator._location_info.drain.assert_called_once_with()
    coordinator._playback.shutdown.assert_called_once_with()
    coordinator._edit.shutdown.assert_called_once_with()
    coordinator._window.ui.preview_window.close_preview.assert_called_once_with(False)
    coordinator._window.ui.map_view.shutdown.assert_called_once_with()
    coordinator._window.ui.map_view.close.assert_called_once_with()
    coordinator._facade.cancel_active_scans.assert_called_once_with()
    coordinator._context.library.shutdown.assert_called_once_with()
    coordinator._context.close_library.assert_called_once_with()
    coordinator._context._asset_runtime.shutdown.assert_called_once_with()
    coordinator._location_info.shutdown.assert_called_once_with()
    coordinator._context.event_bus.shutdown.assert_called_once_with()
    thread_pool.waitForDone.assert_called_once_with(2000)
    thread_pool.clear.assert_not_called()
    assert qapp_instance_calls == []


def test_open_album_from_path_creates_session_when_no_library_is_bound(
    tmp_path: Path,
) -> None:
    coordinator = GalleryCoordinator.__new__(GalleryCoordinator)
    album_root = tmp_path / "Album"
    album_root.mkdir()

    coordinator._context = MagicMock()
    coordinator._context.library_session = None
    coordinator._context.library.root.return_value = None
    coordinator._facade = MagicMock()
    coordinator._navigation = MagicMock()
    coordinator.rebind_library = MagicMock()

    GalleryCoordinator.open_album_from_path(coordinator, album_root)

    coordinator._context.open_library.assert_called_once_with(album_root)
    coordinator.rebind_library.assert_called_once_with()
    coordinator._navigation.open_album.assert_called_once_with(album_root)


def test_open_album_from_path_reuses_session_for_album_inside_library(
    tmp_path: Path,
) -> None:
    coordinator = GalleryCoordinator.__new__(GalleryCoordinator)
    library_root = tmp_path / "Library"
    album_root = library_root / "Album"
    album_root.mkdir(parents=True)

    coordinator._context = MagicMock()
    coordinator._context.library_session = None
    coordinator._context.library.root.return_value = library_root
    coordinator._facade = MagicMock()
    coordinator._navigation = MagicMock()
    coordinator.rebind_library = MagicMock()

    GalleryCoordinator.open_album_from_path(coordinator, album_root)

    coordinator._context.open_library.assert_not_called()
    coordinator.rebind_library.assert_not_called()
    coordinator._navigation.open_album.assert_called_once_with(album_root)


def test_open_album_from_path_blocks_cross_library_switch_during_edit(
    tmp_path: Path,
) -> None:
    coordinator = GalleryCoordinator.__new__(GalleryCoordinator)
    old_root = tmp_path / "Old"
    new_root = tmp_path / "New"
    old_root.mkdir()
    new_root.mkdir()
    coordinator._context = MagicMock()
    coordinator._context.library_session = None
    coordinator._context.library.root.return_value = old_root
    coordinator._facade = MagicMock()
    coordinator._navigation = MagicMock()
    coordinator._library_rebind_preflight = lambda: False

    GalleryCoordinator.open_album_from_path(coordinator, new_root)

    coordinator._context.open_library.assert_not_called()
    coordinator._navigation.open_album.assert_not_called()
    coordinator._facade.errorRaised.emit.assert_called_once()


def test_on_library_tree_updated_rebinds_created_optional_domains() -> None:
    coordinator = MainCoordinator.__new__(MainCoordinator)
    root = Path("/library")
    map_runtime = SimpleNamespace(package_root=lambda: Path("/session/maps"))

    coordinator._context = MagicMock()
    coordinator._context.library_session = None
    coordinator._context.library.root.return_value = root
    coordinator._context.library.map_runtime = map_runtime
    coordinator.gallery = MagicMock()
    coordinator.detail = MagicMock()
    coordinator._recognition = MagicMock()
    coordinator._location_info = MagicMock()
    coordinator._logger = MagicMock()
    coordinator._map_extension_download = MagicMock()
    coordinator._window = MagicMock(ui=MagicMock())

    coordinator._on_library_tree_updated()

    coordinator.gallery.rebind_library.assert_called_once_with()
    coordinator.detail.rebind_library.assert_called_once_with(
        0,
        session_changed=False,
    )
    coordinator._recognition.rebind_library.assert_called_once_with()
    coordinator._location_info.rebind_library.assert_called_once_with()
    coordinator._map_extension_download.set_package_root.assert_called_once_with(
        Path("/session/maps").resolve()
    )


def test_external_library_epoch_change_safely_invalidates_edit_first() -> None:
    coordinator = MainCoordinator.__new__(MainCoordinator)
    coordinator._context = MagicMock(library_epoch=8)
    coordinator._context.library_session = None
    coordinator._context.library.root.return_value = Path("/library-b")
    coordinator._observed_library_epoch = 7
    coordinator._edit = MagicMock()
    coordinator.gallery = MagicMock()
    coordinator.detail = MagicMock()
    coordinator._recognition = None
    coordinator._location_info = None
    coordinator._logger = MagicMock()
    coordinator._map_extension_download = MagicMock()
    coordinator._window = MagicMock(ui=SimpleNamespace())

    coordinator._on_library_tree_updated()

    coordinator._edit.invalidate_library_binding.assert_called_once_with()
    coordinator.detail.rebind_library.assert_called_once_with(
        8,
        session_changed=True,
    )


def test_resolve_map_package_root_prefers_bound_runtime_root() -> None:
    package_root = MainCoordinator._resolve_map_package_root(
        SimpleNamespace(package_root=lambda: Path("/bound/maps"))
    )

    assert package_root == Path("/bound/maps").resolve()


def test_map_feature_activation_binds_lazy_location_and_map_services() -> None:
    coordinator = MainCoordinator.__new__(MainCoordinator)
    location_service = object()
    map_runtime = SimpleNamespace(package_root=lambda: Path("/session/maps"))
    interaction_service = object()
    session = SimpleNamespace(
        locations=location_service,
        maps=map_runtime,
        map_interactions=interaction_service,
    )
    library = MagicMock()
    coordinator._context = SimpleNamespace(
        library_session=session,
        library=library,
    )
    map_view = MagicMock()
    coordinator._window = SimpleNamespace(
        ui=SimpleNamespace(
            map_view=map_view,
            download_map_extension_action=MagicMock(),
        )
    )
    coordinator._map_extension_download = MagicMock()
    coordinator._map_extension_download.maybe_prompt_on_startup.return_value = False
    coordinator._on_map_asset_activated = MagicMock()
    coordinator._on_cluster_activated = MagicMock()

    coordinator._on_feature_created("map", object())

    library.activate_map_services.assert_called_once_with(
        location_service,
        map_runtime,
        interaction_service,
    )
    map_view.set_map_runtime.assert_called_once_with(map_runtime)
    map_view.set_map_interaction_service.assert_called_once_with(interaction_service)


def test_handle_face_name_toggle_changed_persists_setting_and_updates_recognition() -> None:
    coordinator = MainCoordinator.__new__(MainCoordinator)
    coordinator._context = MagicMock()
    coordinator._context.settings.get.return_value = False
    coordinator._context.settings.set = MagicMock()
    coordinator._recognition = MagicMock()

    coordinator._handle_face_name_toggle_changed(True)

    coordinator._context.settings.set.assert_called_once_with("ui.show_face_names_in_detail", True)
    coordinator._recognition.set_face_name_display_enabled.assert_called_once_with(True)


def test_on_map_asset_activated_delegates_to_navigation() -> None:
    coordinator = MainCoordinator.__new__(MainCoordinator)
    coordinator._navigation = MagicMock()

    coordinator._on_map_asset_activated("nested/photo.jpg")

    coordinator._navigation.open_location_asset.assert_called_once_with("nested/photo.jpg")


def test_connect_signals_wires_location_scan_updates_from_library_and_service() -> None:
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

    coordinator._context.library.scanBatchCommitted.connect.assert_any_call(
        coordinator._asset_list_vm.handle_scan_batch
    )
    coordinator._context.library.scanBatchCommitted.connect.assert_any_call(
        coordinator._gallery_vm.handle_location_scan_batch
    )
    coordinator._context.library.scanFinished.connect.assert_any_call(
        coordinator._gallery_store.handle_scan_finished
    )
    coordinator._context.library.scanFinished.connect.assert_any_call(
        coordinator._gallery_vm.handle_location_scan_finished
    )
    coordinator._facade.library_updates.scanBatchCommitted.connect.assert_any_call(
        coordinator._asset_list_vm.handle_scan_batch
    )
    coordinator._facade.library_updates.scanBatchCommitted.connect.assert_any_call(
        coordinator._gallery_vm.handle_location_scan_batch
    )
    coordinator._facade.library_updates.scanFinished.connect.assert_any_call(
        coordinator._gallery_store.handle_scan_finished
    )
    coordinator._facade.library_updates.scanFinished.connect.assert_any_call(
        coordinator._gallery_vm.handle_location_scan_finished
    )
    coordinator._facade.moveFinished.connect.assert_any_call(
        coordinator._status_bar.handle_move_finished
    )
    coordinator._facade.moveFinished.connect.assert_any_call(
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


def test_asset_reload_request_reloads_current_target_album(tmp_path: Path) -> None:
    coordinator = MainCoordinator.__new__(MainCoordinator)
    root = tmp_path / "Library"
    album_root = root / "Album"
    album_root.mkdir(parents=True)
    store = MagicMock()
    store.active_root.return_value = album_root
    store.library_root.return_value = root
    store.current_query.return_value = AssetQuery(album_path="Album")
    coordinator._gallery_store = store

    coordinator._handle_asset_reload_requested(album_root, False, False)

    store.reload_current_selection.assert_called_once_with()


def test_asset_reload_request_reloads_current_library_aggregate(tmp_path: Path) -> None:
    coordinator = MainCoordinator.__new__(MainCoordinator)
    root = tmp_path / "Library"
    album_root = root / "Album"
    album_root.mkdir(parents=True)
    store = MagicMock()
    store.active_root.return_value = root
    store.library_root.return_value = root
    store.current_query.return_value = AssetQuery()
    coordinator._gallery_store = store

    coordinator._handle_asset_reload_requested(album_root, False, False)

    store.reload_current_selection.assert_called_once_with()


def test_asset_reload_request_ignores_unrelated_collection(tmp_path: Path) -> None:
    coordinator = MainCoordinator.__new__(MainCoordinator)
    root = tmp_path / "Library"
    album_root = root / "Album"
    other_root = root / "Other"
    album_root.mkdir(parents=True)
    other_root.mkdir()
    store = MagicMock()
    store.active_root.return_value = other_root
    store.library_root.return_value = root
    store.current_query.return_value = AssetQuery(album_path="Other")
    coordinator._gallery_store = store

    coordinator._handle_asset_reload_requested(album_root, False, False)

    store.reload_current_selection.assert_not_called()


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

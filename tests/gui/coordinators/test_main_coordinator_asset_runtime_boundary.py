from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from iPhoto.gui.coordinators.main_coordinator import MainCoordinator


def test_on_library_tree_updated_rebinds_asset_list_vm_and_reloads_selection() -> None:
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

    coordinator._context.asset_runtime.bind_library_root.assert_called_once_with(root)
    coordinator._asset_service.set_repository.assert_called_once_with(
        coordinator._context.asset_runtime.repository
    )
    coordinator._asset_list_vm.rebind_repository.assert_called_once_with(
        coordinator._context.asset_runtime.repository,
        root,
    )
    coordinator._gallery_vm.on_library_tree_updated.assert_called_once_with()
    coordinator._playback.set_people_library_root.assert_called_once_with(root)


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

    coordinator._asset_list_vm.rebind_repository.assert_called_once_with(
        coordinator._context.asset_runtime.repository,
        root,
    )
    coordinator._gallery_vm.on_library_tree_updated.assert_called_once_with()


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

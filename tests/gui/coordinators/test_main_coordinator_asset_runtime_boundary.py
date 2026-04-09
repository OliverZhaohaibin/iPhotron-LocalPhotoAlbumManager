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
    coordinator._logger = MagicMock()

    coordinator._on_library_tree_updated()

    coordinator._context.asset_runtime.bind_library_root.assert_called_once_with(root)
    coordinator._asset_service.set_repository.assert_called_once_with(
        coordinator._context.asset_runtime.repository
    )
    coordinator._asset_list_vm.rebind_repository.assert_called_once_with(
        coordinator._context.asset_runtime.repository,
        root,
    )
    coordinator._asset_list_vm.reload_current_selection.assert_called_once_with()

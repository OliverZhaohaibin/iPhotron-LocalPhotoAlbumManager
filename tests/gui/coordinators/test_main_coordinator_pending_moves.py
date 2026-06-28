from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from iPhoto.gui.coordinators.main_coordinator import MainCoordinator


class _PendingMoveModel:
    def __init__(self) -> None:
        self.cleared: list[list[Path]] = []
        self.rollback_count = 0

    def clear_pending_moves_for_paths(self, paths: list[Path]) -> bool:
        self.cleared.append(list(paths))
        return True

    def rollback_pending_moves(self) -> bool:
        self.rollback_count += 1
        return True


def test_move_completed_success_clears_pending_sources_and_destinations() -> None:
    model = _PendingMoveModel()
    coordinator = SimpleNamespace(_asset_list_vm=model)

    MainCoordinator._handle_move_completed_pending_cleanup(
        coordinator,
        Path("/library/Album"),
        Path("/library/.trash"),
        [
            (Path("/library/Album/a.jpg"), Path("/library/.trash/a.jpg")),
            ("/library/Album/b.jpg", "/library/.trash/b.jpg"),
        ],
        True,
        True,
        True,
        False,
    )

    assert model.rollback_count == 0
    assert model.cleared == [
        [
            Path("/library/Album/a.jpg"),
            Path("/library/.trash/a.jpg"),
            Path("/library/Album/b.jpg"),
            Path("/library/.trash/b.jpg"),
        ]
    ]


def test_move_completed_failure_rolls_back_pending_moves() -> None:
    model = _PendingMoveModel()
    coordinator = SimpleNamespace(_asset_list_vm=model)

    MainCoordinator._handle_move_completed_pending_cleanup(
        coordinator,
        Path("/library/Album"),
        Path("/library/.trash"),
        [(Path("/library/Album/a.jpg"), Path("/library/.trash/a.jpg"))],
        True,
        False,
        True,
        False,
    )

    assert model.rollback_count == 1
    assert model.cleared == []


def test_move_finished_failure_rolls_back_pending_moves() -> None:
    model = _PendingMoveModel()
    coordinator = SimpleNamespace(_asset_list_vm=model)

    MainCoordinator._handle_move_finished_pending_cleanup(
        coordinator,
        Path("/library/Album"),
        Path("/library/.trash"),
        False,
        "failed",
    )

    assert model.rollback_count == 1
    assert model.cleared == []


def test_library_tree_update_rebinds_location_write_queue() -> None:
    root = Path("/library")
    location_queue = Mock(bind_library_root=Mock())
    coordinator = SimpleNamespace(
        _library_root=lambda: root,
        _logger=Mock(debug=Mock()),
        _context=SimpleNamespace(asset_runtime=Mock(bind_library_root=Mock())),
        _location_write_queue=location_queue,
        _asset_list_vm=Mock(rebind_asset_query_service=Mock()),
        _asset_query_service=Mock(return_value=object()),
        _asset_state_service=Mock(return_value=object()),
        _gallery_vm=Mock(
            bind_asset_state_service=Mock(),
            on_library_tree_updated=Mock(),
        ),
        _detail_vm=Mock(bind_asset_state_service=Mock()),
        _window=SimpleNamespace(ui=None),
        _people_service=Mock(return_value=None),
        _map_runtime=Mock(return_value=None),
        _map_interaction_service=Mock(return_value=None),
        _map_extension_download=Mock(set_package_root=Mock()),
        _resolve_map_package_root=Mock(return_value=None),
        _playback=None,
    )

    MainCoordinator._on_library_tree_updated(coordinator)

    location_queue.bind_library_root.assert_called_once_with(root)

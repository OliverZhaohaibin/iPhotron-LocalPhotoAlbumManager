from pathlib import Path
from types import SimpleNamespace

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

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for People dashboard widget tests", exc_type=ImportError)
pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets not available", exc_type=ImportError)

from PySide6.QtWidgets import QApplication

from iPhoto.gui.ui.widgets.people_dashboard import MergeConfirmDialog, PeopleDashboardWidget
from iPhoto.people.repository import PersonSummary


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def test_drag_merge_shows_single_confirmation(monkeypatch, qapp: QApplication) -> None:
    widget = PeopleDashboardWidget()
    widget._summaries = [
        PersonSummary("person-a", "Alice", "face-a", 3, None, "2024-01-01T00:00:00Z"),
        PersonSummary("person-b", "Bob", "face-b", 2, None, "2024-01-01T00:00:01Z"),
    ]
    widget._populate_cards()

    cards = widget._board.visible_cards()
    assert len(cards) == 2

    confirm_calls: list[int] = []

    def _confirm(_people_count: int, _parent=None) -> bool:
        confirm_calls.append(1)
        return False

    monkeypatch.setattr(MergeConfirmDialog, "confirm", staticmethod(_confirm))
    monkeypatch.setattr(widget._board, "check_card_proximity", lambda _card: None)
    monkeypatch.setattr(widget._board, "animate_to_layout", lambda: None)

    widget._board.proximity_pair = (cards[0], cards[1])
    widget._board.finish_drag(cards[0])

    assert len(confirm_calls) == 1

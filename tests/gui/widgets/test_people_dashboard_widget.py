from __future__ import annotations

import os
from types import SimpleNamespace
from pathlib import Path

import pytest

pytest.importorskip(
    "PySide6", reason="PySide6 is required for People dashboard widget tests", exc_type=ImportError
)
pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets not available", exc_type=ImportError)

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from iPhoto.gui.ui.widgets import people_dashboard_cards
from iPhoto.gui.ui.widgets.people_dashboard import (
    GroupPeopleDialog,
    MergeConfirmDialog,
    PeopleDashboardWidget,
)
from iPhoto.people.repository import PeopleGroupSummary, PersonSummary


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


def test_people_card_menu_contains_new_group(qapp: QApplication) -> None:
    widget = PeopleDashboardWidget()
    widget._summaries = [
        PersonSummary("person-a", "Alice", "face-a", 3, None, "2024-01-01T00:00:00Z"),
        PersonSummary("person-b", "Bob", "face-b", 2, None, "2024-01-01T00:00:01Z"),
    ]

    menu = widget._build_card_menu(widget._summaries[0])
    action_texts = [action.text() for action in menu.actions()]

    assert "New Group" in action_texts
    assert action_texts.index("New Group") < action_texts.index("Merge Into...")


def test_people_card_defers_thumbnail_artwork(
    monkeypatch, qapp: QApplication, tmp_path: Path
) -> None:
    widget = PeopleDashboardWidget()
    thumbnail_path = tmp_path / "face.jpg"
    widget._summaries = [
        PersonSummary(
            "person-a",
            "Alice",
            "face-a",
            3,
            thumbnail_path,
            "2024-01-01T00:00:00Z",
        )
    ]

    calls: list[tuple[Path, tuple[int, int]]] = []

    def _fake_pixmap(path: Path, size: tuple[int, int]) -> QPixmap:
        calls.append((path, size))
        pixmap = QPixmap(size[0], size[1])
        pixmap.fill(QColor("#FF0000"))
        return pixmap

    monkeypatch.setattr(people_dashboard_cards, "_pixmap_from_image_path", _fake_pixmap)
    widget._populate_cards()

    card = widget._board.visible_cards()[0]
    placeholder = card._cover_pixmap()

    assert not placeholder.isNull()
    assert len(widget._artwork_queue) == 1
    assert calls == []

    widget._load_next_artwork()

    assert not card._cover_pixmap().isNull()
    assert widget._artwork_queue == []
    assert calls == [
        (
            thumbnail_path,
            (
                people_dashboard_cards.CARD_WIDTH * 2,
                people_dashboard_cards.CARD_HEIGHT * 2,
            ),
        )
    ]


def test_unnamed_people_card_has_no_display_placeholder(qapp: QApplication) -> None:
    widget = PeopleDashboardWidget()
    widget._summaries = [PersonSummary("person-a", None, "face-a", 3, None, "2024-01-01T00:00:00Z")]
    widget._populate_cards()

    card = widget._board.visible_cards()[0]

    assert card.display_name() == ""


def test_group_people_dialog_defaults_and_shift_selects_range(qapp: QApplication) -> None:
    summaries = [
        PersonSummary("person-a", "Alice", "face-a", 3, None, "2024-01-01T00:00:00Z"),
        PersonSummary("person-b", "Bob", "face-b", 2, None, "2024-01-01T00:00:01Z"),
        PersonSummary("person-c", None, "face-c", 1, None, "2024-01-01T00:00:02Z"),
        PersonSummary("person-d", "Dana", "face-d", 1, None, "2024-01-01T00:00:03Z"),
    ]
    dialog = GroupPeopleDialog(summaries, initial_selected_ids=["person-b"])

    assert dialog.selected_person_ids() == ["person-b"]
    assert dialog.add_button.isEnabled() is False

    dialog._handle_tile_clicked(0, False)
    assert set(dialog.selected_person_ids()) == {"person-a", "person-b"}
    assert dialog.add_button.isEnabled() is True

    dialog._handle_tile_clicked(3, True)
    assert set(dialog.selected_person_ids()) == {
        "person-a",
        "person-b",
        "person-c",
        "person-d",
    }
    dialog.close()


def test_group_people_dialog_supports_light_and_dark_styles(qapp: QApplication) -> None:
    summaries = [
        PersonSummary("person-a", "Alice", "face-a", 3, None, "2024-01-01T00:00:00Z"),
        PersonSummary("person-b", "Bob", "face-b", 2, None, "2024-01-01T00:00:01Z"),
    ]

    light_dialog = GroupPeopleDialog(summaries, dark_mode=False)
    dark_dialog = GroupPeopleDialog(summaries, dark_mode=True)

    assert light_dialog._dark_mode is False
    assert "#FFFFFF" in light_dialog._panel.styleSheet()
    assert "rgba(255, 255, 255, 0.98)" not in light_dialog._panel.styleSheet()
    assert light_dialog._panel.graphicsEffect() is None
    assert light_dialog._SHADOW_MAX_ALPHA == 18
    assert dark_dialog._dark_mode is True
    assert "#171B27" in dark_dialog._panel.styleSheet()
    assert dark_dialog._panel.graphicsEffect() is None

    light_dialog.close()
    dark_dialog.close()


def test_group_people_dialog_has_no_background_overlay(qapp: QApplication) -> None:
    summaries = [
        PersonSummary("person-a", "Alice", "face-a", 3, None, "2024-01-01T00:00:00Z"),
        PersonSummary("person-b", "Bob", "face-b", 2, None, "2024-01-01T00:00:01Z"),
    ]
    dialog = GroupPeopleDialog(summaries, dark_mode=False)
    dialog.show()
    qapp.processEvents()

    image = QImage(dialog.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    dialog.render(image)

    assert image.pixelColor(2, 2).alpha() == 0
    dialog.close()


def test_people_dashboard_popup_theme_uses_window_context(qapp: QApplication) -> None:
    class Theme:
        def __init__(self, mode: str) -> None:
            self.mode = mode

        def get_effective_theme_mode(self) -> str:
            return self.mode

    shell = QWidget()
    shell.coordinator = SimpleNamespace(
        _context=SimpleNamespace(theme=Theme("light"), settings=None)
    )
    widget = PeopleDashboardWidget(parent=shell)

    assert widget._uses_dark_theme() is False
    assert "#111111" in widget._groups_title.styleSheet()
    assert "#111111" in widget._people_title.styleSheet()

    shell.coordinator._context.theme.mode = "dark"
    widget._apply_theme_styles()

    assert widget._uses_dark_theme() is True
    assert "#F5F5F7" in widget._groups_title.styleSheet()
    assert "#F5F5F7" in widget._people_title.styleSheet()

    widget.close()
    shell.close()


def test_groups_section_appears_above_people_and_emits_activation(qapp: QApplication) -> None:
    widget = PeopleDashboardWidget()
    alice = PersonSummary("person-a", "Alice", "face-a", 3, None, "2024-01-01T00:00:00Z")
    bob = PersonSummary("person-b", "Bob", "face-b", 2, None, "2024-01-01T00:00:01Z")
    widget._summaries = [alice, bob]
    widget._groups = [
        PeopleGroupSummary(
            group_id="group-ab",
            name="Alice and Bob",
            member_person_ids=("person-a", "person-b"),
            members=(alice, bob),
            asset_count=1,
            cover_asset_path=None,
            created_at="2024-01-01T00:00:02Z",
        )
    ]

    activated: list[str] = []
    widget.groupActivated.connect(activated.append)
    widget._populate_groups()
    widget._populate_cards()

    assert widget._groups_section.isHidden() is False
    assert widget._content_layout.indexOf(widget._groups_section) < widget._content_layout.indexOf(
        widget._people_title
    )

    card = widget._group_cards["group-ab"]
    card.activated.emit(card.group_id)
    assert activated == ["group-ab"]

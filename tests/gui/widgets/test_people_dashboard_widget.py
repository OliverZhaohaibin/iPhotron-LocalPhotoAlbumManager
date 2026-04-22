from __future__ import annotations

import os
from types import SimpleNamespace
from pathlib import Path

import pytest

pytest.importorskip(
    "PySide6", reason="PySide6 is required for People dashboard widget tests", exc_type=ImportError
)
pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets not available", exc_type=ImportError)

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from iPhoto.gui.services.pinned_items_service import PinnedItemsService
from iPhoto.gui.ui.widgets import people_dashboard_cards
from iPhoto.gui.ui.widgets import people_dashboard_dialogs
from iPhoto.gui.ui.widgets import people_dashboard_widget
from iPhoto.gui.ui.widgets.people_dashboard import (
    GroupPeopleDialog,
    MergeConfirmDialog,
    PeopleDashboardWidget,
)
from iPhoto.gui.ui.widgets.people_dashboard_shared import CANVAS_MARGIN
from iPhoto.people.repository import PeopleGroupSummary, PersonSummary
from iPhoto.settings.manager import SettingsManager


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


def test_drag_reorder_persists_cluster_order(monkeypatch, qapp: QApplication) -> None:
    widget = PeopleDashboardWidget()
    widget._summaries = [
        PersonSummary("person-a", "Alice", "face-a", 3, None, "2024-01-01T00:00:00Z"),
        PersonSummary("person-b", "Bob", "face-b", 2, None, "2024-01-01T00:00:01Z"),
    ]
    widget._populate_cards()

    persisted: list[list[str]] = []
    monkeypatch.setattr(widget._service, "set_cluster_order", lambda person_ids: persisted.append(list(person_ids)))
    monkeypatch.setattr(widget._board, "check_card_proximity", lambda _card: None)
    monkeypatch.setattr(widget._board, "animate_to_layout", lambda: None)

    cards = widget._board.visible_cards()
    widget._board.top_cards = [cards[1], cards[0]]
    widget._board._drag_start_order = ("person-a", "person-b")
    widget._board.finish_drag(cards[1])

    assert persisted == [["person-b", "person-a"]]


def test_drag_reorder_skips_persist_when_order_is_unchanged(monkeypatch, qapp: QApplication) -> None:
    widget = PeopleDashboardWidget()
    widget._summaries = [
        PersonSummary("person-a", "Alice", "face-a", 3, None, "2024-01-01T00:00:00Z"),
        PersonSummary("person-b", "Bob", "face-b", 2, None, "2024-01-01T00:00:01Z"),
    ]
    widget._populate_cards()

    persisted: list[list[str]] = []
    monkeypatch.setattr(widget._service, "set_cluster_order", lambda person_ids: persisted.append(list(person_ids)))
    monkeypatch.setattr(widget._board, "check_card_proximity", lambda _card: None)
    monkeypatch.setattr(widget._board, "animate_to_layout", lambda: None)

    cards = widget._board.visible_cards()
    widget._board._drag_start_order = ("person-a", "person-b")
    widget._board.finish_drag(cards[0])

    assert persisted == []


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


def test_people_card_requests_thumbnail_artwork_immediately(
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

    def _fake_request(path: Path, size: tuple[int, int]) -> tuple[str, QPixmap]:
        calls.append((path, size))
        pixmap = QPixmap(size[0], size[1])
        pixmap.fill(QColor("#FF0000"))
        return "cache-key", pixmap

    monkeypatch.setattr(people_dashboard_cards, "request_cover_pixmap", _fake_request)
    widget._populate_cards()

    card = widget._board.visible_cards()[0]
    assert not card._cover_pixmap().isNull()
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


def test_group_people_dialog_tile_updates_avatar_when_cover_ready(
    monkeypatch, qapp: QApplication, tmp_path: Path
) -> None:
    cache_key = "face-a-cache-key"

    class _FakeCoverCache(QObject):
        coverReady = Signal(str)

        def __init__(self) -> None:
            super().__init__()
            self._pixmaps: dict[str, QPixmap] = {}

        def cached_pixmap(self, cache_key: str) -> QPixmap | None:
            return self._pixmaps.get(cache_key)

    fake_cache = _FakeCoverCache()
    thumbnail_path = tmp_path / "face.jpg"
    summaries = [
        PersonSummary("person-a", "Alice", "face-a", 3, thumbnail_path, "2024-01-01T00:00:00Z"),
    ]

    def _fake_request(path: Path, _size: tuple[int, int]) -> tuple[str, QPixmap | None]:
        assert path == thumbnail_path
        return cache_key, None

    monkeypatch.setattr(people_dashboard_dialogs, "request_cover_pixmap", _fake_request)
    monkeypatch.setattr(people_dashboard_dialogs, "people_cover_cache", lambda: fake_cache)

    dialog = GroupPeopleDialog(summaries, dark_mode=False)
    tile = dialog._tiles[0]
    assert tile._avatar_pixmap() is None
    assert tile._avatar is None

    loaded = QPixmap(64, 64)
    loaded.fill(QColor("#00AA55"))
    fake_cache._pixmaps[cache_key] = loaded
    fake_cache.coverReady.emit(cache_key)
    qapp.processEvents()

    assert tile._avatar is loaded
    dialog.close()


def test_group_people_dialog_supports_light_and_dark_styles(qapp: QApplication) -> None:
    summaries = [
        PersonSummary("person-a", "Alice", "face-a", 3, None, "2024-01-01T00:00:00Z"),
        PersonSummary("person-b", "Bob", "face-b", 2, None, "2024-01-01T00:00:01Z"),
    ]

    light_dialog = GroupPeopleDialog(summaries, dark_mode=False)
    dark_dialog = GroupPeopleDialog(summaries, dark_mode=True)

    assert light_dialog._dark_mode is False
    assert "#F5F6FA" in light_dialog._panel.styleSheet()
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


def test_group_and_people_cards_share_same_left_alignment(qapp: QApplication) -> None:
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

    widget.resize(1200, 900)
    widget._populate_groups()
    widget._populate_cards()
    widget._empty.hide()
    widget._scroll.show()
    widget.show()
    qapp.processEvents()

    group_card = widget._group_cards["group-ab"]
    people_card = widget._board.visible_cards()[0]

    assert group_card.x() == CANVAS_MARGIN
    assert people_card.x() == CANVAS_MARGIN
    widget.close()


def test_status_message_updates_without_reloading_cards(qapp: QApplication) -> None:
    widget = PeopleDashboardWidget()
    widget._summaries = [
        PersonSummary("person-a", "Alice", "face-a", 3, None, "2024-01-01T00:00:00Z")
    ]
    widget._populate_cards()

    original_card = widget._board.visible_cards()[0]

    widget.set_status_message("Scanning...")

    assert widget._board.visible_cards()[0] is original_card
    assert "Click a cluster or group card" in widget._message.text()


def test_person_menu_shows_pin_action_when_pinned_service_is_available(
    tmp_path: Path, qapp: QApplication
) -> None:
    settings = SettingsManager(path=tmp_path / "settings.json")
    settings.load()
    pinned_service = PinnedItemsService(settings)
    widget = PeopleDashboardWidget()
    widget._current_library_root = tmp_path
    widget._service.set_library_root(tmp_path)
    widget.set_pinned_service(pinned_service)
    summary = PersonSummary("person-a", "Alice", "face-a", 3, None, "2024-01-01T00:00:00Z")

    menu = widget._build_card_menu(summary)

    assert "Pin" in [action.text() for action in menu.actions()]


def test_pin_unnamed_person_prompts_for_name_and_persists_pin(
    monkeypatch, tmp_path: Path, qapp: QApplication
) -> None:
    settings = SettingsManager(path=tmp_path / "settings.json")
    settings.load()
    pinned_service = PinnedItemsService(settings)
    widget = PeopleDashboardWidget()
    widget._current_library_root = tmp_path
    widget._service.set_library_root(tmp_path)
    widget.set_pinned_service(pinned_service)
    summary = PersonSummary("person-a", None, "face-a", 3, None, "2024-01-01T00:00:00Z")

    renamed: list[tuple[str, str | None]] = []
    monkeypatch.setattr(widget._service, "rename_cluster", lambda person_id, name: renamed.append((person_id, name)))
    monkeypatch.setattr(widget, "reload", lambda **_kwargs: None)
    monkeypatch.setattr(
        PeopleDashboardWidget,
        "_prompt_required_person_name",
        lambda self, _summary: "Alice",
    )

    widget._toggle_person_pin(summary)

    assert renamed == [("person-a", "Alice")]
    pinned = pinned_service.items_for_library(tmp_path)
    assert [(item.kind, item.item_id, item.label) for item in pinned] == [
        ("person", "person-a", "Alice")
    ]


def test_hidden_person_pin_shows_warning_and_does_not_persist(
    monkeypatch, tmp_path: Path, qapp: QApplication
) -> None:
    settings = SettingsManager(path=tmp_path / "settings.json")
    settings.load()
    pinned_service = PinnedItemsService(settings)
    widget = PeopleDashboardWidget()
    widget._current_library_root = tmp_path
    widget._service.set_library_root(tmp_path)
    widget.set_pinned_service(pinned_service)
    summary = PersonSummary("person-a", "Alice", "face-a", 3, None, "2024-01-01T00:00:00Z")

    warnings: list[str] = []
    monkeypatch.setattr(widget._service, "pin_block_reason", lambda _person_id: "Pinned is blocked.")
    monkeypatch.setattr(people_dashboard_widget.dialogs, "show_warning", lambda _parent, message, title="iPhoto": warnings.append(message))

    widget._toggle_person_pin(summary)

    assert warnings == ["Pinned is blocked."]
    assert pinned_service.items_for_library(tmp_path) == []


def test_pin_unnamed_group_uses_generated_sidebar_label(tmp_path: Path, qapp: QApplication) -> None:
    settings = SettingsManager(path=tmp_path / "settings.json")
    settings.load()
    pinned_service = PinnedItemsService(settings)
    widget = PeopleDashboardWidget()
    widget._current_library_root = tmp_path
    widget._service.set_library_root(tmp_path)
    widget.set_pinned_service(pinned_service)
    summary = PeopleGroupSummary(
        group_id="group-a",
        name="",
        member_person_ids=("person-a", "person-b"),
        members=(),
        asset_count=1,
        cover_asset_path=None,
        created_at="2024-01-01T00:00:00Z",
    )

    widget._toggle_group_pin(summary)

    pinned = pinned_service.items_for_library(tmp_path)
    assert len(pinned) == 1
    assert pinned[0].kind == "group"
    assert pinned[0].label == "Group 1"

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytest.importorskip(
    "PySide6", reason="PySide6 is required for People dashboard widget tests", exc_type=ImportError
)
pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets not available", exc_type=ImportError)

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from iPhoto.application.services.recognition_merge_service import (
    IdentityMergeFailure,
    IdentityMergeOutcome,
    IdentityMergeRefreshPolicy,
    IdentityRef,
)
from iPhoto.gui.services.pinned_items_service import PinnedItemsService
from iPhoto.gui.ui.widgets import (
    people_dashboard_cards,
    people_dashboard_dialogs,
    people_dashboard_widget,
)
from iPhoto.gui.ui.widgets.people_dashboard import (
    GroupPeopleDialog,
    MergeConfirmDialog,
    PeopleDashboardWidget,
)
from iPhoto.gui.ui.widgets.people_dashboard_shared import CANVAS_MARGIN
from iPhoto.people.repository import PeopleGroupSummary, PersonSummary
from iPhoto.people.service import PeopleService
from iPhoto.pets.records import PetSummary
from iPhoto.pets.service import PetService
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


@pytest.mark.parametrize(
    ("source_kind", "target_kind"),
    [
        ("person", "person"),
        ("pet", "pet"),
        ("person", "pet"),
        ("pet", "person"),
    ],
)
def test_real_card_positions_merge_once_in_all_directions(
    source_kind: str,
    target_kind: str,
    monkeypatch,
    qapp: QApplication,
) -> None:
    widget = PeopleDashboardWidget()
    widget._board.resize(800, 500)

    def make_card(kind: str, entity_id: str, index: int):
        if kind == "person":
            summary = PersonSummary(
                entity_id,
                entity_id,
                f"face-{index}",
                1,
                None,
                f"2024-01-01T00:00:0{index}Z",
            )
            return people_dashboard_cards.PeopleCard(
                board=widget._board,
                summary=summary,
                seed_index=index,
            )
        summary = PetSummary(
            entity_id,
            entity_id,
            f"det-{index}",
            1,
            None,
            f"2024-01-01T00:00:0{index}Z",
        )
        return people_dashboard_cards.PetCard(
            board=widget._board,
            summary=summary,
            seed_index=index,
        )

    source = make_card(source_kind, "source", 0)
    target = make_card(target_kind, "target", 1)
    widget._board.set_cards([source, target])
    emitted: list[tuple[str, str]] = []
    widget._board.mergeRequested.disconnect()
    widget._board.mergeRequested.connect(lambda first, second: emitted.append((first, second)))
    monkeypatch.setattr(widget._board, "animate_to_layout", lambda: None)

    source.begin_drag()
    source.move(target.pos())
    widget._board.update_drag(source)

    assert widget._board.top_cards == [source, target]
    assert widget._board.proximity_pair == (source, target)

    widget._board.finish_drag(source)
    source.end_drag()

    assert emitted == [(f"{source_kind}:source", f"{target_kind}:target")]


def test_person_and_pet_with_same_raw_id_remain_distinct(
    monkeypatch,
    qapp: QApplication,
) -> None:
    widget = PeopleDashboardWidget()
    widget._board.resize(800, 500)
    person = people_dashboard_cards.PeopleCard(
        board=widget._board,
        summary=PersonSummary("same-id", "Alice", "face-a", 1, None, "2024-01-01T00:00:00Z"),
        seed_index=0,
    )
    pet = people_dashboard_cards.PetCard(
        board=widget._board,
        summary=PetSummary("same-id", "Miso", "det-a", 1, None, "2024-01-01T00:00:01Z"),
        seed_index=1,
    )
    widget._board.set_cards([person, pet])
    emitted: list[tuple[str, str]] = []
    widget._board.mergeRequested.disconnect()
    widget._board.mergeRequested.connect(lambda first, second: emitted.append((first, second)))
    monkeypatch.setattr(widget._board, "animate_to_layout", lambda: None)

    pet.begin_drag()
    pet.move(person.pos())
    widget._board.update_drag(pet)
    widget._board.finish_drag(pet)
    pet.end_drag()

    assert emitted == [("pet:same-id", "person:same-id")]


def test_set_services_binds_people_and_pets_with_one_reload(
    monkeypatch,
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    widget = PeopleDashboardWidget()
    people_service = PeopleService(tmp_path)
    pet_service = SimpleNamespace(library_root=lambda: tmp_path)
    pinned_service = object()
    reloads: list[bool] = []
    monkeypatch.setattr(
        widget,
        "reload",
        lambda *, preserve_content=False: reloads.append(bool(preserve_content)),
    )

    widget.set_services(people_service, pet_service, pinned_service)

    assert widget._service is people_service
    assert widget._pet_service is pet_service
    assert widget._pinned_service is pinned_service
    assert reloads == [False]


def test_snapshot_advances_dashboard_index_version(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    widget = PeopleDashboardWidget()
    widget.set_services(
        PeopleService(tmp_path),
        SimpleNamespace(library_root=lambda: tmp_path),
        reload=False,
    )

    applied = widget.apply_snapshot(
        library_root=tmp_path,
        summaries=[],
        groups=[],
        pet_summaries=[],
        pending=0,
        pet_pending=0,
        index_version=4,
    )

    assert applied is True
    assert widget._index_version == 4
    assert widget._loaded_index_version == 4


def test_incremental_population_commits_first_viewport_before_remaining_cards(
    monkeypatch,
    qapp: QApplication,
) -> None:
    widget = PeopleDashboardWidget()
    widget._summaries = [
        PersonSummary(
            f"person-{index}",
            f"Person {index}",
            f"face-{index}",
            1,
            None,
            f"2024-01-01T00:00:{index:02d}Z",
        )
        for index in range(20)
    ]
    monkeypatch.setattr(PeopleDashboardWidget, "_emit_first_viewport_ready", lambda self: None)
    monkeypatch.setattr(people_dashboard_cards.PeopleCard, "load_cover_artwork", lambda self: None)

    widget._begin_incremental_population()

    assert len(widget._board.visible_cards()) == 12
    assert len(widget._pending_card_specs) == 8
    widget._build_next_card_batch()
    assert len(widget._board.visible_cards()) == 20
    assert widget._pending_card_specs == []


def test_terminal_load_failure_still_commits_first_viewport(
    qapp: QApplication,
) -> None:
    widget = PeopleDashboardWidget()
    ready_generations: list[int] = []
    widget.firstViewportReady.connect(ready_generations.append)

    widget._on_load_failed(
        widget._load_generation,
        widget._index_version,
        RuntimeError("broken people index"),
        False,
    )

    assert ready_generations == [widget._card_build_generation]


def test_drag_merge_removes_source_card_immediately(monkeypatch, qapp: QApplication) -> None:
    widget = PeopleDashboardWidget()
    widget._summaries = [
        PersonSummary("person-a", "Alice", "face-a", 3, None, "2024-01-01T00:00:00Z"),
        PersonSummary("person-b", "Bob", "face-b", 2, None, "2024-01-01T00:00:01Z"),
    ]
    widget._populate_cards()

    cards = widget._board.visible_cards()
    monkeypatch.setattr(MergeConfirmDialog, "confirm", staticmethod(lambda *_args: True))
    monkeypatch.setattr(
        widget._merge_service,
        "merge",
        lambda source, target: IdentityMergeOutcome(
            True,
            IdentityRef.parse(source),
            IdentityRef.parse(target),
            person_redirects={"person-a": "person-b"},
        ),
    )
    monkeypatch.setattr(widget, "reload", lambda **_kwargs: None)
    monkeypatch.setattr(widget._board, "check_card_proximity", lambda _card: None)
    monkeypatch.setattr(widget._board, "animate_to_layout", lambda: None)

    widget._board.proximity_pair = (cards[0], cards[1])
    widget._board.finish_drag(cards[0])

    assert [card.person_id for card in widget._board.visible_cards()] == ["person-b"]


def test_drag_reorder_persists_cluster_order(monkeypatch, qapp: QApplication) -> None:
    widget = PeopleDashboardWidget()
    widget._summaries = [
        PersonSummary("person-a", "Alice", "face-a", 3, None, "2024-01-01T00:00:00Z"),
        PersonSummary("person-b", "Bob", "face-b", 2, None, "2024-01-01T00:00:01Z"),
    ]
    widget._populate_cards()

    persisted: list[list[str]] = []
    monkeypatch.setattr(
        widget._service, "set_cluster_order", lambda person_ids: persisted.append(list(person_ids))
    )
    monkeypatch.setattr(widget._board, "check_card_proximity", lambda _card: None)
    monkeypatch.setattr(widget._board, "animate_to_layout", lambda: None)

    cards = widget._board.visible_cards()
    widget._board.top_cards = [cards[1], cards[0]]
    widget._board._drag_start_order = ("person:person-a", "person:person-b")
    widget._board.finish_drag(cards[1])

    assert persisted == [["person-b", "person-a"]]


def test_drag_reorder_skips_persist_when_order_is_unchanged(
    monkeypatch, qapp: QApplication
) -> None:
    widget = PeopleDashboardWidget()
    widget._summaries = [
        PersonSummary("person-a", "Alice", "face-a", 3, None, "2024-01-01T00:00:00Z"),
        PersonSummary("person-b", "Bob", "face-b", 2, None, "2024-01-01T00:00:01Z"),
    ]
    widget._populate_cards()

    persisted: list[list[str]] = []
    monkeypatch.setattr(
        widget._service, "set_cluster_order", lambda person_ids: persisted.append(list(person_ids))
    )
    monkeypatch.setattr(widget._board, "check_card_proximity", lambda _card: None)
    monkeypatch.setattr(widget._board, "animate_to_layout", lambda: None)

    cards = widget._board.visible_cards()
    widget._board._drag_start_order = ("person:person-a", "person:person-b")
    widget._board.finish_drag(cards[0])

    assert persisted == []


def test_drag_reorder_persists_group_order(monkeypatch, qapp: QApplication) -> None:
    widget = PeopleDashboardWidget()
    alice = PersonSummary("person-a", "Alice", "face-a", 3, None, "2024-01-01T00:00:00Z")
    bob = PersonSummary("person-b", "Bob", "face-b", 2, None, "2024-01-01T00:00:01Z")
    cara = PersonSummary("person-c", "Cara", "face-c", 1, None, "2024-01-01T00:00:02Z")
    widget._groups = [
        PeopleGroupSummary(
            group_id="group-ab",
            name="Alice and Bob",
            member_person_ids=("person-a", "person-b"),
            members=(alice, bob),
            asset_count=1,
            cover_asset_path=None,
            created_at="2024-01-01T00:00:02Z",
        ),
        PeopleGroupSummary(
            group_id="group-bc",
            name="Bob and Cara",
            member_person_ids=("person-b", "person-c"),
            members=(bob, cara),
            asset_count=1,
            cover_asset_path=None,
            created_at="2024-01-01T00:00:03Z",
        ),
    ]
    widget._populate_groups()

    persisted: list[list[str]] = []
    monkeypatch.setattr(
        widget._service, "set_group_order", lambda group_ids: persisted.append(list(group_ids))
    )
    monkeypatch.setattr(widget._groups_board, "animate_to_layout", lambda: None)

    cards = widget._groups_board.visible_cards()
    widget._groups_board.top_cards = [cards[1], cards[0]]
    widget._groups_board._drag_start_order = ("group-ab", "group-bc")
    widget._groups_board.finish_drag(cards[1])

    assert persisted == [["group-bc", "group-ab"]]


def test_people_card_menu_contains_new_group(qapp: QApplication) -> None:
    widget = PeopleDashboardWidget()
    widget._summaries = [
        PersonSummary("person-a", "Alice", "face-a", 3, None, "2024-01-01T00:00:00Z"),
        PersonSummary("person-b", "Bob", "face-b", 2, None, "2024-01-01T00:00:01Z"),
    ]

    menu = widget._build_card_menu(widget._summaries[0])
    action_texts = [action.text() for action in menu.actions()]
    action_ids = [action.data() for action in menu.actions()]

    assert "New Group" in action_texts
    assert "Hide" in action_texts
    assert action_texts.index("New Group") < action_texts.index("Merge Into...")
    assert "new_group" in action_ids
    assert "toggle_hidden" in action_ids
    assert "merge" in action_ids


def test_people_card_menu_shows_unhide_for_hidden_person(qapp: QApplication) -> None:
    widget = PeopleDashboardWidget()
    summary = PersonSummary("person-a", "Alice", "face-a", 3, None, "2024-01-01T00:00:00Z", True)

    menu = widget._build_card_menu(summary)

    assert "Unhide" in [action.text() for action in menu.actions()]


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


def test_group_card_requests_group_cover_before_collage(
    monkeypatch, qapp: QApplication, tmp_path: Path
) -> None:
    widget = PeopleDashboardWidget()
    cover_path = tmp_path / "group.jpg"
    alice = PersonSummary("person-a", "Alice", "face-a", 3, None, "2024-01-01T00:00:00Z")
    bob = PersonSummary("person-b", "Bob", "face-b", 2, None, "2024-01-01T00:00:01Z")
    widget._groups = [
        PeopleGroupSummary(
            group_id="group-ab",
            name="Alice and Bob",
            member_person_ids=("person-a", "person-b"),
            members=(alice, bob),
            asset_count=1,
            cover_asset_path=cover_path,
            created_at="2024-01-01T00:00:02Z",
        )
    ]

    cover_calls: list[tuple[Path, tuple[int, int]]] = []

    def _fake_request(path: Path, size: tuple[int, int]) -> tuple[str, QPixmap]:
        cover_calls.append((path, size))
        pixmap = QPixmap(size[0], size[1])
        pixmap.fill(QColor("#FF0000"))
        return "group-cover", pixmap

    def _fake_collage(**_kwargs) -> tuple[str, QPixmap | None]:
        raise AssertionError("group cover should be used before collage fallback")

    monkeypatch.setattr(people_dashboard_cards, "request_cover_pixmap", _fake_request)
    monkeypatch.setattr(people_dashboard_cards, "request_rendered_cover_pixmap", _fake_collage)

    widget._populate_groups()

    assert not widget._groups_board.visible_cards()[0]._cover_pixmap().isNull()
    assert cover_calls == [
        (
            cover_path,
            (
                people_dashboard_cards.GROUP_CARD_WIDTH * 2,
                people_dashboard_cards.GROUP_CARD_HEIGHT * 2,
            ),
        )
    ]


def test_unnamed_people_card_has_no_display_placeholder(qapp: QApplication) -> None:
    widget = PeopleDashboardWidget()
    widget._summaries = [PersonSummary("person-a", None, "face-a", 3, None, "2024-01-01T00:00:00Z")]
    widget._populate_cards()

    card = widget._board.visible_cards()[0]

    assert card.display_name() == ""


def test_people_and_pet_card_badges_use_asset_count(qapp: QApplication) -> None:
    widget = PeopleDashboardWidget()
    widget._summaries = [
        PersonSummary(
            "person-a",
            "Alice",
            "face-a",
            4,
            None,
            "2024-01-01T00:00:00Z",
            asset_count=2,
        )
    ]
    widget._pet_summaries = [
        PetSummary(
            "pet-a",
            "Miso",
            "det-a",
            5,
            None,
            "2024-01-01T00:00:01Z",
            asset_count=3,
        )
    ]

    widget._populate_cards()

    people_card, pet_card = widget._board.visible_cards()
    assert people_card._badge_count() == 2
    assert pet_card._badge_count() == 3


def test_people_card_badge_falls_back_for_legacy_summary_object(qapp: QApplication) -> None:
    widget = PeopleDashboardWidget()
    legacy_summary = SimpleNamespace(
        person_id="person-a",
        name="Alice",
        key_face_id="face-a",
        face_count=4,
        thumbnail_path=None,
        created_at="2024-01-01T00:00:00Z",
        is_hidden=False,
    )
    card = people_dashboard_cards.PeopleCard(
        board=widget._board,
        summary=legacy_summary,
        seed_index=0,
    )

    assert card._badge_count() == 4


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


def test_group_people_dialog_supports_single_selection_mode(qapp: QApplication) -> None:
    summaries = [
        PersonSummary("person-a", "Alice", "face-a", 3, None, "2024-01-01T00:00:00Z"),
        PersonSummary("person-b", "Bob", "face-b", 2, None, "2024-01-01T00:00:01Z"),
    ]
    dialog = GroupPeopleDialog(
        summaries,
        title_text="Choose Someone Else",
        prompt_text="Assign this face to",
        confirm_text="Choose",
        min_selection=1,
        max_selection=1,
    )

    assert dialog.add_button.isEnabled() is False

    dialog._handle_tile_clicked(0, False)
    assert dialog.selected_person_ids() == ["person-a"]
    assert dialog.add_button.isEnabled() is True

    dialog._handle_tile_clicked(1, False)
    assert dialog.selected_person_ids() == ["person-b"]

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


def test_new_group_dialog_resolves_current_window_theme(
    monkeypatch,
    qapp: QApplication,
) -> None:
    class Theme:
        def __init__(self, mode: str) -> None:
            self.mode = mode

        def get_effective_theme_mode(self) -> str:
            return self.mode

    theme = Theme("light")
    shell = QWidget()
    shell.coordinator = SimpleNamespace(_context=SimpleNamespace(theme=theme, settings=None))
    widget = PeopleDashboardWidget(parent=shell)
    summaries = [
        PersonSummary("person-a", "Alice", "face-a", 3, None, "2024-01-01T00:00:00Z"),
        PersonSummary("person-b", "Bob", "face-b", 2, None, "2024-01-01T00:00:01Z"),
    ]
    widget._summaries = summaries
    opened_dialogs: list[GroupPeopleDialog] = []

    class CapturingGroupPeopleDialog(GroupPeopleDialog):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            opened_dialogs.append(self)

        def exec(self) -> int:
            return 0

    monkeypatch.setattr(
        people_dashboard_widget,
        "GroupPeopleDialog",
        CapturingGroupPeopleDialog,
    )

    widget._open_group_dialog("person:person-a")
    light_dialog = opened_dialogs[-1]
    assert light_dialog._dark_mode is False
    assert "#F5F6FA" in light_dialog._panel.styleSheet()

    theme.mode = "dark"
    widget._open_group_dialog("person:person-a")
    dark_dialog = opened_dialogs[-1]
    assert dark_dialog._dark_mode is True
    assert "#171B27" in dark_dialog._panel.styleSheet()

    light_dialog.close()
    dark_dialog.close()
    widget.close()
    shell.close()


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


def test_merge_confirm_dialog_respects_light_theme_context(qapp: QApplication) -> None:
    class Theme:
        def get_effective_theme_mode(self) -> str:
            return "light"

    shell = QWidget()
    shell.coordinator = SimpleNamespace(_context=SimpleNamespace(theme=Theme(), settings=None))
    widget = PeopleDashboardWidget(parent=shell)
    dialog = MergeConfirmDialog(
        1,
        parent=widget,
        title_text="Hide This Person?",
        body_text="Body",
        confirm_text="Hide Person",
    )

    assert dialog._dark_mode is False
    assert "rgba(255, 255, 255, 0.94)" in dialog._panel.styleSheet()

    dialog.close()
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


def test_status_message_updates_without_reloading_cards(tmp_path: Path, qapp: QApplication) -> None:
    widget = PeopleDashboardWidget()
    widget._service.set_library_root(tmp_path)
    widget._summaries = [
        PersonSummary("person-a", "Alice", "face-a", 3, None, "2024-01-01T00:00:00Z")
    ]
    widget._populate_cards()

    original_card = widget._board.visible_cards()[0]

    widget.set_status_message("Scanning...")

    assert widget._board.visible_cards()[0] is original_card
    assert "Click a person, pet, or group card" in widget._message.text()


def test_people_dashboard_retranslate_refreshes_loaded_text_without_reloading_cards(
    monkeypatch, tmp_path: Path, qapp: QApplication
) -> None:
    widget = PeopleDashboardWidget()
    widget._service.set_library_root(tmp_path)
    widget._summaries = [
        PersonSummary("person-a", "Alice", "face-a", 3, None, "2024-01-01T00:00:00Z")
    ]
    widget._populate_cards()
    original_card = widget._board.visible_cards()[0]

    monkeypatch.setattr(
        people_dashboard_widget,
        "tr",
        lambda _context, source_text, *_args: f"XX:{source_text}",
    )

    widget.retranslate_ui()

    assert widget._title.text() == "XX:People"
    assert widget._refresh_button.text() == "XX:Refresh"
    assert widget._groups_title.text() == "XX:Groups"
    assert widget._people_title.text() == "XX:People & Pets"
    assert widget._message.text().startswith("XX:Click a person, pet, or group card")
    assert widget._board.visible_cards()[0] is original_card


def test_people_dashboard_retranslate_keeps_unbound_message_after_unbind(
    monkeypatch, tmp_path: Path, qapp: QApplication
) -> None:
    widget = PeopleDashboardWidget()
    widget._current_library_root = tmp_path
    widget._service.set_library_root(tmp_path)
    widget._summaries = [
        PersonSummary("person-a", "Alice", "face-a", 3, None, "2024-01-01T00:00:00Z")
    ]
    widget._populate_cards()
    widget.set_library_root(None)

    monkeypatch.setattr(
        people_dashboard_widget,
        "tr",
        lambda _context, source_text, *_args: f"XX:{source_text}",
    )

    widget.retranslate_ui()

    assert widget._message.text() == "XX:Bind a Basic Library to see People & Pets clusters."
    assert (
        widget._empty.text() == "XX:People & Pets appear here after a library is bound and scanned."
    )


def test_set_services_uses_injected_library_scoped_services(
    monkeypatch, qapp: QApplication, tmp_path: Path
) -> None:
    widget = PeopleDashboardWidget()
    service = PeopleService(tmp_path, asset_repository=object())
    pet_service = PetService(tmp_path, asset_repository=object())
    query_service = object()
    reloads: list[bool] = []
    monkeypatch.setattr(
        widget,
        "reload",
        lambda *, preserve_content=False: reloads.append(bool(preserve_content)),
    )

    widget.set_services(
        service,
        pet_service,
        query_service=query_service,
    )

    assert widget._service is service
    assert widget._pet_service is pet_service
    assert widget._query_service is query_service
    assert reloads == [False]


def test_people_dashboard_loader_reports_locked_database_without_raising(
    qapp: QApplication, tmp_path: Path
) -> None:
    del qapp

    class _LockedPeopleService:
        def library_root(self) -> Path:
            return tmp_path

        def load_dashboard(self, *, include_hidden: bool = False):
            del include_hidden
            raise sqlite3.OperationalError("database is locked")

    class _PetService:
        def load_dashboard(self, *, include_hidden: bool = False):
            del include_hidden
            return [], 0

    failures: list[tuple[int, int, object, bool]] = []
    signals = people_dashboard_widget._PeopleDashboardLoaderSignals()
    signals.failed.connect(
        lambda generation, index_version, error, retryable: failures.append(
            (generation, index_version, error, retryable)
        )
    )
    worker = people_dashboard_widget._PeopleDashboardLoaderWorker(
        generation=7,
        index_version=3,
        people_service=_LockedPeopleService(),
        pet_service=_PetService(),
        status_message=None,
        pet_status_message=None,
        show_hidden_people=False,
        signals=signals,
    )

    worker.run()

    assert len(failures) == 1
    assert failures[0][0:2] == (7, 3)
    assert isinstance(failures[0][2], sqlite3.OperationalError)
    assert failures[0][3] is True


def test_people_dashboard_locked_load_schedules_retry(monkeypatch, qapp: QApplication) -> None:
    del qapp
    widget = PeopleDashboardWidget()
    widget._load_generation = 4
    widget._loading = True
    monkeypatch.setattr(widget, "isVisible", lambda: True)

    widget._on_load_failed(4, 2, sqlite3.OperationalError("database is locked"), True)

    assert widget._loading is False
    assert widget._pending_index_refresh is True
    assert widget._refresh_timer.isActive()
    assert widget._message.text() == (
        "People & Pets is updating in the background. This page will retry shortly."
    )
    widget._refresh_timer.stop()


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
    monkeypatch.setattr(
        widget._service, "rename_cluster", lambda person_id, name: renamed.append((person_id, name))
    )
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
    monkeypatch.setattr(
        widget._service, "pin_block_reason", lambda _person_id: "Pinned is blocked."
    )
    monkeypatch.setattr(
        people_dashboard_widget.dialogs,
        "show_warning",
        lambda _parent, message, title="iPhoto": warnings.append(message),
    )

    widget._toggle_person_pin(summary)

    assert warnings == ["Pinned is blocked."]
    assert pinned_service.items_for_library(tmp_path) == []


def test_group_menu_contains_disband_action(qapp: QApplication) -> None:
    widget = PeopleDashboardWidget()
    summary = PeopleGroupSummary(
        group_id="group-a",
        name="Alice and Bob",
        member_person_ids=("person-a", "person-b"),
        members=(),
        asset_count=1,
        cover_asset_path=None,
        created_at="2024-01-01T00:00:00Z",
    )

    menu = widget._build_group_menu(summary)

    assert "Disband Group" in [action.text() for action in menu.actions()]


def test_merge_person_choices_include_different_hidden_state(qapp: QApplication) -> None:
    widget = PeopleDashboardWidget()
    widget._summaries = [
        PersonSummary("person-a", "Alice", "face-a", 3, None, "2024-01-01T00:00:00Z", True),
        PersonSummary("person-b", "Bob", "face-b", 2, None, "2024-01-01T00:00:01Z", False),
    ]

    choices = widget._merge_choices("person", "person-a")

    assert [choice.person_id for choice in choices] == ["person:person-b"]


def test_merge_person_reuses_group_people_dialog(monkeypatch, qapp: QApplication) -> None:
    widget = PeopleDashboardWidget()
    widget._summaries = [
        PersonSummary("person-a", "Alice", "face-a", 3, None, "2024-01-01T00:00:00Z"),
        PersonSummary("person-b", "Bob", "face-b", 2, None, "2024-01-01T00:00:01Z"),
    ]

    dialog_calls: list[dict[str, object]] = []
    confirmed: list[tuple[str, str]] = []

    class _FakeDialog:
        def __init__(self, summaries, **kwargs) -> None:
            dialog_calls.append({"summaries": summaries, "kwargs": kwargs})

        def exec(self) -> int:
            return 1

        def selected_person_ids(self) -> list[str]:
            return ["person:person-b"]

    monkeypatch.setattr(people_dashboard_widget, "GroupPeopleDialog", _FakeDialog)
    monkeypatch.setattr(
        widget,
        "_confirm_merge",
        lambda source_person_id, target_person_id: (
            confirmed.append((source_person_id, target_person_id)) or True
        ),
    )

    widget._merge_person(widget._summaries[0])

    assert len(dialog_calls) == 1
    assert [summary.person_id for summary in dialog_calls[0]["summaries"]] == ["person:person-b"]
    assert dialog_calls[0]["kwargs"]["title_text"] == "Merge Person"
    assert dialog_calls[0]["kwargs"]["prompt_text"] == "Merge into"
    assert dialog_calls[0]["kwargs"]["confirm_text"] == "Choose"
    assert dialog_calls[0]["kwargs"]["min_selection"] == 1
    assert dialog_calls[0]["kwargs"]["max_selection"] == 1
    assert confirmed == [("person:person-a", "person:person-b")]


def test_cross_identity_merge_invalidates_query_cache_before_reload(
    monkeypatch, qapp: QApplication
) -> None:
    widget = PeopleDashboardWidget()
    widget._summaries = [
        PersonSummary("person-a", "Alice", "face-a", 3, None, "2024-01-01T00:00:00Z")
    ]
    widget._pet_summaries = [PetSummary("pet-a", "Miso", "det-a", 1, None, "2024-01-01T00:00:01Z")]
    widget._query_service = Mock()
    merge_result = IdentityMergeOutcome(
        True,
        IdentityRef("person", "person-a"),
        IdentityRef("pet", "pet-a"),
        refresh_policy=IdentityMergeRefreshPolicy.IMMEDIATE,
    )
    monkeypatch.setattr(
        widget._merge_service,
        "merge",
        Mock(return_value=merge_result),
    )
    monkeypatch.setattr(MergeConfirmDialog, "confirm", staticmethod(lambda *_args: True))
    monkeypatch.setattr(widget, "_remap_pinned_identity", Mock())
    monkeypatch.setattr(widget, "reload", Mock())

    assert widget._confirm_merge("person:person-a", "pet:pet-a") is True

    widget._merge_service.merge.assert_called_once_with(
        "person:person-a",
        "pet:pet-a",
    )
    widget._query_service.invalidate.assert_called_once_with()
    widget.reload.assert_called_once_with(preserve_content=True)


def test_merge_recovery_pending_uses_distinct_information_message(
    monkeypatch,
    qapp: QApplication,
) -> None:
    widget = PeopleDashboardWidget()
    widget._summaries = [
        PersonSummary("person-a", "Alice", "face-a", 1, None, "2024-01-01T00:00:00Z")
    ]
    widget._pet_summaries = [PetSummary("pet-a", "Miso", "det-a", 1, None, "2024-01-01T00:00:01Z")]
    outcome = IdentityMergeOutcome(
        False,
        IdentityRef("person", "person-a"),
        IdentityRef("pet", "pet-a"),
        IdentityMergeFailure.RECOVERY_PENDING,
    )
    monkeypatch.setattr(widget._merge_service, "merge", Mock(return_value=outcome))
    monkeypatch.setattr(MergeConfirmDialog, "confirm", staticmethod(lambda *_args: True))
    messages: list[tuple[str, str]] = []
    monkeypatch.setattr(
        people_dashboard_widget.dialogs,
        "show_information",
        lambda _parent, message, title="iPhoto": messages.append((title, message)),
    )

    assert widget._confirm_merge("person:person-a", "pet:pet-a") is False
    assert messages == [
        (
            "Recognition Busy",
            "Recognition data is still recovering. Please try again shortly.",
        )
    ]


def test_merge_person_dialog_includes_same_hidden_people_and_pets(
    monkeypatch, qapp: QApplication
) -> None:
    widget = PeopleDashboardWidget()
    widget._summaries = [
        PersonSummary("person-a", "Alice", "face-a", 3, None, "2024-01-01T00:00:00Z"),
        PersonSummary("person-b", "Bob", "face-b", 2, None, "2024-01-01T00:00:01Z"),
    ]
    widget._pet_summaries = [
        PetSummary("pet-a", "Miso", "det-a", 1, None, "2024-01-01T00:00:02Z"),
    ]

    dialog_calls: list[list[str]] = []

    class _FakeDialog:
        def __init__(self, summaries, **_kwargs) -> None:
            dialog_calls.append([summary.person_id for summary in summaries])

        def exec(self) -> int:
            return 0

    monkeypatch.setattr(people_dashboard_widget, "GroupPeopleDialog", _FakeDialog)

    widget._merge_person(widget._summaries[0])

    assert dialog_calls == [["person:person-b", "pet:pet-a"]]


def test_merge_pet_dialog_includes_all_same_hidden_pets(monkeypatch, qapp: QApplication) -> None:
    widget = PeopleDashboardWidget()
    widget._pet_summaries = [
        PetSummary("pet-a", "Miso", "det-a", 1, None, "2024-01-01T00:00:00Z"),
        PetSummary("pet-b", "Nori", "det-b", 1, None, "2024-01-01T00:00:01Z"),
    ]

    dialog_calls: list[list[str]] = []

    class _FakeDialog:
        def __init__(self, summaries, **_kwargs) -> None:
            dialog_calls.append([summary.person_id for summary in summaries])

        def exec(self) -> int:
            return 0

    monkeypatch.setattr(people_dashboard_widget, "GroupPeopleDialog", _FakeDialog)

    widget._merge_pet(widget._pet_summaries[0])

    assert dialog_calls == [["pet:pet-b"]]


def test_right_click_pet_to_pet_uses_typed_merge_service_once(
    monkeypatch,
    qapp: QApplication,
) -> None:
    widget = PeopleDashboardWidget()
    widget._pet_summaries = [
        PetSummary("pet-a", "Miso", "det-a", 1, None, "2024-01-01T00:00:00Z"),
        PetSummary("pet-b", "Nori", "det-b", 1, None, "2024-01-01T00:00:01Z"),
    ]
    widget._populate_cards()

    class _FakeDialog:
        def __init__(self, _summaries, **_kwargs) -> None:
            pass

        def exec(self) -> int:
            return 1

        def selected_person_ids(self) -> list[str]:
            return ["pet:pet-b"]

    merge = Mock(
        return_value=IdentityMergeOutcome(
            True,
            IdentityRef("pet", "pet-a"),
            IdentityRef("pet", "pet-b"),
            refresh_policy=IdentityMergeRefreshPolicy.SNAPSHOT,
            pet_redirects={"pet-a": "pet-b"},
        )
    )
    monkeypatch.setattr(people_dashboard_widget, "GroupPeopleDialog", _FakeDialog)
    monkeypatch.setattr(MergeConfirmDialog, "confirm", staticmethod(lambda *_args: True))
    monkeypatch.setattr(widget._merge_service, "merge", merge)
    monkeypatch.setattr(widget, "reload", Mock())

    widget._merge_pet(widget._pet_summaries[0])

    merge.assert_called_once_with("pet:pet-a", "pet:pet-b")
    widget.reload.assert_not_called()
    assert [card.identity_key for card in widget._board.visible_cards()] == ["pet:pet-b"]


def test_merge_pet_choices_include_different_hidden_state(qapp: QApplication) -> None:
    widget = PeopleDashboardWidget()
    widget._pet_summaries = [
        PetSummary("pet-a", "Miso", "det-a", 1, None, "2024-01-01T00:00:00Z", True),
        PetSummary("pet-b", "Nori", "det-b", 1, None, "2024-01-01T00:00:01Z", False),
    ]

    choices = widget._merge_choices("pet", "pet-a")

    assert [choice.person_id for choice in choices] == ["pet:pet-b"]


@pytest.mark.parametrize(
    ("name", "species", "expected"),
    [
        (None, "cat", ""),
        (None, "dog", ""),
        (None, None, ""),
        ("Miso", "cat", "Miso"),
    ],
)
def test_pet_card_has_no_unconfirmed_placeholder_label(
    qapp: QApplication,
    name: str | None,
    species: str | None,
    expected: str,
) -> None:
    widget = PeopleDashboardWidget()
    card = people_dashboard_cards.PetCard(
        board=widget._board,
        summary=PetSummary(
            "pet-a",
            name,
            "det-a",
            1,
            None,
            "2024-01-01T00:00:00Z",
            profile_state="unstable",
            species_label=species,
        ),
        seed_index=0,
    )

    assert card.display_name() == expected


def test_pet_card_profile_state_does_not_change_rendering(qapp: QApplication) -> None:
    widget = PeopleDashboardWidget()

    def _render_card(profile_state: str) -> QImage:
        card = people_dashboard_cards.PetCard(
            board=widget._board,
            summary=PetSummary(
                "pet-a",
                None,
                "det-a",
                3,
                None,
                "2024-01-01T00:00:00Z",
                asset_count=3,
                profile_state=profile_state,
                species_label="cat",
            ),
            seed_index=0,
        )
        artwork = QPixmap(card.size())
        artwork.fill(QColor("#708090"))
        card._artwork = artwork

        card.show()
        qapp.processEvents()
        image = card.grab().toImage()
        card.close()
        return image

    stable_image = _render_card("stable")
    unstable_image = _render_card("unstable")

    assert (
        stable_image.pixelColor(stable_image.width() // 2, stable_image.height() // 2).alpha() > 0
    )
    assert stable_image == unstable_image


def test_toggle_person_hidden_updates_service_and_reloads(monkeypatch, qapp: QApplication) -> None:
    widget = PeopleDashboardWidget()
    widget._query_service = Mock()
    summary = PersonSummary("person-a", "Alice", "face-a", 3, None, "2024-01-01T00:00:00Z")

    toggles: list[tuple[str, bool]] = []
    reloads: list[bool] = []
    monkeypatch.setattr(
        widget._service,
        "set_cluster_hidden",
        lambda person_id, hidden: toggles.append((person_id, hidden)) or True,
    )
    monkeypatch.setattr(
        MergeConfirmDialog,
        "confirm_action",
        classmethod(lambda cls, **_kwargs: True),
    )
    monkeypatch.setattr(
        widget,
        "reload",
        lambda *, preserve_content=False: reloads.append(bool(preserve_content)),
    )

    widget._toggle_person_hidden(summary)

    assert toggles == [("person-a", True)]
    widget._query_service.invalidate.assert_called_once_with()
    assert reloads == [False]


def test_toggle_person_hidden_uses_confirmation_popup(monkeypatch, qapp: QApplication) -> None:
    widget = PeopleDashboardWidget()
    summary = PersonSummary("person-a", "Alice", "face-a", 3, None, "2024-01-01T00:00:00Z")

    confirms: list[tuple[str, str, str]] = []
    toggles: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        MergeConfirmDialog,
        "confirm_action",
        classmethod(
            lambda cls, *, item_count, parent=None, title_text, body_text, confirm_text: (
                confirms.append((title_text, body_text, confirm_text)) or True
            )
        ),
    )
    monkeypatch.setattr(
        widget._service,
        "set_cluster_hidden",
        lambda person_id, hidden: toggles.append((person_id, hidden)) or True,
    )
    monkeypatch.setattr(widget, "reload", lambda **_kwargs: None)

    widget._toggle_person_hidden(summary)

    assert confirms == [
        (
            "Hide This Person?",
            "Hiding Alice will remove them from the People view until you choose Show Hidden People or unhide them.",
            "Hide Person",
        )
    ]
    assert toggles == [("person-a", True)]


def test_disband_group_uses_confirmation_popup_and_deletes(monkeypatch, qapp: QApplication) -> None:
    widget = PeopleDashboardWidget()
    summary = PeopleGroupSummary(
        group_id="group-a",
        name="Alice and Bob",
        member_person_ids=("person-a", "person-b"),
        members=(),
        asset_count=1,
        cover_asset_path=None,
        created_at="2024-01-01T00:00:00Z",
    )

    confirms: list[tuple[str, str, str]] = []
    deletions: list[str] = []
    reloads: list[bool] = []
    monkeypatch.setattr(widget, "_is_group_pinned", lambda _group_id: False)
    monkeypatch.setattr(
        MergeConfirmDialog,
        "confirm_action",
        classmethod(
            lambda cls, *, item_count, parent=None, title_text, body_text, confirm_text: (
                confirms.append((title_text, body_text, confirm_text)) or True
            )
        ),
    )
    monkeypatch.setattr(
        widget._service,
        "delete_group",
        lambda group_id: deletions.append(group_id) or True,
    )
    monkeypatch.setattr(
        widget,
        "reload",
        lambda *, preserve_content=False: reloads.append(bool(preserve_content)),
    )

    widget._disband_group(summary)

    assert confirms == [
        (
            "Disband This Group?",
            "Disbanding Alice and Bob will remove the group but keep all of its people and photos.",
            "Disband Group",
        )
    ]
    assert deletions == ["group-a"]
    assert reloads == [False]


def test_pinned_group_disband_shows_warning_and_does_not_delete(
    monkeypatch, tmp_path: Path, qapp: QApplication
) -> None:
    settings = SettingsManager(path=tmp_path / "settings.json")
    settings.load()
    pinned_service = PinnedItemsService(settings)
    widget = PeopleDashboardWidget()
    widget._current_library_root = tmp_path
    widget._service.set_library_root(tmp_path)
    widget.set_pinned_service(pinned_service)
    pinned_service.pin_group("group-a", "Group 1", library_root=tmp_path)
    summary = PeopleGroupSummary(
        group_id="group-a",
        name="Alice and Bob",
        member_person_ids=("person-a", "person-b"),
        members=(),
        asset_count=1,
        cover_asset_path=None,
        created_at="2024-01-01T00:00:00Z",
    )

    warnings: list[str] = []
    deletions: list[str] = []
    monkeypatch.setattr(
        people_dashboard_widget.dialogs,
        "show_warning",
        lambda _parent, message, title="iPhoto": warnings.append(message),
    )
    monkeypatch.setattr(
        widget._service,
        "delete_group",
        lambda group_id: deletions.append(group_id) or True,
    )

    widget._disband_group(summary)

    assert warnings == ["Pinned groups can't be disbanded until they are unpinned."]
    assert deletions == []


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

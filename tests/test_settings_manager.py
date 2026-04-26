from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for settings tests", exc_type=ImportError)
pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets not available", exc_type=ImportError)
pytest.importorskip("PySide6.QtTest", reason="Qt test helpers not available", exc_type=ImportError)

from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

import iPhoto.gui.services.pinned_items_service as pinned_items_service_module
from iPhoto.gui.services.pinned_items_service import PinnedItemsService
from iPhoto.settings.manager import SettingsManager


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def test_settings_manager_roundtrip(tmp_path: Path, qapp: QApplication) -> None:
    settings_path = tmp_path / "settings.json"
    manager = SettingsManager(path=settings_path)
    manager.load()
    assert settings_path.exists()
    assert manager.get("basic_library_path") is None
    spy = QSignalSpy(manager.settingsChanged)
    library_path = tmp_path / "Library"
    manager.set("basic_library_path", library_path)
    qapp.processEvents()
    assert spy.count() == 1
    assert manager.get("basic_library_path") == str(library_path)
    stored = json.loads(settings_path.read_text(encoding="utf-8"))
    assert stored["basic_library_path"] == str(library_path)


def test_settings_manager_nested_updates_preserve_defaults(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    manager = SettingsManager(path=settings_path)
    manager.load()
    manager.set("ui.sidebar_width", 320)
    assert manager.get("ui.sidebar_width") == 320
    assert manager.get("ui.theme") == "system"
    assert manager.get("ui.show_map_extension_startup_prompt") is True


def test_pinned_items_roundtrip_is_scoped_by_library(tmp_path: Path, qapp: QApplication) -> None:
    settings_path = tmp_path / "settings.json"
    manager = SettingsManager(path=settings_path)
    manager.load()
    service = PinnedItemsService(manager)
    spy = QSignalSpy(service.changed)

    library_a = tmp_path / "LibraryA"
    library_b = tmp_path / "LibraryB"
    library_a.mkdir()
    library_b.mkdir()

    service.pin_album(library_a / "Trips", "Trips", library_root=library_a)
    service.pin_person("person-a", "Alice", library_root=library_a)
    service.pin_group("group-a", "Group 1", library_root=library_b)
    qapp.processEvents()

    assert spy.count() == 3
    assert [(item.kind, item.label) for item in service.items_for_library(library_a)] == [
        ("person", "Alice"),
        ("album", "Trips"),
    ]
    assert [(item.kind, item.label) for item in service.items_for_library(library_b)] == [
        ("group", "Group 1"),
    ]

    stored = json.loads(settings_path.read_text(encoding="utf-8"))
    assert "pinned_items_by_library" in stored


def test_pinned_item_rename_persists_custom_label_flag(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    manager = SettingsManager(path=settings_path)
    manager.load()
    service = PinnedItemsService(manager)

    library_root = tmp_path / "Library"
    library_root.mkdir()

    service.pin_person("person-a", "Alice", library_root=library_root)
    assert service.rename_item(
        kind="person",
        item_id="person-a",
        label="VIP Alice",
        library_root=library_root,
    )

    items = service.items_for_library(library_root)
    assert len(items) == 1
    assert items[0].label == "VIP Alice"
    assert items[0].custom_label is True

    stored = json.loads(settings_path.read_text(encoding="utf-8"))
    library_key = str(library_root.resolve())
    assert stored["pinned_items_by_library"][library_key][0]["custom_label"] is True


def test_pinned_album_path_remap_preserves_custom_label(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    manager = SettingsManager(path=settings_path)
    manager.load()
    service = PinnedItemsService(manager)

    library_root = tmp_path / "Library"
    old_album = library_root / "Trips"
    new_album = library_root / "Renamed Trips"
    old_album.mkdir(parents=True)
    new_album.mkdir()

    service.pin_album(old_album, "Trips", library_root=library_root)
    assert service.rename_item(
        kind="album",
        item_id=old_album,
        label="Best Trips",
        library_root=library_root,
    )

    assert service.remap_album_path(
        old_album,
        new_album,
        library_root=library_root,
        fallback_label="Renamed Trips",
    )

    items = service.items_for_library(library_root)
    assert len(items) == 1
    assert items[0].kind == "album"
    assert items[0].item_id == str(new_album.resolve())
    assert items[0].label == "Best Trips"
    assert items[0].custom_label is True


def test_pinned_child_album_path_remaps_when_parent_is_renamed(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    manager = SettingsManager(path=settings_path)
    manager.load()
    service = PinnedItemsService(manager)

    library_root = tmp_path / "Library"
    old_parent = library_root / "Trips"
    old_child = old_parent / "Paris"
    new_parent = library_root / "Renamed Trips"
    new_child = new_parent / "Paris"
    old_child.mkdir(parents=True)
    new_child.mkdir(parents=True)

    service.pin_album(old_child, "Paris", library_root=library_root)

    assert service.remap_album_path(
        old_parent,
        new_parent,
        library_root=library_root,
        fallback_label="Renamed Trips",
    )

    items = service.items_for_library(library_root)
    assert len(items) == 1
    assert items[0].kind == "album"
    assert items[0].item_id == str(new_child.resolve())
    assert items[0].label == "Paris"
    assert items[0].custom_label is False


def test_prune_missing_people_entities_removes_only_stale_items(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    settings_path = tmp_path / "settings.json"
    manager = SettingsManager(path=settings_path)
    manager.load()
    service = PinnedItemsService(manager)
    spy = QSignalSpy(service.changed)

    library_root = tmp_path / "Library"
    library_root.mkdir()
    service.pin_person("person-stale", "Ghost", library_root=library_root)
    service.pin_person("person-ok", "Alice", library_root=library_root)
    service.pin_group("group-stale", "Old Group", library_root=library_root)
    service.pin_group("group-ok", "New Group", library_root=library_root)
    qapp.processEvents()

    class _StubPeopleService:
        def __init__(self, library_root: Path) -> None:
            self.library_root = library_root

        def has_cluster(self, person_id: str) -> bool:
            return person_id != "person-stale"

        def has_group(self, group_id: str) -> bool:
            return group_id != "group-stale"

    monkeypatch.setattr(pinned_items_service_module, "PeopleService", _StubPeopleService)

    assert service.prune_missing_people_entities(
        library_root,
        person_ids=("person-stale", "person-ok"),
        group_ids=("group-stale", "group-ok"),
    )
    qapp.processEvents()

    items = {(item.kind, item.item_id) for item in service.items_for_library(library_root)}
    assert ("person", "person-stale") not in items
    assert ("group", "group-stale") not in items
    assert ("person", "person-ok") in items
    assert ("group", "group-ok") in items
    assert spy.count() == 5

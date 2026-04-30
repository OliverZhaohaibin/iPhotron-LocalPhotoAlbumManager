import json
import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for tree tests", exc_type=ImportError)
pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets not available", exc_type=ImportError)

from PySide6.QtCore import QModelIndex
from PySide6.QtWidgets import QApplication

import iPhoto.gui.ui.models.album_tree_model as album_tree_model_module
from iPhoto.gui.services.pinned_items_service import PinnedItemsService
from iPhoto.gui.ui.models.album_tree_model import AlbumTreeModel, AlbumTreeRole, NodeType
from iPhoto.library.manager import LibraryManager
from iPhoto.settings.manager import SettingsManager


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def _create_album(root: Path, title: str, *, child: str | None = None) -> Path:
    album_dir = root / title
    album_dir.mkdir(parents=True, exist_ok=True)
    manifest = album_dir / ".iphoto.album.json"
    manifest.write_text(json.dumps({"schema": "iPhoto/album@1", "title": title}), encoding="utf-8")
    if child is not None:
        child_dir = album_dir / child
        child_dir.mkdir(parents=True, exist_ok=True)
        (child_dir / ".iphoto.album").touch()
        return child_dir
    return album_dir


def _find_child(model: AlbumTreeModel, parent_index, title: str):
    for row in range(model.rowCount(parent_index)):
        index = model.index(row, 0, parent_index)
        if model.data(index) == title:
            return index
    return None


def test_placeholder_when_unbound(qapp: QApplication) -> None:
    manager = LibraryManager()
    model = AlbumTreeModel(manager)
    qapp.processEvents()
    assert model.rowCount() == 1
    index = model.index(0, 0)
    assert model.data(index) == "Bind Basic Library…"
    assert model.data(index, AlbumTreeRole.NODE_TYPE) == NodeType.ACTION


def test_model_populates_albums(tmp_path: Path, qapp: QApplication) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    album_dir = _create_album(root, "Trip", child="Day1")
    manager = LibraryManager()
    manager.bind_path(root)
    qapp.processEvents()
    model = AlbumTreeModel(manager)
    qapp.processEvents()

    header_index = model.index(0, 0)
    assert model.data(header_index) == "Basic Library"
    people_index = _find_child(model, header_index, "People")
    location_index = _find_child(model, header_index, "Location")
    assert people_index is not None
    assert location_index is not None
    assert people_index.row() < location_index.row()

    # Albums is now promoted to a header-level entry, therefore it must be
    # discovered directly under the root model index instead of under the
    # "Basic Library" header. Keeping the test explicit ensures the hierarchy
    # change remains intentional and prevents regressions back to the nested
    # layout when the model refresh logic evolves.
    albums_index = _find_child(model, QModelIndex(), "Albums")
    assert albums_index is not None
    # Validating the node type ensures the delegate will render the correct font
    # weight and icon treatment associated with header entries, which was the
    # original motivation for promoting the section.
    assert model.data(albums_index, AlbumTreeRole.NODE_TYPE) == NodeType.HEADER
    albums_item = model.item_from_index(albums_index)
    assert albums_item is not None
    # The Albums header must reference the dedicated folder SVG so the sidebar
    # renders the platform-consistent icon instead of the generic bookshelf used
    # by the "Basic Library" header.
    assert albums_item.icon_name == "folder.svg"
    trip_index = _find_child(model, albums_index, "Trip")
    assert trip_index is not None
    assert model.data(trip_index, AlbumTreeRole.NODE_TYPE) == NodeType.ALBUM
    child_index = _find_child(model, trip_index, "Day1")
    assert child_index is not None
    assert model.data(child_index, AlbumTreeRole.NODE_TYPE) == NodeType.SUBALBUM

    mapped_index = model.index_for_path(album_dir)
    assert mapped_index.isValid()
    assert model.data(mapped_index) == "Day1"


def test_model_inserts_pinned_section_between_library_and_albums(tmp_path: Path, qapp: QApplication) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    album_dir = _create_album(root, "Trips")
    manager = LibraryManager()
    manager.bind_path(root)
    qapp.processEvents()

    settings = SettingsManager(path=tmp_path / "settings.json")
    settings.load()
    pinned_service = PinnedItemsService(settings)
    pinned_service.pin_album(album_dir, "Trips", library_root=root)
    pinned_service.pin_person("person-a", "Alice", library_root=root)

    model = AlbumTreeModel(manager)
    model.set_pinned_service(pinned_service)
    qapp.processEvents()

    pinned_index = _find_child(model, QModelIndex(), "Pinned")
    albums_index = _find_child(model, QModelIndex(), "Albums")
    assert pinned_index is not None
    assert albums_index is not None
    assert pinned_index.row() < albums_index.row()
    assert model.data(pinned_index, AlbumTreeRole.NODE_TYPE) == NodeType.HEADER

    alice_index = _find_child(model, pinned_index, "Alice")
    trips_index = _find_child(model, pinned_index, "Trips")
    assert alice_index is not None
    assert trips_index is not None
    assert model.data(alice_index, AlbumTreeRole.NODE_TYPE) == NodeType.PINNED_PERSON
    assert model.data(trips_index, AlbumTreeRole.NODE_TYPE) == NodeType.PINNED_ALBUM


def test_model_pinned_album_uses_current_album_title_over_custom_label(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    album_dir = _create_album(root, "Trips")
    manager = LibraryManager()
    manager.bind_path(root)
    qapp.processEvents()

    settings = SettingsManager(path=tmp_path / "settings.json")
    settings.load()
    pinned_service = PinnedItemsService(settings)
    pinned_service.pin_album(album_dir, "Trips", library_root=root)
    assert pinned_service.rename_item(
        kind="album",
        item_id=album_dir,
        label="Best Trips",
        library_root=root,
    )

    model = AlbumTreeModel(manager)
    model.set_pinned_service(pinned_service)
    qapp.processEvents()

    pinned_item = pinned_service.items_for_library(root)[0]
    pinned_index = model.index_for_pinned_item(pinned_item)
    pinned_tree_item = model.item_from_index(pinned_index)

    assert pinned_index.isValid()
    assert pinned_tree_item is not None
    assert pinned_tree_item.album is not None
    assert pinned_tree_item.album.path == album_dir
    assert model.data(pinned_index) == "Trips"


def test_model_omits_pinned_section_when_empty(tmp_path: Path, qapp: QApplication) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    _create_album(root, "Trips")
    manager = LibraryManager()
    manager.bind_path(root)
    qapp.processEvents()

    model = AlbumTreeModel(manager)
    qapp.processEvents()

    pinned_index = _find_child(model, QModelIndex(), "Pinned")
    assert pinned_index is None


def test_model_keeps_missing_pinned_entities_visible_until_clicked(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryManager()
    manager.bind_path(root)
    qapp.processEvents()

    settings = SettingsManager(path=tmp_path / "settings.json")
    settings.load()
    pinned_service = PinnedItemsService(settings)
    pinned_service.pin_album(root / "Missing Album", "Missing Album", library_root=root)
    pinned_service.pin_person("missing-person", "Ghost", library_root=root)
    pinned_service.pin_group("missing-group", "Group 1", library_root=root)

    class _StubPeopleService:
        def __init__(self, library_root: Path) -> None:
            self.library_root = library_root

        def list_clusters(self, *, include_hidden: bool = False):
            return []

        def list_groups(self, *, repository=None, summaries=None):
            return []

    monkeypatch.setattr(album_tree_model_module, "PeopleService", _StubPeopleService)

    model = AlbumTreeModel(manager)
    model.set_pinned_service(pinned_service)
    qapp.processEvents()

    pinned_index = _find_child(model, QModelIndex(), "Pinned")
    assert pinned_index is not None
    assert _find_child(model, pinned_index, "Missing Album") is not None
    assert _find_child(model, pinned_index, "Ghost") is not None
    assert _find_child(model, pinned_index, "Group 1") is not None

import base64
import json
import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for sidebar tests", exc_type=ImportError)
pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets not available", exc_type=ImportError)

from PySide6.QtWidgets import QApplication

from iPhoto.gui.services.pinned_items_service import PinnedItemsService
from iPhoto.gui.ui.menus.album_sidebar_menu import AlbumSidebarContextMenu
from iPhoto.gui.ui.models.album_tree_model import NodeType
from iPhoto.gui.ui.widgets.album_sidebar import AlbumSidebar
from iPhoto.library.manager import LibraryManager
from iPhoto.settings.manager import SettingsManager


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def _write_manifest(path: Path, title: str) -> None:
    payload = {"schema": "iPhoto/album@1", "title": title, "filters": {}}
    (path / ".iphoto.album.json").write_text(json.dumps(payload), encoding="utf-8")


def test_programmatic_selection_suppresses_signals(tmp_path: Path, qapp: QApplication) -> None:
    """Verify that programmatic selection calls do not emit navigation signals."""
    root = tmp_path / "Library"
    album_dir = root / "Trip"
    album_dir.mkdir(parents=True)
    _write_manifest(album_dir, "Trip")
    manager = LibraryManager()
    manager.bind_path(root)
    qapp.processEvents()

    sidebar = AlbumSidebar(manager)
    # Force the sidebar to process pending events (e.g. tree population)
    qapp.processEvents()

    triggered_all: list[bool] = []
    triggered_static: list[str] = []
    triggered_album: list[Path] = []

    sidebar.allPhotosSelected.connect(lambda: triggered_all.append(True))
    sidebar.staticNodeSelected.connect(lambda title: triggered_static.append(title))
    sidebar.albumSelected.connect(lambda path: triggered_album.append(path))

    # Test: Selecting "All Photos" programmatically
    sidebar.select_all_photos()
    qapp.processEvents()
    assert not triggered_all, "Programmatic All Photos selection must suppress signal"

    # Test: Selecting static node "Videos" programmatically
    sidebar.select_static_node("Videos")
    qapp.processEvents()
    assert not triggered_static, "Programmatic static node selection must suppress signal"

    # Test: Selecting album path programmatically
    sidebar.select_path(album_dir)
    qapp.processEvents()
    assert not triggered_album, "Programmatic album selection must suppress signal"


def test_programmatic_selection_can_emit_signals(tmp_path: Path, qapp: QApplication) -> None:
    """Verify that programmatic selection can optionally emit signals."""
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryManager()
    manager.bind_path(root)
    qapp.processEvents()

    sidebar = AlbumSidebar(manager)
    qapp.processEvents()

    triggered_all: list[bool] = []
    sidebar.allPhotosSelected.connect(lambda: triggered_all.append(True))

    # Test: Selecting "All Photos" programmatically with signals enabled
    sidebar.select_all_photos(emit_signal=True)
    qapp.processEvents()
    assert triggered_all, "Programmatic All Photos selection should emit signal when requested"


def test_programmatic_pinned_selection_can_emit_signal(tmp_path: Path, qapp: QApplication) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryManager()
    manager.bind_path(root)
    qapp.processEvents()

    settings = SettingsManager(path=tmp_path / "settings.json")
    settings.load()
    pinned_service = PinnedItemsService(settings)
    pinned_service.pin_person("person-a", "Alice", library_root=root)

    sidebar = AlbumSidebar(manager)
    sidebar.set_pinned_service(pinned_service)
    qapp.processEvents()

    emitted: list[object] = []
    sidebar.pinnedItemSelected.connect(emitted.append)

    item = pinned_service.items_for_library(root)[0]
    sidebar.select_pinned_item(item, emit_signal=True)
    qapp.processEvents()

    assert len(emitted) == 1
    assert emitted[0].kind == "person"
    assert emitted[0].item_id == "person-a"


def test_sidebar_album_context_menu_offers_pin_and_unpin(tmp_path: Path, qapp: QApplication) -> None:
    root = tmp_path / "Library"
    album_dir = root / "Trip"
    album_dir.mkdir(parents=True)
    _write_manifest(album_dir, "Trip")

    manager = LibraryManager()
    manager.bind_path(root)
    qapp.processEvents()

    settings = SettingsManager(path=tmp_path / "settings.json")
    settings.load()
    pinned_service = PinnedItemsService(settings)

    sidebar = AlbumSidebar(manager)
    sidebar.set_pinned_service(pinned_service)
    qapp.processEvents()

    album_index = sidebar.tree_model().index_for_path(album_dir)
    item = sidebar.tree_model().item_from_index(album_index)
    assert item is not None
    assert item.node_type == NodeType.ALBUM

    menu = AlbumSidebarContextMenu(
        sidebar,
        sidebar._tree,
        sidebar.tree_model(),
        manager,
        item,
        sidebar._set_pending_selection,
        sidebar.bindLibraryRequested.emit,
    )
    assert menu.actions()[0].text() == "Pin Album"

    menu.actions()[0].trigger()
    qapp.processEvents()
    assert pinned_service.is_pinned(kind="album", item_id=str(album_dir), library_root=root)

    menu = AlbumSidebarContextMenu(
        sidebar,
        sidebar._tree,
        sidebar.tree_model(),
        manager,
        item,
        sidebar._set_pending_selection,
        sidebar.bindLibraryRequested.emit,
    )
    assert menu.actions()[0].text() == "Unpin Album"


def test_sidebar_pinned_album_survives_album_rename(tmp_path: Path, qapp: QApplication) -> None:
    root = tmp_path / "Library"
    album_dir = root / "Trip"
    album_dir.mkdir(parents=True)
    _write_manifest(album_dir, "Trip")

    manager = LibraryManager()
    manager.bind_path(root)
    qapp.processEvents()

    settings = SettingsManager(path=tmp_path / "settings.json")
    settings.load()
    pinned_service = PinnedItemsService(settings)
    pinned_service.pin_album(album_dir, "Trip", library_root=root)

    sidebar = AlbumSidebar(manager)
    sidebar.set_pinned_service(pinned_service)
    manager.albumRenamed.connect(
        lambda old, new: pinned_service.remap_album_path(
            old,
            new,
            library_root=root,
            fallback_label=new.name,
        )
    )
    qapp.processEvents()

    album = next(node for node in manager.list_albums() if node.path == album_dir)
    manager.rename_album(album, "Renamed Trip")
    qapp.processEvents()

    new_album = root / "Renamed Trip"
    pinned = pinned_service.items_for_library(root)
    assert len(pinned) == 1
    assert pinned[0].item_id == str(new_album.resolve())

    refreshed_index = sidebar.tree_model().index_for_pinned_item(pinned[0])
    refreshed_item = sidebar.tree_model().item_from_index(refreshed_index)
    assert refreshed_item is not None
    assert refreshed_item.node_type == NodeType.PINNED_ALBUM
    assert refreshed_item.album is not None
    assert refreshed_item.album.path == new_album
    assert sidebar.tree_model().data(refreshed_index) == "Renamed Trip"


def test_sidebar_pinned_item_context_menu_offers_unpin(tmp_path: Path, qapp: QApplication) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryManager()
    manager.bind_path(root)
    qapp.processEvents()

    settings = SettingsManager(path=tmp_path / "settings.json")
    settings.load()
    pinned_service = PinnedItemsService(settings)
    pinned_service.pin_person("person-a", "Alice", library_root=root)

    sidebar = AlbumSidebar(manager)
    sidebar.set_pinned_service(pinned_service)
    qapp.processEvents()

    pinned_item = pinned_service.items_for_library(root)[0]
    pinned_index = sidebar.tree_model().index_for_pinned_item(pinned_item)
    item = sidebar.tree_model().item_from_index(pinned_index)
    assert item is not None
    assert item.node_type == NodeType.PINNED_PERSON

    menu = AlbumSidebarContextMenu(
        sidebar,
        sidebar._tree,
        sidebar.tree_model(),
        manager,
        item,
        sidebar._set_pending_selection,
        sidebar.bindLibraryRequested.emit,
    )
    assert [action.text() for action in menu.actions() if not action.isSeparator()] == [
        "Rename…",
        "Unpin",
    ]

    menu.actions()[-1].trigger()
    qapp.processEvents()
    assert not pinned_service.items_for_library(root)


def test_sidebar_pinned_item_context_menu_can_rename(tmp_path: Path, qapp: QApplication) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryManager()
    manager.bind_path(root)
    qapp.processEvents()

    settings = SettingsManager(path=tmp_path / "settings.json")
    settings.load()
    pinned_service = PinnedItemsService(settings)
    pinned_service.pin_person("person-a", "Alice", library_root=root)

    sidebar = AlbumSidebar(manager)
    sidebar.set_pinned_service(pinned_service)
    qapp.processEvents()

    pinned_item = pinned_service.items_for_library(root)[0]
    pinned_index = sidebar.tree_model().index_for_pinned_item(pinned_item)
    item = sidebar.tree_model().item_from_index(pinned_index)
    assert item is not None

    from unittest.mock import patch

    with patch(
        "iPhoto.gui.ui.menus.album_sidebar_menu._create_styled_input_dialog",
        return_value=("VIP Alice", True),
    ):
        menu = AlbumSidebarContextMenu(
            sidebar,
            sidebar._tree,
            sidebar.tree_model(),
            manager,
            item,
            sidebar._set_pending_selection,
            sidebar.bindLibraryRequested.emit,
        )
        menu.actions()[0].trigger()

    qapp.processEvents()

    renamed = pinned_service.items_for_library(root)[0]
    assert renamed.label == "VIP Alice"
    assert renamed.custom_label is True

    refreshed_index = sidebar.tree_model().index_for_pinned_item(renamed)
    assert refreshed_index.isValid()
    assert sidebar.tree_model().data(refreshed_index) == "VIP Alice"

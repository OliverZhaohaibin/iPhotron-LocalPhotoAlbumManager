import json
import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for sidebar tests", exc_type=ImportError)
pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets not available", exc_type=ImportError)

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from iPhoto.gui.i18n import TranslationManager, font_policy
from iPhoto.gui.services.pinned_items_service import PinnedItemsService
from iPhoto.gui.ui.delegates.album_sidebar_delegate import AlbumSidebarDelegate
from iPhoto.gui.ui.menus.album_sidebar_menu import AlbumSidebarContextMenu
from iPhoto.gui.ui.models.album_tree_model import NodeType
from iPhoto.gui.ui.widgets.album_sidebar import AlbumSidebar
from iPhoto.library.runtime_controller import LibraryRuntimeController
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


def _settings(tmp_path: Path) -> SettingsManager:
    settings = SettingsManager(path=tmp_path / "settings.json")
    settings.load()
    return settings


def test_programmatic_selection_suppresses_signals(tmp_path: Path, qapp: QApplication) -> None:
    """Verify that programmatic selection calls do not emit navigation signals."""
    root = tmp_path / "Library"
    album_dir = root / "Trip"
    album_dir.mkdir(parents=True)
    _write_manifest(album_dir, "Trip")
    manager = LibraryRuntimeController()
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


def test_sidebar_retranslate_updates_title_and_menu_labels(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    root = tmp_path / "Library"
    album_dir = root / "Trip"
    album_dir.mkdir(parents=True)
    _write_manifest(album_dir, "Trip")
    manager = LibraryRuntimeController()
    manager.bind_path(root)
    qapp.processEvents()

    translations = TranslationManager(_settings(tmp_path))
    translations.apply_language("zh-CN")
    sidebar = AlbumSidebar(manager)
    qapp.processEvents()
    sidebar.retranslate_ui()

    assert sidebar._title.text() == "基础图库"

    album_index = sidebar.tree_model().index_for_path(album_dir)
    item = sidebar.tree_model().item_from_index(album_index)
    assert item is not None
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
        "固定相册",
        "新建子相册…",
        "重命名相册…",
        "在文件管理器中显示",
    ]

    translations._remove_installed_translator(qapp)


def test_sidebar_existing_widgets_sync_windows_chinese_font(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryRuntimeController()
    manager.bind_path(root)
    qapp.processEvents()

    original_font = QFont(qapp.font())
    font_policy._STATE.app_id = None
    font_policy._STATE.original_font = None
    font_policy._STATE.applied_family = None
    monkeypatch.setattr(font_policy.sys, "platform", "win32")
    monkeypatch.setattr(font_policy, "_available_font_families", lambda: ["微软雅黑"])
    sidebar = AlbumSidebar(manager)
    translations = TranslationManager(_settings(tmp_path))

    try:
        translations.apply_language("zh-CN")
        sidebar.retranslate_ui()
        qapp.processEvents()

        assert sidebar._title.font().family() == "微软雅黑"
        assert sidebar._tree.font().family() == "微软雅黑"
        delegate = sidebar._tree.itemDelegate()
        assert isinstance(delegate, AlbumSidebarDelegate)
        assert delegate._font_for_node(QFont("Segoe UI", 12), NodeType.HEADER).family() == "微软雅黑"
    finally:
        sidebar.close()
        translations.apply_language("de")
        translations._remove_installed_translator(qapp)
        qapp.setFont(original_font)
        font_policy._STATE.app_id = None
        font_policy._STATE.original_font = None
        font_policy._STATE.applied_family = None


def test_programmatic_selection_can_emit_signals(tmp_path: Path, qapp: QApplication) -> None:
    """Verify that programmatic selection can optionally emit signals."""
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryRuntimeController()
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
    manager = LibraryRuntimeController()
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

    manager = LibraryRuntimeController()
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

    manager = LibraryRuntimeController()
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


def test_sidebar_pinned_album_rename_updates_album_tree(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    root = tmp_path / "Library"
    album_dir = root / "Trip"
    album_dir.mkdir(parents=True)
    _write_manifest(album_dir, "Trip")

    manager = LibraryRuntimeController()
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

    pinned_item = pinned_service.items_for_library(root)[0]
    pinned_index = sidebar.tree_model().index_for_pinned_item(pinned_item)
    item = sidebar.tree_model().item_from_index(pinned_index)
    assert item is not None
    assert item.node_type == NodeType.PINNED_ALBUM
    assert item.album is not None

    from unittest.mock import patch

    with patch(
        "iPhoto.gui.ui.menus.album_sidebar_menu._create_styled_input_dialog",
        return_value=("Renamed Trip", True),
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
        actions = [action for action in menu.actions() if not action.isSeparator()]
        assert [action.text() for action in actions] == ["Rename Album…", "Unpin"]
        actions[0].trigger()

    qapp.processEvents()
    qapp.processEvents()

    new_album = root / "Renamed Trip"
    assert not album_dir.exists()
    assert new_album.exists()

    renamed_pinned = pinned_service.items_for_library(root)[0]
    assert renamed_pinned.item_id == str(new_album.resolve())

    refreshed_pinned_index = sidebar.tree_model().index_for_pinned_item(renamed_pinned)
    assert sidebar.tree_model().data(refreshed_pinned_index) == "Renamed Trip"

    albums_index = sidebar.tree_model().index_for_path(new_album)
    assert albums_index.isValid()
    assert sidebar.tree_model().data(albums_index) == "Renamed Trip"


def test_sidebar_pinned_item_context_menu_offers_unpin(tmp_path: Path, qapp: QApplication) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryRuntimeController()
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
    manager = LibraryRuntimeController()
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

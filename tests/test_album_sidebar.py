import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for sidebar tests", exc_type=ImportError)
pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets not available", exc_type=ImportError)

from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionViewItem

from iPhoto.gui.i18n import TranslationManager, font_policy
from iPhoto.gui.coordinators.navigation_coordinator import NavigationCoordinator
from iPhoto.gui.coordinators.desktop_coordinator_runtime import DesktopCoordinatorRuntime
from iPhoto.gui.services.pinned_items_service import PinnedItemsService
from iPhoto.gui.ui.delegates.album_sidebar_delegate import (
    SIDEBAR_ICON_STROKE_WIDTH,
    AlbumSidebarDelegate,
)
from iPhoto.gui.ui.icon import load_icon
from iPhoto.gui.ui.menus.album_sidebar_menu import AlbumSidebarContextMenu
from iPhoto.gui.ui.models.album_tree_model import NodeType
from iPhoto.gui.ui.widgets.album_sidebar import AlbumSidebar
from iPhoto.gui.ui.widgets.people_dashboard import PeopleDashboardWidget
from iPhoto.gui.viewmodels.gallery_viewmodel import GalleryViewModel
from iPhoto.library.runtime_controller import LibraryRuntimeController
from iPhoto.pets.records import PetSummary
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
    sidebar = AlbumSidebar(manager)
    sidebar.select_static_node("People")
    assert sidebar._tree.currentIndex().data() == "People&pets"

    translations.apply_language("zh-CN")
    qapp.processEvents()
    sidebar.retranslate_ui()

    assert sidebar._title.text() == "基础图库"
    assert sidebar._tree.currentIndex().data() == "人物与宠物"

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


@pytest.mark.parametrize("kind", ["album", "person", "group"])
def test_programmatic_pinned_selection_can_emit_signal(
    tmp_path: Path, qapp: QApplication, kind: str
) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryRuntimeController()
    manager.bind_path(root)
    qapp.processEvents()

    settings = SettingsManager(path=tmp_path / "settings.json")
    settings.load()
    pinned_service = PinnedItemsService(settings)
    if kind == "album":
        target = root / "Trip"
        target.mkdir()
        pinned_service.pin_album(target, "Trip", library_root=root)
        item_id = str(target.resolve())
    else:
        item_id = f"{kind}-a"
        getattr(pinned_service, f"pin_{kind}")(item_id, "Saved item", library_root=root)

    sidebar = AlbumSidebar(manager)
    sidebar.set_pinned_service(pinned_service)
    qapp.processEvents()

    emitted: list[object] = []
    sidebar.pinnedItemSelected.connect(emitted.append)

    item = pinned_service.items_for_library(root)[0]
    sidebar.select_pinned_item(item, emit_signal=True)
    qapp.processEvents()

    assert len(emitted) == 1
    assert emitted[0].kind == kind
    assert emitted[0].item_id == item_id


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


@pytest.mark.parametrize("pin_child", [False, True])
def test_renamed_pinned_album_does_not_leave_old_directory_or_selection(
    tmp_path: Path, qapp: QApplication, pin_child: bool
) -> None:
    from iPhoto.library.workers.scanner_worker import ScannerSignals, ScannerWorker

    root = tmp_path / "Library"
    old = root / "Trip"
    child = old / "Day1"
    child.mkdir(parents=True)
    manager = LibraryRuntimeController()
    try:
        manager.bind_path(root)
        pinned_service = PinnedItemsService(_settings(tmp_path))
        pinned_service.pin_album(child if pin_child else old, "Trip", library_root=root)
        sidebar = AlbumSidebar(manager)
        sidebar.set_pinned_service(pinned_service)
        manager.albumRenamed.connect(lambda old, new: pinned_service.remap_album_path(
            old, new, library_root=root, fallback_label=new.name
        ))
        sidebar.select_pinned_item(pinned_service.items_for_library(root)[0])
        pending_scan = ScannerWorker(
            old, [], [], ScannerSignals(),
            scan_service=manager.scan_service,
            finalize_scan=True,
        )

        manager.rename_album(manager.list_albums()[0], "Renamed Trip")
        pending_scan.run()
        assert pending_scan.failed
        manager._refresh_tree()
        qapp.processEvents()

        new = root / "Renamed Trip"
        pinned = pinned_service.items_for_library(root)
        assert len(pinned) == 1
        assert pinned[0].item_id == str((new / "Day1" if pin_child else new).resolve())
        assert not old.exists()
        assert not sidebar.tree_model().index_for_path(old).isValid()
        index = sidebar.tree_model().index_for_pinned_item(pinned[0])
        assert index.isValid()
        assert sidebar._tree.currentIndex() == index
    finally:
        manager.shutdown()


@pytest.mark.parametrize("pinned", [False, True])
@pytest.mark.parametrize("child", [False, True])
def test_rename_menu_preserves_the_displayed_album_section_after_later_refreshes(
    tmp_path: Path, qapp: QApplication, pinned: bool, child: bool
) -> None:
    root = tmp_path / "Library"
    old = root / "Trip"
    (old / "Day1").mkdir(parents=True)
    manager = LibraryRuntimeController()
    sidebar = None
    try:
        manager.bind_path(root)
        pins = PinnedItemsService(_settings(tmp_path))
        active = old / "Day1" if child else old
        pins.pin_album(active, active.name, library_root=root)
        sidebar = AlbumSidebar(manager)
        sidebar.set_pinned_service(pins)
        context = SimpleNamespace(library=manager, remember_album=lambda _path: None)
        facade = MagicMock()
        facade.current_album = None
        facade.open_album.side_effect = lambda path: SimpleNamespace(root=path)
        vm = GalleryViewModel(
            store=MagicMock(), context=context, facade=facade, asset_state_service=None,
        )
        navigation = NavigationCoordinator(sidebar, MagicMock(), vm, context, facade, pins)
        runtime = DesktopCoordinatorRuntime.__new__(DesktopCoordinatorRuntime)
        runtime._context = context
        runtime._thumbnail_service = MagicMock()
        runtime._pinned_items_service = pins
        runtime._gallery_vm = vm
        manager.albumRenamed.connect(runtime._on_album_renamed)
        if pinned:
            index = sidebar.tree_model().index_for_pinned_item(pins.items_for_library(root)[0])
        else:
            index = sidebar.tree_model().index_for_path(active)
        # Exercise the real Qt selection signal and queued navigation.
        sidebar._tree.setCurrentIndex(index)
        qapp.processEvents()
        expected_section = "pinned_album" if pinned else "album"
        assert vm.current_section.value == expected_section
        assert vm.active_root.value == active
        target = sidebar.tree_model().index_for_path(old)
        menu = AlbumSidebarContextMenu(
            sidebar, sidebar._tree, sidebar.tree_model(), manager,
            sidebar.tree_model().item_from_index(target),
            sidebar._set_pending_selection, sidebar.bindLibraryRequested.emit,
        )
        with patch(
            "iPhoto.gui.ui.menus.album_sidebar_menu._create_styled_input_dialog",
            return_value=("Renamed Trip", True),
        ):
            menu._prompt_rename_album(menu._item)
        renamed = root / "Renamed Trip"
        expected_path = renamed / "Day1" if child else renamed
        for _ in range(2):
            sidebar.refresh_tree_model()
            qapp.processEvents()
            current = sidebar.tree_model().item_from_index(sidebar._tree.currentIndex())
            assert current is not None
            assert current.node_type == (
                NodeType.PINNED_ALBUM if pinned else NodeType.SUBALBUM if child else NodeType.ALBUM
            )
            assert current.album.path == expected_path
            assert vm.current_section.value == expected_section
            assert vm.active_root.value == expected_path
            assert sidebar._tree.selectionModel().selectedIndexes() == [sidebar._tree.currentIndex()]
        assert navigation is not None
    finally:
        if sidebar is not None:
            sidebar.close()
        manager.shutdown()


@pytest.mark.parametrize("title", [
    "All Photos", "Videos", "Live Photos", "Favorites", "People", "Location",
    "Albums", "Recently Deleted",
])
@pytest.mark.parametrize("previous", ["album", "pinned"])
def test_album_rename_restores_the_current_builtin_section_after_user_navigation(
    tmp_path: Path, qapp: QApplication, title: str, previous: str
) -> None:
    root = tmp_path / "Library"
    old = root / "Trip"
    old.mkdir(parents=True)
    manager = LibraryRuntimeController()
    sidebar = None
    try:
        manager.bind_path(root)
        pins = PinnedItemsService(_settings(tmp_path))
        pins.pin_album(old, "Trip", library_root=root)
        sidebar = AlbumSidebar(manager)
        sidebar.set_pinned_service(pins)
        manager.albumRenamed.connect(lambda source, target: pins.remap_album_path(
            source, target, library_root=root, fallback_label=target.name,
        ))
        if previous == "pinned":
            sidebar.select_pinned_item(pins.items_for_library(root)[0])
        else:
            sidebar.select_path(old)

        def find_builtin(parent=None):
            from PySide6.QtCore import QModelIndex
            parent = parent if parent is not None else QModelIndex()
            for row in range(sidebar.tree_model().rowCount(parent)):
                index = sidebar.tree_model().index(row, 0, parent)
                item = sidebar.tree_model().item_from_index(index)
                if item.title == title and item.node_type in {NodeType.STATIC, NodeType.HEADER}:
                    return index
                nested = find_builtin(index)
                if nested.isValid():
                    return nested
            return QModelIndex()

        index = find_builtin()
        assert index.isValid()
        emitted = []
        sidebar.staticNodeSelected.connect(emitted.append)
        sidebar.allPhotosSelected.connect(lambda: emitted.append("All Photos"))
        sidebar._tree.setCurrentIndex(index)
        assert emitted == [title]
        manager.rename_album(manager.list_albums()[0], "Renamed Trip")
        for _ in range(2):
            sidebar.refresh_tree_model()
            qapp.processEvents()
            current = sidebar.tree_model().item_from_index(sidebar._tree.currentIndex())
            assert current is not None
            assert current.title == title
            assert emitted == [title]
            assert sidebar._tree.selectionModel().selectedIndexes() == [sidebar._tree.currentIndex()]
    finally:
        if sidebar is not None:
            sidebar.close()
        manager.shutdown()


def test_pet_dashboard_menu_pins_a_selectable_sidebar_item(tmp_path: Path, qapp: QApplication) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryRuntimeController()
    manager.bind_path(root)
    pins = PinnedItemsService(_settings(tmp_path))
    dashboard = PeopleDashboardWidget()
    dashboard.set_services(manager.people_service, manager.pet_service, pins, reload=False)
    sidebar = AlbumSidebar(manager)
    sidebar.set_pinned_service(pins)
    summary = PetSummary("pet-a", "Miso", "det-a", 2, None, "2024-01-01T00:00:00Z")
    selected_items = []
    sidebar.pinnedItemSelected.connect(selected_items.append)

    try:
        menu = dashboard._build_pet_menu(summary)
        action = next(action for action in menu.actions() if action.data() == "toggle_pet_pin")
        assert action.text() == "Pin"
        action.trigger()

        (pinned_item,) = pins.items_for_library(root)
        assert (pinned_item.kind, pinned_item.item_id, pinned_item.label) == ("pet", "pet-a", "Miso")
        model = sidebar.tree_model()
        index = model.index_for_pinned_item(pinned_item)
        assert index.isValid()
        assert model.item_from_index(index).node_type == NodeType.PINNED_PET
        assert model.data(index) == "Miso"

        sidebar._tree.setCurrentIndex(index)
        qapp.processEvents()
        assert selected_items == [pinned_item]

        menu = dashboard._build_pet_menu(summary)
        action = next(action for action in menu.actions() if action.data() == "toggle_pet_pin")
        assert action.text() == "Unpin"
        action.trigger()

        assert not pins.items_for_library(root)
        assert not model.index_for_pinned_item(pinned_item).isValid()
    finally:
        dashboard.shutdown()
        dashboard.close()
        sidebar.close()
        manager.shutdown()


def test_pinned_pet_sidebar_uses_outline_and_filled_pawprint(
    tmp_path: Path, qapp: QApplication
) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryRuntimeController()
    manager.bind_path(root)
    pins = PinnedItemsService(_settings(tmp_path))
    pins.pin_pet("pet-a", "Miso", library_root=root)
    sidebar = AlbumSidebar(manager)
    sidebar.set_pinned_service(pins)

    try:
        (item,) = pins.items_for_library(root)
        index = sidebar.tree_model().index_for_pinned_item(item)
        assert index.isValid()
        tree = sidebar._tree
        delegate = tree.itemDelegate()
        assert isinstance(delegate, AlbumSidebarDelegate)
        colour = tree.palette().color(QPalette.ColorRole.Text).name(QColor.NameFormat.HexArgb)
        option = QStyleOptionViewItem()
        option.state = QStyle.StateFlag.State_Enabled
        for selected, asset in ((False, "pawprint.svg"), (True, "pawprint.fill.svg")):
            if selected:
                option.state |= QStyle.StateFlag.State_Selected
            state = delegate._build_paint_state(option, index, option.rect, NodeType.PINNED_PET)
            icon = delegate._get_icon_for_paint_state(index, state)
            expected = load_icon(asset, color=colour, stroke_width=SIDEBAR_ICON_STROKE_WIDTH)
            assert icon.cacheKey() == expected.cacheKey()
            image = icon.pixmap(QSize(60, 60)).toImage()
            assert image.pixelColor(30, 25).alpha() > 0
            assert (image.pixelColor(30, 38).alpha() > 0) is selected
    finally:
        sidebar.close()
        manager.shutdown()


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

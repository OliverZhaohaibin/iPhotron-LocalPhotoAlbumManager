from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from iPhoto.config import RECENTLY_DELETED_DIR_NAME
from iPhoto.domain.models.core import MediaType
from iPhoto.gui.viewmodels.gallery_viewmodel import GalleryViewModel


def _make_vm(*, library_root: Path | None = None):
    store = MagicMock()
    context = MagicMock()
    context.library.root.return_value = library_root
    facade = MagicMock()
    asset_service = MagicMock()
    vm = GalleryViewModel(
        store=store,
        context=context,
        facade=facade,
        asset_service=asset_service,
    )
    return vm, store, context, facade, asset_service


def test_open_album_loads_recursive_album_query(tmp_path: Path) -> None:
    album = tmp_path / "Paris"
    album.mkdir()
    vm, store, context, facade, _asset_service = _make_vm(library_root=tmp_path)
    facade.open_album.return_value = SimpleNamespace(root=album)

    routes = []
    vm.route_requested.connect(routes.append)
    vm.open_album(album)

    store.load_selection.assert_called_once()
    query = store.load_selection.call_args.kwargs["query"]
    assert query.album_path == "Paris"
    assert query.include_subalbums is True
    assert routes == ["gallery"]
    context.remember_album.assert_called_once_with(album)


def test_open_all_photos_loads_root_query(tmp_path: Path) -> None:
    vm, store, _context, _facade, _asset_service = _make_vm(library_root=tmp_path)

    vm.open_all_photos()

    store.load_selection.assert_called_once()
    query = store.load_selection.call_args.kwargs["query"]
    assert query.album_path is None
    assert vm.static_selection.value == "All Photos"


def test_open_recently_deleted_uses_deleted_root(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    deleted_root = library_root / RECENTLY_DELETED_DIR_NAME
    vm, store, context, _facade, _asset_service = _make_vm(library_root=library_root)
    context.library.ensure_deleted_directory.return_value = deleted_root

    vm.open_recently_deleted()

    store.load_selection.assert_called_once()
    active_root = store.load_selection.call_args.args[0]
    query = store.load_selection.call_args.kwargs["query"]
    assert active_root == deleted_root
    assert query.album_path == RECENTLY_DELETED_DIR_NAME


def test_open_filtered_collection_sets_media_types(tmp_path: Path) -> None:
    vm, store, _context, _facade, _asset_service = _make_vm(library_root=tmp_path)

    vm.open_filtered_collection("Videos", media_types=[MediaType.VIDEO])

    query = store.load_selection.call_args.kwargs["query"]
    assert query.media_types == [MediaType.VIDEO]


def test_open_location_asset_uses_full_snapshot_and_emits_detail(tmp_path: Path) -> None:
    vm, store, context, _facade, _asset_service = _make_vm(library_root=tmp_path)
    assets = [
        SimpleNamespace(library_relative="a.jpg", absolute_path=tmp_path / "a.jpg"),
        SimpleNamespace(library_relative="nested/b.jpg", absolute_path=tmp_path / "nested" / "b.jpg"),
    ]
    serial = vm.location_session.begin_load(tmp_path)
    assert vm.location_session.accept_loaded(serial, tmp_path, assets)
    store.row_for_path.return_value = 1

    requested = []
    vm.detail_requested.connect(requested.append)
    vm.open_location_asset("nested/b.jpg")

    store.load_selection.assert_called_once_with(tmp_path, direct_assets=assets, library_root=tmp_path)
    store.row_for_path.assert_called_once_with(tmp_path / "nested" / "b.jpg")
    assert requested == [1]
    assert vm.location_session.mode == "gallery"


def test_return_to_map_from_cluster_gallery_reuses_snapshot(tmp_path: Path) -> None:
    vm, _store, _context, _facade, _asset_service = _make_vm(library_root=tmp_path)
    assets = [SimpleNamespace(library_relative="a.jpg", absolute_path=tmp_path / "a.jpg")]
    serial = vm.location_session.begin_load(tmp_path)
    assert vm.location_session.accept_loaded(serial, tmp_path, assets)
    vm.location_session.set_mode("cluster_gallery")

    routes = []
    map_payloads = []
    vm.route_requested.connect(routes.append)
    vm.map_assets_changed.connect(lambda loaded_assets, root: map_payloads.append((loaded_assets, root)))

    vm.return_to_map_from_cluster_gallery()

    assert routes == ["map"]
    assert map_payloads == [(assets, tmp_path)]


def test_toggle_favorite_row_updates_store_via_asset_service(tmp_path: Path) -> None:
    vm, store, _context, _facade, asset_service = _make_vm(library_root=tmp_path)
    dto = SimpleNamespace(abs_path=tmp_path / "photo.jpg")
    store.asset_at.return_value = dto
    asset_service.toggle_favorite_by_path.return_value = True

    result = vm.toggle_favorite_row(3)

    assert result is True
    asset_service.toggle_favorite_by_path.assert_called_once_with(dto.abs_path)
    store.update_favorite_status.assert_called_once_with(3, True)


def test_rescan_current_emits_message_without_open_library() -> None:
    vm, _store, context, facade, _asset_service = _make_vm(library_root=None)
    facade.current_album = None
    messages = []
    vm.message_requested.connect(lambda text, timeout: messages.append((text, timeout)))

    vm.rescan_current()

    assert messages == [("No album is currently open.", 3000)]
    context.library.start_scanning.assert_not_called()

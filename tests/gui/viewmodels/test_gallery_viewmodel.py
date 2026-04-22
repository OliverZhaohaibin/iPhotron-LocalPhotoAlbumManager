from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

from iPhoto.config import RECENTLY_DELETED_DIR_NAME
from iPhoto.cache.index_store import get_global_repository, reset_global_repository
from iPhoto.domain.models.core import MediaType
from iPhoto.people.index_coordinator import PeopleSnapshotEvent, reset_people_index_coordinators
from iPhoto.people.repository import FaceRecord, FaceRepository, PersonRecord
from iPhoto.gui.viewmodels.gallery_viewmodel import GalleryViewModel
from iPhoto.domain.models.query import AssetQuery


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


def _face_repository(library_root: Path) -> FaceRepository:
    faces_root = library_root / ".iPhoto" / "faces"
    return FaceRepository(faces_root / "face_index.db", faces_root / "face_state.db")


@pytest.fixture(autouse=True)
def _stub_location_names(monkeypatch) -> None:
    monkeypatch.setattr(
        "iPhoto.library.geo_aggregator.resolve_location_name",
        lambda gps: f"{gps.get('lat')},{gps.get('lon')}",
    )


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


def test_open_location_asset_opens_singleton_cluster_gallery(tmp_path: Path) -> None:
    vm, store, _context, _facade, _asset_service = _make_vm(library_root=tmp_path)
    assets = [
        SimpleNamespace(library_relative="a.jpg", absolute_path=tmp_path / "a.jpg"),
        SimpleNamespace(library_relative="nested/b.jpg", absolute_path=tmp_path / "nested" / "b.jpg"),
    ]
    serial = vm.location_session.begin_load(tmp_path)
    assert vm.location_session.accept_loaded(serial, tmp_path, assets)

    requested = []
    cluster_mode = []
    routes = []
    vm.detail_requested.connect(requested.append)
    vm.cluster_gallery_mode_changed.connect(cluster_mode.append)
    vm.route_requested.connect(routes.append)
    vm.open_location_asset("nested/b.jpg")

    selected_asset = assets[1]
    store.load_selection.assert_called_once_with(
        tmp_path,
        direct_assets=[selected_asset],
        library_root=tmp_path,
    )
    store.row_for_path.assert_not_called()
    assert requested == []
    assert routes == ["gallery"]
    assert cluster_mode == [True]
    assert vm.current_section.value == "cluster_gallery"
    assert vm.current_direct_assets.value == [selected_asset]
    assert vm.can_return_to_map.value is True
    assert vm.is_in_cluster_gallery() is True
    assert vm.location_session.mode == "cluster_gallery"


def test_return_to_map_from_singleton_location_cluster_reuses_snapshot(tmp_path: Path) -> None:
    vm, _store, _context, _facade, _asset_service = _make_vm(library_root=tmp_path)
    assets = [SimpleNamespace(library_relative="a.jpg", absolute_path=tmp_path / "a.jpg")]
    serial = vm.location_session.begin_load(tmp_path)
    assert vm.location_session.accept_loaded(serial, tmp_path, assets)
    vm.open_location_asset("a.jpg")

    routes = []
    map_payloads = []
    vm.route_requested.connect(routes.append)
    vm.map_assets_changed.connect(lambda loaded_assets, root: map_payloads.append((loaded_assets, root)))

    vm.return_to_map_from_cluster_gallery()

    assert routes == ["map"]
    assert map_payloads == [(assets, tmp_path)]


def test_location_scan_chunk_updates_map_snapshot_incrementally(tmp_path: Path) -> None:
    vm, _store, _context, _facade, _asset_service = _make_vm(library_root=tmp_path)
    existing = SimpleNamespace(library_relative="a.jpg", absolute_path=tmp_path / "a.jpg")
    serial = vm.location_session.begin_load(tmp_path)
    assert vm.location_session.accept_loaded(serial, tmp_path, [existing])
    vm.location_session.set_mode("map")

    payloads = []
    vm.map_assets_changed.connect(lambda loaded_assets, root: payloads.append((loaded_assets, root)))

    vm.handle_location_scan_chunk(
        tmp_path / "Album",
        [
            {
                "rel": "Album/new.jpg",
                "id": "asset-2",
                "gps": {"lat": 52.5, "lon": 13.4},
                "mime": "image/jpeg",
                "parent_album_path": "Album",
            }
        ],
    )

    snapshot = vm.location_session.full_assets()
    assert [asset.library_relative for asset in snapshot] == ["Album/new.jpg", "a.jpg"]
    assert payloads[-1] == (snapshot, tmp_path)


def test_location_scan_chunk_removes_assets_that_no_longer_qualify(tmp_path: Path) -> None:
    vm, _store, _context, _facade, _asset_service = _make_vm(library_root=tmp_path)
    existing = SimpleNamespace(library_relative="Album/new.jpg", absolute_path=tmp_path / "Album" / "new.jpg")
    serial = vm.location_session.begin_load(tmp_path)
    assert vm.location_session.accept_loaded(serial, tmp_path, [existing])
    vm.location_session.set_mode("map")

    payloads = []
    vm.map_assets_changed.connect(lambda loaded_assets, root: payloads.append((loaded_assets, root)))

    vm.handle_location_scan_chunk(
        tmp_path / "Album",
        [{"rel": "Album/new.jpg", "id": "asset-2", "live_role": 1}],
    )

    snapshot = vm.location_session.full_assets()
    assert snapshot == []
    assert payloads[-1] == (snapshot, tmp_path)


def test_location_scan_chunk_updates_snapshot_without_refreshing_cluster_gallery(tmp_path: Path) -> None:
    vm, _store, _context, _facade, _asset_service = _make_vm(library_root=tmp_path)
    serial = vm.location_session.begin_load(tmp_path)
    assert vm.location_session.accept_loaded(serial, tmp_path, [])
    vm.location_session.set_mode("cluster_gallery")

    payloads = []
    vm.map_assets_changed.connect(lambda loaded_assets, root: payloads.append((loaded_assets, root)))

    vm.handle_location_scan_chunk(
        tmp_path,
        [
            {
                "rel": "Album/new.jpg",
                "id": "asset-2",
                "gps": {"lat": 52.5, "lon": 13.4},
                "mime": "image/jpeg",
                "parent_album_path": "Album",
            }
        ],
    )

    assert payloads == []
    assert vm.location_session.resolve_asset("Album/new.jpg") is not None


def test_location_scan_finished_rebuilds_snapshot_and_refreshes_map(tmp_path: Path) -> None:
    vm, _store, context, _facade, _asset_service = _make_vm(library_root=tmp_path)
    existing = SimpleNamespace(library_relative="a.jpg", absolute_path=tmp_path / "a.jpg")
    refreshed = SimpleNamespace(library_relative="Album/final.jpg", absolute_path=tmp_path / "Album" / "final.jpg")
    serial = vm.location_session.begin_load(tmp_path)
    assert vm.location_session.accept_loaded(serial, tmp_path, [existing])
    vm.location_session.set_mode("map")
    context.library.get_geotagged_assets.return_value = [refreshed]

    payloads = []
    vm.map_assets_changed.connect(lambda loaded_assets, root: payloads.append((loaded_assets, root)))

    vm.handle_location_scan_finished(tmp_path / "Album", True)

    snapshot = vm.location_session.full_assets()
    assert snapshot == [refreshed]
    assert payloads[-1] == (snapshot, tmp_path)


def test_location_scan_updates_ignore_unrelated_scan_roots(tmp_path: Path) -> None:
    vm, _store, context, _facade, _asset_service = _make_vm(library_root=tmp_path)
    serial = vm.location_session.begin_load(tmp_path)
    assert vm.location_session.accept_loaded(serial, tmp_path, [])
    vm.location_session.set_mode("map")
    context.library.get_geotagged_assets.return_value = [SimpleNamespace(library_relative="x.jpg")]

    payloads = []
    vm.map_assets_changed.connect(lambda loaded_assets, root: payloads.append((loaded_assets, root)))

    other_root = tmp_path.parent / "OtherLibrary"
    vm.handle_location_scan_chunk(
        other_root,
        [{"rel": "Album/new.jpg", "gps": {"lat": 52.5, "lon": 13.4}, "mime": "image/jpeg"}],
    )
    vm.handle_location_scan_finished(other_root, True)

    assert payloads == []
    assert vm.location_session.full_assets() == []


def test_location_scan_chunk_invalidates_cached_snapshot_while_location_is_inactive(tmp_path: Path) -> None:
    vm, _store, _context, _facade, _asset_service = _make_vm(library_root=tmp_path)
    existing = SimpleNamespace(library_relative="a.jpg", absolute_path=tmp_path / "a.jpg")
    serial = vm.location_session.begin_load(tmp_path)
    assert vm.location_session.accept_loaded(serial, tmp_path, [existing])
    vm.location_session.set_mode("inactive")

    vm.handle_location_scan_chunk(
        tmp_path / "Album",
        [
            {
                "rel": "Album/new.jpg",
                "id": "asset-2",
                "gps": {"lat": 52.5, "lon": 13.4},
                "mime": "image/jpeg",
                "parent_album_path": "Album",
            }
        ],
    )

    assert vm.location_session.invalidated is True


def test_open_location_map_reloads_after_inactive_snapshot_was_invalidated_by_scan(tmp_path: Path) -> None:
    vm, _store, context, _facade, _asset_service = _make_vm(library_root=tmp_path)
    stale = SimpleNamespace(library_relative="a.jpg", absolute_path=tmp_path / "a.jpg")
    refreshed = SimpleNamespace(library_relative="Album/new.jpg", absolute_path=tmp_path / "Album" / "new.jpg")
    serial = vm.location_session.begin_load(tmp_path)
    assert vm.location_session.accept_loaded(serial, tmp_path, [stale])
    vm.location_session.set_mode("inactive")
    context.library.get_geotagged_assets.return_value = [refreshed]

    payloads = []
    vm.map_assets_changed.connect(lambda loaded_assets, root: payloads.append((loaded_assets, root)))

    vm.handle_location_scan_finished(tmp_path / "Album", True)
    vm.open_location_map()

    assert vm.location_session.invalidated is False
    assert vm.location_session.full_assets() == [refreshed]
    assert payloads[-1] == ([refreshed], tmp_path)


def test_open_people_dashboard_routes_to_people_view(tmp_path: Path) -> None:
    vm, store, _context, _facade, _asset_service = _make_vm(library_root=tmp_path)
    routes = []
    vm.route_requested.connect(routes.append)

    vm.open_people_dashboard()

    store.load_selection.assert_not_called()
    assert vm.static_selection.value == "People"
    assert vm.current_section.value == "people_dashboard"
    assert routes == ["people"]


def test_open_pinned_album_keeps_pinned_static_selection(tmp_path: Path) -> None:
    album = tmp_path / "Trips"
    album.mkdir()
    vm, store, context, facade, _asset_service = _make_vm(library_root=tmp_path)
    facade.open_album.return_value = SimpleNamespace(root=album)
    routes = []
    sidebar_paths = []
    vm.route_requested.connect(routes.append)
    vm.sidebar_path_requested.connect(sidebar_paths.append)

    vm.open_pinned_album(album)

    store.load_selection.assert_called_once()
    query = store.load_selection.call_args.kwargs["query"]
    assert query.album_path == "Trips"
    assert vm.static_selection.value == "Pinned"
    assert vm.current_section.value == "pinned_album"
    assert routes == ["gallery"]
    assert sidebar_paths == []


def test_open_pinned_people_query_hides_cluster_header(tmp_path: Path) -> None:
    vm, store, _context, _facade, _asset_service = _make_vm(library_root=tmp_path)
    query = AssetQuery(asset_ids=["asset-1"])
    cluster_mode = []
    routes = []
    vm.cluster_gallery_mode_changed.connect(cluster_mode.append)
    vm.route_requested.connect(routes.append)

    vm.open_pinned_people_query(query, kind="person", entity_id="person-a")

    store.load_selection.assert_called_once_with(tmp_path, query=query)
    assert vm.static_selection.value == "Pinned"
    assert vm.current_section.value == "pinned_people_gallery"
    assert cluster_mode == [False]
    assert routes == ["gallery"]
    assert vm.is_in_cluster_gallery() is False


def test_people_cluster_gallery_loads_query_and_returns_to_people(tmp_path: Path) -> None:
    vm, store, _context, _facade, _asset_service = _make_vm(library_root=tmp_path)
    query = AssetQuery(asset_ids=["asset-1", "asset-2"])
    routes = []
    vm.route_requested.connect(routes.append)

    vm.open_people_cluster_gallery(query)

    store.load_selection.assert_called_once_with(tmp_path, query=query)
    assert vm.static_selection.value == "People"
    assert vm.current_section.value == "people_cluster_gallery"
    assert vm.cluster_gallery_back_tooltip() == "Return to People"
    assert vm.is_in_cluster_gallery() is True

    vm.return_from_cluster_gallery()

    assert routes == ["gallery", "people"]
    assert vm.current_section.value == "people_dashboard"
    assert vm.static_selection.value == "People"
    assert vm.is_in_cluster_gallery() is False


def test_people_cluster_gallery_retargets_after_snapshot_redirect(tmp_path: Path) -> None:
    reset_global_repository()
    reset_people_index_coordinators()
    library_root = tmp_path / "Library"
    library_root.mkdir()
    global_repo = get_global_repository(library_root)
    global_repo.write_rows(
        [
            {"rel": "album/a.jpg", "id": "asset-a", "media_type": 0, "face_status": "done"},
            {"rel": "album/b.jpg", "id": "asset-b", "media_type": 0, "face_status": "done"},
        ]
    )
    repository = _face_repository(library_root)
    embedding_a = np.asarray([1.0, 0.0, 0.0], dtype=np.float32)
    embedding_b = np.asarray([0.0, 1.0, 0.0], dtype=np.float32)
    repository.replace_all(
        [
            FaceRecord(
                face_id="face-a",
                face_key="face-key-a",
                asset_id="asset-a",
                asset_rel="album/a.jpg",
                box_x=10,
                box_y=10,
                box_w=80,
                box_h=80,
                confidence=0.99,
                embedding=embedding_a,
                embedding_dim=3,
                thumbnail_path=None,
                person_id="person-a",
                detected_at="2024-01-01T00:00:00+00:00",
                image_width=400,
                image_height=300,
            ),
            FaceRecord(
                face_id="face-b",
                face_key="face-key-b",
                asset_id="asset-b",
                asset_rel="album/b.jpg",
                box_x=10,
                box_y=10,
                box_w=80,
                box_h=80,
                confidence=0.99,
                embedding=embedding_b,
                embedding_dim=3,
                thumbnail_path=None,
                person_id="person-b",
                detected_at="2024-01-01T00:00:01+00:00",
                image_width=400,
                image_height=300,
            ),
        ],
        [
            PersonRecord(
                person_id="person-a",
                name="Alice",
                key_face_id="face-a",
                face_count=1,
                center_embedding=embedding_a,
                created_at="2024-01-01T00:00:00+00:00",
                updated_at="2024-01-01T00:00:00+00:00",
            ),
            PersonRecord(
                person_id="person-b",
                name="Bob",
                key_face_id="face-b",
                face_count=1,
                center_embedding=embedding_b,
                created_at="2024-01-01T00:00:01+00:00",
                updated_at="2024-01-01T00:00:01+00:00",
            ),
        ],
    )
    vm, store, _context, _facade, _asset_service = _make_vm(library_root=library_root)
    vm.open_people_cluster_gallery(
        AssetQuery(asset_ids=["asset-a"]),
        kind="person",
        entity_id="person-a",
    )
    store.load_selection.reset_mock()
    repository.merge_persons("person-a", "person-b")

    event = PeopleSnapshotEvent(
        library_root=library_root,
        revision=1,
        person_redirects={"person-a": "person-b"},
    )
    vm.handle_people_snapshot_committed(event)

    store.load_selection.assert_called_once()
    reloaded_query = store.load_selection.call_args.kwargs["query"]
    assert reloaded_query.asset_ids == ["asset-b", "asset-a"]
    assert vm.current_query.value.asset_ids == ["asset-b", "asset-a"]


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

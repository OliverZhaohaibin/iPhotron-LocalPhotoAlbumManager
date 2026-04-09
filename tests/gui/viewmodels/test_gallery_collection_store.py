from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from iPhoto.domain.models import Asset, MediaType
from iPhoto.domain.models.query import AssetQuery
from iPhoto.gui.viewmodels.gallery_collection_store import GalleryCollectionStore
from iPhoto.library.manager import GeotaggedAsset


class _FakeRepo:
    def __init__(self, assets):
        self.assets = list(assets)

    def find_by_query(self, query: AssetQuery):
        offset = query.offset
        limit = query.limit if query.limit is not None else len(self.assets)
        return self.assets[offset : offset + limit]

    def count(self, query: AssetQuery):
        return len(self.assets)


def test_load_initial_window_uses_sparse_cache() -> None:
    assets = [
        Asset(
            id=str(i),
            album_id="a",
            path=Path(f"asset_{i}.jpg"),
            media_type=MediaType.IMAGE,
            size_bytes=1,
        )
        for i in range(600)
    ]
    store = GalleryCollectionStore(_FakeRepo(assets), library_root=Path("."))
    store._path_cache.exists_cached = lambda path: True  # type: ignore[method-assign]

    store.load_selection(Path("."), query=AssetQuery())

    assert store.count() == 600
    assert 0 < len(store._row_cache) <= store.MAX_WINDOW_SIZE
    assert min(store._row_cache) == 0


def test_prioritize_rows_replaces_old_window_with_new_window() -> None:
    assets = [
        Asset(
            id=str(i),
            album_id="a",
            path=Path(f"asset_{i}.jpg"),
            media_type=MediaType.IMAGE,
            size_bytes=1,
        )
        for i in range(1200)
    ]
    store = GalleryCollectionStore(_FakeRepo(assets), library_root=Path("."))
    store._path_cache.exists_cached = lambda path: True  # type: ignore[method-assign]
    store.load_selection(Path("."), query=AssetQuery())

    initial_keys = set(store._row_cache)
    store.prioritize_rows(900, 940)
    updated_keys = set(store._row_cache)

    assert store.count() == 1200
    assert 900 in updated_keys
    assert len(updated_keys) <= store.MAX_WINDOW_SIZE + 1
    assert initial_keys != updated_keys


def test_reload_current_selection_replays_query_after_repository_rebind(tmp_path: Path) -> None:
    first_repo = _FakeRepo(
        [
            Asset(
                id="1",
                album_id="a",
                path=Path("first.jpg"),
                media_type=MediaType.IMAGE,
                size_bytes=1,
            )
        ]
    )
    second_repo = _FakeRepo(
        [
            Asset(
                id="1",
                album_id="a",
                path=Path("first.jpg"),
                media_type=MediaType.IMAGE,
                size_bytes=1,
            ),
            Asset(
                id="2",
                album_id="a",
                path=Path("second.jpg"),
                media_type=MediaType.IMAGE,
                size_bytes=1,
            ),
        ]
    )
    store = GalleryCollectionStore(first_repo, library_root=tmp_path)
    store._path_cache.exists_cached = lambda path: True  # type: ignore[method-assign]

    store.load_selection(tmp_path, query=AssetQuery())
    assert store.count() == 1

    store.rebind_repository(second_repo, tmp_path)
    store.reload_current_selection()

    assert store.count() == 2


def test_reload_current_selection_replays_direct_assets_after_rebind(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    library_root.mkdir()
    asset_path = library_root / "cluster.jpg"
    asset_path.write_bytes(b"cluster")
    store = GalleryCollectionStore(_FakeRepo([]), library_root=library_root)

    direct_assets = [
        GeotaggedAsset(
            library_relative="cluster.jpg",
            album_relative="cluster.jpg",
            absolute_path=asset_path,
            album_path=library_root,
            asset_id="cluster-1",
            latitude=48.0,
            longitude=2.0,
            is_image=True,
            is_video=False,
            still_image_time=None,
            duration=None,
            location_name="Paris",
            live_photo_group_id=None,
            live_partner_rel=None,
        )
    ]

    store.load_selection(library_root, direct_assets=direct_assets, library_root=library_root)
    next_library_root = tmp_path / "OtherLibrary"
    store.rebind_repository(_FakeRepo([]), next_library_root)
    store.reload_current_selection()

    assert store.count() == 1
    assert store.row_for_path(asset_path) == 0


def test_handle_scan_chunk_refreshes_when_new_row_sorts_into_visible_window(tmp_path: Path) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    base_dt = datetime(2024, 1, 1, 12, 0, 0)
    assets = [
        Asset(
            id=f"{1000 - i}",
            album_id="a",
            path=Path(f"asset_{i}.jpg"),
            media_type=MediaType.IMAGE,
            size_bytes=1,
            created_at=base_dt - timedelta(minutes=i),
        )
        for i in range(240)
    ]
    store = GalleryCollectionStore(_FakeRepo(assets), library_root=root)
    store._path_cache.exists_cached = lambda path: True  # type: ignore[method-assign]
    store.set_active_root(root)
    store.load_selection(root, query=AssetQuery())
    store.prioritize_rows(120, 140)

    visible_rows = [store._row_cache[row] for row in range(120, 141) if row in store._row_cache]
    midpoint = visible_rows[len(visible_rows) // 2]

    refreshed = []
    store.data_changed.connect(lambda: refreshed.append(True))
    store.handle_scan_chunk(
        root,
        [{"rel": "new_visible.jpg", "id": "scan-1", "dt": midpoint.created_at.isoformat()}],
    )

    assert refreshed

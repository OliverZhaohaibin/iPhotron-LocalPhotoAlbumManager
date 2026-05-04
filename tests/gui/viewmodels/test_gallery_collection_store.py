from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from iPhoto.domain.models import Asset, MediaType
from iPhoto.domain.models.query import AssetQuery
from iPhoto.gui.viewmodels.gallery_collection_store import GalleryCollectionStore
from iPhoto.library.runtime_controller import GeotaggedAsset


class _FakeQueryService:
    def __init__(self, assets, *, library_root: Path = Path(".")):
        self.assets = list(assets)
        self.library_root = library_root

    def count_query_assets(self, query: AssetQuery):
        return len(self._matching_assets(query))

    def read_query_asset_rows(self, root: Path, query: AssetQuery):
        matching = self._matching_assets(query)
        offset = query.offset
        limit = query.limit if query.limit is not None else len(self.assets)
        return [
            self._row_for_asset(asset, root)
            for asset in matching[offset : offset + limit]
        ]

    def _matching_assets(self, query: AssetQuery):
        assets = list(self.assets)
        if query.asset_ids:
            wanted = set(query.asset_ids)
            assets = [asset for asset in assets if asset.id in wanted]
        if query.album_path:
            prefix = query.album_path.rstrip("/") + "/"
            assets = [
                asset
                for asset in assets
                if asset.parent_album_path == query.album_path
                or (
                    query.include_subalbums
                    and isinstance(asset.parent_album_path, str)
                    and asset.parent_album_path.startswith(prefix)
                )
            ]
        if query.media_types:
            allowed = {media_type.value for media_type in query.media_types}
            assets = [asset for asset in assets if asset.media_type.value in allowed]
        if query.is_favorite is not None:
            assets = [
                asset for asset in assets if asset.is_favorite is query.is_favorite
            ]
        return assets

    def _row_for_asset(self, asset: Asset, root: Path):
        rel = asset.path.as_posix()
        album_path = self._album_path_for(root)
        view_rel = rel
        if album_path:
            prefix = album_path.rstrip("/") + "/"
            if rel.startswith(prefix):
                view_rel = rel[len(prefix):]
        return {
            "id": asset.id,
            "rel": view_rel,
            "media_type": 1 if asset.media_type == MediaType.VIDEO else 0,
            "bytes": asset.size_bytes,
            "dt": asset.created_at.isoformat() if asset.created_at else None,
            "w": asset.width,
            "h": asset.height,
            "dur": asset.duration,
            "is_favorite": asset.is_favorite,
            "parent_album_path": asset.parent_album_path,
        }

    def _album_path_for(self, root: Path) -> str | None:
        try:
            rel = root.resolve().relative_to(self.library_root.resolve())
        except (OSError, ValueError):
            try:
                rel = root.relative_to(self.library_root)
            except ValueError:
                return None
        rel_str = rel.as_posix()
        return None if rel_str in ("", ".") else rel_str


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
    store = GalleryCollectionStore(_FakeQueryService(assets), library_root=Path("."))
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
    store = GalleryCollectionStore(_FakeQueryService(assets), library_root=Path("."))
    store._path_cache.exists_cached = lambda path: True  # type: ignore[method-assign]
    store.load_selection(Path("."), query=AssetQuery())

    initial_keys = set(store._row_cache)
    store.prioritize_rows(900, 940)
    updated_keys = set(store._row_cache)

    assert store.count() == 1200
    assert 900 in updated_keys
    assert len(updated_keys) <= store.MAX_WINDOW_SIZE + 1
    assert initial_keys != updated_keys


def test_asset_at_lazily_fetches_row_outside_initial_window() -> None:
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
    store = GalleryCollectionStore(_FakeQueryService(assets), library_root=Path("."))
    store._path_cache.exists_cached = lambda path: True  # type: ignore[method-assign]

    store.load_selection(Path("."), query=AssetQuery())

    assert store._window_range == (0, 319)
    assert 360 not in store._row_cache
    dto = store.asset_at(360)

    assert dto is not None
    assert dto.rel_path == Path("asset_360.jpg")
    assert 360 in store._row_cache


def test_reload_current_selection_replays_query_after_query_service_rebind(tmp_path: Path) -> None:
    first_query_service = _FakeQueryService(
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
    second_query_service = _FakeQueryService(
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
    store = GalleryCollectionStore(first_query_service, library_root=tmp_path)
    store._path_cache.exists_cached = lambda path: True  # type: ignore[method-assign]

    store.load_selection(tmp_path, query=AssetQuery())
    assert store.count() == 1

    store.rebind_asset_query_service(second_query_service, tmp_path)
    store.reload_current_selection()

    assert store.count() == 2


def test_reload_current_selection_replays_direct_assets_after_rebind(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    library_root.mkdir()
    asset_path = library_root / "cluster.jpg"
    asset_path.write_bytes(b"cluster")
    store = GalleryCollectionStore(_FakeQueryService([]), library_root=library_root)

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
    store.rebind_asset_query_service(_FakeQueryService([]), next_library_root)
    store.reload_current_selection()

    assert store.count() == 1
    assert store.row_for_path(asset_path) == 0


def test_asset_id_query_reads_people_cluster_rows_through_query_service(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    assets = [
        Asset(
            id="asset-a",
            album_id="a",
            path=Path("a.jpg"),
            media_type=MediaType.IMAGE,
            size_bytes=1,
        ),
        Asset(
            id="asset-b",
            album_id="a",
            path=Path("b.jpg"),
            media_type=MediaType.IMAGE,
            size_bytes=1,
        ),
    ]
    store = GalleryCollectionStore(
        _FakeQueryService(assets, library_root=root),
        library_root=root,
    )
    store._path_cache.exists_cached = lambda path: True  # type: ignore[method-assign]

    store.load_selection(root, query=AssetQuery(asset_ids=["asset-b"]))

    dto = store.asset_at(0)
    assert store.count() == 1
    assert dto is not None
    assert dto.id == "asset-b"
    assert dto.rel_path == Path("b.jpg")


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
    store = GalleryCollectionStore(_FakeQueryService(assets, library_root=root), library_root=root)
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


def test_handle_scan_finished_refreshes_count_outside_top_visible_window(tmp_path: Path) -> None:
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
    query_service = _FakeQueryService(assets, library_root=root)
    store = GalleryCollectionStore(query_service, library_root=root)
    store._path_cache.exists_cached = lambda path: True  # type: ignore[method-assign]
    store.set_active_root(root)
    store.load_selection(root, query=AssetQuery())
    store.prioritize_rows(120, 140)

    observed_counts: list[tuple[int, int]] = []
    store.count_changed.connect(lambda old, new: observed_counts.append((old, new)))

    query_service.assets.insert(
        0,
        Asset(
            id="scan-new",
            album_id="a",
            path=Path("new_asset.jpg"),
            media_type=MediaType.IMAGE,
            size_bytes=1,
            created_at=base_dt + timedelta(minutes=5),
        ),
    )

    store.handle_scan_finished(root, True)

    assert store.count() == 241
    assert observed_counts == [(240, 241)]


def test_mid_scroll_rescan_updates_gallery_after_single_scan_finished(tmp_path: Path) -> None:
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
    query_service = _FakeQueryService(assets, library_root=root)
    store = GalleryCollectionStore(query_service, library_root=root)
    store._path_cache.exists_cached = lambda path: True  # type: ignore[method-assign]
    store.set_active_root(root)
    store.load_selection(root, query=AssetQuery())
    store.prioritize_rows(120, 140)

    query_service.assets.insert(
        0,
        Asset(
            id="scan-new",
            album_id="a",
            path=Path("new_asset.jpg"),
            media_type=MediaType.IMAGE,
            size_bytes=1,
            created_at=base_dt + timedelta(minutes=5),
        ),
    )

    store.handle_scan_chunk(
        root,
        [{"rel": "new_asset.jpg", "id": "scan-new", "dt": (base_dt + timedelta(minutes=5)).isoformat()}],
    )

    assert store.count() == 240

    store.handle_scan_finished(root, True)

    assert store.count() == 241
    top_dto = store.asset_at(0)
    assert top_dto is not None
    assert top_dto.rel_path == Path("new_asset.jpg")

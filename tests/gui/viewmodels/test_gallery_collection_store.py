from __future__ import annotations

from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import threading

from PySide6.QtGui import QImage
from PIL import Image

from iPhoto.config import RECENTLY_DELETED_DIR_NAME
from iPhoto.domain.models import Asset, MediaType
from iPhoto.domain.models.query import AssetQuery, WindowResult
from iPhoto.gui.viewmodels.asset_dto_converter import scan_row_to_dto
from iPhoto.gui.viewmodels.gallery_collection_store import GalleryCollectionStore
from iPhoto.library.runtime_controller import GeotaggedAsset


class _FakeQueryService:
    def __init__(self, assets, *, library_root: Path = Path(".")):
        self.assets = list(assets)
        self.library_root = library_root
        self.read_calls: list[tuple[int, int | None]] = []
        self.count_calls = 0
        self.row_lookup_calls: list[Path] = []

    def count_query_assets(self, query: AssetQuery):
        self.count_calls += 1
        return len(self._matching_assets(query))

    def read_query_asset_rows(self, root: Path, query: AssetQuery):
        self.read_calls.append((query.offset, query.limit))
        matching = self._matching_assets(query)
        offset = query.offset
        limit = query.limit if query.limit is not None else len(self.assets)
        return [
            self._row_for_asset(asset, root)
            for asset in matching[offset : offset + limit]
        ]

    def read_query_asset_window(
        self,
        root: Path,
        query: AssetQuery,
        first: int,
        limit: int,
    ):
        self.read_calls.append((first, limit))
        matching = self._matching_assets(query)
        rows = [
            self._row_for_asset(asset, root)
            for asset in matching[first : first + limit]
        ]
        return WindowResult(
            first=first,
            rows=rows,
            total_count=len(matching),
            collection_revision=42,
        )

    def find_row_by_path(self, query: AssetQuery, path: Path):
        self.row_lookup_calls.append(path)
        target = path.name if path.is_absolute() else path.as_posix()
        for index, asset in enumerate(self._matching_assets(query)):
            if asset.path.as_posix() == target or asset.path.name == target:
                return index
        return None

    def find_live_partner(self, _asset_id: str):
        return None


    def _matching_assets(self, query: AssetQuery):
        assets = list(self.assets)
        if query.album_path != RECENTLY_DELETED_DIR_NAME:
            assets = [
                asset
                for asset in assets
                if asset.parent_album_path != RECENTLY_DELETED_DIR_NAME
                and not asset.path.as_posix().startswith(
                    f"{RECENTLY_DELETED_DIR_NAME}/"
                )
            ]
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


class _FailingOnceQueryService(_FakeQueryService):
    def __init__(self, assets, *, fail_offset: int):
        super().__init__(assets)
        self.fail_offset = fail_offset
        self.failed = False

    def read_query_asset_rows(self, root: Path, query: AssetQuery):
        if query.offset >= self.fail_offset and not self.failed:
            self.failed = True
            raise RuntimeError("transient deep window failure")
        return super().read_query_asset_rows(root, query)

    def read_query_asset_window(
        self,
        root: Path,
        query: AssetQuery,
        first: int,
        limit: int,
    ):
        if first >= self.fail_offset and not self.failed:
            self.failed = True
            raise RuntimeError("transient deep window failure")
        return super().read_query_asset_window(root, query, first, limit)


def _jpeg_bytes() -> bytes:
    data = BytesIO()
    Image.new("RGB", (4, 4), (0x33, 0x66, 0x99)).save(data, format="JPEG")
    return data.getvalue()


def test_scan_row_to_dto_decodes_micro_thumbnail_bytes() -> None:
    dto = scan_row_to_dto(
        Path("/library"),
        "photo.jpg",
        {
            "id": "asset",
            "rel": "photo.jpg",
            "media_type": 0,
            "micro_thumbnail": _jpeg_bytes(),
        },
    )

    assert dto is not None
    assert isinstance(dto.micro_thumbnail, QImage)
    assert not dto.micro_thumbnail.isNull()


def test_scan_row_to_dto_ignores_invalid_micro_thumbnail_bytes() -> None:
    dto = scan_row_to_dto(
        Path("/library"),
        "photo.jpg",
        {
            "id": "asset",
            "rel": "photo.jpg",
            "media_type": 0,
            "micro_thumbnail": b"not-an-image",
        },
    )

    assert dto is not None
    assert dto.micro_thumbnail is None


def test_async_window_decodes_micro_off_caller_thread_and_publishes_on_demand() -> None:
    caller_thread = threading.get_ident()
    service = _FakeQueryService(
        [
            Asset(
                id="asset",
                album_id="a",
                path=Path("photo.jpg"),
                media_type=MediaType.IMAGE,
                size_bytes=1,
            )
        ]
    )
    worker_threads: list[int] = []
    original_read = service.read_query_asset_window

    def read_window(root, query, first, limit):
        worker_threads.append(threading.get_ident())
        result = original_read(root, query, first, limit)
        result.rows[0]["micro_thumbnail"] = _jpeg_bytes()
        return result

    service.read_query_asset_window = read_window  # type: ignore[method-assign]
    store = GalleryCollectionStore(service, library_root=Path("."))
    store._path_cache.exists_cached = lambda path: True  # type: ignore[method-assign]
    store.enable_async_windows()
    ready = threading.Event()
    results = []
    store.window_result_ready.connect(lambda result: (results.append(result), ready.set()))

    store.load_selection(Path("."), query=AssetQuery())

    assert store.count() == 0
    assert ready.wait(2.0)
    assert worker_threads and worker_threads[0] != caller_thread
    assert store.apply_window_result(results[-1]) is True
    assert isinstance(store.asset_at(0).micro_thumbnail, QImage)
    store.shutdown()


def test_async_window_discards_stale_generation_after_large_scroll_jump() -> None:
    assets = [
        Asset(
            id=str(index),
            album_id="a",
            path=Path(f"asset_{index}.jpg"),
            media_type=MediaType.IMAGE,
            size_bytes=1,
        )
        for index in range(10_000)
    ]
    service = _FakeQueryService(assets)
    store = GalleryCollectionStore(service, library_root=Path("."))
    store._path_cache.exists_cached = lambda path: True  # type: ignore[method-assign]
    store.enable_async_windows()
    ready = threading.Event()
    results = []

    def collect(result):
        results.append(result)
        if len(results) >= 2:
            ready.set()

    store.window_result_ready.connect(collect)
    store.load_selection(Path("."), query=AssetQuery())
    store.prioritize_rows(7_000, 7_060)

    assert ready.wait(2.0)
    assert store.apply_window_result(results[0]) is False
    assert store.apply_window_result(results[-1]) is True
    assert store.asset_at(7_000) is not None
    store.shutdown()


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


def test_gallery_window_fetch_uses_window_api_without_extra_count() -> None:
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
    service = _FakeQueryService(assets)
    store = GalleryCollectionStore(service, library_root=Path("."))
    store._path_cache.exists_cached = lambda path: True  # type: ignore[method-assign]

    store.load_selection(Path("."), query=AssetQuery())

    assert service.read_calls == [(0, 320)]
    assert service.count_calls == 0


def test_gallery_applies_scan_batch_without_full_reset() -> None:
    assets = [
        Asset(
            id=str(i),
            album_id="a",
            path=Path(f"asset_{i}.jpg"),
            media_type=MediaType.IMAGE,
            size_bytes=1,
        )
        for i in range(120)
    ]
    service = _FakeQueryService(assets)
    store = GalleryCollectionStore(service, library_root=Path("."))
    store._path_cache.exists_cached = lambda path: True  # type: ignore[method-assign]
    store.load_selection(Path("."), query=AssetQuery())
    refreshes: list[object] = []
    store.data_changed.connect(lambda: refreshes.append("changed"))

    store.handle_scan_batch(
        SimpleNamespace(
            root=Path("."),
            collection_revision=99,
            rows=[
                {
                    "rel": "asset_0.jpg",
                    "id": "0",
                    "dt": datetime(2024, 1, 1).isoformat(),
                    "thumbnail_state": "ready",
                    "micro_thumbnail": b"thumb",
                }
            ],
        )
    )

    assert store.snapshot_signature()[2] >= 99
    assert store.count() == 120


class _BackfillQueryService(_FakeQueryService):
    def __init__(self, *, library_root: Path) -> None:
        super().__init__([], library_root=library_root)
        self.backfill_requests: list[tuple[int, int]] = []

    def request_thumbnail_backfill(
        self,
        root: Path,
        query: AssetQuery,
        first: int,
        limit: int,
    ) -> int:
        del root, query
        self.backfill_requests.append((first, limit))
        if self.assets:
            return 0
        return 1

    def complete_backfill(self) -> None:
        self.assets.append(
            Asset(
                id="backfilled",
                album_id="a",
                path=Path("backfilled.jpg"),
                media_type=MediaType.IMAGE,
                size_bytes=1,
            )
        )


class _VisibleBackfillQueryService(_BackfillQueryService):
    def request_thumbnail_backfill(
        self,
        root: Path,
        query: AssetQuery,
        first: int,
        limit: int,
    ) -> int:
        del root, query
        self.backfill_requests.append((first, limit))
        return 1


def test_gallery_requests_visible_window_stale_backfill_once() -> None:
    service = _BackfillQueryService(library_root=Path("."))
    store = GalleryCollectionStore(service, library_root=Path("."))
    store._path_cache.exists_cached = lambda path: True  # type: ignore[method-assign]

    store.load_selection(Path("."), query=AssetQuery())
    store.prioritize_rows(0, 20)

    assert store.count() == 0
    assert service.backfill_requests == [(0, store.INITIAL_VISIBLE_ROWS * store.WINDOW_MULTIPLIER)]
    service.complete_backfill()
    assert store.flush_pending_thumbnail_backfill() is False
    assert store.count() == 1
    assert store.snapshot_signature()[2] >= 42


def test_gallery_shows_stale_rows_while_scheduling_thumbnail_backfill() -> None:
    service = _VisibleBackfillQueryService(library_root=Path("."))
    service.assets.append(
        Asset(
            id="stale",
            album_id="a",
            path=Path("stale.jpg"),
            media_type=MediaType.IMAGE,
            size_bytes=1,
        )
    )
    store = GalleryCollectionStore(service, library_root=Path("."))
    store._path_cache.exists_cached = lambda path: True  # type: ignore[method-assign]

    store.load_selection(Path("."), query=AssetQuery())

    assert store.count() == 1
    assert store.asset_at(0) is not None
    assert service.backfill_requests == [(0, store.MIN_WINDOW_SIZE)]


def test_scan_batch_can_be_recorded_without_immediate_refresh(tmp_path: Path) -> None:
    asset = Asset(
        id="existing",
        album_id="a",
        path=Path("existing.jpg"),
        media_type=MediaType.IMAGE,
        size_bytes=1,
        created_at=datetime(2024, 1, 1),
    )
    service = _FakeQueryService([asset], library_root=tmp_path)
    store = GalleryCollectionStore(service, library_root=tmp_path)
    store._path_cache.exists_cached = lambda path: True  # type: ignore[method-assign]
    store.load_selection(tmp_path, query=AssetQuery())
    calls = []
    original_reload = store._reload_window_for_visible_range

    def _recording_reload(*args, **kwargs):
        calls.append((args, kwargs))
        return original_reload(*args, **kwargs)

    store._reload_window_for_visible_range = _recording_reload  # type: ignore[method-assign]
    batch = SimpleNamespace(
        root=tmp_path,
        collection_revision=100,
        rows=[
            {
                "rel": "new.jpg",
                "id": "new",
                "dt": "2024-01-02T00:00:00",
                "media_type": 0,
                "bytes": 1,
                "thumbnail_state": "ready",
                "micro_thumbnail": b"micro",
            }
        ],
    )

    assert store.record_scan_batch(batch) is True
    assert calls == []

    store.flush_pending_scan_refresh()

    assert len(calls) == 1
    assert store.snapshot_signature()[2] >= 100


def test_thumbnail_backfill_completion_releases_requested_window(tmp_path: Path) -> None:
    asset = Asset(
        id="existing",
        album_id="a",
        path=Path("existing.jpg"),
        media_type=MediaType.IMAGE,
        size_bytes=1,
    )
    service = _FakeQueryService([asset], library_root=tmp_path)
    store = GalleryCollectionStore(service, library_root=tmp_path)
    store._path_cache.exists_cached = lambda path: True  # type: ignore[method-assign]
    store.load_selection(tmp_path, query=AssetQuery())
    store._thumbnail_backfill_windows = {(0, store.MIN_WINDOW_SIZE)}
    store._thumbnail_backfill_pending = True
    batch = SimpleNamespace(
        job_id=f"thumbnail-backfill:{tmp_path}:0:{store.MIN_WINDOW_SIZE}",
        root=tmp_path,
        collection_revision=0,
        ready_count=0,
        rows=[],
    )

    assert store.record_scan_batch(batch) is True
    assert store._thumbnail_backfill_windows == set()
    assert store._thumbnail_backfill_pending is False
    assert store._pending_scan_refresh is True


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


def test_asset_at_does_not_fetch_row_outside_initial_window_synchronously() -> None:
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
    service = _FakeQueryService(assets)
    store = GalleryCollectionStore(service, library_root=Path("."))
    store._path_cache.exists_cached = lambda path: True  # type: ignore[method-assign]

    store.load_selection(Path("."), query=AssetQuery())

    assert store._window_range == (0, 319)
    assert 360 not in store._row_cache
    dto = store.asset_at(360)

    assert dto is None
    assert 360 not in store._row_cache
    assert service.read_calls == [(0, 320)]


def test_row_for_path_uses_query_lookup_without_scanning_batches() -> None:
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
    service = _FakeQueryService(assets)
    store = GalleryCollectionStore(service, library_root=Path("."))
    store._path_cache.exists_cached = lambda path: True  # type: ignore[method-assign]

    store.load_selection(Path("."), query=AssetQuery())
    service.read_calls.clear()

    assert store.row_for_path(Path("asset_360.jpg")) == 360
    assert service.row_lookup_calls == [Path("asset_360.jpg")]
    assert service.read_calls == []


def test_pending_delete_survives_section_reload_for_aggregate_collections(tmp_path: Path) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    deleted_root = root / RECENTLY_DELETED_DIR_NAME
    assets = [
        Asset(
            id="photo",
            album_id="a",
            path=Path("Album/photo.jpg"),
            parent_album_path="Album",
            media_type=MediaType.IMAGE,
            size_bytes=1,
            is_favorite=True,
        ),
        Asset(
            id="video",
            album_id="a",
            path=Path("Album/video.mov"),
            parent_album_path="Album",
            media_type=MediaType.VIDEO,
            size_bytes=1,
        ),
    ]
    service = _FakeQueryService(assets, library_root=root)
    store = GalleryCollectionStore(service, library_root=root)
    store._path_cache.exists_cached = lambda path: True  # type: ignore[method-assign]

    store.load_selection(root, query=AssetQuery())
    photo_path = root / "Album" / "photo.jpg"
    removed_rows, inserted = store.apply_optimistic_move(
        [photo_path],
        deleted_root,
        is_delete=True,
    )

    assert removed_rows == [0]
    assert inserted == []

    store.load_selection(root, query=AssetQuery())

    assert store.count() == 1
    assert [dto.id for dto in store._row_cache.values()] == ["video"]

    video_query = AssetQuery(media_types=[MediaType.VIDEO])
    store.load_selection(root, query=video_query)

    assert store.count() == 1
    assert [dto.id for dto in store._row_cache.values()] == ["video"]

    favorite_query = AssetQuery(is_favorite=True)
    store.load_selection(root, query=favorite_query)

    assert store.count() == 0
    assert store._row_cache == {}


def test_pending_delete_from_physical_album_hides_source_in_aggregate_collections(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Library"
    album_root = root / "Album"
    album_root.mkdir(parents=True)
    deleted_root = root / RECENTLY_DELETED_DIR_NAME
    asset = Asset(
        id="photo",
        album_id="a",
        path=Path("Album/photo.jpg"),
        parent_album_path="Album",
        media_type=MediaType.IMAGE,
        size_bytes=1,
        is_favorite=True,
    )
    service = _FakeQueryService([asset], library_root=root)
    store = GalleryCollectionStore(service, library_root=root)
    store._path_cache.exists_cached = lambda path: True  # type: ignore[method-assign]

    store.load_selection(album_root, query=AssetQuery(album_path="Album"))
    removed_rows, inserted = store.apply_optimistic_move(
        [album_root / "photo.jpg"],
        deleted_root,
        is_delete=True,
    )

    assert removed_rows == [0]
    assert inserted == []

    store.load_selection(root, query=AssetQuery())
    assert store.count() == 0
    assert store._row_cache == {}

    store.load_selection(root, query=AssetQuery(is_favorite=True))
    assert store.count() == 0
    assert store._row_cache == {}

    store.load_selection(deleted_root, query=AssetQuery(album_path=RECENTLY_DELETED_DIR_NAME))
    dto = store.asset_at(0)
    assert store.count() == 1
    assert dto is not None
    assert dto.abs_path == deleted_root / "photo.jpg"


def test_pending_delete_is_visible_in_recently_deleted_until_backend_finishes(tmp_path: Path) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    deleted_root = root / RECENTLY_DELETED_DIR_NAME
    asset = Asset(
        id="photo",
        album_id="a",
        path=Path("Album/photo.jpg"),
        parent_album_path="Album",
        media_type=MediaType.IMAGE,
        size_bytes=1,
    )
    service = _FakeQueryService([asset], library_root=root)
    store = GalleryCollectionStore(service, library_root=root)
    store._path_cache.exists_cached = lambda path: True  # type: ignore[method-assign]

    store.load_selection(root, query=AssetQuery())
    store.apply_optimistic_move([root / "Album" / "photo.jpg"], deleted_root, is_delete=True)

    trash_query = AssetQuery(album_path=RECENTLY_DELETED_DIR_NAME)
    store.load_selection(deleted_root, query=trash_query)

    dto = store.asset_at(0)
    assert store.count() == 1
    assert dto is not None
    assert dto.abs_path == deleted_root / "photo.jpg"


def test_clearing_pending_delete_allows_aggregate_row_to_return(tmp_path: Path) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    deleted_root = root / RECENTLY_DELETED_DIR_NAME
    asset = Asset(
        id="photo",
        album_id="a",
        path=Path("Album/photo.jpg"),
        parent_album_path="Album",
        media_type=MediaType.IMAGE,
        size_bytes=1,
    )
    service = _FakeQueryService([asset], library_root=root)
    store = GalleryCollectionStore(service, library_root=root)
    store._path_cache.exists_cached = lambda path: True  # type: ignore[method-assign]
    source = root / "Album" / "photo.jpg"

    store.load_selection(root, query=AssetQuery())
    store.apply_optimistic_move([source], deleted_root, is_delete=True)
    assert store.clear_pending_moves_for_paths([source, deleted_root / "photo.jpg"]) is True
    store.load_selection(root, query=AssetQuery())

    assert store.count() == 1
    assert store.asset_at(0).id == "photo"


def test_pending_move_from_physical_album_shows_destination_in_aggregates(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Library"
    source_root = root / "Album"
    target_root = root / "Target"
    source_root.mkdir(parents=True)
    target_root.mkdir()
    asset = Asset(
        id="photo",
        album_id="a",
        path=Path("Album/photo.jpg"),
        parent_album_path="Album",
        media_type=MediaType.IMAGE,
        size_bytes=1,
        is_favorite=True,
    )
    service = _FakeQueryService([asset], library_root=root)
    store = GalleryCollectionStore(service, library_root=root)
    store._path_cache.exists_cached = lambda path: True  # type: ignore[method-assign]

    store.load_selection(source_root, query=AssetQuery(album_path="Album"))
    removed_rows, inserted = store.apply_optimistic_move(
        [source_root / "photo.jpg"],
        target_root,
        is_delete=False,
    )

    assert removed_rows == [0]
    assert inserted == []

    store.load_selection(source_root, query=AssetQuery(album_path="Album"))
    assert store.count() == 0

    store.load_selection(target_root, query=AssetQuery(album_path="Target"))
    dto = store.asset_at(0)
    assert store.count() == 1
    assert dto is not None
    assert dto.abs_path == target_root / "photo.jpg"

    store.load_selection(root, query=AssetQuery())
    aggregate_dtos = list(store._row_cache.values())
    assert store.count() == 1
    assert [dto.abs_path for dto in aggregate_dtos] == [target_root / "photo.jpg"]

    store.load_selection(root, query=AssetQuery(is_favorite=True))
    favorite_dto = store.asset_at(0)
    assert store.count() == 1
    assert favorite_dto is not None
    assert favorite_dto.abs_path == target_root / "photo.jpg"


def test_pending_restore_shows_destination_in_album_and_aggregates(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Library"
    trash_root = root / RECENTLY_DELETED_DIR_NAME
    album_root = root / "Album"
    trash_root.mkdir(parents=True)
    album_root.mkdir()
    asset = Asset(
        id="photo",
        album_id="trash",
        path=Path(f"{RECENTLY_DELETED_DIR_NAME}/photo.jpg"),
        parent_album_path=RECENTLY_DELETED_DIR_NAME,
        media_type=MediaType.IMAGE,
        size_bytes=1,
        is_favorite=True,
    )
    service = _FakeQueryService([asset], library_root=root)
    store = GalleryCollectionStore(service, library_root=root)
    store._path_cache.exists_cached = lambda path: True  # type: ignore[method-assign]

    store.load_selection(
        trash_root,
        query=AssetQuery(album_path=RECENTLY_DELETED_DIR_NAME),
    )
    removed_rows, inserted = store.apply_optimistic_move(
        [trash_root / "photo.jpg"],
        album_root,
        is_delete=False,
    )

    assert removed_rows == [0]
    assert inserted == []

    store.load_selection(
        trash_root,
        query=AssetQuery(album_path=RECENTLY_DELETED_DIR_NAME),
    )
    assert store.count() == 0

    store.load_selection(album_root, query=AssetQuery(album_path="Album"))
    album_dto = store.asset_at(0)
    assert store.count() == 1
    assert album_dto is not None
    assert album_dto.abs_path == album_root / "photo.jpg"

    store.load_selection(root, query=AssetQuery())
    aggregate_dto = store.asset_at(0)
    assert store.count() == 1
    assert aggregate_dto is not None
    assert aggregate_dto.abs_path == album_root / "photo.jpg"

    store.load_selection(root, query=AssetQuery(is_favorite=True))
    favorite_dto = store.asset_at(0)
    assert store.count() == 1
    assert favorite_dto is not None
    assert favorite_dto.abs_path == album_root / "photo.jpg"


def test_pending_restore_keeps_existing_aggregate_rows(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Library"
    trash_root = root / RECENTLY_DELETED_DIR_NAME
    album_root = root / "Album"
    other_root = root / "Other"
    trash_root.mkdir(parents=True)
    album_root.mkdir()
    other_root.mkdir()
    trashed_asset = Asset(
        id="photo",
        album_id="trash",
        path=Path(f"{RECENTLY_DELETED_DIR_NAME}/photo.jpg"),
        parent_album_path=RECENTLY_DELETED_DIR_NAME,
        media_type=MediaType.IMAGE,
        size_bytes=1,
        is_favorite=True,
    )
    existing_asset = Asset(
        id="existing",
        album_id="other",
        path=Path("Other/existing.jpg"),
        parent_album_path="Other",
        media_type=MediaType.IMAGE,
        size_bytes=1,
        is_favorite=True,
    )
    service = _FakeQueryService([trashed_asset, existing_asset], library_root=root)
    store = GalleryCollectionStore(service, library_root=root)
    store._path_cache.exists_cached = lambda path: True  # type: ignore[method-assign]

    store.load_selection(
        trash_root,
        query=AssetQuery(album_path=RECENTLY_DELETED_DIR_NAME),
    )
    store.apply_optimistic_move(
        [trash_root / "photo.jpg"],
        album_root,
        is_delete=False,
    )

    store.load_selection(root, query=AssetQuery())

    aggregate_dtos = list(store._row_cache.values())
    assert store.count() == 2
    assert [dto.abs_path for dto in aggregate_dtos] == [
        other_root / "existing.jpg",
        album_root / "photo.jpg",
    ]


def test_pending_restore_does_not_duplicate_restored_database_row(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Library"
    trash_root = root / RECENTLY_DELETED_DIR_NAME
    album_root = root / "Album"
    trash_root.mkdir(parents=True)
    album_root.mkdir()
    trashed_asset = Asset(
        id="trash-photo",
        album_id="trash",
        path=Path(f"{RECENTLY_DELETED_DIR_NAME}/photo.jpg"),
        parent_album_path=RECENTLY_DELETED_DIR_NAME,
        media_type=MediaType.IMAGE,
        size_bytes=1,
        is_favorite=True,
    )
    service = _FakeQueryService([trashed_asset], library_root=root)
    store = GalleryCollectionStore(service, library_root=root)
    store._path_cache.exists_cached = lambda path: True  # type: ignore[method-assign]

    store.load_selection(
        trash_root,
        query=AssetQuery(album_path=RECENTLY_DELETED_DIR_NAME),
    )
    store.apply_optimistic_move(
        [trash_root / "photo.jpg"],
        album_root,
        is_delete=False,
    )

    restored_asset = Asset(
        id="restored-different-id",
        album_id="a",
        path=Path("Album/photo.jpg"),
        parent_album_path="Album",
        media_type=MediaType.IMAGE,
        size_bytes=1,
        is_favorite=True,
    )
    service.assets.append(restored_asset)

    store.load_selection(root, query=AssetQuery())
    aggregate_dtos = list(store._row_cache.values())
    assert store.count() == 1
    assert [dto.id for dto in aggregate_dtos] == ["restored-different-id"]
    assert [dto.abs_path for dto in aggregate_dtos] == [album_root / "photo.jpg"]

    store.load_selection(album_root, query=AssetQuery(album_path="Album"))
    album_dtos = list(store._row_cache.values())
    assert store.count() == 1
    assert [dto.id for dto in album_dtos] == ["restored-different-id"]
    assert [dto.abs_path for dto in album_dtos] == [album_root / "photo.jpg"]

    store.load_selection(root, query=AssetQuery(is_favorite=True))
    favorite_dtos = list(store._row_cache.values())
    assert store.count() == 1
    assert [dto.id for dto in favorite_dtos] == ["restored-different-id"]
    assert [dto.abs_path for dto in favorite_dtos] == [album_root / "photo.jpg"]


def test_pending_delete_offsets_deep_window_after_source_before_window(tmp_path: Path) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    deleted_root = root / RECENTLY_DELETED_DIR_NAME
    assets = [
        Asset(
            id=f"asset{i}",
            album_id="a",
            path=Path(f"Album/asset{i}.jpg"),
            parent_album_path="Album",
            media_type=MediaType.IMAGE,
            size_bytes=1,
        )
        for i in range(500)
    ]
    service = _FakeQueryService(assets, library_root=root)
    store = GalleryCollectionStore(service, library_root=root)
    store._path_cache.exists_cached = lambda path: True  # type: ignore[method-assign]

    store.load_selection(root, query=AssetQuery())
    store.apply_optimistic_move(
        [root / "Album" / "asset0.jpg"],
        deleted_root,
        is_delete=True,
    )
    store.load_selection(root, query=AssetQuery())
    service.read_calls.clear()

    assert store.ensure_row_loaded(350) is True

    dto = store.asset_at(350)
    assert dto is not None
    assert dto.id == "asset351"
    assert service.read_calls[-1][0] == 350


def test_row_for_path_accounts_for_pending_sources_before_raw_row(tmp_path: Path) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    deleted_root = root / RECENTLY_DELETED_DIR_NAME
    assets = [
        Asset(
            id=f"asset{i}",
            album_id="a",
            path=Path(f"Album/asset{i}.jpg"),
            parent_album_path="Album",
            media_type=MediaType.IMAGE,
            size_bytes=1,
        )
        for i in range(5)
    ]
    service = _FakeQueryService(assets, library_root=root)
    store = GalleryCollectionStore(service, library_root=root)
    store._path_cache.exists_cached = lambda path: True  # type: ignore[method-assign]
    source = root / "Album" / "asset0.jpg"

    store.load_selection(root, query=AssetQuery())
    store.apply_optimistic_move([source], deleted_root, is_delete=True)
    store.load_selection(root, query=AssetQuery())

    assert store.row_for_path(source) is None
    assert store.row_for_path(root / "Album" / "asset4.jpg") == 3


def test_pending_delete_from_physical_album_offsets_aggregate_deep_window(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Library"
    album_root = root / "Album"
    album_root.mkdir(parents=True)
    deleted_root = root / RECENTLY_DELETED_DIR_NAME
    assets = [
        Asset(
            id=f"asset{i}",
            album_id="a",
            path=Path(f"Album/asset{i}.jpg"),
            parent_album_path="Album",
            media_type=MediaType.IMAGE,
            size_bytes=1,
        )
        for i in range(500)
    ]
    service = _FakeQueryService(assets, library_root=root)
    store = GalleryCollectionStore(service, library_root=root)
    store._path_cache.exists_cached = lambda path: True  # type: ignore[method-assign]

    store.load_selection(album_root, query=AssetQuery(album_path="Album"))
    store.apply_optimistic_move(
        [album_root / "asset0.jpg"],
        deleted_root,
        is_delete=True,
    )
    store.load_selection(root, query=AssetQuery())
    service.read_calls.clear()

    assert store.ensure_row_loaded(350) is True

    dto = store.asset_at(350)
    assert dto is not None
    assert dto.id == "asset351"
    assert service.read_calls[-1][0] == 350
    assert store.row_for_path(root / "Album" / "asset351.jpg") == 350


def test_prioritize_rows_loads_deep_window_without_sync_asset_at_fetch() -> None:
    assets = [
        Asset(
            id=str(i),
            album_id="a",
            path=Path(f"asset_{i}.jpg"),
            media_type=MediaType.IMAGE,
            size_bytes=1,
        )
        for i in range(10_000)
    ]
    service = _FakeQueryService(assets)
    store = GalleryCollectionStore(service, library_root=Path("."))
    store._path_cache.exists_cached = lambda path: True  # type: ignore[method-assign]

    store.load_selection(Path("."), query=AssetQuery())
    store.prioritize_rows(7_000, 7_060)

    assert store._window_range is not None
    assert store._window_range[0] <= 7_000 <= store._window_range[1]
    assert 7_000 in store._row_cache
    assert store._row_cache[7_000].rel_path == Path("asset_7000.jpg")


def test_prioritize_rows_emits_window_update_without_full_refresh_when_count_unchanged() -> None:
    assets = [
        Asset(
            id=str(i),
            album_id="a",
            path=Path(f"asset_{i}.jpg"),
            media_type=MediaType.IMAGE,
            size_bytes=1,
        )
        for i in range(1_000)
    ]
    service = _FakeQueryService(assets)
    store = GalleryCollectionStore(service, library_root=Path("."))
    store._path_cache.exists_cached = lambda path: True  # type: ignore[method-assign]

    store.load_selection(Path("."), query=AssetQuery())
    data_events: list[object] = []
    window_events: list[tuple[int, int]] = []
    store.data_changed.connect(lambda: data_events.append(object()))
    store.window_changed.connect(lambda first, last: window_events.append((first, last)))

    store.prioritize_rows(600, 660)

    assert data_events == []
    assert window_events
    assert store._window_range is not None
    assert store._window_range[0] <= 600 <= store._window_range[1]


def test_ensure_row_loaded_fetches_bounded_deep_window() -> None:
    assets = [
        Asset(
            id=str(i),
            album_id="a",
            path=Path(f"asset_{i}.jpg"),
            media_type=MediaType.IMAGE,
            size_bytes=1,
        )
        for i in range(10_000)
    ]
    service = _FakeQueryService(assets)
    store = GalleryCollectionStore(service, library_root=Path("."))
    store._path_cache.exists_cached = lambda path: True  # type: ignore[method-assign]

    store.load_selection(Path("."), query=AssetQuery())
    service.read_calls.clear()

    assert store.ensure_row_loaded(7_000) is True

    assert store._window_range is not None
    assert store._window_range[0] <= 7_000 <= store._window_range[1]
    assert store.asset_at(7_000).rel_path == Path("asset_7000.jpg")
    assert service.read_calls
    assert service.read_calls[-1][1] <= store.MAX_WINDOW_SIZE


def test_prioritize_rows_clamps_oversized_visible_range_to_bounded_window() -> None:
    assets = [
        Asset(
            id=str(i),
            album_id="a",
            path=Path(f"asset_{i}.jpg"),
            media_type=MediaType.IMAGE,
            size_bytes=1,
        )
        for i in range(10_000)
    ]
    service = _FakeQueryService(assets)
    store = GalleryCollectionStore(service, library_root=Path("."))
    store._path_cache.exists_cached = lambda path: True  # type: ignore[method-assign]

    store.load_selection(Path("."), query=AssetQuery())
    service.read_calls.clear()
    store.prioritize_rows(7_000, 9_999)

    assert store._window_range == (7_000, 8_999)
    assert service.read_calls[-1] == (7_000, store.MAX_WINDOW_SIZE)
    assert len(store._row_cache) == store.MAX_WINDOW_SIZE


def test_window_fetch_failure_does_not_block_later_visible_range_requests() -> None:
    assets = [
        Asset(
            id=str(i),
            album_id="a",
            path=Path(f"asset_{i}.jpg"),
            media_type=MediaType.IMAGE,
            size_bytes=1,
        )
        for i in range(10_000)
    ]
    service = _FailingOnceQueryService(assets, fail_offset=6_000)
    store = GalleryCollectionStore(service, library_root=Path("."))
    store._path_cache.exists_cached = lambda path: True  # type: ignore[method-assign]

    store.load_selection(Path("."), query=AssetQuery())
    store.prioritize_rows(7_000, 7_060)

    assert service.failed is True
    assert 7_000 not in store._row_cache

    store.prioritize_rows(7_200, 7_260)

    assert 7_200 in store._row_cache
    assert store._row_cache[7_200].rel_path == Path("asset_7200.jpg")


def test_ensure_row_loaded_recovers_after_deep_window_failure() -> None:
    assets = [
        Asset(
            id=str(i),
            album_id="a",
            path=Path(f"asset_{i}.jpg"),
            media_type=MediaType.IMAGE,
            size_bytes=1,
        )
        for i in range(10_000)
    ]
    service = _FailingOnceQueryService(assets, fail_offset=6_000)
    store = GalleryCollectionStore(service, library_root=Path("."))
    store._path_cache.exists_cached = lambda path: True  # type: ignore[method-assign]

    store.load_selection(Path("."), query=AssetQuery())

    assert store.ensure_row_loaded(7_000) is False
    assert service.failed is True

    assert store.ensure_row_loaded(7_200) is True
    assert store.asset_at(7_200).rel_path == Path("asset_7200.jpg")


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


def test_handle_scan_batch_refreshes_when_new_row_sorts_into_visible_window(tmp_path: Path) -> None:
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
    store.handle_scan_batch(
        SimpleNamespace(
            root=root,
            rows=[
                {
                    "rel": "new_visible.jpg",
                    "id": "scan-1",
                    "dt": midpoint.created_at.isoformat(),
                }
            ],
        )
    )

    assert refreshed


def test_handle_scan_batch_refreshes_empty_initial_window(tmp_path: Path) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    query_service = _FakeQueryService([], library_root=root)
    store = GalleryCollectionStore(query_service, library_root=root)
    store._path_cache.exists_cached = lambda path: True  # type: ignore[method-assign]
    store.set_active_root(root)
    store.load_selection(root, query=AssetQuery())

    assert store.count() == 0
    assert store.snapshot_signature()[1] is None

    query_service.assets.append(
        Asset(
            id="scan-new",
            album_id="a",
            path=Path("new_asset.jpg"),
            media_type=MediaType.IMAGE,
            size_bytes=1,
            created_at=datetime(2024, 1, 1, 12, 0, 0),
        )
    )

    store.handle_scan_batch(
        SimpleNamespace(
            root=root,
            collection_revision=2,
            rows=[
                {
                    "rel": "new_asset.jpg",
                    "id": "scan-new",
                    "thumbnail_state": "ready",
                    "thumb_cache_key": "thumb-new",
                }
            ],
        )
    )

    assert store.count() == 1
    assert store.asset_at(0) is not None


def test_scan_batch_library_relative_rel_does_not_double_prefix_in_library_view(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Library"
    album_root = root / "Album"
    album_root.mkdir(parents=True)
    query_service = _FakeQueryService([], library_root=root)
    store = GalleryCollectionStore(query_service, library_root=root)
    store._path_cache.exists_cached = lambda path: True  # type: ignore[method-assign]
    store.load_selection(root, query=AssetQuery())

    batch = SimpleNamespace(
        root=album_root,
        collection_revision=2,
        rows=[
            {
                "rel": "Album/a.jpg",
                "id": "scan-new",
                "thumbnail_state": "ready",
                "thumb_cache_key": "thumb-new",
            }
        ],
    )

    assert store.record_scan_batch(batch) is True
    assert store._pending_scan_rels == {"Album/a.jpg"}

    query_service.assets.append(
        Asset(
            id="scan-new",
            album_id="a",
            path=Path("Album/a.jpg"),
            parent_album_path="Album",
            media_type=MediaType.IMAGE,
            size_bytes=1,
            created_at=datetime(2024, 1, 1, 12, 0, 0),
        )
    )
    store.flush_pending_scan_refresh()

    dto = store.asset_at(0)
    assert store.count() == 1
    assert dto is not None
    assert dto.rel_path == Path("Album/a.jpg")


def test_scan_batch_album_relative_rel_maps_to_library_view_rel(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Library"
    album_root = root / "Album"
    album_root.mkdir(parents=True)
    query_service = _FakeQueryService([], library_root=root)
    store = GalleryCollectionStore(query_service, library_root=root)
    store._path_cache.exists_cached = lambda path: True  # type: ignore[method-assign]
    store.load_selection(root, query=AssetQuery())

    batch = SimpleNamespace(
        root=album_root,
        collection_revision=2,
        rows=[
            {
                "rel": "a.jpg",
                "id": "scan-new",
                "thumbnail_state": "ready",
                "thumb_cache_key": "thumb-new",
            }
        ],
    )

    assert store.record_scan_batch(batch) is True
    assert store._pending_scan_rels == {"Album/a.jpg"}

    query_service.assets.append(
        Asset(
            id="scan-new",
            album_id="a",
            path=Path("Album/a.jpg"),
            parent_album_path="Album",
            media_type=MediaType.IMAGE,
            size_bytes=1,
            created_at=datetime(2024, 1, 1, 12, 0, 0),
        )
    )
    store.flush_pending_scan_refresh()

    dto = store.asset_at(0)
    assert store.count() == 1
    assert dto is not None
    assert dto.rel_path == Path("Album/a.jpg")


def test_scan_batch_library_relative_rel_maps_to_album_view_rel(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Library"
    album_root = root / "Album"
    album_root.mkdir(parents=True)
    query_service = _FakeQueryService([], library_root=root)
    store = GalleryCollectionStore(query_service, library_root=root)
    store._path_cache.exists_cached = lambda path: True  # type: ignore[method-assign]
    store.load_selection(album_root, query=AssetQuery(album_path="Album"))

    batch = SimpleNamespace(
        root=album_root,
        collection_revision=2,
        rows=[
            {
                "rel": "Album/a.jpg",
                "id": "scan-new",
                "thumbnail_state": "ready",
                "thumb_cache_key": "thumb-new",
            }
        ],
    )

    assert store.record_scan_batch(batch) is True
    assert store._pending_scan_rels == {"a.jpg"}

    query_service.assets.append(
        Asset(
            id="scan-new",
            album_id="a",
            path=Path("Album/a.jpg"),
            parent_album_path="Album",
            media_type=MediaType.IMAGE,
            size_bytes=1,
            created_at=datetime(2024, 1, 1, 12, 0, 0),
        )
    )
    store.flush_pending_scan_refresh()

    dto = store.asset_at(0)
    assert store.count() == 1
    assert dto is not None
    assert dto.rel_path == Path("a.jpg")


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

    store.handle_scan_batch(
        SimpleNamespace(
            root=root,
            rows=[
                {
                    "rel": "new_asset.jpg",
                    "id": "scan-new",
                    "dt": (base_dt + timedelta(minutes=5)).isoformat(),
                }
            ],
        )
    )

    assert store.count() == 240

    store.handle_scan_finished(root, True)

    assert store.count() == 241
    visible_dto = store.asset_at(120)
    assert visible_dto is not None
    assert visible_dto.rel_path == Path("asset_119.jpg")
    assert store.asset_at(0) is None

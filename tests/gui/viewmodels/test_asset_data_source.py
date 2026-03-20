import pytest
from unittest.mock import Mock
from datetime import datetime, timedelta
from pathlib import Path
from iPhoto.domain.models import Asset, MediaType
from iPhoto.domain.models.query import AssetQuery
from iPhoto.gui.viewmodels.asset_data_source import AssetDataSource


class _FakeRepo:
    def __init__(self, assets):
        self.assets = list(assets)

    def find_by_query(self, query: AssetQuery):
        offset = query.offset
        limit = query.limit if query.limit is not None else len(self.assets)
        return self.assets[offset : offset + limit]

    def count(self, query: AssetQuery):
        return len(self.assets)


def test_to_dto_converts_media_type_correctly():
    # Arrange
    repo = Mock()
    source = AssetDataSource(repo)

    asset_video = Asset(
        id="1", album_id="a", path=Path("video.mp4"),
        media_type=MediaType.VIDEO, size_bytes=0
    )
    asset_image = Asset(
        id="2", album_id="a", path=Path("image.jpg"),
        media_type=MediaType.IMAGE, size_bytes=0
    )

    # Act
    dto_video = source._to_dto(asset_video)
    dto_image = source._to_dto(asset_image)

    # Assert
    assert dto_video.media_type == "video"
    assert dto_video.is_video is True

    assert dto_image.media_type == "image" or dto_image.media_type == "photo"
    assert dto_image.is_image is True

def test_to_dto_handles_int_media_type():
    # Arrange
    repo = Mock()
    source = AssetDataSource(repo)

    # Simulate an asset coming from repository with legacy int/string
    # Asset dataclass expects MediaType enum, but in python we can pass anything
    asset_video_legacy = Asset(
        id="1", album_id="a", path=Path("video.mp4"),
        media_type="2", size_bytes=0
    )

    dto = source._to_dto(asset_video_legacy)
    assert dto.media_type == "video"
    assert dto.is_video is True

    asset_video_str = Asset(
        id="1", album_id="a", path=Path("video.mp4"),
        media_type="MediaType.VIDEO", size_bytes=0
    )
    dto2 = source._to_dto(asset_video_str)
    assert dto2.media_type == "video"

def test_to_dto_handles_none_width_height():
    """Regression test: to_dto must not raise when width/height are None."""
    repo = Mock()
    source = AssetDataSource(repo)

    asset = Asset(
        id="3", album_id="a", path=Path("image.jpg"),
        media_type=MediaType.IMAGE, size_bytes=0,
        width=None, height=None,
    )

    # Should not raise TypeError
    dto = source._to_dto(asset)
    assert dto.width == 0
    assert dto.height == 0
    assert dto.is_pano is False

def test_update_favorite_status():
    repo = Mock()
    source = AssetDataSource(repo)

    # Directly convert a domain Asset to DTO and pre-populate the data source.
    asset = Asset(id="1", album_id="a", path=Path("p.jpg"), media_type=MediaType.IMAGE, size_bytes=0, is_favorite=False)
    dto = source._to_dto(asset)
    source._row_cache[0] = dto
    source._total_count = 1

    assert source.asset_at(0).is_favorite is False

    source.update_favorite_status(0, True)
    assert source.asset_at(0).is_favorite is True


def test_load_initial_window_uses_sparse_cache():
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
    source = AssetDataSource(_FakeRepo(assets), library_root=Path("."))
    source._path_cache.exists_cached = lambda path: True  # type: ignore[method-assign]

    source.load(AssetQuery())

    assert source.count() == 600
    assert 0 < len(source._row_cache) <= source.MAX_WINDOW_SIZE
    assert min(source._row_cache) == 0


def test_prioritize_rows_replaces_old_window_with_new_window():
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
    source = AssetDataSource(_FakeRepo(assets), library_root=Path("."))
    source._path_cache.exists_cached = lambda path: True  # type: ignore[method-assign]
    source.load(AssetQuery())

    initial_keys = set(source._row_cache)
    source.prioritize_rows(900, 940)
    updated_keys = set(source._row_cache)

    assert source.count() == 1200
    assert 900 in updated_keys
    assert len(updated_keys) <= source.MAX_WINDOW_SIZE + 1  # pinned rows may add one extra
    assert initial_keys != updated_keys


def test_handle_scan_chunk_refreshes_when_new_row_sorts_into_visible_window(tmp_path):
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
    source = AssetDataSource(_FakeRepo(assets), library_root=root)
    source._path_cache.exists_cached = lambda path: True  # type: ignore[method-assign]
    source.set_active_root(root)
    source.load(AssetQuery())
    source.prioritize_rows(120, 140)

    scheduled = []
    source._schedule_scan_refresh = lambda: scheduled.append(True)  # type: ignore[method-assign]

    visible_rows = [source._row_cache[row] for row in range(120, 141) if row in source._row_cache]
    midpoint = visible_rows[len(visible_rows) // 2]

    source.handle_scan_chunk(
        root,
        [{
            "rel": "new_visible.jpg",
            "id": "scan-1",
            "dt": midpoint.created_at.isoformat(),
        }],
    )

    assert scheduled == [True]


def test_handle_scan_chunk_defers_when_new_row_sorts_outside_visible_window(tmp_path):
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
    source = AssetDataSource(_FakeRepo(assets), library_root=root)
    source._path_cache.exists_cached = lambda path: True  # type: ignore[method-assign]
    source.set_active_root(root)
    source.load(AssetQuery())
    source.prioritize_rows(120, 140)

    scheduled = []
    source._schedule_scan_refresh = lambda: scheduled.append(True)  # type: ignore[method-assign]

    source.handle_scan_chunk(
        root,
        [{
            "rel": "new_top.jpg",
            "id": "scan-2",
            "dt": (base_dt + timedelta(days=2)).isoformat(),
        }],
    )

    assert scheduled == []


def test_asset_at_loads_visible_row_on_demand():
    assets = [
        Asset(
            id=str(i),
            album_id="a",
            path=Path(f"asset_{i}.jpg"),
            media_type=MediaType.IMAGE,
            size_bytes=1,
        )
        for i in range(200)
    ]
    source = AssetDataSource(_FakeRepo(assets), library_root=Path("."))
    source._path_cache.exists_cached = lambda path: True  # type: ignore[method-assign]
    source.load(AssetQuery())
    source._row_cache.pop(10)
    source._visible_range = (10, 10)

    dto = source.asset_at(10)

    assert dto is not None
    assert dto.rel_path == Path("asset_10.jpg")
    assert 10 in source._row_cache


def test_load_clears_pending_move_state():
    assets = [
        Asset(
            id="1",
            album_id="a",
            path=Path("asset.jpg"),
            media_type=MediaType.IMAGE,
            size_bytes=1,
        )
    ]
    source = AssetDataSource(_FakeRepo(assets), library_root=Path("."))
    source._pending_moves.append(Mock())
    source._pending_paths.add("C:\\temp\\asset.jpg")
    source._path_cache.exists_cached = lambda path: True  # type: ignore[method-assign]

    source.load(AssetQuery())

    assert source._pending_moves == []
    assert source._pending_paths == set()

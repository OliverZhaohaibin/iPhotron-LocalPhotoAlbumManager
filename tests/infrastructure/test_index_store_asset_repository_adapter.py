from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from iPhoto.cache.index_store import get_global_repository, reset_global_repository
from iPhoto.domain.models import Asset, MediaType
from iPhoto.domain.models.query import AssetQuery, SortOrder
from iPhoto.infrastructure.repositories.index_store_asset_repository import (
    IndexStoreAssetRepositoryAdapter,
)


@pytest.fixture(autouse=True)
def _reset_global_index() -> None:
    reset_global_repository()
    yield
    reset_global_repository()


@pytest.fixture
def adapter(tmp_path: Path) -> IndexStoreAssetRepositoryAdapter:
    store = get_global_repository(tmp_path)
    store.write_rows(
        [
            {
                "rel": "Album/photo-a.jpg",
                "id": "asset-a",
                "parent_album_path": "Album",
                "dt": "2024-01-03T12:00:00",
                "ts": 1_704_284_800_000_000,
                "bytes": 100,
                "media_type": 0,
                "is_favorite": 0,
                "gps": {"lat": 35.0, "lon": 139.0},
                "live_role": 0,
                "face_status": "pending",
            },
            {
                "rel": "Album/photo-b.jpg",
                "id": "asset-b",
                "parent_album_path": "Album",
                "dt": "2024-01-02T12:00:00",
                "ts": 1_704_198_400_000_000,
                "bytes": 200,
                "media_type": 0,
                "is_favorite": 1,
                "live_role": 0,
                "face_status": "pending",
            },
            {
                "rel": "Album/photo-b.mov",
                "id": "asset-b-motion",
                "parent_album_path": "Album",
                "dt": "2024-01-02T12:00:01",
                "ts": 1_704_198_401_000_000,
                "bytes": 300,
                "media_type": 1,
                "is_favorite": 0,
                "live_role": 1,
                "face_status": "skipped",
            },
        ]
    )
    return IndexStoreAssetRepositoryAdapter(store)


def test_reads_assets_by_id_and_path(
    adapter: IndexStoreAssetRepositoryAdapter,
    tmp_path: Path,
) -> None:
    by_id = adapter.get("asset-a")
    by_abs_path = adapter.get_by_path(tmp_path / "Album" / "photo-a.jpg")

    assert by_id is not None
    assert by_id.path == Path("Album/photo-a.jpg")
    assert by_id.metadata["gps"] == {"lat": 35.0, "lon": 139.0}
    assert by_abs_path is not None
    assert by_abs_path.id == "asset-a"


def test_queries_with_pagination_favorites_and_hidden_live_rows(
    adapter: IndexStoreAssetRepositoryAdapter,
) -> None:
    query = AssetQuery(album_path="Album")
    query.limit = 1
    query.offset = 1
    query.order_by = "created_at"
    query.order = SortOrder.DESC

    page = adapter.find_by_query(query)

    assert [asset.id for asset in page] == ["asset-b"]
    assert adapter.count(AssetQuery(album_path="Album")) == 2
    assert [asset.id for asset in adapter.find_by_query(AssetQuery(is_favorite=True))] == [
        "asset-b"
    ]
    assert adapter.find_by_query(AssetQuery(media_types=[MediaType.VIDEO])) == []


def test_save_updates_favorite_without_losing_index_store_fields(
    adapter: IndexStoreAssetRepositoryAdapter,
    tmp_path: Path,
) -> None:
    asset = adapter.get("asset-a")
    assert asset is not None
    asset.is_favorite = True
    adapter.save(asset)

    store = get_global_repository(tmp_path)
    row = store.get_rows_by_rels(["Album/photo-a.jpg"])["Album/photo-a.jpg"]

    assert row["is_favorite"] == 1
    assert row["gps"] == {"lat": 35.0, "lon": 139.0}
    assert row["face_status"] == "pending"


def test_save_preserves_existing_legacy_columns(tmp_path: Path) -> None:
    store = get_global_repository(tmp_path)
    with store.transaction() as conn:
        conn.execute("ALTER TABLE assets ADD COLUMN album_id TEXT")
        conn.execute("ALTER TABLE assets ADD COLUMN metadata TEXT")
        conn.execute("ALTER TABLE assets ADD COLUMN content_identifier TEXT")
        conn.execute("ALTER TABLE assets ADD COLUMN live_photo_group_id TEXT")
        conn.execute(
            """
            INSERT INTO assets (
                rel, id, parent_album_path, media_type, bytes, is_favorite,
                album_id, metadata, content_identifier, live_photo_group_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "Album/legacy.jpg",
                "legacy-asset",
                "Album",
                0,
                123,
                0,
                "legacy-album",
                json.dumps({"legacy": True}),
                "legacy-content",
                "legacy-live-group",
            ),
        )

    adapter = IndexStoreAssetRepositoryAdapter(store)
    asset = adapter.get("legacy-asset")
    assert asset is not None
    asset.is_favorite = True
    adapter.save(asset)

    row = store.get_rows_by_rels(["Album/legacy.jpg"])["Album/legacy.jpg"]

    assert row["is_favorite"] == 1
    assert row["album_id"] == "legacy-album"
    assert json.loads(row["metadata"])["legacy"] is True
    assert row["content_identifier"] == "legacy-content"
    assert row["live_photo_group_id"] == "legacy-live-group"
    assert [asset.id for asset in adapter.get_by_album("legacy-album")] == [
        "legacy-asset"
    ]


def test_save_batch_inserts_domain_asset_into_index_store(
    adapter: IndexStoreAssetRepositoryAdapter,
    tmp_path: Path,
) -> None:
    created = datetime(2024, 2, 1, 9, 30)
    adapter.save_batch(
        [
            Asset(
                id="asset-new",
                album_id="legacy-album",
                path=Path("Album/new.jpg"),
                media_type=MediaType.IMAGE,
                size_bytes=1234,
                created_at=created,
                width=640,
                height=480,
                is_favorite=True,
                parent_album_path="Album",
                metadata={"location": "Tokyo"},
            )
        ]
    )

    row = get_global_repository(tmp_path).get_rows_by_rels(["Album/new.jpg"])[
        "Album/new.jpg"
    ]

    assert row["id"] == "asset-new"
    assert row["dt"] == created.isoformat()
    assert row["bytes"] == 1234
    assert row["location"] == "Tokyo"
    assert row["is_favorite"] == 1


def test_delete_removes_index_store_row_by_rel(
    adapter: IndexStoreAssetRepositoryAdapter,
    tmp_path: Path,
) -> None:
    adapter.delete("asset-b")

    rows = get_global_repository(tmp_path).get_rows_by_rels(["Album/photo-b.jpg"])

    assert rows == {}

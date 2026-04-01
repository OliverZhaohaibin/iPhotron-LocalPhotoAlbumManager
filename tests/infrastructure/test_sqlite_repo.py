import pytest
import sqlite3
import json
from pathlib import Path
from datetime import datetime
from iPhoto.infrastructure.db.pool import ConnectionPool
from iPhoto.infrastructure.repositories.sqlite_asset_repository import SQLiteAssetRepository
from iPhoto.domain.models import Asset, MediaType
from iPhoto.domain.models.query import AssetQuery, SortOrder

@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test_db.sqlite"

@pytest.fixture
def pool(db_path):
    return ConnectionPool(db_path)

@pytest.fixture
def repo(pool):
    return SQLiteAssetRepository(pool)

@pytest.fixture
def sample_asset():
    return Asset(
        id="test_id_1",
        album_id="album_1",
        path=Path("album_1/photo.jpg"),
        media_type=MediaType.IMAGE,
        size_bytes=1024,
        created_at=datetime(2023, 1, 1, 12, 0, 0),
        width=1920,
        height=1080,
        duration=None,
        metadata={"iso": 100},
        content_identifier="cid_1",
        live_photo_group_id=None,
        is_favorite=False,
        parent_album_path="album_1"
    )

def test_repo_initialization(repo, db_path):
    assert db_path.exists()
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='assets'")
        assert cursor.fetchone() is not None

def test_save_and_get_asset(repo, sample_asset):
    repo.save(sample_asset)

    retrieved = repo.get(sample_asset.id)
    assert retrieved is not None
    assert retrieved.id == sample_asset.id
    assert retrieved.path == sample_asset.path
    assert retrieved.media_type == MediaType.IMAGE
    # The repository enriches metadata with live_role when persisting.
    for key, value in sample_asset.metadata.items():
        assert retrieved.metadata[key] == value
    assert retrieved.parent_album_path == "album_1"

def test_update_asset(repo, sample_asset):
    repo.save(sample_asset)

    # Modify
    sample_asset.is_favorite = True
    repo.save(sample_asset)

    retrieved = repo.get(sample_asset.id)
    assert retrieved.is_favorite is True

def test_find_by_query_album(repo, sample_asset):
    repo.save(sample_asset)

    # Another asset in different album
    asset2 = Asset(
        id="test_id_2",
        album_id="album_2",
        path=Path("album_2/photo.jpg"),
        media_type=MediaType.IMAGE,
        size_bytes=2048,
        parent_album_path="album_2",
        created_at=datetime.now()
    )
    repo.save(asset2)

    query = AssetQuery(album_path="album_1")
    results = repo.find_by_query(query)

    assert len(results) == 1
    assert results[0].id == "test_id_1"

def test_find_by_query_media_type(repo):
    a1 = Asset(id="1", album_id="x", size_bytes=1, path=Path("p1"), media_type=MediaType.IMAGE, created_at=datetime.now())
    a2 = Asset(id="2", album_id="x", size_bytes=1, path=Path("p2"), media_type=MediaType.VIDEO, created_at=datetime.now())
    repo.save_batch([a1, a2])

    query = AssetQuery(media_types=[MediaType.VIDEO])
    results = repo.find_by_query(query)

    assert len(results) == 1
    assert results[0].id == "2"

def test_find_by_query_date_range(repo):
    d1 = datetime(2023, 1, 1)
    d2 = datetime(2023, 2, 1)
    d3 = datetime(2023, 3, 1)

    a1 = Asset(id="1", album_id="x", size_bytes=1, path=Path("p1"), media_type=MediaType.IMAGE, created_at=d1)
    a2 = Asset(id="2", album_id="x", size_bytes=1, path=Path("p2"), media_type=MediaType.IMAGE, created_at=d2)
    a3 = Asset(id="3", album_id="x", size_bytes=1, path=Path("p3"), media_type=MediaType.IMAGE, created_at=d3)

    repo.save_batch([a1, a2, a3])

    query = AssetQuery(date_from=datetime(2023, 1, 15), date_to=datetime(2023, 2, 15))
    results = repo.find_by_query(query)

    assert len(results) == 1
    assert results[0].id == "2"

def test_pagination(repo):
    assets = [
        Asset(id=str(i), album_id="x", size_bytes=1, path=Path(f"p{i}"), media_type=MediaType.IMAGE, created_at=datetime(2023, 1, 1, 0, i))
        for i in range(10)
    ]
    repo.save_batch(assets)

    # Page 1, size 3, ordered by creation (default is usually order_by='ts' DESC in query default)
    # Let's be explicit
    query = AssetQuery(limit=3, offset=0, order_by='created_at', order=SortOrder.ASC)
    results = repo.find_by_query(query)

    assert len(results) == 3
    assert results[0].id == "0"
    assert results[2].id == "2"

    # Page 2
    query.offset = 3
    results = repo.find_by_query(query)
    assert len(results) == 3
    assert results[0].id == "3"


def test_repo_migrates_legacy_app_schema(tmp_path):
    db_path = tmp_path / "legacy_repo.sqlite"

    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE assets (
                id TEXT PRIMARY KEY,
                album_id TEXT,
                path TEXT,
                media_type TEXT,
                size_bytes INTEGER,
                created_at TEXT,
                width INTEGER,
                height INTEGER,
                duration REAL,
                metadata TEXT,
                content_identifier TEXT,
                live_photo_group_id TEXT
            )
        """)
        conn.executemany(
            """
            INSERT INTO assets
            (id, album_id, path, media_type, size_bytes, created_at, width, height,
             duration, metadata, content_identifier, live_photo_group_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "legacy-1",
                    "album-legacy",
                    "legacy/photo.jpg",
                    "photo",
                    321,
                    "2023-01-02T03:04:05",
                    640,
                    480,
                    None,
                    json.dumps({"iso": 200}),
                    "cid-legacy",
                    "group-legacy",
                ),
                (
                    "legacy-vid",
                    "album-legacy",
                    "legacy/clip.mp4",
                    "video",
                    999,
                    "2023-06-15T10:00:00",
                    1920,
                    1080,
                    4.25,
                    json.dumps({"codec": "h264"}),
                    "cid-vid",
                    None,
                ),
            ],
        )

    pool = ConnectionPool(db_path)
    repo = SQLiteAssetRepository(pool)

    # -- photo row --
    legacy = repo.get("legacy-1")
    assert legacy is not None
    assert legacy.path == Path("legacy/photo.jpg")
    assert legacy.size_bytes == 321
    assert legacy.created_at == datetime(2023, 1, 2, 3, 4, 5)
    assert legacy.width == 640
    assert legacy.height == 480
    assert legacy.media_type == MediaType.IMAGE
    assert legacy.content_identifier == "cid-legacy"
    assert legacy.metadata["iso"] == 200

    # -- video row --
    legacy_vid = repo.get("legacy-vid")
    assert legacy_vid is not None
    assert legacy_vid.path == Path("legacy/clip.mp4")
    assert legacy_vid.size_bytes == 999
    assert legacy_vid.created_at == datetime(2023, 6, 15, 10, 0, 0)
    assert legacy_vid.width == 1920
    assert legacy_vid.height == 1080
    assert legacy_vid.duration == pytest.approx(4.25)
    assert legacy_vid.media_type == MediaType.VIDEO
    assert legacy_vid.content_identifier == "cid-vid"
    assert legacy_vid.metadata["codec"] == "h264"

    # -- media-type filtering works after migration --
    photo_results = repo.find_by_query(AssetQuery(media_types=[MediaType.IMAGE]))
    assert any(a.id == "legacy-1" for a in photo_results)
    assert all(a.id != "legacy-vid" for a in photo_results)

    video_results = repo.find_by_query(AssetQuery(media_types=[MediaType.VIDEO]))
    assert any(a.id == "legacy-vid" for a in video_results)
    assert all(a.id != "legacy-1" for a in video_results)

    # -- new asset save/retrieve after migration --
    repo.save(
        Asset(
            id="legacy-2",
            album_id="album-legacy",
            path=Path("legacy/new.jpg"),
            media_type=MediaType.IMAGE,
            size_bytes=111,
            created_at=datetime(2024, 1, 1, 12, 0, 0),
            metadata={"flag": True},
            parent_album_path="legacy",
        )
    )

    saved = repo.get("legacy-2")
    assert saved is not None
    assert saved.path == Path("legacy/new.jpg")
    assert saved.parent_album_path == "legacy"
    assert saved.metadata["flag"] is True

    # -- VIDEO asset saved post-migration also round-trips correctly --
    repo.save(
        Asset(
            id="new-vid",
            album_id="album-legacy",
            path=Path("legacy/new_clip.mp4"),
            media_type=MediaType.VIDEO,
            size_bytes=555,
            created_at=datetime(2024, 2, 1, 8, 0, 0),
            duration=10.0,
            parent_album_path="legacy",
        )
    )
    new_vid = repo.get("new-vid")
    assert new_vid is not None
    assert new_vid.media_type == MediaType.VIDEO
    assert new_vid.duration == pytest.approx(10.0)

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from iPhoto.cache.index_store import IndexStore
from iPhoto.cache.index_store.queries import QueryBuilder
from iPhoto.config import RECENTLY_DELETED_DIR_NAME
from iPhoto.domain.models.query import CollectionQuery, CollectionType, PageCursor


@pytest.fixture
def store(tmp_path: Path) -> IndexStore:
    return IndexStore(tmp_path)

def test_sync_favorites(store: IndexStore) -> None:
    """Test synchronizing favorites from a list."""
    rows = [
        {"rel": "a.jpg", "is_favorite": 0},
        {"rel": "b.jpg", "is_favorite": 1},
        {"rel": "c.jpg", "is_favorite": 0},
    ]
    store.write_rows(rows)

    # Sync: a=Fav, b=NotFav, c=NotFav
    store.sync_favorites(["a.jpg"])

    data = {r["rel"]: r["is_favorite"] for r in store.read_all()}
    assert data["a.jpg"] == 1
    assert data["b.jpg"] == 0
    assert data["c.jpg"] == 0

def test_sync_favorites_invalid_paths(store: IndexStore) -> None:
    """Test syncing with paths not in the DB (should be ignored)."""
    rows = [{"rel": "a.jpg", "is_favorite": 0}]
    store.write_rows(rows)

    store.sync_favorites(["a.jpg", "missing.jpg"])

    data = {r["rel"]: r["is_favorite"] for r in store.read_all()}
    assert data["a.jpg"] == 1
    # missing.jpg is ignored

def test_sync_favorites_generator(store: IndexStore) -> None:
    """Test syncing with a generator (verify list conversion fix)."""
    rows = [{"rel": "a.jpg", "is_favorite": 0}]
    store.write_rows(rows)

    gen = (x for x in ["a.jpg"])
    store.sync_favorites(gen)

    data = {r["rel"]: r["is_favorite"] for r in store.read_all()}
    assert data["a.jpg"] == 1

def test_set_favorite_status(store: IndexStore) -> None:
    """Test efficient single-item toggle."""
    rows = [{"rel": "a.jpg", "is_favorite": 0}]
    store.write_rows(rows)

    store.set_favorite_status("a.jpg", True)
    data = {r["rel"]: r["is_favorite"] for r in store.read_all()}
    assert data["a.jpg"] == 1

    store.set_favorite_status("a.jpg", False)
    data = {r["rel"]: r["is_favorite"] for r in store.read_all()}
    assert data["a.jpg"] == 0

def test_read_geometry_only(store: IndexStore) -> None:
    """Test lightweight fetching with columns and filtering."""
    rows = [
        {"rel": "video.mov", "media_type": 1, "is_favorite": 0, "dt": "2023-01-01"},
        {"rel": "photo.jpg", "media_type": 0, "is_favorite": 1, "dt": "2023-01-02"},
        {"rel": "live.jpg", "media_type": 0, "is_favorite": 0, "live_partner_rel": "live.mov", "dt": "2023-01-03"},
    ]
    store.write_rows(rows)

    # 1. Fetch All
    results = list(store.read_geometry_only(sort_by_date=True))
    assert len(results) == 3
    # Check fields
    assert "aspect_ratio" in results[0]
    assert "year" in results[0]
    assert "mime" in results[0]
    # Verify sorting (dt DESC)
    assert results[0]["rel"] == "live.jpg"
    assert results[1]["rel"] == "photo.jpg"
    assert results[2]["rel"] == "video.mov"

    # 2. Filter Videos
    videos = list(store.read_geometry_only(filter_params={"filter_mode": "videos"}))
    assert len(videos) == 1
    assert videos[0]["rel"] == "video.mov"

    # 3. Filter Live
    live = list(store.read_geometry_only(filter_params={"filter_mode": "live"}))
    assert len(live) == 1
    assert live[0]["rel"] == "live.jpg"

    # 4. Filter Favorites
    favs = list(store.read_geometry_only(filter_params={"filter_mode": "favorites"}))
    assert len(favs) == 1
    assert favs[0]["rel"] == "photo.jpg"

    # 5. Invalid Filter
    with pytest.raises(ValueError, match="Invalid filter_mode"):
        list(store.read_geometry_only(filter_params={"filter_mode": "invalid"}))

    # 6. Invalid Media Type
    with pytest.raises(ValueError, match="Invalid media_type"):
        list(store.read_geometry_only(filter_params={"media_type": "string"}))

def test_read_geometry_only_sorting(store: IndexStore) -> None:
    """Verify detailed sorting behavior."""
    rows = [
        {"rel": "a.jpg", "dt": "2023-01-01T10:00:00Z"},
        {"rel": "b.jpg", "dt": "2023-01-01T11:00:00Z"}, # Newer
        {"rel": "c.jpg", "dt": None}, # Nulls last
    ]
    store.write_rows(rows)

    results = list(store.read_geometry_only(sort_by_date=True))
    rels = [r["rel"] for r in results]
    assert rels == ["b.jpg", "a.jpg", "c.jpg"]


def test_collection_query_sql_pushdown_filters_visible_ready_rows(store: IndexStore) -> None:
    base = datetime(2024, 1, 1)
    rows = []
    for index in range(6):
        timestamp = base + timedelta(seconds=index)
        rows.append(
            {
                "rel": f"Album/photo-{index}.jpg",
                "id": f"asset-{index}",
                "dt": timestamp.isoformat(),
                "ts": int(timestamp.timestamp() * 1_000_000),
                "parent_album_path": "Album",
                "media_type": 1 if index == 1 else 0,
                "is_favorite": 1 if index in {2, 3} else 0,
                "gps": {"lat": 1.0, "lon": 2.0} if index == 3 else None,
                "thumbnail_state": "pending" if index == 4 else "ready",
                "thumb_cache_key": f"thumb-{index}" if index != 4 else None,
                "live_role": 1 if index == 5 else 0,
            }
        )
    store.write_rows(rows)

    favorites = CollectionQuery(collection_type=CollectionType.FAVORITES)
    assert store.count_collection(favorites) == 2
    assert [row["id"] for row in store.read_collection_window(favorites, 0, 10).rows] == [
        "asset-3",
        "asset-2",
    ]

    videos = CollectionQuery(collection_type=CollectionType.VIDEOS)
    assert [row["id"] for row in store.read_collection_page(videos, limit=10).rows] == [
        "asset-1"
    ]

    gps = CollectionQuery(collection_type=CollectionType.MAP, has_gps=True)
    assert [row["id"] for row in store.read_collection_window(gps, 0, 10).rows] == [
        "asset-3"
    ]

    all_photos = CollectionQuery(collection_type=CollectionType.ALL_PHOTOS)
    assert [row["id"] for row in store.read_collection_window(all_photos, 0, 10).rows] == [
        "asset-3",
        "asset-2",
        "asset-1",
        "asset-0",
    ]


def test_ready_row_requires_thumbnail_payload(store: IndexStore) -> None:
    base = datetime(2024, 1, 1)
    store.write_rows(
        [
            {
                "rel": "ready.jpg",
                "id": "ready",
                "dt": base.isoformat(),
                "ts": int(base.timestamp() * 1_000_000),
                "media_type": 0,
                "thumbnail_state": "ready",
                "thumb_cache_key": "thumb-ready",
            },
            {
                "rel": "no-thumb.jpg",
                "id": "no-thumb",
                "dt": (base + timedelta(seconds=1)).isoformat(),
                "ts": int((base + timedelta(seconds=1)).timestamp() * 1_000_000),
                "media_type": 0,
                "thumbnail_state": "ready",
            },
        ]
    )

    rows = store.read_collection_window(CollectionQuery(), 0, 10).rows

    assert [row["id"] for row in rows] == ["ready"]
    assert store.get_rows_by_rels(["no-thumb.jpg"])["no-thumb.jpg"]["thumbnail_state"] == "stale"


def test_pending_failed_stale_rows_are_hidden_from_gallery_collection(store: IndexStore) -> None:
    base = datetime(2024, 1, 1)
    store.write_rows(
        {
            "rel": f"{state}.jpg",
            "id": state,
            "dt": (base + timedelta(seconds=index)).isoformat(),
            "ts": int((base + timedelta(seconds=index)).timestamp() * 1_000_000),
            "media_type": 0,
            "thumbnail_state": state,
            "thumb_cache_key": f"thumb-{state}" if state == "ready" else None,
        }
        for index, state in enumerate(("ready", "pending", "failed", "stale"))
    )

    rows = store.read_collection_window(CollectionQuery(), 0, 10).rows

    assert [row["id"] for row in rows] == ["ready"]


def test_collection_query_excludes_recently_deleted_from_normal_views(store: IndexStore) -> None:
    base = datetime(2024, 1, 1)
    store.write_rows(
        [
            {
                "rel": "Album/keep.jpg",
                "id": "keep",
                "parent_album_path": "Album",
                "dt": base.isoformat(),
                "ts": int(base.timestamp() * 1_000_000),
                "media_type": 0,
                "thumb_cache_key": "thumb-keep",
            },
            {
                "rel": f"{RECENTLY_DELETED_DIR_NAME}/deleted.jpg",
                "id": "deleted",
                "parent_album_path": RECENTLY_DELETED_DIR_NAME,
                "dt": (base + timedelta(seconds=1)).isoformat(),
                "ts": int((base + timedelta(seconds=1)).timestamp() * 1_000_000),
                "media_type": 0,
                "thumb_cache_key": "thumb-deleted",
            },
        ]
    )

    all_photos = CollectionQuery(collection_type=CollectionType.ALL_PHOTOS)
    assert [row["id"] for row in store.read_collection_window(all_photos, 0, 10).rows] == [
        "keep"
    ]

    trash = CollectionQuery(
        collection_type=CollectionType.ALBUM,
        album_path=RECENTLY_DELETED_DIR_NAME,
    )
    assert [row["id"] for row in store.read_collection_window(trash, 0, 10).rows] == [
        "deleted"
    ]


def test_collection_page_uses_sort_ts_keyset_cursor(store: IndexStore) -> None:
    base = datetime(2024, 1, 1)
    store.write_rows(
        {
            "rel": f"photo-{index}.jpg",
            "id": f"asset-{index}",
            "dt": (base + timedelta(seconds=index)).isoformat(),
            "ts": int((base + timedelta(seconds=index)).timestamp() * 1_000_000),
            "media_type": 0,
            "thumb_cache_key": f"thumb-{index}",
        }
        for index in range(5)
    )

    query = CollectionQuery(collection_type=CollectionType.ALL_PHOTOS)
    first = store.read_collection_page(query, limit=2)
    assert [row["id"] for row in first.rows] == ["asset-4", "asset-3"]
    assert first.next_cursor == PageCursor(
        sort_ts=first.rows[-1]["sort_ts"],
        asset_id="asset-3",
    )

    second = store.read_collection_page(query, cursor=first.next_cursor, limit=2)
    assert [row["id"] for row in second.rows] == ["asset-2", "asset-1"]


def test_find_row_by_path_uses_collection_lookup(store: IndexStore, tmp_path: Path) -> None:
    library_root = store.library_root
    base = datetime(2024, 1, 1)
    store.write_rows(
        {
            "rel": f"Album/photo-{index}.jpg",
            "id": f"asset-{index}",
            "dt": (base + timedelta(seconds=index)).isoformat(),
            "ts": int((base + timedelta(seconds=index)).timestamp() * 1_000_000),
            "parent_album_path": "Album",
            "media_type": 0,
            "thumb_cache_key": f"thumb-{index}",
        }
        for index in range(4)
    )

    query = CollectionQuery(collection_type=CollectionType.ALBUM, album_path="Album")

    assert store.find_row_by_path(query, library_root / "Album" / "photo-3.jpg") == 0
    assert store.find_row_by_path(query, library_root / "Album" / "photo-1.jpg") == 2
    assert store.find_row_by_path(query, tmp_path / "outside.jpg") is None


def test_collection_window_uses_anchor_seek_for_deep_offsets(
    store: IndexStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = datetime(2024, 1, 1)
    store.write_rows(
        {
            "rel": f"photo-{index:05d}.jpg",
            "id": f"asset-{index:05d}",
            "dt": (base + timedelta(seconds=index)).isoformat(),
            "ts": int((base + timedelta(seconds=index)).timestamp() * 1_000_000),
            "media_type": 0,
            "thumb_cache_key": f"thumb-{index}",
        }
        for index in range(10_000)
    )

    offsets: list[int] = []
    original_build_collection_query = QueryBuilder.build_collection_query

    def recording_build_collection_query(*args, **kwargs):
        offsets.append(int(kwargs.get("offset", 0) or 0))
        return original_build_collection_query(*args, **kwargs)

    monkeypatch.setattr(
        QueryBuilder,
        "build_collection_query",
        staticmethod(recording_build_collection_query),
    )

    query = CollectionQuery(collection_type=CollectionType.ALL_PHOTOS)
    window = store.read_collection_window(query, first=7_000, limit=120)

    assert window.first == 7_000
    assert len(window.rows) == 120
    assert window.rows[0]["id"] == "asset-02999"
    assert window.rows[-1]["id"] == "asset-02880"
    assert all(offset == 0 for offset in offsets)


def test_album_collection_deep_window_filters_non_visible_rows(store: IndexStore) -> None:
    base = datetime(2024, 1, 1)
    visible_rows = [
        {
            "rel": f"Album/photo-{index:05d}.jpg",
            "id": f"asset-{index:05d}",
            "parent_album_path": "Album",
            "dt": (base + timedelta(seconds=index)).isoformat(),
            "ts": int((base + timedelta(seconds=index)).timestamp() * 1_000_000),
            "media_type": 0,
            "thumb_cache_key": f"thumb-{index}",
        }
        for index in range(10_000)
    ]
    hidden_rows = [
        {
            "rel": "Album/pending.jpg",
            "id": "pending",
            "parent_album_path": "Album",
            "dt": (base + timedelta(days=1)).isoformat(),
            "ts": int((base + timedelta(days=1)).timestamp() * 1_000_000),
            "media_type": 0,
            "thumbnail_state": "pending",
        },
        {
            "rel": "Album/motion.mov",
            "id": "motion",
            "parent_album_path": "Album",
            "dt": (base + timedelta(days=2)).isoformat(),
            "ts": int((base + timedelta(days=2)).timestamp() * 1_000_000),
            "media_type": 1,
            "live_role": 1,
        },
        {
            "rel": "Other/photo.jpg",
            "id": "other",
            "parent_album_path": "Other",
            "dt": (base + timedelta(days=3)).isoformat(),
            "ts": int((base + timedelta(days=3)).timestamp() * 1_000_000),
            "media_type": 0,
            "thumb_cache_key": "thumb-other",
        },
    ]
    store.write_rows([*visible_rows, *hidden_rows])

    query = CollectionQuery(collection_type=CollectionType.ALBUM, album_path="Album")
    window = store.read_collection_window(query, first=7_000, limit=3)

    assert [row["id"] for row in window.rows] == [
        "asset-02999",
        "asset-02998",
        "asset-02997",
    ]


def test_sync_favorites_non_ascii(store: IndexStore) -> None:
    """Test synchronizing favorites with non-ASCII filenames."""
    rows = [
        {"rel": "café.jpg", "is_favorite": 0},
        {"rel": "文件.jpg", "is_favorite": 0},
        {"rel": "фото.jpg", "is_favorite": 1},
    ]
    store.write_rows(rows)

    # Sync: café=Fav, 文件=Fav, фото=NotFav
    store.sync_favorites(["café.jpg", "文件.jpg"])

    data = {r["rel"]: r["is_favorite"] for r in store.read_all()}
    assert data["café.jpg"] == 1
    assert data["文件.jpg"] == 1
    assert data["фото.jpg"] == 0

def test_sync_favorites_unicode_normalization(store: IndexStore) -> None:
    """Test synchronizing favorites with different Unicode normalization forms."""
    import unicodedata
    
    # Use NFD form (decomposed) in the database
    cafe_nfd = unicodedata.normalize("NFD", "café")  # e + combining acute accent
    rows = [
        {"rel": cafe_nfd, "is_favorite": 0},
        {"rel": "normal.jpg", "is_favorite": 0},
    ]
    store.write_rows(rows)

    # Use NFC form (composed) in the input
    cafe_nfc = unicodedata.normalize("NFC", "café")  # é as single character
    
    # These should match even though they're different byte sequences
    assert cafe_nfc != cafe_nfd
    assert unicodedata.normalize("NFC", cafe_nfc) == unicodedata.normalize("NFC", cafe_nfd)
    
    store.sync_favorites([cafe_nfc])

    # The database should have updated the row with the NFD key
    data = {r["rel"]: r["is_favorite"] for r in store.read_all()}
    assert data[cafe_nfd] == 1
    assert data["normal.jpg"] == 0

def test_sync_favorites_mixed_unicode_forms(store: IndexStore) -> None:
    """Test syncing when database and input use different Unicode forms."""
    import unicodedata
    
    # Store paths in different normalization forms
    rows = [
        {"rel": unicodedata.normalize("NFC", "café.jpg"), "is_favorite": 0},
        {"rel": unicodedata.normalize("NFD", "naïve.jpg"), "is_favorite": 1},
        {"rel": "regular.jpg", "is_favorite": 0},
    ]
    store.write_rows(rows)
    
    # Input uses opposite normalization forms
    input_list = [
        unicodedata.normalize("NFD", "café.jpg"),  # NFD form
        unicodedata.normalize("NFC", "naïve.jpg"),  # NFC form
    ]
    
    store.sync_favorites(input_list)
    
    # Both should be marked as favorites despite different normalization
    data = {r["rel"]: r["is_favorite"] for r in store.read_all()}
    assert data[unicodedata.normalize("NFC", "café.jpg")] == 1
    assert data[unicodedata.normalize("NFD", "naïve.jpg")] == 1
    assert data["regular.jpg"] == 0


def test_collection_window_reuses_count_revision_cache_until_invalidation(store: IndexStore) -> None:
    store.write_rows(
        [
                {
                    "rel": "ready.jpg",
                    "id": "ready",
                    "thumbnail_state": "ready",
                    "micro_thumbnail": b"thumb",
                    "thumb_cache_key": "thumb-ready",
                }
            ]
        )
    query = CollectionQuery()

    first = store.read_collection_window(query, 0, 10)
    second = store.read_collection_window(query, 0, 10)

    assert first.total_count == second.total_count == 1
    assert store._collection_meta_cache

    store.update_thumbnail_ready(
        "ready.jpg",
        micro_thumbnail=b"new-thumb",
        thumb_cache_key="thumb-ready",
    )

    assert store._collection_meta_cache == {}


def test_thumbnail_backfill_candidates_and_ready_update(store: IndexStore) -> None:
    store.write_rows(
        [
            {
                "rel": "stale.jpg",
                "id": "stale",
                "thumbnail_state": "stale",
            }
        ]
    )
    query = CollectionQuery()

    candidates = store.read_thumbnail_backfill_candidates(query, 0, 10)
    assert [row["rel"] for row in candidates] == ["stale.jpg"]

    store.update_thumbnail_ready(
        "stale.jpg",
        micro_thumbnail=b"thumb",
        thumb_cache_key="thumb-stale",
    )
    rows = store.read_collection_window(query, 0, 10).rows
    assert [row["rel"] for row in rows] == ["stale.jpg"]
    assert rows[0]["thumbnail_state"] == "ready"

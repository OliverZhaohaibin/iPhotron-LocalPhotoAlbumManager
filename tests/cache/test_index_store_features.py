from __future__ import annotations

from pathlib import Path
import pytest
from src.iPhoto.cache.index_store import IndexStore

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

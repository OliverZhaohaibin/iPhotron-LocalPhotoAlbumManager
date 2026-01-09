from __future__ import annotations

import sqlite3
from pathlib import Path
import pytest
from src.iPhoto.cache.index_store import IndexStore, GLOBAL_INDEX_DB_NAME
from src.iPhoto.config import WORK_DIR_NAME

@pytest.fixture
def store(tmp_path: Path) -> IndexStore:
    return IndexStore(tmp_path)

def test_init_creates_db(store: IndexStore, tmp_path: Path) -> None:
    db_path = tmp_path / WORK_DIR_NAME / GLOBAL_INDEX_DB_NAME
    assert db_path.exists()

    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='assets'")
        assert cursor.fetchone() is not None

def test_wal_mode_enabled(store: IndexStore, tmp_path: Path) -> None:
    # Check if journal_mode is WAL
    # Using a new connection because IndexStore manages its own connections transiently
    db_path = tmp_path / WORK_DIR_NAME / GLOBAL_INDEX_DB_NAME

    # We must invoke an operation on store to ensure _init_db runs and sets the mode
    # (actually __init__ runs it, so it should be set)

    # However, PRAGMA journal_mode is persistent for the database file in SQLite.
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]
        assert mode.upper() == "WAL"

def test_write_and_read_rows(store: IndexStore) -> None:
    rows = [
        {"rel": "a.jpg", "id": "1", "dt": "2023-01-01T10:00:00Z", "bytes": 100},
        {"rel": "b.jpg", "id": "2", "dt": "2023-01-02T10:00:00Z", "bytes": 200},
    ]
    store.write_rows(rows)

    read_rows = list(store.read_all())
    assert len(read_rows) == 2

    # Check content. Note that read_all returns extra fields as None
    row_a = next(r for r in read_rows if r["rel"] == "a.jpg")
    assert row_a["id"] == "1"
    assert row_a["bytes"] == 100
    assert row_a["gps"] is None

def test_gps_serialization(store: IndexStore) -> None:
    gps_data = {"lat": 51.5, "lon": -0.1}
    row = {
        "rel": "loc.jpg",
        "id": "3",
        "gps": gps_data,
        "ts": 123456
    }
    store.write_rows([row])

    read_rows = list(store.read_all())
    assert len(read_rows) == 1
    assert read_rows[0]["gps"] == gps_data

def test_read_geotagged(store: IndexStore) -> None:
    gps_data = {"lat": 51.5, "lon": -0.1}
    rows = [
        {"rel": "geo.jpg", "gps": gps_data},
        {"rel": "plain.jpg", "gps": None},
        {"rel": "geo2.jpg", "gps": {"lat": 0, "lon": 0}}
    ]
    store.write_rows(rows)

    geotagged = list(store.read_geotagged())
    assert len(geotagged) == 2
    rels = {r["rel"] for r in geotagged}
    assert "geo.jpg" in rels
    assert "geo2.jpg" in rels
    assert "plain.jpg" not in rels

def test_upsert_row(store: IndexStore) -> None:
    rows = [{"rel": "a.jpg", "id": "1"}]
    store.write_rows(rows)

    new_row = {"rel": "a.jpg", "id": "1_updated", "bytes": 500}
    store.upsert_row("a.jpg", new_row)

    read_rows = list(store.read_all())
    assert len(read_rows) == 1
    assert read_rows[0]["id"] == "1_updated"
    assert read_rows[0]["bytes"] == 500

def test_remove_rows(store: IndexStore) -> None:
    rows = [
        {"rel": "a.jpg", "id": "1"},
        {"rel": "b.jpg", "id": "2"},
        {"rel": "c.jpg", "id": "3"},
    ]
    store.write_rows(rows)

    store.remove_rows(["a.jpg", "c.jpg"])

    read_rows = list(store.read_all())
    assert len(read_rows) == 1
    assert read_rows[0]["rel"] == "b.jpg"

def test_append_rows(store: IndexStore) -> None:
    store.write_rows([{"rel": "a.jpg", "id": "1"}])

    new_rows = [
        {"rel": "b.jpg", "id": "2"},
        {"rel": "a.jpg", "id": "1_replaced"} # Should replace existing
    ]
    store.append_rows(new_rows)

    read_rows = list(store.read_all())
    assert len(read_rows) == 2

    row_a = next(r for r in read_rows if r["rel"] == "a.jpg")
    assert row_a["id"] == "1_replaced"

    row_b = next(r for r in read_rows if r["rel"] == "b.jpg")
    assert row_b["id"] == "2"

def test_count(store: IndexStore) -> None:
    assert store.count() == 0
    store.write_rows([{"rel": "a.jpg"}, {"rel": "b.jpg"}])
    assert store.count() == 2

def test_read_all_sorting(store: IndexStore) -> None:
    rows = [
        {"rel": "old.jpg", "dt": "2020-01-01"},
        {"rel": "null.jpg", "dt": None},
        {"rel": "new.jpg", "dt": "2023-01-01"},
        {"rel": "mid.jpg", "dt": "2022-01-01"},
    ]
    store.write_rows(rows)

    # Test sorted order
    sorted_rows = list(store.read_all(sort_by_date=True))
    assert sorted_rows[0]["rel"] == "new.jpg"
    assert sorted_rows[1]["rel"] == "mid.jpg"
    assert sorted_rows[2]["rel"] == "old.jpg"
    # NULL should be last due to "ORDER BY dt DESC NULLS LAST"
    assert sorted_rows[3]["rel"] == "null.jpg"

def test_transaction(store: IndexStore) -> None:
    with store.transaction():
        store.upsert_row("t1.jpg", {"bytes": 1})
        store.upsert_row("t2.jpg", {"bytes": 2})

    assert store.count() == 2

def test_transaction_rollback(store: IndexStore) -> None:
    store.upsert_row("init.jpg", {"bytes": 0})

    try:
        with store.transaction():
            store.upsert_row("t1.jpg", {"bytes": 1})
            raise RuntimeError("Abort!")
    except RuntimeError:
        pass

    # t1 should not be present due to rollback
    # init should still be present
    assert store.count() == 1
    rows = list(store.read_all())
    assert rows[0]["rel"] == "init.jpg"

    # Verify t1 is gone
    assert not any(r["rel"] == "t1.jpg" for r in rows)

def test_apply_live_role_updates(store: IndexStore) -> None:
    """Verify live role batch updates and reset behavior."""
    rows = [
        {"rel": "still.jpg", "id": "1"},
        {"rel": "motion.mov", "id": "2"},
        {"rel": "other.jpg", "id": "3"},
    ]
    store.write_rows(rows)

    # 1. Test batch update
    updates = [
        ("still.jpg", 0, "motion.mov"),
        ("motion.mov", 1, "still.jpg"),
    ]
    store.apply_live_role_updates(updates)

    data = {r["rel"]: r for r in store.read_all()}
    assert data["still.jpg"]["live_role"] == 0
    assert data["still.jpg"]["live_partner_rel"] == "motion.mov"
    assert data["motion.mov"]["live_role"] == 1
    assert data["motion.mov"]["live_partner_rel"] == "still.jpg"
    assert data["other.jpg"]["live_role"] == 0 # Default

    # 2. Test reset with empty list (should clear roles)
    store.apply_live_role_updates([])
    data = {r["rel"]: r for r in store.read_all()}
    assert data["still.jpg"]["live_role"] == 0
    assert data["still.jpg"]["live_partner_rel"] is None
    assert data["motion.mov"]["live_role"] == 0 # Reset to 0
    assert data["motion.mov"]["live_partner_rel"] is None

def test_read_all_filtered(store: IndexStore) -> None:
    """Verify filter_hidden excludes hidden assets."""
    rows = [
        {"rel": "visible.jpg", "id": "1"},
        {"rel": "hidden.mov", "id": "2"},
    ]
    store.write_rows(rows)
    store.apply_live_role_updates([("hidden.mov", 1, "visible.jpg")])

    # Non-filtered
    all_rows = list(store.read_all(filter_hidden=False))
    assert len(all_rows) == 2

    # Filtered
    visible_rows = list(store.read_all(filter_hidden=True))
    assert len(visible_rows) == 1
    assert visible_rows[0]["rel"] == "visible.jpg"

def test_count_filtered(store: IndexStore) -> None:
    """Verify count respects filter_hidden."""
    rows = [
        {"rel": "visible.jpg", "id": "1"},
        {"rel": "hidden.mov", "id": "2"},
    ]
    store.write_rows(rows)
    store.apply_live_role_updates([("hidden.mov", 1, "visible.jpg")])

    assert store.count(filter_hidden=False) == 2
    assert store.count(filter_hidden=True) == 1


# --------------------------------------------------------------------------
# Tests for new global index features
# --------------------------------------------------------------------------

def test_parent_album_path_computed(store: IndexStore) -> None:
    """Verify parent_album_path is computed from rel if not provided."""
    rows = [
        {"rel": "2023/Trip/photo1.jpg", "id": "1"},
        {"rel": "2023/Trip/photo2.jpg", "id": "2"},
        {"rel": "2024/Summer/img.jpg", "id": "3"},
        {"rel": "root_photo.jpg", "id": "4"},  # File at root, no parent
    ]
    store.write_rows(rows)

    read_rows = {r["rel"]: r for r in store.read_all()}
    assert read_rows["2023/Trip/photo1.jpg"]["parent_album_path"] == "2023/Trip"
    assert read_rows["2023/Trip/photo2.jpg"]["parent_album_path"] == "2023/Trip"
    assert read_rows["2024/Summer/img.jpg"]["parent_album_path"] == "2024/Summer"
    assert read_rows["root_photo.jpg"]["parent_album_path"] == ""


def test_get_assets_page_basic(store: IndexStore) -> None:
    """Test basic cursor-based pagination."""
    # Create test data with explicit dates
    rows = [
        {"rel": "a.jpg", "id": "1", "dt": "2023-01-01T10:00:00Z"},
        {"rel": "b.jpg", "id": "2", "dt": "2023-01-02T10:00:00Z"},
        {"rel": "c.jpg", "id": "3", "dt": "2023-01-03T10:00:00Z"},
        {"rel": "d.jpg", "id": "4", "dt": "2023-01-04T10:00:00Z"},
    ]
    store.write_rows(rows)

    # First page (limit 2)
    page1 = store.get_assets_page(limit=2)
    assert len(page1) == 2
    assert page1[0]["rel"] == "d.jpg"  # Newest first
    assert page1[1]["rel"] == "c.jpg"

    # Second page using cursor from first page
    cursor_dt = page1[-1]["dt"]
    cursor_id = page1[-1]["id"]
    page2 = store.get_assets_page(cursor_dt=cursor_dt, cursor_id=cursor_id, limit=2)
    assert len(page2) == 2
    assert page2[0]["rel"] == "b.jpg"
    assert page2[1]["rel"] == "a.jpg"


def test_get_assets_page_with_album_filter(store: IndexStore) -> None:
    """Test pagination with album path filtering."""
    rows = [
        {"rel": "2023/Trip/a.jpg", "id": "1", "dt": "2023-01-01T10:00:00Z"},
        {"rel": "2023/Trip/b.jpg", "id": "2", "dt": "2023-01-02T10:00:00Z"},
        {"rel": "2024/Summer/c.jpg", "id": "3", "dt": "2023-01-03T10:00:00Z"},
    ]
    store.write_rows(rows)

    # Get only 2023/Trip photos
    results = store.get_assets_page(album_path="2023/Trip", limit=10)
    assert len(results) == 2
    rels = {r["rel"] for r in results}
    assert "2023/Trip/a.jpg" in rels
    assert "2023/Trip/b.jpg" in rels


def test_get_assets_page_with_subalbums(store: IndexStore) -> None:
    """Test pagination including sub-albums."""
    rows = [
        {"rel": "2023/Trip/a.jpg", "id": "1", "dt": "2023-01-01T10:00:00Z"},
        {"rel": "2023/Trip/Day1/b.jpg", "id": "2", "dt": "2023-01-02T10:00:00Z"},
        {"rel": "2023/Trip/Day2/c.jpg", "id": "3", "dt": "2023-01-03T10:00:00Z"},
        {"rel": "2024/Other/d.jpg", "id": "4", "dt": "2023-01-04T10:00:00Z"},
    ]
    store.write_rows(rows)

    # Get 2023/Trip and all sub-albums
    results = store.get_assets_page(album_path="2023/Trip", include_subalbums=True, limit=10)
    assert len(results) == 3
    rels = {r["rel"] for r in results}
    assert "2023/Trip/a.jpg" in rels
    assert "2023/Trip/Day1/b.jpg" in rels
    assert "2023/Trip/Day2/c.jpg" in rels
    assert "2024/Other/d.jpg" not in rels


def test_read_album_assets(store: IndexStore) -> None:
    """Test album-scoped asset retrieval."""
    rows = [
        {"rel": "Albums/Vacation/img1.jpg", "id": "1", "dt": "2023-06-01T10:00:00Z"},
        {"rel": "Albums/Vacation/img2.jpg", "id": "2", "dt": "2023-06-02T10:00:00Z"},
        {"rel": "Albums/Work/doc.jpg", "id": "3", "dt": "2023-07-01T10:00:00Z"},
    ]
    store.write_rows(rows)

    # Read only Vacation album
    vacation_assets = list(store.read_album_assets("Albums/Vacation"))
    assert len(vacation_assets) == 2
    rels = {r["rel"] for r in vacation_assets}
    assert "Albums/Vacation/img1.jpg" in rels
    assert "Albums/Vacation/img2.jpg" in rels


def test_list_albums(store: IndexStore) -> None:
    """Test listing distinct album paths."""
    rows = [
        {"rel": "2023/Trip/a.jpg", "id": "1"},
        {"rel": "2023/Trip/b.jpg", "id": "2"},
        {"rel": "2024/Summer/c.jpg", "id": "3"},
        {"rel": "root.jpg", "id": "4"},  # Root level
    ]
    store.write_rows(rows)

    albums = store.list_albums()
    assert "2023/Trip" in albums
    assert "2024/Summer" in albums
    # Empty string (root) should not be in list since we filter it
    # (files at root have parent_album_path = "")


def test_count_album_assets(store: IndexStore) -> None:
    """Test counting assets in a specific album."""
    rows = [
        {"rel": "Album1/a.jpg", "id": "1"},
        {"rel": "Album1/b.jpg", "id": "2"},
        {"rel": "Album1/Sub/c.jpg", "id": "3"},
        {"rel": "Album2/d.jpg", "id": "4"},
    ]
    store.write_rows(rows)

    # Count without sub-albums
    assert store.count_album_assets("Album1", include_subalbums=False) == 2

    # Count with sub-albums
    assert store.count_album_assets("Album1", include_subalbums=True) == 3


def test_album_path_with_special_chars(store: IndexStore) -> None:
    """Test that album paths containing SQL LIKE wildcards are handled correctly."""
    rows = [
        # Album with % in name
        {"rel": "100%_complete/photo.jpg", "id": "1"},
        {"rel": "100%_complete/sub/photo2.jpg", "id": "2"},
        # Album with _ in name
        {"rel": "my_album/photo.jpg", "id": "3"},
        {"rel": "my_album/sub_dir/photo2.jpg", "id": "4"},
        # Decoy album that would match if escaping is broken
        {"rel": "100X_complete/photo.jpg", "id": "5"},  # X instead of %
        {"rel": "myXalbum/photo.jpg", "id": "6"},  # X instead of _
    ]
    store.write_rows(rows)

    # Test exact match with % in path
    results = store.get_assets_page(album_path="100%_complete", include_subalbums=False, limit=10)
    assert len(results) == 1
    assert results[0]["rel"] == "100%_complete/photo.jpg"

    # Test subalbums with % in path
    results = store.get_assets_page(album_path="100%_complete", include_subalbums=True, limit=10)
    assert len(results) == 2
    rels = {r["rel"] for r in results}
    assert "100%_complete/photo.jpg" in rels
    assert "100%_complete/sub/photo2.jpg" in rels
    # Decoy should NOT be included
    assert "100X_complete/photo.jpg" not in rels

    # Test with _ in path
    results = store.get_assets_page(album_path="my_album", include_subalbums=True, limit=10)
    assert len(results) == 2
    # Decoy should NOT be included
    rels = {r["rel"] for r in results}
    assert "myXalbum/photo.jpg" not in rels

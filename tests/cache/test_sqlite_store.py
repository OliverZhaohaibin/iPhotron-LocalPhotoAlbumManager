from __future__ import annotations

import sqlite3
from pathlib import Path
import pytest
from src.iPhoto.cache.index_store import IndexStore
from src.iPhoto.config import WORK_DIR_NAME

@pytest.fixture
def store(tmp_path: Path) -> IndexStore:
    return IndexStore(tmp_path)

def test_init_creates_db(store: IndexStore, tmp_path: Path) -> None:
    db_path = tmp_path / WORK_DIR_NAME / "index.db"
    assert db_path.exists()

    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='assets'")
        assert cursor.fetchone() is not None

def test_wal_mode_enabled(store: IndexStore, tmp_path: Path) -> None:
    # Check if journal_mode is WAL
    # Using a new connection because IndexStore manages its own connections transiently
    db_path = tmp_path / WORK_DIR_NAME / "index.db"

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
    # NULL should be last due to "ORDER BY dt IS NULL, dt DESC"
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

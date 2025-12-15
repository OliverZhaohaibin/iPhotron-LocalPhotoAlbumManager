from __future__ import annotations

from pathlib import Path

from src.iPhoto.cache.index_store import IndexStore


def test_recover_reindexes_preserves_data(tmp_path: Path) -> None:
    store = IndexStore(tmp_path)
    rows = [
        {"rel": "a.jpg", "is_favorite": 1, "dt": "2023-01-01"},
        {"rel": "b.jpg", "is_favorite": 0, "dt": "2023-01-02"},
    ]
    store.write_rows(rows)

    # Trigger recovery workflow (should finish at Level 1 without data loss)
    store._recover_database()

    data = {row["rel"]: row["is_favorite"] for row in store.read_all()}
    assert data == {"a.jpg": 1, "b.jpg": 0}


def test_corrupted_file_rebuilt(tmp_path: Path) -> None:
    # Create a valid database first
    store = IndexStore(tmp_path)
    store.write_rows([{"rel": "a.jpg", "is_favorite": 1}])

    # Overwrite with corrupted bytes to simulate a malformed database file
    store._force_reset_db()
    store.path.write_bytes(b"\x00\x01corrupted")

    recovered = IndexStore(tmp_path)
    # Corrupted database should be rebuilt and readable instead of crashing
    assert recovered.count() == 0

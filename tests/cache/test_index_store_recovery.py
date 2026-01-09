from __future__ import annotations

from pathlib import Path

from src.iPhoto.cache.index_store import IndexStore


def test_corrupted_file_rebuilt(tmp_path: Path) -> None:
    # Create a valid database first
    store = IndexStore(tmp_path)
    store.write_rows([{"rel": "a.jpg", "is_favorite": 1}])

    # Overwrite with corrupted bytes to simulate a malformed database file
    for suffix in ["", "-wal", "-shm"]:
        p = Path(str(store.path) + suffix)
        if p.exists():
            p.unlink()
    store.path.write_bytes(b"\x00\x01corrupted")

    recovered = IndexStore(tmp_path)
    # Corrupted database should be rebuilt and readable instead of crashing
    assert recovered.count() == 0

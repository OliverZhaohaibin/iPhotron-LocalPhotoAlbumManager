import sqlite3
import pytest
from pathlib import Path
from src.iPhoto.cache.index_store import IndexStore, WORK_DIR_NAME

@pytest.fixture
def index_store(tmp_path):
    root = tmp_path / "lib"
    root.mkdir()
    # Force use of global index
    (root / WORK_DIR_NAME).mkdir()
    store = IndexStore(root, use_global_index=True)
    yield store
    store._force_reset_db()

def test_pagination_with_null_dates(index_store):
    # Insert items with and without dates
    rows = [
        {"rel": "a.jpg", "dt": "2023-01-01", "id": "1", "parent_album_path": ""},
        {"rel": "b.jpg", "dt": None, "id": "2", "parent_album_path": ""}, # NULL date
        {"rel": "c.jpg", "dt": None, "id": "1", "parent_album_path": ""}, # NULL date, smaller ID
    ]
    index_store.write_rows(rows)

    # 1. Fetch first page (limit 1). Should get 'a.jpg' (2023)
    page1 = index_store.get_assets_page(limit=1)
    assert len(page1) == 1
    assert page1[0]["rel"] == "a.jpg"

    last = page1[-1]

    # 2. Fetch second page. Cursor: 2023-01-01, 1. Should get 'b.jpg' (NULL, 2)
    # Because NULLs are LAST.
    page2 = index_store.get_assets_page(cursor_dt=last["dt"], cursor_id=last["id"], limit=1)
    assert len(page2) == 1
    assert page2[0]["rel"] == "b.jpg"
    assert page2[0]["dt"] is None

    last = page2[-1]

    # 3. Fetch third page. Cursor: None, 2. Should get 'c.jpg' (NULL, 1)
    # This is the critical case where cursor_dt is None
    page3 = index_store.get_assets_page(cursor_dt=last["dt"], cursor_id=last["id"], limit=1)
    assert len(page3) == 1
    assert page3[0]["rel"] == "c.jpg"

    last = page3[-1]

    # 4. Fetch fourth page. Cursor: None, 1. Should be empty
    page4 = index_store.get_assets_page(cursor_dt=last["dt"], cursor_id=last["id"], limit=1)
    assert len(page4) == 0

def test_pagination_transition_to_null(index_store):
    # Ensure transition from value to NULL works
    rows = [
        {"rel": "a.jpg", "dt": "2023-01-01", "id": "10", "parent_album_path": ""},
        {"rel": "b.jpg", "dt": None, "id": "20", "parent_album_path": ""},
    ]
    index_store.write_rows(rows)

    page1 = index_store.get_assets_page(limit=1)
    assert page1[0]["rel"] == "a.jpg"

    last = page1[-1]
    # Cursor: 2023, 10
    # Next should be b.jpg (NULL).

    page2 = index_store.get_assets_page(cursor_dt=last["dt"], cursor_id=last["id"], limit=1)
    assert len(page2) == 1
    assert page2[0]["rel"] == "b.jpg"

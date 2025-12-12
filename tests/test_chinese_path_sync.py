
import os
import sqlite3
import unicodedata
from pathlib import Path
import pytest
from iPhoto.cache.index_store import IndexStore

@pytest.fixture
def chinese_album_root(tmp_path):
    # Use a Chinese album name
    album_name = "测试相册"
    album_path = tmp_path / album_name
    album_path.mkdir()
    return album_path

def test_index_store_chinese_path(chinese_album_root):
    """Test IndexStore initialization and basic operations with Chinese path."""

    # Initialize IndexStore
    store = IndexStore(chinese_album_root)
    assert store.path.exists()
    assert str(store.path).endswith("index.db")

    # Check if we can connect manually using the path string (verifying the fix logic)
    conn = sqlite3.connect(str(store.path))
    conn.close()

    # Create a dummy asset
    rel_path = "子文件夹/图片.jpg"
    nfc_rel_path = unicodedata.normalize("NFC", rel_path)

    row = {
        "rel": nfc_rel_path,
        "id": "test_id_1",
        "dt": "2023-01-01T12:00:00Z",
        "ts": 1672574400000000,
        "media_type": 0,
        "live_role": 0,
        "is_favorite": 0
    }

    store.write_rows([row])

    # Verify insertion
    rows = list(store.read_all())
    assert len(rows) == 1
    assert rows[0]["rel"] == nfc_rel_path

    # Test sync_favorites
    store.sync_favorites([nfc_rel_path])
    rows = list(store.read_all())
    assert rows[0]["is_favorite"] == 1

    # Test sync_favorites removal
    store.sync_favorites([])
    rows = list(store.read_all())
    assert rows[0]["is_favorite"] == 0

def test_index_store_normalization(chinese_album_root):
    """Test that sync_favorites handles normalization differences."""
    store = IndexStore(chinese_album_root)

    rel_path = "e\u0301.jpg" # NFD 'é'
    nfc_path = "\u00e9.jpg" # NFC 'é'

    row = {
        "rel": rel_path, # Store NFD in DB
        "id": "test_id_2",
        "dt": "2023-01-01T12:00:00Z",
        "ts": 1672574400000000,
        "media_type": 0,
        "live_role": 0,
        "is_favorite": 0
    }
    store.write_rows([row])

    # Sync using NFC path (simulating manifest having NFC)
    store.sync_favorites([nfc_path])

    rows = list(store.read_all())
    assert rows[0]["is_favorite"] == 1

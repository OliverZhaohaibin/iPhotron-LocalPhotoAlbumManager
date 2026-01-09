
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from src.iPhoto.app import _sync_live_roles_to_db
from src.iPhoto.models.types import LiveGroup
from src.iPhoto.cache.index_store import IndexStore
from src.iPhoto.config import WORK_DIR_NAME

@pytest.fixture
def temp_album(tmp_path):
    album_root = tmp_path / "test_album"
    album_root.mkdir()
    (album_root / WORK_DIR_NAME).mkdir()
    return album_root

def test_sync_live_roles_to_db(temp_album):
    """Verify _sync_live_roles_to_db updates IndexStore correctly."""
    store = IndexStore(temp_album)

    # Initial state: 3 items, no roles
    rows = [
        {"rel": "photo.jpg", "id": "1"},
        {"rel": "video.mov", "id": "2"},
        {"rel": "other.png", "id": "3"},
    ]
    store.write_rows(rows)

    # Create LiveGroup
    group = LiveGroup(
        id="group1",
        still="photo.jpg",
        motion="video.mov",
        confidence=1.0,
        content_id="cid",
        still_image_time=0.0
    )

    # Sync
    _sync_live_roles_to_db(temp_album, [group])

    # Verify DB state
    data = {r["rel"]: r for r in store.read_all()}

    # Photo -> Role 0, Partner Video
    assert data["photo.jpg"]["live_role"] == 0
    assert data["photo.jpg"]["live_partner_rel"] == "video.mov"

    # Video -> Role 1, Partner Photo
    assert data["video.mov"]["live_role"] == 1
    assert data["video.mov"]["live_partner_rel"] == "photo.jpg"

    # Other -> Unchanged
    assert data["other.png"]["live_role"] == 0
    assert data["other.png"]["live_partner_rel"] is None

def test_sync_live_roles_empty(temp_album):
    """Verify syncing empty groups clears existing roles."""
    store = IndexStore(temp_album)
    rows = [{"rel": "a.jpg"}, {"rel": "b.mov"}]
    store.write_rows(rows)

    # Manually set roles via update to simulate existing state
    store.apply_live_role_updates([("b.mov", 1, "a.jpg")])

    # Sync empty
    _sync_live_roles_to_db(temp_album, [])

    data = {r["rel"]: r for r in store.read_all()}
    assert data["b.mov"]["live_role"] == 0
    assert data["b.mov"]["live_partner_rel"] is None

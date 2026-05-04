from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from iPhoto.cache.index_store import get_global_repository, reset_global_repository
from iPhoto.infrastructure.repositories.library_state_repository import (
    IndexStoreLibraryStateRepository,
)


@pytest.fixture(autouse=True)
def clean_global_repository():
    reset_global_repository()
    yield
    reset_global_repository()


def test_index_store_library_state_repository_updates_geodata(tmp_path: Path) -> None:
    repo = get_global_repository(tmp_path)
    repo.write_rows(
        [
            {
                "rel": "image.jpg",
                "id": "asset-image",
                "bytes": 10,
            }
        ]
    )
    with sqlite3.connect(repo.path) as conn:
        conn.execute("ALTER TABLE assets ADD COLUMN metadata TEXT")
    state = IndexStoreLibraryStateRepository(tmp_path)

    state.update_asset_geodata(
        "image.jpg",
        gps={"lat": 48.137154, "lon": 11.576124},
        location="Munich",
        metadata_updates={"iso": 640},
    )

    row = next(repo.read_all())
    metadata = json.loads(row["metadata"])
    assert row["gps"] == {"lat": 48.137154, "lon": 11.576124}
    assert row["location"] == "Munich"
    assert metadata["iso"] == 640
    assert metadata["gps"] == {"lat": 48.137154, "lon": 11.576124}

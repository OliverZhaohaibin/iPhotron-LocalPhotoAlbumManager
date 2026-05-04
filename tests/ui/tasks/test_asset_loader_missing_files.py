from pathlib import Path

import pytest

from iPhoto.gui.ui.tasks.asset_loader_worker import compute_asset_rows


def test_compute_asset_rows_skips_missing_files(tmp_path: Path) -> None:
    existing = tmp_path / "keep.jpg"
    existing.write_bytes(b"data")

    class FakeQueryService:
        def __init__(self) -> None:
            self.library_root = tmp_path

        def read_geometry_rows(self, *args, **kwargs):
            return [
                {
                    "rel": "keep.jpg",
                    "w": 1,
                    "h": 1,
                    "bytes": 4,
                    "ts": 1,
                    "is_favorite": False,
                },
                {
                    "rel": "missing.jpg",
                    "w": 1,
                    "h": 1,
                    "bytes": 4,
                    "ts": 1,
                    "is_favorite": False,
                },
            ]

        def location_cache_writer(self, root: Path):
            return None

    entries, total = compute_asset_rows(
        tmp_path,
        [],
        asset_query_service=FakeQueryService(),
    )

    assert total == 1
    assert [entry["rel"] for entry in entries] == ["keep.jpg"]


def test_compute_asset_rows_requires_session_query_service(
    tmp_path: Path,
) -> None:
    existing = tmp_path / "keep.jpg"
    existing.write_bytes(b"data")

    with pytest.raises(RuntimeError, match="Active library session is unavailable"):
        compute_asset_rows(
            tmp_path,
            [],
            asset_query_service=None,
        )

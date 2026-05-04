from __future__ import annotations

from pathlib import Path

from iPhoto.bootstrap.library_location_service import LibraryLocationService


class _QueryService:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.calls = 0

    def read_geotagged_rows(self):
        self.calls += 1
        return list(self.rows)


def test_location_service_lists_deduped_sorted_assets(tmp_path: Path) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    rows = [
        {
            "rel": "B/photo.jpg",
            "id": "asset-b",
            "gps": {"lat": 2.0, "lon": 3.0},
            "mime": "image/jpeg",
            "parent_album_path": "B",
        },
        {
            "rel": "A/photo.jpg",
            "id": "asset-a",
            "gps": {"lat": 1.0, "lon": 2.0},
            "mime": "image/jpeg",
            "parent_album_path": "A",
        },
        {
            "rel": "A/photo.jpg",
            "id": "duplicate",
            "gps": {"lat": 1.0, "lon": 2.0},
            "mime": "image/jpeg",
            "parent_album_path": "A",
        },
        {
            "rel": "hidden.mov",
            "id": "hidden",
            "gps": {"lat": 1.0, "lon": 2.0},
            "mime": "video/quicktime",
            "live_role": 1,
        },
    ]
    query_service = _QueryService(rows)
    service = LibraryLocationService(root, query_service=query_service)  # type: ignore[arg-type]

    first = service.list_geotagged_assets()
    second = service.list_geotagged_assets()

    assert [asset.library_relative for asset in first] == ["A/photo.jpg", "B/photo.jpg"]
    assert first == second
    assert query_service.calls == 1


def test_location_service_invalidates_cached_assets(tmp_path: Path) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    query_service = _QueryService(
        [
            {
                "rel": "old.jpg",
                "id": "old",
                "gps": {"lat": 1.0, "lon": 2.0},
                "mime": "image/jpeg",
            }
        ]
    )
    service = LibraryLocationService(root, query_service=query_service)  # type: ignore[arg-type]

    assert [asset.library_relative for asset in service.list_geotagged_assets()] == [
        "old.jpg"
    ]
    query_service.rows = [
        {
            "rel": "new.jpg",
            "id": "new",
            "gps": {"lat": 3.0, "lon": 4.0},
            "mime": "image/jpeg",
        }
    ]
    assert [asset.library_relative for asset in service.list_geotagged_assets()] == [
        "old.jpg"
    ]

    service.invalidate_cache()

    assert [asset.library_relative for asset in service.list_geotagged_assets()] == [
        "new.jpg"
    ]
    assert query_service.calls == 2


def test_location_service_converts_incremental_scan_row(tmp_path: Path) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    service = LibraryLocationService(root, query_service=_QueryService([]))  # type: ignore[arg-type]

    asset = service.asset_from_row(
        {
            "rel": "Album/live.heic",
            "id": "still",
            "gps": {"lat": 39.9, "lon": 116.3},
            "mime": "image/heic",
            "parent_album_path": "Album",
            "live_photo_group_id": "group-1",
            "live_partner_rel": "Album/live.mov",
        }
    )

    assert asset is not None
    assert asset.library_relative == "Album/live.heic"
    assert asset.album_relative == "live.heic"
    assert asset.album_path == root / "Album"
    assert asset.live_photo_group_id == "group-1"
    assert asset.live_partner_rel == "Album/live.mov"

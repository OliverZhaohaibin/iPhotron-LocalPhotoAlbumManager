from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from iPhoto.application.gallery_query_window import GalleryPageAnchor
from iPhoto.bootstrap.library_asset_query_service import LibraryAssetQueryService
from iPhoto.config import RECENTLY_DELETED_DIR_NAME
from iPhoto.domain.models.core import MediaType
from iPhoto.domain.models.query import AssetQuery


class _Repository:
    def __init__(self) -> None:
        self.count_calls: list[dict[str, Any]] = []
        self.geometry_calls: list[dict[str, Any]] = []
        self.album_read_calls: list[dict[str, Any]] = []
        self.get_assets_page_calls: list[dict[str, Any]] = []
        self.location_updates: list[tuple[str, str]] = []
        self.geometry_rows = [{"rel": "Trip/a.jpg", "id": "a"}]
        self.album_rows = [{"rel": "Trip/a.jpg", "id": "a"}]
        self.all_rows = [{"rel": "root.jpg", "id": "root"}]
        self.geotagged_rows = [{"rel": "Trip/a.jpg", "gps": {"lat": 1, "lon": 2}}]
        self.rows_by_rel = {"Trip/a.jpg": {"rel": "Trip/a.jpg", "is_favorite": 1}}
        self.rows_by_id = {
            "a": {"rel": "Trip/a.jpg", "id": "a", "dt": "2024-02-02T00:00:00", "live_role": 0},
            "b": {"rel": "Trip/b.jpg", "id": "b", "dt": "2024-02-03T00:00:00", "live_role": 0},
        }

    def count(self, **kwargs):
        self.count_calls.append(dict(kwargs))
        return 7

    def read_geometry_only(self, **kwargs):
        self.geometry_calls.append(dict(kwargs))
        return list(self.geometry_rows)

    def read_album_assets(self, album_path: str, **kwargs):
        call = dict(kwargs)
        call["album_path"] = album_path
        self.album_read_calls.append(call)
        return list(self.album_rows)

    def read_all(self, **_kwargs):
        return list(self.all_rows)

    def get_assets_page(self, **kwargs):
        self.get_assets_page_calls.append(dict(kwargs))
        rows = list(self.album_rows if kwargs.get("album_path") else self.all_rows)
        cursor_dt = kwargs.get("cursor_dt")
        cursor_id = kwargs.get("cursor_id")
        if cursor_dt is not None and cursor_id is not None:
            rows = [
                row
                for row in rows
                if (str(row.get("dt") or ""), str(row.get("id") or ""))
                < (str(cursor_dt), str(cursor_id))
            ]
        offset = int(kwargs.get("offset") or 0)
        limit = int(kwargs.get("limit") or len(rows))
        return rows[offset : offset + limit]

    def read_geotagged(self):
        return list(self.geotagged_rows)

    def update_location(self, rel: str, location: str) -> None:
        self.location_updates.append((rel, location))

    def get_rows_by_rels(self, rels):
        return {rel: self.rows_by_rel[rel] for rel in rels if rel in self.rows_by_rel}

    def get_rows_by_ids(self, asset_ids):
        return {
            asset_id: self.rows_by_id[asset_id]
            for asset_id in asset_ids
            if asset_id in self.rows_by_id
        }


def test_count_and_geometry_rows_are_scoped_to_album_path(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    album_root = library_root / "Trip"
    album_root.mkdir(parents=True)
    repo = _Repository()
    service = LibraryAssetQueryService(library_root, repository_factory=lambda _root: repo)

    assert service.count_assets(album_root, filter_params={"filter_mode": "images"}) == 7
    rows = list(service.read_geometry_rows(album_root, filter_params={"filter_mode": "images"}))

    assert repo.count_calls == [
        {
            "filter_hidden": True,
            "filter_params": {"filter_mode": "images"},
            "album_path": "Trip",
            "include_subalbums": True,
        }
    ]
    assert repo.geometry_calls == [
        {
            "filter_params": {"filter_mode": "images"},
            "sort_by_date": True,
            "album_path": "Trip",
            "include_subalbums": True,
        }
    ]
    assert rows == [{"rel": "a.jpg", "id": "a"}]


def test_scoped_location_writer_maps_to_library_relative_path(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    album_root = library_root / "Trip"
    album_root.mkdir(parents=True)
    repo = _Repository()
    service = LibraryAssetQueryService(library_root, repository_factory=lambda _root: repo)

    service.location_cache_writer(album_root).update_location("a.jpg", "Paris")
    service.location_cache_writer(library_root).update_location("root.jpg", "Berlin")

    assert repo.location_updates == [
        ("Trip/a.jpg", "Paris"),
        ("root.jpg", "Berlin"),
    ]


def test_read_asset_and_geotagged_rows(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    album_root = library_root / "Trip"
    album_root.mkdir(parents=True)
    repo = _Repository()
    service = LibraryAssetQueryService(library_root, repository_factory=lambda _root: repo)

    assert list(service.read_asset_rows(album_root)) == [{"rel": "a.jpg", "id": "a"}]
    assert list(service.read_library_relative_asset_rows(album_root)) == [
        {"rel": "Trip/a.jpg", "id": "a"}
    ]
    assert list(service.read_asset_rows(library_root)) == [
        {"rel": "root.jpg", "id": "root"}
    ]
    assert list(service.read_geotagged_rows()) == [
        {"rel": "Trip/a.jpg", "gps": {"lat": 1, "lon": 2}}
    ]


def test_favorite_status_for_path_uses_library_relative_rel(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    album_root = library_root / "Trip"
    album_root.mkdir(parents=True)
    repo = _Repository()
    service = LibraryAssetQueryService(library_root, repository_factory=lambda _root: repo)

    assert service.favorite_status_for_path(album_root / "a.jpg") is True
    assert service.favorite_status_for_path(album_root / "missing.jpg") is None


def test_count_query_assets_maps_simple_query_to_repository_filters(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    repo = _Repository()
    service = LibraryAssetQueryService(library_root, repository_factory=lambda _root: repo)

    query = AssetQuery(is_favorite=True, media_types=[MediaType.VIDEO])

    assert service.count_query_assets(query) == 7
    assert repo.count_calls == [
        {
            "filter_hidden": True,
            "filter_params": {
                "media_type": 1,
                "filter_mode": "favorites",
                "exclude_path_prefix": RECENTLY_DELETED_DIR_NAME,
            },
            "album_path": None,
            "include_subalbums": False,
        }
    ]


def test_read_query_asset_rows_scopes_album_rows_and_applies_paging(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    album_root = library_root / "Trip"
    album_root.mkdir(parents=True)
    repo = _Repository()
    repo.album_rows = [
        {"rel": "Trip/a.jpg", "id": "a", "live_role": 0},
        {"rel": "Trip/b.jpg", "id": "b", "live_role": 0},
    ]
    service = LibraryAssetQueryService(library_root, repository_factory=lambda _root: repo)
    query = AssetQuery(album_path="Trip", include_subalbums=True, offset=1, limit=1)

    rows = list(service.read_query_asset_rows(album_root, query))

    assert rows == [{"rel": "b.jpg", "id": "b", "live_role": 0}]
    assert repo.album_read_calls == [
        {
            "include_subalbums": True,
            "sort_by_date": True,
            "filter_hidden": True,
            "filter_params": {
                "exclude_path_prefix": RECENTLY_DELETED_DIR_NAME,
            },
            "album_path": "Trip",
        }
    ]


def test_read_query_window_uses_repository_pagination_and_anchor(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    repo = _Repository()
    repo.all_rows = [
        {"rel": f"asset-{index}.jpg", "id": f"id-{index}", "dt": f"2024-01-0{5-index}T00:00:00", "live_role": 0}
        for index in range(5)
    ]
    service = LibraryAssetQueryService(library_root, repository_factory=lambda _root: repo)
    anchor = GalleryPageAnchor(row=1, dt="2024-01-04T00:00:00", asset_id="id-1")

    rows = list(service.read_query_window(library_root, AssetQuery(), first=3, last=4, anchor=anchor))

    assert [row["id"] for row in rows] == ["id-3", "id-4"]
    assert repo.get_assets_page_calls == [
        {
            "cursor_dt": "2024-01-04T00:00:00",
            "cursor_id": "id-1",
            "limit": 2,
            "album_path": None,
            "include_subalbums": False,
            "filter_hidden": True,
            "filter_params": {"exclude_path_prefix": RECENTLY_DELETED_DIR_NAME},
            "offset": 1,
        }
    ]


def test_read_query_window_falls_back_to_in_memory_slice_for_special_query(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    repo = _Repository()
    service = LibraryAssetQueryService(library_root, repository_factory=lambda _root: repo)

    rows = list(
        service.read_query_window(
            library_root,
            AssetQuery(asset_ids=["a", "b"]),
            first=1,
            last=1,
        )
    )

    assert [row["id"] for row in rows] == ["a"]
    assert repo.get_assets_page_calls == []


def test_asset_id_query_uses_rows_by_id_and_keeps_library_relative_rows(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    repo = _Repository()
    service = LibraryAssetQueryService(library_root, repository_factory=lambda _root: repo)

    rows = list(
        service.read_query_asset_rows(
            library_root,
            AssetQuery(asset_ids=["a", "b"]),
        )
    )

    assert [row["id"] for row in rows] == ["b", "a"]
    assert [row["rel"] for row in rows] == ["Trip/b.jpg", "Trip/a.jpg"]


def test_album_id_query_filters_in_memory_rows(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    repo = _Repository()
    repo.all_rows = [
        {"rel": "a.jpg", "id": "a", "album_id": "album-a", "live_role": 0},
        {"rel": "b.jpg", "id": "b", "album_id": "album-b", "live_role": 0},
        {"rel": "missing.jpg", "id": "missing", "live_role": 0},
    ]
    service = LibraryAssetQueryService(library_root, repository_factory=lambda _root: repo)
    query = AssetQuery(album_id="album-a")

    rows = list(service.read_query_asset_rows(library_root, query))

    assert rows == [{"rel": "a.jpg", "id": "a", "album_id": "album-a", "live_role": 0}]
    assert service.count_query_assets(query) == 1


def test_date_query_compares_scanned_utc_rows_with_naive_bounds(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    repo = _Repository()
    repo.all_rows = [
        {"rel": "before.jpg", "id": "before", "dt": "2024-01-01T09:59:59Z", "live_role": 0},
        {"rel": "inside.jpg", "id": "inside", "dt": "2024-01-01T10:30:00Z", "live_role": 0},
        {"rel": "after.jpg", "id": "after", "dt": "2024-01-01T11:00:01Z", "live_role": 0},
    ]
    service = LibraryAssetQueryService(library_root, repository_factory=lambda _root: repo)

    rows = list(
        service.read_query_asset_rows(
            library_root,
            AssetQuery(
                date_from=datetime(2024, 1, 1, 10, 0, 0),
                date_to=datetime(2024, 1, 1, 11, 0, 0),
            ),
        )
    )

    assert [row["id"] for row in rows] == ["inside"]

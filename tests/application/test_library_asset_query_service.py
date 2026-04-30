from __future__ import annotations

from pathlib import Path
from typing import Any

from iPhoto.bootstrap.library_asset_query_service import LibraryAssetQueryService


class _Repository:
    def __init__(self) -> None:
        self.count_calls: list[dict[str, Any]] = []
        self.geometry_calls: list[dict[str, Any]] = []
        self.album_read_calls: list[dict[str, Any]] = []
        self.location_updates: list[tuple[str, str]] = []
        self.geometry_rows = [{"rel": "Trip/a.jpg", "id": "a"}]
        self.album_rows = [{"rel": "Trip/a.jpg", "id": "a"}]
        self.all_rows = [{"rel": "root.jpg", "id": "root"}]
        self.geotagged_rows = [{"rel": "Trip/a.jpg", "gps": {"lat": 1, "lon": 2}}]

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

    def read_geotagged(self):
        return list(self.geotagged_rows)

    def update_location(self, rel: str, location: str) -> None:
        self.location_updates.append((rel, location))


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
    assert list(service.read_asset_rows(library_root)) == [
        {"rel": "root.jpg", "id": "root"}
    ]
    assert list(service.read_geotagged_rows()) == [
        {"rel": "Trip/a.jpg", "gps": {"lat": 1, "lon": 2}}
    ]

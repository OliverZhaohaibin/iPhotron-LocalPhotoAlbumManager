from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for location gallery viewmodel tests")

from iPhoto.gui.viewmodels.asset_dto_converter import geotagged_asset_to_dto
from iPhoto.library.geo_aggregator import GeoAggregatorMixin


class _DummyLibrary(GeoAggregatorMixin):
    def __init__(self, root: Path) -> None:
        self._root = root

    def _require_root(self) -> Path:
        return self._root


def test_geo_aggregator_skips_hidden_live_motion_rows(monkeypatch, tmp_path: Path) -> None:
    library_root = tmp_path / "library"
    library_root.mkdir()
    (library_root / "a.heic").touch()
    (library_root / "a.mov").touch()

    rows = [
        {
            "id": "still-1",
            "rel": "a.heic",
            "gps": {"lat": 39.9, "lon": 116.3},
            "media_type": "photo",
            "live_role": 0,
            "live_photo_group_id": "group-1",
            "live_partner_rel": "a.mov",
        },
        {
            "id": "motion-1",
            "rel": "a.mov",
            "gps": {"lat": 39.9, "lon": 116.3},
            "media_type": "video",
            "live_role": 1,
            "live_photo_group_id": "group-1",
            "live_partner_rel": "a.heic",
        },
    ]

    class _Repo:
        def read_geotagged(self):
            return iter(rows)

    monkeypatch.setattr(
        "iPhoto.library.geo_aggregator.get_global_repository",
        lambda root: _Repo(),
    )
    monkeypatch.setattr(
        "iPhoto.library.geo_aggregator.resolve_location_name",
        lambda gps: "Beijing",
    )

    assets = _DummyLibrary(library_root).get_geotagged_assets()
    assert len(assets) == 1
    assert assets[0].library_relative == "a.heic"
    assert assets[0].live_photo_group_id == "group-1"
    assert assets[0].live_partner_rel == "a.mov"


def test_geotagged_asset_dto_marks_live_photo_and_partner(tmp_path: Path) -> None:
    library_root = tmp_path / "library"
    library_root.mkdir()
    still_path = library_root / "photo.heic"
    still_path.touch()

    from iPhoto.library.geo_aggregator import GeotaggedAsset

    asset = GeotaggedAsset(
        library_relative="photo.heic",
        album_relative="photo.heic",
        absolute_path=still_path,
        album_path=library_root,
        asset_id="asset-1",
        latitude=37.7749,
        longitude=-122.4194,
        is_image=True,
        is_video=False,
        still_image_time=None,
        duration=1.2,
        location_name="San Francisco",
        live_photo_group_id="live-1",
        live_partner_rel="photo.mov",
    )

    dto = geotagged_asset_to_dto(asset, library_root)
    assert dto is not None
    assert dto.is_live is True
    assert dto.media_type == "live"
    assert dto.metadata["live_photo_group_id"] == "live-1"
    assert dto.metadata["live_partner_rel"] == "photo.mov"

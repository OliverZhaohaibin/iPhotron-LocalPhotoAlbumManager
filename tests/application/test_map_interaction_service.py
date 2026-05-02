from __future__ import annotations

from pathlib import Path

from iPhoto.application.dtos import GeotaggedAsset
from iPhoto.application.services.map_interaction_service import LibraryMapInteractionService


def _asset(tmp_path: Path, rel: str) -> GeotaggedAsset:
    return GeotaggedAsset(
        library_relative=rel,
        album_relative=rel,
        absolute_path=tmp_path / rel,
        album_path=tmp_path,
        asset_id=rel,
        latitude=20.0,
        longitude=10.0,
        is_image=True,
        is_video=False,
        still_image_time=None,
        duration=None,
        location_name=None,
        live_photo_group_id=None,
        live_partner_rel=None,
    )


def test_map_interaction_ignores_empty_marker_payload() -> None:
    service = LibraryMapInteractionService()

    activation = service.activate_marker_assets([])

    assert activation.kind == "none"
    assert activation.asset_relative is None
    assert activation.assets == ()


def test_map_interaction_routes_single_asset(tmp_path: Path) -> None:
    service = LibraryMapInteractionService()
    asset = _asset(tmp_path, "a.jpg")

    activation = service.activate_marker_assets([asset])

    assert activation.kind == "asset"
    assert activation.asset_relative == "a.jpg"
    assert activation.assets == (asset,)


def test_map_interaction_routes_cluster_assets(tmp_path: Path) -> None:
    service = LibraryMapInteractionService()
    first = _asset(tmp_path, "a.jpg")
    second = _asset(tmp_path, "b.jpg")

    activation = service.activate_marker_assets([first, object(), second])

    assert activation.kind == "cluster"
    assert activation.asset_relative is None
    assert activation.assets == (first, second)

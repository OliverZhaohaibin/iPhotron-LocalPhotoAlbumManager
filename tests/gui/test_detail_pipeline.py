from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from iPhoto.gui.detail_pipeline import (
    AssetSourceIdentity,
    DetailDecodeKey,
    DetailGeometryState,
    DetailRenderRequest,
    PlaybackAsyncToken,
    select_detail_decode_level,
)


def _request(
    tmp_path: Path,
    *,
    viewport: tuple[int, int] = (1200, 800),
    geometry: DetailGeometryState = DetailGeometryState(),
    texture_limit: int = 8192,
    zoom_factor: float = 1.0,
) -> DetailRenderRequest:
    return DetailRenderRequest(
        generation=1,
        asset_id="asset-1",
        source_identity=AssetSourceIdentity.create(
            tmp_path / "photo.jpg",
            size_bytes=10,
            source_mtime_ns=20,
            index_revision=30,
            width=6000,
            height=4000,
        ),
        viewport_physical_size=viewport,
        device_pixel_ratio=2.0,
        geometry=geometry,
        reason="initial",
        texture_limit=texture_limit,
        zoom_factor=zoom_factor,
    )


def test_source_identity_prefers_mtime_and_falls_back_to_index(tmp_path: Path) -> None:
    identity = AssetSourceIdentity.create(
        tmp_path / "photo.jpg",
        size_bytes=100,
        source_mtime_ns=200,
        index_revision=300,
    )
    assert identity.revision == ("mtime", 100, 200)
    legacy = AssetSourceIdentity.create(
        tmp_path / "photo.jpg",
        size_bytes=100,
        index_revision=300,
    )
    assert legacy.revision == ("index", 100, 300)


def test_source_identity_creation_never_stats_on_the_calling_thread(tmp_path: Path) -> None:
    with patch.object(Path, "stat", side_effect=AssertionError("unexpected stat")):
        identity = AssetSourceIdentity.create(
            tmp_path / "photo.jpg",
            size_bytes=100,
            source_mtime_ns=200,
            width=4000,
            height=3000,
        )
    assert identity.revision == ("mtime", 100, 200)


def test_source_identity_exposes_stable_revision_contract(tmp_path: Path) -> None:
    source = tmp_path / "photo.jpg"
    source.write_bytes(b"source")
    legacy = AssetSourceIdentity.create(source)

    repaired = legacy.repair_revision_from_stat()

    assert legacy.has_stable_revision is False
    assert repaired.has_stable_revision is True
    assert repaired.size_bytes == len(b"source")
    assert repaired.revision[0] == "mtime"


def test_source_identity_remains_unstable_when_stat_fails(tmp_path: Path) -> None:
    legacy = AssetSourceIdentity.create(tmp_path / "missing.jpg")

    assert legacy.repair_revision_from_stat() is legacy
    assert legacy.revision[0] == "legacy"


def test_playback_async_token_checks_every_delivery_identity_field(tmp_path: Path) -> None:
    first = AssetSourceIdentity.create(
        tmp_path / "photo.jpg",
        size_bytes=10,
        source_mtime_ns=20,
    )
    replaced = AssetSourceIdentity.create(
        tmp_path / "photo.jpg",
        size_bytes=11,
        source_mtime_ns=21,
    )
    token = PlaybackAsyncToken.create(
        library_epoch=3,
        asset_generation=4,
        asset_id="asset",
        source_identity=first,
    )

    assert token.matches(
        library_epoch=3,
        asset_generation=4,
        asset_id="asset",
        source_identity=first,
    )
    assert not token.matches(
        library_epoch=3,
        asset_generation=4,
        asset_id="asset",
        source_identity=replaced,
    )


def test_decode_key_distinguishes_source_orientation(tmp_path: Path) -> None:
    first = _request(tmp_path)
    rotated_identity = AssetSourceIdentity.create(
        first.source_identity.path,
        size_bytes=first.source_identity.size_bytes,
        source_mtime_ns=first.source_identity.source_mtime_ns,
        width=first.source_identity.height,
        height=first.source_identity.width,
        orientation=6,
    )
    rotated = DetailRenderRequest(
        generation=first.generation,
        asset_id=first.asset_id,
        source_identity=rotated_identity,
        viewport_physical_size=first.viewport_physical_size,
        device_pixel_ratio=first.device_pixel_ratio,
        geometry=first.geometry,
        reason=first.reason,
        texture_limit=first.texture_limit,
        zoom_factor=first.zoom_factor,
    )

    assert DetailDecodeKey.from_request(first) != DetailDecodeKey.from_request(rotated)


def test_viewport_lod_uses_smallest_satisfying_tier(tmp_path: Path) -> None:
    assert select_detail_decode_level(_request(tmp_path)) == 2048


def test_active_zoom_only_increases_selected_lod(tmp_path: Path) -> None:
    assert select_detail_decode_level(_request(tmp_path, zoom_factor=2.0)) == 3072
    assert select_detail_decode_level(_request(tmp_path, zoom_factor=0.25)) == 2048


def test_rotation_crop_and_projection_increase_lod(tmp_path: Path) -> None:
    cropped = DetailGeometryState(
        crop_width=0.2,
        crop_height=0.2,
        rotate90=1,
        straighten=20.0,
        perspective_vertical=0.5,
    )
    assert select_detail_decode_level(_request(tmp_path, geometry=cropped)) == "full"


def test_geometry_state_preserves_extended_perspective_crop() -> None:
    geometry = DetailGeometryState.from_adjustments(
        {"Crop_CX": -0.1, "Crop_CY": 1.2, "Crop_W": 1.3, "Crop_H": 0.7}
    )

    assert geometry.crop_cx == pytest.approx(-0.1)
    assert geometry.crop_cy == pytest.approx(1.2)
    assert geometry.crop_width == pytest.approx(1.3)


def test_source_smaller_than_tier_is_not_upscaled(tmp_path: Path) -> None:
    request = _request(tmp_path)
    small_identity = AssetSourceIdentity.create(
        tmp_path / "small.png",
        width=640,
        height=480,
        source_mtime_ns=1,
    )
    small = DetailRenderRequest(
        generation=request.generation,
        asset_id=request.asset_id,
        source_identity=small_identity,
        viewport_physical_size=request.viewport_physical_size,
        device_pixel_ratio=request.device_pixel_ratio,
        geometry=request.geometry,
        reason=request.reason,
    )
    assert select_detail_decode_level(small) == 640


def test_missing_indexed_dimensions_uses_full_compatibility_level(tmp_path: Path) -> None:
    request = _request(tmp_path)
    unknown = DetailRenderRequest(
        generation=1,
        asset_id="legacy",
        source_identity=AssetSourceIdentity.create(tmp_path / "legacy.jpg"),
        viewport_physical_size=request.viewport_physical_size,
        device_pixel_ratio=1.0,
        geometry=DetailGeometryState(),
        reason="initial",
    )
    assert select_detail_decode_level(unknown) == "full"

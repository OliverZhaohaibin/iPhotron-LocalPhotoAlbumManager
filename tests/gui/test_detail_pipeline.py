from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from PySide6.QtGui import QImage

from iPhoto.gui.detail_pipeline import (
    AssetSourceIdentity,
    DetailFrameCache,
    DetailFrameIdentity,
    DetailGeometryState,
    DetailRenderRequest,
    select_detail_decode_level,
)


def _request(
    tmp_path: Path,
    *,
    viewport: tuple[int, int] = (1200, 800),
    geometry: DetailGeometryState = DetailGeometryState(),
    texture_limit: int = 8192,
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


def test_viewport_lod_uses_smallest_satisfying_tier(tmp_path: Path) -> None:
    assert select_detail_decode_level(_request(tmp_path)) == 2048


def test_rotation_crop_and_projection_increase_lod(tmp_path: Path) -> None:
    cropped = DetailGeometryState(
        crop_width=0.2,
        crop_height=0.2,
        rotate90=1,
        straighten=20.0,
        perspective_vertical=0.5,
    )
    assert select_detail_decode_level(_request(tmp_path, geometry=cropped)) == "full"


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


def test_frame_identity_tracks_source_and_sidecar_versions(tmp_path: Path) -> None:
    source = tmp_path / "photo.jpg"
    source.write_bytes(b"image-v1")
    first = DetailFrameIdentity.from_path(source)

    source.write_bytes(b"image-version-two")
    os.utime(source, None)
    second = DetailFrameIdentity.from_path(source)
    assert second != first

    sidecar = source.with_suffix(".ipo")
    sidecar.write_text("<iPhoto/>", encoding="utf-8")
    third = DetailFrameIdentity.from_path(source)
    assert third != second


def test_frame_cache_is_bounded_and_returns_detached_images(tmp_path: Path) -> None:
    cache = DetailFrameCache(budget_bytes=1024 * 1024, max_entries=2)
    identities = []
    for index in range(3):
        source = tmp_path / f"{index}.jpg"
        source.write_bytes(bytes([index]))
        identity = DetailFrameIdentity.from_path(source)
        identities.append(identity)
        image = QImage(64, 64, QImage.Format.Format_RGBA8888)
        image.fill(index)
        assert cache.put(identity, image, {"Exposure": index})

    assert cache.get(identities[0]) is None
    cached = cache.get(identities[-1])
    assert cached is not None
    image, adjustments = cached
    assert not image.isNull()
    assert adjustments == {"Exposure": 2}


def test_frame_cache_precisely_invalidates_one_path(tmp_path: Path) -> None:
    cache = DetailFrameCache(budget_bytes=1024 * 1024, max_entries=3)
    identities = []
    for name in ("a.jpg", "b.jpg"):
        source = tmp_path / name
        source.write_bytes(name.encode())
        identity = DetailFrameIdentity.from_path(source)
        identities.append(identity)
        cache.put(identity, QImage(32, 32, QImage.Format.Format_RGBA8888), {})

    cache.invalidate_path(identities[0].path)
    assert cache.get(identities[0]) is None
    assert cache.get(identities[1]) is not None

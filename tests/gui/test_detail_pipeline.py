from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtGui import QImage

from iPhoto.gui.detail_pipeline import DetailFrameCache, DetailFrameIdentity


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

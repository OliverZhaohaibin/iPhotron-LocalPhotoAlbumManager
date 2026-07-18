from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtGui import QImage

from iPhoto.gui.detail_decode_backend import DecodedSurface
from iPhoto.gui.detail_pipeline import (
    AssetSourceIdentity,
    DetailDecodeKey,
    DetailGeometryState,
    DetailRenderRequest,
)
from iPhoto.gui.detail_surface_cache import (
    MappedSurfaceCache,
    NeutralSurfaceStore,
    SurfaceCacheCorruptError,
    surface_memory_budget_bytes,
)


def _request(source: Path, *, revision: int = 11, level: int = 1024) -> DetailRenderRequest:
    return DetailRenderRequest(
        generation=1,
        asset_id="asset-1",
        source_identity=AssetSourceIdentity.create(
            source,
            size_bytes=1234,
            source_mtime_ns=revision,
            width=1600,
            height=1200,
            orientation=1,
        ),
        viewport_physical_size=(800, 600),
        device_pixel_ratio=1.0,
        geometry=DetailGeometryState(),
        reason="initial",
        decode_level=level,
    )


def _surface(request: DetailRenderRequest, width: int = 8, height: int = 4) -> DecodedSurface:
    image = QImage(width, height, QImage.Format.Format_RGBA8888)
    image.fill(0xFF123456)
    return DecodedSurface(
        image=image,
        decode_key=DetailDecodeKey.from_request(request),
        source_size=(1600, 1200),
        decoded_size=(width, height),
        decode_level=request.decode_level or "full",
        backend="qt",
    )


def test_surface_memory_budget_is_two_percent_with_clamps() -> None:
    assert surface_memory_budget_bytes(1 * 1024**3) == 128 * 1024**2
    assert surface_memory_budget_bytes(16 * 1024**3) == int(16 * 1024**3 * 0.02)
    assert surface_memory_budget_bytes(128 * 1024**3) == 512 * 1024**2


def test_mapped_surface_lru_accounts_real_stride_and_evicts(tmp_path: Path) -> None:
    request_a = _request(tmp_path / "a.jpg")
    request_b = _request(tmp_path / "b.jpg")
    surface_a = _surface(request_a, 8, 4)
    surface_b = _surface(request_b, 8, 4)
    cache = MappedSurfaceCache(budget_bytes=surface_a.image.sizeInBytes())

    assert cache.put(surface_a)
    assert cache.put(surface_b)
    assert cache.get(surface_a.decode_key) is None
    hit = cache.get(surface_b.decode_key)
    assert hit is not None
    assert hit.cache_tier == "memory"
    assert cache.used_bytes == surface_b.image.bytesPerLine() * surface_b.image.height()


def test_disk_store_round_trip_returns_mmap_backed_rgba_surface(tmp_path: Path) -> None:
    request = _request(tmp_path / "photo.jpg")
    store = NeutralSurfaceStore(tmp_path)
    original = _surface(request)

    assert store.write(request, original)
    loaded = store.load(request)

    assert loaded is not None
    assert loaded.cache_tier == "disk"
    assert loaded.backing_owner is not None
    assert loaded.decode_key == original.decode_key
    assert loaded.decoded_size == original.decoded_size
    assert bytes(loaded.image.constBits()[:4]) == bytes(original.image.constBits()[:4])


def test_disk_store_source_revision_changes_key_but_sidecar_is_not_an_input(tmp_path: Path) -> None:
    store = NeutralSurfaceStore(tmp_path)
    request = _request(tmp_path / "photo.jpg", revision=11)
    changed = _request(tmp_path / "photo.jpg", revision=12)
    assert store.entry_path(request) != store.entry_path(changed)

    # DetailRenderRequest deliberately has no sidecar revision field. Raw edit
    # mappings therefore do not affect the neutral cache identity.
    edited = replace(request, raw_adjustments={"Exposure": 0.5})
    assert store.entry_path(request) == store.entry_path(edited)


@pytest.mark.parametrize("damage", ["truncate", "payload"])
def test_disk_store_rejects_corruption(tmp_path: Path, damage: str) -> None:
    request = _request(tmp_path / "photo.jpg")
    store = NeutralSurfaceStore(tmp_path)
    assert store.write(request, _surface(request))
    path = store.entry_path(request)
    assert path is not None
    data = bytearray(path.read_bytes())
    if damage == "truncate":
        path.write_bytes(data[:64])
    else:
        data[-1] ^= 0xFF
        path.write_bytes(data)

    with pytest.raises(SurfaceCacheCorruptError):
        store.load(request)


def test_unbound_store_is_a_disabled_disk_tier(tmp_path: Path) -> None:
    request = _request(tmp_path / "photo.jpg")
    store = NeutralSurfaceStore(None)
    assert store.entry_path(request) is None
    assert store.load(request) is None
    assert store.write(request, _surface(request)) is False

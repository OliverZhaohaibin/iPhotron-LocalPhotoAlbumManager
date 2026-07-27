from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from PySide6.QtGui import QImage

import iPhoto.gui.detail_surface_cache as surface_cache_module
from iPhoto.core.color_resolver import ColorStats
from iPhoto.gui.detail_decode_backend import DecodedSurface
from iPhoto.gui.detail_pipeline import (
    AssetSourceIdentity,
    DetailDecodeKey,
    DetailGeometryState,
    DetailRenderRequest,
)
from iPhoto.gui.detail_surface_cache import (
    CachedStillDecodeBackend,
    MappedSurfaceCache,
    NeutralSurfaceStore,
    SurfaceCacheCorruptError,
    surface_memory_budget_bytes,
)


class _Token:
    def is_cancelled(self) -> bool:
        return False


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
        color_stats=ColorStats(saturation_mean=0.73, cast_magnitude=0.21),
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
    assert loaded.color_stats == original.color_stats
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


def test_decoder_contract_bump_bypasses_legacy_surface_and_redecodes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path / "photo.heic")
    store = NeutralSurfaceStore(tmp_path)

    monkeypatch.setattr(surface_cache_module, "_DECODE_SEMANTICS_CONTRACT", 1)
    legacy_path = store.entry_path(request)
    assert legacy_path is not None
    assert store.write(request, _surface(request, width=8, height=4))
    assert legacy_path.exists()

    monkeypatch.setattr(surface_cache_module, "_DECODE_SEMANTICS_CONTRACT", 2)
    current_path = store.entry_path(request)
    assert current_path is not None
    assert current_path != legacy_path
    assert store.load(request) is None

    corrected = _surface(request, width=4, height=8)
    delegate = Mock()
    delegate.decode.return_value = corrected
    backend = CachedStillDecodeBackend(delegate, store=store)
    decoded = backend.decode(request, _Token())
    backend.shutdown()

    delegate.decode.assert_called_once()
    assert decoded.decoded_size == (4, 8)
    assert current_path.exists()


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


def test_color_stats_are_computed_once_across_lod_decodes(tmp_path: Path) -> None:
    first = _request(tmp_path / "photo.jpg", level=1024)
    second = _request(tmp_path / "photo.jpg", level=2048)
    delegate = Mock()
    delegate.decode.side_effect = [_surface(first), _surface(second)]
    backend = CachedStillDecodeBackend(delegate, store=NeutralSurfaceStore(None))
    stats = ColorStats(saturation_mean=0.91)

    with patch(
        "iPhoto.gui.detail_surface_cache.compute_color_statistics",
        return_value=stats,
    ) as compute:
        decoded_first = backend.decode(first, _Token())
        decoded_second = backend.decode(second, _Token())

    backend.shutdown()
    assert compute.call_count == 1
    assert decoded_first.color_stats is stats
    assert decoded_second.color_stats is stats

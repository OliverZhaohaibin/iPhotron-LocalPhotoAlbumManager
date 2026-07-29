from __future__ import annotations

import os
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


def test_disk_store_rejects_truncated_header_synchronously(tmp_path: Path) -> None:
    request = _request(tmp_path / "photo.jpg")
    store = NeutralSurfaceStore(tmp_path)
    assert store.write(request, _surface(request))
    path = store.entry_path(request)
    assert path is not None
    data = bytearray(path.read_bytes())
    path.write_bytes(data[:64])

    with pytest.raises(SurfaceCacheCorruptError):
        store.load(request)


def test_disk_hit_defers_payload_checksum_to_explicit_validation(tmp_path: Path) -> None:
    request = _request(tmp_path / "photo.jpg")
    store = NeutralSurfaceStore(tmp_path)
    assert store.write(request, _surface(request))

    with patch.object(
        surface_cache_module,
        "_checksum",
        wraps=surface_cache_module._checksum,
    ) as checksum:
        loaded = store.load(request)
        assert loaded is not None
        assert checksum.call_count == 0
        assert store.validate(request)
        assert checksum.call_count == 1


def test_async_payload_failure_evicts_and_forces_redecode(tmp_path: Path) -> None:
    request = _request(tmp_path / "photo.jpg")
    store = NeutralSurfaceStore(tmp_path)
    assert store.write(request, _surface(request))
    path = store.entry_path(request)
    assert path is not None
    damaged = bytearray(path.read_bytes())
    damaged[-1] ^= 0xFF
    path.write_bytes(damaged)
    delegate = Mock()
    delegate.decode.side_effect = lambda prepared, _token: _surface(prepared)
    backend = CachedStillDecodeBackend(delegate, store=store)

    disk_hit = backend.decode(request, _Token())
    backend.shutdown()
    decoded = backend.decode(request, _Token())

    assert disk_hit.cache_tier == "disk"
    assert decoded.cache_tier == "decode"
    assert delegate.decode.call_count == 1


def test_unbound_store_is_a_disabled_disk_tier(tmp_path: Path) -> None:
    request = _request(tmp_path / "photo.jpg")
    store = NeutralSurfaceStore(None)
    assert store.entry_path(request) is None
    assert store.load(request) is None
    assert store.write(request, _surface(request)) is False


def test_legacy_identity_is_rejected_by_reusable_cache_layers(tmp_path: Path) -> None:
    request = replace(
        _request(tmp_path / "photo.jpg"),
        source_identity=AssetSourceIdentity.create(tmp_path / "photo.jpg"),
    )
    surface = _surface(request)
    store = NeutralSurfaceStore(tmp_path)
    cache = MappedSurfaceCache()

    assert request.source_identity.has_stable_revision is False
    assert store.entry_path(request) is None
    assert store.write(request, surface) is False
    assert cache.put(surface) is False
    assert cache.get(surface.decode_key) is None


def test_missing_revision_is_repaired_by_stat_before_caching(tmp_path: Path) -> None:
    source = tmp_path / "photo.jpg"
    source.write_bytes(b"first")
    request = replace(
        _request(source),
        source_identity=AssetSourceIdentity.create(source, width=1600, height=1200),
    )
    delegate = Mock()
    delegate.decode.side_effect = lambda prepared, _token: _surface(prepared)
    backend = CachedStillDecodeBackend(delegate, store=NeutralSurfaceStore(None))

    first = backend.decode(request, _Token())
    second = backend.decode(request, _Token())
    backend.shutdown()

    assert first.decode_key.source_revision[0] == "mtime"
    assert second.cache_tier == "memory"
    assert delegate.decode.call_count == 1


def test_in_place_source_replacement_misses_old_memory_and_disk_entries(
    tmp_path: Path,
) -> None:
    source = tmp_path / "photo.jpg"
    source.write_bytes(b"first")
    initial_stat = source.stat()
    request = replace(
        _request(source),
        source_identity=AssetSourceIdentity.create(source, width=1600, height=1200),
    )
    delegate = Mock()
    delegate.decode.side_effect = lambda prepared, _token: _surface(prepared)
    store = NeutralSurfaceStore(tmp_path)
    backend = CachedStillDecodeBackend(delegate, store=store)

    first = backend.decode(request, _Token())
    backend.shutdown()
    source.write_bytes(b"replacement-is-larger")
    os.utime(
        source,
        ns=(initial_stat.st_atime_ns, initial_stat.st_mtime_ns + 1_000_000_000),
    )
    second = backend.decode(request, _Token())

    assert first.decode_key.source_revision != second.decode_key.source_revision
    assert delegate.decode.call_count == 2


def test_stat_failure_bypasses_memory_and_disk_cache(tmp_path: Path) -> None:
    source = tmp_path / "missing.jpg"
    request = replace(
        _request(source),
        source_identity=AssetSourceIdentity.create(source, width=1600, height=1200),
    )
    delegate = Mock()
    delegate.decode.side_effect = lambda prepared, _token: _surface(prepared)
    store = Mock()
    backend = CachedStillDecodeBackend(delegate, store=store)

    backend.decode(request, _Token())
    backend.decode(request, _Token())
    backend.shutdown()

    assert delegate.decode.call_count == 2
    assert backend.memory_cache.used_bytes == 0
    store.load.assert_not_called()
    store.write.assert_not_called()


def test_surface_write_uses_a_buffer_without_python_bytes_copy(tmp_path: Path) -> None:
    request = _request(tmp_path / "photo.jpg")
    store = NeutralSurfaceStore(tmp_path)
    synthetic_64_mib = _surface(request, width=4096, height=4096)
    assert synthetic_64_mib.image.sizeInBytes() == 64 * 1024 * 1024

    with patch.object(
        surface_cache_module,
        "bytes",
        side_effect=AssertionError("payload copied into Python bytes"),
        create=True,
    ):
        assert store.write(request, synthetic_64_mib)


def test_prune_is_batched_across_repeated_writes(tmp_path: Path) -> None:
    request = _request(tmp_path / "photo.jpg")
    store = NeutralSurfaceStore(
        tmp_path,
        prune_every_writes=25,
        prune_after_bytes=10**12,
        prune_interval_seconds=10**6,
    )

    with patch.object(store, "prune") as prune:
        for _ in range(100):
            assert store.write(request, _surface(request))

    assert prune.call_count == 4


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

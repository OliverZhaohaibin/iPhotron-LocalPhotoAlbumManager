from __future__ import annotations

import os
import shutil
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from PySide6.QtGui import QImage

import iPhoto.gui.detail_surface_cache as surface_cache_module
import iPhoto.gui.detail_surface_cache_index as surface_cache_index_module
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


def test_disk_store_uses_v3_sqlite_index(tmp_path: Path) -> None:
    request = _request(tmp_path / "photo.jpg")
    store = NeutralSurfaceStore(tmp_path)

    assert store.write(request, _surface(request))
    assert store.root is not None
    assert store.root.name == "v3"
    assert (store.root / "index.sqlite3").is_file()


def test_trusted_disk_hit_does_not_scan_payload_checksum(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path / "photo.jpg")
    store = NeutralSurfaceStore(tmp_path)
    assert store.write(request, _surface(request))

    def fail_checksum(_payload) -> int:
        raise AssertionError("trusted cache hit must not hash the payload")

    monkeypatch.setattr(surface_cache_module, "_checksum", fail_checksum)
    assert store.load(request) is not None


def test_sampled_audit_discards_same_size_corruption_with_restored_mtime(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path / "photo.jpg")
    store = NeutralSurfaceStore(tmp_path)
    assert store.write(request, _surface(request))
    path = store.entry_path(request)
    assert path is not None
    stat = path.stat()
    with path.open("r+b") as stream:
        stream.seek(-1, os.SEEK_END)
        original = stream.read(1)
        stream.seek(-1, os.SEEK_END)
        stream.write(bytes([original[0] ^ 0xFF]))
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    store._disk_hit_count = surface_cache_module._ACCESS_AUDIT_INTERVAL - 1

    assert store.load(request) is not None
    assert path.exists()

    store.run_pending_audits()
    assert not path.exists()
    assert store.load(request) is None


def test_writes_below_maintenance_watermark_do_not_traverse_payload_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = NeutralSurfaceStore(tmp_path, budget_bytes=64 * 1024 * 1024)
    request = _request(tmp_path / "photo.jpg")

    def fail_glob(_self: Path, _pattern: str):
        raise AssertionError("ordinary writes must not traverse the payload directory")

    monkeypatch.setattr(Path, "glob", fail_glob)
    assert store.write(request, _surface(request))


def test_indexed_prune_uses_lru_and_low_watermark(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Windows can return the same time_ns() value for adjacent writes. Access
    # updates must still become strictly newer than the persisted row.
    monkeypatch.setattr(surface_cache_index_module.time, "time_ns", lambda: 100)
    first = _request(tmp_path / "first.jpg", revision=1)
    second = _request(tmp_path / "second.jpg", revision=2)
    third = _request(tmp_path / "third.jpg", revision=3)
    probe = NeutralSurfaceStore(tmp_path, budget_bytes=1 << 30)
    assert probe.write(first, _surface(first))
    first_path = probe.entry_path(first)
    assert first_path is not None
    file_bytes = first_path.stat().st_size
    probe.close()
    shutil.rmtree(tmp_path / ".iPhoto")

    store = NeutralSurfaceStore(tmp_path, budget_bytes=1 << 30)
    assert store.write(first, _surface(first))
    assert store.write(second, _surface(second))
    assert store.write(third, _surface(third))
    assert store.load(first) is not None
    store._budget_override = file_bytes * 2 + 64
    store.prune()

    assert store.entry_path(first).exists()  # type: ignore[union-attr]
    assert not store.entry_path(second).exists()  # type: ignore[union-attr]
    assert not store.entry_path(third).exists()  # type: ignore[union-attr]


def test_dirty_recovery_indexes_orphan_payload_and_removes_temp(tmp_path: Path) -> None:
    request = _request(tmp_path / "photo.jpg")
    store = NeutralSurfaceStore(tmp_path)
    assert store.write(request, _surface(request))
    path = store.entry_path(request)
    index = store._index_for_root()
    assert path is not None and index is not None
    index.remove(surface_cache_module._key_digest(request))
    temporary = path.parent / ".orphan.ipsurface.crash.tmp"
    temporary.write_bytes(b"partial")

    recovered = NeutralSurfaceStore(tmp_path)
    loaded = recovered.load(request)

    assert loaded is not None
    assert loaded.cache_tier == "disk"
    assert not temporary.exists()


def test_missing_payload_removes_stale_metadata_row(tmp_path: Path) -> None:
    request = _request(tmp_path / "photo.jpg")
    store = NeutralSurfaceStore(tmp_path)
    assert store.write(request, _surface(request))
    path = store.entry_path(request)
    index = store._index_for_root()
    assert path is not None and index is not None
    path.unlink()

    assert store.load(request) is None
    assert index.get(surface_cache_module._key_digest(request)) is None


def test_corrupt_sqlite_index_is_rebuilt_from_untrusted_payload(tmp_path: Path) -> None:
    request = _request(tmp_path / "photo.jpg")
    store = NeutralSurfaceStore(tmp_path)
    assert store.write(request, _surface(request))
    root = store.root
    assert root is not None
    store.close()
    (root / "index.sqlite3").write_bytes(b"not a sqlite database")

    recovered = NeutralSurfaceStore(tmp_path)
    loaded = recovered.load(request)

    assert loaded is not None
    assert loaded.decoded_size == (8, 4)
    assert tuple(root.glob("index.sqlite3.corrupt-*"))


def test_first_v3_write_removes_rebuildable_v2_namespace(tmp_path: Path) -> None:
    legacy = tmp_path / ".iPhoto" / "cache" / "detail-surfaces" / "v2"
    legacy.mkdir(parents=True)
    (legacy / "stale.cache").write_bytes(b"rebuildable")
    request = _request(tmp_path / "photo.jpg")

    assert NeutralSurfaceStore(tmp_path).write(request, _surface(request))

    assert not legacy.exists()


def test_disk_store_source_revision_changes_key_but_sidecar_is_not_an_input(tmp_path: Path) -> None:
    store = NeutralSurfaceStore(tmp_path)
    request = _request(tmp_path / "photo.jpg", revision=11)
    changed = _request(tmp_path / "photo.jpg", revision=12)
    assert store.entry_path(request) != store.entry_path(changed)

    # DetailRenderRequest deliberately has no sidecar revision field. Raw edit
    # mappings therefore do not affect the neutral cache identity.
    edited = replace(request, raw_adjustments={"Exposure": 0.5})
    assert store.entry_path(request) == store.entry_path(edited)


# LIVE PHOTO REGRESSION GUARD — DO NOT DELETE OR WEAKEN THIS TEST.
# Windows HEIC/WIC may return display-oriented pixels.  Contract 1 caches can
# therefore contain a second, incorrect rotation and must never bypass a fresh
# decode after upgrading.  This is the automated counterpart of the verified
# IMG_3684.HEIC (EXIF Orientation=6) Windows regression.
def test_live_photo_decoder_contract_rejects_legacy_wrong_orientation_surface(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path / "photo.heic")
    store = NeutralSurfaceStore(tmp_path)
    production_contract = surface_cache_module._DECODE_SEMANTICS_CONTRACT

    # This assertion deliberately clamps the production contract.  Returning
    # to 1 would make already-persisted horizontal Live Photo stills visible
    # again before the corrected WIC decoder has a chance to run.
    assert production_contract >= 2

    monkeypatch.setattr(surface_cache_module, "_DECODE_SEMANTICS_CONTRACT", 1)
    legacy_path = store.entry_path(request)
    assert legacy_path is not None
    assert store.write(request, _surface(request, width=8, height=4))
    assert legacy_path.exists()

    monkeypatch.setattr(
        surface_cache_module,
        "_DECODE_SEMANTICS_CONTRACT",
        production_contract,
    )
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

    reloaded = store.load(request)
    assert reloaded is not None
    assert reloaded.cache_tier == "disk"
    assert reloaded.decoded_size == (4, 8)


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


def test_backend_bind_library_schedules_keyword_recovery(tmp_path: Path) -> None:
    store = Mock()
    backend = CachedStillDecodeBackend(Mock(), store=store)

    backend.bind_library(tmp_path)
    backend.shutdown()

    store.bind_library.assert_called_once_with(tmp_path)
    store.maintenance.assert_called_once_with(recover=True)


def test_bind_library_without_prior_surface_cache_initializes_v3(tmp_path: Path) -> None:
    backend = CachedStillDecodeBackend(Mock(), store=NeutralSurfaceStore(None))

    backend.bind_library(tmp_path)
    backend.shutdown()

    assert (
        tmp_path
        / ".iPhoto"
        / "cache"
        / "detail-surfaces"
        / "v3"
        / "index.sqlite3"
    ).is_file()


def test_unstable_identity_bypasses_reusable_memory_and_disk_cache(
    tmp_path: Path,
) -> None:
    request = replace(
        _request(tmp_path / "missing.jpg"),
        source_identity=AssetSourceIdentity.create(tmp_path / "missing.jpg"),
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

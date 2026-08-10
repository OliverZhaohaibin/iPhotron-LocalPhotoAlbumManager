from __future__ import annotations

import os
import shutil
import sqlite3
from dataclasses import replace
from pathlib import Path
from threading import Event, Thread
from unittest.mock import Mock, patch

import pytest
from PySide6.QtGui import QImage

import iPhoto.gui.detail_surface_cache as surface_cache_module
import iPhoto.gui.detail_surface_cache_index as surface_cache_index_module
from iPhoto.core.color_resolver import ColorStats
from iPhoto.gui.detail_decode_backend import DecodeCancelledError, DecodedSurface
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
from iPhoto.gui.detail_surface_cache_index import SurfaceCacheIndex


class _Token:
    def is_cancelled(self) -> bool:
        return False


class _FailingConnection:
    def __init__(self, connection: sqlite3.Connection, fail_when) -> None:
        self._connection = connection
        self._fail_when = fail_when

    def execute(self, sql: str, parameters=()):
        if self._fail_when(str(sql)):
            raise sqlite3.DatabaseError("injected runtime index failure")
        return self._connection.execute(sql, parameters)

    def __getattr__(self, name: str):
        return getattr(self._connection, name)


def _fail_index_execute(index: SurfaceCacheIndex, fail_when) -> None:
    assert index.ensure_open()
    connection = index._connection
    assert connection is not None
    index._connection = _FailingConnection(connection, fail_when)  # type: ignore[assignment]


def _index_metadata(root: Path) -> dict[str, int]:
    connection = sqlite3.connect(root / "index.sqlite3")
    try:
        return {
            str(key): int(value)
            for key, value in connection.execute(
                "SELECT key, value FROM metadata"
            ).fetchall()
        }
    finally:
        connection.close()


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


def test_disk_usage_failure_does_not_prune_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path / "photo.jpg")
    store = NeutralSurfaceStore(tmp_path)
    disk_usage = Mock(side_effect=OSError("volume unavailable"))
    monkeypatch.setattr(shutil, "disk_usage", disk_usage)

    assert store.write(request, _surface(request))
    path = store.entry_path(request)
    index = store._index_for_root()
    assert path is not None and index is not None
    finish_maintenance = Mock(wraps=index.finish_maintenance)
    monkeypatch.setattr(index, "finish_maintenance", finish_maintenance)

    store.maintenance(force_prune=True)

    assert disk_usage.call_count >= 2
    assert path.exists()
    assert index.get(surface_cache_module._key_digest(request)) is not None
    finish_maintenance.assert_not_called()


def test_explicit_zero_budget_prunes_all_indexed_payloads(tmp_path: Path) -> None:
    request = _request(tmp_path / "photo.jpg")
    store = NeutralSurfaceStore(tmp_path, budget_bytes=0)

    assert store.write(request, _surface(request))
    path = store.entry_path(request)
    index = store._index_for_root()
    assert path is not None and index is not None

    assert not path.exists()
    assert index.get(surface_cache_module._key_digest(request)) is None


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
    with index._lock:
        index._close_connection_locked()

    recovered = NeutralSurfaceStore(tmp_path)
    loaded = recovered.load(request)

    assert loaded is not None
    assert loaded.cache_tier == "disk"
    assert not temporary.exists()


def test_overlapping_index_leases_keep_marker_dirty_until_last_close(
    tmp_path: Path,
) -> None:
    first = SurfaceCacheIndex(tmp_path)
    second = SurfaceCacheIndex(tmp_path)

    assert first.ensure_open()
    assert second.ensure_open()
    assert not first.needs_recovery
    assert not second.needs_recovery

    first.close(clean=True)

    metadata = _index_metadata(tmp_path)
    assert metadata["clean_shutdown"] == 0
    assert metadata["active_leases"] == 1
    assert metadata["recovery_required"] == 0

    second.close(clean=True)

    metadata = _index_metadata(tmp_path)
    assert metadata["clean_shutdown"] == 1
    assert metadata["active_leases"] == 0
    assert metadata["recovery_required"] == 0


def test_recovery_from_one_lease_survives_other_clean_close(tmp_path: Path) -> None:
    first = SurfaceCacheIndex(tmp_path)
    second = SurfaceCacheIndex(tmp_path)
    assert first.ensure_open()
    assert second.ensure_open()

    first.mark_recovery_required()
    first.close(clean=True)
    second.close(clean=True)

    metadata = _index_metadata(tmp_path)
    assert metadata["clean_shutdown"] == 0
    assert metadata["active_leases"] == 0
    assert metadata["recovery_required"] == 1

    reopened = SurfaceCacheIndex(tmp_path)
    assert reopened.ensure_open()
    assert reopened.needs_recovery
    reopened.close(clean=False)


def test_new_process_session_recovers_stale_active_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    crashed = SurfaceCacheIndex(tmp_path)
    assert crashed.ensure_open()
    with crashed._lock:
        crashed._close_connection_locked()
    monkeypatch.setattr(
        surface_cache_index_module,
        "_PROCESS_SESSION_TOKEN",
        crashed._session_token % ((1 << 63) - 1) + 1,
    )

    recovered = SurfaceCacheIndex(tmp_path)

    assert recovered.ensure_open()
    assert recovered.needs_recovery
    metadata = _index_metadata(tmp_path)
    assert metadata["clean_shutdown"] == 0
    assert metadata["active_leases"] == 1
    assert metadata["recovery_required"] == 1
    recovered.close(clean=False)


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


def test_replace_followed_by_upsert_failure_removes_orphan_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path / "photo.jpg")
    store = NeutralSurfaceStore(tmp_path)
    path = store.entry_path(request)
    index = store._index_for_root()
    assert path is not None and index is not None
    monkeypatch.setattr(index, "upsert", lambda _entry: False)

    assert not store.write(request, _surface(request))

    assert not path.exists()
    assert index.remove(surface_cache_module._key_digest(request))
    assert not index.needs_recovery


def test_failed_write_cleanup_marks_recovery_when_row_cannot_be_removed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path / "photo.jpg")
    store = NeutralSurfaceStore(tmp_path)
    path = store.entry_path(request)
    index = store._index_for_root()
    assert path is not None and index is not None
    monkeypatch.setattr(index, "upsert", lambda _entry: False)
    monkeypatch.setattr(index, "remove", lambda _digest: False)

    assert not store.write(request, _surface(request))

    assert not path.exists()
    assert index.needs_recovery


def test_sqlite_lookup_failure_falls_back_to_delegate_decode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path / "photo.jpg")
    store = NeutralSurfaceStore(tmp_path)
    assert store.write(request, _surface(request))
    path = store.entry_path(request)
    index = store._index_for_root()
    assert path is not None and index is not None
    _fail_index_execute(
        index,
        lambda sql: "FROM entries" in sql and "WHERE digest" in sql,
    )
    write = Mock(return_value=False)
    monkeypatch.setattr(store, "write", write)
    delegate = Mock()
    delegate.decode.side_effect = lambda prepared, _token: _surface(prepared)
    backend = CachedStillDecodeBackend(delegate, store=store)

    decoded = backend.decode(request, _Token())

    assert decoded.backend == "qt"
    delegate.decode.assert_called_once()
    assert path.exists()
    assert index.needs_recovery
    assert index._connection is None
    metadata = _index_metadata(index.root)
    assert metadata["clean_shutdown"] == 0
    assert metadata["recovery_required"] == 1
    backend.shutdown(timeout_ms=5000)

    reopened = SurfaceCacheIndex(index.root)
    assert reopened.ensure_open()
    assert reopened.needs_recovery
    reopened.close(clean=False)


def test_sqlite_lease_metadata_failure_keeps_index_dirty(tmp_path: Path) -> None:
    request = _request(tmp_path / "photo.jpg")
    store = NeutralSurfaceStore(tmp_path)
    assert store.write(request, _surface(request))
    path = store.entry_path(request)
    index = store._index_for_root()
    assert path is not None and index is not None
    _fail_index_execute(index, lambda sql: "SELECT value FROM metadata" in sql)

    assert store.load(request) is None

    assert path.exists()
    assert index._connection is None
    assert index.needs_recovery
    metadata = _index_metadata(index.root)
    assert metadata["clean_shutdown"] == 0
    assert metadata["recovery_required"] == 1

    reopened = SurfaceCacheIndex(index.root)
    assert reopened.ensure_open()
    assert reopened.needs_recovery
    assert _index_metadata(index.root)["active_leases"] == 1
    reopened.close(clean=False)


def test_sqlite_checksum_state_failure_does_not_discard_valid_payload(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path / "photo.jpg")
    store = NeutralSurfaceStore(tmp_path)
    assert store.write(request, _surface(request))
    path = store.entry_path(request)
    index = store._index_for_root()
    assert path is not None and index is not None
    digest = surface_cache_module._key_digest(request)
    index.mark_checksum_state(digest, "untrusted")
    _fail_index_execute(
        index,
        lambda sql: "UPDATE entries" in sql and "checksum_state" in sql,
    )

    assert store.load(request) is None

    assert path.exists()
    assert index.needs_recovery
    assert index._connection is None


def test_sqlite_upsert_failure_after_replace_removes_orphan_payload(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path / "photo.jpg")
    store = NeutralSurfaceStore(tmp_path)
    path = store.entry_path(request)
    index = store._index_for_root()
    assert path is not None and index is not None
    _fail_index_execute(index, lambda sql: "INSERT INTO entries" in sql)

    assert not store.write(request, _surface(request))

    assert not path.exists()
    assert index.needs_recovery


def test_recovery_required_index_does_not_close_clean(tmp_path: Path) -> None:
    index = SurfaceCacheIndex(tmp_path)
    assert index.ensure_open()
    index.mark_recovery_required()

    index.close()
    reopened = SurfaceCacheIndex(tmp_path)

    assert reopened.ensure_open()
    assert reopened.needs_recovery


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


def test_prune_remove_failure_is_bounded_and_keeps_maintenance_pending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path / "photo.jpg")
    store = NeutralSurfaceStore(tmp_path, budget_bytes=1)
    store._budget_override = 1 << 30
    assert store.write(request, _surface(request))
    index = store._index_for_root()
    assert index is not None
    store._budget_override = 1
    remove = Mock(return_value=False)
    monkeypatch.setattr(index, "remove", remove)

    store.maintenance(force_prune=True)

    remove.assert_called_once()
    assert index.needs_recovery
    assert index.maintenance_due(
        1,
        byte_interval=surface_cache_module._MAINTENANCE_WRITE_BYTES,
        time_interval_ns=surface_cache_module._MAINTENANCE_INTERVAL_NS,
    )


def test_prune_unlink_failure_keeps_row_and_marks_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path / "photo.jpg")
    store = NeutralSurfaceStore(tmp_path, budget_bytes=1 << 30)
    assert store.write(request, _surface(request))
    path = store.entry_path(request)
    index = store._index_for_root()
    assert path is not None and index is not None
    original_unlink = Path.unlink

    def fail_payload_unlink(candidate: Path, *, missing_ok: bool = False) -> None:
        if candidate == path:
            raise OSError("injected payload unlink failure")
        original_unlink(candidate, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_payload_unlink)
    store._budget_override = 1

    store.maintenance(force_prune=True)

    assert path.exists()
    assert index.get(surface_cache_module._key_digest(request)) is not None
    assert index.needs_recovery


def test_sqlite_failure_during_maintenance_never_prunes_payloads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path / "photo.jpg")
    store = NeutralSurfaceStore(tmp_path, budget_bytes=1 << 30)
    assert store.write(request, _surface(request))
    path = store.entry_path(request)
    index = store._index_for_root()
    assert path is not None and index is not None
    finish_maintenance = Mock(wraps=index.finish_maintenance)
    monkeypatch.setattr(index, "finish_maintenance", finish_maintenance)
    store._budget_override = 0
    _fail_index_execute(index, lambda sql: "ORDER BY last_access_ns" in sql)

    store.maintenance(force_prune=True)

    assert path.exists()
    assert index.needs_recovery
    assert index._connection is None
    finish_maintenance.assert_not_called()


def test_sqlite_failure_during_recovery_retries_from_payload_scan(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path / "photo.jpg")
    store = NeutralSurfaceStore(tmp_path, budget_bytes=1 << 30)
    assert store.write(request, _surface(request))
    path = store.entry_path(request)
    index = store._index_for_root()
    assert path is not None and index is not None
    index.mark_recovery_required()
    _fail_index_execute(
        index,
        lambda sql: "FROM entries" in sql
        and "WHERE digest" not in sql
        and "ORDER BY" not in sql,
    )

    store.maintenance(recover=True)

    assert path.exists()
    assert index.needs_recovery
    assert index._connection is None

    store.maintenance(recover=True)

    assert not index.needs_recovery
    loaded = store.load(request)
    assert loaded is not None
    assert loaded.cache_tier == "disk"
    assert tuple(index.root.glob("index.sqlite3.corrupt-*"))


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

    reloaded = NeutralSurfaceStore(tmp_path).load(request)
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


def test_backend_bind_library_schedules_normal_maintenance(tmp_path: Path) -> None:
    store = Mock()
    store.library_root = tmp_path.absolute()
    backend = CachedStillDecodeBackend(Mock(), store=store)

    backend.bind_library(tmp_path)
    backend.shutdown()

    store.maintenance.assert_called_once_with()
    store.close.assert_called_once_with()


def test_cross_library_bind_uses_a_new_store_generation(tmp_path: Path) -> None:
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    old_store = Mock()
    old_store.library_root = old_root.absolute()
    new_store = Mock()
    new_store.library_root = new_root.absolute()
    factory = Mock(return_value=new_store)
    backend = CachedStillDecodeBackend(
        Mock(),
        store=old_store,
        store_factory=factory,
    )

    backend.bind_library(new_root)
    backend.shutdown()

    factory.assert_called_once_with(new_root.absolute())
    old_store.bind_library.assert_not_called()
    old_store.close.assert_called_once_with()
    new_store.maintenance.assert_called_once_with()
    new_store.close.assert_called_once_with()


def test_rebind_returns_before_old_store_io_drains(tmp_path: Path) -> None:
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    started = Event()
    release = Event()
    old_closed = Event()
    old_store = Mock()
    old_store.library_root = old_root.absolute()
    old_store.close.side_effect = old_closed.set
    new_store = Mock()
    new_store.library_root = new_root.absolute()
    backend = CachedStillDecodeBackend(
        Mock(),
        store=old_store,
        store_factory=Mock(return_value=new_store),
    )
    generation = backend._active_store_generation

    def slow_io() -> None:
        started.set()
        assert release.wait(5)

    backend._submit_for_generation(generation, slow_io)
    assert started.wait(5)

    backend.bind_library(new_root)

    assert not old_closed.is_set()
    release.set()
    backend.shutdown(timeout_ms=5000)
    assert old_closed.is_set()


def test_a_b_a_rebind_keeps_latest_library_lease_dirty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library_a = tmp_path / "library-a"
    library_b = tmp_path / "library-b"
    request = _request(library_a / "photo.jpg")
    started = Event()
    release = Event()
    old_closed = Event()
    old_store = NeutralSurfaceStore(library_a)
    original_close = old_store.close

    def close_old_store() -> None:
        original_close()
        old_closed.set()

    monkeypatch.setattr(old_store, "close", close_old_store)
    delegate = Mock()

    def slow_decode(
        prepared: DetailRenderRequest,
        _token: _Token,
    ) -> DecodedSurface:
        started.set()
        assert release.wait(5)
        return _surface(prepared)

    delegate.decode.side_effect = slow_decode
    backend = CachedStillDecodeBackend(delegate, store=old_store)
    failures: list[BaseException] = []

    def decode() -> None:
        try:
            backend.decode(request, _Token())
        except DecodeCancelledError as exc:
            failures.append(exc)

    worker = Thread(target=decode)
    worker.start()
    assert started.wait(5)

    backend.bind_library(library_b)
    backend.bind_library(library_a)
    current_store = backend.store
    current_index = current_store._index_for_root()
    assert current_index is not None
    original_glob = Path.glob

    def fail_a_payload_scan(path: Path, pattern: str):
        if path == current_index.root:
            raise AssertionError("same-session A→B→A must not scan payloads")
        return original_glob(path, pattern)

    monkeypatch.setattr(Path, "glob", fail_a_payload_scan)

    current_store.maintenance()

    assert not current_index.needs_recovery
    release.set()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert old_closed.wait(5)
    assert len(failures) == 1
    assert isinstance(failures[0], DecodeCancelledError)
    assert backend.memory_cache.used_bytes == 0
    metadata = _index_metadata(current_index.root)
    assert metadata["clean_shutdown"] == 0
    assert metadata["active_leases"] == 1
    assert metadata["recovery_required"] == 0

    backend.shutdown(timeout_ms=5000)

    metadata = _index_metadata(current_index.root)
    assert metadata["clean_shutdown"] == 1
    assert metadata["active_leases"] == 0
    assert metadata["recovery_required"] == 0


def test_shutdown_timeout_closes_store_after_active_decode_finishes(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path / "photo.jpg")
    started = Event()
    release = Event()
    closed = Event()
    store = Mock()
    store.library_root = tmp_path.absolute()

    def slow_load(_request: DetailRenderRequest) -> None:
        started.set()
        assert release.wait(5)
        return None

    store.load.side_effect = slow_load
    store.close.side_effect = closed.set
    delegate = Mock()
    delegate.decode.side_effect = lambda prepared, _token: _surface(prepared)
    backend = CachedStillDecodeBackend(delegate, store=store)
    failures: list[BaseException] = []

    def decode() -> None:
        try:
            backend.decode(request, _Token())
        except DecodeCancelledError as exc:
            failures.append(exc)

    worker = Thread(target=decode)
    worker.start()
    assert started.wait(5)

    backend.shutdown(timeout_ms=1)

    assert not closed.is_set()
    release.set()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert closed.wait(5)
    assert len(failures) == 1
    assert isinstance(failures[0], DecodeCancelledError)


def test_rebind_rejects_old_decode_without_repopulating_memory_cache(
    tmp_path: Path,
) -> None:
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    request = _request(old_root / "photo.jpg")
    started = Event()
    release = Event()
    old_store = Mock()
    old_store.library_root = old_root.absolute()

    def slow_load(_request: DetailRenderRequest) -> DecodedSurface:
        started.set()
        assert release.wait(5)
        return _surface(request)

    old_store.load.side_effect = slow_load
    new_store = Mock()
    new_store.library_root = new_root.absolute()
    backend = CachedStillDecodeBackend(
        Mock(),
        store=old_store,
        store_factory=Mock(return_value=new_store),
    )
    failures: list[BaseException] = []

    def decode() -> None:
        try:
            backend.decode(request, _Token())
        except DecodeCancelledError as exc:
            failures.append(exc)

    worker = Thread(target=decode)
    worker.start()
    assert started.wait(5)
    backend.bind_library(new_root)
    release.set()
    worker.join(timeout=5)
    backend.shutdown(timeout_ms=5000)

    assert not worker.is_alive()
    assert len(failures) == 1
    assert isinstance(failures[0], DecodeCancelledError)
    assert backend.memory_cache.used_bytes == 0
    old_store.close.assert_called_once_with()
    new_store.close.assert_called_once_with()


def test_rebind_retires_generation_before_shared_memory_cache_clear(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    request = _request(old_root / "photo.jpg")
    old_store = Mock()
    old_store.library_root = old_root.absolute()
    old_store.load.return_value = _surface(request)
    new_store = Mock()
    new_store.library_root = new_root.absolute()
    memory_cache = MappedSurfaceCache()
    backend = CachedStillDecodeBackend(
        Mock(),
        memory_cache=memory_cache,
        store=old_store,
        store_factory=Mock(return_value=new_store),
    )
    old_generation = backend._active_store_generation
    original_clear = memory_cache.clear
    interleaved_result: list[str] = []
    armed = True

    def clear_with_old_decode_completion() -> None:
        nonlocal armed
        original_clear()
        if not armed:
            return
        armed = False
        try:
            backend._decode_for_generation(request, _Token(), old_generation)
        except DecodeCancelledError:
            interleaved_result.append("stale")
        else:
            interleaved_result.append("accepted")

    monkeypatch.setattr(memory_cache, "clear", clear_with_old_decode_completion)

    backend.bind_library(new_root)

    assert interleaved_result == ["stale"]
    assert backend.memory_cache.used_bytes == 0
    backend.shutdown(timeout_ms=5000)
    old_store.close.assert_called_once_with()
    new_store.close.assert_called_once_with()


def test_closed_store_and_index_cannot_reopen(tmp_path: Path) -> None:
    request = _request(tmp_path / "photo.jpg")
    store = NeutralSurfaceStore(tmp_path)
    assert store.write(request, _surface(request))
    index = store._index_for_root()
    root = store.root
    assert index is not None and root is not None

    store.close()

    assert store.closed
    assert index.closed
    assert not index.ensure_open()
    assert store.load(request) is None
    assert not store.write(request, _surface(request))
    with pytest.raises(RuntimeError, match="create a new store generation"):
        store.bind_library(tmp_path / "other")
    assert store.closed
    assert store.root == root
    assert index.closed


def test_clean_library_bind_does_not_traverse_payload_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path / "photo.jpg")
    populated = NeutralSurfaceStore(tmp_path)
    assert populated.write(request, _surface(request))
    populated.close()
    backend = CachedStillDecodeBackend(Mock(), store=NeutralSurfaceStore(None))

    def fail_glob(_self: Path, _pattern: str):
        raise AssertionError("clean library bind must not scan cache payloads")

    monkeypatch.setattr(Path, "glob", fail_glob)
    backend.bind_library(tmp_path)
    backend.shutdown()


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

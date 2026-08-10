"""Versioned neutral-surface caches for the Detail still-image pipeline."""

from __future__ import annotations

import hashlib
import json
import mmap
import os
import shutil
import struct
import tempfile
import time
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field, replace
from pathlib import Path
from threading import Event, RLock
from typing import Final

from PySide6.QtGui import QColorSpace, QImage

from iPhoto.core.color_resolver import ColorStats, compute_color_statistics
from iPhoto.gui.detail_decode_backend import (
    CancellationToken,
    DecodeCancelledError,
    DecodedSurface,
    StillDecodeBackend,
)
from iPhoto.gui.detail_pipeline import DetailDecodeKey, DetailRenderRequest
from iPhoto.gui.detail_profile import emit_detail_event
from iPhoto.gui.detail_surface_cache_index import (
    SurfaceCacheIndex,
    SurfaceCacheIndexEntry,
)
from iPhoto.gui.detail_surface_residency import (
    SurfaceResidencyTracker,
    surface_resource_id,
)
from iPhoto.infrastructure.services.thumbnail_runtime_policy import (
    resolve_physical_memory_bytes,
)

try:
    import xxhash
except ImportError:  # pragma: no cover - declared production dependency
    xxhash = None  # type: ignore[assignment]

_MIB: Final = 1024 * 1024
_GIB: Final = 1024 * _MIB
_MAGIC: Final = b"IPHSURF\0"
_SCHEMA: Final = 3
# Bump this when decoder semantics can change the pixels for an otherwise
# identical source identity.  This is intentionally separate from _SCHEMA:
# the on-disk container remains readable, but surfaces produced by the old
# decoder contract must not bypass the corrected decode path.
# LIVE PHOTO REGRESSION GUARD: do not remove this key component or lower it
# below 2.  Contract 1 may contain Windows HEIC pixels that WIC had already
# oriented and the old pipeline rotated a second time.
_DECODE_SEMANTICS_CONTRACT: Final = 2
_HEADER_SIZE: Final = 4096
_WRITE_CHUNK_BYTES: Final = 4 * _MIB
_ACCESS_AUDIT_INTERVAL: Final = 128
_AUDIT_MAX_AGE_NS: Final = 30 * 24 * 60 * 60 * 1_000_000_000
_MAINTENANCE_WRITE_BYTES: Final = 256 * _MIB
_MAINTENANCE_INTERVAL_NS: Final = 10 * 60 * 1_000_000_000
_PRUNE_BATCH: Final = 128
_PREFIX = struct.Struct("<8sIIIQQ")


class SurfaceCacheCorruptError(RuntimeError):
    """Raised when a disk cache entry cannot be trusted."""


class MappedSurfaceOwner:
    """Keep the file, mmap and exported payload view alive with a QImage."""

    __slots__ = ("file", "mapping", "payload")

    def __init__(self, file, mapping: mmap.mmap, payload: memoryview) -> None:
        self.file = file
        self.mapping = mapping
        self.payload = payload


def surface_memory_budget_bytes(physical_memory_bytes: int | None = None) -> int:
    physical = int(physical_memory_bytes or resolve_physical_memory_bytes())
    return max(128 * _MIB, min(512 * _MIB, int(max(0, physical) * 0.02)))


def _surface_bytes(surface: DecodedSurface) -> int:
    image = surface.image
    return max(0, int(image.bytesPerLine()) * int(image.height()))


class MappedSurfaceCache:
    """Thread-safe byte-budgeted LRU for heap and mmap-backed surfaces."""

    _OWNER_ID: Final = "detail-memory-cache"

    def __init__(
        self,
        budget_bytes: int | None = None,
        *,
        residency_tracker: SurfaceResidencyTracker | None = None,
    ) -> None:
        self._budget_bytes = max(1, int(budget_bytes or surface_memory_budget_bytes()))
        self._entries: OrderedDict[
            DetailDecodeKey,
            tuple[DecodedSurface, int, object],
        ] = OrderedDict()
        self._used_bytes = 0
        self._lock = RLock()
        self._residency_tracker = residency_tracker

    @property
    def budget_bytes(self) -> int:
        return self._budget_bytes

    @property
    def used_bytes(self) -> int:
        with self._lock:
            return self._used_bytes

    def get(self, key: DetailDecodeKey) -> DecodedSurface | None:
        if key.source_revision[0] == "legacy":
            return None
        with self._lock:
            value = self._entries.pop(key, None)
            if value is None:
                return None
            self._entries[key] = value
            surface, _size, _resource_id = value
            return replace(surface, cache_tier="memory")

    def put(self, surface: DecodedSurface) -> bool:
        size = _surface_bytes(surface)
        if (
            surface.decode_key.source_revision[0] == "legacy"
            or surface.image.isNull()
            or size <= 0
            or size > self._budget_bytes
        ):
            return False
        with self._lock:
            previous = self._entries.pop(surface.decode_key, None)
            if previous is not None:
                self._used_bytes -= previous[1]
                if self._residency_tracker is not None:
                    self._residency_tracker.release(self._OWNER_ID, previous[2])
            resource_id = surface_resource_id(surface)
            self._entries[surface.decode_key] = (surface, size, resource_id)
            self._used_bytes += size
            if self._residency_tracker is not None:
                self._residency_tracker.retain_surface(
                    self._OWNER_ID,
                    "memory_cache",
                    surface,
                )
            while self._used_bytes > self._budget_bytes and self._entries:
                _key, (_old, old_size, old_resource_id) = self._entries.popitem(last=False)
                self._used_bytes -= old_size
                if self._residency_tracker is not None:
                    self._residency_tracker.release(self._OWNER_ID, old_resource_id)
        return True

    def invalidate_asset(self, asset_id: str) -> None:
        with self._lock:
            for key in tuple(self._entries):
                if key.asset_id != asset_id:
                    continue
                _surface, size, resource_id = self._entries.pop(key)
                self._used_bytes -= size
                if self._residency_tracker is not None:
                    self._residency_tracker.release(self._OWNER_ID, resource_id)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._used_bytes = 0
            if self._residency_tracker is not None:
                self._residency_tracker.release(self._OWNER_ID)


def _canonical_key(request: DetailRenderRequest) -> bytes:
    key = DetailDecodeKey.from_request(request)
    payload = {
        "asset_id": key.asset_id,
        "source": str(key.source),
        "source_revision": list(key.source_revision),
        "decode_level": key.decode_level,
        "orientation": request.source_identity.orientation,
        "pixel_format": "rgba8888",
        "color_space": "srgb",
        "contract": _DECODE_SEMANTICS_CONTRACT,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _key_digest(request: DetailRenderRequest) -> str:
    return hashlib.sha256(_canonical_key(request)).hexdigest()


def _checksum(payload: memoryview | bytes) -> int:
    if xxhash is not None:
        return int(xxhash.xxh64(payload).intdigest())
    return int.from_bytes(hashlib.blake2b(payload, digest_size=8).digest(), "little")


class NeutralSurfaceStore:
    """Library-scoped, versioned, mmap-readable disk surface store."""

    def __init__(
        self,
        library_root: Path | None = None,
        *,
        budget_bytes: int | None = None,
    ) -> None:
        self._root: Path | None = None
        self._index: SurfaceCacheIndex | None = None
        self._closed = False
        self._budget_override = (
            max(0, int(budget_bytes)) if budget_bytes is not None else None
        )
        self._lock = RLock()
        self._maintenance_lock = RLock()
        self._disk_hit_count = 0
        self._pending_audits: set[str] = set()
        self._legacy_cleanup_done = False
        self.bind_library(library_root)

    @property
    def root(self) -> Path | None:
        with self._lock:
            return self._root

    @property
    def library_root(self) -> Path | None:
        root = self.root
        if root is None:
            return None
        try:
            return root.parents[3]
        except IndexError:
            return None

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def bind_library(self, library_root: Path | None) -> None:
        root = None
        if library_root is not None:
            root = (
                Path(library_root).expanduser().absolute()
                / ".iPhoto"
                / "cache"
                / "detail-surfaces"
                / "v3"
            )
        with self._lock:
            previous = self._index
            self._index = None
            self._root = root
            self._closed = False
            self._disk_hit_count = 0
            self._pending_audits.clear()
            self._legacy_cleanup_done = False
        if previous is not None:
            previous.close(clean=True)

    def _index_for_root(self) -> SurfaceCacheIndex | None:
        root = self.root
        if root is None:
            return None
        with self._lock:
            if self._closed:
                return None
            index = self._index
            if index is None or index.root != root:
                index = SurfaceCacheIndex(root)
                self._index = index
            return index

    def entry_path(self, request: DetailRenderRequest) -> Path | None:
        if not request.source_identity.has_stable_revision:
            return None
        with self._lock:
            if self._closed:
                return None
            root = self._root
            if root is None:
                return None
        try:
            library_root = root.parents[3]
            request.source_identity.path.relative_to(library_root)
        except (IndexError, ValueError):
            # A request that outlived a library rebind must never populate the
            # newly active library's cache namespace.
            return None
        digest = _key_digest(request)
        return root / digest[:2] / f"{digest}.ipsurface"

    def load(self, request: DetailRenderRequest) -> DecodedSurface | None:
        path = self.entry_path(request)
        if path is None:
            return None
        index = self._index_for_root()
        if index is None or not index.ensure_open():
            return None
        if index.needs_recovery:
            self.maintenance(recover=True)
        digest = _key_digest(request)
        entry = index.get(digest)
        if entry is None:
            return None
        if (
            entry.container_schema != _SCHEMA
            or entry.decoder_contract != _DECODE_SEMANTICS_CONTRACT
            or Path(entry.relative_path).as_posix() != path.relative_to(index.root).as_posix()
        ):
            raise SurfaceCacheCorruptError("surface cache index contract mismatch")
        try:
            file = path.open("rb")
        except FileNotFoundError:
            index.remove(digest)
            return None
        except OSError as exc:
            raise SurfaceCacheCorruptError(str(exc)) from exc

        mapping: mmap.mmap | None = None
        payload: memoryview | None = None
        try:
            mapping = mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ)
            if len(mapping) < _HEADER_SIZE:
                raise SurfaceCacheCorruptError("surface cache entry is truncated")
            (
                magic,
                schema,
                header_size,
                metadata_size,
                payload_size,
                checksum,
            ) = _PREFIX.unpack_from(mapping)
            if magic != _MAGIC or schema != _SCHEMA or header_size != _HEADER_SIZE:
                raise SurfaceCacheCorruptError("surface cache header/version mismatch")
            if metadata_size <= 0 or _PREFIX.size + metadata_size > _HEADER_SIZE:
                raise SurfaceCacheCorruptError("surface cache metadata size is invalid")
            if payload_size <= 0 or _HEADER_SIZE + payload_size != len(mapping):
                raise SurfaceCacheCorruptError("surface cache payload size is invalid")
            metadata = json.loads(bytes(mapping[_PREFIX.size:_PREFIX.size + metadata_size]))
            if metadata.get("key_digest") != digest:
                raise SurfaceCacheCorruptError("surface cache key mismatch")
            if int(metadata.get("decoder_contract", -1)) != _DECODE_SEMANTICS_CONTRACT:
                raise SurfaceCacheCorruptError("surface cache decoder contract mismatch")
            width = int(metadata["width"])
            height = int(metadata["height"])
            stride = int(metadata["stride"])
            if width <= 0 or height <= 0 or stride < width * 4 or stride * height != payload_size:
                raise SurfaceCacheCorruptError("surface cache geometry is invalid")
            stat = path.stat()
            if (
                int(stat.st_size) != entry.file_bytes
                or int(stat.st_mtime_ns) != entry.file_mtime_ns
                or int(payload_size) != entry.payload_bytes
                or int(checksum) != entry.checksum
            ):
                raise SurfaceCacheCorruptError("surface cache file metadata mismatch")
            payload = memoryview(mapping)[_HEADER_SIZE:_HEADER_SIZE + payload_size]
            last_verified_ns = entry.last_verified_ns
            if entry.checksum_state != "trusted":
                if _checksum(payload) != checksum:
                    raise SurfaceCacheCorruptError("surface cache checksum mismatch")
                last_verified_ns = time.time_ns()
                index.mark_checksum_state(
                    digest,
                    "trusted",
                    verified_ns=last_verified_ns,
                )
            now = time.time_ns()
            flush_due = index.queue_access(digest, accessed_ns=now)
            with self._lock:
                self._disk_hit_count += 1
                if (
                    self._disk_hit_count % _ACCESS_AUDIT_INTERVAL == 0
                    or now - last_verified_ns >= _AUDIT_MAX_AGE_NS
                ):
                    self._pending_audits.add(digest)
            if flush_due:
                # The cache backend schedules the actual transaction on its
                # I/O lane. Direct store users may call flush_accesses_if_due().
                emit_detail_event(
                    "surface_cache_access_flush_due",
                    generation=request.generation,
                    asset_id=request.asset_id,
                )
            owner = MappedSurfaceOwner(file, mapping, payload)
            image = QImage(payload, width, height, stride, QImage.Format.Format_RGBA8888)
            if image.isNull():
                raise SurfaceCacheCorruptError("surface cache QImage mapping failed")
            image.setColorSpace(QColorSpace(QColorSpace.NamedColorSpace.SRgb))
            source_size = tuple(int(v) for v in metadata.get("source_size", (width, height)))
            color_stats = ColorStats.ensure(metadata.get("color_stats"))
            return DecodedSurface(
                image=image,
                decode_key=DetailDecodeKey.from_request(request),
                source_size=(source_size[0], source_size[1]),
                decoded_size=(width, height),
                decode_level=request.with_decode_level().decode_level or "full",
                backend=str(metadata.get("backend") or "cache"),
                color_stats=color_stats,
                fallback=metadata.get("fallback") or None,
                cache_tier="disk",
                backing_owner=owner,
            )
        except Exception as exc:
            if payload is not None:
                try:
                    payload.release()
                except (BufferError, ValueError):
                    pass
            try:
                if mapping is not None:
                    mapping.close()
            except (BufferError, OSError):
                pass
            file.close()
            if isinstance(exc, SurfaceCacheCorruptError):
                raise
            raise SurfaceCacheCorruptError(str(exc)) from exc

    def write(self, request: DetailRenderRequest, surface: DecodedSurface) -> bool:
        path = self.entry_path(request)
        if path is None or surface.image.isNull():
            return False
        image = surface.image
        if image.format() != QImage.Format.Format_RGBA8888:
            image = image.convertToFormat(QImage.Format.Format_RGBA8888)
        metadata = json.dumps(
            {
                "key_digest": _key_digest(request),
                "container_schema": _SCHEMA,
                "decoder_contract": _DECODE_SEMANTICS_CONTRACT,
                "width": image.width(),
                "height": image.height(),
                "stride": image.bytesPerLine(),
                "source_size": list(surface.source_size),
                "backend": surface.backend,
                "fallback": surface.fallback,
                "color_stats": {
                    "saturation_mean": surface.color_stats.saturation_mean,
                    "saturation_median": surface.color_stats.saturation_median,
                    "highlight_ratio": surface.color_stats.highlight_ratio,
                    "dark_ratio": surface.color_stats.dark_ratio,
                    "skin_ratio": surface.color_stats.skin_ratio,
                    "cast_magnitude": surface.color_stats.cast_magnitude,
                    "white_balance_gain_r": surface.color_stats.white_balance_gain[0],
                    "white_balance_gain_g": surface.color_stats.white_balance_gain[1],
                    "white_balance_gain_b": surface.color_stats.white_balance_gain[2],
                },
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if _PREFIX.size + len(metadata) > _HEADER_SIZE:
            return False
        payload_size = int(image.sizeInBytes())
        if payload_size <= 0:
            return False
        index = self._index_for_root()
        if index is None or not index.ensure_open():
            return False
        digest = _key_digest(request)
        temporary: Path | None = None
        replaced = False
        metadata_committed = False
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = memoryview(image.constBits())[:payload_size]
            hasher = xxhash.xxh64() if xxhash is not None else hashlib.blake2b(digest_size=8)
            with tempfile.NamedTemporaryFile(
                mode="w+b",
                prefix=f".{path.name}.{os.getpid()}.",
                suffix=".tmp",
                dir=path.parent,
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
                stream.write(b"\0" * _HEADER_SIZE)
                for offset in range(0, payload_size, _WRITE_CHUNK_BYTES):
                    chunk = payload[offset:min(payload_size, offset + _WRITE_CHUNK_BYTES)]
                    hasher.update(chunk)
                    stream.write(chunk)
                checksum = (
                    int(hasher.intdigest())
                    if xxhash is not None
                    else int.from_bytes(hasher.digest(), "little")
                )
                header = bytearray(_HEADER_SIZE)
                _PREFIX.pack_into(
                    header,
                    0,
                    _MAGIC,
                    _SCHEMA,
                    _HEADER_SIZE,
                    len(metadata),
                    payload_size,
                    checksum,
                )
                header[_PREFIX.size:_PREFIX.size + len(metadata)] = metadata
                stream.seek(0)
                stream.write(header)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            replaced = True
            temporary = None
            stat = path.stat()
            now = time.time_ns()
            indexed = index.upsert(
                SurfaceCacheIndexEntry(
                    digest=digest,
                    relative_path=path.relative_to(index.root).as_posix(),
                    container_schema=_SCHEMA,
                    decoder_contract=_DECODE_SEMANTICS_CONTRACT,
                    payload_bytes=payload_size,
                    file_bytes=int(stat.st_size),
                    checksum=checksum,
                    checksum_state="trusted",
                    file_mtime_ns=int(stat.st_mtime_ns),
                    created_ns=now,
                    last_access_ns=now,
                    last_verified_ns=now,
                )
            )
            if not indexed:
                self._cleanup_failed_write(index, digest, path)
                return False
            metadata_committed = True
            budget = self._budget_bytes()
            if budget is not None and index.maintenance_due(
                budget,
                byte_interval=_MAINTENANCE_WRITE_BYTES,
                time_interval_ns=_MAINTENANCE_INTERVAL_NS,
            ):
                self.maintenance()
            self._cleanup_legacy_cache()
            return True
        except (BufferError, OSError, ValueError):
            if replaced and not metadata_committed:
                self._cleanup_failed_write(index, digest, path)
            elif temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    index.mark_recovery_required()
            return False

    @staticmethod
    def _cleanup_failed_write(
        index: SurfaceCacheIndex,
        digest: str,
        path: Path,
    ) -> None:
        payload_removed = True
        try:
            path.unlink(missing_ok=True)
        except OSError:
            payload_removed = False
        row_removed = index.remove(digest)
        if not payload_removed or not row_removed:
            index.mark_recovery_required()

    def discard(self, request: DetailRenderRequest) -> None:
        path = self.entry_path(request)
        if path is None:
            return
        self._discard_digest(_key_digest(request), path)

    def prune(self) -> None:
        """Compatibility entry point for an explicitly forced indexed prune."""

        self.maintenance(force_prune=True)

    def flush_accesses_if_due(self, *, force: bool = False) -> bool:
        index = self._index_for_root()
        return bool(index is not None and index.flush_accesses(force=force))

    def run_pending_audits(self) -> None:
        with self._lock:
            digests = tuple(self._pending_audits)
            self._pending_audits.clear()
        index = self._index_for_root()
        if index is None:
            return
        for digest in digests:
            entry = index.get(digest)
            if entry is None:
                continue
            path = self._indexed_path(index, entry.relative_path)
            if path is None:
                index.remove(digest)
                continue
            if self._entry_checksum_is_valid(entry, path):
                index.mark_checksum_state(digest, "trusted", verified_ns=time.time_ns())
                continue
            self._discard_digest(digest, path)
            emit_detail_event(
                "surface_cache_audit_failed",
                generation=0,
                digest_prefix=digest[:12],
            )

    def maintenance(
        self,
        *,
        recover: bool = False,
        force_prune: bool = False,
    ) -> None:
        """Recover if needed, flush access metadata, and prune from the SQL LRU."""

        with self._maintenance_lock:
            index = self._index_for_root()
            if index is None or not index.ensure_open():
                return
            if recover or index.needs_recovery:
                self._recover_index(index)
                if index.needs_recovery:
                    return
            index.flush_accesses(force=True)
            budget = self._budget_bytes()
            if budget is None:
                return
            due = force_prune or index.maintenance_due(
                budget,
                byte_interval=_MAINTENANCE_WRITE_BYTES,
                time_interval_ns=_MAINTENANCE_INTERVAL_NS,
            )
            if not due:
                return
            target = int(budget * 0.9)
            maintenance_failed = False
            while index.indexed_bytes > target:
                victims = index.lru_victims(limit=_PRUNE_BATCH)
                if not victims:
                    maintenance_failed = True
                    break
                removed = 0
                for victim in victims:
                    if index.indexed_bytes <= target:
                        break
                    path = self._indexed_path(index, victim.relative_path)
                    if path is None:
                        if not index.remove(victim.digest):
                            index.mark_recovery_required()
                            maintenance_failed = True
                            break
                        removed += 1
                        continue
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        index.mark_recovery_required()
                        maintenance_failed = True
                        continue
                    if not index.remove(victim.digest):
                        index.mark_recovery_required()
                        maintenance_failed = True
                        break
                    removed += 1
                if maintenance_failed:
                    break
                if removed == 0:
                    maintenance_failed = True
                    break
            if not maintenance_failed:
                index.finish_maintenance()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            index = self._index
            self._index = None
            self._pending_audits.clear()
        if index is not None:
            index.close(clean=True)

    def _budget_bytes(self) -> int | None:
        if self._budget_override is not None:
            return self._budget_override
        root = self.root
        if root is None:
            return None
        try:
            probe = root if root.exists() else root.parents[3]
            return min(2 * _GIB, max(0, int(shutil.disk_usage(probe).free * 0.02)))
        except (IndexError, OSError):
            return None

    def _recover_index(self, index: SurfaceCacheIndex) -> None:
        indexed = {entry.digest: entry for entry in index.all_entries()}
        seen: set[str] = set()
        recovered = True
        for temporary in index.root.glob("*/*.tmp"):
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                recovered = False
        for path in index.root.glob("*/*.ipsurface"):
            mapping: mmap.mmap | None = None
            file = None
            try:
                file = path.open("rb")
                mapping = mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ)
                metadata, payload_size, checksum = self._read_header(mapping)
                digest = str(metadata["key_digest"])
                if path.name != f"{digest}.ipsurface":
                    raise SurfaceCacheCorruptError("recovered cache filename mismatch")
                stat = path.stat()
                now = time.time_ns()
                previous = indexed.get(digest)
                seen.add(digest)
                if not index.upsert(
                    SurfaceCacheIndexEntry(
                        digest=digest,
                        relative_path=path.relative_to(index.root).as_posix(),
                        container_schema=int(metadata.get("container_schema", _SCHEMA)),
                        decoder_contract=int(metadata.get("decoder_contract", -1)),
                        payload_bytes=payload_size,
                        file_bytes=int(stat.st_size),
                        checksum=checksum,
                        checksum_state="untrusted",
                        file_mtime_ns=int(stat.st_mtime_ns),
                        created_ns=previous.created_ns if previous is not None else now,
                        last_access_ns=previous.last_access_ns if previous is not None else now,
                        last_verified_ns=0,
                    )
                ):
                    recovered = False
            except (KeyError, OSError, SurfaceCacheCorruptError, ValueError):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    recovered = False
            finally:
                try:
                    if mapping is not None:
                        mapping.close()
                except (BufferError, OSError):
                    pass
                if file is not None:
                    file.close()
        for digest in indexed:
            if digest not in seen:
                recovered = index.remove(digest) and recovered
        index.recalculate_indexed_bytes()
        if recovered:
            index.mark_recovered()
            index.finish_maintenance()
        else:
            index.mark_recovery_required()

    @staticmethod
    def _read_header(mapping: mmap.mmap) -> tuple[dict, int, int]:
        if len(mapping) < _HEADER_SIZE:
            raise SurfaceCacheCorruptError("surface cache entry is truncated")
        magic, schema, header_size, metadata_size, payload_size, checksum = _PREFIX.unpack_from(
            mapping
        )
        if magic != _MAGIC or schema != _SCHEMA or header_size != _HEADER_SIZE:
            raise SurfaceCacheCorruptError("surface cache header/version mismatch")
        if metadata_size <= 0 or _PREFIX.size + metadata_size > _HEADER_SIZE:
            raise SurfaceCacheCorruptError("surface cache metadata size is invalid")
        if payload_size <= 0 or _HEADER_SIZE + payload_size != len(mapping):
            raise SurfaceCacheCorruptError("surface cache payload size is invalid")
        try:
            metadata = json.loads(bytes(mapping[_PREFIX.size:_PREFIX.size + metadata_size]))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise SurfaceCacheCorruptError("surface cache metadata is invalid") from exc
        return metadata, int(payload_size), int(checksum)

    def _entry_checksum_is_valid(
        self,
        entry: SurfaceCacheIndexEntry,
        path: Path,
    ) -> bool:
        file = None
        mapping: mmap.mmap | None = None
        payload: memoryview | None = None
        try:
            file = path.open("rb")
            mapping = mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ)
            metadata, payload_size, checksum = self._read_header(mapping)
            if (
                str(metadata.get("key_digest")) != entry.digest
                or payload_size != entry.payload_bytes
                or checksum != entry.checksum
            ):
                return False
            payload = memoryview(mapping)[_HEADER_SIZE:_HEADER_SIZE + payload_size]
            return _checksum(payload) == checksum
        except (OSError, SurfaceCacheCorruptError, ValueError):
            return False
        finally:
            if payload is not None:
                try:
                    payload.release()
                except (BufferError, ValueError):
                    pass
            if mapping is not None:
                try:
                    mapping.close()
                except (BufferError, OSError):
                    pass
            if file is not None:
                file.close()

    def _discard_digest(self, digest: str, path: Path) -> None:
        index = self._index_for_root()
        try:
            path.unlink(missing_ok=True)
        except OSError:
            if index is not None:
                index.mark_recovery_required()
            return
        if index is not None and not index.remove(digest):
            index.mark_recovery_required()

    @staticmethod
    def _indexed_path(index: SurfaceCacheIndex, relative_path: str) -> Path | None:
        try:
            root = index.root.resolve()
            candidate = (root / str(relative_path)).resolve()
            candidate.relative_to(root)
        except (OSError, ValueError):
            return None
        return candidate

    def _cleanup_legacy_cache(self) -> None:
        with self._lock:
            if self._legacy_cleanup_done:
                return
            self._legacy_cleanup_done = True
        root = self.root
        if root is None:
            return
        legacy = root.parent / "v2"
        if legacy.name != "v2" or legacy.parent != root.parent:
            return
        try:
            shutil.rmtree(legacy)
        except FileNotFoundError:
            pass
        except OSError:
            with self._lock:
                self._legacy_cleanup_done = False


@dataclass(slots=True)
class _SurfaceStoreGeneration:
    epoch: int
    library_root: Path | None
    store: NeutralSurfaceStore
    retired: bool = False
    close_submitted: bool = False
    active_calls: int = 0
    drained: Event = field(default_factory=Event)

    def __post_init__(self) -> None:
        self.drained.set()


def _normalise_library_root(library_root: Path | None) -> Path | None:
    if library_root is None:
        return None
    return Path(library_root).expanduser().absolute()


class CachedStillDecodeBackend:
    """Memory/disk/decode lookup chain with asynchronous persistence."""

    def __init__(
        self,
        delegate: StillDecodeBackend,
        *,
        memory_cache: MappedSurfaceCache | None = None,
        store: NeutralSurfaceStore | None = None,
        store_factory: Callable[[Path | None], NeutralSurfaceStore] | None = None,
        residency_tracker: SurfaceResidencyTracker | None = None,
    ) -> None:
        self._delegate = delegate
        self.residency_tracker = residency_tracker or SurfaceResidencyTracker()
        self.memory_cache = memory_cache or MappedSurfaceCache(
            residency_tracker=self.residency_tracker
        )
        self._store_factory = store_factory or NeutralSurfaceStore
        initial_store = store or self._store_factory(None)
        initial_root = getattr(initial_store, "library_root", None)
        if not isinstance(initial_root, Path):
            initial_root = None
        self._store_epoch = 0
        self._active_store_generation = _SurfaceStoreGeneration(
            epoch=self._store_epoch,
            library_root=initial_root,
            store=initial_store,
        )
        self._io = ThreadPoolExecutor(max_workers=1, thread_name_prefix="iPhoto-surface-cache")
        self._futures: set[Future] = set()
        self._lock = RLock()
        self._shutting_down = False
        self._shutdown_barrier: Future | None = None
        self._executor_shutdown = False
        self._color_stats_by_source: OrderedDict[tuple, ColorStats] = OrderedDict()

    @property
    def store(self) -> NeutralSurfaceStore:
        with self._lock:
            return self._active_store_generation.store

    def bind_library(self, library_root: Path | None) -> None:
        normalised_root = _normalise_library_root(library_root)
        with self._lock:
            if self._shutting_down:
                return
            current = self._active_store_generation
            if current.library_root == normalised_root:
                self._color_stats_by_source.clear()
                self.memory_cache.clear()
                self._submit_generation_locked(current, current.store.maintenance)
                return
            replacement = self._store_factory(normalised_root)
            self._store_epoch += 1
            generation = _SurfaceStoreGeneration(
                epoch=self._store_epoch,
                library_root=normalised_root,
                store=replacement,
            )
            current.retired = True
            self._active_store_generation = generation
            self._color_stats_by_source.clear()
            self.memory_cache.clear()
            self._queue_store_close_locked(current)
            self._submit_generation_locked(generation, replacement.maintenance)

    @staticmethod
    def _source_stats_key(request: DetailRenderRequest) -> tuple:
        identity = request.source_identity
        return (identity.path, identity.revision, identity.orientation)

    def _remember_color_stats(
        self,
        request: DetailRenderRequest,
        stats: ColorStats,
    ) -> ColorStats:
        key = self._source_stats_key(request)
        with self._lock:
            existing = self._color_stats_by_source.pop(key, None)
            resolved = existing or stats
            self._color_stats_by_source[key] = resolved
            while len(self._color_stats_by_source) > 16:
                self._color_stats_by_source.popitem(last=False)
            return resolved

    def _cached_color_stats(self, request: DetailRenderRequest) -> ColorStats | None:
        key = self._source_stats_key(request)
        with self._lock:
            stats = self._color_stats_by_source.pop(key, None)
            if stats is not None:
                self._color_stats_by_source[key] = stats
            return stats

    def decode(
        self,
        request: DetailRenderRequest,
        cancellation: CancellationToken,
    ) -> DecodedSurface:
        generation = self._acquire_store_generation(cancellation)
        try:
            return self._decode_for_generation(request, cancellation, generation)
        finally:
            self._release_store_generation(generation)

    def _decode_for_generation(
        self,
        request: DetailRenderRequest,
        cancellation: CancellationToken,
        generation: _SurfaceStoreGeneration,
    ) -> DecodedSurface:
        prepared = request.with_decode_level()
        key = DetailDecodeKey.from_request(prepared)
        cacheable = prepared.source_identity.has_stable_revision
        if cancellation.is_cancelled():
            raise DecodeCancelledError("Still-image decode cancelled")
        surface = self.memory_cache.get(key) if cacheable else None
        if surface is not None:
            with self._lock:
                self._raise_if_generation_stale_locked(generation, cancellation)
                self._remember_color_stats(prepared, surface.color_stats)
            emit_detail_event(
                "surface_cache_hit",
                generation=prepared.generation,
                asset_id=key.asset_id,
                tier="memory",
            )
            return surface
        if cacheable:
            try:
                surface = generation.store.load(prepared)
            except SurfaceCacheCorruptError:
                emit_detail_event(
                    "surface_cache_corrupt",
                    generation=prepared.generation,
                    asset_id=key.asset_id,
                )
                self._submit_for_generation(
                    generation,
                    generation.store.discard,
                    prepared,
                )
                surface = None
        if surface is not None:
            with self._lock:
                self._raise_if_generation_stale_locked(generation, cancellation)
                self.memory_cache.put(surface)
                self._remember_color_stats(prepared, surface.color_stats)
                self._submit_generation_locked(
                    generation,
                    generation.store.flush_accesses_if_due,
                )
                self._submit_generation_locked(
                    generation,
                    generation.store.run_pending_audits,
                )
            emit_detail_event(
                "surface_cache_hit",
                generation=prepared.generation,
                asset_id=key.asset_id,
                tier="disk",
            )
            return surface
        with self._lock:
            self._raise_if_generation_stale_locked(generation, cancellation)
        emit_detail_event(
            "surface_cache_miss",
            generation=prepared.generation,
            asset_id=key.asset_id,
        )
        surface = self._delegate.decode(prepared, cancellation)
        with self._lock:
            self._raise_if_generation_stale_locked(generation, cancellation)
            stats = self._cached_color_stats(prepared) if cacheable else None
        if stats is None:
            started = time.perf_counter()
            stats = compute_color_statistics(surface.image)
            emit_detail_event(
                "color_stats",
                generation=prepared.generation,
                asset_id=prepared.asset_id,
                duration_ms=(time.perf_counter() - started) * 1000.0,
                width=surface.decoded_size[0],
                height=surface.decoded_size[1],
            )
        surface = replace(surface, color_stats=stats)
        with self._lock:
            self._raise_if_generation_stale_locked(generation, cancellation)
            if cacheable:
                stats = self._remember_color_stats(prepared, stats)
                surface = replace(surface, color_stats=stats)
                self.memory_cache.put(surface)
                self._submit_generation_locked(
                    generation,
                    self._write_surface,
                    generation,
                    prepared,
                    surface,
                )
        return surface

    def _acquire_store_generation(
        self,
        cancellation: CancellationToken,
    ) -> _SurfaceStoreGeneration:
        with self._lock:
            generation = self._active_store_generation
            self._raise_if_generation_stale_locked(generation, cancellation)
            generation.active_calls += 1
            generation.drained.clear()
            return generation

    def _release_store_generation(
        self,
        generation: _SurfaceStoreGeneration,
    ) -> None:
        with self._lock:
            generation.active_calls = max(0, generation.active_calls - 1)
            if generation.active_calls == 0:
                generation.drained.set()

    def _write_surface(
        self,
        generation: _SurfaceStoreGeneration,
        request: DetailRenderRequest,
        surface: DecodedSurface,
    ) -> None:
        if generation.store.write(request, surface) and self._generation_is_current(
            generation
        ):
            emit_detail_event(
                "surface_cache_write",
                generation=request.generation,
                asset_id=surface.decode_key.asset_id,
                width=surface.decoded_size[0],
                height=surface.decoded_size[1],
            )

    def _raise_if_generation_stale_locked(
        self,
        generation: _SurfaceStoreGeneration,
        cancellation: CancellationToken,
    ) -> None:
        if (
            cancellation.is_cancelled()
            or self._shutting_down
            or generation.retired
            or generation is not self._active_store_generation
        ):
            raise DecodeCancelledError("Still-image decode generation was retired")

    def _generation_is_current(self, generation: _SurfaceStoreGeneration) -> bool:
        with self._lock:
            return bool(
                not self._shutting_down
                and not generation.retired
                and generation is self._active_store_generation
            )

    def _submit_for_generation(
        self,
        generation: _SurfaceStoreGeneration,
        fn,
        *args,
        **kwargs,
    ) -> Future | None:
        with self._lock:
            return self._submit_generation_locked(generation, fn, *args, **kwargs)

    def _submit_generation_locked(
        self,
        generation: _SurfaceStoreGeneration,
        fn,
        *args,
        **kwargs,
    ) -> Future | None:
        if (
            self._shutting_down
            or generation.retired
            or generation is not self._active_store_generation
        ):
            return None
        return self._submit_raw_locked(
            self._run_generation_task,
            generation,
            fn,
            args,
            kwargs,
        )

    def _run_generation_task(
        self,
        generation: _SurfaceStoreGeneration,
        fn,
        args: tuple,
        kwargs: dict,
    ):
        # Submission is the ownership boundary. Once accepted, work remains
        # ordered ahead of that generation's close barrier even if a rebind or
        # shutdown retires the generation before the worker reaches it.
        _ = generation
        return fn(*args, **kwargs)

    def _queue_store_close_locked(
        self,
        generation: _SurfaceStoreGeneration,
    ) -> Future | None:
        if generation.close_submitted:
            return None
        generation.close_submitted = True
        return self._submit_raw_locked(self._close_generation_store, generation)

    @staticmethod
    def _close_generation_store(generation: _SurfaceStoreGeneration) -> None:
        generation.drained.wait()
        generation.store.close()

    def _submit_raw_locked(self, fn, *args, **kwargs) -> Future | None:
        try:
            future = self._io.submit(fn, *args, **kwargs)
        except RuntimeError:
            return None
        self._futures.add(future)
        future.add_done_callback(self._forget_future)
        return future

    def _forget_future(self, future: Future) -> None:
        with self._lock:
            self._futures.discard(future)

    def shutdown(self, *, timeout_ms: int = 1000) -> None:
        with self._lock:
            if not self._shutting_down:
                self._shutting_down = True
                generation = self._active_store_generation
                generation.retired = True
                self._queue_store_close_locked(generation)
                self._shutdown_barrier = self._submit_raw_locked(lambda: None)
            barrier = self._shutdown_barrier
        completed = not bool(barrier)
        if barrier is not None:
            done, _pending = wait(
                (barrier,),
                timeout=max(0, int(timeout_ms)) / 1000.0,
            )
            completed = barrier in done
        if not completed:
            with self._lock:
                pending_count = len(self._futures)
            emit_detail_event(
                "surface_cache_shutdown_timeout",
                generation=0,
                pending=pending_count,
            )
        self.memory_cache.clear()
        with self._lock:
            if self._executor_shutdown:
                return
            self._executor_shutdown = True
        self._io.shutdown(wait=False, cancel_futures=False)


__all__ = [
    "CachedStillDecodeBackend",
    "MappedSurfaceCache",
    "MappedSurfaceOwner",
    "NeutralSurfaceStore",
    "SurfaceCacheCorruptError",
    "surface_memory_budget_bytes",
]

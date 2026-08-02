"""Versioned neutral-surface caches for the Detail still-image pipeline."""

from __future__ import annotations

import hashlib
import json
import mmap
import os
import shutil
import struct
import time
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import replace
from pathlib import Path
from threading import RLock
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
_SCHEMA: Final = 2
_HEADER_SIZE: Final = 4096
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

    def __init__(self, budget_bytes: int | None = None) -> None:
        self._budget_bytes = max(1, int(budget_bytes or surface_memory_budget_bytes()))
        self._entries: OrderedDict[DetailDecodeKey, tuple[DecodedSurface, int]] = OrderedDict()
        self._used_bytes = 0
        self._lock = RLock()

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
            surface, _size = value
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
            self._entries[surface.decode_key] = (surface, size)
            self._used_bytes += size
            while self._used_bytes > self._budget_bytes and self._entries:
                _key, (_old, old_size) = self._entries.popitem(last=False)
                self._used_bytes -= old_size
        return True

    def discard(self, key: DetailDecodeKey) -> None:
        with self._lock:
            previous = self._entries.pop(key, None)
            if previous is not None:
                self._used_bytes -= previous[1]

    def invalidate_asset(self, asset_id: str) -> None:
        with self._lock:
            for key in tuple(self._entries):
                if key.asset_id != asset_id:
                    continue
                _surface, size = self._entries.pop(key)
                self._used_bytes -= size

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._used_bytes = 0


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
        "contract": 1,
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
        prune_every_writes: int = 32,
        prune_after_bytes: int = 256 * _MIB,
        prune_interval_seconds: float = 60.0,
    ) -> None:
        self._root: Path | None = None
        self._lock = RLock()
        self._prune_every_writes = max(1, int(prune_every_writes))
        self._prune_after_bytes = max(1, int(prune_after_bytes))
        self._prune_interval_seconds = max(0.0, float(prune_interval_seconds))
        self._writes_since_prune = 0
        self._bytes_since_prune = 0
        self._last_prune_at = time.monotonic()
        self._invalid_paths: set[Path] = set()
        self.bind_library(library_root)

    @property
    def root(self) -> Path | None:
        with self._lock:
            return self._root

    def bind_library(self, library_root: Path | None) -> None:
        root = None
        if library_root is not None:
            root = Path(library_root).expanduser().absolute() / ".iPhoto" / "cache" / "detail-surfaces" / "v2"
        with self._lock:
            self._root = root
            self._writes_since_prune = 0
            self._bytes_since_prune = 0
            self._last_prune_at = time.monotonic()
            self._invalid_paths.clear()

    def entry_path(self, request: DetailRenderRequest) -> Path | None:
        root = self.root
        if root is None or not request.source_identity.has_stable_revision:
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
        with self._lock:
            if path in self._invalid_paths:
                return None
        try:
            file = path.open("rb")
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise SurfaceCacheCorruptError(str(exc)) from exc

        try:
            mapping = mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ)
            if len(mapping) < _HEADER_SIZE:
                raise SurfaceCacheCorruptError("surface cache entry is truncated")
            magic, schema, header_size, metadata_size, payload_size, _checksum_value = _PREFIX.unpack_from(mapping)
            if magic != _MAGIC or schema != _SCHEMA or header_size != _HEADER_SIZE:
                raise SurfaceCacheCorruptError("surface cache header/version mismatch")
            if metadata_size <= 0 or _PREFIX.size + metadata_size > _HEADER_SIZE:
                raise SurfaceCacheCorruptError("surface cache metadata size is invalid")
            if payload_size <= 0 or _HEADER_SIZE + payload_size != len(mapping):
                raise SurfaceCacheCorruptError("surface cache payload size is invalid")
            metadata = json.loads(bytes(mapping[_PREFIX.size:_PREFIX.size + metadata_size]))
            if metadata.get("key_digest") != _key_digest(request):
                raise SurfaceCacheCorruptError("surface cache key mismatch")
            width = int(metadata["width"])
            height = int(metadata["height"])
            stride = int(metadata["stride"])
            if width <= 0 or height <= 0 or stride < width * 4 or stride * height != payload_size:
                raise SurfaceCacheCorruptError("surface cache geometry is invalid")
            payload = memoryview(mapping)[_HEADER_SIZE:_HEADER_SIZE + payload_size]
            # Payload validation is deliberately asynchronous.  Header, key,
            # and geometry checks are enough to construct the mapped surface;
            # a background verifier evicts a corrupt payload before reuse.
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
                color_stats_computed=bool(metadata.get("color_stats_computed", False)),
                fallback=metadata.get("fallback") or None,
                cache_tier="disk",
                backing_owner=owner,
            )
        except Exception as exc:
            try:
                mapping.close()  # type: ignore[possibly-undefined]
            except (BufferError, NameError, OSError):
                pass
            file.close()
            if isinstance(exc, SurfaceCacheCorruptError):
                raise
            raise SurfaceCacheCorruptError(str(exc)) from exc

    def validate(self, request: DetailRenderRequest) -> bool:
        """Validate a payload away from the disk-hit delivery path."""

        path = self.entry_path(request)
        if path is None:
            return False
        try:
            with path.open("rb") as file:
                mapping = mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ)
                payload = None
                try:
                    if len(mapping) < _HEADER_SIZE:
                        raise SurfaceCacheCorruptError("surface cache entry is truncated")
                    (
                        magic,
                        schema,
                        header_size,
                        _metadata_size,
                        payload_size,
                        checksum,
                    ) = _PREFIX.unpack_from(mapping)
                    if magic != _MAGIC or schema != _SCHEMA or header_size != _HEADER_SIZE:
                        raise SurfaceCacheCorruptError("surface cache header/version mismatch")
                    if payload_size <= 0 or _HEADER_SIZE + payload_size != len(mapping):
                        raise SurfaceCacheCorruptError("surface cache payload size is invalid")
                    payload = memoryview(mapping)[_HEADER_SIZE:_HEADER_SIZE + payload_size]
                    if _checksum(payload) != checksum:
                        raise SurfaceCacheCorruptError("surface cache checksum mismatch")
                    return True
                finally:
                    if payload is not None:
                        payload.release()
                    mapping.close()
        except SurfaceCacheCorruptError:
            raise
        except (FileNotFoundError, OSError) as exc:
            raise SurfaceCacheCorruptError(str(exc)) from exc

    def touch(self, request: DetailRenderRequest) -> None:
        """Refresh disk-LRU metadata away from the foreground mmap path."""

        path = self.entry_path(request)
        if path is None:
            return
        try:
            os.utime(path, None)
        except OSError:
            return

    def write(self, request: DetailRenderRequest, surface: DecodedSurface) -> bool:
        path = self.entry_path(request)
        if path is None or surface.image.isNull():
            return False
        image = surface.image
        if image.format() != QImage.Format.Format_RGBA8888:
            image = image.convertToFormat(QImage.Format.Format_RGBA8888)
        payload = memoryview(image.constBits())[: image.sizeInBytes()]
        metadata = json.dumps(
            {
                "key_digest": _key_digest(request),
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
                "color_stats_computed": bool(surface.color_stats_computed),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if _PREFIX.size + len(metadata) > _HEADER_SIZE:
            return False
        header = bytearray(_HEADER_SIZE)
        _PREFIX.pack_into(
            header,
            0,
            _MAGIC,
            _SCHEMA,
            _HEADER_SIZE,
            len(metadata),
            len(payload),
            _checksum(payload),
        )
        header[_PREFIX.size:_PREFIX.size + len(metadata)] = metadata
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            with temporary.open("wb") as stream:
                stream.write(header)
                stream.write(payload)
            os.replace(temporary, path)
            with self._lock:
                self._invalid_paths.discard(path)
            self._maybe_prune(len(payload))
            return True
        except OSError:
            try:
                temporary.unlink(missing_ok=True)  # type: ignore[possibly-undefined]
            except (NameError, OSError):
                pass
            return False

    def _maybe_prune(self, written_bytes: int) -> None:
        now = time.monotonic()
        with self._lock:
            self._writes_since_prune += 1
            self._bytes_since_prune += max(0, int(written_bytes))
            due = (
                self._writes_since_prune >= self._prune_every_writes
                or self._bytes_since_prune >= self._prune_after_bytes
                or now - self._last_prune_at >= self._prune_interval_seconds
            )
            if not due:
                return
            self._writes_since_prune = 0
            self._bytes_since_prune = 0
            self._last_prune_at = now
        self.prune()

    def discard(self, request: DetailRenderRequest) -> None:
        path = self.entry_path(request)
        if path is not None:
            with self._lock:
                self._invalid_paths.add(path)
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def prune(self) -> None:
        root = self.root
        if root is None or not root.exists():
            return
        try:
            budget = min(2 * _GIB, max(0, int(shutil.disk_usage(root).free * 0.02)))
            entries = []
            total = 0
            for path in root.glob("*/*.ipsurface"):
                try:
                    stat = path.stat()
                except OSError:
                    continue
                total += int(stat.st_size)
                entries.append((int(stat.st_mtime_ns), path, int(stat.st_size)))
            for _mtime, path, size in sorted(entries):
                if total <= budget:
                    break
                try:
                    path.unlink()
                    total -= size
                except OSError:
                    continue
        except OSError:
            return


class CachedStillDecodeBackend:
    """Memory/disk/decode lookup chain with asynchronous persistence."""

    def __init__(
        self,
        delegate: StillDecodeBackend,
        *,
        memory_cache: MappedSurfaceCache | None = None,
        store: NeutralSurfaceStore | None = None,
        defer_persistence_until_presented: bool = False,
    ) -> None:
        self._delegate = delegate
        self.memory_cache = memory_cache or MappedSurfaceCache()
        self.store = store or NeutralSurfaceStore()
        self._io = ThreadPoolExecutor(max_workers=1, thread_name_prefix="iPhoto-surface-cache")
        self._futures: set[Future] = set()
        self._lock = RLock()
        self._shutting_down = False
        self._defer_persistence_until_presented = bool(
            defer_persistence_until_presented
        )
        self._pending_writes: OrderedDict[
            DetailDecodeKey,
            tuple[DetailRenderRequest, DecodedSurface],
        ] = OrderedDict()
        self._persisted_surface_keys: set[DetailDecodeKey] = set()
        self._color_stats_by_source: OrderedDict[tuple, ColorStats] = OrderedDict()

    def bind_library(self, library_root: Path | None) -> None:
        self.memory_cache.clear()
        with self._lock:
            self._color_stats_by_source.clear()
            self._pending_writes.clear()
        self.store.bind_library(library_root)

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

    def _ensure_color_stats(
        self,
        request: DetailRenderRequest,
        surface: DecodedSurface,
    ) -> DecodedSurface:
        """Resolve statistics only for sidecar-backed adjustment requests."""

        if surface.color_stats_computed:
            self._remember_color_stats(request, surface.color_stats)
            return surface
        cached = self._cached_color_stats(request)
        if cached is not None:
            return replace(
                surface,
                color_stats=cached,
                color_stats_computed=True,
            )
        if not request.raw_adjustments:
            return surface
        started = time.perf_counter()
        stats = self._remember_color_stats(
            request,
            compute_color_statistics(surface.image),
        )
        emit_detail_event(
            "color_stats",
            generation=request.generation,
            asset_id=request.asset_id,
            duration_ms=(time.perf_counter() - started) * 1000.0,
            width=surface.decoded_size[0],
            height=surface.decoded_size[1],
        )
        return replace(
            surface,
            color_stats=stats,
            color_stats_computed=True,
        )

    def decode(self, request: DetailRenderRequest, cancellation: CancellationToken) -> DecodedSurface:
        prepared = request.with_decode_level()
        repaired_identity = prepared.source_identity.repair_revision_from_stat()
        if repaired_identity != prepared.source_identity:
            prepared = replace(prepared, source_identity=repaired_identity)
        key = DetailDecodeKey.from_request(prepared)
        if cancellation.is_cancelled():
            raise DecodeCancelledError("Still-image decode cancelled")
        if not prepared.source_identity.has_stable_revision:
            emit_detail_event(
                "surface_cache_bypass",
                generation=prepared.generation,
                asset_id=key.asset_id,
                reason="unstable_source_revision",
            )
            surface = self._delegate.decode(prepared, cancellation)
            return self._ensure_color_stats(prepared, surface)
        surface = self.memory_cache.get(key)
        if surface is not None:
            surface = self._ensure_color_stats(prepared, surface)
            self.memory_cache.put(surface)
            emit_detail_event("surface_cache_hit", generation=prepared.generation, asset_id=key.asset_id, tier="memory")
            return surface
        try:
            surface = self.store.load(prepared)
        except SurfaceCacheCorruptError:
            emit_detail_event("surface_cache_corrupt", generation=prepared.generation, asset_id=key.asset_id)
            with self._lock:
                self._persisted_surface_keys.discard(key)
            self._submit(self.store.discard, prepared)
            surface = None
        if surface is not None:
            if cancellation.is_cancelled():
                raise DecodeCancelledError("Still-image decode cancelled")
            surface = self._ensure_color_stats(prepared, surface)
            self.memory_cache.put(surface)
            with self._lock:
                self._persisted_surface_keys.add(key)
            self._submit(self._validate_disk_surface, prepared, key)
            self._submit(self.store.touch, prepared)
            emit_detail_event("surface_cache_hit", generation=prepared.generation, asset_id=key.asset_id, tier="disk")
            return surface
        with self._lock:
            # The store may have been pruned or explicitly cleared since this
            # process last observed the key.  Do not let benchmark/readiness
            # introspection treat that historical observation as durable.
            self._persisted_surface_keys.discard(key)
        emit_detail_event("surface_cache_miss", generation=prepared.generation, asset_id=key.asset_id)
        surface = self._delegate.decode(prepared, cancellation)
        surface = self._ensure_color_stats(prepared, surface)
        self.memory_cache.put(surface)
        if self._defer_persistence_until_presented:
            with self._lock:
                self._pending_writes.pop(surface.decode_key, None)
                self._pending_writes[surface.decode_key] = (prepared, surface)
                while len(self._pending_writes) > 16:
                    self._pending_writes.popitem(last=False)
        else:
            self._submit(self._write_surface, prepared, surface)
        return surface

    def persist_surface(self, key: DetailDecodeKey) -> bool:
        """Persist a decoded surface only after its first GPU use is safe."""

        with self._lock:
            pending = self._pending_writes.pop(key, None)
            shutting_down = self._shutting_down
        if pending is None or shutting_down:
            return False
        self._submit(self._write_surface, *pending)
        return True

    def has_persisted_surface(self, key: DetailDecodeKey) -> bool:
        """Return whether this backend has observed *key* safely on disk."""

        with self._lock:
            return key in self._persisted_surface_keys

    def _validate_disk_surface(
        self,
        request: DetailRenderRequest,
        key: DetailDecodeKey,
    ) -> None:
        try:
            self.store.validate(request)
        except SurfaceCacheCorruptError:
            self.memory_cache.discard(key)
            self.store.discard(request)
            emit_detail_event(
                "surface_cache_corrupt",
                generation=request.generation,
                asset_id=key.asset_id,
                phase="async_validation",
            )

    def _write_surface(self, request: DetailRenderRequest, surface: DecodedSurface) -> None:
        written = self.store.write(request, surface)
        if written:
            with self._lock:
                self._persisted_surface_keys.add(surface.decode_key)
        else:
            with self._lock:
                self._persisted_surface_keys.discard(surface.decode_key)
        if written and not self._shutting_down:
            emit_detail_event(
                "surface_cache_write",
                generation=request.generation,
                asset_id=surface.decode_key.asset_id,
                width=surface.decoded_size[0],
                height=surface.decoded_size[1],
            )

    def _submit(self, fn, *args) -> None:
        with self._lock:
            if self._shutting_down:
                return
            try:
                future = self._io.submit(fn, *args)
            except RuntimeError:
                return
            self._futures.add(future)
            future.add_done_callback(self._forget_future)

    def _forget_future(self, future: Future) -> None:
        with self._lock:
            self._futures.discard(future)

    def shutdown(self, *, timeout_ms: int = 1000) -> None:
        with self._lock:
            self._shutting_down = True
            self._pending_writes.clear()
            futures = tuple(self._futures)
        if futures:
            wait(futures, timeout=max(0, int(timeout_ms)) / 1000.0)
        self._io.shutdown(wait=False, cancel_futures=True)


__all__ = [
    "CachedStillDecodeBackend",
    "MappedSurfaceCache",
    "MappedSurfaceOwner",
    "NeutralSurfaceStore",
    "SurfaceCacheCorruptError",
    "surface_memory_budget_bytes",
]

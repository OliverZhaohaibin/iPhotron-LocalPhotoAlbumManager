"""Diagnostic-only ownership accounting for Detail still-image resources.

The tracker deliberately does not own, pin, evict, or otherwise influence a
resource.  It is the observation surface used before the later SurfaceLease
budget migration so that the current retention graph can be measured without
changing its behaviour.
"""

from __future__ import annotations

from collections.abc import Hashable
from dataclasses import dataclass
from threading import RLock
from typing import TYPE_CHECKING

from iPhoto.gui.detail_profile import emit_detail_event

if TYPE_CHECKING:
    from iPhoto.gui.detail_decode_backend import DecodedSurface


@dataclass(frozen=True, slots=True)
class SurfaceByteBreakdown:
    """Bytes retained by one unique surface resource."""

    cpu_heap: int = 0
    mmap: int = 0
    upload_staging: int = 0
    gpu_estimated: int = 0
    raw_intermediate: int = 0

    def __post_init__(self) -> None:
        for name in (
            "cpu_heap",
            "mmap",
            "upload_staging",
            "gpu_estimated",
            "raw_intermediate",
        ):
            object.__setattr__(self, name, max(0, int(getattr(self, name))))

    @property
    def total(self) -> int:
        return (
            self.cpu_heap
            + self.mmap
            + self.upload_staging
            + self.gpu_estimated
            + self.raw_intermediate
        )

    def __add__(self, other: SurfaceByteBreakdown) -> SurfaceByteBreakdown:
        if not isinstance(other, SurfaceByteBreakdown):
            return NotImplemented
        return SurfaceByteBreakdown(
            cpu_heap=self.cpu_heap + other.cpu_heap,
            mmap=self.mmap + other.mmap,
            upload_staging=self.upload_staging + other.upload_staging,
            gpu_estimated=self.gpu_estimated + other.gpu_estimated,
            raw_intermediate=self.raw_intermediate + other.raw_intermediate,
        )


@dataclass(frozen=True, slots=True)
class SurfaceResidencySnapshot:
    """One immutable diagnostic view of unique resources and their owners."""

    resource_count: int
    owner_count: int
    reference_count: int
    unique_bytes: SurfaceByteBreakdown
    bytes_by_owner_kind: tuple[tuple[str, int], ...]


def surface_resource_id(surface: DecodedSurface, *, stage: str = "surface") -> Hashable:
    """Return a process-local identity that distinguishes shared QImage storage."""

    try:
        image_key = int(surface.image.cacheKey())
    except (AttributeError, RuntimeError, TypeError, ValueError):
        image_key = id(surface.image)
    return (str(stage), surface.decode_key, image_key)


def surface_bytes(surface: DecodedSurface) -> SurfaceByteBreakdown:
    """Classify one decoded image as heap or mmap-backed resident storage."""

    image = surface.image
    size = max(0, int(image.bytesPerLine()) * int(image.height()))
    if surface.backing_owner is not None:
        return SurfaceByteBreakdown(mmap=size)
    return SurfaceByteBreakdown(cpu_heap=size)


class SurfaceResidencyTracker:
    """Thread-safe diagnostic registry; never applies allocation policy."""

    def __init__(self) -> None:
        self._resources: dict[Hashable, SurfaceByteBreakdown] = {}
        self._resource_owners: dict[Hashable, set[str]] = {}
        self._owner_resources: dict[str, set[Hashable]] = {}
        self._owner_kinds: dict[str, str] = {}
        self._lock = RLock()

    def retain(
        self,
        owner_id: str,
        owner_kind: str,
        resource_id: Hashable,
        byte_breakdown: SurfaceByteBreakdown,
        *,
        generation: int = 0,
    ) -> None:
        """Observe one owner retaining one resource, idempotently."""

        owner = str(owner_id)
        kind = str(owner_kind)
        with self._lock:
            self._resources[resource_id] = byte_breakdown
            self._resource_owners.setdefault(resource_id, set()).add(owner)
            self._owner_resources.setdefault(owner, set()).add(resource_id)
            self._owner_kinds[owner] = kind
            snapshot = self._snapshot_locked()
        self._emit_snapshot("surface_owner_retain", generation, kind, snapshot)

    def release(
        self,
        owner_id: str,
        resource_id: Hashable | None = None,
        *,
        generation: int = 0,
    ) -> None:
        """Observe one resource release, or every resource held by an owner."""

        owner = str(owner_id)
        with self._lock:
            resources = self._owner_resources.get(owner)
            if not resources:
                return
            targets = tuple(resources) if resource_id is None else (resource_id,)
            for target in targets:
                if target not in resources:
                    continue
                resources.discard(target)
                owners = self._resource_owners.get(target)
                if owners is None:
                    continue
                owners.discard(owner)
                if not owners:
                    self._resource_owners.pop(target, None)
                    self._resources.pop(target, None)
            if not resources:
                self._owner_resources.pop(owner, None)
                kind = self._owner_kinds.pop(owner, "unknown")
            else:
                kind = self._owner_kinds.get(owner, "unknown")
            snapshot = self._snapshot_locked()
        self._emit_snapshot("surface_owner_release", generation, kind, snapshot)

    def retain_surface(
        self,
        owner_id: str,
        owner_kind: str,
        surface: DecodedSurface,
        *,
        generation: int = 0,
    ) -> Hashable:
        resource_id = surface_resource_id(surface)
        self.retain(
            owner_id,
            owner_kind,
            resource_id,
            surface_bytes(surface),
            generation=generation,
        )
        return resource_id

    def snapshot(self) -> SurfaceResidencySnapshot:
        with self._lock:
            return self._snapshot_locked()

    def clear(self, *, generation: int = 0) -> None:
        with self._lock:
            self._resources.clear()
            self._resource_owners.clear()
            self._owner_resources.clear()
            self._owner_kinds.clear()
            snapshot = self._snapshot_locked()
        self._emit_snapshot("surface_owner_clear", generation, "all", snapshot)

    def _snapshot_locked(self) -> SurfaceResidencySnapshot:
        total = SurfaceByteBreakdown()
        for byte_breakdown in self._resources.values():
            total += byte_breakdown
        by_kind: dict[str, int] = {}
        for owner, resources in self._owner_resources.items():
            kind = self._owner_kinds.get(owner, "unknown")
            by_kind[kind] = by_kind.get(kind, 0) + sum(
                self._resources[resource].total
                for resource in resources
                if resource in self._resources
            )
        return SurfaceResidencySnapshot(
            resource_count=len(self._resources),
            owner_count=len(self._owner_resources),
            reference_count=sum(len(resources) for resources in self._owner_resources.values()),
            unique_bytes=total,
            bytes_by_owner_kind=tuple(sorted(by_kind.items())),
        )

    @staticmethod
    def _emit_snapshot(
        stage: str,
        generation: int,
        owner_kind: str,
        snapshot: SurfaceResidencySnapshot,
    ) -> None:
        emit_detail_event(
            stage,
            generation=generation,
            owner_kind=owner_kind,
            resources=snapshot.resource_count,
            owners=snapshot.owner_count,
            references=snapshot.reference_count,
            cpu_heap_bytes=snapshot.unique_bytes.cpu_heap,
            mmap_bytes=snapshot.unique_bytes.mmap,
            upload_staging_bytes=snapshot.unique_bytes.upload_staging,
            gpu_estimated_bytes=snapshot.unique_bytes.gpu_estimated,
            raw_intermediate_bytes=snapshot.unique_bytes.raw_intermediate,
        )


__all__ = [
    "SurfaceByteBreakdown",
    "SurfaceResidencySnapshot",
    "SurfaceResidencyTracker",
    "surface_bytes",
    "surface_resource_id",
]

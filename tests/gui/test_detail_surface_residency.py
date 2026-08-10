from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QImage

from iPhoto.core.color_resolver import ColorStats
from iPhoto.gui.detail_decode_backend import DecodedSurface
from iPhoto.gui.detail_pipeline import (
    AssetSourceIdentity,
    DetailDecodeKey,
    DetailGeometryState,
    DetailRenderRequest,
)
from iPhoto.gui.detail_render_session import EditRenderState, PhotoRenderSessionHandle
from iPhoto.gui.detail_surface_cache import MappedSurfaceCache
from iPhoto.gui.detail_surface_residency import (
    SurfaceByteBreakdown,
    SurfaceResidencyTracker,
    surface_resource_id,
)


def _surface(path: Path, *, level: int = 1024) -> tuple[AssetSourceIdentity, DecodedSurface]:
    identity = AssetSourceIdentity.create(
        path,
        size_bytes=123,
        source_mtime_ns=456,
        index_revision=7,
        width=1600,
        height=1200,
    )
    request = DetailRenderRequest(
        generation=1,
        asset_id="asset-1",
        source_identity=identity,
        viewport_physical_size=(800, 600),
        device_pixel_ratio=1.0,
        geometry=DetailGeometryState(),
        reason="initial",
        decode_level=level,
    )
    image = QImage(8, 6, QImage.Format.Format_RGBA8888)
    image.fill(0xFF123456)
    return identity, DecodedSurface(
        image=image,
        decode_key=DetailDecodeKey.from_request(request),
        source_size=(1600, 1200),
        decoded_size=(8, 6),
        decode_level=level,
        backend="fake",
        color_stats=ColorStats(),
    )


def test_tracker_counts_shared_resource_once_across_multiple_owners(tmp_path: Path) -> None:
    _identity, surface = _surface(tmp_path / "photo.jpg")
    tracker = SurfaceResidencyTracker()
    resource_id = surface_resource_id(surface)

    tracker.retain_surface("cache", "memory_cache", surface)
    tracker.retain_surface("session", "render_session", surface)

    snapshot = tracker.snapshot()
    assert snapshot.resource_count == 1
    assert snapshot.owner_count == 2
    assert snapshot.reference_count == 2
    assert snapshot.unique_bytes.cpu_heap == surface.image.sizeInBytes()
    assert dict(snapshot.bytes_by_owner_kind) == {
        "memory_cache": surface.image.sizeInBytes(),
        "render_session": surface.image.sizeInBytes(),
    }

    tracker.release("cache", resource_id)
    assert tracker.snapshot().unique_bytes.cpu_heap == surface.image.sizeInBytes()
    tracker.release("session")
    assert tracker.snapshot().unique_bytes.total == 0


def test_tracker_separates_host_staging_gpu_and_raw_bytes() -> None:
    tracker = SurfaceResidencyTracker()
    tracker.retain(
        "owner",
        "diagnostic",
        "resource",
        SurfaceByteBreakdown(
            cpu_heap=1,
            mmap=2,
            upload_staging=3,
            gpu_estimated=4,
            raw_intermediate=5,
        ),
    )

    snapshot = tracker.snapshot()
    assert snapshot.unique_bytes.total == 15
    assert snapshot.unique_bytes == SurfaceByteBreakdown(1, 2, 3, 4, 5)


def test_render_session_releases_all_observed_lods(tmp_path: Path) -> None:
    identity, first = _surface(tmp_path / "photo.jpg", level=1024)
    _identity, second = _surface(tmp_path / "photo.jpg", level=2048)
    tracker = SurfaceResidencyTracker()
    state = EditRenderState.create(
        {},
        color_stats=first.color_stats,
        revision=("index", identity.index_revision),
    )
    session = PhotoRenderSessionHandle(
        session_id=9,
        asset_id="asset-1",
        source_identity=identity,
        current_surface=first,
        edit_state=state,
        baseline_state=state,
        residency_tracker=tracker,
    )

    session.retain_surface(second)
    assert tracker.snapshot().resource_count == 2

    session.release_residency_observations()
    assert tracker.snapshot().owner_count == 0
    assert tracker.snapshot().unique_bytes.total == 0


def test_memory_cache_observes_eviction_and_clear(tmp_path: Path) -> None:
    _identity, first = _surface(tmp_path / "first.jpg")
    _identity, second = _surface(tmp_path / "second.jpg")
    tracker = SurfaceResidencyTracker()
    cache = MappedSurfaceCache(
        budget_bytes=first.image.sizeInBytes(),
        residency_tracker=tracker,
    )

    assert cache.put(first)
    assert cache.put(second)
    snapshot = tracker.snapshot()
    assert snapshot.resource_count == 1
    assert snapshot.unique_bytes.cpu_heap == second.image.sizeInBytes()

    cache.clear()
    assert tracker.snapshot().unique_bytes.total == 0

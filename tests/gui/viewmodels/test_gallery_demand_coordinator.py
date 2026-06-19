from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize

from iPhoto.application.dtos import AssetDTO
from iPhoto.domain.models.query import AssetQuery
from iPhoto.gui.gallery_demand import build_viewport_demand
from iPhoto.gui.viewmodels.gallery_demand_coordinator import GalleryDemandCoordinator
from iPhoto.gui.viewmodels.gallery_thumbnail_hint_loader import (
    GalleryThumbnailCandidate,
    GalleryThumbnailHintResult,
)


def _dto(row: int) -> AssetDTO:
    path = Path("/library") / f"{row}.jpg"
    return AssetDTO(
        id=str(row),
        abs_path=path,
        rel_path=Path(f"{row}.jpg"),
        media_type="photo",
        created_at=None,
        width=512,
        height=512,
        duration=0.0,
        size_bytes=1,
        metadata={},
        is_favorite=False,
        thumb_cache_key=f"l2-{row}",
    )


def test_old_generation_hint_is_reused_when_it_still_overlaps_guard() -> None:
    coordinator = GalleryDemandCoordinator()
    query = AssetQuery()
    first = build_viewport_demand(
        generation=1,
        row_count=1_000,
        visible_first=100,
        visible_last=109,
        direction=1,
        screens_per_second=1.0,
        actively_scrolling=True,
    )
    current = build_viewport_demand(
        generation=2,
        row_count=1_000,
        visible_first=101,
        visible_last=110,
        direction=1,
        screens_per_second=1.0,
        actively_scrolling=True,
    )
    coordinator.update_viewport(
        current,
        root=Path("/library"),
        query=query,
        collection_revision=7,
    )
    overlapping_row = next(iter(current.iter_full_guard_rows()))
    result = GalleryThumbnailHintResult(
        request_id=1,
        generation=first.generation,
        collection_revision=7,
        root=Path("/library"),
        query=query,
        first=first.full_prefetch_first,
        limit=first.full_prefetch_last - first.full_prefetch_first + 1,
        candidates=(
            GalleryThumbnailCandidate(
                overlapping_row,
                Path("/library") / f"{overlapping_row}.jpg",
                f"l2-{overlapping_row}",
                0,
                "guard",
            ),
        ),
        elapsed_ms=20.0,
    )

    assert coordinator.merge_hint_result(result) == 1
    snapshot = coordinator.build_thumbnail_snapshot(
        visible_rows=[],
        prefetched_rows={},
        size=QSize(512, 512),
    )
    assert snapshot is not None
    assert Path("/library") / f"{overlapping_row}.jpg" in snapshot.guard_paths


def test_hint_from_old_collection_revision_is_rejected() -> None:
    coordinator = GalleryDemandCoordinator()
    query = AssetQuery()
    viewport = build_viewport_demand(
        generation=100,
        row_count=1_000,
        visible_first=300,
        visible_last=309,
        direction=1,
        screens_per_second=1.0,
        actively_scrolling=True,
    )
    coordinator.update_viewport(
        viewport,
        root=Path("/library"),
        query=query,
        collection_revision=9,
    )
    row = next(iter(viewport.iter_full_guard_rows()))
    result = GalleryThumbnailHintResult(
        request_id=90,
        generation=90,
        collection_revision=8,
        root=Path("/library"),
        query=query,
        first=row,
        limit=1,
        candidates=(
            GalleryThumbnailCandidate(
                row,
                Path("/library") / f"{row}.jpg",
                f"l2-{row}",
                0,
                "guard",
            ),
        ),
        elapsed_ms=1.0,
    )

    assert coordinator.merge_hint_result(result) == 0
    assert coordinator.hint_candidates_by_row == {}


def test_snapshot_orders_all_guard_paths_before_speculation() -> None:
    coordinator = GalleryDemandCoordinator()
    viewport = build_viewport_demand(
        generation=3,
        row_count=1_000,
        visible_first=100,
        visible_last=102,
        direction=1,
        screens_per_second=1.0,
        actively_scrolling=True,
    )
    coordinator.update_viewport(
        viewport,
        root=Path("/library"),
        query=AssetQuery(),
        collection_revision=1,
    )
    rows = {
        row: _dto(row)
        for row in (
            *viewport.iter_full_guard_rows(),
            *viewport.iter_full_speculative_rows(),
        )
    }

    snapshot = coordinator.build_thumbnail_snapshot(
        visible_rows=[(100, _dto(100))],
        prefetched_rows=rows,
        size=QSize(512, 512),
    )

    assert snapshot is not None
    assert snapshot.guard_paths == tuple(
        rows[row].abs_path for row in viewport.iter_full_guard_rows()
    )
    assert snapshot.speculative_paths == tuple(
        rows[row].abs_path for row in viewport.iter_full_speculative_rows()
    )


def test_revision_changes_only_for_scheduling_or_collection_changes() -> None:
    coordinator = GalleryDemandCoordinator()
    query = AssetQuery()
    viewport = build_viewport_demand(
        generation=4,
        row_count=1_000,
        visible_first=100,
        visible_last=109,
        direction=1,
        screens_per_second=1.0,
        actively_scrolling=True,
    )

    coordinator.update_viewport(
        viewport,
        root=Path("/library"),
        query=query,
        collection_revision=3,
    )
    assert coordinator.revision == 4

    coordinator.update_viewport(
        viewport,
        root=Path("/library"),
        query=query,
        collection_revision=3,
    )
    assert coordinator.revision == 4

    coordinator.update_viewport(
        viewport,
        root=Path("/library"),
        query=query,
        collection_revision=4,
    )
    assert coordinator.revision == 5
    assert coordinator.viewport is not None
    assert coordinator.viewport.generation == 5

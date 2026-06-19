"""Single source of truth for Gallery viewport and thumbnail demand."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable, Mapping

from PySide6.QtCore import QSize

from iPhoto.application.dtos import AssetDTO
from iPhoto.gui.gallery_demand import GalleryViewportDemand
from iPhoto.infrastructure.services.thumbnail_cache_service import (
    ThumbnailDemandSnapshot,
    ThumbnailPrefetchCandidate,
)

from .gallery_thumbnail_hint_loader import (
    GalleryThumbnailCandidate,
    GalleryThumbnailHintResult,
)


class GalleryDemandCoordinator:
    """Merge viewport, row snapshots, and reusable hint results into one demand."""

    def __init__(self) -> None:
        self.viewport: GalleryViewportDemand | None = None
        self.root: Path | None = None
        self.query: Any | None = None
        self.collection_revision = 0
        self.revision = 0
        self._scheduling_identity: tuple[object, ...] | None = None
        self.hint_candidates_by_row: dict[int, GalleryThumbnailCandidate] = {}

    def reset(self) -> None:
        self.viewport = None
        self.root = None
        self.query = None
        self.collection_revision = 0
        self.revision = 0
        self._scheduling_identity = None
        self.hint_candidates_by_row.clear()

    def update_viewport(
        self,
        viewport: GalleryViewportDemand,
        *,
        root: Path | None,
        query: Any | None,
        collection_revision: int,
    ) -> None:
        root = Path(root) if root is not None else None
        selection_changed = (
            (self.root is not None or self.query is not None)
            and (root != self.root or query != self.query)
        )
        revision_changed = (
            collection_revision > 0
            and self.collection_revision > 0
            and collection_revision != self.collection_revision
        )
        if selection_changed or revision_changed:
            self.hint_candidates_by_row.clear()
        scheduling_identity = (
            viewport.scheduling_identity,
            root,
            query,
            int(collection_revision),
        )
        if scheduling_identity != self._scheduling_identity:
            self.revision = max(self.revision + 1, int(viewport.generation))
            self._scheduling_identity = scheduling_identity
        effective_viewport = (
            viewport
            if viewport.generation == self.revision
            else replace(viewport, generation=self.revision)
        )
        self.viewport = effective_viewport
        self.root = root
        self.query = query
        self.collection_revision = int(collection_revision)
        self.prune_hints()

    def prune_hints(self) -> None:
        if self.viewport is None or not self.hint_candidates_by_row:
            return
        first, last = self.viewport.full_prefetch_range
        self.hint_candidates_by_row = {
            row: candidate
            for row, candidate in self.hint_candidates_by_row.items()
            if first <= row <= last
        }

    def merge_hint_result(self, result: GalleryThumbnailHintResult) -> int:
        """Accept old-generation work when it still covers the current demand."""

        viewport = self.viewport
        if (
            viewport is None
            or result.error is not None
            or self.root is None
            or Path(result.root) != self.root
            or result.query != self.query
            or (
                result.collection_revision > 0
                and self.collection_revision > 0
                and result.collection_revision != self.collection_revision
            )
        ):
            return 0
        first, last = viewport.full_prefetch_range
        relevant = {
            candidate.row: candidate
            for candidate in result.candidates
            if first <= candidate.row <= last
        }
        self.hint_candidates_by_row.update(relevant)
        return len(relevant)

    def build_thumbnail_snapshot(
        self,
        *,
        visible_rows: Iterable[tuple[int, AssetDTO]],
        prefetched_rows: Mapping[int, AssetDTO],
        size: QSize,
    ) -> ThumbnailDemandSnapshot | None:
        viewport = self.viewport
        if viewport is None:
            return None
        guard_rows = tuple(viewport.iter_full_guard_rows())
        speculative_rows = tuple(viewport.iter_full_speculative_rows())
        candidates: list[ThumbnailPrefetchCandidate] = []

        def resolve(rows: tuple[int, ...], kind: str) -> tuple[Path, ...]:
            paths: list[Path] = []
            for rank, row in enumerate(rows):
                dto = prefetched_rows.get(row)
                hint = self.hint_candidates_by_row.get(row)
                if dto is not None:
                    path = Path(dto.abs_path)
                    l2_key = dto.thumb_cache_key
                    if not isinstance(l2_key, str) or not l2_key.strip():
                        metadata_key = (
                            dto.metadata.get("thumb_cache_key") if dto.metadata else None
                        )
                        l2_key = metadata_key if isinstance(metadata_key, str) else None
                elif hint is not None:
                    path = Path(hint.path)
                    l2_key = hint.l2_cache_key
                else:
                    continue
                paths.append(path)
                if isinstance(l2_key, str) and l2_key.strip():
                    candidates.append(
                        ThumbnailPrefetchCandidate(
                            row=row,
                            path=path,
                            l2_cache_key=l2_key,
                            kind=kind,
                            rank=rank,
                        )
                    )
            return tuple(dict.fromkeys(paths))

        guard_paths = resolve(guard_rows, "guard")
        speculative_paths = resolve(speculative_rows, "far_speculative")
        visible_paths = tuple(
            dict.fromkeys(Path(dto.abs_path) for _row, dto in visible_rows)
        )
        return ThumbnailDemandSnapshot(
            revision=viewport.generation,
            size=size,
            visible_paths=visible_paths,
            guard_paths=guard_paths,
            speculative_paths=speculative_paths,
            candidates=tuple(candidates),
            phase=viewport.phase,
            intent=viewport.intent,
        )

    def hint_candidates(
        self,
        ordered_rows: tuple[int, ...],
        guard_rows: frozenset[int],
    ) -> tuple[GalleryThumbnailCandidate, ...]:
        return tuple(
            GalleryThumbnailCandidate(
                row=row,
                path=candidate.path,
                l2_cache_key=candidate.l2_cache_key,
                rank=rank,
                kind="guard" if row in guard_rows else "far_speculative",
            )
            for rank, row in enumerate(ordered_rows)
            if (candidate := self.hint_candidates_by_row.get(row)) is not None
        )


__all__ = ["GalleryDemandCoordinator"]

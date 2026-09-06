"""Single source of truth for Gallery viewport and thumbnail demand."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable, Mapping

from PySide6.QtCore import QSize

from iPhoto.application.dtos import AssetDTO
from iPhoto.gui.gallery_demand import AssetViewportDemand
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
        self.viewports: dict[str, AssetViewportDemand] = {}
        self._latest_surface_id = "gallery"
        self.root: Path | None = None
        self.query: Any | None = None
        self.collection_revision = 0
        self.revision = 0
        self._scheduling_identities: dict[str, tuple[object, ...]] = {}
        self.hint_candidates_by_row: dict[int, GalleryThumbnailCandidate] = {}

    def reset(self) -> None:
        self.viewports.clear()
        self._latest_surface_id = "gallery"
        self.root = None
        self.query = None
        self.collection_revision = 0
        self.revision = 0
        self._scheduling_identities.clear()
        self.hint_candidates_by_row.clear()

    def update_viewport(
        self,
        viewport: AssetViewportDemand,
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
        surface_id = viewport.surface_id
        if scheduling_identity != self._scheduling_identities.get(surface_id):
            self.revision = max(self.revision + 1, int(viewport.generation))
            self._scheduling_identities[surface_id] = scheduling_identity
        effective_viewport = (
            viewport
            if viewport.generation == self.revision
            else replace(viewport, generation=self.revision)
        )
        self.viewports[surface_id] = effective_viewport
        self._latest_surface_id = surface_id
        self.root = root
        self.query = query
        self.collection_revision = int(collection_revision)
        self.prune_hints()

    def prune_hints(self) -> None:
        if not self.viewports or not self.hint_candidates_by_row:
            return
        self.hint_candidates_by_row = {
            row: candidate
            for row, candidate in self.hint_candidates_by_row.items()
            if any(
                viewport.full_prefetch_first <= row <= viewport.full_prefetch_last
                for viewport in self.viewports.values()
            )
        }

    @property
    def viewport(self) -> AssetViewportDemand | None:
        return self.viewports.get(self._latest_surface_id)

    def viewport_for(self, surface_id: str) -> AssetViewportDemand | None:
        return self.viewports.get(str(surface_id))

    def release_viewport(self, surface_id: str) -> None:
        surface_id = str(surface_id)
        self.viewports.pop(surface_id, None)
        self._scheduling_identities.pop(surface_id, None)
        if self._latest_surface_id == surface_id:
            self._latest_surface_id = next(reversed(self.viewports), "gallery")
        self.prune_hints()

    def merge_hint_result(self, result: GalleryThumbnailHintResult) -> int:
        """Accept old-generation work when it still covers the current demand."""

        viewport = self.viewport_for(getattr(result, "surface_id", "gallery"))
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
        surface_id: str = "gallery",
        visible_rows: Iterable[tuple[int, AssetDTO]],
        prefetched_rows: Mapping[int, AssetDTO],
        size: QSize,
    ) -> ThumbnailDemandSnapshot | None:
        viewport = self.viewport_for(surface_id)
        if viewport is None:
            return None
        visible_rows = tuple(visible_rows)
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
                    thumbnail_state = dto.thumbnail_state
                    thumb_revision = dto.thumb_revision
                elif hint is not None:
                    path = Path(hint.path)
                    l2_key = hint.l2_cache_key
                    thumbnail_state = hint.thumbnail_state
                    thumb_revision = hint.thumb_revision
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
                            thumbnail_state=thumbnail_state,
                            thumb_revision=thumb_revision,
                        )
                    )
            return tuple(dict.fromkeys(paths))

        guard_paths = resolve(guard_rows, "guard")
        speculative_paths = resolve(speculative_rows, "far_speculative")
        visible_paths = tuple(
            dict.fromkeys(Path(dto.abs_path) for _row, dto in visible_rows)
        )
        for rank, (row, dto) in enumerate(visible_rows):
            l2_key = dto.thumb_cache_key
            if not isinstance(l2_key, str) or not l2_key.strip():
                continue
            candidates.append(
                ThumbnailPrefetchCandidate(
                    row=row,
                    path=dto.abs_path,
                    l2_cache_key=l2_key,
                    kind="guard",
                    rank=rank,
                    thumbnail_state=dto.thumbnail_state,
                    thumb_revision=dto.thumb_revision,
                )
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
                thumbnail_state=candidate.thumbnail_state,
                thumb_revision=candidate.thumb_revision,
            )
            for rank, row in enumerate(ordered_rows)
            if (candidate := self.hint_candidates_by_row.get(row)) is not None
        )


__all__ = ["GalleryDemandCoordinator"]

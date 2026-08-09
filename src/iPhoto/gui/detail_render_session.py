"""Shared Detail/Edit render-session state for still photographs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from iPhoto.core.adjustment_mapping import resolve_adjustment_mapping
from iPhoto.core.color_resolver import ColorStats
from iPhoto.gui.detail_decode_backend import DecodedSurface
from iPhoto.gui.detail_pipeline import AssetSourceIdentity, DetailDecodeKey
from iPhoto.gui.detail_surface_residency import (
    SurfaceResidencyTracker,
    surface_resource_id,
)

EditRevisionKind = Literal["index", "session", "commit"]


@dataclass(frozen=True, slots=True)
class EditRenderState:
    """One immutable raw/resolved adjustment snapshot for a render session."""

    revision: tuple[EditRevisionKind, int]
    raw_adjustments: Mapping[str, Any]
    shader_adjustments: Mapping[str, Any]
    color_stats: ColorStats

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "raw_adjustments",
            MappingProxyType(dict(self.raw_adjustments)),
        )
        object.__setattr__(
            self,
            "shader_adjustments",
            MappingProxyType(dict(self.shader_adjustments)),
        )

    @classmethod
    def create(
        cls,
        raw_adjustments: Mapping[str, Any] | None,
        *,
        color_stats: ColorStats,
        revision: tuple[EditRevisionKind, int],
    ) -> EditRenderState:
        raw = dict(raw_adjustments or {})
        return cls(
            revision=revision,
            raw_adjustments=raw,
            shader_adjustments=resolve_adjustment_mapping(
                raw,
                stats=color_stats,
                bool_as_float=True,
                normalize_bw_for_render=True,
            ),
            color_stats=color_stats,
        )


@dataclass(slots=True)
class PhotoRenderSessionHandle:
    """GUI-thread-owned handle shared by Detail and Edit for one still asset."""

    session_id: int
    asset_id: str
    source_identity: AssetSourceIdentity
    current_surface: DecodedSurface
    edit_state: EditRenderState
    baseline_state: EditRenderState
    available_lods: set[int | str] = field(default_factory=set)
    edit_references: int = 0
    residency_tracker: SurfaceResidencyTracker | None = None
    _revision_counter: int = 0
    _surfaces_by_key: dict[DetailDecodeKey, DecodedSurface] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.available_lods.add(self.current_surface.decode_level)
        self._surfaces_by_key[self.current_surface.decode_key] = self.current_surface
        if self.residency_tracker is not None:
            self.residency_tracker.retain_surface(
                self._residency_owner_id,
                "render_session",
                self.current_surface,
            )
        self._revision_counter = max(
            int(self.source_identity.index_revision),
            int(self.edit_state.revision[1]),
        )

    @property
    def source(self) -> Path:
        return self.source_identity.path

    @property
    def current_texture_key(self) -> DetailDecodeKey:
        return self.current_surface.decode_key

    def replace_surface(self, surface: DecodedSurface) -> None:
        self.current_surface = surface
        self.retain_surface(surface)

    def retain_surface(self, surface: DecodedSurface) -> None:
        """Retain a decoded LOD without making it the presented surface."""

        previous = self._surfaces_by_key.get(surface.decode_key)
        if previous is not None and self.residency_tracker is not None:
            self.residency_tracker.release(
                self._residency_owner_id,
                surface_resource_id(previous),
            )
        self._surfaces_by_key[surface.decode_key] = surface
        self.available_lods.add(surface.decode_level)
        if self.residency_tracker is not None:
            self.residency_tracker.retain_surface(
                self._residency_owner_id,
                "render_session",
                surface,
            )

    def activate_surface(self, key: DetailDecodeKey) -> bool:
        surface = self._surfaces_by_key.get(key)
        if surface is None:
            return False
        self.current_surface = surface
        return True

    def surface_for_key(self, key: DetailDecodeKey) -> DecodedSurface | None:
        """Return a retained LOD surface without changing session state."""

        return self._surfaces_by_key.get(key)

    @property
    def _residency_owner_id(self) -> str:
        return f"render-session:{self.session_id}"

    def release_residency_observations(self) -> None:
        """Drop diagnostic owner references without changing surface lifetime."""

        if self.residency_tracker is not None:
            self.residency_tracker.release(self._residency_owner_id)

    def next_state(
        self,
        raw_adjustments: Mapping[str, Any],
        *,
        kind: EditRevisionKind = "session",
    ) -> EditRenderState:
        self._revision_counter += 1
        state = EditRenderState.create(
            raw_adjustments,
            color_stats=self.edit_state.color_stats,
            revision=(kind, self._revision_counter),
        )
        self.edit_state = state
        return state

    def commit_current_state(self) -> EditRenderState:
        state = self.next_state(self.edit_state.raw_adjustments, kind="commit")
        self.baseline_state = state
        return state

    def restore_baseline(self) -> EditRenderState:
        self.edit_state = self.baseline_state
        return self.edit_state


__all__ = ["EditRenderState", "PhotoRenderSessionHandle"]

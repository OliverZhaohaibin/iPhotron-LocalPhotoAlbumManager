"""Budgeted render-session state shared by Detail and Edit."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from iPhoto.core.adjustment_mapping import resolve_adjustment_mapping
from iPhoto.core.color_resolver import ColorStats
from iPhoto.gui.detail_decode_backend import DecodedSurface
from iPhoto.gui.detail_pipeline import AssetSourceIdentity, DetailDecodeKey


EditRevisionKind = Literal["index", "session", "commit"]


@dataclass(frozen=True, slots=True)
class EditRenderState:
    """One immutable raw/resolved adjustment snapshot."""

    revision: tuple[EditRevisionKind, int]
    raw_adjustments: Mapping[str, Any]
    shader_adjustments: Mapping[str, Any]
    color_stats: ColorStats

    def __post_init__(self) -> None:
        object.__setattr__(self, "raw_adjustments", MappingProxyType(dict(self.raw_adjustments)))
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
    ) -> "EditRenderState":
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


class SurfaceRetentionBudget:
    """Account strong CPU-surface references owned by PlayerView sessions."""

    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max(0, int(max_bytes))
        self._bytes_by_session: dict[int, int] = {}

    @property
    def retained_bytes(self) -> int:
        return sum(self._bytes_by_session.values())

    def can_replace(self, session_id: int, byte_count: int) -> bool:
        previous = self._bytes_by_session.get(int(session_id), 0)
        return self.retained_bytes - previous + max(0, int(byte_count)) <= self.max_bytes

    def replace(self, session_id: int, byte_count: int) -> bool:
        if not self.can_replace(session_id, byte_count):
            return False
        self._bytes_by_session[int(session_id)] = max(0, int(byte_count))
        return True

    def release(self, session_id: int) -> None:
        self._bytes_by_session.pop(int(session_id), None)

    def clear(self) -> None:
        self._bytes_by_session.clear()

    @staticmethod
    def surface_bytes(surface: DecodedSurface | None) -> int:
        if surface is None or surface.image.isNull():
            return 0
        return max(0, int(surface.image.sizeInBytes()))


@dataclass(slots=True)
class PhotoRenderSessionHandle:
    """GUI-owned, safely invalidatable handle for one still asset.

    A session deliberately retains only the current surface and, while an
    upload is in flight, one fallback.  It never accumulates every visited LOD.
    """

    session_id: int
    asset_id: str
    source_identity: AssetSourceIdentity
    current_surface: DecodedSurface | None
    edit_state: EditRenderState
    baseline_state: EditRenderState
    upload_fallback: DecodedSurface | None = None
    edit_references: int = 0
    valid: bool = True
    _revision_counter: int = 0

    def __post_init__(self) -> None:
        self._revision_counter = max(
            int(self.source_identity.index_revision),
            int(self.edit_state.revision[1]),
        )

    @property
    def source(self) -> Path:
        return self.source_identity.path

    @property
    def current_texture_key(self) -> DetailDecodeKey | None:
        surface = self.current_surface
        return surface.decode_key if surface is not None else None

    @property
    def retained_bytes(self) -> int:
        current = SurfaceRetentionBudget.surface_bytes(self.current_surface)
        fallback = self.upload_fallback
        if fallback is self.current_surface:
            return current
        return current + SurfaceRetentionBudget.surface_bytes(fallback)

    def replace_surface(
        self,
        surface: DecodedSurface,
        *,
        retain_upload_fallback: bool = False,
    ) -> bool:
        if not self.valid:
            return False
        previous = self.current_surface
        self.current_surface = surface
        self.upload_fallback = previous if retain_upload_fallback else None
        return True

    def finish_upload(self) -> None:
        self.upload_fallback = None

    def surface_for_key(self, key: DetailDecodeKey) -> DecodedSurface | None:
        if not self.valid:
            return None
        for surface in (self.current_surface, self.upload_fallback):
            if surface is not None and surface.decode_key == key:
                return surface
        return None

    def next_state(
        self,
        raw_adjustments: Mapping[str, Any],
        *,
        kind: EditRevisionKind = "session",
    ) -> EditRenderState | None:
        if not self.valid:
            return None
        self._revision_counter += 1
        state = EditRenderState.create(
            raw_adjustments,
            color_stats=self.edit_state.color_stats,
            revision=(kind, self._revision_counter),
        )
        self.edit_state = state
        return state

    def restore_baseline(self) -> EditRenderState | None:
        if not self.valid:
            return None
        self.edit_state = self.baseline_state
        return self.edit_state

    def invalidate(self) -> None:
        """Make all late consumers harmless and release strong surfaces."""

        self.valid = False
        self.edit_references = 0
        self.current_surface = None
        self.upload_fallback = None


__all__ = [
    "EditRenderState",
    "PhotoRenderSessionHandle",
    "SurfaceRetentionBudget",
]

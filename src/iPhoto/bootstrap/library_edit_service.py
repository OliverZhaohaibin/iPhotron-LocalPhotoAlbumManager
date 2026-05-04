"""Library-scoped edit sidecar and render-state helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..application.ports import EditRenderingState, EditServicePort, EditSidecarPort
from ..core.adjustment_mapping import (
    default_adjustment_values,
    normalise_video_trim,
    resolve_adjustment_mapping,
    trim_is_non_default,
    video_has_visible_edits,
    video_requires_adjusted_preview,
)
from ..infrastructure.repositories.edit_sidecar_repository import (
    FileSystemEditSidecarRepository,
)


class LibraryEditService(EditServicePort):
    """Own edit-sidecar access for one active library session."""

    def __init__(
        self,
        library_root: Path | None,
        *,
        sidecar_repository: EditSidecarPort | None = None,
    ) -> None:
        self.library_root = (
            self._normalize_path(Path(library_root)) if library_root is not None else None
        )
        self._sidecar_repository = sidecar_repository or FileSystemEditSidecarRepository()

    def sidecar_exists(self, path: Path) -> bool:
        return self._sidecar_repository.sidecar_exists(self._normalize_path(path))

    def read_adjustments(self, path: Path) -> dict[str, Any]:
        return self._sidecar_repository.read_adjustments(self._normalize_path(path))

    def write_adjustments(self, path: Path, adjustments: dict[str, Any]) -> None:
        self._sidecar_repository.write_adjustments(
            self._normalize_path(path),
            adjustments,
        )

    def default_adjustments(self) -> dict[str, Any]:
        return default_adjustment_values()

    def describe_adjustments(
        self,
        path: Path,
        *,
        duration_hint: float | None = None,
        color_stats: Any | None = None,
    ) -> EditRenderingState:
        normalized = self._normalize_path(path)
        sidecar_exists = self._sidecar_repository.sidecar_exists(normalized)
        raw_adjustments = self._sidecar_repository.read_adjustments(normalized)
        resolved_adjustments = resolve_adjustment_mapping(
            raw_adjustments,
            stats=color_stats,
            normalize_bw_for_render=True,
        )
        adjusted_preview = video_requires_adjusted_preview(raw_adjustments)
        has_visible_edits = video_has_visible_edits(raw_adjustments, duration_hint)
        trim_range_ms: tuple[int, int] | None = None
        effective_duration_sec = duration_hint
        if trim_is_non_default(raw_adjustments, duration_hint):
            trim_in_sec, trim_out_sec = normalise_video_trim(raw_adjustments, duration_hint)
            trim_range_ms = (
                int(round(trim_in_sec * 1000.0)),
                int(round(trim_out_sec * 1000.0)),
            )
            effective_duration_sec = max(trim_out_sec - trim_in_sec, 0.0)
        return EditRenderingState(
            sidecar_exists=sidecar_exists,
            raw_adjustments=raw_adjustments,
            resolved_adjustments=resolved_adjustments,
            adjusted_preview=adjusted_preview,
            has_visible_edits=has_visible_edits,
            trim_range_ms=trim_range_ms,
            effective_duration_sec=effective_duration_sec,
        )

    @staticmethod
    def _normalize_path(path: Path) -> Path:
        try:
            return Path(path).expanduser().resolve()
        except OSError:
            return Path(path).expanduser()


__all__ = ["LibraryEditService"]

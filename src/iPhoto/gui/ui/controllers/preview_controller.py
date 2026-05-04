"""Encapsulate the long-press preview behaviour for asset grids."""

from __future__ import annotations

from functools import partial
from pathlib import Path
import sys
from typing import Any, Callable

from PySide6.QtCore import QModelIndex, QObject, QRect

from ....application.ports import EditServicePort
from ..models.roles import Roles
from ..widgets.asset_grid import AssetGrid
from ..widgets.preview_window import PreviewWindow


class PreviewController(QObject):
    """Manage preview requests originating from gallery widgets."""

    def __init__(
        self,
        preview_window: PreviewWindow,
        edit_service_getter: Callable[[], EditServicePort | None] | None = None,
        parent: QObject | None = None,
    ) -> None:
        """Store the shared preview window instance."""

        super().__init__(parent)
        self._preview_window = preview_window
        self._edit_service_getter = edit_service_getter

    def bind_view(self, view: AssetGrid) -> None:
        """Attach preview signal handlers to *view*."""

        view.requestPreview.connect(partial(self._handle_request_preview, view))
        view.previewReleased.connect(self.close_preview_after_release)
        view.previewCancelled.connect(self.cancel_preview)

    def close_preview(self, delayed: bool = True) -> None:
        """Close the preview window, optionally cancelling the delay timer."""

        self._preview_window.close_preview(delayed)

    def close_preview_after_release(self) -> None:
        """Hide the preview window after a successful long press."""

        self.close_preview(True)

    def cancel_preview(self) -> None:
        """Abort a preview that was cancelled before finishing the gesture."""

        self.close_preview(False)

    def _handle_request_preview(self, view: AssetGrid, index: QModelIndex) -> None:
        """Show the preview for *index* if it represents playable media."""

        if not index or not index.isValid():
            return
        is_video = bool(index.data(Roles.IS_VIDEO))
        is_live = bool(index.data(Roles.IS_LIVE))
        if not is_video and not is_live:
            return
        preview_raw = None
        if is_live:
            preview_raw = index.data(Roles.LIVE_MOTION_ABS)
        else:
            preview_raw = index.data(Roles.ABS)
        if not preview_raw:
            return
        preview_path = Path(str(preview_raw))
        adjustment_raw = index.data(Roles.ABS) or preview_raw
        adjustment_path = Path(str(adjustment_raw))
        rect = view.visualRect(index)
        global_rect = QRect(view.viewport().mapToGlobal(rect.topLeft()), rect.size())
        info = index.data(Roles.INFO)
        aspect_hint = self._extract_aspect_hint(info)
        adjustments, trim_range_ms, adjusted_preview = self._extract_preview_rendering(
            adjustment_path,
            info,
        )
        self._preview_window.show_preview(
            preview_path,
            global_rect,
            aspect_ratio_hint=aspect_hint,
            adjustments=adjustments,
            trim_range_ms=trim_range_ms,
            adjusted_preview=adjusted_preview,
        )

    def _extract_aspect_hint(self, info: Any) -> float | None:
        """Return a best-effort display aspect ratio hint from model metadata."""

        if not isinstance(info, dict):
            return None

        def _to_float(value: Any) -> float | None:
            if isinstance(value, bool):
                return None
            if isinstance(value, (int, float)):
                numeric = float(value)
                return numeric if numeric > 0.0 else None
            if isinstance(value, str):
                try:
                    numeric = float(value.strip())
                except ValueError:
                    return None
                return numeric if numeric > 0.0 else None
            return None

        width = _to_float(info.get("w")) or _to_float(info.get("width"))
        height = _to_float(info.get("h")) or _to_float(info.get("height"))
        if width is None or height is None:
            return None

        rotation_value = (
            info.get("rotation")
            or info.get("rotate")
            or info.get("video_rotation")
            or info.get("display_rotation")
        )
        rotation = 0
        if rotation_value is not None:
            try:
                rotation = int(float(rotation_value)) % 360
            except (TypeError, ValueError):
                rotation = 0
        if rotation in (90, 270):
            width, height = height, width

        if height <= 0.0:
            return None
        return width / height

    def _extract_preview_rendering(
        self,
        preview_path: Path,
        info: Any,
    ) -> tuple[dict[str, object] | None, tuple[int, int] | None, bool]:
        """Return render adjustments, trim range, and preview mode for *preview_path*."""

        duration_sec = self._extract_duration_hint(info)
        edit_service = self._edit_service_getter() if self._edit_service_getter else None
        if edit_service is None:
            return None, None, False

        try:
            state = edit_service.describe_adjustments(
                preview_path,
                duration_hint=duration_sec,
            )
        except Exception:  # noqa: BLE001 - preview should gracefully fall back to raw playback
            return None, None, False
        adjusted_preview = state.adjusted_preview
        if sys.platform == "darwin" and state.has_visible_edits:
            # The non-adjusted long-press popup uses Qt's plain video item,
            # not our VideoRendererWidget path. On macOS that means even
            # "native-renderer-safe" edits such as rotate-only sidecars would
            # be skipped unless we route the popup through the adjusted RHI
            # preview surface.
            adjusted_preview = True
        adjustments = state.resolved_adjustments if adjusted_preview else None
        return adjustments, state.trim_range_ms, adjusted_preview

    def _extract_duration_hint(self, info: Any) -> float | None:
        """Return a best-effort duration hint from model metadata."""

        if not isinstance(info, dict):
            return None

        for key in ("dur", "duration"):
            value = info.get(key)
            if value is None:
                continue
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                continue
            if numeric > 0.0:
                return numeric
        return None

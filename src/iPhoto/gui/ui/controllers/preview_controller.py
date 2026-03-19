"""Encapsulate the long-press preview behaviour for asset grids."""

from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Any, Optional

from PySide6.QtCore import QModelIndex, QObject, QRect

from ..models.roles import Roles
from ..widgets.asset_grid import AssetGrid
from ..widgets.preview_window import PreviewWindow


class PreviewController(QObject):
    """Manage preview requests originating from gallery widgets."""

    def __init__(self, preview_window: PreviewWindow, parent: QObject | None = None) -> None:
        """Store the shared preview window instance."""

        super().__init__(parent)
        self._preview_window = preview_window

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
        rect = view.visualRect(index)
        global_rect = QRect(view.viewport().mapToGlobal(rect.topLeft()), rect.size())
        aspect_hint = self._extract_aspect_hint(index.data(Roles.INFO))
        self._preview_window.show_preview(
            preview_path,
            global_rect,
            aspect_ratio_hint=aspect_hint,
        )

    def _extract_aspect_hint(self, info: Any) -> Optional[float]:
        """Return a best-effort display aspect ratio hint from model metadata."""

        if not isinstance(info, dict):
            return None

        def _to_float(value: Any) -> Optional[float]:
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

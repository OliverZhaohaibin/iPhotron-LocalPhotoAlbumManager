"""Pure Python detail-screen view model."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

from iPhoto.application.ports import EditRenderingState, EditServicePort
from iPhoto.application.dtos import AssetDTO
from iPhoto.application.services.asset_service import AssetService
from iPhoto.gui.ui.media.media_restore_request import MediaRestoreRequest
from iPhoto.utils.geocoding import resolve_location_name

from .base import BaseViewModel
from .gallery_collection_store import GalleryCollectionStore
from .signal import ObservableProperty, Signal


class AdjustmentCommitPort(Protocol):
    def commit(self, source: Path, adjustments: dict, *, reason: str) -> bool: ...


class MediaSelectionPort(Protocol):
    def set_current_row(self, row: int) -> Optional[Path]: ...
    def set_current_by_path(self, path: Path) -> bool: ...
    def current_row(self) -> int: ...
    def current_source(self) -> Optional[Path]: ...
    def next_row(self) -> Optional[int]: ...
    def previous_row(self) -> Optional[int]: ...


@dataclass(frozen=True)
class DetailPresentation:
    row: int
    asset_id: str
    path: Path
    is_video: bool
    is_live: bool
    is_favorite: bool
    info: dict[str, Any]
    location: Optional[str]
    timestamp: object
    can_edit: bool
    can_rotate: bool
    can_share: bool
    can_toggle_favorite: bool
    info_panel_visible: bool
    live_motion_rel: Optional[Path]
    live_motion_abs: Optional[Path]
    video_adjustments: Optional[dict[str, Any]]
    video_trim_range_ms: Optional[tuple[int, int]]
    video_adjusted_preview: bool
    reload_token: int


class DetailViewModel(BaseViewModel):
    """Own detail presentation and detail-scoped actions."""

    def __init__(
        self,
        *,
        collection_store: GalleryCollectionStore,
        media_session: MediaSelectionPort,
        asset_service: AssetService,
        adjustment_commit_port: AdjustmentCommitPort | None = None,
        edit_service_getter: Callable[[], EditServicePort | None] | None = None,
    ) -> None:
        super().__init__()
        self._store = collection_store
        self._media_session = media_session
        self._asset_service = asset_service
        self._adjustment_commit_port = adjustment_commit_port
        self._edit_service_getter = edit_service_getter
        self._info_panel_visible = False
        self._presentation_reload_token = 0
        self._pending_restore_requests: dict[Path, MediaRestoreRequest] = {}
        self._video_presentation_cache: dict | None = None
        self._store.data_changed.connect(self._handle_store_changed)
        self._store.row_changed.connect(self._handle_row_changed)
        restore_signal = getattr(self._media_session, "restoreRequested", None)
        if restore_signal is not None:
            restore_signal.connect(self._handle_restore_requested)
        committed_signal = getattr(self._adjustment_commit_port, "adjustmentsCommitted", None)
        if committed_signal is not None:
            committed_signal.connect(self._handle_adjustments_committed)

        self.current_row = ObservableProperty(-1)
        self.current_path = ObservableProperty(None)
        self.presentation = ObservableProperty(None)

        self.route_requested = Signal()
        self.presentation_changed = Signal()
        self.edit_requested = Signal()
        self.rotate_requested = Signal()

    def show_row(self, row: int) -> None:
        source = self._media_session.set_current_row(row)
        if source is None:
            return
        self._store.pin_row(row)
        dto = self._store.asset_at(row)
        if dto is None:
            return
        self.current_row.value = row
        self.current_path.value = source
        presentation = self._build_presentation(row, dto)
        self.presentation.value = presentation
        self.route_requested.emit("detail")
        self.presentation_changed.emit(presentation)

    def show_current(self) -> None:
        row = self._media_session.current_row()
        if row >= 0:
            self.show_row(row)

    def next(self) -> None:
        row = self._media_session.next_row()
        if row is not None:
            self.show_row(row)

    def previous(self) -> None:
        row = self._media_session.previous_row()
        if row is not None:
            self.show_row(row)

    def toggle_favorite(self) -> None:
        row = self.current_row.value
        if row is None or row < 0:
            return
        dto = self._store.asset_at(row)
        if dto is None:
            return
        new_state = self._asset_service.toggle_favorite_by_path(dto.abs_path)
        self._store.update_favorite_status(row, new_state)
        self._refresh_presentation()

    def toggle_info(self) -> None:
        self._info_panel_visible = not self._info_panel_visible
        self._refresh_presentation()

    def hide_info_panel(self, *, refresh_presentation: bool = True) -> None:
        """Ensure the floating info panel is not considered visible anymore."""

        if not self._info_panel_visible:
            return
        self._info_panel_visible = False
        if refresh_presentation:
            self._refresh_presentation()

    def rotate_current(self) -> None:
        presentation = self.presentation.value
        if presentation is None:
            return
        self.rotate_requested.emit(presentation.path, presentation.is_video)

    def request_edit(self) -> None:
        path = self.current_path.value
        if isinstance(path, Path):
            self.hide_info_panel(refresh_presentation=True)
            self.edit_requested.emit(path)

    def back_to_gallery(self) -> None:
        self.hide_info_panel(refresh_presentation=True)
        self.route_requested.emit("gallery")

    def restore_after_adjustment(
        self,
        path: Path,
        reason: str,
        restore_request: MediaRestoreRequest | None = None,
    ) -> None:
        request = restore_request or MediaRestoreRequest(path=path, reason=reason)
        self._presentation_reload_token += 1
        current_path = self.current_path.value
        if isinstance(current_path, Path) and current_path == path:
            self._pending_restore_requests[current_path] = request
            self.show_current()
            return
        if self._media_session.set_current_by_path(path):
            current_source = self._media_session.current_source()
            restore_key = current_source if isinstance(current_source, Path) else path
            self._pending_restore_requests[restore_key] = request
            self.show_current()
            return

    def info_for_current(self) -> Optional[dict[str, Any]]:
        presentation = self.presentation.value
        if presentation is None:
            return None
        return dict(presentation.info)

    def current_asset_path(self) -> Optional[Path]:
        path = self.current_path.value
        return path if isinstance(path, Path) else None

    def refresh_current(self) -> None:
        self._refresh_presentation()

    def _refresh_presentation(self) -> None:
        row = self.current_row.value
        if row is None or row < 0:
            return
        dto = self._store.asset_at(row)
        if dto is None:
            return
        presentation = self._build_presentation(row, dto)
        self.presentation.value = presentation
        self.presentation_changed.emit(presentation)

    def _handle_store_changed(self) -> None:
        current_row = self._media_session.current_row()
        current_path = self._media_session.current_source()
        if current_row < 0 or not isinstance(current_path, Path):
            return
        self.current_row.value = current_row
        self.current_path.value = current_path
        self._refresh_presentation()

    def _handle_row_changed(self, row: int) -> None:
        current_row = self.current_row.value
        if current_row == row:
            self._refresh_presentation()

    def _handle_restore_requested(self, request: object) -> None:
        if not isinstance(request, MediaRestoreRequest):
            return
        self.restore_after_adjustment(
            request.path,
            request.reason,
            restore_request=request,
        )

    def _handle_adjustments_committed(self, path: object, reason: str) -> None:
        if not isinstance(path, Path) or reason == "edit_done":
            return
        self.restore_after_adjustment(path, reason)

    def _build_presentation(self, row: int, dto: AssetDTO) -> DetailPresentation:
        info = dto.metadata.copy() if dto.metadata else {}
        info.update(
            {
                "rel": str(dto.rel_path),
                "abs": str(dto.abs_path),
                "name": dto.rel_path.name,
                "is_video": dto.is_video,
                "w": dto.width,
                "h": dto.height,
                "dur": dto.duration,
                "bytes": dto.size_bytes,
            }
        )
        location = self._resolve_location(dto)
        if isinstance(location, str) and location.strip():
            info["location"] = location.strip()
        live_motion_rel, live_motion_abs = self._resolve_live_motion(dto)
        video_adjustments: dict[str, Any] | None = None
        video_trim_range_ms: tuple[int, int] | None = None
        video_adjusted_preview = False
        restore_request = self._pending_restore_requests.pop(dto.abs_path, None)
        if dto.is_video:
            cache = self._video_presentation_cache
            if (
                isinstance(cache, dict)
                and cache.get("path") == dto.abs_path
                and cache.get("reload_token") == self._presentation_reload_token
            ):
                video_adjustments = cache.get("video_adjustments")
                video_trim_range_ms = cache.get("video_trim_range_ms")
                video_adjusted_preview = bool(cache.get("video_adjusted_preview", False))
            else:
                duration_sec = self._resolve_video_duration(dto, restore_request)
                edit_state = self._describe_adjustments(
                    dto.abs_path,
                    duration_hint=duration_sec,
                )
                video_adjusted_preview = edit_state.adjusted_preview
                video_trim_range_ms = edit_state.trim_range_ms
                video_adjustments = (
                    edit_state.resolved_adjustments
                    if video_adjusted_preview
                    else (edit_state.raw_adjustments or None)
                )
                self._video_presentation_cache = {
                    "path": dto.abs_path,
                    "reload_token": self._presentation_reload_token,
                    "video_adjustments": video_adjustments,
                    "video_trim_range_ms": video_trim_range_ms,
                    "video_adjusted_preview": video_adjusted_preview,
                }
        return DetailPresentation(
            row=row,
            asset_id=dto.id,
            path=dto.abs_path,
            is_video=dto.is_video,
            is_live=dto.is_live,
            is_favorite=dto.is_favorite,
            info=info,
            location=location,
            timestamp=dto.created_at,
            can_edit=True,
            can_rotate=True,
            can_share=True,
            can_toggle_favorite=True,
            info_panel_visible=self._info_panel_visible,
            live_motion_rel=live_motion_rel,
            live_motion_abs=live_motion_abs,
            video_adjustments=video_adjustments,
            video_trim_range_ms=video_trim_range_ms,
            video_adjusted_preview=video_adjusted_preview,
            reload_token=self._presentation_reload_token,
        )

    def _resolve_video_duration(
        self,
        dto: AssetDTO,
        restore_request: MediaRestoreRequest | None,
    ) -> float | None:
        duration_hint = restore_request.duration_sec if restore_request is not None else None
        if duration_hint is not None and duration_hint > 0.0:
            return float(duration_hint)
        try:
            duration_sec = float(dto.duration or 0.0)
        except (TypeError, ValueError):
            return None
        if duration_sec <= 0.0:
            return None
        return duration_sec

    def _describe_adjustments(
        self,
        path: Path,
        *,
        duration_hint: float | None = None,
    ) -> EditRenderingState:
        edit_service = self._edit_service_getter() if self._edit_service_getter else None
        if edit_service is None:
            return EditRenderingState(
                sidecar_exists=False,
                raw_adjustments={},
                resolved_adjustments={},
                adjusted_preview=False,
                has_visible_edits=False,
                trim_range_ms=None,
                effective_duration_sec=duration_hint,
            )
        return edit_service.describe_adjustments(
            path,
            duration_hint=duration_hint,
        )

    def _resolve_location(self, dto: AssetDTO) -> Optional[str]:
        metadata = dto.metadata or {}
        location = metadata.get("location") or metadata.get("place")
        if isinstance(location, str) and location.strip():
            return location.strip()
        gps = metadata.get("gps")
        if isinstance(gps, dict):
            resolved = resolve_location_name(gps)
            if resolved:
                metadata["location"] = resolved
                return resolved
        components = [metadata.get("city"), metadata.get("state"), metadata.get("country")]
        normalized = [str(item).strip() for item in components if item]
        return ", ".join(normalized) if normalized else None

    def _resolve_live_motion(self, dto: AssetDTO) -> tuple[Optional[Path], Optional[Path]]:
        metadata = dto.metadata or {}
        live_partner_rel = metadata.get("live_partner_rel")
        live_role = metadata.get("live_role")
        if isinstance(live_partner_rel, str) and live_partner_rel and live_role != 1:
            rel_path = Path(live_partner_rel)
            if rel_path.is_absolute():
                return rel_path, rel_path
            library_root = self._store.library_root()
            if library_root is not None:
                return rel_path, (library_root / rel_path).resolve()
            return rel_path, None

        group_id = metadata.get("live_photo_group_id")
        if not group_id:
            return None, None
        for candidate_row in range(self._store.count()):
            candidate = self._store.asset_at(candidate_row)
            if candidate is None or not candidate.is_video:
                continue
            candidate_group = (candidate.metadata or {}).get("live_photo_group_id")
            if candidate_group == group_id:
                return candidate.rel_path, candidate.abs_path
        return None, None

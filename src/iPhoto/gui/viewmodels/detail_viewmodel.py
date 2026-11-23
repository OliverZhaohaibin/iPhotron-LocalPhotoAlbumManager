"""Pure Python detail-screen view model."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

from iPhoto.application.dtos import AssetDTO
from iPhoto.application.ports import AssetStateServicePort, EditServicePort
from iPhoto.gui.detail_pipeline import AssetSourceIdentity
from iPhoto.gui.detail_profile import emit_detail_event
from iPhoto.gui.ui.media.media_restore_request import MediaRestoreRequest
from iPhoto.gui.ui.media.media_selection_session import (
    MediaSelectionChangeReason,
    MediaSelectionSnapshot,
    MediaSelectionState,
)

from .base import BaseViewModel
from .gallery_collection_store import GalleryCollectionStore
from .signal import ObservableProperty, Signal


class AdjustmentCommitPort(Protocol):
    def commit(self, source: Path, adjustments: dict, *, reason: str) -> bool: ...


class MediaSelectionPort(Protocol):
    selectionChanged: Signal

    def set_current_row(self, row: int) -> Optional[Path]: ...
    def set_current_by_path(self, path: Path) -> bool: ...
    def selection_snapshot(self) -> MediaSelectionSnapshot: ...
    def current_row(self) -> int: ...
    def current_source(self) -> Optional[Path]: ...
    def selection_state(self) -> MediaSelectionState: ...
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
    request_generation: int = 0
    video_duration_hint: float | None = None
    source_identity: AssetSourceIdentity | None = None

    @property
    def render_key(self) -> tuple[object, ...]:
        """Return the stable media identity for one visible render transaction.

        Gallery rows are positional and can move whenever a scan publishes a
        newly sorted batch.  They must therefore never participate in render
        identity.  Metadata-only changes (favorite, title, location, and
        similar chrome) also intentionally stay outside this key.
        """

        identity = self.source_identity or AssetSourceIdentity.from_info(
            self.path,
            self.info,
        )
        media_kind = "video" if self.is_video else "image"
        if media_kind == "image" and self.is_live and self.live_motion_abs is not None:
            media_kind = "live_motion"
        live_motion = (
            Path(self.live_motion_abs).expanduser().absolute()
            if self.live_motion_abs is not None
            else None
        )
        return (
            str(self.asset_id),
            identity.path,
            media_kind,
            identity.revision,
            int(identity.orientation),
            int(self.reload_token),
            live_motion,
        )


class DetailViewModel(BaseViewModel):
    """Own detail presentation and detail-scoped actions."""

    def __init__(
        self,
        *,
        collection_store: GalleryCollectionStore,
        media_session: MediaSelectionPort,
        asset_state_service: AssetStateServicePort | None,
        adjustment_commit_port: AdjustmentCommitPort | None = None,
        edit_service_getter: Callable[[], EditServicePort | None] | None = None,
    ) -> None:
        super().__init__()
        self._store = collection_store
        self._media_session = media_session
        self._asset_state_service = asset_state_service
        self._adjustment_commit_port = adjustment_commit_port
        del edit_service_getter
        self._info_panel_visible = False
        self._presentation_reload_token = 0
        self._pending_restore_requests: dict[Path, MediaRestoreRequest] = {}
        self._pending_show_row: int | None = None
        self._request_generation = 0
        self._last_selection_version = 0
        self._selection_snapshot = MediaSelectionSnapshot(
            version=0,
            state=MediaSelectionState.NONE,
            row=None,
            path=None,
            asset_id=None,
        )

        self.current_row = ObservableProperty(-1)
        self.current_path = ObservableProperty(None)
        self.selection_state = ObservableProperty(MediaSelectionState.NONE)
        self.presentation = ObservableProperty(None)

        self.route_requested = Signal()
        self.presentation_changed = Signal()
        self.edit_requested = Signal()
        self.rotate_requested = Signal()

        self._store.data_changed.connect(self._handle_store_changed)
        self._store.row_changed.connect(self._handle_row_changed)
        row_loaded = getattr(self._store, "row_loaded", None)
        if row_loaded is not None:
            row_loaded.connect(self._handle_row_loaded)
        restore_signal = getattr(self._media_session, "restoreRequested", None)
        if restore_signal is not None:
            restore_signal.connect(self._handle_restore_requested)
        navigation_signal = getattr(self._media_session, "navigationRequested", None)
        if navigation_signal is not None:
            navigation_signal.connect(self._handle_navigation_requested)
        selection_signal = getattr(self._media_session, "selectionChanged", None)
        if selection_signal is not None:
            selection_signal.connect(self._handle_selection_changed)
        committed_signal = getattr(self._adjustment_commit_port, "adjustmentsCommitted", None)
        if committed_signal is not None:
            committed_signal.connect(self._handle_adjustments_committed)

    def bind_asset_state_service(
        self,
        asset_state_service: AssetStateServicePort | None,
    ) -> None:
        self._asset_state_service = asset_state_service
        self._refresh_presentation()

    def show_row(self, row: int) -> None:
        self._request_generation += 1
        self._request_selection_row(row)

    def _request_selection_row(self, row: int) -> None:
        """Submit one selection command without creating a second generation."""

        count = self._store.count()
        row_in_collection = row >= 0 and (
            not isinstance(count, int) or row < count
        )
        self._pending_show_row = row if row_in_collection else None
        previous_version = self._last_selection_version
        source = self._media_session.set_current_row(row)
        if source is None:
            return

        # Real MediaSelectionSession publishes synchronously. Older embedders
        # and narrow test doubles may only implement the read API, so reconcile
        # a coherent compatibility snapshot when no event arrived.
        if self._last_selection_version != previous_version:
            return
        dto = self._store.asset_at(row)
        if dto is None:
            return
        self._handle_selection_changed(
            MediaSelectionSnapshot(
                version=previous_version + 1,
                state=MediaSelectionState.RESOLVED,
                row=row,
                path=dto.abs_path,
                asset_id=str(dto.id),
            ),
            MediaSelectionChangeReason.USER_SELECTED,
            dto,
        )

    def _publish_user_selection(
        self,
        snapshot: MediaSelectionSnapshot,
        dto: AssetDTO,
    ) -> None:
        emit_detail_event(
            "click_received",
            generation=self._request_generation,
            row=snapshot.row,
            media_type="video" if dto.is_video else "image",
        )
        # Route before constructing even the lightweight presentation. The
        # coordinator starts all file I/O on a later event-loop turn, allowing
        # the opaque Detail loading surface to paint first.
        self.route_requested.emit("detail")
        self._refresh_presentation(
            render_change_already_versioned=True,
            dto_override=dto,
        )

    def _publish_passive_selection(
        self,
        snapshot: MediaSelectionSnapshot,
        dto: AssetDTO,
    ) -> None:
        del snapshot
        self._refresh_presentation(
            render_change_already_versioned=False,
            dto_override=dto,
        )

    def show_current(self) -> None:
        row = self._media_session.current_row()
        if row >= 0:
            self.show_row(row)

    def recover_current_presentation(self) -> bool:
        """Restart the stable visible asset without requiring an authoritative row.

        Fullscreen may need to recover a terminal still render while Gallery is
        resolving (or has exhausted) the asset's new row. Re-versioning the
        immutable presentation starts a fresh render transaction without adding
        database or filesystem work to the GUI path.
        """

        snapshot = self._media_selection_snapshot()
        presentation = self.presentation.value
        if (
            not isinstance(presentation, DetailPresentation)
            or presentation.is_video
            or snapshot.state is MediaSelectionState.NONE
            or snapshot.path != presentation.path
            or (
                snapshot.asset_id is not None
                and snapshot.asset_id != str(presentation.asset_id)
            )
        ):
            return False
        if snapshot.is_resolved:
            self.show_current()
            return True
        if snapshot.state not in {
            MediaSelectionState.ANCHOR_RESOLVING,
            MediaSelectionState.ANCHOR_UNRESOLVED,
            MediaSelectionState.FALLBACK_PENDING,
        }:
            return False

        self._request_generation = max(
            int(self._request_generation),
            int(presentation.request_generation),
        ) + 1
        recovered = replace(
            presentation,
            row=-1,
            request_generation=self._request_generation,
        )
        emit_detail_event(
            "stable_presentation_recovery_requested",
            generation=self._request_generation,
            asset_id=presentation.asset_id,
            selection_state=snapshot.state.value,
        )
        self._publish_presentation_if_changed(recovered)
        return True

    def next(self) -> None:
        row = self._media_session.next_row()
        if row is not None:
            self.show_row(row)

    def previous(self) -> None:
        row = self._media_session.previous_row()
        if row is not None:
            self.show_row(row)

    def toggle_favorite(self) -> None:
        if self._asset_state_service is None:
            return
        presentation = self._actionable_presentation(
            capability="can_toggle_favorite",
            require_resolved=True,
        )
        snapshot = self._media_selection_snapshot()
        row = snapshot.row
        if presentation is None or row is None:
            return
        dto = self._store.asset_at(row)
        if (
            dto is None
            or dto.abs_path != presentation.path
            or str(dto.id) != str(presentation.asset_id)
        ):
            return
        new_state = self._asset_state_service.toggle_favorite(presentation.path)
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
        presentation = self._actionable_presentation(capability="can_rotate")
        if presentation is None:
            return
        self.rotate_requested.emit(presentation.path, presentation.is_video)

    def request_edit(self) -> None:
        presentation = self._actionable_presentation(capability="can_edit")
        if presentation is None:
            return
        self.hide_info_panel(refresh_presentation=True)
        self.edit_requested.emit(presentation.path)

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
        presentation = self._actionable_presentation(capability="can_share")
        return presentation.path if presentation is not None else None

    def refresh_current(self) -> None:
        self._refresh_presentation()

    def _refresh_presentation(
        self,
        *,
        render_change_already_versioned: bool = False,
        dto_override: AssetDTO | None = None,
    ) -> None:
        row = self.current_row.value
        if row is None or row < 0:
            return
        dto = dto_override or self._store.asset_at(row)
        if dto is None:
            return
        presentation = self._build_presentation(
            row,
            dto,
            request_generation=self._request_generation,
        )
        previous = self.presentation.value
        if (
            isinstance(previous, DetailPresentation)
            and previous.render_key != presentation.render_key
            and not render_change_already_versioned
        ):
            # A store refresh normally changes only row/chrome metadata.  If a
            # real render input changed, give it a fresh generation so a late
            # result from the old source revision cannot be accepted as current.
            self._request_generation = max(
                int(self._request_generation),
                int(previous.request_generation),
            ) + 1
            presentation = replace(
                presentation,
                request_generation=self._request_generation,
            )
            emit_detail_event(
                "render_input_changed",
                generation=self._request_generation,
                asset_id=presentation.asset_id,
            )
        if presentation == previous:
            return
        self.presentation.value = presentation
        self.presentation_changed.emit(presentation)

    def _handle_store_changed(self) -> None:
        snapshot = self._media_selection_snapshot()
        presentation = self.presentation.value
        emit_detail_event(
            "detail_store_refresh",
            generation=self._request_generation,
            current_row=snapshot.row,
            selection_state=snapshot.state.value,
            has_current_source=isinstance(snapshot.path, Path),
            presentation_row=(
                presentation.row if isinstance(presentation, DetailPresentation) else None
            ),
            presentation_generation=(
                presentation.request_generation
                if isinstance(presentation, DetailPresentation)
                else None
            ),
        )
        # Selection transitions arrive through MediaSelectionSession's atomic
        # snapshot event. Store refreshes only update chrome/metadata for the
        # already reconciled resolved asset.
        if not snapshot.is_resolved:
            return
        if not self._snapshot_matches_current_presentation(snapshot):
            return
        self._refresh_presentation()

    def _handle_row_changed(self, row: int) -> None:
        current_row = self.current_row.value
        if current_row == row:
            self._refresh_presentation()

    def _handle_row_loaded(self, row: int) -> None:
        if self._pending_show_row == row:
            self._request_selection_row(row)

    def _handle_navigation_requested(self, row: int) -> None:
        self.show_row(row)

    def _handle_selection_changed(
        self,
        snapshot: MediaSelectionSnapshot,
        reason: MediaSelectionChangeReason,
        dto_hint: AssetDTO | None = None,
    ) -> None:
        """Atomically reconcile Detail state from the Session source of truth."""

        if not isinstance(snapshot, MediaSelectionSnapshot):
            return
        if snapshot.version <= self._last_selection_version:
            return
        self._last_selection_version = snapshot.version
        self._selection_snapshot = snapshot
        self.selection_state.value = snapshot.state
        self.current_row.value = snapshot.row if snapshot.row is not None else -1
        self.current_path.value = snapshot.path

        if snapshot.state in {
            MediaSelectionState.ANCHOR_RESOLVING,
            MediaSelectionState.ANCHOR_UNRESOLVED,
            MediaSelectionState.FALLBACK_PENDING,
        }:
            self._publish_pending_presentation(snapshot)
            return
        if snapshot.state is MediaSelectionState.NONE:
            self._pending_show_row = None
            self._publish_disabled_presentation(snapshot)
            return
        if not snapshot.is_resolved or snapshot.path is None:
            return

        row = snapshot.row
        if row is None:
            return
        dto = dto_hint or self._store.asset_at(row)
        if (
            dto is None
            or dto.abs_path != snapshot.path
            or (snapshot.asset_id is not None and str(dto.id) != snapshot.asset_id)
        ):
            emit_detail_event(
                "selection_snapshot_dto_mismatch",
                generation=self._request_generation,
                asset_id=snapshot.asset_id,
                row=row,
            )
            return

        self._pending_show_row = None
        if reason is MediaSelectionChangeReason.USER_SELECTED:
            self._publish_user_selection(snapshot, dto)
        else:
            self._publish_passive_selection(snapshot, dto)

    def _publish_pending_presentation(
        self,
        snapshot: MediaSelectionSnapshot,
    ) -> None:
        presentation = self.presentation.value
        if (
            not isinstance(presentation, DetailPresentation)
            or snapshot.path is None
            or presentation.path != snapshot.path
            or (
                snapshot.asset_id is not None
                and str(presentation.asset_id) != snapshot.asset_id
            )
        ):
            return
        capabilities = {"can_toggle_favorite": False}
        if snapshot.state in {
            MediaSelectionState.ANCHOR_UNRESOLVED,
            MediaSelectionState.FALLBACK_PENDING,
        }:
            capabilities.update(
                can_edit=False,
                can_rotate=False,
                can_share=False,
            )
        pending_presentation = replace(presentation, row=-1, **capabilities)
        self._publish_presentation_if_changed(pending_presentation)

    def _publish_disabled_presentation(
        self,
        snapshot: MediaSelectionSnapshot,
    ) -> None:
        del snapshot
        presentation = self.presentation.value
        if not isinstance(presentation, DetailPresentation):
            return
        self._publish_presentation_if_changed(
            replace(
                presentation,
                row=-1,
                can_edit=False,
                can_rotate=False,
                can_share=False,
                can_toggle_favorite=False,
            )
        )

    def _publish_presentation_if_changed(
        self,
        presentation: DetailPresentation,
    ) -> None:
        if presentation == self.presentation.value:
            return
        self.presentation.value = presentation
        self.presentation_changed.emit(presentation)

    def _media_selection_snapshot(self) -> MediaSelectionSnapshot:
        getter = getattr(self._media_session, "selection_snapshot", None)
        if callable(getter):
            snapshot = getter()
            if isinstance(snapshot, MediaSelectionSnapshot):
                return snapshot

        if self._selection_snapshot.version > 0:
            return self._selection_snapshot

        state = self._media_selection_state()
        row_value = getattr(self._media_session, "current_row", lambda: -1)()
        row = row_value if isinstance(row_value, int) and row_value >= 0 else None
        path_value = getattr(self._media_session, "current_source", lambda: None)()
        path = path_value if isinstance(path_value, Path) else self.current_path.value
        presentation = self.presentation.value
        asset_id = (
            str(presentation.asset_id)
            if isinstance(presentation, DetailPresentation)
            and isinstance(path, Path)
            and presentation.path == path
            else None
        )
        return MediaSelectionSnapshot(
            version=self._last_selection_version,
            state=state,
            row=row,
            path=path if isinstance(path, Path) else None,
            asset_id=asset_id,
        )

    def _snapshot_matches_current_presentation(
        self,
        snapshot: MediaSelectionSnapshot,
    ) -> bool:
        presentation = self.presentation.value
        return bool(
            isinstance(presentation, DetailPresentation)
            and snapshot.path == presentation.path
            and (
                not snapshot.is_resolved
                or snapshot.row == presentation.row
            )
            and (
                snapshot.asset_id is None
                or snapshot.asset_id == str(presentation.asset_id)
            )
        )

    def _actionable_presentation(
        self,
        *,
        capability: str,
        require_resolved: bool = False,
    ) -> DetailPresentation | None:
        snapshot = self._media_selection_snapshot()
        if snapshot.state in {
            MediaSelectionState.NONE,
            MediaSelectionState.ANCHOR_UNRESOLVED,
            MediaSelectionState.FALLBACK_PENDING,
        }:
            return None
        if require_resolved and not snapshot.is_resolved:
            return None
        presentation = self.presentation.value
        if (
            not isinstance(presentation, DetailPresentation)
            or snapshot.path != presentation.path
            or (
                snapshot.asset_id is not None
                and snapshot.asset_id != str(presentation.asset_id)
            )
            or (
                snapshot.is_resolved
                and snapshot.row != presentation.row
            )
            or not bool(getattr(presentation, capability, False))
        ):
            return None
        return presentation

    def _media_selection_state(self) -> MediaSelectionState:
        getter = getattr(self._media_session, "selection_state", None)
        if callable(getter):
            state = getter()
            if isinstance(state, MediaSelectionState):
                return state
            try:
                return MediaSelectionState(str(state))
            except ValueError:
                pass
        row = self.current_row.value
        if isinstance(row, int) and row >= 0:
            return MediaSelectionState.RESOLVED
        return MediaSelectionState.NONE

    def _handle_restore_requested(self, request: object) -> None:
        if not isinstance(request, MediaRestoreRequest):
            return
        self.restore_after_adjustment(
            request.path,
            request.reason,
            restore_request=request,
        )

    def _handle_adjustments_committed(self, path: object, reason: str) -> None:
        if not isinstance(path, Path) or reason in {"edit_done", "rotate"}:
            return
        self.restore_after_adjustment(path, reason)

    def _build_presentation(
        self,
        row: int,
        dto: AssetDTO,
        *,
        request_generation: int | None = None,
    ) -> DetailPresentation:
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
        location = self._resolve_stored_location(dto)
        if isinstance(location, str) and location.strip():
            info["location"] = location.strip()
        live_motion_rel, live_motion_abs = self._resolve_live_motion(
            dto,
            allow_fallback_scan=False,
        )
        video_adjustments: dict[str, Any] | None = None
        video_trim_range_ms: tuple[int, int] | None = None
        video_adjusted_preview = False
        restore_request = self._pending_restore_requests.pop(dto.abs_path, None)
        video_duration_hint = (
            self._resolve_video_duration(dto, restore_request)
            if dto.is_video
            else None
        )
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
            can_toggle_favorite=self._asset_state_service is not None,
            info_panel_visible=self._info_panel_visible,
            live_motion_rel=live_motion_rel,
            live_motion_abs=live_motion_abs,
            video_adjustments=video_adjustments,
            video_trim_range_ms=video_trim_range_ms,
            video_adjusted_preview=video_adjusted_preview,
            reload_token=self._presentation_reload_token,
            request_generation=(
                self._request_generation
                if request_generation is None
                else int(request_generation)
            ),
            video_duration_hint=video_duration_hint,
            source_identity=AssetSourceIdentity.from_info(dto.abs_path, info),
        )

    @staticmethod
    def _resolve_stored_location(dto: AssetDTO) -> Optional[str]:
        """Return only indexed location data; never initialise geocoding here."""

        metadata = dto.metadata or {}
        location = metadata.get("location") or metadata.get("place")
        if isinstance(location, str) and location.strip():
            return location.strip()
        components = [metadata.get("city"), metadata.get("state"), metadata.get("country")]
        normalized = [str(item).strip() for item in components if item]
        return ", ".join(normalized) if normalized else None

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

    def _resolve_live_motion(
        self,
        dto: AssetDTO,
        *,
        allow_fallback_scan: bool = True,
    ) -> tuple[Optional[Path], Optional[Path]]:
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
        if not group_id or not allow_fallback_scan:
            return None, None
        for candidate_row in range(self._store.count()):
            candidate = self._store.asset_at(candidate_row)
            if candidate is None or not candidate.is_video:
                continue
            candidate_group = (candidate.metadata or {}).get("live_photo_group_id")
            if candidate_group == group_id:
                return candidate.rel_path, candidate.abs_path
        return None, None

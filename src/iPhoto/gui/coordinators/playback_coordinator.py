"""Coordinator that binds detail widgets to DetailViewModel presentation."""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import (
    QLocale,
    QModelIndex,
    QObject,
    QRunnable,
    QThread,
    QThreadPool,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import QAction, QColor, QPalette

from iPhoto.application.ports import EditServicePort, LocationWriteJobRecord, MapRuntimePort
from iPhoto.config import PLAY_ASSET_DEBOUNCE_MS
from iPhoto.gui.coordinators.view_router import ViewRouter
from iPhoto.gui.detail_pipeline import (
    AssetSourceIdentity,
    DetailPrefetchDescriptor,
    DetailRenderTransaction,
    PlaybackAsyncToken,
    VideoPresentationState,
)
from iPhoto.gui.detail_profile import emit_detail_event, log_detail_profile
from iPhoto.gui.detail_render_coordinator import (
    DetailRenderCoordinator,
    DetailRenderState,
    DetailSurfacePresentationResult,
)
from iPhoto.gui.i18n import tr
from iPhoto.gui.ui.controllers.edit_zoom_handler import EditZoomHandler
from iPhoto.gui.ui.controllers.header_controller import HeaderController
from iPhoto.gui.ui.icons import load_icon
from iPhoto.gui.ui.media.media_selection_session import MediaSelectionState
from iPhoto.gui.ui.models.proxy_mapping import map_from_root_source, map_to_root_source
from iPhoto.gui.ui.widgets import dialogs
from iPhoto.gui.viewmodels.detail_viewmodel import DetailPresentation, DetailViewModel
from iPhoto.utils.ffmpeg import probe_video_rotation_info
from iPhoto.utils.geocoding import resolve_location_name

if TYPE_CHECKING:
    from iPhoto.utils.settings import Settings
    from PySide6.QtWidgets import QPushButton, QSlider, QToolButton, QWidget

    from iPhoto.application.services.location_assignment_service import LocationAssignment
    from iPhoto.events.bus import EventBus
    from iPhoto.gui.coordinators.navigation_coordinator import NavigationCoordinator
    from iPhoto.gui.services.location_file_write_queue import LocationFileWriteQueue
    from iPhoto.gui.services.location_search_controller import LocationSearchController
    from iPhoto.gui.ui.controllers.player_view_controller import PlayerViewController
    from iPhoto.gui.ui.media import MediaAdjustmentCommitter
    from iPhoto.gui.ui.widgets.face_name_overlay import FaceNameOverlayWidget
    from iPhoto.gui.ui.widgets.filmstrip_view import FilmstripView
    from iPhoto.gui.ui.widgets.info_panel import InfoPanel
    from iPhoto.gui.ui.widgets.player_bar import PlayerBar
    from iPhoto.gui.ui.widgets.recognition_annotations import RecognitionIdentitySuggestion
    from iPhoto.gui.viewmodels.gallery_list_model_adapter import GalleryListModelAdapter
    from iPhoto.library.runtime_controller import LibraryRuntimeController
    from iPhoto.people.service import PeopleService
    from iPhoto.pets.service import PetService

LOGGER = logging.getLogger(__name__)

# Lightweight test seams.  Production resolves these optional classes at the
# point of use, keeping their dependency graphs off the startup import path.
LocationAssignmentService = None
IndexStoreLocationAssignmentRepository = None
ManualFaceAddWorker = None


def SessionMapRuntimeService():  # noqa: N802 - compatibility factory name
    from iPhoto.infrastructure.services.map_runtime_service import (
        SessionMapRuntimeService as RuntimeService,
    )

    return RuntimeService()

_INFO_PANEL_METADATA_CACHE_MAX = 200
_LOCATION_EXTENSION_PROMPT = "Install the map extension to use Assign a Location."
_LOCATION_EXIFTOOL_LIMITED_TITLE = "功能受限"
_LOCATION_EXIFTOOL_LIMITED_MESSAGE = (
    "地点已保存到本机图库数据库。\n\n"
    "应用当前环境未找到或无法访问 ExifTool，暂时无法把 GPS 信息写入原始照片/视频文件。"
    "请确认 ExifTool 已安装并可被应用访问。"
)
_LOCATION_FILE_WRITE_LIMITED_TITLE = "原文件写入失败"
_LOCATION_FILE_WRITE_LIMITED_MESSAGE_TEMPLATE = (
    "地点已保存到本机图库数据库。\n\n"
    "GPS 信息未能写入原始照片/视频文件：{reason}"
)
_LOCATION_VIDEO_WRITE_PLACEHOLDER = "Writing data, please wait..."


def _location_video_write_placeholder() -> str:
    return tr("PlaybackCoordinator", _LOCATION_VIDEO_WRITE_PLACEHOLDER)


class _RecognitionOverlaySignals(QObject):
    ready = Signal(int, int, object)
    failed = Signal(int, object)


class _RecognitionOverlayWorker(QRunnable):
    def __init__(
        self,
        *,
        request_generation: int,
        still_generation: int,
        asset_id: str,
        query_service: object,
        signals: _RecognitionOverlaySignals,
    ) -> None:
        super().__init__()
        self._request_generation = request_generation
        self._still_generation = still_generation
        self._asset_id = asset_id
        self._query_service = query_service
        self._signals = signals

    def run(self) -> None:  # pragma: no cover - worker thread
        try:
            snapshot = self._query_service.load_overlay(self._asset_id)
        except Exception as exc:  # noqa: BLE001 - async optional-domain boundary
            self._signals.failed.emit(self._request_generation, exc)
            return
        self._signals.ready.emit(
            self._request_generation,
            self._still_generation,
            snapshot,
        )


class _VideoPreparationSignals(QObject):
    ready = Signal(object, object)
    failed = Signal(object, object)


class _VideoPreparationWorker(QRunnable):
    """Read edit and rotation state without blocking the GUI thread."""

    def __init__(
        self,
        *,
        presentation: DetailPresentation,
        token: PlaybackAsyncToken,
        edit_service_getter: Callable[[], EditServicePort | None] | None,
        signals: _VideoPreparationSignals,
    ) -> None:
        super().__init__()
        self._presentation = presentation
        self._token = token
        self._edit_service_getter = edit_service_getter
        self._signals = signals

    def run(self) -> None:  # pragma: no cover - worker thread
        presentation = self._presentation
        generation = int(presentation.request_generation)
        try:
            adjustments = dict(presentation.video_adjustments or {})
            trim_range = presentation.video_trim_range_ms
            adjusted_preview = bool(presentation.video_adjusted_preview)
            edit_service = (
                self._edit_service_getter()
                if self._edit_service_getter is not None
                else None
            )
            if edit_service is not None:
                duration = (
                    presentation.video_duration_hint
                    if presentation.video_duration_hint is not None
                    else presentation.info.get("dur")
                )
                try:
                    duration_hint = float(duration) if duration else None
                except (TypeError, ValueError):
                    duration_hint = None
                edit_state = edit_service.describe_adjustments(
                    presentation.path,
                    duration_hint=duration_hint,
                )
                adjusted_preview = bool(edit_state.adjusted_preview)
                trim_range = edit_state.trim_range_ms
                adjustments = dict(
                    edit_state.resolved_adjustments
                    if adjusted_preview
                    else (edit_state.raw_adjustments or {})
                )
            cached_rotation = presentation.info.get("video_rotation_cw")
            cached_linux_hint = presentation.info.get("video_linux_180_hint")
            if cached_rotation is None or cached_linux_hint is None:
                rotation, raw_w, raw_h, linux_hint = probe_video_rotation_info(
                    presentation.path
                )
            else:
                rotation = int(cached_rotation) % 360
                raw_w = int(presentation.info.get("w") or 0)
                raw_h = int(presentation.info.get("h") or 0)
                linux_hint = bool(cached_linux_hint)
            state = VideoPresentationState(
                request_generation=generation,
                adjustments=adjustments,
                trim_range_ms=trim_range,
                adjusted_preview=adjusted_preview,
                rotation_cw=rotation,
                raw_width=raw_w,
                raw_height=raw_h,
                linux_180_hint=linux_hint,
            )
        except Exception as exc:  # noqa: BLE001 - codec/sidecar boundary
            self._signals.failed.emit(self._token, exc)
            return
        self._signals.ready.emit(self._token, state)


class _DeferredLocationSignals(QObject):
    ready = Signal(object, str)


class _DeferredLocationWorker(QRunnable):
    def __init__(self, token: PlaybackAsyncToken, gps: dict, signals) -> None:
        super().__init__()
        self._token = token
        self._gps = dict(gps)
        self._signals = signals

    def run(self) -> None:  # pragma: no cover - worker thread
        location = resolve_location_name(self._gps)
        if location:
            self._signals.ready.emit(self._token, location)


class PlaybackCoordinator(QObject):
    """Bind detail widgets to the current presentation from DetailViewModel."""

    assetChanged = Signal(int)

    def __init__(
        self,
        player_bar: PlayerBar,
        player_view: PlayerViewController,
        router: ViewRouter,
        asset_model: GalleryListModelAdapter,
        detail_vm: DetailViewModel,
        adjustment_committer: MediaAdjustmentCommitter,
        zoom_slider: QSlider,
        zoom_in_button: QToolButton,
        zoom_out_button: QToolButton,
        zoom_widget: QWidget,
        favorite_button: QToolButton,
        info_button: QToolButton,
        rotate_button: QToolButton,
        edit_button: QPushButton,
        share_button: QToolButton,
        filmstrip_view: FilmstripView,
        toggle_filmstrip_action: QAction,
        settings: Settings,
        header_controller: HeaderController | None = None,
        face_name_overlay: FaceNameOverlayWidget | None = None,
        people_service: PeopleService | None = None,
        pet_service: PetService | None = None,
        people_dashboard_refresh_callback: Callable[[], None] | None = None,
        library_manager: LibraryRuntimeController | None = None,
        location_session_invalidator: Callable[[], None] | None = None,
        map_runtime: MapRuntimePort | None = None,
        event_bus: EventBus | None = None,
        location_write_queue: LocationFileWriteQueue | None = None,
        edit_service_getter: Callable[[], EditServicePort | None] | None = None,
        library_epoch_getter: Callable[[], int] | None = None,
    ) -> None:
        super().__init__()
        self._player_bar = player_bar
        self._player_view = player_view
        self._router = router
        self._asset_model = asset_model
        self._detail_vm = detail_vm
        self._adjustment_committer = adjustment_committer
        set_preparation_invalidator = getattr(
            adjustment_committer,
            "set_adjustment_preparation_invalidator",
            None,
        )
        if callable(set_preparation_invalidator):
            set_preparation_invalidator(player_view.invalidate_adjustment_preparation)

        self._zoom_slider = zoom_slider
        self._zoom_in = zoom_in_button
        self._zoom_out = zoom_out_button
        self._zoom_widget = zoom_widget

        self._favorite_button = favorite_button
        self._info_button = info_button
        self._rotate_button = rotate_button
        self._edit_button = edit_button
        self._share_button = share_button

        self._filmstrip_view = filmstrip_view
        self._toggle_filmstrip_action = toggle_filmstrip_action
        self._settings = settings
        self._header_controller = header_controller
        self._face_name_overlay = face_name_overlay
        self._people_service = people_service
        self._pet_service = pet_service
        self._recognition_query_service = None
        self._people_library_root = self._service_library_root(people_service)
        self._pet_library_root = self._service_library_root(pet_service)
        self._people_dashboard_refresh_callback = people_dashboard_refresh_callback
        self._library_manager = library_manager
        self._location_session_invalidator = location_session_invalidator
        self._map_runtime = map_runtime or getattr(library_manager, "map_runtime", None)
        self._event_bus = event_bus
        self._location_write_queue = location_write_queue
        self._edit_service_getter = edit_service_getter
        self._library_epoch_getter = library_epoch_getter
        self._library_epoch = self._read_library_epoch()
        self._asset_generation = 0
        self._active_async_token: PlaybackAsyncToken | None = None
        self._pending_video_token: PlaybackAsyncToken | None = None
        self._pending_location_token: PlaybackAsyncToken | None = None

        self._is_playing = False
        self._navigation: NavigationCoordinator | None = None
        self._info_panel: InfoPanel | None = None
        self._active_live_motion: Path | None = None
        self._active_live_still: Path | None = None
        self._active_live_asset_id: str = ""
        self._active_live_media_generation: int | None = None
        self._resume_after_transition = False
        self._trim_in_ms = 0
        self._trim_out_ms = 0
        self._current_presentation: DetailPresentation | None = None
        self._info_panel_metadata_cache: dict[str, dict[str, Any]] = {}
        self._info_panel_metadata_inflight: set[str] = set()
        self._info_panel_metadata_attempted: set[str] = set()
        self._play_profile_started_at: float | None = None
        self._play_profile_row: int | None = None
        self._requested_play_row: int | None = None
        self._detail_request_generation = 0
        self._detail_render_coordinator = DetailRenderCoordinator(self)
        self._detail_render_transaction: DetailRenderTransaction | None = None
        self._manual_face_add_inflight = False
        self._manual_face_inflight_asset_id: str | None = None
        self._manual_face_pending_merge_target: str | None = None
        self._pending_manual_face_annotations: dict[str, list[object]] = {}
        self._pending_manual_face_sequence = 0
        self._location_search_controller: "LocationSearchController | None" = None
        self._location_assign_inflight = False
        self._location_assign_path: Path | None = None
        self._confirmed_location_metadata: dict[Path, dict[str, Any]] = {}
        self._deferred_locations: dict[Path, str] = {}
        self._location_released_video_path: Path | None = None
        self._location_released_video_was_playing = False
        self._location_released_video_position_ms: int | None = None
        self._location_video_write_inflight_paths: set[Path] = set()
        self._location_write_jobs_by_path: dict[Path, str] = {}
        if self._location_write_queue is not None:
            self._location_write_queue.writeStarted.connect(
                self._handle_location_file_write_started
            )
            self._location_write_queue.writeVerified.connect(
                self._handle_location_file_write_verified
            )
            self._location_write_queue.writeFailed.connect(
                self._handle_location_file_write_failed
            )

        self._pending_play_row: int | None = None
        self._show_face_names = False
        self._overlay_request_generation = 0
        self._presented_still_generation = 0
        self._presented_still_source: Path | None = None
        self._overlay_signals = _RecognitionOverlaySignals(self)
        self._overlay_signals.ready.connect(self._on_recognition_overlay_ready)
        self._overlay_signals.failed.connect(self._on_recognition_overlay_failed)
        self._overlay_pool = QThreadPool(self)
        self._overlay_pool.setMaxThreadCount(1)
        self._overlay_pool.setThreadPriority(QThread.Priority.LowPriority)
        self._video_prepare_signals = _VideoPreparationSignals(self)
        self._video_prepare_signals.ready.connect(self._on_video_preparation_ready)
        self._video_prepare_signals.failed.connect(self._on_video_preparation_failed)
        self._video_prepare_pool = QThreadPool(self)
        self._video_prepare_pool.setMaxThreadCount(2)
        self._deferred_location_signals = _DeferredLocationSignals(self)
        self._deferred_location_signals.ready.connect(self._on_deferred_location_ready)
        self._deferred_location_pool = QThreadPool(self)
        self._deferred_location_pool.setMaxThreadCount(1)
        self._deferred_location_pool.setThreadPriority(QThread.Priority.LowPriority)
        self._play_debounce = QTimer(self)
        self._play_debounce.setSingleShot(True)
        self._play_debounce.setInterval(PLAY_ASSET_DEBOUNCE_MS)
        self._play_debounce.timeout.connect(self._execute_pending_play)

        self._connect_signals()
        self._setup_zoom_handler()
        self._restore_filmstrip_preference()

    def set_navigation_coordinator(self, nav: NavigationCoordinator) -> None:
        self._navigation = nav

    def _render_transaction_coordinator(self) -> DetailRenderCoordinator:
        coordinator = getattr(self, "_detail_render_coordinator", None)
        if coordinator is None:
            coordinator = DetailRenderCoordinator()
            self._detail_render_coordinator = coordinator
        return coordinator

    def set_people_service(self, service: PeopleService | None) -> None:
        self._people_service = service
        self._people_library_root = self._service_library_root(service)
        self._refresh_face_name_overlay_for_current_presentation()

    def set_pet_service(self, service: PetService | None) -> None:
        self._pet_service = service
        self._pet_library_root = self._service_library_root(service)
        self._refresh_face_name_overlay_for_current_presentation()

    def set_recognition_query_service(self, service: object | None) -> None:
        self._invalidate_overlay_requests(clear=True)
        self._recognition_query_service = service

    def set_recognition_merge_service(self, service: object | None) -> None:
        self._recognition_merge_service = service

    def rebind_library(
        self,
        library_epoch: int | None = None,
        *,
        session_changed: bool = True,
    ) -> None:
        """Invalidate library-scoped media preparation and decoded-frame caches."""

        if not session_changed:
            return
        self._library_epoch = (
            self._read_library_epoch()
            if library_epoch is None
            else max(0, int(library_epoch))
        )
        self._asset_generation = int(getattr(self, "_asset_generation", 0)) + 1
        self._detail_request_generation = int(
            getattr(self, "_detail_request_generation", 0)
        ) + 1
        self._active_async_token = None
        self._pending_video_token = None
        self._pending_location_token = None
        self._invalidate_overlay_requests(clear=True)
        for pool_name in ("_video_prepare_pool", "_deferred_location_pool"):
            pool = getattr(self, pool_name, None)
            if pool is not None:
                pool.clear()
        deferred_locations = getattr(self, "_deferred_locations", None)
        if deferred_locations is not None:
            deferred_locations.clear()
        render_coordinator = self._render_transaction_coordinator()
        render_coordinator.reset()
        self._detail_render_transaction = None
        self._current_presentation = None
        self._active_live_motion = None
        self._active_live_still = None
        self._active_live_asset_id = ""
        self._active_live_media_generation = None
        self._presented_still_generation = 0
        self._presented_still_source = None
        video_area = self._player_view.video_area
        video_area.stop()
        self._player_view.defer_still_updates(False)
        cancel_stills = getattr(self._player_view, "cancel_pending_image_requests", None)
        if callable(cancel_stills):
            cancel_stills()
        clear_frames = getattr(self._player_view, "clear_frame_cache", None)
        if callable(clear_frames):
            clear_frames()
        self._player_view.show_placeholder()
        self._player_bar.setEnabled(False)
        self._is_playing = False
        self._update_header(None)
        info_panel = getattr(self, "_info_panel", None)
        if info_panel is not None:
            info_panel.close()
        self._clear_info_panel_metadata_state()
        self._clear_confirmed_location_metadata()

    def _read_library_epoch(self) -> int:
        getter = getattr(self, "_library_epoch_getter", None)
        if getter is None:
            return max(0, int(getattr(self, "_library_epoch", 0)))
        try:
            return max(0, int(getter()))
        except (TypeError, ValueError):
            return max(0, int(getattr(self, "_library_epoch", 0)))

    def _token_for_presentation(
        self,
        presentation: DetailPresentation,
    ) -> PlaybackAsyncToken:
        identity = presentation.source_identity
        if identity is None or identity.path != presentation.path:
            identity = AssetSourceIdentity.from_info(
                presentation.path,
                presentation.info if identity is None else None,
            )
        return PlaybackAsyncToken.create(
            library_epoch=int(getattr(self, "_library_epoch", 0)),
            asset_generation=int(getattr(self, "_asset_generation", 0)),
            asset_id=presentation.asset_id,
            source_identity=identity,
        )

    @staticmethod
    def _transaction_for_presentation(
        presentation: DetailPresentation,
    ) -> DetailRenderTransaction:
        identity = presentation.source_identity or AssetSourceIdentity.from_info(
            presentation.path,
            presentation.info,
        )
        media_kind = "video" if presentation.is_video else "image"
        if (
            media_kind == "image"
            and presentation.is_live
            and presentation.live_motion_abs is not None
        ):
            media_kind = "live_motion"
        return DetailRenderTransaction(
            generation=presentation.request_generation,
            asset_id=presentation.asset_id,
            media_kind=media_kind,
            source_identity=identity,
        )

    def _async_token_is_current(
        self,
        token: object,
        *,
        expected_path: Path | None = None,
    ) -> bool:
        if not isinstance(token, PlaybackAsyncToken):
            return False
        active = getattr(self, "_active_async_token", None)
        if active is None:
            return False
        if self._read_library_epoch() != int(getattr(self, "_library_epoch", -1)):
            return False
        if token.library_epoch != active.library_epoch:
            return False
        if token.asset_generation != active.asset_generation:
            return False
        if token.asset_id != active.asset_id:
            return False
        if expected_path is not None and token.source_path != Path(expected_path):
            return False
        if token.source_path == active.source_path and (
            token.source_revision != active.source_revision
        ):
            return False
        return True

    def set_info_panel(self, panel: InfoPanel) -> None:
        self._info_panel = panel
        panel.dismissed.connect(self._handle_info_panel_dismissed)
        panel.manualFaceAddRequested.connect(self._handle_manual_face_add_requested)
        panel.faceDeleteRequested.connect(self._handle_info_panel_face_delete_requested)
        panel.faceMoveRequested.connect(self._handle_info_panel_face_move_requested)
        panel.faceMoveToNewPersonRequested.connect(
            self._handle_info_panel_face_move_to_new_person_requested
        )
        panel.locationQueryChanged.connect(self._handle_location_query_changed)
        panel.locationConfirmRequested.connect(self._handle_location_confirm_requested)
        if getattr(self, "_location_search_controller", None) is not None and getattr(
            self,
            "_map_runtime",
            None,
        ) is not None:
            self._refresh_location_extension_state()

    def set_location_write_queue(self, queue: LocationFileWriteQueue | None) -> None:
        previous = getattr(self, "_location_write_queue", None)
        if previous is queue:
            return
        if previous is not None:
            for signal_name, handler in (
                ("writeStarted", self._handle_location_file_write_started),
                ("writeVerified", self._handle_location_file_write_verified),
                ("writeFailed", self._handle_location_file_write_failed),
            ):
                try:
                    getattr(previous, signal_name).disconnect(handler)
                except (RuntimeError, TypeError):
                    pass
        self._location_write_queue = queue
        if queue is not None:
            queue.writeStarted.connect(self._handle_location_file_write_started)
            queue.writeVerified.connect(self._handle_location_file_write_verified)
            queue.writeFailed.connect(self._handle_location_file_write_failed)

    def set_people_library_root(self, library_root: Path | None) -> None:
        self._invalidate_overlay_requests(clear=True)
        people_service = getattr(self, "_people_service", None)
        service_matches_root = self._service_library_root(people_service) == library_root
        if not service_matches_root:
            bound_people_service = getattr(self._library_manager, "people_service", None)
            if self._service_library_root(bound_people_service) == library_root:
                self._people_service = bound_people_service
            else:
                self._people_service = None
        self._people_library_root = library_root
        self._refresh_face_name_overlay_for_current_presentation()

    @staticmethod
    def _service_library_root(service: object | None) -> Path | None:
        getter = getattr(service, "library_root", None)
        if not callable(getter):
            return None
        try:
            root = getter()
        except Exception:  # noqa: BLE001 - optional service boundary
            return None
        return Path(root) if root is not None else None

    def _ensure_location_search_controller(self):
        controller = getattr(self, "_location_search_controller", None)
        if controller is not None:
            return controller
        factory = getattr(self, "_location_search_controller_factory", None)
        if factory is None:
            raise RuntimeError("Location/Info domain has not been initialised")
        controller = factory(self)
        controller.suggestionsReady.connect(self._handle_location_suggestions_ready)
        controller.searchFailed.connect(self._handle_location_search_failed)
        self._location_search_controller = controller
        return controller

    def configure_location_domain(
        self,
        *,
        search_controller_factory,
        assignment_service_factory,
        assignment_repository_factory,
        metadata_worker_factory,
    ) -> None:
        self._location_search_controller_factory = search_controller_factory
        self._location_assignment_service_factory = assignment_service_factory
        self._location_assignment_repository_factory = assignment_repository_factory
        self._info_metadata_worker_factory = metadata_worker_factory

    def configure_recognition_domain(self, *, manual_face_worker_factory) -> None:
        self._manual_face_worker_factory = manual_face_worker_factory

    def set_map_runtime(self, map_runtime: MapRuntimePort | None) -> None:
        """Bind the current session map runtime capability surface."""

        previous_package_root = self._map_runtime_package_root()
        self._map_runtime = map_runtime or getattr(self._library_manager, "map_runtime", None)
        if self._map_runtime_package_root() != previous_package_root:
            self._reset_location_search_service(clear_cache=True)
        if self._info_panel is None or self._info_panel.current_rel() is None:
            return
        capabilities = self._map_runtime_capabilities()
        location_enabled = self._refresh_location_extension_state()
        self._info_panel.set_location_capability(
            enabled=location_enabled,
            preview_enabled=self._info_panel_preview_enabled(
                capabilities,
                location_enabled=location_enabled,
            ),
            fallback_text=_LOCATION_EXTENSION_PROMPT,
        )

    def set_face_name_display_enabled(self, enabled: bool) -> None:
        self._show_face_names = bool(enabled)
        if not self._show_face_names:
            self._invalidate_overlay_requests(clear=True)
            return
        self._refresh_face_name_overlay_for_current_presentation()

    def current_row(self) -> int:
        row = self._detail_vm.current_row.value
        return int(row) if isinstance(row, int) else -1

    def suspend_playback_for_transition(self) -> bool:
        resume_after = self._is_playing
        self._resume_after_transition = resume_after
        if resume_after:
            self._player_view.video_area.pause()
        return resume_after

    def resume_playback_after_transition(self) -> None:
        if not self._resume_after_transition:
            return
        self._resume_after_transition = False
        self._player_view.video_area.play()

    def prepare_fullscreen_asset(self) -> bool:
        if self._asset_model.rowCount() <= 0:
            return False
        current_row = self.current_row()
        target_row = current_row if current_row >= 0 else 0
        if not self._router.is_detail_view_active():
            self.play_asset(target_row)
            return True
        presentation = getattr(self, "_current_presentation", None)
        if presentation is None:
            self.play_asset(target_row)
            return True
        if presentation.is_video or getattr(self, "_active_live_motion", None):
            return True

        has_session = getattr(self._player_view, "has_current_render_session", None)
        if callable(has_session) and has_session(presentation.path):
            return True

        expected = self._transaction_for_presentation(presentation)
        snapshot = self._render_transaction_coordinator().snapshot
        if (
            snapshot is not None
            and snapshot.transaction == expected
            and snapshot.state
            in {
                DetailRenderState.CREATED,
                DetailRenderState.ROUTED,
                DetailRenderState.PREPARING,
            }
        ):
            # Fullscreen will emit viewportMetricsChanged; keep the one active
            # decode rather than starting a competing request.
            return True

        emit_detail_event(
            "fullscreen_still_recovery",
            generation=presentation.request_generation,
            asset_id=presentation.asset_id,
            state=snapshot.state.value if snapshot is not None else "missing",
        )
        selection_state_property = getattr(self._detail_vm, "selection_state", None)
        selection_state = getattr(selection_state_property, "value", None)
        if selection_state in {
            MediaSelectionState.ANCHOR_RESOLVING,
            MediaSelectionState.ANCHOR_UNRESOLVED,
            MediaSelectionState.FALLBACK_PENDING,
        }:
            recover = getattr(self._detail_vm, "recover_current_presentation", None)
            return bool(recover()) if callable(recover) else False
        self._detail_vm.show_current()
        return True

    def show_placeholder_in_viewer(self) -> None:
        self._player_view.show_placeholder()
        self._hide_face_name_overlay(clear_annotations=True)

    def _connect_signals(self) -> None:
        self._player_bar.playPauseRequested.connect(self.toggle_playback)
        self._player_bar.scrubStarted.connect(self._on_scrub_start)
        self._player_bar.scrubFinished.connect(self._on_scrub_end)
        self._player_bar.seekRequested.connect(self._on_seek)

        self._player_view.liveReplayRequested.connect(self.replay_live_photo)
        still_presented = getattr(self._player_view, "stillFramePresented", None)
        if still_presented is not None:
            still_presented.connect(self._on_still_frame_presented)
        still_failed = getattr(self._player_view, "imageLoadingFailed", None)
        if still_failed is not None:
            still_failed.connect(self._on_still_loading_failed)
        self._player_view.video_area.playbackStateChanged.connect(self._sync_playback_state)
        self._player_view.video_area.playbackFinished.connect(self._handle_playback_finished)
        self._player_view.video_area.durationChanged.connect(self._on_video_duration_changed)
        self._player_view.video_area.positionChanged.connect(self._on_video_position_changed)
        first_media_frame = getattr(self._player_view.video_area, "mediaFirstFrameReady", None)
        if first_media_frame is not None:
            first_media_frame.connect(self._on_video_first_frame_presented)

        self._detail_vm.route_requested.connect(self._handle_route_requested)
        self._detail_vm.presentation_changed.connect(self._handle_presentation_changed)
        self._detail_vm.rotate_requested.connect(self._handle_rotate_requested)
        self._detail_vm.edit_requested.connect(self._handle_edit_requested)

        self._filmstrip_view.nextItemRequested.connect(self.select_next)
        self._filmstrip_view.prevItemRequested.connect(self.select_previous)
        self._filmstrip_view.itemClicked.connect(self._on_filmstrip_clicked)
        self._toggle_filmstrip_action.toggled.connect(self._handle_filmstrip_toggled)
        rename_signal = getattr(self._face_name_overlay, "renameSubmitted", None)
        if rename_signal is not None:
            rename_signal.connect(self._handle_face_name_rename_submitted)
        unassigned_rename_signal = getattr(
            self._face_name_overlay,
            "unassignedRenameSubmitted",
            None,
        )
        if unassigned_rename_signal is not None:
            unassigned_rename_signal.connect(
                self._handle_info_panel_face_move_to_new_person_requested
            )
        existing_identity_signal = getattr(
            self._face_name_overlay,
            "existingIdentitySubmitted",
            None,
        )
        if existing_identity_signal is not None:
            existing_identity_signal.connect(
                self._handle_face_name_existing_identity_submitted
            )
        manual_signal = getattr(self._face_name_overlay, "manualFaceSubmitted", None)
        if manual_signal is not None:
            manual_signal.connect(self._handle_manual_face_submitted)

    def _setup_zoom_handler(self) -> None:
        self._zoom_handler = EditZoomHandler(
            viewer=self._player_view.image_viewer,
            zoom_in_button=self._zoom_in,
            zoom_out_button=self._zoom_out,
            zoom_slider=self._zoom_slider,
            parent=self,
        )
        self._zoom_handler.connect_controls()

    @property
    def zoom_handler(self) -> EditZoomHandler:
        """Return the zoom handler shared by detail and edit presentation modes."""
        return self._zoom_handler

    def _restore_filmstrip_preference(self) -> None:
        stored = self._settings.get("ui.show_filmstrip", True)
        if isinstance(stored, str):
            show = stored.strip().lower() in {"1", "true", "yes", "on"}
        else:
            show = bool(stored)
        self._filmstrip_view.setVisible(show)
        self._toggle_filmstrip_action.setChecked(show)

    @Slot(bool)
    def _handle_filmstrip_toggled(self, checked: bool) -> None:
        self._filmstrip_view.setVisible(checked)
        self._settings.set("ui.show_filmstrip", checked)

    @Slot(QModelIndex)
    def _on_filmstrip_clicked(self, index: QModelIndex) -> None:
        source_idx = map_to_root_source(index)
        self.play_asset(source_idx.row() if source_idx.isValid() else index.row())

    @Slot(int)
    @Slot()
    def toggle_playback(self) -> None:
        if self._is_playing:
            self._player_view.video_area.pause()
        else:
            self._player_view.video_area.play()

    @Slot(bool)
    def _sync_playback_state(self, is_playing: bool) -> None:
        self._is_playing = is_playing

    @Slot()
    def _on_scrub_start(self) -> None:
        self._player_view.video_area.pause()

    @Slot()
    def _on_scrub_end(self) -> None:
        if self._is_playing:
            self._player_view.video_area.play()

    @Slot(int)
    def _on_seek(self, position: int) -> None:
        self._player_view.video_area.seek(position + self._trim_in_ms)

    @Slot(int)
    def _on_video_duration_changed(self, duration_ms: int) -> None:
        if self._player_view.video_area.is_edit_mode_active():
            return
        trim_in_ms, trim_out_ms = self._player_view.video_area.trim_range_ms()
        self._trim_in_ms = trim_in_ms
        self._trim_out_ms = trim_out_ms
        if self._trim_out_ms > self._trim_in_ms:
            self._player_bar.set_duration(self._trim_out_ms - self._trim_in_ms)
        else:
            self._player_bar.set_duration(duration_ms)

    @Slot(int)
    def _on_video_position_changed(self, position_ms: int) -> None:
        if self._player_view.video_area.is_edit_mode_active():
            return
        self._player_bar.set_position(max(0, position_ms - self._trim_in_ms))

    def play_asset(self, row: int) -> None:
        if row < 0 or row >= self._asset_model.rowCount():
            return
        self._requested_play_row = row
        self._play_profile_started_at = time.perf_counter()
        self._play_profile_row = row
        if not self._play_debounce.isActive() and self._pending_play_row is None:
            self._dispatch_play_row(row, reason="immediate")
            self._play_debounce.start()
            return
        self._pending_play_row = row
        if not self._play_debounce.isActive():
            self._play_debounce.start()

    def prefetch_asset(self, row: int) -> bool:
        """Resolve and warm one hovered still without changing presentation."""

        descriptor_getter = getattr(
            self._asset_model,
            "detail_prefetch_descriptor",
            None,
        )
        if not callable(descriptor_getter):
            return False
        descriptor = descriptor_getter(int(row))
        return self.prefetch_descriptor(descriptor)

    def prefetch_descriptor(self, descriptor: DetailPrefetchDescriptor | None) -> bool:
        """Submit the Gallery-owned identity directly to the still scheduler."""

        if descriptor is None or descriptor.is_video:
            return False
        return bool(self._player_view.prefetch_image(descriptor))

    def _execute_pending_play(self) -> None:
        row = self._pending_play_row
        self._pending_play_row = None
        if row is None:
            return
        self._dispatch_play_row(row, reason="debounced")
        self._play_debounce.start()

    def _clear_play_profile(self, row: int | None = None) -> None:
        if row is not None and getattr(self, "_play_profile_row", None) != row:
            return
        self._play_profile_started_at = None
        self._play_profile_row = None

    def _clear_play_request_state(self) -> None:
        self._pending_play_row = None
        self._requested_play_row = None
        self._clear_play_profile()
        play_debounce = getattr(self, "_play_debounce", None)
        if play_debounce is not None:
            play_debounce.stop()

    def _dispatch_play_row(self, row: int, *, reason: str) -> None:
        if (
            getattr(self, "_play_profile_started_at", None) is not None
            and getattr(self, "_play_profile_row", None) == row
        ):
            elapsed_ms = (time.perf_counter() - self._play_profile_started_at) * 1000.0
            log_detail_profile(
                "playback",
                "play_asset.dispatch",
                elapsed_ms,
                row=row,
                reason=reason,
            )
        self._detail_vm.show_row(row)

    @Slot(str)
    def _handle_route_requested(self, view: str) -> None:
        if view == "detail":
            self._router.show_detail()
        elif view == "gallery":
            self.reset_for_gallery()
            self._router.show_gallery()

    @Slot(object)
    def _handle_edit_requested(self, _path: object) -> None:
        self._hide_face_name_overlay(clear_annotations=False)

    def handle_still_edit_finished(self, path: Path, _reason: str) -> None:
        """Restore Detail-only overlays without replaying the shared still session."""

        presentation = self._current_presentation
        if presentation is None or presentation.path != Path(path):
            return
        self._refresh_face_name_overlay_for_current_presentation()

    def _handle_presentation_changed(self, presentation: DetailPresentation) -> None:
        if getattr(self, "_requested_play_row", None) == presentation.row:
            self._requested_play_row = None
        if (
            getattr(self, "_play_profile_started_at", None) is not None
            and getattr(self, "_play_profile_row", None) == presentation.row
        ):
            elapsed_ms = (time.perf_counter() - self._play_profile_started_at) * 1000.0
            log_detail_profile(
                "playback",
                "presentation_changed",
                elapsed_ms,
                row=presentation.row,
                path=presentation.path.name,
                is_video=presentation.is_video,
            )
        previous = self._current_presentation
        if previous is not None:
            presentation = self._preserve_live_presentation(previous, presentation)
        confirmed_metadata = self._confirmed_location_metadata_for_path(presentation.path)
        if confirmed_metadata is not None:
            presentation = self._apply_location_metadata_to_presentation(
                presentation,
                confirmed_metadata,
            )
        deferred_location = getattr(self, "_deferred_locations", {}).get(
            presentation.path
        )
        if deferred_location and not presentation.location:
            info = dict(presentation.info)
            info["location"] = deferred_location
            presentation = replace(
                presentation,
                location=deferred_location,
                info=info,
            )
        if not self._router.is_detail_view_active():
            self._clear_play_profile(presentation.row)
            return
        self._current_presentation = presentation
        self._detail_request_generation = int(presentation.request_generation)
        row = presentation.row
        authoritative_row = row if row >= 0 else None
        visual_row = self._asset_model.set_current_asset(
            authoritative_row,
            presentation.path,
        )
        if not isinstance(visual_row, int) or visual_row < 0:
            visual_row = authoritative_row
        if authoritative_row is not None:
            self.assetChanged.emit(authoritative_row)
        self._update_header(presentation)
        if visual_row is not None:
            self._select_filmstrip_row(visual_row)
        same_render_request = (
            previous is not None
            and previous.render_key == presentation.render_key
            and previous.request_generation == presentation.request_generation
        )
        if same_render_request:
            emit_detail_event(
                "render_transaction_reused",
                generation=presentation.request_generation,
                asset_id=presentation.asset_id,
                row=row,
            )
            self._reconcile_action_capabilities(presentation)
            self._update_favorite_icon(presentation.is_favorite)
            if self._info_panel and presentation.info_panel_visible:
                self._refresh_info_panel(presentation.info)
                self._prepare_info_panel_presentation()
                self._info_panel.show()
            elif (
                self._info_panel
                and self._info_panel.isVisible()
                and not presentation.info_panel_visible
            ):
                self._info_panel.hide()
            self._clear_play_profile(presentation.row)
            return
        transaction = self._transaction_for_presentation(presentation)
        lifecycle = self._render_transaction_coordinator()
        active_snapshot = lifecycle.snapshot
        if (
            active_snapshot is not None
            and active_snapshot.transaction.generation == transaction.generation
            and active_snapshot.transaction != transaction
        ):
            emit_detail_event(
                "render_generation_collision",
                generation=transaction.generation,
                asset_id=transaction.asset_id,
                state=active_snapshot.state.value,
            )
            # A generation may identify only one immutable render input.  Ask
            # the ViewModel for a new click-generation instead of letting old
            # and new decoder results share an acceptance token.
            QTimer.singleShot(0, self._detail_vm.show_current)
            self._clear_play_profile(presentation.row)
            return
        if not lifecycle.begin(transaction):
            snapshot = lifecycle.snapshot
            emit_detail_event(
                "render_transaction_duplicate",
                generation=presentation.request_generation,
                asset_id=presentation.asset_id,
                state=snapshot.state.value if snapshot is not None else "missing",
            )
            # The current transaction already owns the decode/presentation.
            # Never cover its surface or invalidate its async token merely
            # because Gallery published another view of the same asset.
            self._clear_play_profile(presentation.row)
            return
        self._detail_render_transaction = transaction
        self._asset_generation = int(getattr(self, "_asset_generation", 0)) + 1
        self._library_epoch = self._read_library_epoch()
        self._active_async_token = self._token_for_presentation(presentation)
        self._pending_video_token = None
        self._pending_location_token = None
        self._player_view.show_placeholder("")
        QTimer.singleShot(
            0,
            lambda target_row=row, generation=self._detail_request_generation:
            self._center_filmstrip_if_current(target_row, generation),
        )
        # Yield one full Qt event-loop turn so Detail's opaque loading surface
        # paints before decoding, sidecar reads or media backend preparation.
        QTimer.singleShot(
            0,
            lambda candidate=presentation, generation=self._detail_request_generation,
            token=self._active_async_token:
            self._render_if_current(candidate, generation, token),
        )

    def _render_if_current(
        self,
        presentation: DetailPresentation,
        generation: int,
        token: PlaybackAsyncToken | None = None,
    ) -> None:
        if generation != self._detail_request_generation:
            return
        current = self._current_presentation
        if current is None or current.path != presentation.path:
            return
        if token is not None and token != getattr(self, "_active_async_token", None):
            return
        if not self._render_transaction_coordinator().mark_routed(
            generation,
            row=presentation.row,
        ):
            return
        self._render_presentation(presentation)

    def _preserve_live_presentation(
        self,
        previous: DetailPresentation,
        current: DetailPresentation,
    ) -> DetailPresentation:
        """Keep Live replay metadata stable across same-asset refreshes.

        During rescans the same asset may briefly refresh through a partial row
        that has not yet been re-paired. When the previous Live motion file
        still exists on disk, preserve that replay state for the currently
        displayed asset instead of transiently degrading it to a still image.
        """

        if previous.asset_id != current.asset_id or previous.path != current.path:
            return current
        if current.is_live and current.live_motion_abs is not None:
            return current
        if not previous.is_live or previous.live_motion_abs is None:
            return current
        try:
            if not previous.live_motion_abs.exists():
                return current
        except OSError:
            return current

        info = dict(current.info)
        if previous.live_motion_rel is not None:
            info.setdefault("live_partner_rel", str(previous.live_motion_rel))

        return replace(
            current,
            is_live=True,
            info=info,
            live_motion_rel=previous.live_motion_rel,
            live_motion_abs=previous.live_motion_abs,
        )

    def _render_presentation(self, presentation: DetailPresentation) -> None:
        self._render_transaction_coordinator().mark_preparing(
            presentation.request_generation
        )
        render_started = time.perf_counter()
        self._invalidate_overlay_requests(clear=True)
        self._presented_still_generation = 0
        self._presented_still_source = None
        source = presentation.path
        self._active_live_motion = None
        self._active_live_still = None
        self._active_live_asset_id = ""
        self._active_live_media_generation = None

        self._reconcile_action_capabilities(presentation)
        self._info_button.setEnabled(True)
        self._update_favorite_icon(presentation.is_favorite)

        self._zoom_slider.blockSignals(True)
        self._zoom_slider.setValue(100)
        self._zoom_slider.blockSignals(False)

        if presentation.is_video:
            self._hide_face_name_overlay(clear_annotations=True)
            if self._is_location_video_write_inflight(source):
                self._player_view.show_placeholder(_location_video_write_placeholder())
                if self._player_view.video_area.has_video():
                    self._player_view.video_area.stop()
                self._player_bar.setEnabled(False)
                self._zoom_handler.set_viewer(self._player_view.video_area)
                self._zoom_widget.show()
            else:
                trim_range_ms = presentation.video_trim_range_ms
                if trim_range_ms is not None:
                    self._trim_in_ms, self._trim_out_ms = trim_range_ms
                else:
                    self._trim_in_ms = 0
                    self._trim_out_ms = 0
                has_trim = trim_range_ms is not None
                load_started = time.perf_counter()
                self._player_view.video_area.begin_load(
                    source,
                    presentation.request_generation,
                )
                self._schedule_video_preparation(presentation)
                # Keep the loading surface covered until this generation's
                # first video frame is part of a submitted window frame.
                self._player_view.begin_video_transition(
                    presentation.request_generation,
                    interactive_when_ready=True,
                )
                log_detail_profile(
                    "playback",
                    "video.transaction_prepare",
                    (time.perf_counter() - load_started) * 1000.0,
                    path=source.name,
                    adjusted_preview=presentation.video_adjusted_preview,
                    has_trim=has_trim,
                )
                self._player_bar.setEnabled(True)
                self._zoom_handler.set_viewer(self._player_view.video_area)
                self._player_view.video_area.reset_zoom()
                self._zoom_widget.show()
        else:
            if self._player_view.video_area.has_video():
                self._player_view.video_area.stop()
            display_started = time.perf_counter()
            identity_kwargs = (
                {"source_identity": presentation.source_identity}
                if presentation.source_identity is not None
                else {}
            )
            self._player_view.display_image(
                source,
                asset_id=presentation.asset_id,
                request_generation=presentation.request_generation,
                transaction=getattr(self, "_detail_render_transaction", None),
                **identity_kwargs,
            )
            log_detail_profile(
                "playback",
                "image.display_image",
                (time.perf_counter() - display_started) * 1000.0,
                path=source.name,
            )
            self._player_bar.setEnabled(False)
            self._zoom_handler.set_viewer(self._player_view.image_viewer)
            self._player_view.image_viewer.reset_zoom()
            self._zoom_widget.show()

            if presentation.is_live:
                self._hide_face_name_overlay(clear_annotations=False)
                self._player_view.show_live_badge()
                self._player_view.set_live_replay_enabled(True)
                self._autoplay_live_motion(presentation)
            else:
                self._player_view.hide_live_badge()
                self._player_view.set_live_replay_enabled(False)

        self._is_playing = False
        self._player_bar.set_playback_state(False)
        self._player_bar.set_position(0)

        if self._info_panel and presentation.info_panel_visible:
            self._refresh_info_panel(presentation.info)
            self._prepare_info_panel_presentation()
            self._info_panel.show()
        elif (
            self._info_panel
            and self._info_panel.isVisible()
            and not presentation.info_panel_visible
        ):
            self._info_panel.hide()
        log_detail_profile(
            "playback",
            "render_presentation.total",
            (time.perf_counter() - render_started) * 1000.0,
            path=source.name,
            is_video=presentation.is_video,
        )
        self._clear_play_profile(presentation.row)
        self._schedule_deferred_location(presentation)

    def _reconcile_action_capabilities(
        self,
        presentation: DetailPresentation,
    ) -> None:
        """Apply one capability snapshot without changing the render session."""

        capability_buttons = (
            ("_favorite_button", presentation.can_toggle_favorite),
            ("_share_button", presentation.can_share),
            ("_rotate_button", presentation.can_rotate),
        )
        for attribute, enabled in capability_buttons:
            button = getattr(self, attribute, None)
            if button is not None:
                button.setEnabled(bool(enabled))

        edit_enabled = bool(presentation.can_edit)
        if not presentation.is_video:
            edit_enabled = edit_enabled and (
                getattr(self, "_presented_still_source", None) == presentation.path
                and getattr(self, "_presented_still_generation", 0)
                == int(presentation.request_generation)
            )
        edit_button = getattr(self, "_edit_button", None)
        if edit_button is not None:
            edit_button.setEnabled(edit_enabled)

    def _schedule_deferred_location(self, presentation: DetailPresentation) -> None:
        if (
            presentation.location
            or presentation.path in getattr(self, "_deferred_locations", {})
        ):
            return
        gps = presentation.info.get("gps")
        if not isinstance(gps, dict) or not hasattr(self, "_deferred_location_pool"):
            return
        self._deferred_location_pool.clear()
        self._deferred_location_pool.start(
            _DeferredLocationWorker(
                self._active_async_token,
                gps,
                self._deferred_location_signals,
            )
        )
        self._pending_location_token = self._active_async_token

    @Slot(object, str)
    def _on_deferred_location_ready(
        self,
        token: object,
        location: str,
    ) -> None:
        if token != getattr(self, "_pending_location_token", None):
            return
        if not self._async_token_is_current(token):
            return
        self._pending_location_token = None
        path = token.source_path
        presentation = getattr(self, "_current_presentation", None)
        if presentation is None or presentation.path != path:
            return
        if not hasattr(self, "_deferred_locations"):
            self._deferred_locations = {}
        self._deferred_locations[path] = location
        info = dict(presentation.info)
        info["location"] = location
        presentation = replace(presentation, location=location, info=info)
        self._current_presentation = presentation
        self._update_header(presentation)

    def _schedule_video_preparation(self, presentation: DetailPresentation) -> None:
        token = self._token_for_presentation(presentation)
        self._pending_video_token = token
        self._video_prepare_pool.clear()
        self._video_prepare_pool.start(
            _VideoPreparationWorker(
                presentation=presentation,
                token=token,
                edit_service_getter=self._edit_service_getter,
                signals=self._video_prepare_signals,
            )
        )

    @Slot(object, object)
    def _on_video_preparation_ready(
        self,
        token: object,
        state: object,
    ) -> None:
        if token != getattr(self, "_pending_video_token", None):
            return
        presentation = getattr(self, "_current_presentation", None)
        is_live_motion = bool(getattr(self, "_active_live_motion", None))
        if presentation is None or (not presentation.is_video and not is_live_motion):
            return
        expected_path = (
            self._active_live_motion if is_live_motion else presentation.path
        )
        if not self._async_token_is_current(token, expected_path=expected_path):
            return
        if not isinstance(state, VideoPresentationState):
            return
        transaction = self._detail_render_transaction
        if transaction is None or transaction.generation != state.request_generation:
            return
        self._pending_video_token = None
        state = replace(state, transaction=transaction)
        if not self._player_view.video_area.commit_presentation(state):
            return
        if state.trim_range_ms is not None:
            self._trim_in_ms, self._trim_out_ms = state.trim_range_ms
        else:
            self._trim_in_ms = 0
            self._trim_out_ms = 0
        self._player_view.video_area.play()

    @Slot(object, object)
    def _on_video_preparation_failed(
        self,
        token: object,
        error: object,
    ) -> None:
        if token != getattr(self, "_pending_video_token", None):
            return
        if not self._async_token_is_current(token):
            return
        self._pending_video_token = None
        transaction = getattr(self, "_detail_render_transaction", None)
        if transaction is None:
            return
        generation = transaction.generation
        source = token.source_path
        active_live_motion = getattr(self, "_active_live_motion", None)
        if (
            transaction.media_kind == "live_motion"
            and active_live_motion is not None
            and Path(active_live_motion) == source
            and self._restore_live_still(stop_motion=True)
        ):
            emit_detail_event(
                "live_motion_failed",
                generation=generation,
                asset_id=transaction.asset_id,
                message=str(error),
            )
            LOGGER.warning(
                "Live Motion preparation failed for %s; restored still: %s",
                source.name,
                error,
            )
            return
        self._render_transaction_coordinator().mark_failed(generation, str(error))
        self._player_view.video_area.stop()
        self._player_view.show_placeholder(
            tr("PlaybackCoordinator", "Unable to load this video.")
        )
        LOGGER.warning("Video preparation failed for %s: %s", source.name, error)

    @Slot(int)
    def _on_video_first_frame_presented(self, generation: int) -> None:
        if generation != getattr(self, "_detail_request_generation", 0):
            return
        presentation = getattr(self, "_current_presentation", None)
        is_live_motion = bool(getattr(self, "_active_live_motion", None))
        if presentation is None or (not presentation.is_video and not is_live_motion):
            return
        surface_kind = "live_motion_frame" if is_live_motion else "video_frame"
        result = self._render_transaction_coordinator().mark_surface_presented(
            generation,
            surface_kind,
        )
        if result is DetailSurfacePresentationResult.REJECTED_STALE:
            return
        # Do not reclaim the user's scroll position when decoding completes.

    def _is_location_video_write_inflight(self, path: Path) -> bool:
        inflight = getattr(self, "_location_video_write_inflight_paths", set())
        return Path(path) in inflight

    def _defer_location_video_loading(self, path: Path) -> None:
        if not hasattr(self, "_location_video_write_inflight_paths"):
            self._location_video_write_inflight_paths = set()
        self._location_video_write_inflight_paths.add(Path(path))

    def _allow_location_video_loading(self, path: Path) -> None:
        inflight = getattr(self, "_location_video_write_inflight_paths", None)
        if inflight is not None:
            inflight.discard(Path(path))

    def _maybe_render_location_video_after_file_write(self, path: Path) -> None:
        presentation = getattr(self, "_current_presentation", None)
        if (
            presentation is None
            or presentation.path != Path(path)
            or not presentation.is_video
            or not self._router.is_detail_view_active()
        ):
            return
        self._render_presentation(presentation)

    def _complete_location_video_file_write(self, path: Path) -> None:
        path = Path(path)
        self._allow_location_video_loading(path)
        if getattr(self, "_location_released_video_path", None) == path:
            self._restore_video_released_for_location_write()
            return
        self._maybe_render_location_video_after_file_write(path)

    def _autoplay_live_motion(self, presentation: DetailPresentation) -> None:
        motion_path = presentation.live_motion_abs
        if motion_path is None:
            self._refresh_face_name_overlay_for_presentation(presentation)
            return
        self._active_live_motion = motion_path
        self._active_live_still = presentation.path
        self._active_live_asset_id = presentation.asset_id
        self._hide_face_name_overlay(clear_annotations=False)
        self._player_view.defer_still_updates(True)
        self._trim_in_ms = 0
        self._trim_out_ms = 0
        self._active_live_media_generation = self._player_view.video_area.begin_load(
            motion_path,
            presentation.request_generation,
        )
        self._schedule_video_preparation(
            replace(
                presentation,
                path=motion_path,
                is_video=True,
                is_live=False,
                video_adjustments=None,
                video_trim_range_ms=None,
                video_adjusted_preview=False,
            )
        )
        self._player_view.begin_video_transition(
            presentation.request_generation,
            interactive_when_ready=False,
        )
        self._player_bar.setEnabled(False)
        self._is_playing = True

    def _restore_live_still(self, *, stop_motion: bool = False) -> bool:
        """End one Live Motion attempt and restore its primary still asset."""

        if not self._active_live_motion or not self._active_live_still:
            return False
        still = self._active_live_still
        asset_id = self._active_live_asset_id
        transaction = getattr(self, "_detail_render_transaction", None)
        if stop_motion:
            self._player_view.video_area.stop()
        self._active_live_motion = None
        self._active_live_still = None
        self._active_live_asset_id = ""
        self._active_live_media_generation = None
        self._player_view.defer_still_updates(False)
        if not self._player_view.apply_pending_still():
            if (
                transaction is not None
                and transaction.media_kind == "live_motion"
                and transaction.source_identity.path == still
                and self._render_transaction_coordinator().owns_generation(
                    transaction.generation
                )
            ):
                self._player_view.display_image(still, transaction=transaction)
            else:
                self._player_view.display_image(still, asset_id=asset_id)
        self._player_bar.setEnabled(False)
        self._player_view.show_live_badge()
        self._player_view.set_live_replay_enabled(True)
        self._is_playing = False
        return True

    @Slot(int, object, int)
    def _handle_playback_finished(
        self,
        request_generation: int,
        source: object,
        media_generation: int,
    ) -> None:
        try:
            finished_source = Path(source)
        except TypeError:
            return
        if request_generation != getattr(self, "_detail_request_generation", 0):
            return
        if finished_source != getattr(self, "_active_live_motion", None):
            return
        if media_generation != getattr(self, "_active_live_media_generation", None):
            return
        self._restore_live_still()

    def _hide_face_name_overlay(self, *, clear_annotations: bool) -> None:
        overlay = getattr(self, "_face_name_overlay", None)
        if overlay is None:
            return
        if clear_annotations:
            overlay.clear_annotations()
        overlay.set_overlay_active(False)

    def _invalidate_overlay_requests(self, *, clear: bool) -> None:
        self._overlay_request_generation = int(
            getattr(self, "_overlay_request_generation", 0)
        ) + 1
        pool = getattr(self, "_overlay_pool", None)
        if pool is not None:
            pool.clear()
        if clear:
            self._hide_face_name_overlay(clear_annotations=True)

    @Slot(object, int)
    def _on_still_frame_presented(self, source: object, generation: int) -> None:
        presentation = getattr(self, "_current_presentation", None)
        if presentation is None or presentation.is_video:
            return
        try:
            presented_source = Path(source)
        except TypeError:
            return
        if presented_source != presentation.path:
            return
        if getattr(self, "_active_live_motion", None):
            return
        transaction = getattr(self, "_detail_render_transaction", None)
        if (
            transaction is None
            or transaction.generation != int(generation)
            or transaction.source_identity.path != presented_source
            or not self._render_transaction_coordinator().owns_generation(
                int(generation)
            )
        ):
            return
        surface_kind = (
            "live_still" if transaction.media_kind == "live_motion" else "still"
        )
        result = self._render_transaction_coordinator().mark_surface_presented(
            int(generation),
            surface_kind,
        )
        if result is DetailSurfacePresentationResult.REJECTED_STALE:
            return
        self._presented_still_source = presented_source
        self._presented_still_generation = int(generation)
        self._reconcile_action_capabilities(presentation)
        self._schedule_recognition_overlay(presentation, int(generation))
        if not getattr(self, "_active_live_motion", None):
            self._prefetch_neighbor_stills(presentation.row)

    @Slot(object, str)
    def _on_still_loading_failed(self, source: object, message: str) -> None:
        presentation = getattr(self, "_current_presentation", None)
        if presentation is None or presentation.is_video:
            return
        try:
            failed_source = Path(source)
        except TypeError:
            return
        if failed_source != presentation.path:
            return
        transaction = getattr(self, "_detail_render_transaction", None)
        if transaction is None or transaction.source_identity.path != failed_source:
            return
        self._render_transaction_coordinator().mark_failed(
            transaction.generation,
            str(message),
        )
        edit_button = getattr(self, "_edit_button", None)
        if edit_button is not None:
            edit_button.setEnabled(False)

    def _prefetch_neighbor_stills(self, row: int) -> None:
        descriptor_getter = getattr(
            self._asset_model,
            "detail_prefetch_descriptor",
            None,
        )
        prefetch_many = getattr(self._player_view, "prefetch_images", None)
        if not callable(descriptor_getter) or not callable(prefetch_many):
            return
        descriptors: list[DetailPrefetchDescriptor | Path] = []
        for candidate_row in (row - 1, row + 1):
            descriptor = descriptor_getter(candidate_row)
            if descriptor is not None and not descriptor.is_video:
                descriptors.append(descriptor)
        if descriptors:
            prefetch_many(descriptors)

    def _schedule_recognition_overlay(
        self,
        presentation: DetailPresentation | None,
        still_generation: int,
    ) -> None:
        if not self._should_show_face_name_overlay(presentation):
            self._hide_face_name_overlay(clear_annotations=True)
            return
        query_service = getattr(self, "_recognition_query_service", None)
        if query_service is None or presentation is None:
            return
        query_root = getattr(query_service, "library_root", None)
        if query_root is not None and Path(query_root) != self._people_library_root:
            return
        self._overlay_request_generation += 1
        request_generation = self._overlay_request_generation
        self._overlay_pool.clear()
        self._overlay_pool.start(
            _RecognitionOverlayWorker(
                request_generation=request_generation,
                still_generation=still_generation,
                asset_id=presentation.asset_id,
                query_service=query_service,
                signals=self._overlay_signals,
            )
        )

    @Slot(int, int, object)
    def _on_recognition_overlay_ready(
        self,
        request_generation: int,
        still_generation: int,
        snapshot: object,
    ) -> None:
        if request_generation != self._overlay_request_generation:
            return
        if not self._show_face_names:
            return
        presentation = getattr(self, "_current_presentation", None)
        if not self._should_show_face_name_overlay(presentation):
            return
        if presentation is None or snapshot.asset_id != presentation.asset_id:
            return
        if Path(snapshot.library_root) != self._people_library_root:
            return
        if still_generation != self._presented_still_generation:
            return
        if self._presented_still_source != presentation.path:
            return

        from iPhoto.gui.ui.widgets.recognition_annotations import (
            RecognitionIdentitySuggestion,
            face_annotation_adapter,
            pet_annotation_adapter,
        )

        annotations = [face_annotation_adapter(value) for value in snapshot.faces]
        annotations.extend(pet_annotation_adapter(value) for value in snapshot.pets)
        suggestions = [
            RecognitionIdentitySuggestion(
                identity_key=value.identity_key,
                name=value.name,
                thumbnail_path=value.thumbnail_path,
                count=value.count,
            )
            for value in getattr(snapshot, "candidates", ())
        ]
        overlay = getattr(self, "_face_name_overlay", None)
        if overlay is None:
            return
        setter = getattr(overlay, "set_identity_suggestions", None)
        if callable(setter):
            setter(suggestions)
        overlay.set_annotations(annotations)
        overlay.set_overlay_active(bool(annotations))
        if annotations:
            emit_detail_event(
                "face_presented",
                generation=getattr(self, "_detail_request_generation", 0),
                annotation_count=len(annotations),
            )

    @Slot(int, object)
    def _on_recognition_overlay_failed(
        self,
        request_generation: int,
        error: object,
    ) -> None:
        if request_generation == self._overlay_request_generation:
            LOGGER.warning("Recognition overlay query failed: %s", error)

    def _refresh_face_name_overlay_for_current_presentation(self) -> None:
        self._refresh_face_name_overlay_for_presentation(
            getattr(self, "_current_presentation", None)
        )

    @Slot(object)
    def handle_people_snapshot_committed(self, event: object) -> None:
        changed_asset_ids = getattr(event, "changed_asset_ids", None)
        self._invalidate_recognition_query_cache(changed_asset_ids)
        presentation = getattr(self, "_current_presentation", None)
        if presentation is None or not presentation.asset_id:
            return
        # Skip the refresh if the snapshot doesn't touch the current asset.
        # An absent or empty changed_asset_ids means "all assets potentially
        # changed" (e.g., a set_person_order event) — in that case always refresh.
        if changed_asset_ids and presentation.asset_id not in changed_asset_ids:
            return
        self._refresh_face_name_overlay_for_presentation(presentation)
        self._refresh_info_panel_faces(presentation.asset_id)

    def _refresh_face_name_overlay_for_presentation(
        self,
        presentation: DetailPresentation | None,
    ) -> None:
        overlay = getattr(self, "_face_name_overlay", None)
        if overlay is None:
            return
        if not self._should_show_face_name_overlay(presentation):
            self._hide_face_name_overlay(clear_annotations=True)
            return
        if (
            getattr(self, "_presented_still_source", None) == presentation.path
            and getattr(self, "_presented_still_generation", 0) > 0
        ):
            self._schedule_recognition_overlay(
                presentation,
                self._presented_still_generation,
            )

    def _should_show_face_name_overlay(
        self,
        presentation: DetailPresentation | None,
    ) -> bool:
        if presentation is None or presentation.is_video or not presentation.asset_id:
            return False
        if not bool(getattr(self, "_show_face_names", False)):
            return False
        if getattr(self, "_active_live_motion", None) is not None:
            return False
        player_view = getattr(self, "_player_view", None)
        video_area = getattr(player_view, "video_area", None)
        is_edit_mode_active = getattr(video_area, "is_edit_mode_active", None)
        if callable(is_edit_mode_active) and is_edit_mode_active():
            return False
        return True

    def _load_face_name_annotations(self, asset_id: str) -> list:
        if not asset_id:
            return []
        annotations: list[object] = []
        from iPhoto.gui.ui.widgets.recognition_annotations import (
            face_annotation_adapter,
            pet_annotation_adapter,
        )

        people_service = getattr(self, "_people_service", None)
        if people_service is not None:
            try:
                annotations.extend(
                    face_annotation_adapter(annotation)
                    for annotation in people_service.list_asset_face_annotations(asset_id)
                )
            except (sqlite3.Error, OSError):
                LOGGER.exception("Failed to load face annotations for asset %s", asset_id)
        pet_service = getattr(self, "_pet_service", None)
        if pet_service is not None:
            try:
                annotations.extend(
                    pet_annotation_adapter(annotation)
                    for annotation in pet_service.list_asset_pet_annotations(asset_id)
                )
            except (sqlite3.Error, OSError):
                LOGGER.exception("Failed to load pet annotations for asset %s", asset_id)
        return annotations

    def _load_recognition_identity_suggestions(
        self,
        *,
        include_hidden: bool,
    ) -> list[RecognitionIdentitySuggestion]:
        from iPhoto.gui.ui.widgets.recognition_annotations import (
            RecognitionIdentitySuggestion,
        )

        suggestions: list[RecognitionIdentitySuggestion] = []
        people_service = getattr(self, "_people_service", None)
        if people_service is not None:
            try:
                people_candidates = people_service.list_clusters(include_hidden=include_hidden)
                if isinstance(people_candidates, (list, tuple)):
                    suggestions.extend(
                        RecognitionIdentitySuggestion(
                            identity_key=f"person:{summary.person_id}",
                            name=summary.name.strip(),
                            thumbnail_path=summary.thumbnail_path,
                            count=int(getattr(summary, "face_count", 0) or 0),
                        )
                        for summary in people_candidates
                        if getattr(summary, "person_id", None)
                        and isinstance(summary.name, str)
                        and summary.name.strip()
                    )
            except (sqlite3.Error, OSError):
                LOGGER.exception("Failed to load person identity suggestions")
        pet_service = getattr(self, "_pet_service", None)
        if pet_service is not None:
            try:
                pet_candidates = pet_service.list_pets(include_hidden=include_hidden)
                if isinstance(pet_candidates, (list, tuple)):
                    suggestions.extend(
                        RecognitionIdentitySuggestion(
                            identity_key=f"pet:{summary.pet_id}",
                            name=summary.name.strip(),
                            thumbnail_path=summary.thumbnail_path,
                            count=int(getattr(summary, "detection_count", 0) or 0),
                        )
                        for summary in pet_candidates
                        if getattr(summary, "pet_id", None)
                        and isinstance(summary.name, str)
                        and summary.name.strip()
                    )
            except (sqlite3.Error, OSError):
                LOGGER.exception("Failed to load pet identity suggestions")
        return suggestions

    def _apply_recognition_identity_suggestions(
        self,
        overlay: object,
        *,
        include_hidden: bool,
    ) -> None:
        suggestions = self._load_recognition_identity_suggestions(
            include_hidden=include_hidden,
        )
        set_identity_suggestions = getattr(overlay, "set_identity_suggestions", None)
        if callable(set_identity_suggestions):
            set_identity_suggestions(suggestions)
            return
        set_name_suggestions = getattr(overlay, "set_name_suggestions", None)
        if callable(set_name_suggestions):
            set_name_suggestions(suggestions)

    def _refresh_recognition_views_after_mutation(self) -> None:
        self._invalidate_recognition_query_cache()
        self._refresh_face_name_overlay_for_current_presentation()
        presentation = getattr(self, "_current_presentation", None)
        if presentation is not None and presentation.asset_id:
            self._refresh_info_panel_faces(presentation.asset_id)
        refresh_callback = getattr(self, "_people_dashboard_refresh_callback", None)
        if callable(refresh_callback):
            refresh_callback()

    def _invalidate_recognition_query_cache(self, changed_asset_ids=None) -> None:
        query_service = getattr(self, "_recognition_query_service", None)
        invalidate = getattr(query_service, "invalidate", None)
        if callable(invalidate):
            invalidate(changed_asset_ids)

    @staticmethod
    def _entity_kind_and_id(entity_key: str | None) -> tuple[str, str]:
        if not entity_key:
            return ("person", "")
        if entity_key.startswith("pet:"):
            return ("pet", entity_key.removeprefix("pet:"))
        if entity_key.startswith("person:"):
            return ("person", entity_key.removeprefix("person:"))
        return ("person", entity_key)

    @staticmethod
    def _annotation_kind(annotation: object) -> str:
        source_kind = getattr(
            annotation,
            "source_detection_kind",
            getattr(annotation, "kind", "person"),
        )
        return "pet" if source_kind == "pet" else "person"

    @staticmethod
    def _annotation_id(annotation: object) -> str:
        source_id = getattr(annotation, "source_annotation_id", None)
        if source_id:
            return str(source_id)
        if getattr(annotation, "kind", "person") == "pet":
            return str(
                getattr(annotation, "detection_id", "")
                or getattr(annotation, "annotation_id", "")
            )
        return str(getattr(annotation, "face_id", ""))

    @staticmethod
    def _target_entity_id(entity_key: str) -> str:
        if entity_key.startswith("pet:"):
            return entity_key.removeprefix("pet:")
        if entity_key.startswith("person:"):
            return entity_key.removeprefix("person:")
        return entity_key

    def _rename_pet_from_overlay(self, pet_id: str, new_name: object) -> bool:
        pet_service = getattr(self, "_pet_service", None)
        if pet_service is None or not pet_id:
            return False
        name = new_name.strip() if isinstance(new_name, str) else None
        try:
            pet_service.rename_pet(pet_id, name or None)
        except (sqlite3.Error, OSError):
            LOGGER.exception("Failed to rename pet %s", pet_id)
            return False
        return True

    @Slot(str, object)
    def _handle_face_name_rename_submitted(
        self,
        person_id: str,
        new_name: object,
    ) -> None:
        if not person_id:
            return
        entity_kind, entity_id = self._entity_kind_and_id(person_id)
        if entity_kind == "pet":
            if self._rename_pet_from_overlay(entity_id, new_name):
                self._refresh_recognition_views_after_mutation()
            return
        people_service = getattr(self, "_people_service", None)
        if people_service is None:
            return
        name = new_name.strip() if isinstance(new_name, str) else None
        try:
            people_service.rename_cluster(entity_id, name or None)
        except (sqlite3.Error, OSError):
            LOGGER.exception("Failed to rename person %s", entity_id)
            return
        self._refresh_recognition_views_after_mutation()

    @Slot(object, str)
    def _handle_face_name_existing_identity_submitted(
        self,
        annotation: object,
        target_identity: str,
    ) -> None:
        """Assign an inline name selection without changing typed-name semantics."""

        target_kind, target_id = self._entity_kind_and_id(target_identity)
        if not target_id:
            return
        normalized_target = f"{target_kind}:{target_id}"
        current_identity = getattr(annotation, "person_id", None)
        if not isinstance(current_identity, str) or not current_identity:
            self._handle_info_panel_face_move_requested(annotation, normalized_target)
            return

        source_kind, source_id = self._entity_kind_and_id(current_identity)
        if not source_id:
            return
        normalized_source = f"{source_kind}:{source_id}"
        if normalized_source == normalized_target:
            return
        merge_service = getattr(self, "_recognition_merge_service", None)
        if merge_service is None:
            return
        try:
            outcome = merge_service.merge(normalized_source, normalized_target)
        except (sqlite3.Error, OSError):
            LOGGER.exception(
                "Failed to merge inline recognition identity %s into %s",
                normalized_source,
                normalized_target,
            )
            self._show_inline_identity_error(
                tr(
                    "PlaybackCoordinator",
                    "The name could not be assigned. Please try again.",
                )
            )
            return
        if getattr(outcome, "merged", False):
            self._refresh_recognition_views_after_mutation()
            return

        failure_value = getattr(outcome, "failure", None)
        failure = getattr(failure_value, "value", failure_value)
        if failure == "same_asset_conflict":
            self._show_same_asset_identity_error()
            return
        self._show_identity_assignment_changed_error()

    def _show_identity_assignment_changed_error(self) -> None:
        self._show_inline_identity_error(
            tr(
                "PlaybackCoordinator",
                "The name could not be assigned. The identities may have changed.",
            )
        )

    def _show_same_asset_identity_error(self) -> None:
        self._show_inline_identity_error(
            tr(
                "PlaybackCoordinator",
                (
                    "A pet identity cannot contain two detections from the same photo. "
                    "Delete a duplicate detection instead of merging it."
                ),
            )
        )

    def _show_inline_identity_error(self, message: str) -> None:
        overlay = getattr(self, "_face_name_overlay", None)
        show_error = getattr(overlay, "show_name_error", None)
        if not callable(show_error):
            show_error = getattr(overlay, "show_manual_error", None)
        if callable(show_error):
            show_error(message)

    @Slot(object)
    def _handle_info_panel_face_delete_requested(self, annotation: object) -> None:
        annotation_id = self._annotation_id(annotation)
        if not annotation_id:
            return
        if self._annotation_kind(annotation) == "pet":
            pet_service = getattr(self, "_pet_service", None)
            if pet_service is None:
                return
            try:
                changed = pet_service.delete_detection(annotation_id)
            except (sqlite3.Error, OSError):
                LOGGER.exception("Failed to delete pet detection %s", annotation_id)
                return
            if changed:
                self._refresh_recognition_views_after_mutation()
            return
        people_service = getattr(self, "_people_service", None)
        if people_service is None:
            return
        try:
            changed = people_service.delete_face(annotation_id)
        except (sqlite3.Error, OSError):
            LOGGER.exception("Failed to delete face %s", annotation_id)
            return
        if not changed:
            return
        self._refresh_recognition_views_after_mutation()

    @Slot(object, str)
    def _handle_info_panel_face_move_requested(
        self,
        annotation: object,
        target_person_id: str,
    ) -> None:
        if not target_person_id:
            return
        annotation_id = self._annotation_id(annotation)
        if not annotation_id:
            return
        source_kind = self._annotation_kind(annotation)
        target_kind, target_id = self._entity_kind_and_id(target_person_id)
        if source_kind != target_kind:
            people_service = getattr(self, "_people_service", None)
            if people_service is None:
                return
            try:
                changed = people_service.reassign_detection_identity(
                    source_kind=source_kind,
                    source_annotation_id=annotation_id,
                    target_identity=f"{target_kind}:{target_id}",
                )
            except (sqlite3.Error, OSError):
                LOGGER.exception(
                    "Failed to reassign %s detection %s to %s identity %s",
                    source_kind,
                    annotation_id,
                    target_kind,
                    target_id,
                )
                return
            if changed:
                self._refresh_recognition_views_after_mutation()
            return
        if source_kind == "pet":
            pet_service = getattr(self, "_pet_service", None)
            if pet_service is None:
                return
            target_pet_id = target_id
            try:
                outcome = pet_service.move_detection_to_pet_with_outcome(
                    annotation_id,
                    target_pet_id,
                )
            except (sqlite3.Error, OSError):
                LOGGER.exception(
                    "Failed to move pet detection %s to pet %s",
                    annotation_id,
                    target_pet_id,
                )
                return
            if getattr(outcome, "succeeded", False):
                self._refresh_recognition_views_after_mutation()
                return
            failure_value = getattr(outcome, "failure", None)
            failure = getattr(failure_value, "value", failure_value)
            if failure == "same_asset_conflict":
                self._show_same_asset_identity_error()
            else:
                self._show_identity_assignment_changed_error()
            return
        people_service = getattr(self, "_people_service", None)
        if people_service is None:
            return
        target_person_id = target_id
        try:
            changed = people_service.move_face_to_person(annotation_id, target_person_id)
        except (sqlite3.Error, OSError):
            LOGGER.exception(
                "Failed to move face %s to person %s",
                annotation_id,
                target_person_id,
            )
            return
        if not changed:
            return
        self._refresh_recognition_views_after_mutation()

    @Slot(object, str)
    def _handle_info_panel_face_move_to_new_person_requested(
        self,
        annotation: object,
        new_name: str,
    ) -> None:
        annotation_id = self._annotation_id(annotation)
        if not annotation_id:
            return
        if self._annotation_kind(annotation) == "pet":
            pet_service = getattr(self, "_pet_service", None)
            if pet_service is None:
                return
            try:
                created_pet_id = pet_service.move_detection_to_new_pet(annotation_id, new_name)
            except (sqlite3.Error, OSError):
                LOGGER.exception("Failed to move pet detection %s into a new pet", annotation_id)
                return
            if created_pet_id:
                self._refresh_recognition_views_after_mutation()
            return
        people_service = getattr(self, "_people_service", None)
        if people_service is None:
            return
        try:
            created_person_id = people_service.move_face_to_new_person(annotation_id, new_name)
        except (sqlite3.Error, OSError):
            LOGGER.exception("Failed to move face %s into a new person", annotation_id)
            return
        if not created_person_id:
            return
        self._refresh_recognition_views_after_mutation()

    def _sync_filmstrip_selection(self, row: int) -> None:
        idx = self._select_filmstrip_row(row)
        if idx.isValid():
            self._filmstrip_view.center_on_index(idx)

    def _select_filmstrip_row(self, row: int) -> QModelIndex:
        """Update the highlight without triggering a programmatic scroll."""

        idx = self._asset_model.index(row, 0)
        model = self._filmstrip_view.model()
        mapped = map_from_root_source(model, idx)
        if not mapped.isValid() and hasattr(model, "mapFromSource"):
            mapped = model.mapFromSource(idx)
        idx = mapped
        if idx.isValid():
            self._filmstrip_view.select_index_for_centering(idx)
        return idx

    def _center_filmstrip_if_current(self, row: int, generation: int) -> None:
        """Center once after route paint, before slow media preparation."""

        if generation != getattr(self, "_detail_request_generation", 0):
            return
        presentation = getattr(self, "_current_presentation", None)
        if presentation is None or presentation.row != row:
            return
        idx = self._asset_model.index(row, 0)
        model = self._filmstrip_view.model()
        mapped = map_from_root_source(model, idx)
        if not mapped.isValid() and hasattr(model, "mapFromSource"):
            mapped = model.mapFromSource(idx)
        idx = mapped
        if idx.isValid():
            self._filmstrip_view.center_on_index(idx)

    def _update_favorite_icon(self, is_favorite: bool) -> None:
        icon_name = "suit.heart.fill.svg" if is_favorite else "suit.heart.svg"
        icon_color = self._resolve_icon_tint()
        self._favorite_button.setIcon(load_icon(icon_name, color=icon_color))

    def _resolve_icon_tint(self) -> str | None:
        palette = self._favorite_button.palette()
        color = palette.color(QPalette.ColorRole.ButtonText)
        if not color.isValid():
            color = palette.color(QPalette.ColorRole.WindowText)
        if not color.isValid():
            return None
        return color.name(QColor.NameFormat.HexArgb)

    def reset_for_gallery(self) -> None:
        self._invalidate_overlay_requests(clear=False)
        for pool_name in ("_video_prepare_pool", "_deferred_location_pool"):
            pool = getattr(self, pool_name, None)
            if pool is not None:
                pool.clear()
        self._presented_still_generation = 0
        self._presented_still_source = None
        self._clear_play_request_state()
        cancel_stills = getattr(self._player_view, "cancel_pending_image_requests", None)
        if callable(cancel_stills):
            cancel_stills()
        self._reset_location_search_service(clear_cache=True)
        video_area = self._player_view.video_area
        has_video = False
        has_video_method = getattr(video_area, "has_video", None)
        if callable(has_video_method):
            has_video = bool(has_video_method())
        router = getattr(self, "_router", None)
        is_detail_active = False
        if router is not None:
            is_detail_view_active = getattr(router, "is_detail_view_active", None)
            if callable(is_detail_view_active):
                is_detail_active = bool(is_detail_view_active())
        needs_view_cleanup = bool(
            has_video
            or getattr(self, "_is_playing", False)
            or getattr(self, "_current_presentation", None) is not None
            or is_detail_active
        )
        LOGGER.info(
            "reset_for_gallery: needs_view_cleanup=%s has_video=%s is_playing=%s detail_active=%s",
            needs_view_cleanup,
            has_video,
            getattr(self, "_is_playing", False),
            is_detail_active,
        )
        if needs_view_cleanup:
            if has_video:
                video_area.stop()
                LOGGER.info("reset_for_gallery: video_stop_done")
            else:
                LOGGER.info("reset_for_gallery: video_stop_skipped")
            self._player_view.show_placeholder()
            self._hide_face_name_overlay(clear_annotations=True)
        else:
            LOGGER.info("reset_for_gallery: idle_view_cleanup_skipped")
        self._player_bar.setEnabled(False)
        self._is_playing = False
        self._current_presentation = None
        self._detail_vm.hide_info_panel(refresh_presentation=False)
        self._update_header(None)
        if self._info_panel:
            self._info_panel.hide()
        self._clear_info_panel_metadata_state()
        self._clear_confirmed_location_metadata()

    def shutdown(self) -> None:
        self._invalidate_overlay_requests(clear=True)
        self._overlay_pool.waitForDone(1500)
        for pool_name in ("_video_prepare_pool", "_deferred_location_pool"):
            pool = getattr(self, pool_name, None)
            if pool is not None:
                pool.clear()
                pool.waitForDone(1500)
        self._clear_play_request_state()
        location_search_controller = getattr(self, "_location_search_controller", None)
        if location_search_controller is not None:
            location_search_controller.shutdown()
        self._player_view.video_area.stop()
        shutdown_player = getattr(self._player_view, "shutdown", None)
        if callable(shutdown_player):
            shutdown_player()
        self._hide_face_name_overlay(clear_annotations=True)
        self._is_playing = False
        self._current_presentation = None
        self._detail_vm.hide_info_panel(refresh_presentation=False)
        self._update_header(None)
        if self._info_panel:
            self._info_panel.shutdown()
            self._info_panel.hide()
        self._clear_info_panel_metadata_state()
        self._clear_confirmed_location_metadata()

    def _update_header(self, presentation: DetailPresentation | None) -> None:
        if not self._header_controller:
            return
        if presentation is None:
            self._header_controller.clear()
            return
        self._header_controller.update_from_values(presentation.location, presentation.timestamp)

    def _edit_service(self) -> EditServicePort | None:
        getter = getattr(self, "_edit_service_getter", None)
        if callable(getter):
            return getter()
        library_manager = getattr(self, "_library_manager", None)
        if library_manager is None:
            return None
        service = getattr(library_manager, "edit_service", None)
        if callable(getattr(service, "read_adjustments", None)):
            return service
        return service() if callable(service) else service

    def select_next(self) -> None:
        self._request_relative_asset(1)

    def select_previous(self) -> None:
        self._request_relative_asset(-1)

    def _request_relative_asset(self, delta: int) -> None:
        """Coalesce relative navigation without losing individual steps."""

        row_count = self._asset_model.rowCount()
        if row_count <= 0 or delta == 0:
            return
        detail_vm = getattr(self, "_detail_vm", None)
        state_property = getattr(detail_vm, "selection_state", None)
        selection_state = getattr(state_property, "value", None)
        if selection_state in {
            MediaSelectionState.ANCHOR_RESOLVING,
            MediaSelectionState.ANCHOR_UNRESOLVED,
            MediaSelectionState.FALLBACK_PENDING,
        }:
            if delta > 0:
                detail_vm.next()
            else:
                detail_vm.previous()
            return
        pending_row = self._pending_play_row
        requested_row = getattr(self, "_requested_play_row", None)
        if pending_row is not None:
            base_row = pending_row
        elif requested_row is not None:
            base_row = requested_row
        else:
            base_row = self.current_row()
        if base_row < 0:
            if delta < 0:
                return
            target_row = 0
        else:
            target_row = max(0, min(row_count - 1, base_row + int(delta)))
        if target_row == base_row:
            return
        self.play_asset(target_row)

    def replay_live_photo(self) -> None:
        presentation = self._current_presentation
        if presentation is None or not presentation.is_live:
            return
        self._autoplay_live_motion(presentation)

    def rotate_current_asset(self) -> None:
        self._detail_vm.rotate_current()

    def _handle_rotate_requested(self, path: object, is_video: object) -> None:
        if not isinstance(path, Path):
            return
        is_video_value = bool(is_video)
        try:
            edit_service = self._edit_service()
            if edit_service is None:
                raise RuntimeError("Edit service is unavailable")
            previous_adjustments = dict(edit_service.read_adjustments(path) or {})
            if is_video_value:
                updates = self._player_view.video_area.rotate_image_ccw()
            else:
                updates = self._player_view.image_viewer.rotate_image_ccw()
            committed_adjustments = {**previous_adjustments, **updates}
            if not self._adjustment_committer.commit(
                path,
                committed_adjustments,
                reason="rotate",
            ):
                restored = self._player_view.apply_committed_adjustments(
                    path,
                    previous_adjustments,
                    "rotate_rollback",
                )
                if not restored and is_video_value:
                    self._player_view.video_area.apply_committed_adjustments(
                        previous_adjustments
                    )
                elif not restored:
                    self._player_view.image_viewer.set_adjustments(
                        previous_adjustments
                    )
                return
            self._player_view.apply_committed_adjustments(
                path,
                committed_adjustments,
                "rotate",
            )
        except Exception:
            LOGGER.exception("Failed to rotate %s", path)

    def _refresh_info_panel(self, info: dict) -> None:
        if not self._info_panel:
            return
        self._ensure_info_panel_metadata_state()
        capabilities = self._map_runtime_capabilities()
        location_enabled = self._refresh_location_extension_state()
        local_info = dict(info)
        next_rel = str(local_info.get("rel") or local_info.get("name") or "") or None
        current_rel_getter = getattr(self._info_panel, "current_rel", None)
        current_rel = current_rel_getter() if callable(current_rel_getter) else None
        asset_changed = current_rel is not None and current_rel != next_rel
        abs_path = local_info.get("abs")
        path_key = self._info_panel_path_key(abs_path)
        if path_key is not None:
            cached = self._info_panel_metadata_cache.get(path_key)
            if cached:
                local_info = self._merge_info_panel_metadata(local_info, cached)
        current_path = Path(path_key) if path_key is not None else None
        needs_enrichment = self._info_panel_metadata_needs_enrichment(local_info)
        should_queue_enrichment = bool(
            path_key is not None
            and needs_enrichment
            and path_key not in self._info_panel_metadata_attempted
            and path_key not in self._info_panel_metadata_inflight
        )
        is_loading = bool(
            path_key is not None
            and needs_enrichment
            and (
                should_queue_enrichment
                or path_key in self._info_panel_metadata_inflight
            )
        )
        if is_loading:
            local_info["_metadata_loading"] = True
        else:
            local_info.pop("_metadata_loading", None)
        with self._info_panel_content_update():
            if asset_changed:
                self._info_panel.set_face_actions_enabled(False)
                self._info_panel.set_face_action_candidates([])
                self._info_panel.set_asset_faces([])
            self._info_panel.set_location_capability(
                enabled=location_enabled,
                preview_enabled=self._info_panel_preview_enabled(
                    capabilities,
                    location_enabled=location_enabled,
                ),
                fallback_text=_LOCATION_EXTENSION_PROMPT,
            )
            self._info_panel.set_asset_metadata(local_info)
            location_assign_path = getattr(self, "_location_assign_path", None)
            self._info_panel.set_location_busy(
                bool(getattr(self, "_location_assign_inflight", False))
                and location_assign_path is not None
                and current_path == location_assign_path
            )
            presentation = getattr(self, "_current_presentation", None)
            self._refresh_info_panel_faces(
                presentation.asset_id if presentation is not None else None
            )
            recognition_ready = bool(
                getattr(self, "_manual_face_worker_factory", None) is not None
                and getattr(self, "_people_service", None) is not None
            )
            self._info_panel.set_face_actions_enabled(recognition_ready)
        if should_queue_enrichment:
            self._queue_info_panel_metadata_enrichment(
                Path(path_key),
                is_video=bool(local_info.get("is_video")),
            )
    def _refresh_location_extension_state(self) -> bool:
        enabled = False
        capabilities = self._map_runtime_capabilities()
        if capabilities is not None:
            enabled = bool(capabilities.location_search_available)
        if not enabled:
            self._reset_location_search_service()
            return False
        return True

    @staticmethod
    def _info_panel_preview_enabled(
        capabilities,
        *,
        location_enabled: bool = False,
    ) -> bool:
        if capabilities is None:
            return bool(location_enabled)
        return bool(
            location_enabled
            and
            getattr(capabilities, "display_available", False)
            and getattr(capabilities, "osmand_extension_available", False)
        )

    def _map_runtime_capabilities(self):
        map_runtime = self._ensure_map_runtime()
        capabilities_getter = getattr(map_runtime, "capabilities", None)
        if callable(capabilities_getter):
            return capabilities_getter()
        return None

    def _map_runtime_package_root(self) -> Path | None:
        map_runtime = self._ensure_map_runtime()
        package_root_getter = getattr(map_runtime, "package_root", None)
        if callable(package_root_getter):
            try:
                package_root = package_root_getter()
            except Exception:
                LOGGER.debug("Failed to resolve playback map runtime package root", exc_info=True)
            else:
                if package_root is not None:
                    return Path(package_root)
        package_root = getattr(map_runtime, "_package_root", None)
        if package_root is not None:
            return Path(package_root)
        return None

    def _ensure_map_runtime(self) -> MapRuntimePort | None:
        map_runtime = getattr(self, "_map_runtime", None)
        if map_runtime is not None:
            return map_runtime
        library_manager = getattr(self, "_library_manager", None)
        library_runtime = getattr(library_manager, "map_runtime", None)
        if library_runtime is not None:
            self._map_runtime = library_runtime
            return library_runtime
        try:
            fallback_runtime = SessionMapRuntimeService()
        except Exception:
            LOGGER.debug("Failed to create fallback session map runtime", exc_info=True)
            return None
        self._map_runtime = fallback_runtime
        return fallback_runtime

    def _reset_location_search_service(self, *, clear_cache: bool = False) -> None:
        controller = getattr(self, "_location_search_controller", None)
        if controller is not None:
            controller.reset()
            if clear_cache:
                controller.clear_cache()

    @Slot(str)
    def _handle_location_query_changed(self, query: str) -> None:
        info_panel = getattr(self, "_info_panel", None)
        if info_panel is None:
            return

        if not query.strip():
            self._reset_location_search_service()
            info_panel.set_location_suggestions([])
            return

        if not self._refresh_location_extension_state():
            info_panel.set_location_suggestions([])
            return
        if self._location_assign_inflight:
            self._reset_location_search_service()
            info_panel.set_location_suggestions([])
            return

        presentation = getattr(self, "_current_presentation", None)
        if presentation is None:
            self._reset_location_search_service()
            info_panel.set_location_suggestions([])
            return

        locale = QLocale.system().bcp47Name()
        self._ensure_location_search_controller().search(
            query,
            target_path=presentation.path,
            package_root=self._map_runtime_package_root(),
            locale=locale,
        )

    @Slot(int, object, str, object)
    def _handle_location_suggestions_ready(
        self,
        _token: int,
        target_path: object,
        _query: str,
        suggestions_obj: object,
    ) -> None:
        info_panel = getattr(self, "_info_panel", None)
        current_presentation = getattr(self, "_current_presentation", None)
        if (
            info_panel is None
            or current_presentation is None
            or current_presentation.path != Path(target_path)
            or self._location_assign_inflight
        ):
            return
        suggestions = list(suggestions_obj) if isinstance(suggestions_obj, list) else []
        info_panel.set_location_suggestions(suggestions)

    @Slot(int, object, str, str)
    def _handle_location_search_failed(
        self,
        _token: int,
        target_path: object,
        query: str,
        message: str,
    ) -> None:
        info_panel = getattr(self, "_info_panel", None)
        current_presentation = getattr(self, "_current_presentation", None)
        if (
            info_panel is None
            or current_presentation is None
            or current_presentation.path != Path(target_path)
        ):
            return
        LOGGER.warning("Offline location search failed for query %r: %s", query, message)
        info_panel.set_location_suggestions([])

    @Slot(str, object)
    def _handle_location_confirm_requested(self, query: str, suggestion_obj: object) -> None:
        if self._location_assign_inflight or not self._refresh_location_extension_state():
            return
        if not all(
            hasattr(suggestion_obj, attribute)
            for attribute in ("display_name", "latitude", "longitude")
        ):
            return
        presentation = getattr(self, "_current_presentation", None)
        if presentation is None:
            return

        rel_value = presentation.info.get("rel")
        if not isinstance(rel_value, str) or not rel_value.strip():
            return

        library_root = None
        library_manager = getattr(self, "_library_manager", None)
        if library_manager is not None:
            library_root = library_manager.root()
        if library_root is None:
            library_root = self._asset_model.store.library_root()
        if library_root is None:
            return
        asset_rel = self._location_assignment_asset_rel(
            presentation.path,
            Path(library_root),
            rel_value,
        )

        self._ensure_location_search_controller().reset()

        display_name = suggestion_obj.display_name.strip() or query.strip()
        if not display_name:
            return
        self._location_assign_inflight = True
        self._location_assign_path = presentation.path
        if self._info_panel is not None:
            self._info_panel.set_location_busy(True)
            self._info_panel.set_location_suggestions([])
        existing_metadata = dict(presentation.info)
        stored_metadata = self._asset_model.metadata_for_path(presentation.path)
        if isinstance(stored_metadata, dict):
            existing_metadata.update(stored_metadata)

        assignment: LocationAssignment | None = None
        try:
            service_type = LocationAssignmentService
            repository_type = IndexStoreLocationAssignmentRepository
            if service_type is None:
                service_type = getattr(self, "_location_assignment_service_factory", None)
            if repository_type is None:
                repository_type = getattr(
                    self,
                    "_location_assignment_repository_factory",
                    None,
                )
            if service_type is None or repository_type is None:
                raise RuntimeError("Location/Info domain has not been initialised")

            service = service_type(
                repository_type(Path(library_root)),
                self._event_bus,
            )
            assignment = service.assign(
                asset_path=presentation.path,
                asset_rel=asset_rel,
                display_name=display_name,
                latitude=float(suggestion_obj.latitude),
                longitude=float(suggestion_obj.longitude),
                is_video=bool(presentation.is_video),
                existing_metadata=existing_metadata,
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Failed to assign location", exc_info=True)
            self._handle_location_assignment_error(str(exc))
            self._location_assign_inflight = False
            self._location_assign_path = None
            if self._info_panel is not None:
                self._info_panel.set_location_busy(False)
            return

        self._project_location_assignment(assignment)
        if presentation.is_video:
            self._defer_location_video_loading(presentation.path)
            self._release_current_video_for_location_write(presentation)
        queue = getattr(self, "_location_write_queue", None)
        try:
            if queue is None:
                raise RuntimeError("write-back queue unavailable")
            self._location_write_jobs_by_path[presentation.path] = assignment.write_job.job_id
            queue.enqueue(assignment.write_job)
        except Exception as exc:  # noqa: BLE001
            message = str(exc)
            LOGGER.warning(
                "Location saved locally, but original-file write-back could not be queued: %s",
                message,
                exc_info=True,
            )
            self._queue_location_file_write_warning(message)
            if presentation.is_video:
                self._allow_location_video_loading(presentation.path)
                self._restore_video_released_for_location_write()
        finally:
            self._location_assign_inflight = False
            self._location_assign_path = None
            if self._info_panel is not None:
                self._info_panel.set_location_busy(False)

    @staticmethod
    def _location_assignment_asset_rel(
        asset_path: Path,
        library_root: Path,
        fallback_rel: str,
    ) -> str:
        try:
            return (
                Path(asset_path)
                .resolve()
                .relative_to(Path(library_root).resolve())
                .as_posix()
            )
        except (OSError, RuntimeError, ValueError):
            pass
        try:
            return Path(asset_path).relative_to(Path(library_root)).as_posix()
        except ValueError:
            return fallback_rel.strip()

    def _release_current_video_for_location_write(self, presentation: DetailPresentation) -> None:
        if not presentation.is_video:
            return
        video_area = getattr(getattr(self, "_player_view", None), "video_area", None)
        current_source = getattr(video_area, "current_source", None)
        stop = getattr(video_area, "stop", None)
        is_playing = getattr(video_area, "is_playing", None)
        current_position = getattr(video_area, "current_position", None)
        if not callable(current_source) or not callable(stop):
            return
        if current_source() != presentation.path:
            return
        self._location_released_video_path = presentation.path
        self._location_released_video_was_playing = (
            bool(is_playing()) if callable(is_playing) else False
        )
        self._location_released_video_position_ms = (
            max(0, int(current_position())) if callable(current_position) else None
        )
        self._player_view.show_placeholder(_location_video_write_placeholder())
        self._player_bar.setEnabled(False)
        stop()

    def _restore_video_released_for_location_write(self) -> None:
        released_path = getattr(self, "_location_released_video_path", None)
        if released_path is None:
            return
        was_playing = bool(getattr(self, "_location_released_video_was_playing", False))
        position_ms = getattr(self, "_location_released_video_position_ms", None)
        self._location_released_video_path = None
        self._location_released_video_was_playing = False
        self._location_released_video_position_ms = None
        presentation = getattr(self, "_current_presentation", None)
        if (
            presentation is None
            or presentation.path != released_path
            or not presentation.is_video
            or not self._router.is_detail_view_active()
        ):
            return
        self._render_presentation(presentation)
        if isinstance(position_ms, int) and position_ms > 0:
            self._player_view.video_area.seek(position_ms)
        if not was_playing:
            self._player_view.video_area.pause()

    def _project_location_assignment(self, assignment: LocationAssignment) -> None:
        metadata = self._merged_location_assignment_metadata(
            assignment.asset_path,
            assignment.metadata,
        )
        row = self._asset_model.row_for_path(assignment.asset_path)
        if row is not None:
            self._asset_model.store.update_asset_metadata(row, dict(metadata))

        self._apply_location_assignment_to_current_presentation(
            assignment.asset_path,
            metadata,
            display_name=assignment.display_name,
            refresh_info_panel=True,
        )
        self._remember_confirmed_location_metadata(
            assignment.asset_path,
            metadata,
        )
        library_manager = getattr(self, "_library_manager", None)
        invalidate = getattr(library_manager, "invalidate_geotagged_assets_cache", None)
        if callable(invalidate):
            try:
                invalidate(emit_tree_updated=False)
            except Exception:  # noqa: BLE001
                LOGGER.warning("Failed to refresh geotagged asset caches", exc_info=True)
        invalidate_location_session = getattr(self, "_location_session_invalidator", None)
        if callable(invalidate_location_session):
            try:
                invalidate_location_session()
            except Exception:  # noqa: BLE001
                LOGGER.warning("Failed to invalidate cached location-session data", exc_info=True)

    def _merged_location_assignment_metadata(
        self,
        asset_path: Path,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        presentation = getattr(self, "_current_presentation", None)
        if presentation is not None and presentation.path == asset_path:
            merged.update(presentation.info)
        asset_metadata = self._asset_model.metadata_for_path(asset_path)
        if isinstance(asset_metadata, dict):
            merged.update(asset_metadata)
        merged.update(metadata)
        return merged

    def _remember_confirmed_location_metadata(
        self,
        path: Path,
        metadata: dict[str, Any],
    ) -> None:
        if not hasattr(self, "_confirmed_location_metadata"):
            self._confirmed_location_metadata = {}
        location_metadata = {
            key: metadata[key]
            for key in ("gps", "location", "location_name", "place")
            if key in metadata
        }
        self._confirmed_location_metadata[Path(path)] = location_metadata

    def _confirmed_location_metadata_for_path(self, path: Path) -> dict[str, Any] | None:
        confirmed = getattr(self, "_confirmed_location_metadata", None)
        if not isinstance(confirmed, dict):
            return None
        metadata = confirmed.get(Path(path))
        return metadata if isinstance(metadata, dict) else None

    def _clear_confirmed_location_metadata(self) -> None:
        confirmed = getattr(self, "_confirmed_location_metadata", None)
        if isinstance(confirmed, dict):
            confirmed.clear()

    def _apply_location_metadata_to_presentation(
        self,
        presentation: DetailPresentation,
        metadata: dict[str, Any],
        *,
        display_name: object = None,
    ) -> DetailPresentation:
        merged_info = self._merge_info_panel_metadata(presentation.info, metadata)
        location_value = (
            metadata.get("location")
            or metadata.get("place")
            or metadata.get("location_name")
            or display_name
        )
        location = (
            str(location_value).strip()
            if isinstance(location_value, str) and str(location_value).strip()
            else presentation.location
        )
        if location:
            merged_info["location"] = location

        return replace(
            presentation,
            info=merged_info,
            location=location,
        )

    def _apply_location_assignment_to_current_presentation(
        self,
        asset_path: Path,
        metadata: dict[str, Any],
        *,
        display_name: object = None,
        refresh_info_panel: bool = True,
    ) -> DetailPresentation | None:
        presentation = getattr(self, "_current_presentation", None)
        if presentation is None or presentation.path != asset_path:
            return None

        updated = self._apply_location_metadata_to_presentation(
            presentation,
            metadata,
            display_name=display_name,
        )
        if updated == presentation:
            return presentation
        self._current_presentation = updated
        self._update_header(updated)
        if (
            refresh_info_panel
            and self._info_panel is not None
            and updated.info_panel_visible
        ):
            self._refresh_info_panel(updated.info)
        return updated

    def _is_missing_exiftool_error(self, message: str) -> bool:
        normalized = message.casefold()
        return "exiftool" in normalized and (
            "not found" in normalized or "filenotfounderror" in normalized
        )

    def _queue_location_exiftool_missing_warning(self) -> None:
        QTimer.singleShot(0, self._show_location_exiftool_missing_warning)

    def _queue_location_file_write_warning(self, message: str) -> None:
        QTimer.singleShot(0, lambda: self._show_location_file_write_warning(message))

    def _location_warning_parent(self) -> QWidget | None:
        info_panel = getattr(self, "_info_panel", None)
        if info_panel is None:
            return None
        parent_widget = info_panel.parentWidget()
        return parent_widget if parent_widget is not None else info_panel

    def _show_location_exiftool_missing_warning(self) -> None:
        popup_parent = self._location_warning_parent()
        if popup_parent is None:
            return
        dialogs.show_warning(
            popup_parent,
            _LOCATION_EXIFTOOL_LIMITED_MESSAGE,
            title=_LOCATION_EXIFTOOL_LIMITED_TITLE,
        )

    def _show_location_file_write_warning(self, message: str) -> None:
        popup_parent = self._location_warning_parent()
        if popup_parent is None:
            return
        dialogs.show_warning(
            popup_parent,
            _LOCATION_FILE_WRITE_LIMITED_MESSAGE_TEMPLATE.format(reason=message.strip()),
            title=_LOCATION_FILE_WRITE_LIMITED_TITLE,
        )

    @Slot(object)
    def _handle_location_file_write_started(self, job: object) -> None:
        if not isinstance(job, LocationWriteJobRecord) or not job.is_video:
            return
        path = Path(job.asset_path)
        self._location_write_jobs_by_path[path] = job.job_id
        already_inflight = self._is_location_video_write_inflight(path)
        self._defer_location_video_loading(path)
        if already_inflight:
            return
        presentation = getattr(self, "_current_presentation", None)
        if (
            presentation is not None
            and presentation.path == path
            and presentation.is_video
        ):
            self._release_current_video_for_location_write(presentation)

    @Slot(object)
    def _handle_location_file_write_verified(self, result: object) -> None:
        from iPhoto.gui.services.location_file_write_queue import LocationFileWriteResult

        if not isinstance(result, LocationFileWriteResult):
            return
        self._location_write_jobs_by_path.pop(result.asset_path, None)
        self._complete_location_video_file_write(result.asset_path)

    @Slot(object)
    def _handle_location_file_write_failed(self, result: object) -> None:
        from iPhoto.gui.services.location_file_write_queue import LocationFileWriteResult

        if not isinstance(result, LocationFileWriteResult):
            return
        message = result.error or "unknown error"
        LOGGER.warning(
            "Location saved in the library, but GPS metadata was not written to %s: %s",
            result.asset_path,
            message,
        )
        self._location_write_jobs_by_path.pop(result.asset_path, None)
        if self._is_missing_exiftool_error(message):
            self._queue_location_exiftool_missing_warning()
        else:
            self._queue_location_file_write_warning(message)
        self._complete_location_video_file_write(result.asset_path)

    @Slot(str)
    def _handle_location_assignment_error(self, message: str) -> None:
        LOGGER.warning("Failed to assign location: %s", message)
        location_assign_path = getattr(self, "_location_assign_path", None)
        if location_assign_path is not None:
            self._allow_location_video_loading(location_assign_path)
        self._restore_video_released_for_location_write()
        info_panel = getattr(self, "_info_panel", None)
        if info_panel is not None:
            info_panel.set_location_busy(False)

    def _refresh_info_panel_faces(self, asset_id: str | None) -> None:
        info_panel = getattr(self, "_info_panel", None)
        if info_panel is None:
            return
        candidates = self._load_recognition_identity_suggestions(include_hidden=True)
        set_candidates = getattr(info_panel, "set_face_action_candidates", None)
        if callable(set_candidates):
            set_candidates(candidates)
        if not asset_id:
            info_panel.set_asset_faces([])
            return
        info_panel.set_asset_faces(self._compose_info_panel_faces(asset_id))

    def _compose_info_panel_faces(self, asset_id: str) -> list[object]:
        annotations = list(self._load_face_name_annotations(asset_id))
        pending = getattr(self, "_pending_manual_face_annotations", {}).get(asset_id, [])
        if pending:
            annotations.extend(pending)
        return annotations

    def _queue_pending_manual_face(
        self,
        asset_id: str,
        presentation: DetailPresentation,
        payload: dict[str, object],
    ) -> None:
        from iPhoto.people.repository import AssetFaceAnnotation

        requested_box = payload.get("requested_box")
        if (
            not isinstance(requested_box, tuple)
            or len(requested_box) != 4
            or not all(isinstance(value, int) for value in requested_box)
        ):
            return
        pending_faces = getattr(self, "_pending_manual_face_annotations", None)
        if not isinstance(pending_faces, dict):
            pending_faces = {}
            self._pending_manual_face_annotations = pending_faces
        sequence = int(getattr(self, "_pending_manual_face_sequence", 0)) + 1
        self._pending_manual_face_sequence = sequence
        name = payload.get("name")
        person_id = payload.get("person_id")
        image_width = presentation.info.get("w")
        image_height = presentation.info.get("h")
        pending_face = AssetFaceAnnotation(
            face_id=f"pending-manual-{sequence}",
            person_id=person_id if isinstance(person_id, str) and person_id else None,
            display_name=name.strip() if isinstance(name, str) and name.strip() else None,
            box_x=requested_box[0],
            box_y=requested_box[1],
            box_w=requested_box[2],
            box_h=requested_box[3],
            image_width=image_width if isinstance(image_width, int) and image_width > 0 else max(1, requested_box[0] + requested_box[2]),
            image_height=image_height if isinstance(image_height, int) and image_height > 0 else max(1, requested_box[1] + requested_box[3]),
            thumbnail_path=None,
            is_manual=True,
        )
        pending_faces.setdefault(asset_id, []).append(pending_face)

    def _clear_pending_manual_faces(self, asset_id: str | None) -> None:
        if not asset_id:
            return
        pending_faces = getattr(self, "_pending_manual_face_annotations", None)
        if isinstance(pending_faces, dict):
            pending_faces.pop(asset_id, None)

    def toggle_info_panel(self) -> None:
        self._detail_vm.toggle_info()

    def _prepare_info_panel_presentation(self) -> None:
        info_panel = getattr(self, "_info_panel", None)
        prepare = getattr(info_panel, "prepare_for_presentation", None)
        if callable(prepare):
            prepare()

    @Slot()
    def _handle_info_panel_dismissed(self) -> None:
        self._detail_vm.hide_info_panel(refresh_presentation=True)

    def _ensure_info_panel_metadata_state(self) -> None:
        if not hasattr(self, "_info_panel_metadata_cache"):
            self._info_panel_metadata_cache = {}
        if not hasattr(self, "_info_panel_metadata_inflight"):
            self._info_panel_metadata_inflight = set()
        if not hasattr(self, "_info_panel_metadata_attempted"):
            self._info_panel_metadata_attempted = set()

    def _clear_info_panel_metadata_state(self) -> None:
        self._ensure_info_panel_metadata_state()
        self._info_panel_metadata_cache.clear()
        self._info_panel_metadata_inflight.clear()
        self._info_panel_metadata_attempted.clear()

    def _info_panel_path_key(self, path: object) -> str | None:
        if isinstance(path, Path):
            return str(path)
        if isinstance(path, str) and path.strip():
            return str(Path(path))
        return None

    def _info_panel_metadata_needs_enrichment(self, info: dict[str, Any]) -> bool:
        is_video = bool(info.get("is_video"))
        return (
            (not info.get("frame_rate") or not info.get("lens"))
            if is_video
            else not info.get("iso")
        )

    def _merge_info_panel_metadata(
        self,
        base_info: dict[str, Any],
        extra_info: dict[str, Any],
    ) -> dict[str, Any]:
        merged = dict(base_info)
        merged.update({key: value for key, value in extra_info.items() if value is not None})
        merged.pop("_metadata_loading", None)
        return merged

    def _queue_info_panel_metadata_enrichment(self, path: Path, *, is_video: bool) -> None:
        self._ensure_info_panel_metadata_state()
        path_key = str(path)
        if path_key in self._info_panel_metadata_inflight:
            return
        self._info_panel_metadata_inflight.add(path_key)

        worker_factory = getattr(self, "_info_metadata_worker_factory", None)
        if worker_factory is None:
            self._info_panel_metadata_inflight.discard(path_key)
            return
        worker = worker_factory(path, is_video=is_video)
        worker.signals.ready.connect(self._handle_info_panel_metadata_ready)
        worker.signals.error.connect(self._handle_info_panel_metadata_error)
        worker.signals.finished.connect(self._handle_info_panel_metadata_finished)
        try:
            QThreadPool.globalInstance().start(worker, -1)
        except Exception:  # noqa: BLE001
            LOGGER.warning("Failed to start metadata enrichment worker for %s", path_key, exc_info=True)
            self._info_panel_metadata_inflight.discard(path_key)
            self._info_panel_metadata_attempted.discard(path_key)

    @Slot(object)
    def _handle_info_panel_metadata_ready(self, result: object) -> None:
        self._ensure_info_panel_metadata_state()
        path_key = str(result.path)
        # Evict oldest entry (insertion-order FIFO, Python 3.7+) before inserting
        # so the cache never grows beyond _INFO_PANEL_METADATA_CACHE_MAX entries.
        if len(self._info_panel_metadata_cache) >= _INFO_PANEL_METADATA_CACHE_MAX:
            evict_key = next(iter(self._info_panel_metadata_cache))
            del self._info_panel_metadata_cache[evict_key]
            self._info_panel_metadata_attempted.discard(evict_key)
        self._info_panel_metadata_cache[path_key] = dict(result.metadata)

        if not self._info_panel or not self._info_panel.isVisible():
            return
        presentation = self._current_presentation
        if presentation is None or presentation.path != result.path:
            return
        local_info = self._merge_info_panel_metadata(presentation.info, result.metadata)
        with self._info_panel_content_update():
            self._info_panel.set_asset_metadata(local_info)

    def _info_panel_content_update(self):
        info_panel = getattr(self, "_info_panel", None)
        content_update = getattr(info_panel, "content_update", None)
        if callable(content_update):
            context = content_update()
            if hasattr(context, "__enter__") and hasattr(context, "__exit__"):
                return context
        return nullcontext()

    @Slot(str, str)
    def _handle_info_panel_metadata_error(self, path_key: str, message: str) -> None:
        LOGGER.debug(
            "Failed to enrich info-panel metadata for %s: %s",
            path_key,
            message,
        )

    @Slot(str)
    def _handle_info_panel_metadata_finished(self, path_key: str) -> None:
        self._ensure_info_panel_metadata_state()
        self._info_panel_metadata_inflight.discard(path_key)
        self._info_panel_metadata_attempted.add(path_key)

    @Slot()
    def _handle_manual_face_add_requested(self) -> None:
        presentation = getattr(self, "_current_presentation", None)
        overlay = getattr(self, "_face_name_overlay", None)
        if overlay is None or presentation is None or presentation.is_video or not presentation.asset_id:
            return
        self._apply_recognition_identity_suggestions(overlay, include_hidden=False)
        overlay.set_annotations(self._load_face_name_annotations(presentation.asset_id))
        overlay.set_overlay_active(True)
        overlay.start_manual_face()

    @Slot(object)
    def _handle_manual_face_submitted(self, payload: object) -> None:
        if self._manual_face_add_inflight:
            return
        presentation = getattr(self, "_current_presentation", None)
        overlay = getattr(self, "_face_name_overlay", None)
        library_root = self._people_service.library_root()
        if (
            presentation is None
            or overlay is None
            or library_root is None
            or not presentation.asset_id
            or not isinstance(payload, dict)
        ):
            return
        requested_box = payload.get("requested_box")
        if (
            not isinstance(requested_box, tuple)
            or len(requested_box) != 4
            or not all(isinstance(value, int) for value in requested_box)
        ):
            overlay.show_manual_error("The face circle could not be mapped back to the photo.")
            return
        identity_key = payload.get("identity_key")
        selected_identity_key = identity_key if isinstance(identity_key, str) else None
        selected_person_id = payload.get("person_id") if isinstance(payload.get("person_id"), str) else None
        worker_person_id = selected_person_id
        if selected_identity_key and selected_identity_key.startswith("pet:"):
            worker_person_id = None
            self._manual_face_pending_merge_target = selected_identity_key
        else:
            self._manual_face_pending_merge_target = None
        self._manual_face_add_inflight = True
        self._manual_face_inflight_asset_id = presentation.asset_id
        overlay.set_manual_face_busy(True)
        self._queue_pending_manual_face(presentation.asset_id, presentation, payload)
        self._refresh_info_panel_faces(presentation.asset_id)
        worker_type = ManualFaceAddWorker
        if worker_type is None:
            worker_type = getattr(self, "_manual_face_worker_factory", None)
        if worker_type is None:
            self._handle_manual_face_error("Recognition domain has not been initialised")
            return
        worker = worker_type(
            library_root=library_root,
            asset_id=presentation.asset_id,
            requested_box=requested_box,
            name_or_none=payload.get("name") if isinstance(payload.get("name"), str) else None,
            person_id=worker_person_id,
            people_service=self._people_service,
        )
        worker.signals.ready.connect(self._handle_manual_face_ready)
        worker.signals.error.connect(self._handle_manual_face_error)
        worker.signals.finished.connect(self._handle_manual_face_finished)
        QThreadPool.globalInstance().start(worker, -1)

    @Slot(object)
    def _handle_manual_face_ready(self, result: object) -> None:
        submitted_asset_id = self._manual_face_inflight_asset_id
        if submitted_asset_id:
            self._clear_pending_manual_faces(submitted_asset_id)
        merge_target = getattr(self, "_manual_face_pending_merge_target", None)
        if isinstance(merge_target, str) and merge_target.startswith("pet:"):
            person_id = getattr(result, "person_id", None)
            if isinstance(person_id, str) and person_id:
                merge_service = getattr(self, "_recognition_merge_service", None)
                if merge_service is None:
                    merged = None
                else:
                    try:
                        outcome = merge_service.merge(f"person:{person_id}", merge_target)
                        merged = outcome if outcome.merged else None
                    except (sqlite3.Error, OSError):
                        LOGGER.exception(
                            "Failed to merge manual face person %s into %s",
                            person_id,
                            merge_target,
                        )
                        merged = None
                if merged is None:
                    LOGGER.warning(
                        "Manual face was saved but could not be merged into %s",
                        merge_target,
                    )
                    overlay = getattr(self, "_face_name_overlay", None)
                    if overlay is not None:
                        overlay.show_manual_error(
                            "The face was saved, but could not be linked to that name."
                        )
        self._refresh_recognition_views_after_mutation()

    @Slot(str)
    def _handle_manual_face_error(self, message: str) -> None:
        self._manual_face_pending_merge_target = None
        submitted_asset_id = getattr(self, "_manual_face_inflight_asset_id", None)
        if not submitted_asset_id:
            presentation = getattr(self, "_current_presentation", None)
            submitted_asset_id = presentation.asset_id if presentation is not None else None
        if submitted_asset_id:
            self._clear_pending_manual_faces(submitted_asset_id)
        presentation = getattr(self, "_current_presentation", None)
        if (
            submitted_asset_id
            and presentation is not None
            and presentation.asset_id == submitted_asset_id
        ):
            self._refresh_info_panel_faces(submitted_asset_id)
        overlay = getattr(self, "_face_name_overlay", None)
        if overlay is not None:
            overlay.set_manual_face_busy(False)
            overlay.show_manual_error(message)

    @Slot()
    def _handle_manual_face_finished(self) -> None:
        self._manual_face_add_inflight = False
        self._manual_face_inflight_asset_id = None
        self._manual_face_pending_merge_target = None
        overlay = getattr(self, "_face_name_overlay", None)
        if overlay is not None:
            overlay.set_manual_face_busy(False)

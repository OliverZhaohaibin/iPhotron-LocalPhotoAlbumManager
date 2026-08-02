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
    QItemSelectionModel,
    QLocale,
    QModelIndex,
    QObject,
    QThreadPool,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import QAction, QColor, QPalette

from iPhoto.application.ports import EditServicePort, LocationWriteJobRecord, MapRuntimePort
from iPhoto.application.services.location_assignment_service import (
    LocationAssignment,
    LocationAssignmentService,
)
from iPhoto.config import PLAY_ASSET_DEBOUNCE_MS
from iPhoto.gui.coordinators.view_router import ViewRouter
from iPhoto.gui.detail_pipeline import (
    AssetSourceIdentity,
    DetailRenderTransaction,
    PlaybackAsyncToken,
)
from iPhoto.gui.detail_profile import log_detail_profile
from iPhoto.gui.detail_render_coordinator import DetailRenderCoordinator
from iPhoto.gui.i18n import tr
from iPhoto.gui.services.location_file_write_queue import (
    LocationFileWriteQueue,
    LocationFileWriteResult,
)
from iPhoto.gui.services.location_search_controller import LocationSearchController
from iPhoto.gui.ui.controllers.edit_zoom_handler import EditZoomHandler
from iPhoto.gui.ui.controllers.header_controller import HeaderController
from iPhoto.gui.ui.icons import load_icon
from iPhoto.gui.ui.tasks.info_panel_metadata_worker import (
    InfoPanelMetadataResult,
    InfoPanelMetadataWorker,
)
from iPhoto.gui.ui.tasks.manual_face_add_worker import ManualFaceAddWorker
from iPhoto.gui.ui.widgets import dialogs
from iPhoto.gui.ui.widgets.recognition_annotations import (
    RecognitionIdentitySuggestion,
    pet_annotation_adapter,
)
from iPhoto.gui.viewmodels.detail_viewmodel import DetailPresentation, DetailViewModel
from iPhoto.infrastructure.repositories.location_assignment_repository import (
    IndexStoreLocationAssignmentRepository,
)
from iPhoto.library.runtime_controller import LibraryRuntimeController
from iPhoto.people.repository import AssetFaceAnnotation
from iPhoto.people.service import PeopleService
from iPhoto.pets.service import PetService
from maps.osmand_search import SearchSuggestion

if TYPE_CHECKING:
    from iPhoto.utils.settings import Settings
    from PySide6.QtWidgets import QPushButton, QSlider, QToolButton, QWidget

    from iPhoto.events.bus import EventBus
    from iPhoto.gui.coordinators.navigation_coordinator import NavigationCoordinator
    from iPhoto.gui.ui.controllers.player_view_controller import PlayerViewController
    from iPhoto.gui.ui.media import MediaAdjustmentCommitter
    from iPhoto.gui.ui.widgets.face_name_overlay import FaceNameOverlayWidget
    from iPhoto.gui.ui.widgets.filmstrip_view import FilmstripView
    from iPhoto.gui.ui.widgets.info_panel import InfoPanel
    from iPhoto.gui.ui.widgets.player_bar import PlayerBar
    from iPhoto.gui.viewmodels.gallery_list_model_adapter import GalleryListModelAdapter

LOGGER = logging.getLogger(__name__)


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
        library_binding_token_getter: Callable[[], object | None] | None = None,
    ) -> None:
        super().__init__()
        self._player_bar = player_bar
        self._player_view = player_view
        self._router = router
        self._asset_model = asset_model
        self._detail_vm = detail_vm
        self._adjustment_committer = adjustment_committer

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
        self._people_service = people_service or PeopleService()
        self._pet_service = pet_service or PetService()
        self._people_dashboard_refresh_callback = people_dashboard_refresh_callback
        self._library_manager = library_manager
        self._location_session_invalidator = location_session_invalidator
        self._map_runtime = map_runtime or getattr(library_manager, "map_runtime", None)
        self._event_bus = event_bus
        self._location_write_queue = location_write_queue
        self._library_binding_token_getter = library_binding_token_getter
        self._library_binding_token = (
            library_binding_token_getter()
            if library_binding_token_getter is not None
            else None
        )
        self._asset_generation = 0
        self._active_async_token: PlaybackAsyncToken | None = None
        self._location_search_async_tokens: dict[int, PlaybackAsyncToken] = {}
        self._location_search_dispatch_token: PlaybackAsyncToken | None = None

        self._is_playing = False
        self._navigation: NavigationCoordinator | None = None
        self._info_panel: InfoPanel | None = None
        self._active_live_motion: Path | None = None
        self._active_live_still: Path | None = None
        self._detail_render_lifecycle = DetailRenderCoordinator(self)
        self._detail_generation = 0
        self._detail_render_transaction: DetailRenderTransaction | None = None
        self._live_transaction: DetailRenderTransaction | None = None
        self._resume_after_transition = False
        self._trim_in_ms = 0
        self._trim_out_ms = 0
        self._current_presentation: DetailPresentation | None = None
        self._info_panel_metadata_cache: dict[str, dict[str, Any]] = {}
        self._info_panel_metadata_inflight: set[str] = set()
        self._info_panel_metadata_tokens: dict[str, PlaybackAsyncToken | None] = {}
        self._info_panel_metadata_attempted: set[str] = set()
        self._play_profile_started_at: float | None = None
        self._play_profile_row: int | None = None
        self._manual_face_add_inflight = False
        self._manual_face_inflight_asset_id: str | None = None
        self._manual_face_pending_merge_target: str | None = None
        self._pending_manual_face_annotations: dict[str, list[AssetFaceAnnotation]] = {}
        self._pending_manual_face_sequence = 0
        self._location_search_controller = LocationSearchController(self)
        self._location_search_controller.suggestionsReady.connect(
            self._handle_location_suggestions_ready
        )
        self._location_search_controller.searchFailed.connect(
            self._handle_location_search_failed
        )
        self._location_assign_inflight = False
        self._location_assign_path: Path | None = None
        self._confirmed_location_metadata: dict[Path, dict[str, Any]] = {}
        self._location_released_video_path: Path | None = None
        self._location_released_video_was_playing = False
        self._location_released_video_position_ms: int | None = None
        self._location_video_write_inflight_paths: set[Path] = set()
        self._location_write_jobs_by_path: dict[Path, str] = {}
        self._location_write_tokens_by_job: dict[str, PlaybackAsyncToken] = {}
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
        self._play_debounce = QTimer(self)
        self._play_debounce.setSingleShot(True)
        self._play_debounce.setInterval(PLAY_ASSET_DEBOUNCE_MS)
        self._play_debounce.timeout.connect(self._execute_pending_play)

        self._connect_signals()
        self._setup_zoom_handler()
        self._restore_filmstrip_preference()

    def rebind_library(
        self,
        binding_token: object | None,
        *,
        session_change: bool,
    ) -> None:
        """Invalidate library-scoped playback only for a real session change."""

        if not session_change:
            return
        self._library_binding_token = binding_token
        self._asset_generation += 1
        self._active_async_token = None
        self._location_search_async_tokens.clear()
        self._location_search_dispatch_token = None
        getattr(self, "_location_write_tokens_by_job", {}).clear()
        getattr(self, "_location_write_jobs_by_path", {}).clear()
        getattr(self, "_location_video_write_inflight_paths", set()).clear()
        self._location_assign_inflight = False
        self._location_assign_path = None
        self._manual_face_add_inflight = False
        self._manual_face_inflight_asset_id = None
        self._manual_face_pending_merge_target = None
        getattr(self, "_pending_manual_face_annotations", {}).clear()
        self._retire_live_transaction()
        self._reset_location_search_service(clear_cache=True)
        self._player_view.invalidate_async_work()
        self._player_view.video_area.stop()
        self._player_view.show_placeholder()
        self._hide_face_name_overlay(clear_annotations=True)
        self._is_playing = False
        self._current_presentation = None
        self._clear_info_panel_metadata_state()
        self._clear_confirmed_location_metadata()

    def _current_library_epoch(self) -> int:
        getter = getattr(self, "_library_binding_token_getter", None)
        binding = getter() if callable(getter) else getattr(self, "_library_binding_token", None)
        return max(0, int(getattr(binding, "epoch", 0) or 0))

    def _is_async_token_current(self, token: PlaybackAsyncToken | None) -> bool:
        if token is None:
            return True
        return (
            token == getattr(self, "_active_async_token", None)
            and token.library_epoch == self._current_library_epoch()
        )

    def set_navigation_coordinator(self, nav: NavigationCoordinator) -> None:
        self._navigation = nav

    def _render_transaction_coordinator(self) -> DetailRenderCoordinator:
        lifecycle = getattr(self, "_detail_render_lifecycle", None)
        if lifecycle is None:
            lifecycle = DetailRenderCoordinator()
            self._detail_render_lifecycle = lifecycle
        return lifecycle

    def set_people_service(self, service: PeopleService | None) -> None:
        self._people_service = service or PeopleService()
        self._refresh_face_name_overlay_for_current_presentation()

    def set_pet_service(self, service: PetService | None) -> None:
        self._pet_service = service or PetService()
        self._refresh_face_name_overlay_for_current_presentation()

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

    def set_people_library_root(self, library_root: Path | None) -> None:
        people_service = getattr(self, "_people_service", None)
        service_matches_root = (
            isinstance(people_service, PeopleService)
            and people_service.library_root() == library_root
        )
        if not service_matches_root:
            bound_people_service = getattr(self._library_manager, "people_service", None)
            if (
                isinstance(bound_people_service, PeopleService)
                and bound_people_service.library_root() == library_root
            ):
                self._people_service = bound_people_service
            elif library_root is None:
                self._people_service = PeopleService()
            else:
                self._people_service = PeopleService(library_root)
        self._refresh_face_name_overlay_for_current_presentation()

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
        if current_row < 0 or not self._router.is_detail_view_active():
            self.play_asset(target_row)
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
        self._player_view.video_area.playbackStateChanged.connect(self._sync_playback_state)
        self._player_view.video_area.playbackFinished.connect(self._handle_playback_finished)
        still_presented = getattr(self._player_view, "stillFramePresented", None)
        if still_presented is not None:
            still_presented.connect(self._handle_still_frame_presented)
        video_presented = getattr(self._player_view, "videoFramePresented", None)
        if video_presented is not None:
            video_presented.connect(self._handle_video_frame_presented)
        self._player_view.imageLoadingFailed.connect(self._handle_image_load_failed)
        self._player_view.video_area.mediaLoadFailed.connect(self._handle_video_load_failed)
        self._player_view.video_area.durationChanged.connect(self._on_video_duration_changed)
        self._player_view.video_area.positionChanged.connect(self._on_video_position_changed)

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
        model = self._filmstrip_view.model()
        if hasattr(model, "mapToSource"):
            source_idx = model.mapToSource(index)
            if source_idx.isValid():
                self.play_asset(source_idx.row())
                return
        self.play_asset(index.row())

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
        self._play_profile_started_at = time.perf_counter()
        self._play_profile_row = row
        if not self._play_debounce.isActive() and self._pending_play_row is None:
            self._dispatch_play_row(row, reason="immediate")
            self._play_debounce.start()
            return
        self._pending_play_row = row
        if not self._play_debounce.isActive():
            self._play_debounce.start()

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
        self._clear_play_profile()
        self._asset_generation = max(0, int(getattr(self, "_asset_generation", 0))) + 1
        self._active_async_token = None
        tokens = getattr(self, "_location_search_async_tokens", None)
        if isinstance(tokens, dict):
            tokens.clear()
        player_view = getattr(self, "_player_view", None)
        invalidate_async_work = getattr(player_view, "invalidate_async_work", None)
        if callable(invalidate_async_work):
            invalidate_async_work()
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

    def _handle_presentation_changed(self, presentation: DetailPresentation) -> None:
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
        if not self._router.is_detail_view_active():
            self._clear_play_profile(presentation.row)
            return
        self._current_presentation = presentation
        row = presentation.row
        self._asset_model.set_current_row(row)
        self.assetChanged.emit(row)
        self._update_header(presentation)
        self._sync_filmstrip_selection(row)
        same_asset = (
            previous is not None
            and previous.row == presentation.row
            and previous.path == presentation.path
            and previous.reload_token == presentation.reload_token
            and previous.request_generation == presentation.request_generation
        )
        if same_asset:
            self._update_favorite_icon(presentation.is_favorite)
            if self._info_panel and presentation.info_panel_visible:
                self._refresh_info_panel(presentation.info)
                self._info_panel.show()
            elif self._info_panel and self._info_panel.isVisible() and not presentation.info_panel_visible:
                self._info_panel.close()
            self._clear_play_profile(presentation.row)
            return
        request_generation = int(getattr(presentation, "request_generation", 0))
        if request_generation <= 0:
            request_generation = max(0, int(getattr(self, "_asset_generation", 0))) + 1
        self._asset_generation = max(
            request_generation,
            int(getattr(self, "_asset_generation", 0)),
        )
        identity = presentation.source_identity or AssetSourceIdentity.from_info(
            presentation.path,
            presentation.info,
        )
        self._active_async_token = PlaybackAsyncToken.create(
            library_epoch=self._current_library_epoch(),
            asset_generation=request_generation,
            asset_id=presentation.asset_id or presentation.path.name,
            source_identity=identity,
        )
        media_kind = "video" if presentation.is_video else "image"
        if presentation.is_live and not presentation.is_video:
            media_kind = "live_motion"
        transaction = DetailRenderTransaction(
            generation=request_generation,
            asset_id=presentation.asset_id or presentation.path.name,
            media_kind=media_kind,
            source_identity=identity,
        )
        self._detail_render_transaction = transaction
        if media_kind == "live_motion":
            self._live_transaction = transaction
        else:
            self._live_transaction = None
        lifecycle = self._render_transaction_coordinator()
        lifecycle.begin(transaction)
        lifecycle.mark_routed(transaction.generation, row=row)
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

        if previous.row != current.row or previous.path != current.path:
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
        render_started = time.perf_counter()
        source = presentation.path
        self._active_live_motion = None
        self._active_live_still = None
        transaction = getattr(self, "_detail_render_transaction", None)
        if transaction is None or transaction.source_identity.path != source:
            generation = int(getattr(presentation, "request_generation", 0))
            if generation <= 0:
                generation = max(1, int(getattr(self, "_asset_generation", 0)) + 1)
            identity = presentation.source_identity or AssetSourceIdentity.from_info(
                source,
                presentation.info,
            )
            media_kind = "video" if presentation.is_video else "image"
            if presentation.is_live and not presentation.is_video:
                media_kind = "live_motion"
            transaction = DetailRenderTransaction(
                generation=generation,
                asset_id=presentation.asset_id or source.name,
                media_kind=media_kind,
                source_identity=identity,
            )
            self._detail_render_transaction = transaction
            lifecycle = self._render_transaction_coordinator()
            lifecycle.begin(transaction)
            lifecycle.mark_routed(transaction.generation, row=presentation.row)
        lifecycle = self._render_transaction_coordinator()
        if (
            not lifecycle.mark_preparing(transaction.generation)
            and not lifecycle.is_current(transaction.generation)
        ):
            return
        self._favorite_button.setEnabled(presentation.can_toggle_favorite)
        self._info_button.setEnabled(True)
        self._share_button.setEnabled(presentation.can_share)
        self._edit_button.setEnabled(presentation.can_edit)
        self._rotate_button.setEnabled(presentation.can_rotate)
        self._update_favorite_icon(presentation.is_favorite)

        self._zoom_slider.blockSignals(True)
        self._zoom_slider.setValue(100)
        self._zoom_slider.blockSignals(False)

        self._is_playing = False
        self._player_bar.set_playback_state(False)
        self._player_bar.set_position(0)

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
                self._player_view.begin_video_transaction(
                    transaction,
                    async_token=getattr(self, "_active_async_token", None),
                )
                self._player_view.show_video_surface(interactive=True)
                trim_range_ms = presentation.video_trim_range_ms
                if trim_range_ms is not None:
                    self._trim_in_ms, self._trim_out_ms = trim_range_ms
                else:
                    self._trim_in_ms = 0
                    self._trim_out_ms = 0
                has_trim = trim_range_ms is not None
                load_started = time.perf_counter()
                self._player_view.video_area.load_video(
                    source,
                    adjustments=presentation.video_adjustments,
                    trim_range_ms=trim_range_ms,
                    adjusted_preview=presentation.video_adjusted_preview,
                )
                log_detail_profile(
                    "playback",
                    "video.load_video",
                    (time.perf_counter() - load_started) * 1000.0,
                    path=source.name,
                    adjusted_preview=presentation.video_adjusted_preview,
                    has_trim=has_trim,
                )
                self._player_view.video_area.play()
                self._is_playing = True
                self._player_bar.setEnabled(True)
                self._zoom_handler.set_viewer(self._player_view.video_area)
                self._player_view.video_area.reset_zoom()
                self._zoom_widget.show()
        else:
            if self._player_view.video_area.has_video():
                self._player_view.video_area.stop()
            self._player_view.show_image_surface()
            display_started = time.perf_counter()
            async_token = getattr(self, "_active_async_token", None)
            self._player_view.display_image(
                source,
                asset_id=transaction.asset_id,
                request_generation=transaction.generation,
                transaction=transaction,
                source_identity=transaction.source_identity,
                async_token=async_token,
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
                self._refresh_face_name_overlay_for_presentation(presentation)

        if self._info_panel and presentation.info_panel_visible:
            self._refresh_info_panel(presentation.info)
            self._info_panel.show()
        elif self._info_panel and self._info_panel.isVisible() and not presentation.info_panel_visible:
            self._info_panel.close()
        log_detail_profile(
            "playback",
            "render_presentation.total",
            (time.perf_counter() - render_started) * 1000.0,
            path=source.name,
            is_video=presentation.is_video,
        )
        self._clear_play_profile(presentation.row)

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
        transaction = self._begin_or_reuse_live_transaction(presentation)
        self._active_live_motion = motion_path
        self._active_live_still = presentation.path
        self._hide_face_name_overlay(clear_annotations=False)
        self._player_view.defer_still_updates(True)
        self._player_view.show_video_surface(interactive=False)
        self._trim_in_ms = 0
        self._trim_out_ms = 0
        self._player_view.begin_video_transaction(
            transaction,
            async_token=getattr(self, "_active_async_token", None),
            source=motion_path,
            cancel_still=False,
        )
        self._player_view.video_area.load_video(
            motion_path,
            adjustments=None,
            trim_range_ms=None,
            adjusted_preview=False,
        )
        self._player_view.video_area.play()
        self._player_bar.setEnabled(False)
        self._is_playing = True
        self._detail_render_lifecycle.mark_preparing(transaction.generation)

    def _handle_playback_finished(self) -> None:
        if not self._active_live_motion or not self._active_live_still:
            return
        still = self._active_live_still
        self._active_live_motion = None
        transaction = getattr(self, "_live_transaction", None)
        applied_pending = self._player_view.apply_pending_still()
        self._player_view.defer_still_updates(False)
        if not applied_pending:
            async_token = getattr(self, "_active_async_token", None)
            if transaction is None:
                if async_token is None:
                    self._player_view.display_image(still)
                else:
                    self._player_view.display_image(still, async_token=async_token)
            else:
                kwargs = {"transaction": transaction}
                if async_token is not None:
                    kwargs["async_token"] = async_token
                self._player_view.display_image(still, **kwargs)
        self._player_bar.setEnabled(False)
        self._player_view.show_live_badge()
        self._player_view.set_live_replay_enabled(True)
        self._is_playing = False

    def _begin_or_reuse_live_transaction(
        self,
        presentation: DetailPresentation,
    ) -> DetailRenderTransaction:
        current = getattr(self, "_detail_render_transaction", None)
        if (
            current is not None
            and current.media_kind == "live_motion"
            and current.asset_id == presentation.asset_id
            and current.source_identity.path == presentation.path
        ):
            self._live_transaction = current
            return current
        lifecycle = getattr(self, "_detail_render_lifecycle", None)
        if lifecycle is None:
            lifecycle = DetailRenderCoordinator()
            self._detail_render_lifecycle = lifecycle
        lifecycle.cancel_current()
        self._detail_generation = max(
            int(getattr(self, "_asset_generation", 0)),
            int(getattr(self, "_detail_generation", 0)),
        ) + 1
        transaction = DetailRenderTransaction(
            generation=self._detail_generation,
            asset_id=presentation.asset_id or presentation.path.name,
            media_kind="live_motion",
            source_identity=AssetSourceIdentity.from_info(
                presentation.path,
                presentation.info,
            ),
            reason="click",
        )
        lifecycle.begin(transaction)
        lifecycle.mark_routed(transaction.generation, row=presentation.row)
        lifecycle.mark_preparing(transaction.generation)
        self._detail_render_transaction = transaction
        self._live_transaction = transaction
        return transaction

    def _retire_live_transaction(self) -> None:
        transaction = getattr(self, "_live_transaction", None)
        if transaction is None:
            return
        lifecycle = getattr(self, "_detail_render_lifecycle", None)
        if lifecycle is not None and lifecycle.current_generation == transaction.generation:
            lifecycle.cancel_current()
        self._live_transaction = None

    @Slot(int)
    def _handle_video_frame_presented(self, generation: int) -> None:
        transaction = getattr(self, "_detail_render_transaction", None) or getattr(
            self,
            "_live_transaction",
            None,
        )
        if (
            transaction is None
            or transaction.generation != int(generation)
            or transaction.media_kind not in {"video", "live_motion"}
        ):
            return
        self._render_transaction_coordinator().mark_surface_presented(
            transaction.generation,
            "live_motion_frame" if transaction.media_kind == "live_motion" else "video_frame",
        )

    def _handle_live_motion_first_frame(self, generation: int) -> None:
        """Compatibility entry point for the existing Live Photo contract."""

        self._handle_video_frame_presented(generation)

    @Slot(object, int)
    def _handle_still_frame_presented(self, source: object, generation: int) -> None:
        transaction = getattr(self, "_detail_render_transaction", None) or getattr(
            self,
            "_live_transaction",
            None,
        )
        presentation = getattr(self, "_current_presentation", None)
        try:
            presented_source = Path(source)
        except TypeError:
            return
        if (
            presentation is None
            or presentation.is_video
            or presentation.path != presented_source
            or transaction is None
            or transaction.generation != int(generation)
            or transaction.source_identity.path != presented_source
        ):
            return
        if not self._render_transaction_coordinator().mark_surface_presented(
            transaction.generation,
            "live_still" if transaction.media_kind == "live_motion" else "still",
        ):
            return
        self._refresh_face_name_overlay_for_current_presentation()
        self._prefetch_neighbor_rows()

    def _handle_live_still_presented(self, source: Path, generation: int) -> None:
        """Compatibility entry point for the existing Live Photo contract."""

        self._handle_still_frame_presented(source, generation)

    @Slot(Path, str)
    def _handle_image_load_failed(self, source: Path, message: str) -> None:
        transaction = getattr(self, "_detail_render_transaction", None)
        if transaction is None or transaction.source_identity.path != Path(source):
            return
        self._render_transaction_coordinator().mark_failed(transaction.generation, message)

    @Slot(Path, str)
    def _handle_video_load_failed(self, source: Path, message: str) -> None:
        transaction = getattr(self, "_detail_render_transaction", None)
        expected_source = (
            getattr(self, "_active_live_motion", None)
            if transaction is not None and transaction.media_kind == "live_motion"
            else getattr(getattr(transaction, "source_identity", None), "path", None)
        )
        if (
            transaction is None
            or transaction.media_kind not in {"video", "live_motion"}
            or expected_source != Path(source)
        ):
            return
        self._render_transaction_coordinator().mark_failed(transaction.generation, message)

    def _prefetch_neighbor_rows(self) -> None:
        row = self.current_row()
        count = self._asset_model.rowCount()
        if row < 0 or count <= 1:
            return
        first = max(0, row - 1)
        last = min(count - 1, row + 1)
        self._asset_model.prioritize_rows(first, last)
        descriptor_getter = getattr(self._asset_model, "detail_prefetch_descriptor", None)
        prefetch_many = getattr(self._player_view, "prefetch_images", None)
        if not callable(descriptor_getter) or not callable(prefetch_many):
            return
        descriptors = []
        for candidate_row in (row - 1, row + 1):
            descriptor = descriptor_getter(candidate_row)
            if descriptor is not None and not descriptor.is_video:
                descriptors.append(descriptor)
        if descriptors:
            prefetch_many(descriptors)

    def _hide_face_name_overlay(self, *, clear_annotations: bool) -> None:
        overlay = getattr(self, "_face_name_overlay", None)
        if overlay is None:
            return
        if clear_annotations:
            overlay.clear_annotations()
        overlay.set_overlay_active(False)

    def _refresh_face_name_overlay_for_current_presentation(self) -> None:
        self._refresh_face_name_overlay_for_presentation(
            getattr(self, "_current_presentation", None)
        )

    @Slot(object)
    def handle_people_snapshot_committed(self, event: object) -> None:
        presentation = getattr(self, "_current_presentation", None)
        if presentation is None or not presentation.asset_id:
            return
        # Skip the refresh if the snapshot doesn't touch the current asset.
        # An absent or empty changed_asset_ids means "all assets potentially
        # changed" (e.g., a set_person_order event) — in that case always refresh.
        changed_asset_ids = getattr(event, "changed_asset_ids", None)
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
        annotations = self._load_face_name_annotations(presentation.asset_id)
        self._apply_recognition_identity_suggestions(overlay, include_hidden=False)
        overlay.set_annotations(annotations)
        overlay.set_overlay_active(bool(annotations))

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
        people_service = getattr(self, "_people_service", None)
        if people_service is not None:
            try:
                annotations.extend(people_service.list_asset_face_annotations(asset_id))
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
        self._refresh_face_name_overlay_for_current_presentation()
        presentation = getattr(self, "_current_presentation", None)
        if presentation is not None and presentation.asset_id:
            self._refresh_info_panel_faces(presentation.asset_id)
        refresh_callback = getattr(self, "_people_dashboard_refresh_callback", None)
        if callable(refresh_callback):
            refresh_callback()

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
        return "pet" if getattr(annotation, "kind", "person") == "pet" else "person"

    @staticmethod
    def _annotation_id(annotation: object) -> str:
        if getattr(annotation, "kind", "person") == "pet":
            return str(getattr(annotation, "detection_id", "") or getattr(annotation, "annotation_id", ""))
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
        if not isinstance(annotation, AssetFaceAnnotation):
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
        if self._annotation_kind(annotation) == "pet":
            pet_service = getattr(self, "_pet_service", None)
            if pet_service is None:
                return
            target_pet_id = self._target_entity_id(target_person_id)
            try:
                changed = pet_service.move_detection_to_pet(annotation_id, target_pet_id)
            except (sqlite3.Error, OSError):
                LOGGER.exception(
                    "Failed to move pet detection %s to pet %s",
                    annotation_id,
                    target_pet_id,
                )
                return
            if changed:
                self._refresh_recognition_views_after_mutation()
            return
        if not isinstance(annotation, AssetFaceAnnotation):
            return
        people_service = getattr(self, "_people_service", None)
        if people_service is None:
            return
        target_person_id = self._target_entity_id(target_person_id)
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
        if not isinstance(annotation, AssetFaceAnnotation):
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
        idx = self._asset_model.index(row, 0)
        model = self._filmstrip_view.model()
        if hasattr(model, "mapFromSource"):
            idx = model.mapFromSource(idx)
        if idx.isValid():
            self._filmstrip_view.selectionModel().setCurrentIndex(
                idx, QItemSelectionModel.ClearAndSelect
            )
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
        self._clear_play_request_state()
        self._retire_live_transaction()
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
            self._info_panel.close()
        self._clear_info_panel_metadata_state()
        self._clear_confirmed_location_metadata()

    def shutdown(self) -> None:
        self._clear_play_request_state()
        self._retire_live_transaction()
        location_search_controller = getattr(self, "_location_search_controller", None)
        if location_search_controller is not None:
            location_search_controller.shutdown()
        self._player_view.video_area.stop()
        self._hide_face_name_overlay(clear_annotations=True)
        self._is_playing = False
        self._current_presentation = None
        self._detail_vm.hide_info_panel(refresh_presentation=False)
        self._update_header(None)
        if self._info_panel:
            self._info_panel.shutdown()
            self._info_panel.close()
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
        library_manager = getattr(self, "_library_manager", None)
        if library_manager is None:
            return None
        return getattr(library_manager, "edit_service", None)

    def select_next(self) -> None:
        self._detail_vm.next()

    def select_previous(self) -> None:
        self._detail_vm.previous()

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
        if is_video_value:
            updates = self._player_view.video_area.rotate_image_ccw()
        else:
            updates = self._player_view.image_viewer.rotate_image_ccw()
        try:
            edit_service = self._edit_service()
            if edit_service is None:
                raise RuntimeError("Edit service is unavailable")
            current_adjustments = edit_service.read_adjustments(path)
            current_adjustments.update(updates)
            self._adjustment_committer.commit(path, current_adjustments, reason="rotate")
        except Exception:
            LOGGER.exception("Failed to rotate %s", path)

    def _refresh_info_panel(self, info: dict) -> None:
        if not self._info_panel:
            return
        self._ensure_info_panel_metadata_state()
        capabilities = self._map_runtime_capabilities()
        location_enabled = self._refresh_location_extension_state()
        local_info = dict(info)
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
            self._refresh_info_panel_faces(presentation.asset_id if presentation is not None else None)
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
        self._warm_location_search_controller()
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
        tokens = getattr(self, "_location_search_async_tokens", None)
        if isinstance(tokens, dict):
            tokens.clear()
        self._location_search_dispatch_token = None
        controller = getattr(self, "_location_search_controller", None)
        if controller is not None:
            controller.reset()
            if clear_cache:
                controller.clear_cache()

    def _warm_location_search_controller(self) -> None:
        controller = getattr(self, "_location_search_controller", None)
        if controller is None:
            return
        try:
            controller.warm_up(
                package_root=self._map_runtime_package_root(),
                locale=QLocale.system().bcp47Name(),
            )
        except Exception:
            LOGGER.debug("Failed to warm location search controller", exc_info=True)

    @Slot(str)
    def _handle_location_query_changed(self, query: str) -> None:
        info_panel = getattr(self, "_info_panel", None)
        if info_panel is None:
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
        async_token = getattr(self, "_active_async_token", None)
        self._location_search_dispatch_token = async_token
        search_token = self._location_search_controller.search(
            query,
            target_path=presentation.path,
            package_root=self._map_runtime_package_root(),
            locale=locale,
        )
        if async_token is not None:
            self._location_search_async_tokens[int(search_token)] = async_token
            while len(self._location_search_async_tokens) > 128:
                oldest = next(iter(self._location_search_async_tokens))
                self._location_search_async_tokens.pop(oldest, None)
        self._location_search_dispatch_token = None

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
        async_token = self._location_search_async_tokens.get(
            int(_token),
            getattr(self, "_location_search_dispatch_token", None),
        )
        if async_token is None and getattr(self, "_active_async_token", None) is not None:
            return
        if (
            info_panel is None
            or current_presentation is None
            or not self._is_async_token_current(async_token)
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
        async_token = self._location_search_async_tokens.get(
            int(_token),
            getattr(self, "_location_search_dispatch_token", None),
        )
        if async_token is None and getattr(self, "_active_async_token", None) is not None:
            return
        if (
            info_panel is None
            or current_presentation is None
            or not self._is_async_token_current(async_token)
            or current_presentation.path != Path(target_path)
        ):
            return
        LOGGER.warning("Offline location search failed for query %r: %s", query, message)
        info_panel.set_location_suggestions([])

    @Slot(str, object)
    def _handle_location_confirm_requested(self, query: str, suggestion_obj: object) -> None:
        if self._location_assign_inflight or not self._refresh_location_extension_state():
            return
        if not isinstance(suggestion_obj, SearchSuggestion):
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

        self._location_search_controller.reset()

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
            service = LocationAssignmentService(
                IndexStoreLocationAssignmentRepository(Path(library_root)),
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
            async_token = getattr(self, "_active_async_token", None)
            if async_token is not None:
                token_map = getattr(self, "_location_write_tokens_by_job", None)
                if not isinstance(token_map, dict):
                    token_map = {}
                    self._location_write_tokens_by_job = token_map
                token_map[assignment.write_job.job_id] = async_token
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
            getattr(self, "_location_write_tokens_by_job", {}).pop(
                assignment.write_job.job_id,
                None,
            )
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
        if not self._location_write_result_is_current(job.job_id):
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
        if not isinstance(result, LocationFileWriteResult):
            return
        if not self._location_write_result_is_current(result.job_id):
            getattr(self, "_location_write_tokens_by_job", {}).pop(result.job_id, None)
            return
        getattr(self, "_location_write_tokens_by_job", {}).pop(result.job_id, None)
        self._location_write_jobs_by_path.pop(result.asset_path, None)
        self._complete_location_video_file_write(result.asset_path)

    @Slot(object)
    def _handle_location_file_write_failed(self, result: object) -> None:
        if not isinstance(result, LocationFileWriteResult):
            return
        if not self._location_write_result_is_current(result.job_id):
            getattr(self, "_location_write_tokens_by_job", {}).pop(result.job_id, None)
            return
        getattr(self, "_location_write_tokens_by_job", {}).pop(result.job_id, None)
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

    def _location_write_result_is_current(self, job_id: str) -> bool:
        active = getattr(self, "_active_async_token", None)
        if active is None:
            return True
        token = getattr(self, "_location_write_tokens_by_job", {}).get(str(job_id))
        return token is not None and self._is_async_token_current(token)

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

    @Slot()
    def _handle_info_panel_dismissed(self) -> None:
        self._detail_vm.hide_info_panel(refresh_presentation=False)

    def _ensure_info_panel_metadata_state(self) -> None:
        if not hasattr(self, "_info_panel_metadata_cache"):
            self._info_panel_metadata_cache = {}
        if not hasattr(self, "_info_panel_metadata_inflight"):
            self._info_panel_metadata_inflight = set()
        if not hasattr(self, "_info_panel_metadata_tokens"):
            self._info_panel_metadata_tokens = {}
        if not hasattr(self, "_info_panel_metadata_attempted"):
            self._info_panel_metadata_attempted = set()

    def _clear_info_panel_metadata_state(self) -> None:
        self._ensure_info_panel_metadata_state()
        self._info_panel_metadata_cache.clear()
        self._info_panel_metadata_inflight.clear()
        self._info_panel_metadata_tokens.clear()
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
        async_token = getattr(self, "_active_async_token", None)
        self._info_panel_metadata_tokens[path_key] = async_token

        worker = InfoPanelMetadataWorker(path, is_video=is_video)
        worker.signals.ready.connect(
            lambda result, token=async_token: self._handle_info_panel_metadata_ready(
                result,
                async_token=token,
            )
        )
        worker.signals.error.connect(
            lambda key, message, token=async_token: self._handle_info_panel_metadata_error(
                key,
                message,
                async_token=token,
            )
        )
        worker.signals.finished.connect(
            lambda key, token=async_token: self._handle_info_panel_metadata_finished(
                key,
                async_token=token,
            )
        )
        try:
            QThreadPool.globalInstance().start(worker, -1)
        except Exception:  # noqa: BLE001
            LOGGER.warning("Failed to start metadata enrichment worker for %s", path_key, exc_info=True)
            self._handle_info_panel_metadata_finished(
                path_key,
                async_token=async_token,
            )

    def _handle_info_panel_metadata_ready(
        self,
        result: InfoPanelMetadataResult,
        *,
        async_token: PlaybackAsyncToken | None = None,
    ) -> None:
        if not self._is_async_token_current(async_token):
            return
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
            self._refresh_info_panel_faces(presentation.asset_id)

    def _info_panel_content_update(self):
        info_panel = getattr(self, "_info_panel", None)
        content_update = getattr(info_panel, "content_update", None)
        if callable(content_update):
            context = content_update()
            if hasattr(context, "__enter__") and hasattr(context, "__exit__"):
                return context
        return nullcontext()

    def _handle_info_panel_metadata_error(
        self,
        path_key: str,
        message: str,
        *,
        async_token: PlaybackAsyncToken | None = None,
    ) -> None:
        if not self._is_async_token_current(async_token):
            return
        LOGGER.debug(
            "Failed to enrich info-panel metadata for %s: %s",
            path_key,
            message,
        )

    def _handle_info_panel_metadata_finished(
        self,
        path_key: str,
        *,
        async_token: PlaybackAsyncToken | None = None,
    ) -> None:
        self._ensure_info_panel_metadata_state()
        owner_token = self._info_panel_metadata_tokens.get(path_key)
        if path_key in self._info_panel_metadata_tokens and owner_token != async_token:
            return
        self._info_panel_metadata_inflight.discard(path_key)
        self._info_panel_metadata_tokens.pop(path_key, None)
        if not self._is_async_token_current(async_token):
            return
        self._info_panel_metadata_attempted.add(path_key)

        info_panel = getattr(self, "_info_panel", None)
        presentation = getattr(self, "_current_presentation", None)
        if (
            info_panel is None
            or not info_panel.isVisible()
            or presentation is None
            or str(presentation.path) != path_key
        ):
            return
        # ``ready`` may not be emitted when metadata extraction fails. Refresh
        # after leaving the inflight state so the panel replaces its loading
        # placeholder with the cached metadata or the unavailable fallback.
        self._refresh_info_panel(presentation.info)

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
        worker = ManualFaceAddWorker(
            library_root=library_root,
            asset_id=presentation.asset_id,
            requested_box=requested_box,
            name_or_none=payload.get("name") if isinstance(payload.get("name"), str) else None,
            person_id=worker_person_id,
            people_service=self._people_service,
        )
        async_token = getattr(self, "_active_async_token", None)
        worker.signals.ready.connect(
            lambda result, token=async_token: self._handle_manual_face_ready(
                result,
                async_token=token,
            )
        )
        worker.signals.error.connect(
            lambda message, token=async_token: self._handle_manual_face_error(
                message,
                async_token=token,
            )
        )
        worker.signals.finished.connect(
            lambda token=async_token: self._handle_manual_face_finished(
                async_token=token,
            )
        )
        QThreadPool.globalInstance().start(worker, -1)

    def _handle_manual_face_ready(
        self,
        result: object,
        *,
        async_token: PlaybackAsyncToken | None = None,
    ) -> None:
        if not self._is_async_token_current(async_token):
            return
        submitted_asset_id = self._manual_face_inflight_asset_id
        if submitted_asset_id:
            self._clear_pending_manual_faces(submitted_asset_id)
        merge_target = getattr(self, "_manual_face_pending_merge_target", None)
        if isinstance(merge_target, str) and merge_target.startswith("pet:"):
            person_id = getattr(result, "person_id", None)
            if isinstance(person_id, str) and person_id:
                try:
                    merged = self._people_service.merge_identities(
                        f"person:{person_id}",
                        merge_target,
                    )
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
        presentation = getattr(self, "_current_presentation", None)
        if presentation is not None and presentation.asset_id == submitted_asset_id:
            self._refresh_face_name_overlay_for_current_presentation()
            self._refresh_info_panel_faces(presentation.asset_id)
        refresh_callback = getattr(self, "_people_dashboard_refresh_callback", None)
        if callable(refresh_callback):
            refresh_callback()

    def _handle_manual_face_error(
        self,
        message: str,
        *,
        async_token: PlaybackAsyncToken | None = None,
    ) -> None:
        if not self._is_async_token_current(async_token):
            return
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

    def _handle_manual_face_finished(
        self,
        *,
        async_token: PlaybackAsyncToken | None = None,
    ) -> None:
        if not self._is_async_token_current(async_token):
            return
        self._manual_face_add_inflight = False
        self._manual_face_inflight_asset_id = None
        self._manual_face_pending_merge_target = None
        overlay = getattr(self, "_face_name_overlay", None)
        if overlay is not None:
            overlay.set_manual_face_busy(False)

"""Coordinator that binds detail widgets to DetailViewModel presentation."""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

from PySide6.QtCore import QItemSelectionModel, QModelIndex, QObject, QLocale, QThreadPool, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QColor, QPalette

from iPhoto.application.ports import MapRuntimePort
from iPhoto.config import PLAY_ASSET_DEBOUNCE_MS
from iPhoto.gui.ui.tasks.assign_location_worker import (
    AssignLocationRequest,
    AssignLocationWorker,
)
from iPhoto.infrastructure.services.map_runtime_service import SessionMapRuntimeService
from iPhoto.gui.detail_profile import log_detail_profile
from iPhoto.gui.coordinators.view_router import ViewRouter
from iPhoto.gui.ui.controllers.edit_zoom_handler import EditZoomHandler
from iPhoto.gui.ui.controllers.header_controller import HeaderController
from iPhoto.gui.ui.icons import load_icon
from iPhoto.gui.ui.tasks.info_panel_metadata_worker import (
    InfoPanelMetadataResult,
    InfoPanelMetadataWorker,
)
from iPhoto.gui.ui.tasks.manual_face_add_worker import ManualFaceAddWorker
from iPhoto.gui.ui.widgets import dialogs
from iPhoto.gui.ui.widgets.info_panel import InfoPanel
from iPhoto.gui.viewmodels.detail_viewmodel import DetailPresentation, DetailViewModel
from iPhoto.io import sidecar
from iPhoto.library.manager import LibraryManager
from iPhoto.people.repository import AssetFaceAnnotation
from iPhoto.people.service import PeopleService
from maps.osmand_search import OsmAndSearchService, SearchSuggestion

if TYPE_CHECKING:
    from iPhoto.utils.settings import Settings
    from PySide6.QtWidgets import QPushButton, QSlider, QToolButton, QWidget

    from iPhoto.gui.coordinators.navigation_coordinator import NavigationCoordinator
    from iPhoto.gui.ui.controllers.player_view_controller import PlayerViewController
    from iPhoto.gui.ui.media import MediaAdjustmentCommitter
    from iPhoto.gui.ui.widgets.face_name_overlay import FaceNameOverlayWidget
    from iPhoto.gui.ui.widgets.filmstrip_view import FilmstripView
    from iPhoto.gui.ui.widgets.player_bar import PlayerBar
    from iPhoto.gui.viewmodels.gallery_list_model_adapter import GalleryListModelAdapter

LOGGER = logging.getLogger(__name__)

_INFO_PANEL_METADATA_CACHE_MAX = 200
_LOCATION_SEARCH_RESULT_LIMIT = 5
_LOCATION_SEARCH_DEBOUNCE_MS = 80
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
        people_dashboard_refresh_callback: Callable[[], None] | None = None,
        library_manager: LibraryManager | None = None,
        location_session_invalidator: Callable[[], None] | None = None,
        map_runtime: MapRuntimePort | None = None,
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
        self._people_dashboard_refresh_callback = people_dashboard_refresh_callback
        self._library_manager = library_manager
        self._location_session_invalidator = location_session_invalidator
        self._map_runtime = map_runtime or getattr(library_manager, "map_runtime", None)

        self._is_playing = False
        self._navigation: NavigationCoordinator | None = None
        self._info_panel: InfoPanel | None = None
        self._active_live_motion: Path | None = None
        self._active_live_still: Path | None = None
        self._resume_after_transition = False
        self._trim_in_ms = 0
        self._trim_out_ms = 0
        self._current_presentation: DetailPresentation | None = None
        self._info_panel_metadata_cache: dict[str, dict[str, Any]] = {}
        self._info_panel_metadata_inflight: set[str] = set()
        self._info_panel_metadata_attempted: set[str] = set()
        self._play_profile_started_at: float | None = None
        self._play_profile_row: int | None = None
        self._manual_face_add_inflight = False
        self._manual_face_inflight_asset_id: str | None = None
        self._pending_manual_face_annotations: dict[str, list[AssetFaceAnnotation]] = {}
        self._pending_manual_face_sequence = 0
        self._location_search_service: OsmAndSearchService | None = None
        self._location_search_cache: dict[str, list[SearchSuggestion]] = {}
        self._location_assign_inflight = False
        self._location_assign_path: Path | None = None
        self._location_preview_path: Path | None = None
        self._location_preview_metadata: dict[str, Any] | None = None
        self._pending_location_query = ""
        self._location_search_target_path: Path | None = None

        self._pending_play_row: int | None = None
        self._show_face_names = False
        self._play_debounce = QTimer(self)
        self._play_debounce.setSingleShot(True)
        self._play_debounce.setInterval(PLAY_ASSET_DEBOUNCE_MS)
        self._play_debounce.timeout.connect(self._execute_pending_play)
        self._location_search_timer = QTimer(self)
        self._location_search_timer.setSingleShot(True)
        self._location_search_timer.setInterval(_LOCATION_SEARCH_DEBOUNCE_MS)
        self._location_search_timer.timeout.connect(self._perform_location_search)

        self._connect_signals()
        self._setup_zoom_handler()
        self._restore_filmstrip_preference()

    def set_navigation_coordinator(self, nav: NavigationCoordinator) -> None:
        self._navigation = nav

    def set_people_service(self, service: PeopleService | None) -> None:
        self._people_service = service or PeopleService()
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
            self._reset_location_search_service()
        if self._info_panel is None or self._info_panel.current_rel() is None:
            return
        capabilities = self._map_runtime_capabilities()
        self._info_panel.set_location_capability(
            enabled=self._refresh_location_extension_state(),
            preview_enabled=bool(capabilities.display_available) if capabilities is not None else False,
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

        self._favorite_button.setEnabled(presentation.can_toggle_favorite)
        self._info_button.setEnabled(True)
        self._share_button.setEnabled(presentation.can_share)
        self._edit_button.setEnabled(presentation.can_edit)
        self._rotate_button.setEnabled(presentation.can_rotate)
        self._update_favorite_icon(presentation.is_favorite)

        self._zoom_slider.blockSignals(True)
        self._zoom_slider.setValue(100)
        self._zoom_slider.blockSignals(False)

        if presentation.is_video:
            self._hide_face_name_overlay(clear_annotations=True)
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
            self._player_bar.setEnabled(True)
            self._zoom_handler.set_viewer(self._player_view.video_area)
            self._player_view.video_area.reset_zoom()
            self._zoom_widget.show()
        else:
            if self._player_view.video_area.has_video():
                self._player_view.video_area.stop()
            self._player_view.show_image_surface()
            display_started = time.perf_counter()
            self._player_view.display_image(source)
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

        self._is_playing = False
        self._player_bar.set_playback_state(False)
        self._player_bar.set_position(0)

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

    def _autoplay_live_motion(self, presentation: DetailPresentation) -> None:
        motion_path = presentation.live_motion_abs
        if motion_path is None:
            self._refresh_face_name_overlay_for_presentation(presentation)
            return
        self._active_live_motion = motion_path
        self._active_live_still = presentation.path
        self._hide_face_name_overlay(clear_annotations=False)
        self._player_view.defer_still_updates(True)
        self._player_view.show_video_surface(interactive=False)
        self._trim_in_ms = 0
        self._trim_out_ms = 0
        self._player_view.video_area.load_video(
            motion_path,
            adjustments=None,
            trim_range_ms=None,
            adjusted_preview=False,
        )
        self._player_view.video_area.play()
        self._player_bar.setEnabled(False)
        self._is_playing = True

    def _handle_playback_finished(self) -> None:
        if not self._active_live_motion or not self._active_live_still:
            return
        still = self._active_live_still
        self._active_live_motion = None
        self._player_view.defer_still_updates(False)
        if not self._player_view.apply_pending_still():
            self._player_view.display_image(still)
        self._player_bar.setEnabled(False)
        self._player_view.show_live_badge()
        self._player_view.set_live_replay_enabled(True)
        self._is_playing = False
        self._refresh_face_name_overlay_for_current_presentation()

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
        try:
            people_service = getattr(self, "_people_service", None)
            if people_service is not None:
                overlay.set_name_suggestions(people_service.list_person_name_suggestions())
        except (sqlite3.Error, OSError):
            LOGGER.exception("Failed to load person name suggestions")
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
        people_service = getattr(self, "_people_service", None)
        if people_service is None or not asset_id:
            return []
        try:
            return people_service.list_asset_face_annotations(asset_id)
        except (sqlite3.Error, OSError):
            LOGGER.exception("Failed to load face annotations for asset %s", asset_id)
            return []

    @Slot(str, object)
    def _handle_face_name_rename_submitted(
        self,
        person_id: str,
        new_name: object,
    ) -> None:
        if not person_id:
            return
        people_service = getattr(self, "_people_service", None)
        if people_service is None:
            return
        name = new_name.strip() if isinstance(new_name, str) else None
        try:
            people_service.rename_cluster(person_id, name or None)
        except (sqlite3.Error, OSError):
            LOGGER.exception("Failed to rename person %s", person_id)
            return
        self._refresh_face_name_overlay_for_current_presentation()
        presentation = getattr(self, "_current_presentation", None)
        if presentation is not None and presentation.asset_id:
            self._refresh_info_panel_faces(presentation.asset_id)
        refresh_callback = getattr(self, "_people_dashboard_refresh_callback", None)
        if callable(refresh_callback):
            refresh_callback()

    @Slot(object)
    def _handle_info_panel_face_delete_requested(self, annotation: object) -> None:
        if not isinstance(annotation, AssetFaceAnnotation):
            return
        people_service = getattr(self, "_people_service", None)
        if people_service is None:
            return
        try:
            changed = people_service.delete_face(annotation.face_id)
        except (sqlite3.Error, OSError):
            LOGGER.exception("Failed to delete face %s", annotation.face_id)
            return
        if not changed:
            return
        self._refresh_face_name_overlay_for_current_presentation()
        presentation = getattr(self, "_current_presentation", None)
        if presentation is not None and presentation.asset_id:
            self._refresh_info_panel_faces(presentation.asset_id)
        refresh_callback = getattr(self, "_people_dashboard_refresh_callback", None)
        if callable(refresh_callback):
            refresh_callback()

    @Slot(object, str)
    def _handle_info_panel_face_move_requested(
        self,
        annotation: object,
        target_person_id: str,
    ) -> None:
        if not isinstance(annotation, AssetFaceAnnotation) or not target_person_id:
            return
        people_service = getattr(self, "_people_service", None)
        if people_service is None:
            return
        try:
            changed = people_service.move_face_to_person(annotation.face_id, target_person_id)
        except (sqlite3.Error, OSError):
            LOGGER.exception(
                "Failed to move face %s to person %s",
                annotation.face_id,
                target_person_id,
            )
            return
        if not changed:
            return
        self._refresh_face_name_overlay_for_current_presentation()
        presentation = getattr(self, "_current_presentation", None)
        if presentation is not None and presentation.asset_id:
            self._refresh_info_panel_faces(presentation.asset_id)
        refresh_callback = getattr(self, "_people_dashboard_refresh_callback", None)
        if callable(refresh_callback):
            refresh_callback()

    @Slot(object, str)
    def _handle_info_panel_face_move_to_new_person_requested(
        self,
        annotation: object,
        new_name: str,
    ) -> None:
        if not isinstance(annotation, AssetFaceAnnotation):
            return
        people_service = getattr(self, "_people_service", None)
        if people_service is None:
            return
        try:
            created_person_id = people_service.move_face_to_new_person(annotation.face_id, new_name)
        except (sqlite3.Error, OSError):
            LOGGER.exception("Failed to move face %s into a new person", annotation.face_id)
            return
        if not created_person_id:
            return
        self._refresh_face_name_overlay_for_current_presentation()
        presentation = getattr(self, "_current_presentation", None)
        if presentation is not None and presentation.asset_id:
            self._refresh_info_panel_faces(presentation.asset_id)
        refresh_callback = getattr(self, "_people_dashboard_refresh_callback", None)
        if callable(refresh_callback):
            refresh_callback()

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
        location_timer = getattr(self, "_location_search_timer", None)
        if location_timer is not None:
            location_timer.stop()
        self._pending_location_query = ""
        self._location_search_target_path = None
        location_service = getattr(self, "_location_search_service", None)
        if location_service is not None:
            location_service.shutdown()
            self._location_search_service = None
        self._player_view.video_area.stop()
        self._player_view.show_placeholder()
        self._hide_face_name_overlay(clear_annotations=True)
        self._player_bar.setEnabled(False)
        self._is_playing = False
        self._current_presentation = None
        self._detail_vm.hide_info_panel(refresh_presentation=False)
        self._update_header(None)
        if self._info_panel:
            self._info_panel.close()
        self._clear_info_panel_metadata_state()

    def shutdown(self) -> None:
        self._clear_play_request_state()
        self._location_search_timer.stop()
        self._pending_location_query = ""
        self._location_search_target_path = None
        if self._location_search_service is not None:
            self._location_search_service.shutdown()
            self._location_search_service = None
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

    def _update_header(self, presentation: DetailPresentation | None) -> None:
        if not self._header_controller:
            return
        if presentation is None:
            self._header_controller.clear()
            return
        self._header_controller.update_from_values(presentation.location, presentation.timestamp)

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
            current_adjustments = sidecar.load_adjustments(path)
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
        self._info_panel.set_location_capability(
            enabled=location_enabled,
            preview_enabled=bool(capabilities.display_available) if capabilities is not None else location_enabled,
            fallback_text=_LOCATION_EXTENSION_PROMPT,
        )
        local_info = dict(info)
        abs_path = local_info.get("abs")
        path_key = self._info_panel_path_key(abs_path)
        if path_key is not None:
            cached = self._info_panel_metadata_cache.get(path_key)
            if cached:
                local_info = self._merge_info_panel_metadata(local_info, cached)
        current_path = Path(path_key) if path_key is not None else None
        location_preview_path = getattr(self, "_location_preview_path", None)
        location_preview_metadata = getattr(self, "_location_preview_metadata", None)
        if (
            current_path is not None
            and location_preview_path is not None
            and location_preview_metadata is not None
            and current_path == location_preview_path
        ):
            local_info = self._merge_info_panel_metadata(local_info, location_preview_metadata)
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

        if getattr(self, "_location_search_service", None) is not None:
            return True

        try:
            self._location_search_service = OsmAndSearchService(
                package_root=self._map_runtime_package_root(),
            )
        except Exception:  # noqa: BLE001
            LOGGER.warning("Failed to initialize offline location search", exc_info=True)
            self._location_search_service = None
            return False
        return True

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

    def _reset_location_search_service(self) -> None:
        location_timer = getattr(self, "_location_search_timer", None)
        if location_timer is not None:
            location_timer.stop()
        self._pending_location_query = ""
        self._location_search_target_path = None
        location_cache = getattr(self, "_location_search_cache", None)
        if location_cache is not None:
            location_cache.clear()
        location_service = getattr(self, "_location_search_service", None)
        if location_service is not None:
            location_service.shutdown()
            self._location_search_service = None

    def _normalize_location_query(self, query: str) -> str:
        return " ".join(query.split()).casefold()

    def _should_search_location_query(self, query: str) -> bool:
        trimmed = " ".join(query.split())
        if not trimmed:
            return False
        if len(trimmed) >= 2:
            return True
        return any(ord(character) >= 128 for character in trimmed)

    def _preview_cached_location_suggestions(
        self,
        query: str,
    ) -> tuple[list[SearchSuggestion] | None, bool]:
        normalized_query = self._normalize_location_query(query)
        if not normalized_query:
            return None, False
        exact = self._location_search_cache.get(normalized_query)
        if exact is not None:
            return list(exact), True

        for cached_query, cached_results in sorted(
            self._location_search_cache.items(),
            key=lambda item: len(item[0]),
            reverse=True,
        ):
            if not normalized_query.startswith(cached_query):
                continue
            filtered = [
                suggestion
                for suggestion in cached_results
                if normalized_query in self._normalize_location_query(
                    " ".join(
                        part
                        for part in (
                            suggestion.display_name,
                            suggestion.secondary_text,
                        )
                        if part
                    )
                )
            ]
            if filtered:
                return filtered[:_LOCATION_SEARCH_RESULT_LIMIT], False
        return None, False

    @Slot(str)
    def _handle_location_query_changed(self, query: str) -> None:
        info_panel = getattr(self, "_info_panel", None)
        if info_panel is None:
            return

        self._location_search_timer.stop()
        self._pending_location_query = ""
        self._location_search_target_path = None
        if self._location_search_service is not None:
            self._location_search_service.abort()

        if not self._refresh_location_extension_state():
            info_panel.set_location_suggestions([])
            return
        if self._location_assign_inflight:
            info_panel.set_location_suggestions([])
            return
        if not self._should_search_location_query(query):
            info_panel.set_location_suggestions([])
            return

        preview, is_exact = self._preview_cached_location_suggestions(query)
        if preview is not None:
            info_panel.set_location_suggestions(preview)
            if is_exact:
                return
        else:
            info_panel.set_location_suggestions([])

        presentation = getattr(self, "_current_presentation", None)
        if presentation is None:
            return
        self._pending_location_query = query.strip()
        self._location_search_target_path = presentation.path
        self._location_search_timer.start()

    @Slot()
    def _perform_location_search(self) -> None:
        info_panel = getattr(self, "_info_panel", None)
        presentation = getattr(self, "_current_presentation", None)
        query = self._pending_location_query.strip()
        target_path = self._location_search_target_path
        self._pending_location_query = ""
        if (
            info_panel is None
            or presentation is None
            or not query
            or target_path is None
            or presentation.path != target_path
        ):
            return

        if not self._refresh_location_extension_state():
            info_panel.set_location_suggestions([])
            return
        service = self._location_search_service
        if service is None:
            info_panel.set_location_suggestions([])
            return

        locale = QLocale.system().bcp47Name()
        try:
            suggestions = service.search(
                query,
                limit=_LOCATION_SEARCH_RESULT_LIMIT,
                locale=locale,
            )
        except Exception:  # noqa: BLE001
            LOGGER.warning("Offline location search failed for query %r", query, exc_info=True)
            suggestions = []

        normalized_query = self._normalize_location_query(query)
        self._location_search_cache[normalized_query] = list(suggestions)
        if len(self._location_search_cache) > 64:
            oldest_key = next(iter(self._location_search_cache))
            self._location_search_cache.pop(oldest_key, None)

        current_presentation = getattr(self, "_current_presentation", None)
        if (
            current_presentation is None
            or current_presentation.path != target_path
            or self._location_assign_inflight
        ):
            return
        info_panel.set_location_suggestions(suggestions)

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

        self._location_search_timer.stop()
        self._pending_location_query = ""
        self._location_search_target_path = None
        if self._location_search_service is not None:
            self._location_search_service.abort()

        display_name = suggestion_obj.display_name.strip() or query.strip()
        self._location_assign_inflight = True
        self._location_assign_path = presentation.path
        self._location_preview_path = presentation.path
        self._location_preview_metadata = {
            "location": display_name,
            "place": display_name,
            "gps": {
                "lat": float(suggestion_obj.latitude),
                "lon": float(suggestion_obj.longitude),
            },
        }
        if self._info_panel is not None:
            self._info_panel.preview_location(
                display_name,
                float(suggestion_obj.latitude),
                float(suggestion_obj.longitude),
            )
            self._info_panel.set_location_busy(True)
            self._info_panel.set_location_suggestions([])

        existing_metadata = self._asset_model.metadata_for_path(presentation.path) or dict(presentation.info)
        worker = AssignLocationWorker(
            AssignLocationRequest(
                library_root=Path(library_root),
                asset_path=presentation.path,
                asset_rel=rel_value,
                display_name=display_name,
                latitude=float(suggestion_obj.latitude),
                longitude=float(suggestion_obj.longitude),
                is_video=bool(presentation.is_video),
                existing_metadata=existing_metadata,
            )
        )
        worker.signals.ready.connect(self._handle_location_assignment_ready)
        worker.signals.error.connect(self._handle_location_assignment_error)
        worker.signals.finished.connect(self._handle_location_assignment_finished)
        try:
            QThreadPool.globalInstance().start(worker, -1)
        except Exception:  # noqa: BLE001
            LOGGER.warning("Failed to start location assignment worker", exc_info=True)
            self._location_assign_inflight = False
            self._location_assign_path = None
            if self._info_panel is not None:
                self._info_panel.set_location_busy(False)

    @Slot(object)
    def _handle_location_assignment_ready(self, result: object) -> None:
        asset_path = getattr(result, "asset_path", None)
        metadata = getattr(result, "metadata", None)
        if not isinstance(asset_path, Path) or not isinstance(metadata, dict):
            return

        file_write_error = getattr(result, "file_write_error", None)
        if isinstance(file_write_error, str) and file_write_error.strip():
            LOGGER.warning(
                "Location saved in the library, but GPS metadata was not written to %s: %s",
                asset_path,
                file_write_error,
            )
            if self._is_missing_exiftool_error(file_write_error):
                self._queue_location_exiftool_missing_warning()
            else:
                self._queue_location_file_write_warning(file_write_error)

        row = self._asset_model.row_for_path(asset_path)
        if row is not None:
            self._asset_model.store.update_asset_metadata(row, dict(metadata))

        path_key = str(asset_path)
        if len(self._info_panel_metadata_cache) >= _INFO_PANEL_METADATA_CACHE_MAX:
            evict_key = next(iter(self._info_panel_metadata_cache))
            del self._info_panel_metadata_cache[evict_key]
            self._info_panel_metadata_attempted.discard(evict_key)
        self._info_panel_metadata_cache[path_key] = dict(metadata)
        self._info_panel_metadata_attempted.add(path_key)
        self._info_panel_metadata_inflight.discard(path_key)
        if self._location_preview_path == asset_path:
            self._location_preview_path = None
            self._location_preview_metadata = None

        self._detail_vm.refresh_current()

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

    @Slot(str)
    def _handle_location_assignment_error(self, message: str) -> None:
        LOGGER.warning("Failed to assign location: %s", message)
        if self._location_preview_path == self._location_assign_path:
            self._location_preview_path = None
            self._location_preview_metadata = None
        info_panel = getattr(self, "_info_panel", None)
        presentation = getattr(self, "_current_presentation", None)
        if (
            info_panel is not None
            and presentation is not None
            and info_panel.isVisible()
            and self._location_assign_path is not None
            and presentation.path == self._location_assign_path
        ):
            self._refresh_info_panel(presentation.info)
        elif info_panel is not None:
            info_panel.set_location_busy(False)

    @Slot()
    def _handle_location_assignment_finished(self) -> None:
        self._location_assign_inflight = False
        self._location_assign_path = None
        info_panel = getattr(self, "_info_panel", None)
        presentation = getattr(self, "_current_presentation", None)
        if info_panel is None:
            return
        if presentation is not None and info_panel.isVisible():
            self._refresh_info_panel(presentation.info)
            return
        info_panel.set_location_busy(False)

    def _refresh_info_panel_faces(self, asset_id: str | None) -> None:
        info_panel = getattr(self, "_info_panel", None)
        if info_panel is None:
            return
        people_service = getattr(self, "_people_service", None)
        if people_service is not None:
            try:
                info_panel.set_face_action_candidates(
                    people_service.list_clusters(include_hidden=True)
                )
            except (sqlite3.Error, OSError):
                LOGGER.exception("Failed to load face action candidates")
                info_panel.set_face_action_candidates([])
        if not asset_id:
            info_panel.set_asset_faces([])
            return
        info_panel.set_asset_faces(self._compose_info_panel_faces(asset_id))

    def _compose_info_panel_faces(self, asset_id: str) -> list[AssetFaceAnnotation]:
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

        worker = InfoPanelMetadataWorker(path, is_video=is_video)
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
    def _handle_info_panel_metadata_ready(self, result: InfoPanelMetadataResult) -> None:
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
        self._info_panel.set_asset_metadata(local_info)
        self._refresh_info_panel_faces(presentation.asset_id)

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
        try:
            overlay.set_name_suggestions(self._people_service.list_person_name_suggestions())
        except (sqlite3.Error, OSError):
            LOGGER.exception("Failed to load person name suggestions")
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
            person_id=payload.get("person_id") if isinstance(payload.get("person_id"), str) else None,
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
        presentation = getattr(self, "_current_presentation", None)
        if presentation is not None and presentation.asset_id == submitted_asset_id:
            self._refresh_face_name_overlay_for_current_presentation()
            self._refresh_info_panel_faces(presentation.asset_id)
        refresh_callback = getattr(self, "_people_dashboard_refresh_callback", None)
        if callable(refresh_callback):
            refresh_callback()

    @Slot(str)
    def _handle_manual_face_error(self, message: str) -> None:
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
        overlay = getattr(self, "_face_name_overlay", None)
        if overlay is not None:
            overlay.set_manual_face_busy(False)

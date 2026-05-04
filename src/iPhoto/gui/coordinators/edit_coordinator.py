"""Coordinator for the Edit View workflow."""

from __future__ import annotations
import logging
import math
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from PySide6.QtCore import QObject, QSize, Qt, QThreadPool, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QAbstractSlider,
    QAbstractSpinBox,
    QComboBox,
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
    QWidget,
)

from iPhoto.application.ports import EditServicePort
from iPhoto.gui.coordinators.view_router import ViewRouter
from iPhoto.events.bus import EventBus
from iPhoto.gui.ui.models.edit_session import EditSession
from iPhoto.gui.ui.controllers.edit_history_manager import EditHistoryManager
from iPhoto.gui.ui.controllers.edit_pipeline_loader import EditPipelineLoader
from iPhoto.gui.ui.controllers.edit_preview_manager import EditPreviewManager
from iPhoto.gui.ui.controllers.edit_zoom_handler import EditZoomHandler
from iPhoto.gui.ui.controllers.edit_modes import AdjustModeState, CropModeState
from iPhoto.gui.ui.controllers.header_controller import HeaderController
from iPhoto.gui.ui.controllers.edit_fullscreen_manager import EditFullscreenManager
from iPhoto.gui.ui.controllers.edit_view_transition import EditViewTransitionManager
from iPhoto.gui.ui.tasks.video_trim_thumbnail_worker import VideoTrimThumbnailWorker
from iPhoto.gui.ui.tasks.video_sidebar_preview_worker import (
    VideoSidebarPreviewResult,
    VideoSidebarPreviewWorker,
)
from iPhoto.gui.ui.controllers.edit_preview_manager import resolve_adjustment_mapping
from iPhoto.gui.ui.palette import viewer_surface_color
from iPhoto.gui.ui.media import MediaRestoreRequest
from iPhoto.io.metadata import read_video_meta
from iPhoto.media_classifier import VIDEO_EXTENSIONS
from iPhoto.utils.logging import get_logger
from iPhoto.utils.ffmpeg import probe_video_rotation
from iPhoto.core.adjustment_mapping import (
    VIDEO_TRIM_IN_KEY,
    VIDEO_TRIM_OUT_KEY,
    normalise_video_trim,
)
from iPhoto.core.curve_resolver import DEFAULT_CURVE_POINTS
from iPhoto.core.levels_resolver import DEFAULT_LEVELS_HANDLES
from iPhoto.core.selective_color_resolver import DEFAULT_SELECTIVE_COLOR_RANGES

if TYPE_CHECKING:
    from iPhoto.gui.viewmodels.gallery_list_model_adapter import GalleryListModelAdapter
    from iPhoto.gui.ui.controllers.window_theme_controller import WindowThemeController
    from iPhoto.gui.coordinators.navigation_coordinator import NavigationCoordinator
    from iPhoto.gui.ui.media import MediaAdjustmentCommitter, MediaSelectionSession

_LOGGER = logging.getLogger(__name__)
_APP_LOGGER = get_logger().getChild("video_trim")
_DEFAULT_VIDEO_FRAME_STEP_MS = 33


class EditCoordinator(QObject):
    """
    Manages the Edit View, including entering/exiting edit mode and applying changes.
    Replaces EditController.
    """

    def __init__(
        self,
        edit_page: QObject, # The widget containing edit UI (Ui_MainWindow components)
        router: ViewRouter,
        event_bus: EventBus,
        asset_vm: GalleryListModelAdapter,
        window: QObject | None = None,
        theme_controller: WindowThemeController | None = None,
        navigation: "NavigationCoordinator | None" = None,
        media_session: "MediaSelectionSession | None" = None,
        adjustment_committer: "MediaAdjustmentCommitter | None" = None,
        edit_service_getter: Callable[[], EditServicePort | None] | None = None,
    ):
        super().__init__()
        # We need access to specific UI elements within edit_page (which is likely MainWindow.ui)
        self._ui = edit_page
        self._router = router
        self._bus = event_bus
        self._asset_vm = asset_vm
        self._theme_controller = theme_controller
        self._navigation = navigation
        self._media_session = media_session
        self._adjustment_committer = adjustment_committer
        self._edit_service_getter = edit_service_getter

        self._transition_manager = EditViewTransitionManager(
            self._ui,
            window,
            parent=self,
            theme_controller=theme_controller
        )

        # State
        self._session: Optional[EditSession] = None
        self._current_source: Optional[Path] = None
        self._history_manager = EditHistoryManager(parent=self)
        self._pipeline_loader = EditPipelineLoader(self)
        self._is_loading_edit_image = False
        self._skip_next_preview_frame = False
        self._compare_active = False
        self._active_adjustments: dict[str, float] = {}
        self._video_color_stats = None
        self._video_thumbnail_generation = 0
        self._video_sidebar_generation = 0
        self._video_trim_worker: VideoTrimThumbnailWorker | None = None
        self._video_sidebar_worker: VideoSidebarPreviewWorker | None = None
        self._video_sidebar_workers: list[VideoSidebarPreviewWorker] = []
        self._video_trim_diag: dict[int, dict[str, object]] = {}
        self._pending_video_duration_sec: float | None = None
        self._video_frame_step_ms = _DEFAULT_VIDEO_FRAME_STEP_MS

        # Helpers / Sub-controllers (Ported from EditController)
        self._zoom_handler = EditZoomHandler(
            viewer=self._ui.edit_image_viewer,
            zoom_in_button=self._ui.zoom_in_button,
            zoom_out_button=self._ui.zoom_out_button,
            zoom_slider=self._ui.zoom_slider,
            parent=self,
        )

        self._adjust_mode = AdjustModeState(
            self._ui,
            lambda: self._session,
            lambda: self._active_edit_viewport(),
            parent=self,
        )
        self._crop_mode = CropModeState(
            self._ui,
            lambda: self._session,
            lambda: self._active_edit_viewport(),
            parent=self,
        )
        self._current_mode = self._adjust_mode

        # Create HeaderController with UI reference for layout management
        # Uses placeholder labels that won't be displayed - only layout management is used
        self._header_controller = HeaderController(
            self._ui.location_label,
            self._ui.timestamp_label,
            ui=self._ui,
            parent=self,
        )
        self._preview_manager = EditPreviewManager(self._ui.edit_image_viewer, self)
        self._fullscreen_manager = EditFullscreenManager(
            self._ui,
            window,
            self._preview_manager,
            parent=self,
        )

        self._update_throttler = QTimer(self)
        self._update_throttler.setSingleShot(True)
        self._update_throttler.setInterval(30)  # ~30fps cap
        self._update_throttler.timeout.connect(self._perform_deferred_update)
        self._video_trim_thumbnail_timer = QTimer(self)
        self._video_trim_thumbnail_timer.setSingleShot(True)
        self._video_trim_thumbnail_timer.setInterval(1500)
        self._video_trim_thumbnail_timer.timeout.connect(self._flush_video_trim_thumbnail_request)
        self._video_sidebar_preview_timer = QTimer(self)
        self._video_sidebar_preview_timer.setSingleShot(True)
        self._video_sidebar_preview_timer.setInterval(1000)
        self._video_sidebar_preview_timer.timeout.connect(self._queue_video_sidebar_preview)
        self._pending_session_values: Optional[dict] = None
        self._preview_updates_suspended = False
        self._interaction_depth = 0
        self._eyedropper_target = "curve"

        self._connect_signals()

    def _connect_signals(self):
        # Pipeline signals
        self._pipeline_loader.imageLoaded.connect(self._on_edit_image_loaded)
        self._pipeline_loader.imageLoadFailed.connect(self._on_edit_image_load_failed)
        self._pipeline_loader.sidebarPreviewReady.connect(self._handle_sidebar_preview_ready)

        # UI wiring
        self._ui.edit_reset_button.clicked.connect(self._handle_reset_clicked)
        self._ui.edit_done_button.clicked.connect(self._handle_done_clicked)
        self._ui.edit_rotate_left_button.clicked.connect(self._handle_rotate_left_clicked)

        # Mode switching
        self._ui.edit_adjust_action.triggered.connect(lambda c: self._handle_mode_change("adjust", c))
        self._ui.edit_crop_action.triggered.connect(lambda c: self._handle_mode_change("crop", c))
        self._ui.edit_mode_control.currentIndexChanged.connect(self._handle_top_bar_index_changed)

        # Compare
        self._ui.edit_compare_button.pressed.connect(self._handle_compare_pressed)
        self._ui.edit_compare_button.released.connect(self._handle_compare_released)

        # Sidebar interactions
        self._ui.edit_sidebar.interactionStarted.connect(self.push_undo_state)
        self._ui.edit_sidebar.interactionStarted.connect(self._handle_sidebar_interaction_started)
        self._ui.edit_sidebar.interactionFinished.connect(self._handle_sidebar_interaction_finished)
        self._ui.edit_sidebar.bwParamsPreviewed.connect(self._handle_bw_params_previewed)
        self._ui.edit_sidebar.bwParamsCommitted.connect(self._handle_bw_params_committed)
        self._ui.edit_sidebar.wbParamsPreviewed.connect(self._handle_wb_params_previewed)
        self._ui.edit_sidebar.wbParamsCommitted.connect(self._handle_wb_params_committed)
        self._ui.edit_sidebar.curveParamsPreviewed.connect(self._handle_curve_params_previewed)
        self._ui.edit_sidebar.curveParamsCommitted.connect(self._handle_curve_params_committed)
        self._ui.edit_sidebar.levelsParamsPreviewed.connect(self._handle_levels_params_previewed)
        self._ui.edit_sidebar.levelsParamsCommitted.connect(self._handle_levels_params_committed)
        self._ui.edit_sidebar.definitionParamsPreviewed.connect(
            self._handle_definition_params_previewed
        )
        self._ui.edit_sidebar.definitionParamsCommitted.connect(
            self._handle_definition_params_committed
        )
        self._ui.edit_sidebar.denoiseParamsPreviewed.connect(
            self._handle_denoise_params_previewed
        )
        self._ui.edit_sidebar.denoiseParamsCommitted.connect(
            self._handle_denoise_params_committed
        )
        self._ui.edit_sidebar.sharpenParamsPreviewed.connect(
            self._handle_sharpen_params_previewed
        )
        self._ui.edit_sidebar.sharpenParamsCommitted.connect(
            self._handle_sharpen_params_committed
        )
        self._ui.edit_sidebar.vignetteParamsPreviewed.connect(
            self._handle_vignette_params_previewed
        )
        self._ui.edit_sidebar.vignetteParamsCommitted.connect(
            self._handle_vignette_params_committed
        )
        self._ui.edit_sidebar.selectiveColorParamsPreviewed.connect(
            self._handle_selective_color_params_previewed
        )
        self._ui.edit_sidebar.selectiveColorParamsCommitted.connect(
            self._handle_selective_color_params_committed
        )
        self._ui.edit_sidebar.curveEyedropperModeChanged.connect(
            self._handle_curve_eyedropper_mode_changed
        )
        self._ui.edit_sidebar.wbEyedropperModeChanged.connect(
            self._handle_wb_eyedropper_mode_changed
        )
        self._ui.edit_sidebar.selectiveColorEyedropperModeChanged.connect(
            self._handle_selective_color_eyedropper_mode_changed
        )
        self._ui.edit_sidebar.perspectiveInteractionStarted.connect(
            lambda: self._active_edit_viewport().start_perspective_interaction()
        )
        self._ui.edit_sidebar.perspectiveInteractionFinished.connect(
            lambda: self._active_edit_viewport().end_perspective_interaction()
        )
        self._ui.edit_sidebar.aspectRatioChanged.connect(
            lambda ratio: self._active_edit_viewport().set_crop_aspect_ratio(ratio)
        )
        self._ui.edit_image_viewer.cropInteractionStarted.connect(self.push_undo_state)
        self._ui.edit_image_viewer.cropChanged.connect(self._handle_crop_changed)
        self._ui.edit_image_viewer.colorPicked.connect(self._handle_color_picked)
        self._ui.video_area.cropInteractionStarted.connect(self.push_undo_state)
        self._ui.video_area.cropChanged.connect(self._handle_crop_changed)
        self._ui.video_area.colorPicked.connect(self._handle_color_picked)
        self._ui.video_area.durationChanged.connect(self._handle_video_duration_changed)
        self._ui.video_area.positionChanged.connect(self._handle_video_position_changed)
        self._ui.video_area.playbackStateChanged.connect(self._handle_video_playback_state_changed)
        self._ui.video_trim_bar.playPauseRequested.connect(self._handle_video_trim_play_pause_requested)
        self._ui.video_trim_bar.inPointChanged.connect(self._handle_trim_in_ratio_changed)
        self._ui.video_trim_bar.outPointChanged.connect(self._handle_trim_out_ratio_changed)
        self._ui.video_trim_bar.playheadSeeked.connect(self._handle_trim_playhead_seeked)
        self._ui.video_trim_bar.trimDragStarted.connect(self.push_undo_state)
        self._ui.video_trim_bar.trimDragStarted.connect(self._ui.video_area.pause)

    def _active_edit_viewport(self):
        """Return the viewport currently driving the edit workflow."""

        if self._is_video_source():
            return self._ui.video_area
        return self._ui.edit_image_viewer

    def _is_video_source(self) -> bool:
        """Return ``True`` when the active edit source is a video."""

        return (
            self._current_source is not None
            and self._current_source.suffix.lower() in VIDEO_EXTENSIONS
        )

    def is_in_fullscreen(self) -> bool:
        """Return ``True`` if the edit view is in immersive full screen mode."""

        return self._fullscreen_manager.is_in_fullscreen()

    def is_editing(self) -> bool:
        """Return ``True`` when an edit session is active."""

        return self._session is not None

    # ------------------------------------------------------------------
    # Public transport API (used by AppShortcutManager)
    # ------------------------------------------------------------------

    def video_is_transport_active(self) -> bool:
        """Return True when video transport shortcuts should be handled here.

        The shortcut manager calls this to decide whether to route Space /
        frame-step commands to the edit coordinator rather than the plain
        detail-view handler.
        """
        return self._can_handle_video_edit_transport_shortcut(allow_conflicting_focus=True)

    def toggle_video_playback(self) -> None:
        """Toggle play / pause for the video currently open in edit mode."""
        self._handle_video_trim_play_pause_requested()

    def step_video_frame(self, direction: int) -> None:
        """Advance (direction=+1) or rewind (direction=-1) one frame."""
        self._handle_video_frame_step_shortcut(direction)

    def enter_fullscreen_preview(self) -> bool:
        """Enter immersive full screen preview for the current edit session."""

        if not self._session or not self._current_source:
            return False
        adjustments = self._resolve_session_adjustments()
        return self._fullscreen_manager.enter_fullscreen_preview(
            self._current_source,
            adjustments,
        )

    def exit_fullscreen_preview(self) -> None:
        """Exit immersive full screen preview if active."""

        source = self._current_source
        adjustments = None
        if self._session is not None:
            adjustments = self._resolve_session_adjustments()
        self._fullscreen_manager.exit_fullscreen_preview(source, adjustments)

    def enter_edit_mode(self, asset_path: Path):
        """Prepares the edit view for the given asset and switches view."""
        if self._session is not None:
            return
        _LOGGER.debug(
            "[trace][edit] enter_edit_mode:start %s",
            {
                "asset": str(asset_path),
                "is_video": asset_path.suffix.lower() in VIDEO_EXTENSIONS,
            },
        )

        self._current_source = asset_path
        self._emit_video_trim_diag(
            "enter_edit_mode",
            source=asset_path,
            is_video=asset_path.suffix.lower() in VIDEO_EXTENSIONS,
        )

        # Load Adjustments
        edit_service = self._edit_service()
        adjustments = (
            edit_service.read_adjustments(asset_path)
            if edit_service is not None
            else {}
        )

        # Setup Session
        session = EditSession(self)
        session.set_values(adjustments, emit_individual=False)
        session.valuesChanged.connect(self._handle_session_changed)
        self._session = session
        self._history_manager.set_session(session)
        self._ui.edit_sidebar.set_session(session)
        self._ui.edit_sidebar.set_video_edit_mode(self._is_video_source())

        viewport = self._active_edit_viewport()
        self._zoom_handler.set_viewer(viewport)
        self._ui.video_trim_bar.setVisible(self._is_video_source())

        # Apply to Viewer
        self._apply_session_adjustments_to_viewer()

        # Reset Viewer State
        viewport.setCropMode(False, session.values())
        if self._is_video_source():
            self._skip_next_preview_frame = False
            viewport.reset_zoom()
            self._start_video_edit_load(asset_path)
        else:
            viewer = self._ui.edit_image_viewer
            current_source = viewer.current_image_source()
            self._skip_next_preview_frame = current_source == asset_path
            if not self._skip_next_preview_frame:
                viewer.reset_zoom()

        # UI State
        self._compare_active = False
        self._set_mode("adjust")
        self._header_controller.switch_to_edit_mode()
        self._zoom_handler.connect_controls()

        if self._theme_controller:
            self._theme_controller.apply_edit_theme()

        # Switch View
        self._router.show_edit()
        self._transition_manager.enter_edit_mode(animate=True)
        _LOGGER.debug(
            "[trace][edit] enter_edit_mode:after_transition_start %s",
            {
                "is_video": self._is_video_source(),
                "surface": self._ui.video_area._diag_surface_name(),
                "adjusted_preview_enabled": self._ui.video_area.adjusted_preview_enabled(),
            },
        )

        # Start Loading High-Res Image / Video
        if not self._is_video_source():
            self._start_async_edit_load(asset_path)

    def _start_async_edit_load(self, source: Path):
        if self._session is None: return
        self._is_loading_edit_image = True
        self._ui.edit_image_viewer.set_loading(not self._skip_next_preview_frame)
        self._pipeline_loader.load_image(source)

    def _start_video_edit_load(self, source: Path) -> None:
        """Initialise video playback and trim UI for the active edit session."""

        if self._session is None:
            return
        self._emit_video_trim_diag("start_video_edit_load", source=source)
        self._video_frame_step_ms = self._probe_video_frame_step_ms(source)
        self._video_color_stats = None
        self._pending_video_duration_sec = self._probe_video_duration_sec(source)
        self._video_trim_thumbnail_timer.stop()
        self._video_sidebar_preview_timer.stop()
        self._ui.video_area.set_edit_mode_active(True)
        self._ui.video_area.set_controls_enabled(False)
        self._ui.video_area.hide_controls(animate=False)
        self._ui.video_area.set_adjusted_preview_enabled(True)
        self._ui.video_area.set_adjustments(self._resolve_session_adjustments())
        trim_in, trim_out = normalise_video_trim(
            self._session.values(),
            self._pending_video_duration_sec,
        )
        if self._pending_video_duration_sec is not None:
            canonical = self._canonical_trim_updates(
                trim_in,
                trim_out,
                self._pending_video_duration_sec,
            )
            self._session.set_values(canonical, emit_individual=False)
        self._ui.video_area.load_video(
            source,
            adjustments=self._resolve_session_adjustments(),
            trim_range_ms=(int(round(trim_in * 1000.0)), int(round(trim_out * 1000.0))),
            adjusted_preview=True,
        )
        self._ui.video_trim_bar.clear()
        if self._pending_video_duration_sec is not None:
            in_ratio, out_ratio = self._trim_ratios_for_duration(self._pending_video_duration_sec)
            self._ui.video_trim_bar.set_trim_ratios(in_ratio, out_ratio)
            self._ui.video_trim_bar.set_playhead_ratio(in_ratio)
        else:
            self._ui.video_trim_bar.set_trim_ratios(0.0, 1.0)
            self._ui.video_trim_bar.set_playhead_ratio(0.0)
        self._ui.video_trim_bar.set_playing(False)
        self._queue_video_trim_thumbnails(self._pending_video_duration_sec)
        self._ui.video_area.play()

    def _on_edit_image_loaded(self, path: Path, image: QImage):
        if self._session is None or self._current_source != path: return

        try:
            self._preview_manager.start_session(image, self._session.values())
        except Exception:
            _LOGGER.exception("Failed to init preview")
            self.leave_edit_mode()
            return

        resolved = self._resolve_session_adjustments()
        self._active_adjustments = resolved
        self._ui.edit_image_viewer.set_image(image, resolved, image_source=path, reset_view=False)
        self._skip_next_preview_frame = False
        self._is_loading_edit_image = False
        self._ui.edit_image_viewer.set_loading(False)

        # Calculate target height for sidebar previews
        target_height = self._ui.edit_sidebar.preview_thumbnail_height()
        if target_height <= 0:
            target_height = 64  # Fallback

        self._pipeline_loader.prepare_sidebar_preview(
            image,
            target_height=target_height,
            full_res_image_for_fallback=image
        )

    def _on_edit_image_load_failed(self, path: Path, msg: str):
        _LOGGER.error(f"Failed to load edit image: {msg}")
        self.leave_edit_mode()

    def _handle_sidebar_preview_ready(self, result):
        if self._session:
            self._ui.edit_sidebar.set_light_preview_image(result.image, color_stats=result.stats)
            self._ui.edit_sidebar.refresh()

    def leave_edit_mode(self, *, restore_reason: str = "edit_exit"):
        """Returns to detail view."""
        source = self._current_source
        is_video_source = (
            source is not None
            and source.suffix.lower() in VIDEO_EXTENSIONS
        )
        should_restore_detail = (
            source is not None
            and self._media_session is not None
        )
        _LOGGER.debug(
            "[trace][edit] leave_edit_mode:start %s",
            {
                "source": str(source) if source is not None else None,
                "session_exists": self._session is not None,
                "is_video": is_video_source,
                "surface_before": self._ui.video_area._diag_surface_name(),
                "adjusted_preview_before": self._ui.video_area.adjusted_preview_enabled(),
            },
        )
        if self._fullscreen_manager.is_in_fullscreen():
            adjustments = None
            if self._session is not None:
                adjustments = self._resolve_session_adjustments()
            self._fullscreen_manager.exit_fullscreen_preview(source, adjustments)
        if self._session is not None:
            self._active_edit_viewport().setCropMode(False, self._session.values())
        self._active_edit_viewport().set_eyedropper_mode(False)
        self._current_source = None
        self._session = None
        self._preview_manager.stop_session()
        self._zoom_handler.disconnect_controls()
        self._header_controller.restore_detail_mode()
        self._video_color_stats = None
        pending_duration_sec = self._pending_video_duration_sec
        self._pending_video_duration_sec = None
        self._video_trim_thumbnail_timer.stop()
        self._video_sidebar_preview_timer.stop()
        self._video_thumbnail_generation += 1
        self._video_sidebar_generation += 1
        self._video_trim_worker = None
        self._video_sidebar_worker = None
        self._video_trim_diag.clear()
        self._video_frame_step_ms = _DEFAULT_VIDEO_FRAME_STEP_MS

        if self._theme_controller:
            self._theme_controller.restore_global_theme()

        self._ui.edit_image_viewer.set_surface_color_override(
            viewer_surface_color(self._ui.edit_image_viewer)
        )
        self._ui.video_area.set_edit_mode_active(False)
        self._ui.video_area.set_controls_enabled(True)
        self._ui.video_trim_bar.hide()

        self._ui.edit_sidebar.set_session(None)
        self._ui.edit_sidebar.set_video_edit_mode(False)
        self._router.show_detail()
        show_filmstrip = True
        toggle_filmstrip_action = getattr(self._ui, "toggle_filmstrip_action", None)
        if toggle_filmstrip_action is not None:
            show_filmstrip = bool(toggle_filmstrip_action.isChecked())
        self._transition_manager.leave_edit_mode(
            animate=True,
            show_filmstrip=show_filmstrip,
        )
        if should_restore_detail:
            self._media_session.request_restore(
                MediaRestoreRequest(
                    path=source,
                    reason=restore_reason,
                    duration_sec=pending_duration_sec if is_video_source else None,
                )
            )
        _LOGGER.debug(
            "[trace][edit] leave_edit_mode:after_transition_start %s",
            {
                "source": str(source) if source is not None else None,
                "surface_now": self._ui.video_area._diag_surface_name(),
                "adjusted_preview_now": self._ui.video_area.adjusted_preview_enabled(),
            },
        )

    # --- Actions ---

    def _handle_done_clicked(self):
        if not self._session or not self._current_source:
            self.leave_edit_mode()
            return

        source = self._current_source
        self._session.set_values(
            self._active_edit_viewport().crop_values(),
            emit_individual=False,
        )
        if self._adjustment_committer is None:
            navigation = self._navigation
            if navigation:
                navigation.pause_library_watcher()
            try:
                edit_service = self._edit_service()
                if edit_service is None:
                    raise RuntimeError("Edit service is unavailable")
                edit_service.write_adjustments(source, self._session.values())
                self._asset_vm.invalidate_thumbnail(str(source))
            finally:
                if navigation:
                    navigation.resume_library_watcher()
            self.leave_edit_mode(restore_reason="edit_done")
            return
        if self._adjustment_committer.commit(source, self._session.values(), reason="edit_done"):
            self.leave_edit_mode(restore_reason="edit_done")

    def _handle_reset_clicked(self):
        if self._session:
            self.push_undo_state()
            self._session.reset()

    def _handle_rotate_left_clicked(self):
        if self._session:
            self.push_undo_state()
            updates = self._active_edit_viewport().rotate_image_ccw()
            self._session.set_values(updates, emit_individual=False)

    def _handle_compare_pressed(self):
        self._compare_active = True
        viewport = self._active_edit_viewport()
        viewport.set_adjustments({})
        if self._is_video_source():
            duration_ms = self._ui.video_area.player_bar.duration()
            if duration_ms > 0:
                self._ui.video_area.set_trim_range_ms(0, duration_ms)
                self._ui.video_trim_bar.set_trim_ratios(0.0, 1.0)
                self._ui.video_trim_bar.set_playhead_ratio(self._safe_video_ratio(0))

    def _handle_compare_released(self):
        self._compare_active = False
        self._apply_session_adjustments_to_viewer()

    def push_undo_state(self):
        self._history_manager.push_undo_state()

    def undo(self):
        self._history_manager.undo()

    def redo(self):
        self._history_manager.redo()

    # --- Helpers ---

    def _edit_service(self) -> EditServicePort | None:
        if self._edit_service_getter is None:
            return None
        return self._edit_service_getter()

    def _handle_session_changed(self, values: dict):
        """Buffer session updates to avoid spamming the preview pipeline."""
        self._pending_session_values = values
        if not self._update_throttler.isActive():
            self._perform_deferred_update()
            self._update_throttler.start()

    def _perform_deferred_update(self):
        if self._session:
            # Always fetch the authoritative state from the session
            current_values = self._session.values()
            if not self._preview_updates_suspended and not self._is_video_source():
                self._preview_manager.update_adjustments(current_values)
            self._apply_session_adjustments_to_viewer()
            self._pending_session_values = None

    def _apply_session_adjustments_to_viewer(self):
        if self._session and not self._compare_active:
            adj = self._resolve_session_adjustments()
            self._active_adjustments = adj
            self._active_edit_viewport().set_adjustments(adj)
            if self._is_video_source():
                self._apply_video_trim_from_session()

    def _resolve_session_adjustments(self):
        if not self._session:
            return {}
        return self._resolve_adjustments_for_values(self._session.values())

    def _resolve_adjustments_for_values(self, values: dict):
        if self._is_video_source():
            return resolve_adjustment_mapping(values, stats=self._video_color_stats)
        try:
            return self._preview_manager.resolve_adjustments(values)
        except AttributeError:
            return resolve_adjustment_mapping(values, stats=self._preview_manager.color_stats())

    def _video_duration_ms(self) -> int:
        return max(int(self._ui.video_area.player_bar.duration()), 0)

    def _video_duration_sec(self) -> float | None:
        # Prefer the probed/player-reported absolute duration tracked by
        # _pending_video_duration_sec.  player_bar.duration() may reflect a
        # trim-relative display value set by PlaybackCoordinator before edit
        # mode was entered, which would cause wrong trim calculations.
        if self._pending_video_duration_sec is not None and self._pending_video_duration_sec > 0.0:
            return self._pending_video_duration_sec
        duration_ms = self._video_duration_ms()
        if duration_ms <= 0:
            return None
        return duration_ms / 1000.0

    def _normalised_video_trim(self) -> tuple[float, float]:
        if self._session is None:
            return (0.0, 0.0)
        return normalise_video_trim(self._session.values(), self._video_duration_sec())

    def _canonical_trim_updates(
        self,
        trim_in_sec: float,
        trim_out_sec: float,
        duration_sec: float | None,
    ) -> dict[str, float]:
        canonical_in = 0.0 if trim_in_sec <= 1e-3 else float(trim_in_sec)
        canonical_out = float(trim_out_sec)
        if duration_sec is not None and canonical_in <= 1e-3 and abs(trim_out_sec - duration_sec) <= 1e-3:
            canonical_out = 0.0
        return {
            VIDEO_TRIM_IN_KEY: canonical_in,
            VIDEO_TRIM_OUT_KEY: canonical_out,
        }

    def _trim_ratios_for_session(self) -> tuple[float, float]:
        return self._trim_ratios_for_duration(self._video_duration_sec())

    def _trim_ratios_for_duration(self, duration_sec: float | None) -> tuple[float, float]:
        if duration_sec is None or duration_sec <= 0.0:
            return (0.0, 1.0)
        trim_in_sec, trim_out_sec = normalise_video_trim(
            self._session.values() if self._session is not None else {},
            duration_sec,
        )
        return (
            max(0.0, min(1.0, trim_in_sec / duration_sec)),
            max(0.0, min(1.0, trim_out_sec / duration_sec)),
        )

    def _safe_video_ratio(self, position_ms: int) -> float:
        duration_ms = self._video_duration_ms()
        if duration_ms <= 0:
            return 0.0
        return max(0.0, min(1.0, float(position_ms) / float(duration_ms)))

    def _apply_video_trim_from_session(self) -> None:
        trim_in_sec, trim_out_sec = self._normalised_video_trim()
        self._ui.video_area.set_trim_range_ms(
            int(round(trim_in_sec * 1000.0)),
            int(round(trim_out_sec * 1000.0)),
        )
        in_ratio, out_ratio = self._trim_ratios_for_session()
        self._ui.video_trim_bar.set_trim_ratios(in_ratio, out_ratio)
        self._ui.video_trim_bar.set_playhead_ratio(
            self._safe_video_ratio(self._ui.video_area.player_bar.position())
        )

    def _refresh_video_sidebar_preview(self) -> None:
        if self._current_source is None or not self._is_video_source():
            return
        duration_sec = self._video_duration_sec()
        trim_in_sec, trim_out_sec = self._normalised_video_trim()
        target_height = self._ui.edit_sidebar.preview_thumbnail_height()
        if target_height <= 0:
            target_height = 64
        self._video_sidebar_generation += 1
        generation = self._video_sidebar_generation
        worker = VideoSidebarPreviewWorker(
            self._current_source,
            generation=generation,
            target_size=QSize(target_height * 3, target_height * 2),
            still_image_time=trim_in_sec + max(trim_out_sec - trim_in_sec, 0.0) * 0.5,
            duration=duration_sec,
            trim_in_sec=trim_in_sec,
            trim_out_sec=trim_out_sec,
        )

        def _handle_ready(result: VideoSidebarPreviewResult, worker_generation: int) -> None:
            if worker_generation != self._video_sidebar_generation:
                return
            if self._session is None or not self._is_video_source():
                return
            self._video_color_stats = result.stats
            self._session.set_color_stats(result.stats)
            self._pipeline_loader.prepare_sidebar_preview_inline(
                result.image,
                target_height=target_height,
                full_res_image_for_fallback=result.image,
            )
            self._apply_session_adjustments_to_viewer()

        def _handle_error(worker_generation: int, message: str) -> None:
            if worker_generation != self._video_sidebar_generation:
                return
            _LOGGER.debug(
                "Failed to generate video sidebar preview for %s: %s",
                self._current_source,
                message,
            )

        def _handle_finished(worker_generation: int) -> None:
            del worker_generation
            try:
                self._video_sidebar_workers.remove(worker)
            except ValueError:
                pass
            if worker is self._video_sidebar_worker:
                self._video_sidebar_worker = None

        self._video_sidebar_worker = worker
        if not hasattr(self, "_video_sidebar_workers"):
            self._video_sidebar_workers = []
        self._video_sidebar_workers.append(worker)
        worker.signals.ready.connect(_handle_ready, Qt.ConnectionType.QueuedConnection)
        worker.signals.error.connect(_handle_error, Qt.ConnectionType.QueuedConnection)
        worker.signals.finished.connect(_handle_finished, Qt.ConnectionType.QueuedConnection)
        QThreadPool.globalInstance().start(worker, -1)

    def _flush_video_trim_thumbnail_request(self) -> None:
        self._queue_video_trim_thumbnails(self._pending_video_duration_sec)

    def _queue_video_sidebar_preview(self) -> None:
        self._refresh_video_sidebar_preview()

    def _queue_video_trim_thumbnails(self, duration_sec: float | None) -> None:
        if self._current_source is None:
            self._emit_video_trim_diag(
                "skip",
                source=self._current_source,
                duration_sec=duration_sec,
            )
            return
        diag_duration = None if duration_sec is None else round(duration_sec, 3)
        target_width = 96
        width, count = self._estimate_video_trim_thumbnail_request(self._current_source)
        self._video_thumbnail_generation += 1
        generation = self._video_thumbnail_generation
        worker = VideoTrimThumbnailWorker(
            self._current_source,
            generation=generation,
            duration_sec=duration_sec,
            target_height=72,
            target_width=target_width,
            count=count,
        )
        self._video_trim_diag[generation] = {
            "source": str(self._current_source),
            "requested_count": count,
            "duration_sec": diag_duration,
            "received_images": 0,
            "ready_batches": 0,
            "errors": [],
        }
        self._emit_video_trim_diag(
            "queue",
            generation=generation,
            source=self._current_source,
            duration_sec=diag_duration,
            width=width,
            requested_count=count,
        )
        self._ui.video_trim_bar.clear()
        self._video_trim_worker = worker
        worker.signals.thumbnail.connect(
            self._handle_video_trim_thumbnail_image,
            Qt.ConnectionType.QueuedConnection,
        )
        worker.signals.ready.connect(
            self._handle_video_trim_thumbnails_ready,
            Qt.ConnectionType.QueuedConnection,
        )
        worker.signals.error.connect(
            self._handle_video_trim_thumbnail_error,
            Qt.ConnectionType.QueuedConnection,
        )
        worker.signals.finished.connect(
        self._handle_video_trim_thumbnail_finished,
            Qt.ConnectionType.QueuedConnection,
        )
        QThreadPool.globalInstance().start(worker, -1)

    def _handle_video_trim_thumbnail_image(self, image: QImage, generation: int) -> None:
        if generation != self._video_thumbnail_generation:
            self._emit_video_trim_diag("drop_image_stale", generation=generation)
            return
        if self._session is None or not self._is_video_source() or image.isNull():
            self._emit_video_trim_diag(
                "drop_image_invalid",
                generation=generation,
                is_video=self._is_video_source(),
                session=self._session is not None,
                is_null=image.isNull(),
            )
            return
        diag = self._video_trim_diag.setdefault(generation, {})
        received = int(diag.get("received_images", 0)) + 1
        diag["received_images"] = received
        if received <= 3 or received % 5 == 0:
            self._emit_video_trim_diag(
                "image",
                generation=generation,
                received_images=received,
                size=f"{image.width()}x{image.height()}",
            )
        self._apply_video_trim_from_session()
        self._ui.video_trim_bar.add_thumbnail(QPixmap.fromImage(image))

    def _handle_video_trim_thumbnails_ready(self, images: list[QImage], generation: int) -> None:
        if generation != self._video_thumbnail_generation:
            self._emit_video_trim_diag("drop_ready_stale", generation=generation, image_count=len(images))
            return
        if self._session is None or not self._is_video_source():
            self._emit_video_trim_diag(
                "drop_ready_invalid",
                generation=generation,
                image_count=len(images),
                is_video=self._is_video_source(),
                session=self._session is not None,
            )
            return
        diag = self._video_trim_diag.setdefault(generation, {})
        diag["ready_batches"] = int(diag.get("ready_batches", 0)) + 1
        diag["ready_image_count"] = len(images)
        self._emit_video_trim_diag(
            "ready",
            generation=generation,
            image_count=len(images),
            ready_batches=diag["ready_batches"],
        )
        self._apply_video_trim_from_session()
        self._ui.video_trim_bar.set_thumbnails(
            [
                QPixmap.fromImage(image)
                for image in images
                if image is not None and not image.isNull()
            ]
        )

    def _handle_video_trim_thumbnail_error(self, generation: int, message: str) -> None:
        if generation != self._video_thumbnail_generation:
            self._emit_video_trim_diag("drop_error_stale", generation=generation, message=message)
            return
        diag = self._video_trim_diag.setdefault(generation, {})
        errors = list(diag.get("errors", []))
        errors.append(message)
        diag["errors"] = errors
        self._emit_video_trim_diag("error", generation=generation, message=message)
        _LOGGER.warning("Failed to generate trim thumbnails for %s: %s", self._current_source, message)

    def _handle_video_trim_thumbnail_finished(self, generation: int) -> None:
        if generation != self._video_thumbnail_generation:
            self._emit_video_trim_diag("drop_finished_stale", generation=generation)
            return
        diag = self._video_trim_diag.get(generation, {})
        self._emit_video_trim_diag(
            "finished",
            generation=generation,
            requested_count=diag.get("requested_count"),
            received_images=diag.get("received_images"),
            ready_batches=diag.get("ready_batches"),
            ready_image_count=diag.get("ready_image_count"),
            errors=diag.get("errors"),
        )
        self._video_trim_worker = None

    def _estimate_video_trim_thumbnail_request(self, source: Path) -> tuple[int, int]:
        width = self._ui.video_trim_bar.thumbnail_view_width()
        if width < 100:
            width = 1000

        rotation, raw_w, raw_h = probe_video_rotation(source)
        if rotation in {90, 270}:
            display_w, display_h = raw_h, raw_w
        else:
            display_w, display_h = raw_w, raw_h

        logical_thumb_h = 42.0
        logical_thumb_w = 0.0
        if display_w > 0 and display_h > 0:
            logical_thumb_w = display_w * (logical_thumb_h / float(display_h))

        if logical_thumb_w > 1.0:
            count = int(math.ceil(width / logical_thumb_w)) + 2
        else:
            count = width // max(96, 1)

        count = max(int(count), 6)
        count = min(count, 60)
        return width, count

    def _handle_video_duration_changed(self, duration_ms: int) -> None:
        if self._session is None or not self._is_video_source():
            return
        duration_sec = max(float(duration_ms) / 1000.0, 0.0)
        self._pending_video_duration_sec = duration_sec
        trim_in_sec, trim_out_sec = normalise_video_trim(self._session.values(), duration_sec)
        canonical = self._canonical_trim_updates(trim_in_sec, trim_out_sec, duration_sec)
        self._session.set_values(canonical, emit_individual=False)
        self._apply_video_trim_from_session()
        self._emit_video_trim_diag(
            "duration_changed",
            duration_ms=duration_ms,
            duration_sec=round(duration_sec, 3),
            trim_in=round(trim_in_sec, 3),
            trim_out=round(trim_out_sec, 3),
        )
        active_diag = self._video_trim_diag.get(self._video_thumbnail_generation, {})
        has_thumbnails = bool(active_diag.get("received_images")) or bool(active_diag.get("ready_image_count"))
        desired_count = 0
        if self._current_source is not None:
            _, desired_count = self._estimate_video_trim_thumbnail_request(self._current_source)
        requested_count = int(active_diag.get("requested_count", 0) or 0)
        needs_more = has_thumbnails and requested_count < desired_count
        if self._video_trim_worker is None and (not has_thumbnails or needs_more):
            self._queue_video_trim_thumbnails(duration_sec)
        else:
            self._emit_video_trim_diag(
                "duration_changed_no_requeue",
                generation=self._video_thumbnail_generation,
                worker_active=self._video_trim_worker is not None,
                has_thumbnails=has_thumbnails,
                requested_count=requested_count,
                desired_count=desired_count,
            )
        self._video_sidebar_preview_timer.start()

    def _emit_video_trim_diag(self, stage: str, **fields: object) -> None:
        parts = [f"{key}={value}" for key, value in fields.items()]
        message = f"[video-trim] {stage}"
        if parts:
            message += " | " + ", ".join(parts)
        _APP_LOGGER.warning(message)

    def _handle_video_position_changed(self, position_ms: int) -> None:
        if self._session is None or not self._is_video_source():
            return
        self._ui.video_trim_bar.set_playhead_ratio(self._safe_video_ratio(position_ms))

    def _handle_video_playback_state_changed(self, is_playing: bool) -> None:
        """Keep the trim-bar transport button aligned with the edit preview state."""

        if self._session is None or not self._is_video_source():
            return
        self._ui.video_trim_bar.set_playing(is_playing)

    def _handle_video_trim_play_pause_requested(self) -> None:
        """Toggle playback from the trim bar's demo-style play button."""

        if self._session is None or not self._is_video_source():
            return
        if self._ui.video_area.is_playing():
            self._ui.video_area.pause()
        else:
            self._ui.video_area.play()

    def _handle_video_play_pause_shortcut(self) -> None:
        """Toggle play/pause via keyboard shortcut while in edit mode."""
        if not self._can_handle_video_edit_transport_shortcut(allow_conflicting_focus=True):
            return
        if self._ui.video_area.is_playing():
            self._ui.video_area.pause()
        else:
            self._ui.video_area.play()
        self._ui.video_area.note_activity()

    def _handle_video_frame_step_shortcut(self, direction: int) -> None:
        if direction == 0:
            return
        if not self._can_handle_video_edit_transport_shortcut(allow_conflicting_focus=False):
            return
        step_ms = max(int(self._video_frame_step_ms), 1)
        position_ms = int(self._ui.video_area.player_bar.position())
        self._ui.video_area.pause()
        self._ui.video_area.seek(position_ms + int(direction) * step_ms)
        self._ui.video_area.note_activity()

    def _can_handle_video_edit_transport_shortcut(self, *, allow_conflicting_focus: bool) -> bool:
        if self._session is None or not self._is_video_source():
            return False
        if not self._router.is_edit_view_active():
            return False
        if allow_conflicting_focus:
            return True
        return not self._focus_blocks_video_transport_shortcuts(QApplication.focusWidget())

    @staticmethod
    def _focus_blocks_video_transport_shortcuts(widget: QWidget | None) -> bool:
        if widget is None:
            return False
        return isinstance(
            widget,
            (
                QAbstractSlider,
                QAbstractSpinBox,
                QComboBox,
                QLineEdit,
                QPlainTextEdit,
                QTextEdit,
            ),
        )

    def _probe_video_frame_step_ms(self, source: Path) -> int:
        try:
            info = read_video_meta(source)
        except Exception:
            _LOGGER.debug("Failed to probe frame rate for %s", source, exc_info=True)
            return _DEFAULT_VIDEO_FRAME_STEP_MS

        try:
            frame_rate = float(info.get("frame_rate"))
        except (TypeError, ValueError):
            return _DEFAULT_VIDEO_FRAME_STEP_MS
        if frame_rate <= 0.0:
            return _DEFAULT_VIDEO_FRAME_STEP_MS
        return max(int(round(1000.0 / frame_rate)), 1)

    def _probe_video_duration_sec(self, source: Path) -> float | None:
        try:
            info = read_video_meta(source)
        except Exception:
            _LOGGER.debug("Failed to probe duration for %s", source, exc_info=True)
            return None

        try:
            duration_sec = float(info.get("dur") or info.get("duration") or 0.0)
        except (AttributeError, TypeError, ValueError):
            return None
        if duration_sec <= 0.0:
            return None
        return duration_sec

    def _handle_trim_in_ratio_changed(self, ratio: float) -> None:
        if self._session is None or not self._is_video_source():
            return
        duration_sec = self._video_duration_sec()
        if duration_sec is None or duration_sec <= 0.0:
            return
        trim_out_ratio = self._ui.video_trim_bar.trim_ratios()[1]
        trim_in_sec = max(0.0, min(1.0, float(ratio))) * duration_sec
        trim_out_sec = max(trim_in_sec, max(0.0, min(1.0, float(trim_out_ratio))) * duration_sec)
        self._session.set_values(
            self._canonical_trim_updates(trim_in_sec, trim_out_sec, duration_sec),
            emit_individual=False,
        )

    def _handle_trim_out_ratio_changed(self, ratio: float) -> None:
        if self._session is None or not self._is_video_source():
            return
        duration_sec = self._video_duration_sec()
        if duration_sec is None or duration_sec <= 0.0:
            return
        trim_in_ratio = self._ui.video_trim_bar.trim_ratios()[0]
        trim_in_sec = max(0.0, min(1.0, float(trim_in_ratio))) * duration_sec
        trim_out_sec = max(trim_in_sec, max(0.0, min(1.0, float(ratio))) * duration_sec)
        self._session.set_values(
            self._canonical_trim_updates(trim_in_sec, trim_out_sec, duration_sec),
            emit_individual=False,
        )

    def _handle_trim_playhead_seeked(self, ratio: float) -> None:
        if self._session is None or not self._is_video_source():
            return
        duration_ms = self._video_duration_ms()
        if duration_ms <= 0:
            return
        self._ui.video_area.seek(int(round(max(0.0, min(1.0, float(ratio))) * duration_ms)))

    def _handle_crop_changed(self, cx, cy, w, h):
        if self._session:
            self._session.set_values({
                "Crop_CX": float(cx), "Crop_CY": float(cy),
                "Crop_W": float(w), "Crop_H": float(h)
            }, emit_individual=False)

    def _handle_bw_params_previewed(self, params) -> None:
        """Apply transient Black & White previews without mutating session state."""

        if self._session is None or self._compare_active:
            return

        try:
            preview_values = self._session.values()
            preview_values.update({
                "BW_Enabled": True,
                "BW_Intensity": float(params.intensity),
                "BW_Neutrals": float(params.neutrals),
                "BW_Tone": float(params.tone),
                "BW_Grain": float(params.grain),
                "BW_Master": float(params.master),
            })
            adjustments = self._resolve_adjustments_for_values(preview_values)
        except Exception:
            _LOGGER.exception("Failed to resolve BW preview adjustments")
            return

        self._active_edit_viewport().set_adjustments(adjustments)

    def _handle_bw_params_committed(self, params) -> None:
        """Persist Black & White adjustments into the active edit session."""

        if self._session is None:
            return

        updates = {
            "BW_Enabled": True,
            "BW_Intensity": float(params.intensity),
            "BW_Neutrals": float(params.neutrals),
            "BW_Tone": float(params.tone),
            "BW_Grain": float(params.grain),
            "BW_Master": float(params.master),
        }
        self._session.set_values(updates)

    def _handle_wb_params_previewed(self, params) -> None:
        """Apply transient White Balance previews without mutating session state."""

        if self._session is None or self._compare_active:
            return

        try:
            preview_values = self._session.values()
            preview_values.update({
                "WB_Enabled": True,
                "WB_Warmth": float(params.warmth),
                "WB_Temperature": float(params.temperature),
                "WB_Tint": float(params.tint),
            })
            adjustments = self._resolve_adjustments_for_values(preview_values)
        except Exception:
            _LOGGER.exception("Failed to resolve WB preview adjustments")
            return

        self._active_edit_viewport().set_adjustments(adjustments)

    def _handle_wb_params_committed(self, params) -> None:
        """Persist White Balance adjustments into the active edit session."""

        if self._session is None:
            return

        updates = {
            "WB_Enabled": True,
            "WB_Warmth": float(params.warmth),
            "WB_Temperature": float(params.temperature),
            "WB_Tint": float(params.tint),
        }
        self._session.set_values(updates)

    def _handle_curve_params_previewed(self, curve_data: dict) -> None:
        """Apply transient curve previews without mutating session state."""

        if self._session is None or self._compare_active:
            return

        try:
            preview_values = self._session.values()
            preview_values.update({
                "Curve_Enabled": True,
                "Curve_RGB": curve_data.get("RGB", list(DEFAULT_CURVE_POINTS)),
                "Curve_Red": curve_data.get("Red", list(DEFAULT_CURVE_POINTS)),
                "Curve_Green": curve_data.get("Green", list(DEFAULT_CURVE_POINTS)),
                "Curve_Blue": curve_data.get("Blue", list(DEFAULT_CURVE_POINTS)),
            })
            adjustments = self._resolve_adjustments_for_values(preview_values)
        except Exception:
            _LOGGER.exception("Failed to resolve curve preview adjustments")
            return

        self._active_edit_viewport().set_adjustments(adjustments)

    def _handle_curve_params_committed(self, curve_data: dict) -> None:
        """Persist curve adjustments into the active edit session."""

        if self._session is None:
            return

        updates = {
            "Curve_Enabled": True,
            "Curve_RGB": curve_data.get("RGB", list(DEFAULT_CURVE_POINTS)),
            "Curve_Red": curve_data.get("Red", list(DEFAULT_CURVE_POINTS)),
            "Curve_Green": curve_data.get("Green", list(DEFAULT_CURVE_POINTS)),
            "Curve_Blue": curve_data.get("Blue", list(DEFAULT_CURVE_POINTS)),
        }
        self._session.set_values(updates)

    def _handle_levels_params_previewed(self, levels_data: dict) -> None:
        """Apply transient levels previews without mutating session state."""

        if self._session is None or self._compare_active:
            return

        try:
            preview_values = self._session.values()
            preview_values.update({
                "Levels_Enabled": True,
                "Levels_Handles": levels_data.get("Handles", list(DEFAULT_LEVELS_HANDLES)),
            })
            adjustments = self._resolve_adjustments_for_values(preview_values)
        except Exception:
            _LOGGER.exception("Failed to resolve levels preview adjustments")
            return

        self._active_edit_viewport().set_adjustments(adjustments)

    def _handle_levels_params_committed(self, levels_data: dict) -> None:
        """Persist levels adjustments into the active edit session."""

        if self._session is None:
            return

        updates = {
            "Levels_Enabled": True,
            "Levels_Handles": levels_data.get("Handles", list(DEFAULT_LEVELS_HANDLES)),
        }
        self._session.set_values(updates)

    def _handle_definition_params_previewed(self, def_data: dict) -> None:
        """Apply transient definition previews without mutating session state."""

        if self._session is None or self._compare_active:
            return

        try:
            preview_values = self._session.values()
            preview_values.update({
                "Definition_Enabled": True,
                "Definition_Value": float(def_data.get("Value", 0.0)),
            })
            adjustments = self._resolve_adjustments_for_values(preview_values)
        except Exception:
            _LOGGER.exception("Failed to resolve definition preview adjustments")
            return

        self._active_edit_viewport().set_adjustments(adjustments)

    def _handle_definition_params_committed(self, def_data: dict) -> None:
        """Persist definition adjustments into the active edit session."""

        if self._session is None:
            return

        updates = {
            "Definition_Enabled": True,
            "Definition_Value": float(def_data.get("Value", 0.0)),
        }
        self._session.set_values(updates)

    def _handle_denoise_params_previewed(self, dn_data: dict) -> None:
        """Apply transient noise-reduction previews without mutating session state."""

        if self._session is None or self._compare_active:
            return

        try:
            preview_values = self._session.values()
            preview_values.update({
                "Denoise_Enabled": True,
                "Denoise_Amount": float(dn_data.get("Amount", 0.0)),
            })
            adjustments = self._resolve_adjustments_for_values(preview_values)
        except Exception:
            _LOGGER.exception("Failed to resolve denoise preview adjustments")
            return

        self._active_edit_viewport().set_adjustments(adjustments)

    def _handle_denoise_params_committed(self, dn_data: dict) -> None:
        """Persist noise-reduction adjustments into the active edit session."""

        if self._session is None:
            return

        updates = {
            "Denoise_Enabled": True,
            "Denoise_Amount": float(dn_data.get("Amount", 0.0)),
        }
        self._session.set_values(updates)

    def _handle_sharpen_params_previewed(self, sh_data: dict) -> None:
        """Apply transient sharpen previews without mutating session state."""

        if self._session is None or self._compare_active:
            return

        try:
            preview_values = self._session.values()
            preview_values.update({
                "Sharpen_Enabled": True,
                "Sharpen_Intensity": float(sh_data.get("Intensity", 0.0)),
                "Sharpen_Edges": float(sh_data.get("Edges", 0.0)),
                "Sharpen_Falloff": float(sh_data.get("Falloff", 0.0)),
            })
            adjustments = self._resolve_adjustments_for_values(preview_values)
        except Exception:
            _LOGGER.exception("Failed to resolve sharpen preview adjustments")
            return

        self._active_edit_viewport().set_adjustments(adjustments)

    def _handle_sharpen_params_committed(self, sh_data: dict) -> None:
        """Persist sharpen adjustments into the active edit session."""

        if self._session is None:
            return

        updates = {
            "Sharpen_Enabled": True,
            "Sharpen_Intensity": float(sh_data.get("Intensity", 0.0)),
            "Sharpen_Edges": float(sh_data.get("Edges", 0.0)),
            "Sharpen_Falloff": float(sh_data.get("Falloff", 0.0)),
        }
        self._session.set_values(updates)

    def _handle_vignette_params_previewed(self, vig_data: dict) -> None:
        """Apply transient vignette previews without mutating session state."""

        if self._session is None or self._compare_active:
            return

        try:
            preview_values = self._session.values()
            preview_values.update({
                "Vignette_Enabled": True,
                "Vignette_Strength": float(vig_data.get("Strength", 0.0)),
                "Vignette_Radius": float(vig_data.get("Radius", 0.50)),
                "Vignette_Softness": float(vig_data.get("Softness", 0.0)),
            })
            adjustments = self._resolve_adjustments_for_values(preview_values)
        except Exception:
            _LOGGER.exception("Failed to resolve vignette preview adjustments")
            return

        self._active_edit_viewport().set_adjustments(adjustments)

    def _handle_vignette_params_committed(self, vig_data: dict) -> None:
        """Persist vignette adjustments into the active edit session."""

        if self._session is None:
            return

        updates = {
            "Vignette_Enabled": True,
            "Vignette_Strength": float(vig_data.get("Strength", 0.0)),
            "Vignette_Radius": float(vig_data.get("Radius", 0.50)),
            "Vignette_Softness": float(vig_data.get("Softness", 0.0)),
        }
        self._session.set_values(updates)

    def _handle_selective_color_params_previewed(self, sc_data: dict) -> None:
        """Apply transient Selective Color previews without mutating session state."""

        if self._session is None or self._compare_active:
            return

        try:
            preview_values = self._session.values()
            preview_values.update({
                "SelectiveColor_Enabled": True,
                "SelectiveColor_Ranges": sc_data.get(
                    "Ranges",
                    [list(r) for r in DEFAULT_SELECTIVE_COLOR_RANGES],
                ),
            })
            adjustments = self._resolve_adjustments_for_values(preview_values)
        except Exception:
            _LOGGER.exception("Failed to resolve selective color preview adjustments")
            return

        self._active_edit_viewport().set_adjustments(adjustments)

    def _handle_selective_color_params_committed(self, sc_data: dict) -> None:
        """Persist Selective Color adjustments into the active edit session."""

        if self._session is None:
            return

        updates = {
            "SelectiveColor_Enabled": True,
            "SelectiveColor_Ranges": sc_data.get(
                "Ranges",
                [list(r) for r in DEFAULT_SELECTIVE_COLOR_RANGES],
            ),
        }
        self._session.set_values(updates)

    def _handle_curve_eyedropper_mode_changed(self, mode: object) -> None:
        """Toggle eyedropper sampling on the GL image viewer."""

        active = mode is not None
        if active:
            self._eyedropper_target = "curve"
            # Deactivate the WB and Selective Color eyedroppers to enforce mutual exclusion.
            self._ui.edit_sidebar.deactivate_wb_eyedropper()
            self._ui.edit_sidebar.deactivate_selective_color_eyedropper()
        self._active_edit_viewport().set_eyedropper_mode(active)

    def _handle_wb_eyedropper_mode_changed(self, mode: object) -> None:
        """Toggle eyedropper sampling on the GL image viewer for WB."""

        active = mode is not None
        if active:
            self._eyedropper_target = "wb"
            # Deactivate the Curve eyedropper to enforce mutual exclusion.
            self._ui.edit_sidebar.deactivate_curve_eyedropper()
            self._ui.edit_sidebar.deactivate_selective_color_eyedropper()
        self._active_edit_viewport().set_eyedropper_mode(active)

    def _handle_selective_color_eyedropper_mode_changed(self, mode: object) -> None:
        """Toggle eyedropper sampling on the GL image viewer for Selective Color."""

        active = mode is not None
        if active:
            self._eyedropper_target = "selective_color"
            # Deactivate the other eyedroppers to enforce mutual exclusion.
            self._ui.edit_sidebar.deactivate_curve_eyedropper()
            self._ui.edit_sidebar.deactivate_wb_eyedropper()
        self._active_edit_viewport().set_eyedropper_mode(active)

    def _handle_color_picked(self, r: float, g: float, b: float) -> None:
        """Forward eyedropper color picks to the appropriate section."""

        if self._eyedropper_target == "wb":
            self._ui.edit_sidebar.handle_wb_color_picked(r, g, b)
        elif self._eyedropper_target == "selective_color":
            self._ui.edit_sidebar.handle_selective_color_color_picked(r, g, b)
        else:
            self._ui.edit_sidebar.handle_curve_color_picked(r, g, b)

    def _handle_mode_change(self, mode: str, checked: bool):
        if checked: self._set_mode(mode)

    def _handle_top_bar_index_changed(self, index: int):
        self._set_mode("adjust" if index == 0 else "crop")

    def _set_mode(self, mode: str):
        new_state = self._adjust_mode if mode == "adjust" else self._crop_mode
        if self._current_mode == new_state: return
        self._current_mode.exit()
        self._current_mode = new_state
        self._current_mode.enter()

    def _handle_sidebar_interaction_started(self) -> None:
        """Suspend heavy preview rendering while the user drags adjustment sliders."""

        self._interaction_depth += 1
        if self._interaction_depth == 1:
            self._preview_updates_suspended = True
            self._preview_manager.cancel_pending_updates()

    def _handle_sidebar_interaction_finished(self) -> None:
        """Re-enable preview rendering after slider interaction completes."""

        if self._interaction_depth > 0:
            self._interaction_depth -= 1
        if self._interaction_depth == 0:
            self._preview_updates_suspended = False
            if self._session is not None:
                self._preview_manager.update_adjustments(self._session.values())

    def shutdown(self):
        """Cleanup resources on app exit."""
        if self._session:
            self.leave_edit_mode()

"""Coordinator for the Edit View workflow."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional
from collections.abc import Callable

from PySide6.QtCore import QObject, Slot, QSize, QTimer
from PySide6.QtGui import QImage

from src.iPhoto.gui.coordinators.view_router import ViewRouter
from src.iPhoto.events.bus import EventBus
from src.iPhoto.gui.ui.models.edit_session import EditSession
from src.iPhoto.gui.ui.controllers.edit_history_manager import EditHistoryManager
from src.iPhoto.gui.ui.controllers.edit_pipeline_loader import EditPipelineLoader
from src.iPhoto.gui.ui.controllers.edit_preview_manager import EditPreviewManager
from src.iPhoto.gui.ui.controllers.edit_zoom_handler import EditZoomHandler
from src.iPhoto.gui.ui.controllers.edit_modes import AdjustModeState, CropModeState
from src.iPhoto.gui.ui.controllers.header_layout_manager import HeaderLayoutManager
from src.iPhoto.gui.ui.controllers.edit_fullscreen_manager import EditFullscreenManager
from src.iPhoto.gui.ui.controllers.edit_view_transition import EditViewTransitionManager
from src.iPhoto.gui.ui.tasks.edit_sidebar_preview_worker import EditSidebarPreviewResult
from src.iPhoto.gui.ui.controllers.edit_preview_manager import resolve_adjustment_mapping
from src.iPhoto.gui.ui.palette import viewer_surface_color
from src.iPhoto.io import sidecar

if TYPE_CHECKING:
    from src.iPhoto.gui.viewmodels.asset_list_viewmodel import AssetListViewModel
    from src.iPhoto.gui.ui.controllers.window_theme_controller import WindowThemeController

_LOGGER = logging.getLogger(__name__)


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
        asset_vm: AssetListViewModel,
        window: QObject | None = None,
        theme_controller: WindowThemeController | None = None,
        transition_finished_callback: Callable[[str], None] | None = None,
    ):
        super().__init__()
        # We need access to specific UI elements within edit_page (which is likely MainWindow.ui)
        self._ui = edit_page
        self._router = router
        self._bus = event_bus
        self._asset_vm = asset_vm
        self._theme_controller = theme_controller

        self._transition_manager = EditViewTransitionManager(
            self._ui,
            window,
            parent=self,
            theme_controller=theme_controller
        )
        if transition_finished_callback is not None:
            # Callback receives direction strings: "enter" or "exit".
            self._transition_manager.transition_finished.connect(transition_finished_callback)

        # State
        self._session: Optional[EditSession] = None
        self._current_source: Optional[Path] = None
        self._history_manager = EditHistoryManager(parent=self)
        self._pipeline_loader = EditPipelineLoader(self)
        self._is_loading_edit_image = False
        self._skip_next_preview_frame = False
        self._compare_active = False
        self._active_adjustments: dict[str, float] = {}

        # Helpers / Sub-controllers (Ported from EditController)
        self._zoom_handler = EditZoomHandler(
            viewer=self._ui.edit_image_viewer,
            zoom_in_button=self._ui.zoom_in_button,
            zoom_out_button=self._ui.zoom_out_button,
            zoom_slider=self._ui.zoom_slider,
            parent=self,
        )

        self._adjust_mode = AdjustModeState(self._ui, lambda: self._session, parent=self)
        self._crop_mode = CropModeState(self._ui, lambda: self._session, parent=self)
        self._current_mode = self._adjust_mode

        self._header_layout_manager = HeaderLayoutManager(self._ui, parent=self)
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
        self._pending_session_values: Optional[dict] = None
        self._preview_updates_suspended = False
        self._interaction_depth = 0

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
        self._ui.edit_sidebar.perspectiveInteractionStarted.connect(
            self._ui.edit_image_viewer.start_perspective_interaction
        )
        self._ui.edit_sidebar.perspectiveInteractionFinished.connect(
            self._ui.edit_image_viewer.end_perspective_interaction
        )
        self._ui.edit_image_viewer.cropInteractionStarted.connect(self.push_undo_state)
        self._ui.edit_image_viewer.cropChanged.connect(self._handle_crop_changed)

    def is_in_fullscreen(self) -> bool:
        """Return ``True`` if the edit view is in immersive full screen mode."""

        return self._fullscreen_manager.is_in_fullscreen()

    def is_editing(self) -> bool:
        """Return ``True`` when an edit session is active."""

        return self._session is not None

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

        self._current_source = asset_path

        # Load Adjustments
        adjustments = sidecar.load_adjustments(asset_path)

        # Setup Session
        session = EditSession(self)
        session.set_values(adjustments, emit_individual=False)
        session.valuesChanged.connect(self._handle_session_changed)
        self._session = session
        self._history_manager.set_session(session)
        self._ui.edit_sidebar.set_session(session)

        # Apply to Viewer
        self._apply_session_adjustments_to_viewer()

        # Reset Viewer State
        viewer = self._ui.edit_image_viewer
        viewer.setCropMode(False, session.values())
        current_source = viewer.current_image_source()
        self._skip_next_preview_frame = current_source == asset_path
        if not self._skip_next_preview_frame:
            viewer.reset_zoom()

        # UI State
        self._compare_active = False
        self._set_mode("adjust")
        self._header_layout_manager.switch_to_edit_mode()
        self._zoom_handler.connect_controls()

        if self._theme_controller:
            self._theme_controller.apply_edit_theme()

        # Switch View
        self._router.show_edit()
        self._transition_manager.enter_edit_mode(animate=True)

        # Start Loading High-Res Image
        self._start_async_edit_load(asset_path)

    def _start_async_edit_load(self, source: Path):
        if self._session is None: return
        self._is_loading_edit_image = True
        self._ui.edit_image_viewer.set_loading(not self._skip_next_preview_frame)
        self._pipeline_loader.load_image(source)

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

    def leave_edit_mode(self):
        """Returns to detail view."""
        if self._fullscreen_manager.is_in_fullscreen():
            source = self._current_source
            adjustments = None
            if self._session is not None:
                adjustments = self._resolve_session_adjustments()
            self._fullscreen_manager.exit_fullscreen_preview(source, adjustments)
        if self._session is not None:
            self._ui.edit_image_viewer.setCropMode(False, self._session.values())
        self._current_source = None
        self._session = None
        self._preview_manager.stop_session()
        self._zoom_handler.disconnect_controls()
        self._header_layout_manager.restore_detail_mode()

        if self._theme_controller:
            self._theme_controller.restore_global_theme()

        self._ui.edit_image_viewer.set_surface_color_override(
            viewer_surface_color(self._ui.edit_image_viewer)
        )

        self._ui.edit_sidebar.set_session(None)
        self._router.show_detail()
        self._transition_manager.leave_edit_mode(animate=True)

    # --- Actions ---

    def _handle_done_clicked(self):
        if not self._session or not self._current_source:
            self.leave_edit_mode()
            return

        # Save
        source = self._current_source
        self._session.set_values(self._ui.edit_image_viewer.crop_values(), emit_individual=False)
        sidecar.save_adjustments(source, self._session.values())

        # Update thumbnails via ViewModel
        self._asset_vm.invalidate_thumbnail(str(source))

        self.leave_edit_mode()

    def _handle_reset_clicked(self):
        if self._session:
            self.push_undo_state()
            self._session.reset()

    def _handle_rotate_left_clicked(self):
        if self._session:
            self.push_undo_state()
            updates = self._ui.edit_image_viewer.rotate_image_ccw()
            self._session.set_values(updates, emit_individual=False)

    def _handle_compare_pressed(self):
        self._compare_active = True
        self._ui.edit_image_viewer.set_adjustments({})

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
            if not self._preview_updates_suspended:
                self._preview_manager.update_adjustments(current_values)
            self._apply_session_adjustments_to_viewer()
            self._pending_session_values = None

    def _apply_session_adjustments_to_viewer(self):
        if self._session and not self._compare_active:
            adj = self._resolve_session_adjustments()
            self._active_adjustments = adj
            self._ui.edit_image_viewer.set_adjustments(adj)

    def _resolve_session_adjustments(self):
        if not self._session: return {}
        try:
            return self._preview_manager.resolve_adjustments(self._session.values())
        except AttributeError:
            return resolve_adjustment_mapping(self._session.values(), stats=self._preview_manager.color_stats())

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
            adjustments = self._preview_manager.resolve_adjustments(preview_values)
        except Exception:
            _LOGGER.exception("Failed to resolve BW preview adjustments")
            return

        self._ui.edit_image_viewer.set_adjustments(adjustments)

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

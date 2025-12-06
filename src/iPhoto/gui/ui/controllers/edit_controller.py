"""Controller coordinating the edit view and non-destructive adjustments."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Mapping, Optional, TYPE_CHECKING

from PySide6.QtCore import QObject, QThreadPool, QTimer, Signal, QSize, Slot
from PySide6.QtGui import QImage

from ....io import sidecar
from ....core.bw_resolver import BWParams
from ....settings import SettingsManager
from ..models.asset_model import AssetModel
from ..models.edit_session import EditSession
from .edit_history_manager import EditHistoryManager
from .edit_pipeline_loader import EditPipelineLoader
from .thumbnail_cache_service import ThumbnailCacheService
from .edit_zoom_handler import EditZoomHandler
from .edit_modes import AdjustModeState, CropModeState
from .header_layout_manager import HeaderLayoutManager
from ..tasks.thumbnail_loader import ThumbnailLoader
from ..tasks.edit_sidebar_preview_worker import EditSidebarPreviewResult
from ..theme_manager import ThemeManager
from .edit_fullscreen_manager import EditFullscreenManager
from .edit_preview_manager import EditPreviewManager, resolve_adjustment_mapping
from .edit_view_transition import EditViewTransitionManager
from .player_view_controller import PlayerViewController
from .view_controller import ViewController
from .window_theme_controller import WindowThemeController

if TYPE_CHECKING:  # pragma: no cover - import for typing only
    from ..ui_main_window import Ui_MainWindow
    from .detail_ui_controller import DetailUIController
    from .navigation_controller import NavigationController


_LOGGER = logging.getLogger(__name__)


class EditController(QObject):
    """Own the edit session state and synchronise UI widgets."""

    editingStarted = Signal(Path)
    """Emitted after a source asset has been loaded for editing."""

    editingFinished = Signal(Path)
    """Emitted once adjustments are saved and the detail view is restored."""

    def __init__(
        self,
        ui: Ui_MainWindow,
        view_controller: ViewController,
        player_view: PlayerViewController,
        playlist,
        asset_model: AssetModel,
        parent: Optional[QObject] = None,
        *,
        navigation: "NavigationController" | None = None,
        detail_ui_controller: "DetailUIController" | None = None,
        settings: SettingsManager | None = None,
        theme_manager: ThemeManager | None = None,
    ) -> None:
        super().__init__(parent)
        # ``parent`` is the main window hosting the edit UI.  Retaining a weak reference to the
        # window allows the controller to ask the frameless window manager to rebuild menu styles
        # after the palette flips between light and dark variants.
        self._window: QObject | None = parent
        self._ui = ui
        self._view_controller = view_controller
        self._player_view = player_view
        self._playlist = playlist
        self._asset_model = asset_model
        # ``_navigation`` is injected lazily so the controller can coordinate
        # with :class:`NavigationController` without creating an import cycle
        # during startup.  The reference stays optional because unit tests may
        # exercise the edit workflow without bootstrapping the full GUI stack.
        self._navigation: "NavigationController" | None = navigation
        # ``_detail_ui_controller`` provides access to the detail view's zoom wiring helpers so the
        # shared zoom toolbar can swap targets cleanly when the edit tools take over the header.
        self._detail_ui_controller: "DetailUIController" | None = detail_ui_controller
        # ``_settings`` caches the shared settings manager so edit workflows can
        # respect user preferences such as whether the filmstrip should appear
        # after leaving the editor.
        self._settings: SettingsManager | None = settings
        # ``_theme_controller`` manages window chrome styling.
        self._theme_controller: WindowThemeController | None = None
        if theme_manager and parent:
            self._theme_controller = WindowThemeController(ui, parent, theme_manager)

        self._zoom_handler = EditZoomHandler(
            viewer=self._ui.edit_image_viewer,
            zoom_in_button=self._ui.zoom_in_button,
            zoom_out_button=self._ui.zoom_out_button,
            zoom_slider=self._ui.zoom_slider,
            parent=self,
        )
        self._thumbnail_cache_service = ThumbnailCacheService(asset_model, parent=self)

        # State pattern for modes
        self._adjust_mode = AdjustModeState(ui, lambda: self._session, parent=self)
        self._crop_mode = CropModeState(ui, lambda: self._session, parent=self)
        self._current_mode = self._adjust_mode

        self._header_layout_manager = HeaderLayoutManager(ui, parent=self)

        # Track the background image loader so we can avoid queueing multiple
        # decode jobs for the same asset if the user re-enters the edit view
        # quickly.  The worker owns the heavy file I/O, leaving the GUI thread
        # free to animate the transition into the edit chrome.
        self._pipeline_loader = EditPipelineLoader(self)
        self._pipeline_loader.imageLoaded.connect(self._on_edit_image_loaded)
        self._pipeline_loader.imageLoadFailed.connect(self._on_edit_image_load_failed)
        self._pipeline_loader.sidebarPreviewReady.connect(self._handle_sidebar_preview_ready)

        self._is_loading_edit_image = False

        self._preview_manager = EditPreviewManager(self._ui.edit_image_viewer, self)
        self._ui.edit_sidebar.bwParamsPreviewed.connect(self._handle_bw_previewed)
        self._ui.edit_sidebar.bwParamsCommitted.connect(self._handle_bw_committed)
        self._ui.edit_sidebar.perspectiveInteractionStarted.connect(
            self._ui.edit_image_viewer.start_perspective_interaction
        )
        self._ui.edit_sidebar.perspectiveInteractionFinished.connect(
            self._ui.edit_image_viewer.end_perspective_interaction
        )

        self._transition_manager = EditViewTransitionManager(
            self._ui,
            self._window,
            self,
            theme_controller=self._theme_controller,
        )
        self._transition_manager.transition_finished.connect(self._on_transition_finished)
        self._transition_manager.set_detail_ui_controller(self._detail_ui_controller)
        if self._theme_controller:
            self._theme_controller.set_detail_ui_controller(self._detail_ui_controller)

        self._fullscreen_manager = EditFullscreenManager(
            self._ui,
            self._window,
            self._preview_manager,
            self,
        )

        self._session: Optional[EditSession] = None
        self._current_source: Optional[Path] = None
        self._compare_active = False
        # ``_active_adjustments`` caches the resolved shader values representing the
        # most recent session state.  Storing the mapping lets compare mode and
        # the full screen workflow reapply the exact GPU uniforms without
        # recalculating them repeatedly.
        self._active_adjustments: dict[str, float] = {}
        self._skip_next_preview_frame = False
        # ``_edit_viewer_fullscreen_connected`` ensures we only connect the
        # image viewer's full screen exit signal once per controller lifetime.
        self._edit_viewer_fullscreen_connected = False
        ui.edit_reset_button.clicked.connect(self._handle_reset_clicked)
        ui.edit_done_button.clicked.connect(self._handle_done_clicked)
        ui.edit_rotate_left_button.clicked.connect(self._handle_rotate_left_clicked)
        ui.edit_adjust_action.triggered.connect(lambda checked: self._handle_mode_change("adjust", checked))
        ui.edit_crop_action.triggered.connect(lambda checked: self._handle_mode_change("crop", checked))
        ui.edit_compare_button.pressed.connect(self._handle_compare_pressed)
        ui.edit_compare_button.released.connect(self._handle_compare_released)
        ui.edit_mode_control.currentIndexChanged.connect(self._handle_top_bar_index_changed)

        playlist.currentChanged.connect(self._handle_playlist_change)
        playlist.sourceChanged.connect(lambda _path: self._handle_playlist_change())

        ui.edit_header_container.hide()

        self._history_manager = EditHistoryManager(parent=self)

        # Wire up undo triggers from the UI widgets.
        self._ui.edit_sidebar.interactionStarted.connect(self.push_undo_state)
        self._ui.edit_image_viewer.cropInteractionStarted.connect(self.push_undo_state)

    # ------------------------------------------------------------------
    def begin_edit(self) -> None:
        """Enter the edit view for the playlist's current asset."""

        if self._theme_controller:
            self._theme_controller.apply_edit_theme()

        if self._view_controller.is_edit_view_active():
            return
        source = self._playlist.current_source()
        if source is None:
            return
        self._current_source = source

        adjustments = sidecar.load_adjustments(source)

        session = EditSession(self)
        session.set_values(adjustments, emit_individual=False)
        session.valuesChanged.connect(self._handle_session_changed)
        self._session = session
        self._history_manager.set_session(session)
        self._apply_session_adjustments_to_viewer()

        # Detect whether the detail view already uploaded the same image so the
        # edit transition can reuse the existing GPU texture without a
        # redundant re-upload.  When the source matches we keep the user's
        # zoom/pan framing intact.
        viewer = self._ui.edit_image_viewer
        try:
            viewer.cropChanged.disconnect(self._handle_crop_changed)
        except (TypeError, RuntimeError):
            pass
        viewer.cropChanged.connect(self._handle_crop_changed)
        viewer.setCropMode(False, session.values())
        current_source = viewer.current_image_source()
        self._skip_next_preview_frame = current_source == source
        if not self._skip_next_preview_frame:
            viewer.reset_zoom()

        # Clear any stale preview content before attaching the fresh session.  The sidebar reuses
        # its last preview image until it receives an explicit replacement, so resetting it ahead
        # of the new binding avoids showing thumbnails from the previously edited asset while the
        # replacement image is loading in the background.
        self._ui.edit_sidebar.set_light_preview_image(None)
        self._ui.edit_sidebar.set_session(session)
        self._ui.edit_sidebar.refresh()
        if not self._edit_viewer_fullscreen_connected:
            # Route double-click exit requests from the edit viewer through the
            # controller so the dedicated full screen manager can restore the
            # chrome even when immersive mode is triggered outside the
            # frameless window manager.
            self._ui.edit_image_viewer.fullscreenExitRequested.connect(
                self.exit_fullscreen_preview
            )
            self._edit_viewer_fullscreen_connected = True

        self._compare_active = False
        self._set_mode("adjust")

        self._header_layout_manager.switch_to_edit_mode()
        if self._detail_ui_controller is not None:
            self._detail_ui_controller.disconnect_zoom_controls()
        self._zoom_handler.connect_controls()
        self._view_controller.show_edit_view()
        self._transition_manager.enter_edit_mode(animate=True)

        self.editingStarted.emit(source)

        # Start loading the full resolution image on the worker pool immediately so the
        # transition animation can play without waiting for disk I/O or decode work.
        self._start_async_edit_load(source)

    def _start_async_edit_load(self, source: Path) -> None:
        """Kick off the threaded image load for *source*."""

        if self._session is None:
            return

        self._is_loading_edit_image = True
        if self._skip_next_preview_frame:
            # The detail surface already uploaded the texture for this asset.  Suppressing the
            # loading overlay keeps the transition animation visible while the background decode
            # worker prepares its copy of the pixels for the edit preview pipeline.
            self._ui.edit_image_viewer.set_loading(False)
        else:
            self._ui.edit_image_viewer.set_loading(True)

        self._pipeline_loader.load_image(source)

    def _on_edit_image_loaded(self, path: Path, image: QImage) -> None:
        """Handle a successfully decoded edit image."""

        if self._session is None or self._current_source != path:
            return
        if not self._view_controller.is_edit_view_active():
            return

        session = self._session

        if not self._edit_viewer_fullscreen_connected:
            self._ui.edit_image_viewer.fullscreenExitRequested.connect(
                self.exit_fullscreen_preview
            )
            self._edit_viewer_fullscreen_connected = True

        try:
            self._preview_manager.start_session(image, session.values())
        except Exception:
            _LOGGER.exception("Failed to initialise preview session for %s", path)
            self._is_loading_edit_image = False
            self._ui.edit_image_viewer.set_loading(False)
            self.leave_edit_mode(animate=False)
            return

        # Resolve the GPU shader uniforms before painting so both the edit preview and the GL
        # surface share identical Photos-compatible maths.  When the ``image_source`` matches the
        # current frame :meth:`GLImageViewer.set_image` short-circuits the expensive texture upload
        # while still refreshing the uniforms.
        resolved_adjustments = self._resolve_session_adjustments()
        self._active_adjustments = resolved_adjustments
        self._ui.edit_image_viewer.set_image(
            image,
            resolved_adjustments,
            image_source=path,
            reset_view=False,
        )
        self._skip_next_preview_frame = False
        if self._compare_active:
            # Honour an active compare press by restoring the original look immediately.  The fast
            # path above refreshes the edit uniforms, so a second call restores the neutral shader
            # state until the user releases the gesture.
            self._ui.edit_image_viewer.set_adjustments({})

        # The worker has delivered the full-resolution frame, so the loading scrim can disappear
        # immediately.  This mirrors the legacy behaviour where the QWidget-based viewer stopped
        # animating the spinner as soon as decoding finished.
        self._is_loading_edit_image = False
        self._ui.edit_image_viewer.set_loading(False)

        # Hand the decoded frame to the sidebar.  A GPU render produces a neutral, pre-scaled
        # preview that keeps the worker focused on colour statistics.  Should the GPU pass fail the
        # code gracefully falls back to the original full-resolution frame so the legacy CPU path
        # remains functional.
        track_height = max(1, self._ui.edit_sidebar.preview_thumbnail_height())
        target_height = max(track_height * 6, 320)
        source_size = image.size()
        source_height = max(1, source_size.height())
        scaled_width = max(1, int(round(source_size.width() * (target_height / float(source_height)))))
        target_size = QSize(scaled_width, target_height)

        try:
            scaled_preview_image = self._preview_manager.generate_scaled_neutral_preview(target_size)
        except Exception:
            _LOGGER.exception("Failed to generate GPU-scaled neutral preview")
            scaled_preview_image = QImage()

        if scaled_preview_image.isNull():
            _LOGGER.error("GPU scaled preview image was null; falling back to CPU scaling")
            scaled_preview_image = image

        self._pipeline_loader.prepare_sidebar_preview(
            scaled_preview_image,
            target_height=target_height,
            full_res_image_for_fallback=image,
        )

    def _on_edit_image_load_failed(self, path: Path, message: str) -> None:
        """Abort the edit flow when the source image fails to load."""

        del path  # The controller already stores the active source path separately.
        if not self._is_loading_edit_image:
            return
        self._is_loading_edit_image = False
        self._ui.edit_image_viewer.set_loading(False)
        self._skip_next_preview_frame = False
        _LOGGER.error("Failed to load image for editing: %s", message)
        self.leave_edit_mode(animate=False)

    def _handle_sidebar_preview_ready(
        self,
        result: EditSidebarPreviewResult,
    ) -> None:
        """Apply the asynchronously prepared sidebar preview when it matches the active asset."""

        if self._session is None or not self._view_controller.is_edit_view_active():
            return
        self._ui.edit_sidebar.set_light_preview_image(
            result.image,
            color_stats=result.stats,
        )
        self._ui.edit_sidebar.refresh()

    def leave_edit_mode(self, animate: bool = True) -> None:
        """Return to the standard detail view, optionally animating the transition."""

        if self._theme_controller:
            self._theme_controller.restore_global_theme()

        self._preview_manager.cancel_pending_updates()
        if self._is_loading_edit_image:
            self._is_loading_edit_image = False
            self._ui.edit_image_viewer.set_loading(False)
        if self._fullscreen_manager.is_in_fullscreen():
            self.exit_fullscreen_preview()
        if (
            not self._view_controller.is_edit_view_active()
            and not self._transition_manager.is_transition_active()
        ):
            return

        # Ensure the preview surface shows the latest adjusted frame before any widgets start
        # disappearing so the user never sees a partially restored original.
        self._handle_compare_released()
        self._ui.edit_image_viewer.setCropMode(False)

        self._zoom_handler.disconnect_controls()
        if self._detail_ui_controller is not None:
            self._detail_ui_controller.connect_zoom_controls()
        self._header_layout_manager.restore_detail_mode()
        if self._edit_viewer_fullscreen_connected:
            try:
                self._ui.edit_image_viewer.fullscreenExitRequested.disconnect(
                    self.exit_fullscreen_preview
                )
            except (TypeError, RuntimeError):
                pass
            self._edit_viewer_fullscreen_connected = False
        self._view_controller.show_detail_view()

        show_filmstrip = True
        if self._settings is not None:
            stored_preference = self._settings.get("ui.show_filmstrip", True)
            if isinstance(stored_preference, bool):
                show_filmstrip = stored_preference
            elif isinstance(stored_preference, str):
                show_filmstrip = stored_preference.strip().lower() in {
                    "1",
                    "true",
                    "yes",
                    "on",
                }
            else:
                show_filmstrip = bool(stored_preference)

        self._transition_manager.leave_edit_mode(
            animate=animate,
            show_filmstrip=show_filmstrip,
        )
        self._preview_manager.stop_session()
        self._skip_next_preview_frame = False

    # ------------------------------------------------------------------
    def _handle_session_changed(self, values: dict) -> None:
        del values  # The session retains the authoritative mapping internally.
        if self._session is None:
            return
        self._preview_manager.update_adjustments(self._session.values())
        self._apply_session_adjustments_to_viewer()

    def push_undo_state(self) -> None:
        """Capture the current session state onto the undo stack."""
        self._history_manager.push_undo_state()

    def undo(self) -> None:
        """Revert the last edit action."""
        self._history_manager.undo()

    def redo(self) -> None:
        """Restore the previously undone edit action."""
        self._history_manager.redo()

    def _handle_crop_changed(self, cx: float, cy: float, width: float, height: float) -> None:
        if self._session is None:
            return
        updates = {
            "Crop_CX": float(cx),
            "Crop_CY": float(cy),
            "Crop_W": float(width),
            "Crop_H": float(height),
        }
        self._session.set_values(updates, emit_individual=False)

    def _handle_rotate_left_clicked(self) -> None:
        if self._session is None:
            return
        self.push_undo_state()
        updates = self._ui.edit_image_viewer.rotate_image_ccw()
        # Persist the viewer-driven rotation so the backing session stays aligned with the
        # logical coordinate system used by the OpenGL paint path.  Persisting the mapping
        # in a single call avoids per-field signals and keeps the crop overlay in sync with
        # the updated orientation.
        self._session.set_values(updates, emit_individual=False)

    def _apply_session_adjustments_to_viewer(self) -> None:
        """Forward the latest session values to the GL viewer."""

        if self._session is None:
            return
        adjustments = self._resolve_session_adjustments()
        self._active_adjustments = adjustments
        # Avoid repainting while the compare button is held so the user continues
        # to see the unadjusted frame until the interaction ends.
        if not self._compare_active:
            self._ui.edit_image_viewer.set_adjustments(adjustments)

    @Slot(BWParams)
    def _handle_bw_previewed(self, params: BWParams) -> None:
        """Apply *params* to the viewer without mutating the session."""

        if self._session is None or self._compare_active:
            return
        if not bool(self._session.value("BW_Enabled")):
            return
        base = dict(self._active_adjustments or self._resolve_session_adjustments())
        base["BWIntensity"] = params.intensity
        base["BWNeutrals"] = params.neutrals
        base["BWTone"] = params.tone
        base["BWGrain"] = params.grain
        self._ui.edit_image_viewer.set_adjustments(base)

    @Slot(BWParams)
    def _handle_bw_committed(self, params: BWParams) -> None:
        """Persist *params* into the session and re-enable the effect if needed."""

        if self._session is None:
            return
        clamped = params.clamp()
        updates = {
            "BW_Master": clamped.master,
            "BW_Intensity": clamped.intensity,
            "BW_Neutrals": clamped.neutrals,
            "BW_Tone": clamped.tone,
            "BW_Grain": clamped.grain,
        }
        self._session.set_values(updates)
        if not bool(self._session.value("BW_Enabled")):
            self._session.set_value("BW_Enabled", True)

    def _resolve_session_adjustments(self) -> dict[str, float]:
        """Return the current session adjustments resolved for the GL shader."""

        if self._session is None:
            return {}

        values = self._session.values()

        # Prefer the preview manager helper so both the CPU thumbnails and the GPU shader share
        # identical Photos-compatible math.  Fall back to the module-level resolver if the preview
        # pipeline has not been initialised yet (for example, during early unit tests).
        try:
            resolved = self._preview_manager.resolve_adjustments(values)
        except AttributeError:
            resolved = resolve_adjustment_mapping(values, stats=self._preview_manager.color_stats())
        else:
            # ``resolve_adjustments`` already honours colour statistics when available.  Guard the
            # return value here so callers always receive a defensive copy and accidental mutation
            # cannot leak back into the preview manager's caches.
            resolved = dict(resolved)

        return resolved

    def _restore_active_adjustments(self) -> None:
        """Reapply the cached adjustments to the GL viewer."""

        if self._compare_active:
            return
        if self._active_adjustments:
            self._ui.edit_image_viewer.set_adjustments(self._active_adjustments)
        else:
            self._ui.edit_image_viewer.set_adjustments({})

    def _on_transition_finished(self, direction: str) -> None:
        """Clean up controller state after the transition manager completes."""

        if direction == "exit":
            self._ui.edit_header_container.hide()
            self._ui.edit_sidebar.set_session(None)
            self._ui.edit_sidebar.set_light_preview_image(None)
            # Do not call ``clear`` on the shared GL viewer here.  The retained texture lets the
            # detail surface display the adjusted frame instantly when the edit chrome slides away.
            self._session = None
            self._current_source = None
            self._compare_active = False
            self._active_adjustments = {}
            self._skip_next_preview_frame = False
            self._history_manager.clear()

    # ------------------------------------------------------------------
    # Dedicated edit full screen workflow
    # ------------------------------------------------------------------
    def is_in_fullscreen(self) -> bool:
        """Expose the immersive full screen state managed externally."""

        return self._fullscreen_manager.is_in_fullscreen()

    def enter_fullscreen_preview(self) -> None:
        """Expand the edit viewer into a chrome-free full screen mode."""

        if not self._view_controller.is_edit_view_active():
            return
        if self._current_source is None or self._session is None:
            return

        adjustments = self._active_adjustments or self._resolve_session_adjustments()
        if self._fullscreen_manager.enter_fullscreen_preview(
            self._current_source,
            adjustments,
        ):
            self._compare_active = False

    def exit_fullscreen_preview(self) -> None:
        """Restore the standard edit chrome after leaving full screen."""

        adjustments: Optional[dict[str, float]] = None
        if self._session is not None:
            adjustments = self._active_adjustments or self._resolve_session_adjustments()

        if self._fullscreen_manager.exit_fullscreen_preview(
            self._current_source,
            adjustments,
        ):
            self._compare_active = False

    def _handle_compare_pressed(self) -> None:
        """Display the original photo while the compare button is held."""

        self._compare_active = True
        self._ui.edit_image_viewer.set_adjustments({})

    def _handle_compare_released(self) -> None:
        """Restore the adjusted preview after a comparison glance."""

        self._compare_active = False
        self._restore_active_adjustments()

    def _handle_reset_clicked(self) -> None:
        if self._session is None:
            return
        self.push_undo_state()
        # Stop any pending preview updates so the reset renders immediately.
        self._preview_manager.cancel_pending_updates()
        self._session.reset()

    def _handle_done_clicked(self) -> None:
        # Ensure no delayed preview runs after committing the adjustments.
        self._preview_manager.stop_session()
        if self._session is None or self._current_source is None:
            self.leave_edit_mode(animate=True)
            return
        # Store the source path locally before ``leave_edit_mode`` clears the
        # controller state.  The detail player needs the same asset path to
        # reload the freshly saved adjustments once the edit chrome is hidden.
        source = self._current_source
        # Capture the rendered preview so the detail view can reuse it while
        # the background worker recalculates the full-resolution frame.  A copy
        # keeps the pixmap alive after the edit widgets tear down their state.
        preview_pixmap = self._ui.edit_image_viewer.pixmap()
        self._session.set_values(
            self._ui.edit_image_viewer.crop_values(),
            emit_individual=False,
        )
        adjustments = self._session.values()
        if self._navigation is not None:
            # Saving adjustments writes sidecar files, which triggers the
            # filesystem watcher to rebuild the sidebar tree.  That rebuild
            # reselects the active collection ("All Photos", etc.) and would
            # otherwise emit navigation signals that yank the UI back to the
            # gallery.  Arm the suppression guard *before* touching the disk so
            # those callbacks are ignored until the detail surface finishes
            # updating.
            self._navigation.suppress_tree_refresh_for_edit()
        sidecar.save_adjustments(source, adjustments)
        # Defer cache invalidation until after the detail surface has been
        # restored so the gallery does not briefly become the active view while
        # its thumbnails refresh in the background.
        self._thumbnail_cache_service.schedule_refresh(source)
        self.leave_edit_mode(animate=True)
        # ``display_image`` schedules an asynchronous reload; logging the
        # boolean result would not improve the UX, so simply trigger it and
        # fall back to the playlist selection handlers if scheduling fails.
        self._player_view.display_image(source, placeholder=preview_pixmap)
        self.editingFinished.emit(source)

    def _handle_mode_change(self, mode: str, checked: bool) -> None:
        if not checked:
            return
        self._set_mode(mode)

    def _handle_top_bar_index_changed(self, index: int) -> None:
        """Synchronise action state when the segmented bar changes selection."""
        mode = "adjust" if index == 0 else "crop"
        self._set_mode(mode)

    def _set_mode(self, mode: str) -> None:
        if mode == "adjust":
            new_state = self._adjust_mode
        else:
            new_state = self._crop_mode

        if self._current_mode == new_state:
            return

        if self._current_mode:
            self._current_mode.exit()

        self._current_mode = new_state
        self._current_mode.enter()

    def _handle_playlist_change(self) -> None:
        if self._view_controller.is_edit_view_active():
            self.leave_edit_mode()
    def set_navigation_controller(self, navigation: "NavigationController") -> None:
        """Attach the navigation controller after construction.

        The main window builds the view controllers before wiring the
        navigation stack.  Providing a setter keeps the constructor flexible
        while still allowing the edit workflow to coordinate suppression of
        sidebar-driven navigation callbacks when adjustments are saved.
        """

        self._navigation = navigation

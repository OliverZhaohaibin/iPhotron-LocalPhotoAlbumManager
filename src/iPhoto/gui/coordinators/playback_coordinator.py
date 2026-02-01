"""Coordinator for media playback and detail view interactions."""

from __future__ import annotations

from pathlib import Path
import logging
from typing import TYPE_CHECKING, Any, Optional

from PySide6.QtCore import QObject, Slot, QTimer, Signal, QModelIndex, QItemSelectionModel
from PySide6.QtGui import QAction, QColor, QPalette

from src.iPhoto.gui.coordinators.view_router import ViewRouter
from src.iPhoto.gui.ui.icons import load_icon
from src.iPhoto.gui.ui.controllers.header_controller import HeaderController
from src.iPhoto.gui.ui.models.roles import Roles
from src.iPhoto.gui.ui.widgets.info_panel import InfoPanel
from src.iPhoto.io.metadata import read_image_meta
from src.iPhoto.io import sidecar

if TYPE_CHECKING:
    from src.iPhoto.gui.ui.widgets.player_bar import PlayerBar
    from src.iPhoto.gui.ui.controllers.player_view_controller import PlayerViewController
    from src.iPhoto.gui.viewmodels.asset_list_viewmodel import AssetListViewModel
    from src.iPhoto.gui.coordinators.navigation_coordinator import NavigationCoordinator
    from src.iPhoto.gui.ui.widgets.filmstrip_view import FilmstripView
    from src.iPhoto.utils.settings import Settings
    from PySide6.QtWidgets import QPushButton, QSlider, QToolButton, QWidget

LOGGER = logging.getLogger(__name__)
_FILMSTRIP_SYNC_MAX_RETRIES = 4
_FILMSTRIP_SYNC_RETRY_DELAY_MS = 50


class PlaybackCoordinator(QObject):
    """
    Manages playback state, the PlayerBar, and interactions within the Detail View.
    Replaces PlaybackController and DetailUiController.
    """

    assetChanged = Signal(int)

    def __init__(
        self,
        player_bar: PlayerBar,
        player_view: PlayerViewController,
        router: ViewRouter,
        asset_vm: AssetListViewModel,
        # Zoom Controls
        zoom_slider: QSlider,
        zoom_in_button: QToolButton,
        zoom_out_button: QToolButton,
        zoom_widget: QWidget,
        # Action Buttons
        favorite_button: QToolButton,
        info_button: QToolButton,
        rotate_button: QToolButton,
        edit_button: QPushButton,
        share_button: QToolButton,
        # Filmstrip
        filmstrip_view: FilmstripView,
        toggle_filmstrip_action: QAction,
        settings: Settings,
        header_controller: Optional[HeaderController] = None,
    ):
        super().__init__()
        self._player_bar = player_bar
        self._player_view = player_view
        self._router = router
        self._asset_vm = asset_vm

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

        self._is_playing = False
        self._current_row = -1
        self._navigation: Optional[NavigationCoordinator] = None
        self._info_panel: Optional[InfoPanel] = None
        self._active_live_motion: Optional[Path] = None
        self._active_live_still: Optional[Path] = None
        self._resume_after_transition = False

        self._filmstrip_scroll_sync_pending = False
        self._filmstrip_model = None
        self._filmstrip_sync_attempts = 0
        self._connect_signals()
        self._connect_zoom_controls()
        self._restore_filmstrip_preference()

    def set_navigation_coordinator(self, nav: NavigationCoordinator):
        self._navigation = nav

    def set_info_panel(self, panel: InfoPanel):
        self._info_panel = panel

    def current_row(self) -> int:
        """Expose current row index for external controllers (e.g. Share)."""
        return self._current_row

    def suspend_playback_for_transition(self) -> bool:
        """Pause active video/live playback before a window transition."""

        resume_after = self._is_playing
        self._resume_after_transition = resume_after
        if resume_after:
            self._player_view.video_area.pause()
        return resume_after

    def resume_playback_after_transition(self) -> None:
        """Resume playback if it was active before the transition."""

        if not self._resume_after_transition:
            return
        self._resume_after_transition = False
        self._player_view.video_area.play()

    def prepare_fullscreen_asset(self) -> bool:
        """Ensure the current asset is displayed before entering immersive mode."""

        if self._current_row < 0:
            return False
        if not self._router.is_detail_view_active():
            self.play_asset(self._current_row)
        return True

    def show_placeholder_in_viewer(self) -> None:
        """Display the placeholder surface in the detail viewer."""

        self._player_view.show_placeholder()

    def _connect_signals(self):
        # Player Bar -> Coordinator
        self._player_bar.playPauseRequested.connect(self.toggle_playback)
        self._player_bar.scrubStarted.connect(self._on_scrub_start)
        self._player_bar.scrubFinished.connect(self._on_scrub_end)
        self._player_bar.seekRequested.connect(self._on_seek)

        # Player View -> Coordinator
        self._player_view.liveReplayRequested.connect(self.replay_live_photo)
        self._player_view.video_area.playbackStateChanged.connect(self._sync_playback_state)
        self._player_view.video_area.playbackFinished.connect(self._handle_playback_finished)

        # Model -> Coordinator
        self._asset_vm.dataChanged.connect(self._on_data_changed)

        # Filmstrip -> Coordinator
        self._filmstrip_view.nextItemRequested.connect(self.select_next)
        self._filmstrip_view.prevItemRequested.connect(self.select_previous)
        self._filmstrip_view.itemClicked.connect(self._on_filmstrip_clicked)
        self._toggle_filmstrip_action.toggled.connect(self._handle_filmstrip_toggled)
        self._attach_filmstrip_model_signals()

    def _connect_zoom_controls(self):
        viewer = self._player_view.image_viewer
        self._zoom_in.clicked.connect(viewer.zoom_in)
        self._zoom_out.clicked.connect(viewer.zoom_out)
        self._zoom_slider.valueChanged.connect(self._handle_zoom_slider_changed)
        viewer.zoomChanged.connect(self._handle_viewer_zoom_changed)

    def _restore_filmstrip_preference(self):
        """Restore filmstrip visibility from settings."""
        stored = self._settings.get("ui.show_filmstrip", True)
        if isinstance(stored, str):
            show = stored.strip().lower() in {"1", "true", "yes", "on"}
        else:
            show = bool(stored)

        self._filmstrip_view.setVisible(show)
        self._toggle_filmstrip_action.setChecked(show)
        if show:
            self._schedule_filmstrip_sync()

    @Slot(bool)
    def _handle_filmstrip_toggled(self, checked: bool):
        self._filmstrip_view.setVisible(checked)
        self._settings.set("ui.show_filmstrip", checked)
        if checked:
            self._schedule_filmstrip_sync()

    @Slot(QModelIndex)
    def _on_filmstrip_clicked(self, index: QModelIndex):
        # Handle Proxy Model (if present)
        model = self._filmstrip_view.model()
        if hasattr(model, "mapToSource"):
            source_idx = model.mapToSource(index)
            if source_idx.isValid():
                self.play_asset(source_idx.row())
        else:
            self.play_asset(index.row())

    @Slot(int)
    def _handle_zoom_slider_changed(self, value: int):
        target = float(max(1, value)) / 100.0
        self._player_view.image_viewer.set_zoom(target)

    @Slot(float)
    def _handle_viewer_zoom_changed(self, factor: float):
        val = int(round(factor * 100))
        self._zoom_slider.blockSignals(True)
        self._zoom_slider.setValue(val)
        self._zoom_slider.blockSignals(False)

    @Slot()
    def toggle_playback(self):
        # Delegate state logic to the player (VideoArea)
        # We assume if it's currently playing, we pause, else we play.
        # But relying on _is_playing is better for toggle logic.
        if self._is_playing:
            self._player_view.video_area.pause()
        else:
            self._player_view.video_area.play()

    @Slot(bool)
    def _sync_playback_state(self, is_playing: bool):
        self._is_playing = is_playing
        # PlayerBar is updated by VideoArea directly, but we keep coordinator state in sync.

    @Slot()
    def _on_scrub_start(self):
        self._player_view.video_area.pause()

    @Slot()
    def _on_scrub_end(self):
        if self._is_playing:
            self._player_view.video_area.play()

    @Slot(int)
    def _on_seek(self, position: int):
        self._player_view.video_area.seek(position)

    def play_asset(self, row: int):
        """Switch to detail view and play/show the asset at the given row."""
        # Validate row
        if row < 0 or row >= self._asset_vm.rowCount():
            return

        self._router.show_detail()
        self._current_row = row

        # Notify selection change
        self.assetChanged.emit(row)

        # Sync ViewModel State (for Delegate sizing)
        self._asset_vm.set_current_row(row)
        self._update_header(row)

        # Sync Filmstrip
        self._sync_filmstrip_selection(row)

        idx = self._asset_vm.index(row, 0)
        abs_path = self._asset_vm.data(idx, Roles.ABS)
        is_video = self._asset_vm.data(idx, Roles.IS_VIDEO)
        is_live = self._asset_vm.data(idx, Roles.IS_LIVE)
        is_fav = self._asset_vm.data(idx, Roles.FEATURED)

        if not abs_path:
            return

        source = Path(abs_path)
        self._active_live_motion = None
        self._active_live_still = None

        # Enable detail page actions
        self._favorite_button.setEnabled(True)
        self._info_button.setEnabled(True)
        self._share_button.setEnabled(True)
        self._edit_button.setEnabled(True)
        self._rotate_button.setEnabled(not is_video)  # Simple logic for now

        self._update_favorite_icon(bool(is_fav))

        # Load Media
        if is_video:
            self._player_view.show_video_surface(interactive=True)
            self._player_view.video_area.load_video(source)
            self._player_view.video_area.play()
            self._player_bar.setEnabled(True)
            self._zoom_widget.hide()
        else:
            # Image or Live Photo
            self._player_view.show_image_surface()
            self._player_view.display_image(source)
            self._player_bar.setEnabled(False)
            self._zoom_widget.show()

            if is_live:
                self._player_view.show_live_badge()
                self._player_view.set_live_replay_enabled(True)
                self._autoplay_live_motion(row, source)
            else:
                self._player_view.hide_live_badge()
                self._player_view.set_live_replay_enabled(False)

        self._is_playing = False
        self._player_bar.set_playback_state(False)
        self._player_bar.set_position(0)

        # Update Info Panel if visible
        if self._info_panel and self._info_panel.isVisible():
            self._refresh_info_panel(row)

    def _autoplay_live_motion(self, row: int, still_source: Path) -> None:
        idx = self._asset_vm.index(row, 0)
        motion_abs = self._asset_vm.data(idx, Roles.LIVE_MOTION_ABS)
        if not motion_abs:
            motion_rel = self._asset_vm.data(idx, Roles.LIVE_MOTION_REL)
            if motion_rel and Path(str(motion_rel)).is_absolute():
                motion_abs = motion_rel
        if not motion_abs:
            return
        motion_path = Path(str(motion_abs))
        self._active_live_motion = motion_path
        self._active_live_still = still_source
        self._player_view.defer_still_updates(True)
        self._player_view.show_video_surface(interactive=False)
        self._player_view.video_area.load_video(motion_path)
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

    def _sync_filmstrip_selection(self, row: int) -> bool:
        """Update filmstrip selection and scroll to the item.

        Returns ``True`` when the filmstrip was able to apply the selection.
        """
        idx = self._asset_vm.index(row, 0)
        if not idx.isValid():
            return False

        # Handle Proxy Model (if present)
        model = self._filmstrip_view.model()
        if hasattr(model, "mapFromSource"):
            idx = model.mapFromSource(idx)

        if not idx.isValid():
            return False

        selection_model = self._filmstrip_view.selectionModel()
        if selection_model is None:
            return False
        if selection_model.currentIndex() != idx:
            selection_model.setCurrentIndex(idx, QItemSelectionModel.ClearAndSelect)
        self._filmstrip_view.center_on_index(idx)
        return True

    def _schedule_filmstrip_sync(self) -> None:
        if not self._can_sync_filmstrip():
            return
        self._filmstrip_sync_attempts = 0
        self._filmstrip_scroll_sync_pending = True
        QTimer.singleShot(0, self._apply_filmstrip_sync)

    def schedule_filmstrip_sync(self) -> None:
        """Resync filmstrip selection after layout/transition changes.

        Uses the retry mechanism to wait for the filmstrip layout to stabilize,
        which is useful after edit transitions or model resets.
        """
        self._schedule_filmstrip_sync()

    def _apply_filmstrip_sync(self) -> None:
        if self._current_row < 0:
            self._filmstrip_scroll_sync_pending = False
            return
        if self._sync_filmstrip_selection(self._current_row):
            self._filmstrip_scroll_sync_pending = False
            self._filmstrip_sync_attempts = 0
            return
        if self._filmstrip_sync_attempts < _FILMSTRIP_SYNC_MAX_RETRIES:
            self._filmstrip_sync_attempts += 1
            QTimer.singleShot(_FILMSTRIP_SYNC_RETRY_DELAY_MS, self._apply_filmstrip_sync)
            return
        self._filmstrip_scroll_sync_pending = False
        self._filmstrip_sync_attempts = 0

    def _attach_filmstrip_model_signals(self) -> None:
        model = self._filmstrip_view.model()
        if model is None or model is self._filmstrip_model:
            return
        if self._filmstrip_model is not None:
            try:
                self._filmstrip_model.modelReset.disconnect(self._schedule_filmstrip_sync)
                self._filmstrip_model.rowsInserted.disconnect(self._schedule_filmstrip_sync)
                self._filmstrip_model.rowsRemoved.disconnect(self._schedule_filmstrip_sync)
                self._filmstrip_model.layoutChanged.disconnect(self._schedule_filmstrip_sync)
            except (RuntimeError, TypeError) as exc:
                LOGGER.debug("Filmstrip model disconnect skipped: %s", exc)
        self._filmstrip_model = model
        model.modelReset.connect(self._schedule_filmstrip_sync)
        model.rowsInserted.connect(self._schedule_filmstrip_sync)
        model.rowsRemoved.connect(self._schedule_filmstrip_sync)
        model.layoutChanged.connect(self._schedule_filmstrip_sync)

    def _can_sync_filmstrip(self) -> bool:
        if self._filmstrip_scroll_sync_pending:
            return False
        return self._current_row >= 0

    def _update_favorite_icon(self, is_favorite: bool):
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

    def _on_data_changed(self, top, bottom, roles):
        if self._current_row < 0:
            return

        # Check if the current asset is affected
        # Since we use row index, we just check if current_row is within top/bottom
        # Note: Qt models can emit ranges.

        # Check if current row is in the range
        if top.row() <= self._current_row <= bottom.row():
            if not roles or Roles.FEATURED in roles:
                idx = self._asset_vm.index(self._current_row, 0)
                is_fav = self._asset_vm.data(idx, Roles.FEATURED)
                self._update_favorite_icon(bool(is_fav))
            if not roles or Roles.DT in roles or Roles.LOCATION in roles:
                self._update_header(self._current_row)

    def reset_for_gallery(self):
        self._player_view.video_area.stop()
        self._player_view.show_placeholder()
        self._player_bar.setEnabled(False)
        self._is_playing = False
        self._update_header(None)
        if self._info_panel:
            self._info_panel.close()

    def shutdown(self):
        """Stop any active media playback and release resources."""
        self._player_view.video_area.stop()
        self._is_playing = False
        self._update_header(None)
        if self._info_panel:
            self._info_panel.close()

    def _update_header(self, row: Optional[int]) -> None:
        if not self._header_controller:
            return
        if row is None or row < 0:
            self._header_controller.clear()
            return
        self._header_controller.update_for_row(row, self._asset_vm)

    def select_next(self):
        """Move to the next asset."""
        next_row = self._current_row + 1
        if next_row < self._asset_vm.rowCount():
            self.play_asset(next_row)

    def select_previous(self):
        """Move to the previous asset."""
        prev_row = self._current_row - 1
        if prev_row >= 0:
            self.play_asset(prev_row)

    def replay_live_photo(self):
        if self._current_row < 0:
            return
        idx = self._asset_vm.index(self._current_row, 0)
        abs_path = self._asset_vm.data(idx, Roles.ABS)
        is_live = self._asset_vm.data(idx, Roles.IS_LIVE)
        if not abs_path or not is_live:
            return
        self._autoplay_live_motion(self._current_row, Path(abs_path))

    def rotate_current_asset(self):
        if self._current_row < 0: return

        idx = self._asset_vm.index(self._current_row, 0)
        abs_path = self._asset_vm.data(idx, Roles.ABS)

        if not abs_path: return

        source = Path(abs_path)

        # 1. Update UI immediately (Optimistic)
        updates = self._player_view.image_viewer.rotate_image_ccw()

        # 2. Persist adjustments
        try:
            current_adjustments = sidecar.load_adjustments(source)
            current_adjustments.update(updates)
            sidecar.save_adjustments(source, current_adjustments)

            # 3. Invalidate thumbnails
            self._asset_vm.invalidate_thumbnail(str(source))

        except Exception as e:
            LOGGER.error(f"Failed to rotate: {e}")

    # --- Detail UI Logic Ported ---

    def _refresh_info_panel(self, row: int):
        if not self._info_panel: return

        idx = self._asset_vm.index(row, 0)
        info = self._asset_vm.data(idx, Roles.INFO)

        if info and not info.get("iso"):
             abs_path = info.get("abs")
             if abs_path:
                 try:
                     fresh = read_image_meta(Path(abs_path))
                     info.update(fresh)
                 except Exception as e:
                     LOGGER.debug(f"Failed enrichment: {e}")

        self._info_panel.set_asset_metadata(info)

    def toggle_info_panel(self):
        if not self._info_panel: return
        if self._info_panel.isVisible():
            self._info_panel.close()
        else:
            if self._current_row >= 0:
                self._refresh_info_panel(self._current_row)
                self._info_panel.show()

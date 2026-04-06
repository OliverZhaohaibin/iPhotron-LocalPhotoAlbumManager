"""Coordinator for media playback and detail view interactions."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QItemSelectionModel, QModelIndex, QObject, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QColor, QPalette

from iPhoto.config import PLAY_ASSET_DEBOUNCE_MS
from iPhoto.errors import ExternalToolError
from iPhoto.gui.coordinators.view_router import ViewRouter
from iPhoto.gui.ui.controllers.edit_zoom_handler import EditZoomHandler
from iPhoto.gui.ui.controllers.header_controller import HeaderController
from iPhoto.gui.ui.icons import load_icon
from iPhoto.gui.ui.models.roles import Roles
from iPhoto.gui.ui.widgets.info_panel import InfoPanel
from iPhoto.io import sidecar
from iPhoto.io.metadata import read_image_meta, read_video_meta
from iPhoto.utils.exiftool import get_metadata_batch

if TYPE_CHECKING:
    from iPhoto.utils.settings import Settings
    from PySide6.QtWidgets import QPushButton, QSlider, QToolButton, QWidget

    from iPhoto.gui.coordinators.navigation_coordinator import NavigationCoordinator
    from iPhoto.gui.ui.controllers.player_view_controller import PlayerViewController
    from iPhoto.gui.ui.widgets.filmstrip_view import FilmstripView
    from iPhoto.gui.ui.widgets.player_bar import PlayerBar
    from iPhoto.gui.viewmodels.asset_list_viewmodel import AssetListViewModel

LOGGER = logging.getLogger(__name__)


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
        header_controller: HeaderController | None = None,
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
        self._navigation: NavigationCoordinator | None = None
        self._info_panel: InfoPanel | None = None
        self._active_live_motion: Path | None = None
        self._active_live_still: Path | None = None
        self._resume_after_transition = False

        # Trim range of the currently-loaded video (in ms).  Used to remap the
        # player bar so that its slider spans [0, trim_duration] instead of the
        # video's full duration, making every part of the bar accessible.
        self._trim_in_ms: int = 0
        self._trim_out_ms: int = 0

        # Debounce rapid play_asset() calls (e.g. holding an arrow key) so that
        # only the *last* requested row is actually loaded.  This prevents the
        # player from being overwhelmed with overlapping load/play cycles that
        # can lead to freezes or corrupted state.
        self._pending_play_row: int | None = None
        self._play_debounce = QTimer(self)
        self._play_debounce.setSingleShot(True)
        self._play_debounce.setInterval(PLAY_ASSET_DEBOUNCE_MS)
        self._play_debounce.timeout.connect(self._execute_pending_play)

        self._connect_signals()
        self._setup_zoom_handler()
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

        if self._asset_vm.rowCount() <= 0:
            return False
        target_row = self._current_row if self._current_row >= 0 else 0
        if self._current_row < 0 or not self._router.is_detail_view_active():
            self.play_asset(target_row)
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
        # Re-map the player bar to show trim-relative duration/position so the
        # entire bar is accessible and reflects only the playable range.
        self._player_view.video_area.durationChanged.connect(self._on_video_duration_changed)
        self._player_view.video_area.positionChanged.connect(self._on_video_position_changed)

        # Model -> Coordinator
        self._asset_vm.dataChanged.connect(self._on_data_changed)

        # Filmstrip -> Coordinator
        self._filmstrip_view.nextItemRequested.connect(self.select_next)
        self._filmstrip_view.prevItemRequested.connect(self.select_previous)
        self._filmstrip_view.itemClicked.connect(self._on_filmstrip_clicked)
        self._toggle_filmstrip_action.toggled.connect(self._handle_filmstrip_toggled)

    def _setup_zoom_handler(self):
        self._zoom_handler = EditZoomHandler(
            viewer=self._player_view.image_viewer,
            zoom_in_button=self._zoom_in,
            zoom_out_button=self._zoom_out,
            zoom_slider=self._zoom_slider,
            parent=self,
        )
        self._zoom_handler.connect_controls()

    def _restore_filmstrip_preference(self):
        """Restore filmstrip visibility from settings."""
        stored = self._settings.get("ui.show_filmstrip", True)
        if isinstance(stored, str):
            show = stored.strip().lower() in {"1", "true", "yes", "on"}
        else:
            show = bool(stored)

        self._filmstrip_view.setVisible(show)
        self._toggle_filmstrip_action.setChecked(show)

    @Slot(bool)
    def _handle_filmstrip_toggled(self, checked: bool):
        self._filmstrip_view.setVisible(checked)
        self._settings.set("ui.show_filmstrip", checked)

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
        self._player_view.video_area.seek(position + self._trim_in_ms)

    @Slot(int)
    def _on_video_duration_changed(self, duration_ms: int) -> None:
        """Re-map the player bar to show the trimmed playable duration."""
        # Skip remapping while the video area is in edit mode: EditCoordinator
        # owns the video area at that point and manages its own trim bar.
        # We avoid touching the detail-view player bar (PlaybackCoordinator's
        # _player_bar) until edit mode exits so it stays at the last known
        # valid state and doesn't flicker with intermediate edit-mode durations.
        if self._player_view.video_area.is_edit_mode_active():
            return
        # Sync trim state from VideoArea so that trim changes applied by
        # EditCoordinator._restore_detail_video_preview (which bypasses the
        # normal play_asset path) are reflected in the duration display.
        trim_in_ms, trim_out_ms = self._player_view.video_area.trim_range_ms()
        self._trim_in_ms = trim_in_ms
        self._trim_out_ms = trim_out_ms
        if self._trim_out_ms > self._trim_in_ms:
            self._player_bar.set_duration(self._trim_out_ms - self._trim_in_ms)
        else:
            # No trim is active (or range is degenerate) — show the full clip duration.
            self._player_bar.set_duration(duration_ms)

    @Slot(int)
    def _on_video_position_changed(self, position_ms: int) -> None:
        """Re-map the player bar position to be relative to the trim in-point."""
        # Skip remapping during edit mode for the same reason as above.
        if self._player_view.video_area.is_edit_mode_active():
            return
        self._player_bar.set_position(max(0, position_ms - self._trim_in_ms))

    def play_asset(self, row: int):
        """Switch to detail view and play/show the asset at the given row.

        Rapid calls (e.g. holding an arrow key) are coalesced via a short
        debounce timer so that only the *last* requested row triggers the
        expensive load/play cycle.
        """
        # Validate row
        if row < 0 or row >= self._asset_vm.rowCount():
            return

        # Show detail view and update lightweight UI immediately so the user
        # sees a responsive reaction even while the debounce timer is running.
        self._router.show_detail()
        self._current_row = row
        self.assetChanged.emit(row)
        self._asset_vm.set_current_row(row)
        self._update_header(row)
        self._sync_filmstrip_selection(row)

        # Stop any active video / live-motion playback so the decoder releases
        # its resources before the next asset attempts to use the same sink.
        if self._active_live_motion:
            self._active_live_motion = None
            self._active_live_still = None
            self._player_view.defer_still_updates(False)
        self._player_view.video_area.stop()

        # Record the target row and (re-)start the debounce timer.
        self._pending_play_row = row
        self._play_debounce.start()

    # ------------------------------------------------------------------
    def _execute_pending_play(self) -> None:
        """Execute the most recently requested play_asset row."""
        row = self._pending_play_row
        self._pending_play_row = None
        if row is None:
            return
        if row < 0 or row >= self._asset_vm.rowCount():
            return
        self._do_play_asset(row)

    # ------------------------------------------------------------------
    def _do_play_asset(self, row: int) -> None:
        """Internal implementation of *play_asset* — called after debounce."""
        # Validate row (may have become stale after the debounce delay)
        if row < 0 or row >= self._asset_vm.rowCount():
            return

        idx = self._asset_vm.index(row, 0)
        abs_path = self._asset_vm.data(idx, Roles.ABS)
        is_video = self._asset_vm.data(idx, Roles.IS_VIDEO)
        is_live = self._asset_vm.data(idx, Roles.IS_LIVE)
        is_fav = self._asset_vm.data(idx, Roles.FEATURED)
        info = self._asset_vm.data(idx, Roles.INFO) or {}

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
        self._rotate_button.setEnabled(True)

        self._update_favorite_icon(bool(is_fav))

        # Reset the zoom slider to 100% before loading a new asset so that
        # stale zoom state from the previous asset does not bleed through.
        self._zoom_slider.blockSignals(True)
        self._zoom_slider.setValue(100)
        self._zoom_slider.blockSignals(False)

        # Load Media
        if is_video:
            self._player_view.show_video_surface(interactive=True)
            raw_adjustments = sidecar.load_adjustments(source)
            duration_sec = None
            if isinstance(info, dict):
                try:
                    duration_sec = float(info.get("dur") or info.get("duration") or 0.0) or None
                except (TypeError, ValueError):
                    duration_sec = None
            has_trim = sidecar.trim_is_non_default(raw_adjustments, duration_sec)
            needs_adjusted_preview = sidecar.video_requires_adjusted_preview(raw_adjustments)
            trim_in_sec, trim_out_sec = sidecar.normalise_video_trim(raw_adjustments, duration_sec)
            trim_range_ms = None
            if has_trim:
                trim_range_ms = (
                    int(round(trim_in_sec * 1000.0)),
                    int(round(trim_out_sec * 1000.0)),
                )
            # Store trim range before loading so that _on_video_duration_changed
            # and _on_video_position_changed remap the player bar correctly even
            # when the first durationChanged signal fires synchronously.
            if trim_range_ms is not None:
                self._trim_in_ms, self._trim_out_ms = trim_range_ms
            else:
                self._trim_in_ms = 0
                self._trim_out_ms = 0
            self._player_view.video_area.load_video(
                source,
                adjustments=(
                    sidecar.resolve_render_adjustments(raw_adjustments)
                    if needs_adjusted_preview
                    else (raw_adjustments or None)
                ),
                trim_range_ms=trim_range_ms,
                adjusted_preview=needs_adjusted_preview,
            )
            self._player_view.video_area.play()
            self._player_bar.setEnabled(True)
            self._zoom_handler.set_viewer(self._player_view.video_area)
            self._player_view.video_area.reset_zoom()
            self._zoom_widget.show()
        else:
            # Image or Live Photo
            self._player_view.show_image_surface()
            self._player_view.display_image(source)
            self._player_bar.setEnabled(False)
            self._zoom_handler.set_viewer(self._player_view.image_viewer)
            self._player_view.image_viewer.reset_zoom()
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

    def _sync_filmstrip_selection(self, row: int):
        """Update filmstrip selection and scroll to the item."""
        idx = self._asset_vm.index(row, 0)

        # Handle Proxy Model (if present)
        model = self._filmstrip_view.model()
        if hasattr(model, "mapFromSource"):
            idx = model.mapFromSource(idx)

        if idx.isValid():
            self._filmstrip_view.selectionModel().setCurrentIndex(
                idx, QItemSelectionModel.ClearAndSelect
            )
            self._filmstrip_view.center_on_index(idx)

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

    def _update_header(self, row: int | None) -> None:
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
        is_video = bool(self._asset_vm.data(idx, Roles.IS_VIDEO))

        if not abs_path: return

        source = Path(abs_path)

        # 1. Update UI immediately (Optimistic)
        if is_video:
            updates = self._player_view.video_area.rotate_image_ccw()
        else:
            updates = self._player_view.image_viewer.rotate_image_ccw()

        # 2. Persist adjustments
        try:
            navigation = self._navigation
            if navigation:
                navigation.pause_library_watcher()
            try:
                current_adjustments = sidecar.load_adjustments(source)
                current_adjustments.update(updates)
                sidecar.save_adjustments(source, current_adjustments)

                # 3. Invalidate thumbnails
                self._asset_vm.invalidate_thumbnail(str(source))
            finally:
                if navigation:
                    navigation.resume_library_watcher()

        except Exception as e:
            LOGGER.error(f"Failed to rotate: {e}")

    # --- Detail UI Logic Ported ---

    def _refresh_info_panel(self, row: int):
        if not self._info_panel:
            return

        idx = self._asset_vm.index(row, 0)
        info = self._asset_vm.data(idx, Roles.INFO)

        is_video = bool(info.get("is_video")) if info else False
        # Trigger enrichment when technical fields are absent.  For video we
        # also re-fetch when lens is missing because the stored metadata may
        # have been scanned before ExifTool lens extraction was in place.
        needs_enrichment = info and (
            (not info.get("frame_rate") or not info.get("lens")) if is_video
            else not info.get("iso")
        )
        if needs_enrichment:
            abs_path = info.get("abs")
            if abs_path:
                try:
                    if is_video:
                        # Pass ExifTool data so that lens/focal-length fields
                        # (which only come from ExifTool, not ffprobe) are
                        # included in the fresh metadata.
                        exif_payload = None
                        try:
                            exif_batch = get_metadata_batch([Path(abs_path)])
                            exif_payload = exif_batch[0] if exif_batch else None
                        except (ExternalToolError, OSError) as exc:
                            LOGGER.debug("ExifTool metadata fetch failed for %s: %s", abs_path, exc)
                        fresh = read_video_meta(Path(abs_path), exif_payload)
                    else:
                        fresh = read_image_meta(Path(abs_path))
                    # Only merge keys that have a real value so that existing
                    # metadata (e.g. lens stored in the DB) is never replaced
                    # by None from a call that could not retrieve the field.
                    info.update({k: v for k, v in fresh.items() if v is not None})
                except Exception as e:
                    LOGGER.debug("Failed enrichment: %s", e)

        self._info_panel.set_asset_metadata(info)

    def toggle_info_panel(self):
        if not self._info_panel: return
        if self._info_panel.isVisible():
            self._info_panel.close()
        else:
            if self._current_row >= 0:
                self._refresh_info_panel(self._current_row)
                self._info_panel.show()

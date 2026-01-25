"""Coordinator for media playback and detail view interactions."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional, Any
from pathlib import Path

from PySide6.QtCore import QObject, Slot, QTimer, Signal
from PySide6.QtGui import QIcon

from src.iPhoto.gui.coordinators.view_router import ViewRouter
from src.iPhoto.gui.ui.icons import load_icon
from src.iPhoto.gui.ui.models.roles import Roles
from src.iPhoto.gui.ui.widgets.info_panel import InfoPanel
from src.iPhoto.io.metadata import read_image_meta
from src.iPhoto.io import sidecar

if TYPE_CHECKING:
    from src.iPhoto.gui.ui.widgets.player_bar import PlayerBar
    from src.iPhoto.gui.ui.controllers.player_view_controller import PlayerViewController
    from src.iPhoto.gui.viewmodels.asset_list_viewmodel import AssetListViewModel
    from src.iPhoto.gui.coordinators.navigation_coordinator import NavigationCoordinator
    from PySide6.QtWidgets import QPushButton, QSlider, QToolButton, QWidget

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

        self._is_playing = False
        self._current_row = -1
        self._navigation: Optional[NavigationCoordinator] = None
        self._info_panel: Optional[InfoPanel] = None

        self._connect_signals()
        self._connect_zoom_controls()

    def set_navigation_coordinator(self, nav: NavigationCoordinator):
        self._navigation = nav

    def set_info_panel(self, panel: InfoPanel):
        self._info_panel = panel

    def current_row(self) -> int:
        """Expose current row index for external controllers (e.g. Share)."""
        return self._current_row

    def _connect_signals(self):
        # Player Bar -> Coordinator
        self._player_bar.playPauseRequested.connect(self.toggle_playback)
        self._player_bar.scrubStarted.connect(self._on_scrub_start)
        self._player_bar.scrubFinished.connect(self._on_scrub_end)
        self._player_bar.seekRequested.connect(self._on_seek)

        # Player View -> Coordinator
        self._player_view.liveReplayRequested.connect(self.replay_live_photo)
        self._player_view.video_area.playbackStateChanged.connect(self._sync_playback_state)

        # Model -> Coordinator
        self._asset_vm.dataChanged.connect(self._on_data_changed)

    def _connect_zoom_controls(self):
        viewer = self._player_view.image_viewer
        self._zoom_in.clicked.connect(viewer.zoom_in)
        self._zoom_out.clicked.connect(viewer.zoom_out)
        self._zoom_slider.valueChanged.connect(self._handle_zoom_slider_changed)
        viewer.zoomChanged.connect(self._handle_viewer_zoom_changed)

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

        idx = self._asset_vm.index(row, 0)
        abs_path = self._asset_vm.data(idx, Roles.ABS)
        is_video = self._asset_vm.data(idx, Roles.IS_VIDEO)
        is_live = self._asset_vm.data(idx, Roles.IS_LIVE)
        is_fav = self._asset_vm.data(idx, Roles.FEATURED)

        if not abs_path:
            return

        source = Path(abs_path)

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
            else:
                self._player_view.hide_live_badge()
                self._player_view.set_live_replay_enabled(False)

        self._is_playing = False
        self._player_bar.set_playback_state(False)
        self._player_bar.set_position(0)

        # Update Info Panel if visible
        if self._info_panel and self._info_panel.isVisible():
            self._refresh_info_panel(row)

    def _update_favorite_icon(self, is_favorite: bool):
        icon_name = "suit.heart.fill.svg" if is_favorite else "suit.heart.svg"
        self._favorite_button.setIcon(load_icon(icon_name))

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

    def reset_for_gallery(self):
        self._player_view.video_area.stop()
        self._player_view.show_placeholder()
        self._player_bar.setEnabled(False)
        self._is_playing = False
        if self._info_panel:
            self._info_panel.close()

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
        # Delegate to image viewer logic
        # Legacy: self._image_viewer.replay_live()
        # The PlayerViewController doesn't expose it directly, but image_viewer does.
        if hasattr(self._player_view.image_viewer, "replay_live"):
            self._player_view.image_viewer.replay_live()
        else:
            # Check if PlayerViewController has it
            pass

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

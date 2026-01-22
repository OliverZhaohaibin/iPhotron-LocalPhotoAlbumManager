"""Coordinator for media playback and detail view interactions."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional, Any
from pathlib import Path

from PySide6.QtCore import QObject, Slot, QTimer
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

LOGGER = logging.getLogger(__name__)

class PlaybackCoordinator(QObject):
    """
    Manages playback state, the PlayerBar, and interactions within the Detail View.
    Replaces PlaybackController and DetailUiController.
    """

    def __init__(
        self,
        player_bar: PlayerBar,
        player_view: PlayerViewController,
        router: ViewRouter,
        asset_vm: AssetListViewModel
    ):
        super().__init__()
        self._player_bar = player_bar
        self._player_view = player_view
        self._router = router
        self._asset_vm = asset_vm

        self._is_playing = False
        self._current_row = -1
        self._navigation: Optional[NavigationCoordinator] = None
        self._info_panel: Optional[InfoPanel] = None

        self._connect_signals()

    def set_navigation_coordinator(self, nav: NavigationCoordinator):
        self._navigation = nav

    def _connect_signals(self):
        # Player Bar -> Coordinator
        self._player_bar.playPauseRequested.connect(self.toggle_playback)
        self._player_bar.scrubStarted.connect(self._on_scrub_start)
        self._player_bar.scrubFinished.connect(self._on_scrub_end)
        self._player_bar.seekRequested.connect(self._on_seek)

        # Player View -> Coordinator
        self._player_view.liveReplayRequested.connect(self.replay_live_photo)

    @Slot()
    def toggle_playback(self):
        self._is_playing = not self._is_playing
        self._player_bar.set_playback_state(self._is_playing)

        if self._is_playing:
            self._player_view.video_area.play()
        else:
            self._player_view.video_area.pause()

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

        idx = self._asset_vm.index(row, 0)
        abs_path = self._asset_vm.data(idx, Roles.ABS)
        is_video = self._asset_vm.data(idx, Roles.IS_VIDEO)
        is_live = self._asset_vm.data(idx, Roles.IS_LIVE)

        if not abs_path:
            return

        source = Path(abs_path)

        # Load Media
        if is_video:
            self._player_view.show_video_surface(interactive=True)
            self._player_view.video_area.load_video(source)
            self._player_bar.setEnabled(True)
        else:
            # Image or Live Photo
            self._player_view.show_image_surface()
            self._player_view.display_image(source)
            self._player_bar.setEnabled(False)

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

    def reset_for_gallery(self):
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

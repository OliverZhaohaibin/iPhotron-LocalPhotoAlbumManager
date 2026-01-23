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
from src.iPhoto.application.services.asset_service import AssetService

if TYPE_CHECKING:
    from src.iPhoto.gui.ui.widgets.player_bar import PlayerBar
    from src.iPhoto.gui.ui.controllers.player_view_controller import PlayerViewController
    from src.iPhoto.gui.viewmodels.asset_list_viewmodel import AssetListViewModel
    from src.iPhoto.gui.coordinators.navigation_coordinator import NavigationCoordinator
    from PySide6.QtWidgets import QSlider, QToolButton, QWidget

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
        asset_service: Optional[AssetService] = None
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

        self._asset_service = asset_service

        self._is_playing = False
        self._current_row = -1
        self._navigation: Optional[NavigationCoordinator] = None
        self._info_panel: Optional[InfoPanel] = None

        self._connect_signals()
        self._connect_zoom_controls()

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

        # Notify selection change
        self.assetChanged.emit(row)

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

    def toggle_favorite(self, row: int):
        """Toggles favorite status for the given row."""
        if row < 0: return

        idx = self._asset_vm.index(row, 0)
        rel_path = self._asset_vm.data(idx, Roles.REL)
        is_fav = self._asset_vm.data(idx, Roles.FEATURED)

        if not rel_path or self._asset_service is None:
            return

        new_state = not is_fav
        try:
            # Toggle in DB
            self._asset_service.set_favorite(rel_path, new_state)

            # Refresh to update UI
            # Since VM doesn't have fine-grained setter for Favorite role yet,
            # we rely on cache invalidation or reloading data for that row?
            # Or assume ViewModel re-queries?
            # Re-querying is robust.

            # Let's force a reload of the current query in the VM to refresh data.
            # (Optimizable later)
            # self._asset_vm.load_query(...) # We don't have the query object handy here easily.

            # Better: Invalidate the specific row in the model if possible.
            # But the model is read-only from cached DTOs.
            # We need to update the cached DTO in DataSource.
            # Since we can't easily reach into DataSource cache from here...

            # Workaround: Emit a signal that MainCoordinator catches to reload?
            # Or just hack it by calling invalidate_thumbnail which triggers dataChanged signal?
            # That might redraw the icon if the icon logic re-reads from DB?
            # No, data comes from DTO.

            # We really should update the DTO.
            # But DTOs are in DataSource.

            # Let's trigger a full refresh for correctness for now.
            # Or ask Navigation to reload?
            if self._navigation:
                # Trigger re-open of current view?
                # Navigation keeps state.
                pass

            # Actually, let's just use `invalidate_thumbnail` trick IF the ViewModel re-fetches.
            # But VM data() uses DTO.

            # Minimal fix: Force a reload of the current view (heavy but correct).
            # self._navigation.refresh_current_view() if implemented.

            # For this patch, since I cannot easily update the DTO in place without new API on DataSource,
            # I will assume the user navigates or refreshes.
            # Wait, the user complaint is "cannot operate". They click and nothing happens.
            # Updating DB is "operating". UI feedback is secondary but important.

            # Let's try to update the icon state locally in the button if possible?
            # No, MVVM.

            # I will add `reload()` to `AssetListViewModel`/`DataSource` that re-runs the last query.
            self._asset_vm.reload()

        except Exception as e:
            LOGGER.error(f"Failed to toggle favorite: {e}")

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

"""Coordinator that binds detail widgets to DetailViewModel presentation."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QItemSelectionModel, QModelIndex, QObject, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QColor, QPalette

from iPhoto.config import PLAY_ASSET_DEBOUNCE_MS
from iPhoto.errors import ExternalToolError
from iPhoto.gui.coordinators.view_router import ViewRouter
from iPhoto.gui.ui.controllers.edit_zoom_handler import EditZoomHandler
from iPhoto.gui.ui.controllers.header_controller import HeaderController
from iPhoto.gui.ui.icons import load_icon
from iPhoto.gui.ui.widgets.info_panel import InfoPanel
from iPhoto.gui.viewmodels.detail_viewmodel import DetailPresentation, DetailViewModel
from iPhoto.io import sidecar
from iPhoto.io.metadata import read_image_meta, read_video_meta
from iPhoto.utils.exiftool import get_metadata_batch

if TYPE_CHECKING:
    from iPhoto.utils.settings import Settings
    from PySide6.QtWidgets import QPushButton, QSlider, QToolButton, QWidget

    from iPhoto.gui.coordinators.edit_coordinator import EditCoordinator
    from iPhoto.gui.coordinators.navigation_coordinator import NavigationCoordinator
    from iPhoto.gui.ui.controllers.player_view_controller import PlayerViewController
    from iPhoto.gui.ui.media import MediaAdjustmentCommitter, MediaSelectionSession
    from iPhoto.gui.ui.widgets.filmstrip_view import FilmstripView
    from iPhoto.gui.ui.widgets.player_bar import PlayerBar
    from iPhoto.gui.viewmodels.gallery_list_model_adapter import GalleryListModelAdapter

LOGGER = logging.getLogger(__name__)


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
        media_session: MediaSelectionSession,
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
    ) -> None:
        super().__init__()
        self._player_bar = player_bar
        self._player_view = player_view
        self._router = router
        self._asset_model = asset_model
        self._detail_vm = detail_vm
        self._media_session = media_session
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

        self._is_playing = False
        self._navigation: NavigationCoordinator | None = None
        self._edit_coordinator: EditCoordinator | None = None
        self._info_panel: InfoPanel | None = None
        self._active_live_motion: Path | None = None
        self._active_live_still: Path | None = None
        self._resume_after_transition = False
        self._pending_restore_path: Path | None = None
        self._pending_restore_reason: str | None = None
        self._trim_in_ms = 0
        self._trim_out_ms = 0
        self._current_presentation: DetailPresentation | None = None

        self._pending_play_row: int | None = None
        self._play_debounce = QTimer(self)
        self._play_debounce.setSingleShot(True)
        self._play_debounce.setInterval(PLAY_ASSET_DEBOUNCE_MS)
        self._play_debounce.timeout.connect(self._execute_pending_play)

        self._connect_signals()
        self._setup_zoom_handler()
        self._restore_filmstrip_preference()

    def set_navigation_coordinator(self, nav: NavigationCoordinator) -> None:
        self._navigation = nav

    def set_edit_coordinator(self, edit: EditCoordinator) -> None:
        self._edit_coordinator = edit

    def set_info_panel(self, panel: InfoPanel) -> None:
        self._info_panel = panel

    def current_row(self) -> int:
        return self._media_session.current_row()

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
        current_row = self._media_session.current_row()
        target_row = current_row if current_row >= 0 else 0
        if current_row < 0 or not self._router.is_detail_view_active():
            self.play_asset(target_row)
        return True

    def show_placeholder_in_viewer(self) -> None:
        self._player_view.show_placeholder()

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

        self._media_session.restoreRequested.connect(self._handle_restore_requested)
        self._adjustment_committer.adjustmentsCommitted.connect(self._handle_adjustments_committed)
        self._router.detailViewShown.connect(self._handle_detail_view_shown)

        self._detail_vm.route_requested.connect(self._handle_route_requested)
        self._detail_vm.presentation_changed.connect(self._handle_presentation_changed)
        self._detail_vm.rotate_requested.connect(self._handle_rotate_requested)

        self._filmstrip_view.nextItemRequested.connect(self.select_next)
        self._filmstrip_view.prevItemRequested.connect(self.select_previous)
        self._filmstrip_view.itemClicked.connect(self._on_filmstrip_clicked)
        self._toggle_filmstrip_action.toggled.connect(self._handle_filmstrip_toggled)

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
        self._pending_play_row = row
        self._play_debounce.start()

    def _execute_pending_play(self) -> None:
        row = self._pending_play_row
        self._pending_play_row = None
        if row is None:
            return
        self._detail_vm.show_row(row)

    @Slot(str)
    def _handle_route_requested(self, view: str) -> None:
        if view == "detail":
            self._router.show_detail()
        elif view == "gallery":
            self._router.show_gallery()

    def _handle_presentation_changed(self, presentation: DetailPresentation) -> None:
        previous = self._current_presentation
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
        )
        if same_asset:
            self._update_favorite_icon(presentation.is_favorite)
            if self._info_panel and presentation.info_panel_visible:
                self._refresh_info_panel(presentation.info)
                self._info_panel.show()
            elif self._info_panel and self._info_panel.isVisible() and not presentation.info_panel_visible:
                self._info_panel.close()
            return
        self._render_presentation(presentation)

    def _render_presentation(self, presentation: DetailPresentation) -> None:
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
            self._player_view.show_video_surface(interactive=True)
            raw_adjustments = sidecar.load_adjustments(source)
            info = presentation.info
            duration_sec = None
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
            self._player_view.show_image_surface()
            self._player_view.display_image(source)
            self._player_bar.setEnabled(False)
            self._zoom_handler.set_viewer(self._player_view.image_viewer)
            self._player_view.image_viewer.reset_zoom()
            self._zoom_widget.show()

            if presentation.is_live:
                self._player_view.show_live_badge()
                self._player_view.set_live_replay_enabled(True)
                self._autoplay_live_motion(presentation)
            else:
                self._player_view.hide_live_badge()
                self._player_view.set_live_replay_enabled(False)

        self._is_playing = False
        self._player_bar.set_playback_state(False)
        self._player_bar.set_position(0)

        if self._info_panel and presentation.info_panel_visible:
            self._refresh_info_panel(presentation.info)
            self._info_panel.show()
        elif self._info_panel and self._info_panel.isVisible() and not presentation.info_panel_visible:
            self._info_panel.close()

    def _autoplay_live_motion(self, presentation: DetailPresentation) -> None:
        motion_path = presentation.live_motion_abs
        if motion_path is None:
            return
        self._active_live_motion = motion_path
        self._active_live_still = presentation.path
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
        self._player_view.video_area.stop()
        self._player_view.show_placeholder()
        self._player_bar.setEnabled(False)
        self._is_playing = False
        self._pending_restore_path = None
        self._pending_restore_reason = None
        self._current_presentation = None
        self._update_header(None)
        if self._info_panel:
            self._info_panel.close()

    def shutdown(self) -> None:
        self._player_view.video_area.stop()
        self._is_playing = False
        self._pending_restore_path = None
        self._pending_restore_reason = None
        self._current_presentation = None
        self._update_header(None)
        if self._info_panel:
            self._info_panel.close()

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
        local_info = dict(info)
        is_video = bool(local_info.get("is_video"))
        needs_enrichment = (
            (not local_info.get("frame_rate") or not local_info.get("lens"))
            if is_video
            else not local_info.get("iso")
        )
        if needs_enrichment:
            abs_path = local_info.get("abs")
            if abs_path:
                try:
                    if is_video:
                        exif_payload = None
                        try:
                            exif_batch = get_metadata_batch([Path(abs_path)])
                            exif_payload = exif_batch[0] if exif_batch else None
                        except (ExternalToolError, OSError):
                            LOGGER.debug("ExifTool metadata fetch failed for %s", abs_path, exc_info=True)
                        fresh = read_video_meta(Path(abs_path), exif_payload)
                    else:
                        fresh = read_image_meta(Path(abs_path))
                    local_info.update({k: v for k, v in fresh.items() if v is not None})
                except Exception:
                    LOGGER.debug("Failed enrichment for %s", abs_path, exc_info=True)
        self._info_panel.set_asset_metadata(local_info)

    def toggle_info_panel(self) -> None:
        self._detail_vm.toggle_info()

    def _is_edit_session_active(self) -> bool:
        return bool(self._edit_coordinator and self._edit_coordinator.is_editing())

    def _handle_restore_requested(self, path: object, reason: str) -> None:
        if not isinstance(path, Path):
            return
        if self._is_edit_session_active():
            self._pending_restore_path = path
            self._pending_restore_reason = reason
            return
        self._detail_vm.restore_after_adjustment(path, reason)

    def _handle_adjustments_committed(self, path: object, reason: str) -> None:
        self._handle_restore_requested(path, reason)

    def _handle_detail_view_shown(self) -> None:
        if self._pending_restore_path is None:
            return
        path = self._pending_restore_path
        reason = self._pending_restore_reason or "restore"
        self._pending_restore_path = None
        self._pending_restore_reason = None
        self._detail_vm.restore_after_adjustment(path, reason)

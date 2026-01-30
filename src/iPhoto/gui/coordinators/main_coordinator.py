"""Coordinator that wires the main window to application logic.

This replaces the legacy MainController as the top-level orchestrator.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Optional

from PySide6.QtCore import (
    QObject,
    QThreadPool,
    QModelIndex,
    QItemSelectionModel,
    QCoreApplication,
    Qt,
)
from PySide6.QtGui import QShortcut, QKeySequence, QAction

from src.iPhoto.appctx import AppContext
from src.iPhoto.config import DEFAULT_EXCLUDE, DEFAULT_INCLUDE
from src.iPhoto.gui.ui.models.asset_model import Roles
from src.iPhoto.gui.ui.models.spacer_proxy_model import SpacerProxyModel
from src.iPhoto.gui.ui.controllers.dialog_controller import DialogController
from src.iPhoto.gui.ui.controllers.header_controller import HeaderController
from src.iPhoto.gui.ui.controllers.share_controller import ShareController
from src.iPhoto.gui.ui.controllers.status_bar_controller import StatusBarController
from src.iPhoto.gui.ui.controllers.window_theme_controller import WindowThemeController
from src.iPhoto.gui.ui.controllers.preview_controller import PreviewController
from src.iPhoto.gui.ui.controllers.context_menu_controller import ContextMenuController
from src.iPhoto.gui.ui.controllers.selection_controller import SelectionController
from src.iPhoto.gui.ui.controllers.export_controller import ExportController
from src.iPhoto.gui.ui.widgets.asset_delegate import AssetGridDelegate

# New Architecture Imports
from src.iPhoto.gui.viewmodels.album_viewmodel import AlbumViewModel
from src.iPhoto.gui.viewmodels.asset_list_viewmodel import AssetListViewModel
from src.iPhoto.gui.viewmodels.asset_data_source import AssetDataSource
from src.iPhoto.application.services.album_service import AlbumService
from src.iPhoto.application.services.asset_service import AssetService
from src.iPhoto.di.container import DependencyContainer
from src.iPhoto.events.bus import EventBus
from src.iPhoto.domain.repositories import IAssetRepository
from src.iPhoto.infrastructure.services.thumbnail_cache_service import ThumbnailCacheService

# Background Tasks
from src.iPhoto.gui.ui.tasks.background_scanner import BackgroundScanner

# New Coordinators
from src.iPhoto.gui.coordinators.view_router import ViewRouter
from src.iPhoto.gui.coordinators.navigation_coordinator import NavigationCoordinator
from src.iPhoto.gui.coordinators.playback_coordinator import PlaybackCoordinator
from src.iPhoto.gui.coordinators.edit_coordinator import EditCoordinator

if TYPE_CHECKING:
    from src.iPhoto.gui.ui.main_window import MainWindow


class MainCoordinator(QObject):
    """High-level coordinator for the main window.
    Acts as the entry point and glue code for the application, initializing
    legacy controllers and bridging them with the new architecture.
    """

    def __init__(self, window: MainWindow, context: AppContext, container: DependencyContainer = None) -> None:
        super().__init__(window)
        self._window = window
        self._context = context
        self._container = container
        # facade reference kept for signal wiring as some systems still emit through it
        self._facade = context.facade
        self._logger = logging.getLogger(__name__)

        # Resolve Services
        if self._container:
            self._event_bus = self._container.resolve(EventBus)
            self._album_service = self._container.resolve(AlbumService)
            self._asset_service = self._container.resolve(AssetService)
            self._asset_repo = self._container.resolve(IAssetRepository)
        else:
            raise RuntimeError("DependencyContainer is required for MainCoordinator")

        # --- ViewModels Setup ---
        lib_root = context.library.root()
        self._asset_data_source = AssetDataSource(self._asset_repo, lib_root)

        # Thumbnail Service
        cache_root = Path.home() / ".iPhoto" / "cache" / "thumbs"
        if lib_root:
            cache_root = lib_root / ".iPhoto" / "cache" / "thumbs"

        self._thumbnail_service = ThumbnailCacheService(cache_root)
        self._asset_list_vm = AssetListViewModel(self._asset_data_source, self._thumbnail_service)

        # Background Scanner
        self._background_scanner = BackgroundScanner(self._container)

        # --- Coordinators Setup ---

        # 1. View Router
        self._view_router = ViewRouter(window.ui)

        # 2. Navigation Coordinator
        self._navigation = NavigationCoordinator(
            window.ui.sidebar,
            self._view_router,
            self._album_service,
            self._asset_list_vm,
            self._event_bus,
            context,
            context.facade,  # Legacy Facade Bridge
        )

        # 3. Playback Coordinator
        from src.iPhoto.gui.ui.controllers.player_view_controller import PlayerViewController
        self._player_view_controller = PlayerViewController(
            window.ui.player_stack,
            window.ui.image_viewer,
            window.ui.video_area,
            window.ui.player_placeholder,
            window.ui.live_badge
        )
        self._header_controller = HeaderController(
            window.ui.location_label,
            window.ui.timestamp_label,
        )

        self._playback = PlaybackCoordinator(
            player_bar=window.ui.player_bar,
            player_view=self._player_view_controller,
            router=self._view_router,
            asset_vm=self._asset_list_vm,
            zoom_slider=window.ui.zoom_slider,
            zoom_in_button=window.ui.zoom_in_button,
            zoom_out_button=window.ui.zoom_out_button,
            zoom_widget=window.ui.zoom_widget,
            favorite_button=window.ui.favorite_button,
            info_button=window.ui.info_button,
            rotate_button=window.ui.rotate_left_button,
            edit_button=window.ui.edit_button,
            share_button=window.ui.share_button,
            filmstrip_view=window.ui.filmstrip_view,
            toggle_filmstrip_action=window.ui.toggle_filmstrip_action,
            settings=context.settings,
            header_controller=self._header_controller,
        )

        # Inject optional dependencies into Playback
        self._playback.set_navigation_coordinator(self._navigation)
        self._navigation.set_playback_coordinator(self._playback)
        # Manually attach info panel if available
        if hasattr(window.ui, 'info_panel'):
            self._playback.set_info_panel(window.ui.info_panel)

        # 4. Theme Controller
        self._theme_controller = WindowThemeController(window.ui, window, context.theme)

        # 5. Edit Coordinator
        self._edit = EditCoordinator(
            window.ui, # Pass UI root for access to sidebar/header/viewer
            self._view_router,
            self._event_bus,
            self._asset_list_vm, # Injected for invalidation
            window,
            self._theme_controller
        )

        # --- Legacy Controllers ---
        self._dialog = DialogController(window, context, window.ui.status_bar)
        self._status_bar = StatusBarController(
            window.ui.status_bar,
            window.ui.progress_bar,
            window.ui.rescan_action,
            context,
        )

        self._share_controller = ShareController(
            settings=context.settings,
            playlist=self._playback,  # Acts as playlist controller (provides current_row)
            asset_model=self._asset_list_vm,
            status_bar=self._status_bar,
            notification_toast=window.ui.notification_toast,
            share_button=window.ui.share_button,
            share_action_group=window.ui.share_action_group,
            copy_file_action=window.ui.share_action_copy_file,
            copy_path_action=window.ui.share_action_copy_path,
            reveal_action=window.ui.share_action_reveal_file,
        )
        self._share_controller.restore_preference()

        self._export_controller = ExportController(
            settings=context.settings,
            library=context.library,
            status_bar=self._status_bar,
            toast=window.ui.notification_toast,
            export_all_action=window.ui.export_all_edited_action,
            export_selected_action=window.ui.export_selected_action,
            destination_group=window.ui.export_destination_group,
            destination_library=window.ui.export_destination_library,
            destination_ask=window.ui.export_destination_ask,
            main_window=window,
            selection_callback=window.current_selection,
        )

        # --- Binding Data to Views ---
        window.ui.grid_view.setModel(self._asset_list_vm)

        # Assign Delegate for Grid View (Fixes text display and spacing)
        self._grid_delegate = AssetGridDelegate(window.ui.grid_view, filmstrip_mode=False)
        window.ui.grid_view.setItemDelegate(self._grid_delegate)

        window.ui.grid_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        # Use SpacerProxyModel for Filmstrip to allow centering of first/last items
        self._filmstrip_proxy = SpacerProxyModel(window.ui.filmstrip_view)
        self._filmstrip_proxy.setSourceModel(self._asset_list_vm)
        window.ui.filmstrip_view.setModel(self._filmstrip_proxy)

        # Assign Delegate for Filmstrip View
        self._filmstrip_delegate = AssetGridDelegate(window.ui.filmstrip_view, filmstrip_mode=True)
        window.ui.filmstrip_view.setItemDelegate(self._filmstrip_delegate)

        self._preview_controller = PreviewController(window.ui.preview_window)
        self._preview_controller.bind_view(window.ui.grid_view)

        self._selection_controller = SelectionController(
            selection_button=window.ui.selection_button,
            grid_view=window.ui.grid_view,
            grid_delegate=self._grid_delegate,
            preview_controller=self._preview_controller,
            playback=None,
            handle_grid_clicks=False,
            parent=self,
        )

        self._context_menu = ContextMenuController(
            grid_view=window.ui.grid_view,
            asset_model=self._asset_list_vm,
            facade=self._facade,
            status_bar=self._status_bar,
            notification_toast=window.ui.notification_toast,
            selection_controller=self._selection_controller,
            navigation=self._navigation,
            export_callback=window.ui.export_selected_action.trigger,
            parent=self,
        )

        self._connect_signals()

    def start(self):
        """Start the coordinator."""
        self._logger.info("MainCoordinator started")
        self._view_router.show_gallery()

    # ------------------------------------------------------------------
    # Window manager integration (legacy interface)
    # ------------------------------------------------------------------
    def is_edit_view_active(self) -> bool:
        """Return True when the edit view is currently active."""

        return self._view_router.is_edit_view_active()

    def edit_controller(self) -> EditCoordinator:
        """Expose the edit coordinator for immersive mode hooks."""

        return self._edit

    def suspend_playback_for_transition(self) -> bool:
        """Pause playback before a chrome transition."""

        return self._playback.suspend_playback_for_transition()

    def prepare_fullscreen_asset(self) -> bool:
        """Ensure the current asset is ready for immersive mode."""

        return self._playback.prepare_fullscreen_asset()

    def show_placeholder_in_viewer(self) -> None:
        """Display a placeholder while the detail view is preparing."""

        self._playback.show_placeholder_in_viewer()

    def resume_playback_after_transition(self) -> None:
        """Restore playback after a chrome transition."""

        self._playback.resume_playback_after_transition()

    def shutdown(self) -> None:
        """Stop worker threads and background jobs before the app exits."""
        # 1. Cancel any active background scans/imports via Facade
        if self._facade:
            self._facade.cancel_active_scans()
        if self._context and self._context.library:
            self._context.library.shutdown()

        # 2. Stop playback (video/audio)
        if self._playback:
            self._playback.shutdown()

        # 3. Shutdown other coordinators if they have cleanup logic
        if self._edit:
            self._edit.shutdown()

        if self._thumbnail_service:
            self._thumbnail_service.shutdown()

        if hasattr(self._window.ui, "preview_window"):
            try:
                self._window.ui.preview_window.close_preview(False)
            except AttributeError:
                self._window.ui.preview_window.close()
        if hasattr(self._window.ui, "map_view"):
            try:
                self._window.ui.map_view.close()
            except RuntimeError:
                self._logger.warning("Failed to close map view during shutdown", exc_info=True)

        # 4. Wait briefly for background threads (e.g. thumbnail generation) to finish
        thread_pool = QThreadPool.globalInstance()
        if not thread_pool.waitForDone(2000):
            thread_pool.clear()

        app = QCoreApplication.instance()
        if app is not None:
            app.closeAllWindows()
            app.quit()

    def _connect_signals(self) -> None:
        """Connect application signals."""
        ui = self._window.ui

        # Grid interactions
        ui.grid_view.itemClicked.connect(self._on_asset_clicked)

        # Filmstrip clicks are now handled by PlaybackCoordinator

        # Connect favorite click from grid view
        if hasattr(ui.grid_view, "favoriteClicked"):
            ui.grid_view.favoriteClicked.connect(self._on_favorite_clicked)

        # Coordinator Signals
        self._playback.assetChanged.connect(self._sync_selection)

        # Viewer Interactions (Wheel Navigation)
        ui.image_viewer.nextItemRequested.connect(self._playback.select_next)
        ui.image_viewer.prevItemRequested.connect(self._playback.select_previous)
        ui.video_area.nextItemRequested.connect(self._playback.select_next)
        ui.video_area.prevItemRequested.connect(self._playback.select_previous)

        # Menus
        ui.open_album_action.triggered.connect(self._handle_open_album_dialog)
        ui.rescan_action.triggered.connect(self._handle_rescan_clicked)
        ui.edit_button.clicked.connect(self._handle_edit_clicked)
        # ui.edit_rotate_left_button is handled by EditCoordinator in Edit Mode
        ui.rotate_left_button.clicked.connect(self._playback.rotate_current_asset)
        ui.favorite_button.clicked.connect(self._handle_toggle_favorite)

        # Info Button
        if hasattr(ui, 'info_button'):
            ui.info_button.clicked.connect(self._playback.toggle_info_panel)

        # Back Button
        if hasattr(ui, 'back_button'):
            ui.back_button.clicked.connect(self._handle_back_button)

        # Dashboard Click
        if hasattr(ui, 'albums_dashboard_page'):
            ui.albums_dashboard_page.albumSelected.connect(self.open_album_from_path)

        # Navigation
        self._navigation.bindLibraryRequested.connect(self._dialog.bind_library_dialog)
        self._navigation.scanRequested.connect(self._handle_scan_requested)
        ui.bind_library_action.triggered.connect(self._dialog.bind_library_dialog)

        # Preferences (Wheel, Volume) - Filmstrip handled in PlaybackCoordinator
        self._restore_preferences()
        ui.wheel_action_group.triggered.connect(self._handle_wheel_action_changed)

        # Status Bar Connections (Restored)
        # Facade Signals -> Status Bar
        # Note: AppFacade exposes library_updates (ScannerSignals)
        updates = self._facade.library_updates
        updates.scanProgress.connect(self._status_bar.handle_scan_progress)
        updates.scanFinished.connect(self._status_bar.handle_scan_finished)
        self._facade.scanBatchFailed.connect(self._status_bar.handle_scan_batch_failed)
        self._facade.scanProgress.connect(self._status_bar.handle_scan_progress)
        self._facade.scanFinished.connect(self._status_bar.handle_scan_finished)

        # Background Scanner Signals
        self._background_scanner.scanProgress.connect(self._status_bar.handle_scan_progress)
        self._background_scanner.scanFinished.connect(self._status_bar.handle_scan_finished)
        # Refresh view model when scan finishes
        self._background_scanner.scanFinished.connect(self._on_background_scan_finished)

        self._facade.loadStarted.connect(self._status_bar.handle_load_started)
        self._facade.loadProgress.connect(self._status_bar.handle_load_progress)
        self._facade.loadFinished.connect(self._status_bar.handle_load_finished)

        import_service = self._facade.import_service
        import_service.importStarted.connect(self._status_bar.handle_import_started)
        import_service.importProgress.connect(self._status_bar.handle_import_progress)
        import_service.importFinished.connect(self._status_bar.handle_import_finished)

        move_service = self._facade.move_service
        move_service.moveStarted.connect(self._status_bar.handle_move_started)
        move_service.moveProgress.connect(self._status_bar.handle_move_progress)
        move_service.moveFinished.connect(self._status_bar.handle_move_finished)

        # Error Reporting
        self._facade.errorRaised.connect(self._dialog.show_error)
        self._context.library.errorRaised.connect(self._dialog.show_error)

        # Theme Switching (Restored)
        ui.theme_system.triggered.connect(lambda: self._context.settings.set("ui.theme", "system"))
        ui.theme_light.triggered.connect(lambda: self._context.settings.set("ui.theme", "light"))
        ui.theme_dark.triggered.connect(lambda: self._context.settings.set("ui.theme", "dark"))

        current_theme = self._context.settings.get("ui.theme", "system")
        if current_theme == "light":
            ui.theme_light.setChecked(True)
        elif current_theme == "dark":
            ui.theme_dark.setChecked(True)
        else:
            ui.theme_system.setChecked(True)

        # Shortcuts
        self._favorite_shortcut = QShortcut(QKeySequence("."), self._window)
        self._favorite_shortcut.activated.connect(self._handle_toggle_favorite)
        self._exit_fullscreen_shortcut = QShortcut(
            QKeySequence(Qt.Key_Escape),
            self._window,
        )
        self._exit_fullscreen_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._exit_fullscreen_shortcut.activated.connect(self._window.exit_fullscreen)

    def _on_asset_clicked(self, index: QModelIndex):
        if self._selection_controller and self._selection_controller.is_active():
            return
        self._playback.play_asset(index.row())

    def _on_favorite_clicked(self, index: QModelIndex):
        """Handle favorite badge click from grid view."""
        path_str = self._asset_list_vm.data(index, Roles.REL) or self._asset_list_vm.data(index, Roles.ABS)
        if path_str:
            new_state = self._asset_service.toggle_favorite_by_path(Path(path_str))
            self._asset_list_vm.update_favorite(index.row(), new_state)

    def _sync_selection(self, row: int):
        """Syncs grid view selection when playback asset changes."""
        idx = self._asset_list_vm.index(row, 0)
        self._window.ui.grid_view.selectionModel().setCurrentIndex(
            idx, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows
        )
        self._window.ui.grid_view.scrollTo(idx)

    def _handle_open_album_dialog(self):
        path = self._dialog.open_album_dialog()
        if path:
            self.open_album_from_path(path)

    def _handle_rescan_clicked(self) -> None:
        """Trigger a background rescan and surface progress feedback."""
        if self._navigation.current_album_id:
            self._handle_scan_requested(self._navigation.current_album_id)
            return

        self._status_bar.begin_scan()

        if self._facade.current_album is not None:
            self._facade.rescan_current_async()
            return

        library_root = self._context.library.root()
        if library_root is None:
            self._status_bar.show_message("No album is currently open.", 3000)
            return

        self._context.library.start_scanning(
            library_root, DEFAULT_INCLUDE, DEFAULT_EXCLUDE
        )

    def _handle_scan_requested(self, album_id: str):
        self._status_bar.begin_scan()
        path = self._navigation.current_album_path or Path("unknown")
        self._background_scanner.scan(album_id, path)

    def _on_background_scan_finished(self, root: Path, success: bool):
        if success:
            self._asset_list_vm.refresh()

    def _handle_edit_clicked(self):
        # Trigger Edit Mode from Detail View context
        indexes = self._window.ui.grid_view.selectionModel().selectedIndexes()

        # Fallback: If no selection in grid, but we have a playback row?
        if not indexes and self._playback.current_row() >= 0:
            idx = self._asset_list_vm.index(self._playback.current_row(), 0)
            if idx.isValid():
                indexes = [idx]

        if indexes:
            idx = indexes[0]
            path_str = self._asset_list_vm.data(idx, Roles.ABS)
            if path_str:
                self._edit.enter_edit_mode(Path(path_str))

    def _handle_back_button(self):
        """Returns to the gallery view."""
        self._playback.reset_for_gallery()
        self._view_router.show_gallery()

    def _handle_toggle_favorite(self):
        """Toggles favorite status for selected assets."""
        indexes = self._window.ui.grid_view.selectionModel().selectedIndexes()

        # Fallback: If no selection in grid, but we have a playback row?
        # This handles the case where Detail View is active but grid selection sync failed or focus issue.
        if not indexes and self._playback.current_row() >= 0:
             # Construct an index for the current playback row
             idx = self._asset_list_vm.index(self._playback.current_row(), 0)
             if idx.isValid():
                 indexes = [idx]

        if not indexes:
            # Try filmstrip if grid has no selection
            indexes = self._window.ui.filmstrip_view.selectionModel().selectedIndexes()

        for idx in indexes:
            path_str = self._asset_list_vm.data(idx, Roles.REL) or self._asset_list_vm.data(idx, Roles.ABS)
            if path_str:
                new_state = self._asset_service.toggle_favorite_by_path(Path(path_str))
                self._asset_list_vm.update_favorite(idx.row(), new_state)

    def open_album_from_path(self, path: Path):
        self._navigation.open_album(path)

    def _restore_preferences(self) -> None:
        """Restore UI preferences for wheel action and volume."""
        ui = self._window.ui
        settings = self._context.settings

        # 1. Wheel Action
        wheel_action = settings.get("ui.wheel_action", "navigate")
        if wheel_action == "zoom":
            ui.wheel_action_zoom.setChecked(True)
        else:
            wheel_action = "navigate"
            ui.wheel_action_navigate.setChecked(True)
        ui.image_viewer.set_wheel_action(wheel_action)

        # 2. Volume / Mute
        stored_volume = settings.get("ui.volume", 75)
        try:
            initial_volume = int(round(float(stored_volume)))
        except (TypeError, ValueError):
            initial_volume = 75
        initial_volume = max(0, min(100, initial_volume))

        stored_muted = settings.get("ui.is_muted", False)
        if isinstance(stored_muted, str):
            initial_muted = stored_muted.strip().lower() in {"1", "true", "yes", "on"}
        else:
            initial_muted = bool(stored_muted)

        ui.video_area.set_volume(initial_volume)
        ui.video_area.set_muted(initial_muted)

    def _handle_wheel_action_changed(self, action: QAction) -> None:
        ui = self._window.ui
        if action is ui.wheel_action_zoom:
            selected = "zoom"
        else:
            selected = "navigate"

        if self._context.settings.get("ui.wheel_action") != selected:
            self._context.settings.set("ui.wheel_action", selected)

        ui.image_viewer.set_wheel_action(selected)

    # --- Public Accessors for Window ---
    def toggle_playback(self):
        self._playback.toggle_playback()

    def replay_live_photo(self):
        self._playback.replay_live_photo()

    def request_next_item(self):
        self._playback.select_next()

    def request_previous_item(self):
        self._playback.select_previous()

    def paths_from_indexes(self, indexes: Iterable[QModelIndex]) -> list[Path]:
        paths = []
        for idx in indexes:
            p = self._asset_list_vm.data(idx, Roles.ABS)
            if p:
                paths.append(Path(p))
        return paths

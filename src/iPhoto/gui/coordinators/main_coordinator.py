"""Coordinator that wires the main window to application logic.

This replaces the legacy MainController as the top-level orchestrator.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, TYPE_CHECKING, Optional
import logging

from PySide6.QtCore import QObject, QThreadPool, QModelIndex, QItemSelectionModel
from PySide6.QtGui import QShortcut, QKeySequence

from src.iPhoto.appctx import AppContext
from src.iPhoto.gui.ui.models.asset_model import Roles
from src.iPhoto.gui.ui.controllers.data_manager import DataManager
from src.iPhoto.gui.ui.controllers.dialog_controller import DialogController
from src.iPhoto.gui.ui.controllers.status_bar_controller import StatusBarController
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
            context.facade # Legacy Facade Bridge
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

        self._playback = PlaybackCoordinator(
            window.ui.player_bar,
            self._player_view_controller,
            self._view_router,
            self._asset_list_vm,
            window.ui.zoom_slider,
            window.ui.zoom_in_button,
            window.ui.zoom_out_button,
            window.ui.zoom_widget
        )

        # Inject optional dependencies into Playback
        self._playback.set_navigation_coordinator(self._navigation)
        self._navigation.set_playback_coordinator(self._playback)
        # Manually attach info panel if available
        if hasattr(window.ui, 'info_panel'):
            self._playback._info_panel = window.ui.info_panel # Direct private access for MVP wiring

        # 4. Edit Coordinator
        self._edit = EditCoordinator(
            window.ui, # Pass UI root for access to sidebar/header/viewer
            self._view_router,
            self._event_bus,
            self._asset_list_vm # Injected for invalidation
        )

        # --- Legacy Controllers ---
        self._dialog = DialogController(window, context, window.ui.status_bar)
        self._status_bar = StatusBarController(
            window.ui.status_bar,
            window.ui.progress_bar,
            window.ui.rescan_action,
            context,
        )

        # --- Binding Data to Views ---
        window.ui.grid_view.setModel(self._asset_list_vm)

        # Assign Delegate for Grid View (Fixes text display and spacing)
        self._grid_delegate = AssetGridDelegate(window.ui.grid_view, filmstrip_mode=False)
        window.ui.grid_view.setItemDelegate(self._grid_delegate)

        window.ui.filmstrip_view.setModel(self._asset_list_vm)

        # Assign Delegate for Filmstrip View
        self._filmstrip_delegate = AssetGridDelegate(window.ui.filmstrip_view, filmstrip_mode=True)
        window.ui.filmstrip_view.setItemDelegate(self._filmstrip_delegate)

        self._connect_signals()

    def start(self):
        """Start the coordinator."""
        self._logger.info("MainCoordinator started")
        self._view_router.show_gallery()

    def shutdown(self) -> None:
        """Stop worker threads and background jobs before the app exits."""
        self._status_bar.stop()
        QThreadPool.globalInstance().waitForDone()

    def _connect_signals(self) -> None:
        """Connect application signals."""
        ui = self._window.ui

        # Grid interactions
        ui.grid_view.itemClicked.connect(self._on_asset_clicked)
        ui.filmstrip_view.itemClicked.connect(self._on_asset_clicked)

        # Coordinator Signals
        self._playback.assetChanged.connect(self._sync_selection)

        # Menus
        ui.open_album_action.triggered.connect(self._handle_open_album_dialog)
        ui.edit_button.clicked.connect(self._handle_edit_clicked)
        ui.edit_rotate_left_button.clicked.connect(self._playback.rotate_current_asset)

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

        # Status Bar Connections (Restored)
        # Facade Signals -> Status Bar
        # Note: AppFacade exposes library_updates (ScannerSignals)
        updates = self._facade.library_updates
        updates.scanProgress.connect(self._status_bar.handle_scan_progress)
        updates.scanFinished.connect(self._status_bar.handle_scan_finished)
        self._facade.scanBatchFailed.connect(self._status_bar.handle_scan_batch_failed)
        self._facade.scanProgress.connect(self._status_bar.handle_scan_progress)
        self._facade.scanFinished.connect(self._status_bar.handle_scan_finished)

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
        self._favorite_shortcut = QShortcut(QKeySequence("."), window)
        self._favorite_shortcut.activated.connect(self._handle_toggle_favorite)

    def _on_asset_clicked(self, index: QModelIndex):
        self._playback.play_asset(index.row())

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

    def _handle_edit_clicked(self):
        # Trigger Edit Mode from Detail View context
        indexes = self._window.ui.grid_view.selectionModel().selectedIndexes()
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
        if not indexes:
            # Try filmstrip if grid has no selection
            indexes = self._window.ui.filmstrip_view.selectionModel().selectedIndexes()

        for idx in indexes:
            asset_id = self._asset_list_vm.data(idx, Roles.ASSET_ID)
            if asset_id:
                new_state = self._asset_service.toggle_favorite(asset_id)
                self._asset_list_vm.update_favorite(idx.row(), new_state)

    def open_album_from_path(self, path: Path):
        self._navigation.open_album(path)

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

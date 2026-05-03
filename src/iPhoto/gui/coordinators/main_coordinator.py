"""Coordinator that wires the main window to application logic.

This replaces the legacy MainController as the top-level orchestrator.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import (
    QCoreApplication,
    QItemSelectionModel,
    QModelIndex,
    QObject,
    Qt,
    QThreadPool,
)
from PySide6.QtGui import QAction

from iPhoto.application.contracts.runtime_entry_contract import RuntimeEntryContract
from iPhoto.config import RECENTLY_DELETED_DIR_NAME
from iPhoto.gui.coordinators.edit_coordinator import EditCoordinator
from iPhoto.gui.coordinators.navigation_coordinator import NavigationCoordinator
from iPhoto.gui.coordinators.playback_coordinator import PlaybackCoordinator
from iPhoto.gui.coordinators.view_router import ViewRouter
from iPhoto.gui.ui.controllers.context_menu_controller import ContextMenuController
from iPhoto.gui.ui.controllers.dialog_controller import DialogController
from iPhoto.gui.ui.controllers.export_controller import ExportController
from iPhoto.gui.ui.controllers.header_controller import HeaderController
from iPhoto.gui.ui.controllers.map_extension_download_controller import (
    MapExtensionDownloadController,
)
from iPhoto.gui.ui.controllers.preview_controller import PreviewController
from iPhoto.gui.ui.controllers.selection_controller import SelectionController
from iPhoto.gui.ui.controllers.share_controller import ShareController
from iPhoto.gui.ui.controllers.status_bar_controller import StatusBarController
from iPhoto.gui.ui.controllers.window_theme_controller import WindowThemeController
from iPhoto.gui.ui.media import MediaAdjustmentCommitter, MediaSelectionSession
from iPhoto.gui.ui.models.roles import Roles
from iPhoto.gui.ui.models.spacer_proxy_model import SpacerProxyModel
from iPhoto.gui.services.location_trash_navigation_service import (
    LocationTrashNavigationService,
)
from iPhoto.gui.services.people_service_resolver import resolve_people_service
from iPhoto.gui.services.pinned_items_service import PinnedItemsService
from iPhoto.gui.ui.widgets.asset_delegate import AssetGridDelegate
from iPhoto.gui.viewmodels.detail_viewmodel import DetailViewModel
from iPhoto.gui.viewmodels.gallery_list_model_adapter import GalleryListModelAdapter
from iPhoto.gui.viewmodels.gallery_viewmodel import GalleryViewModel
from iPhoto.people.service import PeopleService
from maps.map_sources import supports_map_extension_download

if TYPE_CHECKING:
    from iPhoto.gui.ui.main_window import MainWindow


class MainCoordinator(QObject):
    """High-level coordinator for the main window.
    Acts as the entry point and glue code for the application, initializing
    legacy controllers and bridging them with the new architecture.
    """

    def __init__(
        self,
        window: MainWindow,
        context: RuntimeEntryContract,
    ) -> None:
        super().__init__(window)
        self._window = window
        self._context = context
        # facade reference kept for signal wiring as some systems still emit through it
        self._facade = context.facade
        self._logger = logging.getLogger(__name__)
        self._media_failure_cleanup_paths: set[str] = set()
        self._map_extension_download = MapExtensionDownloadController(
            window,
            context,
            package_root=self._resolve_map_package_root(getattr(context.library, "map_runtime", None)),
        )
        if hasattr(window.ui, "download_map_extension_action"):
            window.ui.download_map_extension_action.setEnabled(supports_map_extension_download())

        self._event_bus = context.event_bus
        edit_service_getter = lambda: getattr(context.library, "edit_service", None)
        asset_state_service = getattr(context.library, "asset_state_service", None)

        # --- ViewModels Setup ---
        lib_root = context.library.root()
        self._context.asset_runtime.bind_library_root(lib_root)
        self._asset_list_vm = GalleryListModelAdapter.create(
            asset_query_service=getattr(context.library, "asset_query_service", None),
            thumbnail_service=self._context.asset_runtime.thumbnail_service,
            edit_service_getter=edit_service_getter,
            library_root=lib_root,
            parent=window.ui.grid_view,
        )
        self._gallery_store = self._asset_list_vm.store
        self._media_session = MediaSelectionSession()
        self._media_session.bind_collection(self._gallery_store)
        self._thumbnail_service = self._context.asset_runtime.thumbnail_service
        bound_people_service = resolve_people_service(
            context.library,
            library_root=lib_root,
        )
        self._playback_people_service = bound_people_service or PeopleService()
        if hasattr(window.ui, "people_page"):
            if bound_people_service is not None and hasattr(window.ui.people_page, "set_people_service"):
                window.ui.people_page.set_people_service(self._playback_people_service)
            else:
                window.ui.people_page.set_library_root(lib_root)
            window.ui.people_page.set_status_message(context.library.face_scan_status_message())
        self._pinned_items_service = PinnedItemsService(
            context.settings,
            people_service_getter=lambda root: resolve_people_service(
                context.library,
                library_root=root,
            ),
            parent=self,
        )
        window.ui.sidebar.set_pinned_service(self._pinned_items_service)
        if hasattr(window.ui, "people_page"):
            window.ui.people_page.set_pinned_service(self._pinned_items_service)
        if hasattr(window.ui, "albums_dashboard_page"):
            window.ui.albums_dashboard_page.set_pinned_service(self._pinned_items_service)
            self._facade.albumCoverUpdated.connect(
                window.ui.albums_dashboard_page.update_album_cover
            )

        # Inject ViewModel provider into Facade for legacy operations (restore/delete)
        if self._facade:
            self._facade.set_model_provider(lambda: self._asset_list_vm)

        # --- Coordinators Setup ---

        # 1. View Router
        self._view_router = ViewRouter(window.ui)
        self._location_trash_navigation_service = LocationTrashNavigationService(
            library_manager_getter=lambda: context.library,
            parent=self,
        )

        self._gallery_vm = GalleryViewModel(
            store=self._gallery_store,
            context=context,
            facade=context.facade,
            asset_state_service=asset_state_service,
            location_trash_service=self._location_trash_navigation_service,
        )

        # 2. Navigation Coordinator
        self._navigation = NavigationCoordinator(
            window.ui.sidebar,
            self._view_router,
            self._gallery_vm,
            context,
            context.facade,  # Legacy Facade Bridge
            pinned_items_service=self._pinned_items_service,
        )
        self._adjustment_committer = MediaAdjustmentCommitter(
            asset_vm=self._asset_list_vm,
            pause_watcher=self._navigation.pause_library_watcher,
            resume_watcher=self._navigation.resume_library_watcher,
            edit_service_getter=edit_service_getter,
            parent=self,
        )
        self._detail_vm = DetailViewModel(
            collection_store=self._gallery_store,
            media_session=self._media_session,
            asset_state_service=asset_state_service,
            adjustment_commit_port=self._adjustment_committer,
            edit_service_getter=edit_service_getter,
        )

        # 3. Playback Coordinator
        from iPhoto.gui.ui.controllers.player_view_controller import PlayerViewController

        self._player_view_controller = PlayerViewController(
            window.ui.player_stack,
            window.ui.image_viewer,
            window.ui.video_area,
            window.ui.player_placeholder,
            window.ui.live_badge,
            edit_service_getter=edit_service_getter,
        )
        self._header_controller = HeaderController(
            window.ui.location_label,
            window.ui.timestamp_label,
        )

        self._playback = PlaybackCoordinator(
            player_bar=window.ui.player_bar,
            player_view=self._player_view_controller,
            router=self._view_router,
            asset_model=self._asset_list_vm,
            detail_vm=self._detail_vm,
            adjustment_committer=self._adjustment_committer,
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
            face_name_overlay=window.ui.face_name_overlay,
            people_service=self._playback_people_service,
            people_dashboard_refresh_callback=window.ui.people_page.schedule_index_refresh,
            library_manager=context.library,
            location_session_invalidator=self._gallery_vm.invalidate_location_session,
            map_runtime=getattr(context.library, "map_runtime", None),
        )

        # Inject optional dependencies into Playback
        self._playback.set_navigation_coordinator(self._navigation)
        self._navigation.set_playback_coordinator(self._playback)
        context.library.peopleSnapshotCommitted.connect(
            self._handle_people_snapshot_sidebar_refresh
        )
        # Manually attach info panel if available
        if hasattr(window.ui, "info_panel"):
            window.ui.info_panel.set_map_runtime(getattr(context.library, "map_runtime", None))
            self._playback.set_info_panel(window.ui.info_panel)
            window.ui.info_panel.downloadMapExtensionRequested.connect(
                lambda: self._map_extension_download.start_download(source="info_panel")
            )
        if hasattr(window.ui, "map_view"):
            window.ui.map_view.set_map_runtime(getattr(context.library, "map_runtime", None))
            window.ui.map_view.set_map_interaction_service(
                getattr(context.library, "map_interaction_service", None)
            )

        # 4. Theme Controller
        self._theme_controller = WindowThemeController(window.ui, window, context.theme)

        # 5. Edit Coordinator
        self._edit = EditCoordinator(
            window.ui,  # Pass UI root for access to sidebar/header/viewer
            self._view_router,
            self._event_bus,
            self._asset_list_vm,  # Injected for invalidation
            window,
            self._theme_controller,
            self._navigation,
            self._media_session,
            self._adjustment_committer,
            edit_service_getter,
        )

        # --- Legacy Controllers ---
        self._dialog = DialogController(window, context, window.ui.status_bar)
        self._facade.register_restore_prompt(self._dialog.prompt_restore_to_root)
        self._status_bar = StatusBarController(
            window.ui.status_bar,
            window.ui.progress_bar,
            window.ui.rescan_action,
            context,
        )

        self._share_controller = ShareController(
            settings=context.settings,
            current_path_provider=self._detail_vm.current_asset_path,
            status_bar=self._status_bar,
            notification_toast=window.ui.notification_toast,
            share_button=window.ui.share_button,
            share_action_group=window.ui.share_action_group,
            copy_file_action=window.ui.share_action_copy_file,
            copy_path_action=window.ui.share_action_copy_path,
            reveal_action=window.ui.share_action_reveal_file,
            edit_service_getter=edit_service_getter,
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
            format_group=window.ui.export_format_group,
            format_jpg=window.ui.export_format_jpg,
            format_png=window.ui.export_format_png,
            format_tiff=window.ui.export_format_tiff,
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

        self._preview_controller = PreviewController(
            window.ui.preview_window,
            edit_service_getter=edit_service_getter,
        )
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
            selected_paths_provider=self._gallery_vm.paths_for_rows,
            facade=self._facade,
            status_bar=self._status_bar,
            notification_toast=window.ui.notification_toast,
            selection_controller=self._selection_controller,
            navigation=self._navigation,
            export_callback=window.ui.export_selected_action.trigger,
            prepare_paths_for_mutation=self._prepare_paths_for_mutation,
            gallery_viewmodel=self._gallery_vm,
            parent=self,
        )

        # --- Centralised shortcut manager ---
        # All window-level shortcuts are owned and dispatched here.
        # See: src/iPhoto/gui/ui/shortcuts/app_shortcut_manager.py
        from iPhoto.gui.ui.shortcuts.app_shortcut_manager import AppShortcutManager

        self._shortcut_manager = AppShortcutManager(
            window,
            self._view_router,
            toggle_favorite_cb=self._detail_vm.toggle_favorite,
            exit_fullscreen_cb=window.exit_fullscreen,
            parent=self,
        )
        self._shortcut_manager.set_video_area(window.ui.video_area)
        self._shortcut_manager.set_edit_coordinator(self._edit)

        self._connect_signals()

    def start(self):
        """Start the coordinator."""
        self._logger.info("MainCoordinator started")
        self._view_router.show_gallery()
        self._map_extension_download.maybe_prompt_on_startup()

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

        self._context.asset_runtime.shutdown()

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
        self._context.library.treeUpdated.connect(self._on_library_tree_updated)
        self._context.library.albumRenamed.connect(self._on_album_renamed)
        self._facade.scanChunkReady.connect(self._gallery_store.handle_scan_chunk)
        self._facade.scanFinished.connect(self._gallery_store.handle_scan_finished)
        self._context.library.scanChunkReady.connect(self._gallery_vm.handle_location_scan_chunk)
        self._context.library.scanFinished.connect(self._gallery_vm.handle_location_scan_finished)
        self._gallery_vm.message_requested.connect(self._status_bar.show_message)

        # Grid interactions
        ui.grid_view.itemClicked.connect(self._on_asset_clicked)
        ui.grid_view.visibleRowsChanged.connect(self._asset_list_vm.prioritize_rows)

        # Filmstrip clicks are now handled by PlaybackCoordinator

        # Connect favorite click from grid view
        if hasattr(ui.grid_view, "favoriteClicked"):
            ui.grid_view.favoriteClicked.connect(self._on_favorite_clicked)

        # Coordinator Signals
        self._playback.assetChanged.connect(self._sync_selection)
        self._player_view_controller.imageLoadingFailed.connect(self._handle_media_load_failed)
        ui.video_area.mediaLoadFailed.connect(self._handle_media_load_failed)

        # Viewer Interactions (Wheel Navigation)
        ui.image_viewer.nextItemRequested.connect(self._playback.select_next)
        ui.image_viewer.prevItemRequested.connect(self._playback.select_previous)
        ui.video_area.nextItemRequested.connect(self._playback.select_next)
        ui.video_area.prevItemRequested.connect(self._playback.select_previous)

        # Map view cluster interactions
        if hasattr(ui, "map_view") and ui.map_view is not None:
            ui.map_view.assetActivated.connect(self._on_map_asset_activated)
            ui.map_view.clusterActivated.connect(self._on_cluster_activated)

        # Menus
        ui.open_album_action.triggered.connect(self._handle_open_album_dialog)
        ui.rescan_action.triggered.connect(self._status_bar.begin_scan)
        ui.rescan_action.triggered.connect(self._gallery_vm.rescan_current)
        ui.download_map_extension_action.triggered.connect(
            lambda: self._map_extension_download.start_download(source="settings")
        )
        ui.edit_button.clicked.connect(self._detail_vm.request_edit)
        # ui.edit_rotate_left_button is handled by EditCoordinator in Edit Mode
        ui.rotate_left_button.clicked.connect(self._playback.rotate_current_asset)
        ui.favorite_button.clicked.connect(self._detail_vm.toggle_favorite)
        ui.toggle_face_names_action.toggled.connect(self._handle_face_name_toggle_changed)
        ui.toggle_hidden_people_action.toggled.connect(self._handle_hidden_people_toggle_changed)

        # Info Button
        if hasattr(ui, "info_button"):
            ui.info_button.clicked.connect(self._playback.toggle_info_panel)

        # Back Button (detail page)
        if hasattr(ui, "back_button"):
            ui.back_button.clicked.connect(self._detail_vm.back_to_gallery)

        # Gallery page back button for cluster gallery mode
        if hasattr(ui, "gallery_page") and hasattr(ui.gallery_page, "backRequested"):
            ui.gallery_page.backRequested.connect(self._gallery_vm.return_from_cluster_gallery)

        # Dashboard Click
        if hasattr(ui, "albums_dashboard_page"):
            ui.albums_dashboard_page.albumSelected.connect(self.open_album_from_path)
        if hasattr(ui, "people_page"):
            ui.people_page.clusterActivated.connect(self._on_people_cluster_activated)
            ui.people_page.groupActivated.connect(self._on_people_group_activated)
            self._context.library.peopleIndexUpdated.connect(ui.people_page.schedule_index_refresh)
            self._context.library.peopleSnapshotCommitted.connect(
                self._gallery_vm.handle_people_snapshot_committed
            )
            self._context.library.peopleSnapshotCommitted.connect(
                self._playback.handle_people_snapshot_committed
            )
            self._context.library.faceScanStatusChanged.connect(ui.people_page.set_status_message)

        # Navigation
        self._navigation.bindLibraryRequested.connect(self._dialog.bind_library_dialog)
        ui.bind_library_action.triggered.connect(self._dialog.bind_library_dialog)
        self._detail_vm.edit_requested.connect(self._edit.enter_edit_mode)

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
        move_service.moveFinished.connect(self._handle_move_finished_toast)

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

        # Note: keyboard shortcuts are now managed centrally by
        # AppShortcutManager, which is created in __init__ after all
        # coordinators are initialised.  Do not add QShortcut instances here.

    def _on_library_tree_updated(self) -> None:
        root = self._context.library.root()
        self._logger.debug("_on_library_tree_updated: root=%s", root)
        self._context.asset_runtime.bind_library_root(root)
        self._asset_list_vm.rebind_asset_query_service(
            getattr(self._context.library, "asset_query_service", None),
            root,
        )
        asset_state_service = getattr(self._context.library, "asset_state_service", None)
        self._gallery_vm.bind_asset_state_service(asset_state_service)
        self._detail_vm.bind_asset_state_service(asset_state_service)
        self._gallery_vm.on_library_tree_updated()
        window = getattr(self, "_window", None)
        ui = getattr(window, "ui", None)
        people_page = getattr(ui, "people_page", None)
        bound_people_service = resolve_people_service(
            self._context.library,
            library_root=root,
        )
        if bound_people_service is not None:
            self._playback_people_service = bound_people_service
        if people_page is not None:
            if bound_people_service is not None and hasattr(people_page, "set_people_service"):
                people_page.set_people_service(bound_people_service)
            else:
                people_page.set_library_root(root)
            people_page.set_status_message(self._context.library.face_scan_status_message())
        map_runtime = getattr(self._context.library, "map_runtime", None)
        map_interaction_service = getattr(
            self._context.library,
            "map_interaction_service",
            None,
        )
        self._map_extension_download.set_package_root(
            self._resolve_map_package_root(map_runtime)
        )
        if ui is not None and hasattr(ui, "map_view"):
            ui.map_view.set_map_runtime(map_runtime)
            ui.map_view.set_map_interaction_service(map_interaction_service)
        if ui is not None and hasattr(ui, "info_panel"):
            ui.info_panel.set_map_runtime(map_runtime)
        playback = getattr(self, "_playback", None)
        if playback is not None:
            playback.set_map_runtime(map_runtime)
            if bound_people_service is not None and hasattr(playback, "set_people_service"):
                playback.set_people_service(bound_people_service)
            else:
                playback.set_people_library_root(root)

    @staticmethod
    def _resolve_map_package_root(map_runtime: object | None) -> Path:
        package_root_getter = getattr(map_runtime, "package_root", None)
        if callable(package_root_getter):
            try:
                package_root = package_root_getter()
            except Exception:
                package_root = None
            if package_root is not None:
                return Path(package_root).resolve()

        package_root = getattr(map_runtime, "_package_root", None)
        if package_root is not None:
            return Path(package_root).resolve()
        return Path(__file__).resolve().parents[3] / "maps"

    def _on_album_renamed(self, old_path: Path, new_path: Path) -> None:
        self._pinned_items_service.remap_album_path(
            old_path,
            new_path,
            library_root=self._context.library.root(),
            fallback_label=new_path.name,
        )
        self._thumbnail_service.remap_album_paths(old_path, new_path)
        self._gallery_vm.handle_album_renamed(old_path, new_path)

    def _handle_people_snapshot_sidebar_refresh(self, event: object) -> None:
        library_root = self._context.library.root()
        if (
            library_root is not None
            and getattr(event, "library_root", None) == library_root
        ):
            self._pinned_items_service.prune_missing_people_entities(
                library_root,
                person_ids=tuple(getattr(event, "changed_person_ids", ()) or ()),
                group_ids=tuple(getattr(event, "changed_group_ids", ()) or ()),
                person_redirects=dict(getattr(event, "person_redirects", {}) or {}),
                group_redirects=dict(getattr(event, "group_redirects", {}) or {}),
            )
        self._window.ui.sidebar.refresh_tree_model()

    def _handle_move_finished_toast(
        self,
        source: Path,
        destination: Path,
        success: bool,
        message: str,
    ) -> None:
        """Show the lightweight completion toast for successful ordinary moves."""

        del message
        if not success or self._is_recently_deleted_move(source, destination):
            return

        self._window.ui.notification_toast.show_toast("Moved")

    def _is_recently_deleted_move(self, source: Path, destination: Path) -> bool:
        """Return whether a move completion belongs to delete or restore flows."""

        trash_root = self._context.library.deleted_directory()
        if trash_root is not None:
            return self._paths_equal(source, trash_root) or self._paths_equal(
                destination,
                trash_root,
            )
        return (
            source.name == RECENTLY_DELETED_DIR_NAME
            or destination.name == RECENTLY_DELETED_DIR_NAME
        )

    def _paths_equal(self, first: Path, second: Path) -> bool:
        """Return ``True`` when *first* and *second* refer to the same location."""

        try:
            first_resolved = first.resolve()
        except OSError:
            first_resolved = first
        try:
            second_resolved = second.resolve()
        except OSError:
            second_resolved = second
        return first_resolved == second_resolved

    def _handle_media_load_failed(self, path: Path, message: str) -> None:
        path_key = str(path)
        if path_key in self._media_failure_cleanup_paths:
            return

        self._media_failure_cleanup_paths.add(path_key)
        try:
            self._dialog.show_error(f"File not found or unreadable: {path.name}\n\n{message}")
            facade = getattr(self, "_facade", None)
            updates = getattr(facade, "library_updates", None)
            if updates is None:
                return

            refresh_root = updates.handle_media_load_failure(path)
            if refresh_root is not None:
                self._gallery_store.reload_current_selection()
        finally:
            self._media_failure_cleanup_paths.discard(path_key)

    def _on_asset_clicked(self, index: QModelIndex):
        if self._selection_controller and self._selection_controller.is_active():
            return
        self._gallery_vm.open_row(index.row())

    def _on_favorite_clicked(self, index: QModelIndex):
        self._gallery_vm.toggle_favorite_row(index.row())

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

    def _on_cluster_activated(self, assets: list):
        """Handle cluster click from map view to open cluster gallery.

        This is triggered when the user clicks a cluster with multiple assets
        on the map. Opens a gallery showing all assets in the cluster.
        """
        self._navigation.open_cluster_gallery(assets)

    def _on_map_asset_activated(self, rel: str) -> None:
        """Handle single-asset map activation inside the Location context."""

        self._navigation.open_location_asset(rel)

    def _on_people_cluster_activated(self, person_id: str) -> None:
        query = self._window.ui.people_page.build_cluster_query(person_id)
        if not query.asset_ids:
            return
        self._gallery_vm.open_people_cluster_gallery(
            query,
            kind="person",
            entity_id=person_id,
        )
        self._view_router.show_gallery()

    def _on_people_group_activated(self, group_id: str) -> None:
        query = self._window.ui.people_page.build_group_query(group_id)
        if not query.asset_ids:
            return
        self._gallery_vm.open_people_cluster_gallery(
            query,
            kind="group",
            entity_id=group_id,
        )
        self._view_router.show_gallery()

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

        stored_face_names = settings.get("ui.show_face_names_in_detail", False)
        if isinstance(stored_face_names, str):
            show_face_names = stored_face_names.strip().lower() in {"1", "true", "yes", "on"}
        else:
            show_face_names = bool(stored_face_names)
        ui.toggle_face_names_action.setChecked(show_face_names)
        self._playback.set_face_name_display_enabled(show_face_names)

        stored_hidden_people = settings.get("ui.show_hidden_people", False)
        if isinstance(stored_hidden_people, str):
            show_hidden_people = stored_hidden_people.strip().lower() in {"1", "true", "yes", "on"}
        else:
            show_hidden_people = bool(stored_hidden_people)
        ui.toggle_hidden_people_action.setChecked(show_hidden_people)
        if hasattr(ui, "people_page"):
            ui.people_page.set_show_hidden_people(show_hidden_people)

        # 2. Volume / Mute
        stored_volume = settings.get("ui.volume", 75)
        try:
            initial_volume = round(float(stored_volume))
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

    def _handle_face_name_toggle_changed(self, checked: bool) -> None:
        if self._context.settings.get("ui.show_face_names_in_detail") != checked:
            self._context.settings.set("ui.show_face_names_in_detail", checked)
        self._playback.set_face_name_display_enabled(checked)

    def _handle_hidden_people_toggle_changed(self, checked: bool) -> None:
        if self._context.settings.get("ui.show_hidden_people") != checked:
            self._context.settings.set("ui.show_hidden_people", checked)
        if hasattr(self._window.ui, "people_page"):
            self._window.ui.people_page.set_show_hidden_people(checked)

    def _prepare_paths_for_mutation(self, paths: list[Path]) -> None:
        """Release preview/player handles before mutating files on disk."""

        self._preview_controller.close_preview(False)

        current_path = self._detail_vm.current_asset_path()
        if current_path is None:
            return

        current_key = self._normalise_path_key(current_path)
        selected_keys = {
            key for key in (self._normalise_path_key(path) for path in paths) if key is not None
        }
        if current_key is not None and current_key in selected_keys:
            self._playback.reset_for_gallery()

    def _normalise_path_key(self, path: Path) -> str | None:
        try:
            return str(path.resolve())
        except OSError:
            return str(path)

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

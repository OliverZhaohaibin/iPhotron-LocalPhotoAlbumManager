"""Desktop coordinator runtime and application composition root."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import (
    QCoreApplication,
    QItemSelectionModel,
    QModelIndex,
    QObject,
    Qt,
    QThreadPool,
    QTimer,
)
from PySide6.QtGui import QAction

from iPhoto.application.contracts.runtime_entry_contract import RuntimeEntryContract
from iPhoto.bootstrap.startup_profile import mark
from iPhoto.config import RECENTLY_DELETED_DIR_NAME
from iPhoto.events.asset_events import AssetMetadataUpdated
from iPhoto.gui.coordinators.navigation_coordinator import NavigationCoordinator
from iPhoto.gui.coordinators.playback_coordinator import PlaybackCoordinator
from iPhoto.gui.coordinators.view_router import ViewRouter
from iPhoto.gui.services.location_trash_navigation_service import (
    LocationTrashNavigationService,
)
from iPhoto.gui.services.people_service_resolver import resolve_people_service
from iPhoto.gui.services.pinned_items_service import PinnedItemsService
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
from iPhoto.gui.ui.models.spacer_proxy_model import SpacerProxyModel
from iPhoto.gui.ui.models.thumbnail_surface_proxy_model import ThumbnailSurfaceProxyModel
from iPhoto.gui.ui.widgets.asset_delegate import AssetGridDelegate
from iPhoto.gui.viewmodels.detail_viewmodel import DetailViewModel
from iPhoto.gui.viewmodels.gallery_list_model_adapter import GalleryListModelAdapter
from iPhoto.gui.viewmodels.gallery_viewmodel import GalleryViewModel
from maps.map_sources import supports_map_extension_download

if TYPE_CHECKING:
    from iPhoto.gui.coordinators.edit_coordinator import EditCoordinator
    from iPhoto.gui.ui.main_window import MainWindow


def _mark_domain_duration(domain: str, started_ns: int) -> None:
    mark(
        "startup.coordinator_domain.finished",
        domain=domain,
        duration_ms=round((time.perf_counter_ns() - started_ns) / 1_000_000.0, 3),
    )


class DesktopCoordinatorRuntime(QObject):
    """Compose desktop coordinators and own their shared lifecycle."""

    def __init__(
        self,
        window: MainWindow,
        context: RuntimeEntryContract,
    ) -> None:
        super().__init__(window)
        self._window = window
        self._context = context
        self._observed_library_epoch = self._context_library_epoch()
        self._is_shutting_down = False
        self._shutdown_complete = False
        # facade reference kept for signal wiring as some systems still emit through it
        self._facade = context.facade
        self._logger = logging.getLogger(__name__)
        self._media_failure_cleanup_paths: set[str] = set()
        self._people_view_activation_bound = False
        self._map_extension_download = MapExtensionDownloadController(
            window,
            context,
            package_root=self._resolve_map_package_root(None),
            on_bundled_install_ready=self._handle_bundled_map_install_ready,
        )
        if hasattr(window.ui, "download_map_extension_action"):
            window.ui.download_map_extension_action.setEnabled(False)

        self._event_bus = context.event_bus
        edit_service_getter = self._edit_service
        self._edit_service_getter = edit_service_getter
        asset_state_service = self._asset_state_service()
        gallery_started_ns = time.perf_counter_ns()

        # --- ViewModels Setup ---
        lib_root = self._library_root()
        self._context.asset_runtime.bind_library_root(lib_root)
        self._asset_list_vm = GalleryListModelAdapter.create(
            asset_query_service=self._asset_query_service(),
            thumbnail_service=self._context.asset_runtime.thumbnail_service,
            edit_service_getter=edit_service_getter,
            library_root=lib_root,
            parent=window.ui.grid_view,
        )
        self._gallery_store = self._asset_list_vm.store
        self._media_session = MediaSelectionSession()
        self._media_session.bind_collection(self._gallery_store)
        self._thumbnail_service = self._context.asset_runtime.thumbnail_service
        bound_people_service = None
        bound_pet_service = None
        self._playback_people_service = None
        self._playback_pet_service = None
        self._pinned_items_service = PinnedItemsService(
            context.settings,
            people_service_getter=self._people_service,
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
        self._location_info = None
        self._recognition = None
        self._asset_metadata_subscription = self._event_bus.subscribe(
            AssetMetadataUpdated,
            self._handle_asset_metadata_updated,
        )

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
        _mark_domain_duration("gallery", gallery_started_ns)
        detail_started_ns = time.perf_counter_ns()
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
            library_root_getter=self._library_root,
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
            people_service=bound_people_service,
            pet_service=bound_pet_service,
            people_dashboard_refresh_callback=self._schedule_people_dashboard_refresh,
            library_manager=context.library,
            location_session_invalidator=self._gallery_vm.invalidate_location_session,
            map_runtime=None,
            event_bus=self._event_bus,
            location_write_queue=None,
            edit_service_getter=edit_service_getter,
            library_epoch_getter=self._context_library_epoch,
        )

        # Inject optional dependencies into Playback
        self._playback.set_navigation_coordinator(self._navigation)
        context.library.peopleSnapshotCommitted.connect(
            self._handle_people_snapshot_sidebar_refresh
        )
        context.library.petSnapshotCommitted.connect(
            self._handle_people_snapshot_sidebar_refresh
        )
        if hasattr(window.ui, "map_view"):
            window.ui.map_view.set_map_runtime(None)
            window.ui.map_view.set_map_interaction_service(None)
        _mark_domain_duration("detail_playback", detail_started_ns)
        shell_started_ns = time.perf_counter_ns()

        # 4. Theme Controller
        stage_started_ns = time.perf_counter_ns()
        self._theme_controller = WindowThemeController(
            window.ui,
            window,
            context.theme,
            apply_initial_theme=False,
        )
        _mark_domain_duration("desktop_shell.theme", stage_started_ns)

        # The edit graph imports CPU/JIT rendering backends.  Construct it on
        # first edit/fullscreen use, never in the Gallery startup turn.
        self._edit: EditCoordinator | None = None

        # --- Legacy Controllers ---
        self._dialog = DialogController(
            window,
            context,
            window.ui.status_bar,
            library_rebind_preflight=self._library_rebind_preflight,
        )
        self._facade.register_restore_prompt(self._dialog.prompt_restore_to_root)
        self._status_bar = StatusBarController(
            window.ui.status_bar,
            window.ui.progress_bar,
            window.ui.rescan_action,
            context,
        )
        _mark_domain_duration("desktop_shell.dialog_status", stage_started_ns)

        stage_started_ns = time.perf_counter_ns()
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
        _mark_domain_duration("desktop_shell.share", stage_started_ns)

        stage_started_ns = time.perf_counter_ns()
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
        _mark_domain_duration("desktop_shell.export", stage_started_ns)
        _mark_domain_duration("desktop_shell.controllers", shell_started_ns)
        gallery_ui_started_ns = time.perf_counter_ns()

        # --- Binding Data to Views ---
        window.ui.grid_view.setModel(self._asset_list_vm)

        # Assign Delegate for Grid View (Fixes text display and spacing)
        self._grid_delegate = AssetGridDelegate(window.ui.grid_view, filmstrip_mode=False)
        window.ui.grid_view.setItemDelegate(self._grid_delegate)

        window.ui.grid_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        # Use SpacerProxyModel for Filmstrip to allow centering of first/last items
        self._filmstrip_thumbnail_proxy = ThumbnailSurfaceProxyModel(
            "filmstrip",
            window.ui.filmstrip_view,
        )
        self._filmstrip_thumbnail_proxy.setSourceModel(self._asset_list_vm)
        self._filmstrip_proxy = SpacerProxyModel(window.ui.filmstrip_view)
        self._filmstrip_proxy.setSourceModel(self._filmstrip_thumbnail_proxy)
        window.ui.filmstrip_view.setModel(self._filmstrip_proxy)

        # Assign Delegate for Filmstrip View
        self._filmstrip_delegate = AssetGridDelegate(window.ui.filmstrip_view, filmstrip_mode=True)
        window.ui.filmstrip_view.setItemDelegate(self._filmstrip_delegate)

        self._preview_controller = PreviewController(
            edit_service_getter=edit_service_getter,
            preview_window_provider=self._ensure_preview_window,
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
        _mark_domain_duration("desktop_shell.gallery_ui", gallery_ui_started_ns)
        bindings_started_ns = time.perf_counter_ns()

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

        from iPhoto.gui.coordinators.detail_coordinator import DetailCoordinator
        from iPhoto.gui.coordinators.gallery_coordinator import GalleryCoordinator

        self.gallery = GalleryCoordinator(
            context=context,
            facade=self._facade,
            navigation=self._navigation,
            asset_model=self._asset_list_vm,
            gallery_viewmodel=self._gallery_vm,
            library_root_getter=self._library_root,
            asset_query_service_getter=self._asset_query_service,
            asset_state_service_getter=self._asset_state_service,
            library_rebind_preflight=self._library_rebind_preflight,
            parent=self,
        )
        self.detail = DetailCoordinator(
            router=self._view_router,
            playback=self._playback,
            edit_provider=self._ensure_edit_coordinator,
            detail_viewmodel=self._detail_vm,
            asset_state_service_getter=self._asset_state_service,
            parent=self,
        )
        self._navigation.set_detail_navigation_port(self.detail)

        self._connect_signals()
        window.ui.featureCreated.connect(self._on_feature_created)
        _mark_domain_duration("desktop_shell.bindings", bindings_started_ns)
        _mark_domain_duration("desktop_shell", shell_started_ns)

    def start(self):
        """Start the coordinator."""
        self._logger.info("DesktopCoordinatorRuntime started")
        self._theme_controller.apply_current_theme()
        self._view_router.show_gallery()

    def enable_detail_interaction_warmup(self) -> None:
        """Enable demand-driven Detail imports after startup is complete."""

        self._player_view_controller.enable_interaction_warmup()

    # ------------------------------------------------------------------
    # Lazy Edit lifecycle used by the Detail immersive port
    # ------------------------------------------------------------------
    def _ensure_edit_coordinator(self) -> EditCoordinator:
        if self._edit is not None:
            return self._edit
        from iPhoto.gui.coordinators.edit_coordinator import EditCoordinator

        self._window.ui.ensure_detail_edit_bundle()
        self._theme_controller.apply_current_theme()
        self._edit = EditCoordinator(
            self._window.ui,
            self._view_router,
            self._event_bus,
            self._asset_list_vm,
            self._window,
            self._theme_controller,
            self._navigation,
            self._media_session,
            self._adjustment_committer,
            self._edit_service_getter,
            self._player_view_controller,
            zoom_handler=self._playback.zoom_handler,
        )
        self._edit.stillEditFinished.connect(self._playback.handle_still_edit_finished)
        self._edit.editUnavailable.connect(self._status_bar.show_message)
        self._shortcut_manager.set_edit_coordinator(self._edit)
        return self._edit

    def _enter_edit_mode(self, *args) -> None:
        self._ensure_edit_coordinator().enter_edit_mode(*args)

    def _library_rebind_preflight(self) -> bool:
        edit = getattr(self, "_edit", None)
        if edit is None:
            return True
        preflight = getattr(edit, "preflight_library_rebind", None)
        return bool(preflight()) if callable(preflight) else not edit.is_editing()

    def _handle_asset_metadata_updated(self, event: AssetMetadataUpdated) -> None:
        asset_path = event.asset_path
        if asset_path is None:
            return
        metadata = dict(event.metadata_delta or {})
        if event.gps:
            metadata["gps"] = dict(event.gps)
        if event.location:
            metadata["location"] = event.location
            metadata.setdefault("location_name", event.location)
        path = Path(asset_path)
        existing_metadata = self._asset_list_vm.metadata_for_path(path) or {}
        merged_metadata = dict(existing_metadata)
        merged_metadata.update(metadata)
        row = self._gallery_store.row_for_path(path)
        if row is not None:
            self._gallery_store.update_asset_metadata(row, merged_metadata)
        try:
            self._context.library.invalidate_geotagged_assets_cache(emit_tree_updated=False)
        except Exception:  # noqa: BLE001
            self._logger.warning("Failed to invalidate geotagged assets cache", exc_info=True)
        try:
            self._gallery_vm.invalidate_location_session()
        except Exception:  # noqa: BLE001
            self._logger.warning("Failed to invalidate location session", exc_info=True)

    def shutdown(self) -> None:
        """Stop worker threads and background jobs before the app exits."""
        if self._shutdown_complete or self._is_shutting_down:
            return
        self._is_shutting_down = True

        location_info = getattr(self, "_location_info", None)
        try:
            if location_info is not None:
                location_info.drain()

            # 1. Stop UI-owned workers and widgets before their QObject graph is destroyed.
            if self._playback:
                self._playback.shutdown()

            recognition = getattr(self, "_recognition", None)
            if recognition is not None:
                recognition.shutdown()
            people_page = getattr(self._window.ui, "people_page", None)
            shutdown_people = getattr(people_page, "shutdown", None)
            if callable(shutdown_people):
                shutdown_people()

            if self._edit:
                self._edit.shutdown()

            if hasattr(self._window.ui, "preview_window"):
                try:
                    self._window.ui.preview_window.close_preview(False)
                except AttributeError:
                    self._window.ui.preview_window.close()

            if hasattr(self._window.ui, "map_view"):
                map_view = self._window.ui.map_view
                try:
                    shutdown = getattr(map_view, "shutdown", None)
                    if callable(shutdown):
                        shutdown()
                    map_view.close()
                except RuntimeError:
                    self._logger.warning(
                        "Failed to close map view during shutdown",
                        exc_info=True,
                    )

            # 2. Cancel active scans/imports and close library-scoped runtime services.
            if self._facade:
                self._facade.cancel_active_scans()
            if self._context and self._context.library:
                self._context.library.shutdown()
            if self._context:
                self._context.close_library()
                asset_runtime = getattr(self._context, "_asset_runtime", None)
                asset_runtime_shutdown = getattr(asset_runtime, "shutdown", None)
                if callable(asset_runtime_shutdown):
                    asset_runtime_shutdown()

            if location_info is not None:
                location_info.shutdown()

            event_bus = getattr(self._context, "event_bus", None)
            event_bus_shutdown = getattr(event_bus, "shutdown", None)
            if callable(event_bus_shutdown):
                event_bus_shutdown()

            # 3. Wait briefly for background threads (e.g. thumbnail generation) to finish.
            thread_pool = QThreadPool.globalInstance()
            if not thread_pool.waitForDone(2000):
                thread_pool.clear()
        finally:
            self._shutdown_complete = True
            self._is_shutting_down = False

    def _handle_thumbnail_surface_visibility(self, surface_id: str, view, visible: bool) -> None:
        if not visible:
            self._asset_list_vm.release_viewport_surface(surface_id)
            return
        QTimer.singleShot(0, view.schedule_viewport_publish)

    def _connect_signals(self) -> None:
        """Connect application signals."""
        ui = self._window.ui
        updates = self._facade.library_updates
        self._context.library.treeUpdated.connect(self._on_library_tree_updated)
        self._context.library.albumRenamed.connect(self._on_album_renamed)
        # Library watcher rescans still emit through the bound LibraryRuntimeController,
        # while facade-initiated rescans emit through LibraryUpdateService.
        self._context.library.scanBatchCommitted.connect(self._asset_list_vm.handle_scan_batch)
        self._context.library.scanBatchCommitted.connect(
            self._gallery_vm.handle_location_scan_batch
        )
        self._context.library.scanFinished.connect(self._gallery_store.handle_scan_finished)
        self._context.library.scanFinished.connect(self._gallery_vm.handle_location_scan_finished)
        updates.scanBatchCommitted.connect(self._asset_list_vm.handle_scan_batch)
        updates.scanBatchCommitted.connect(self._gallery_vm.handle_location_scan_batch)
        updates.scanFinished.connect(self._gallery_store.handle_scan_finished)
        updates.scanFinished.connect(self._gallery_vm.handle_location_scan_finished)
        self._gallery_vm.message_requested.connect(self._status_bar.show_message)
        self._facade.assetReloadRequested.connect(self._handle_asset_reload_requested)

        # Grid interactions
        ui.grid_view.itemClicked.connect(self._on_asset_clicked)
        ui.grid_view.viewportStateChanged.connect(self._asset_list_vm.update_viewport)
        ui.filmstrip_view.viewportStateChanged.connect(self._asset_list_vm.update_viewport)
        ui.grid_view.viewportVisibilityChanged.connect(
            lambda visible: self._handle_thumbnail_surface_visibility(
                "gallery", ui.grid_view, visible
            )
        )
        ui.filmstrip_view.viewportVisibilityChanged.connect(
            lambda visible: self._handle_thumbnail_surface_visibility(
                "filmstrip", ui.filmstrip_view, visible
            )
        )
        if hasattr(ui.grid_view, "detailPrefetchRequested"):
            ui.grid_view.detailPrefetchRequested.connect(
                self._playback.prefetch_descriptor
            )

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
            ui.info_button.clicked.connect(self._toggle_info_panel)

        # Back Button (detail page)
        if hasattr(ui, "back_button"):
            ui.back_button.clicked.connect(self._detail_vm.back_to_gallery)

        # Gallery page back button for cluster gallery mode
        if hasattr(ui, "gallery_page") and hasattr(ui.gallery_page, "backRequested"):
            ui.gallery_page.backRequested.connect(self._gallery_vm.return_from_cluster_gallery)

        # Dashboard Click
        if hasattr(ui, "albums_dashboard_page"):
            ui.albums_dashboard_page.albumSelected.connect(
                lambda path: self.gallery.open_album_from_path(path)
            )
        if hasattr(ui, "people_page"):
            ui.people_page.clusterActivated.connect(self._on_people_cluster_activated)
            ui.people_page.groupActivated.connect(self._on_people_group_activated)
            if hasattr(ui.people_page, "petActivated"):
                ui.people_page.petActivated.connect(self._on_pet_activated)
            self._context.library.peopleIndexUpdated.connect(ui.people_page.schedule_index_refresh)
            self._context.library.petIndexUpdated.connect(ui.people_page.schedule_index_refresh)
            self._context.library.peopleSnapshotCommitted.connect(
                self._gallery_vm.handle_people_snapshot_committed
            )
            self._context.library.petSnapshotCommitted.connect(
                self._gallery_vm.handle_people_snapshot_committed
            )
            self._context.library.peopleSnapshotCommitted.connect(
                self._playback.handle_people_snapshot_committed
            )
            self._context.library.petSnapshotCommitted.connect(
                self._playback.handle_people_snapshot_committed
            )
            self._context.library.faceScanStatusChanged.connect(ui.people_page.set_status_message)
            if hasattr(ui.people_page, "set_pet_status_message"):
                self._context.library.petScanStatusChanged.connect(
                    ui.people_page.set_pet_status_message
                )

        # Navigation
        self._navigation.bindLibraryRequested.connect(self._dialog.bind_library_dialog)
        ui.bind_library_action.triggered.connect(self._dialog.bind_library_dialog)
        self._detail_vm.edit_requested.connect(self._enter_edit_mode)

        # Preferences (Wheel, Volume) - Filmstrip handled in PlaybackCoordinator
        self._restore_preferences()
        ui.wheel_action_group.triggered.connect(self._handle_wheel_action_changed)

        # Status Bar Connections (Restored)
        # Facade Signals -> Status Bar
        # Note: AppFacade exposes library_updates (ScannerSignals)
        updates.scanProgress.connect(self._status_bar.handle_scan_progress)
        updates.scanFinished.connect(self._status_bar.handle_scan_finished)
        self._facade.scanBatchFailed.connect(self._status_bar.handle_scan_batch_failed)
        self._facade.scanProgress.connect(self._status_bar.handle_scan_progress)
        self._facade.scanFinished.connect(self._status_bar.handle_scan_finished)
        self._asset_list_vm.thumbnailBackfillProgress.connect(
            self._status_bar.handle_thumbnail_backfill_progress
        )
        self._asset_list_vm.thumbnailBackfillCompleted.connect(
            self._status_bar.handle_thumbnail_backfill_completed
        )
        self._asset_list_vm.thumbnailBackfillFailed.connect(
            self._status_bar.handle_thumbnail_backfill_failed
        )

        self._facade.loadStarted.connect(self._status_bar.handle_load_started)
        self._facade.loadProgress.connect(self._status_bar.handle_load_progress)
        self._facade.loadFinished.connect(self._status_bar.handle_load_finished)

        self._facade.importStarted.connect(self._status_bar.handle_import_started)
        self._facade.importProgress.connect(self._status_bar.handle_import_progress)
        self._facade.importFinished.connect(self._status_bar.handle_import_finished)

        self._facade.moveStarted.connect(self._status_bar.handle_move_started)
        self._facade.moveProgress.connect(self._status_bar.handle_move_progress)
        self._facade.moveFinished.connect(self._status_bar.handle_move_finished)
        self._facade.moveFinished.connect(self._handle_move_finished_toast)
        self._facade.moveFinished.connect(self._handle_move_finished_pending_cleanup)
        self._facade.moveCompletedDetailed.connect(self._handle_move_completed_pending_cleanup)

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

        # Language Switching
        ui.language_group.triggered.connect(self._handle_language_action_triggered)
        self._context.settings.settingsChanged.connect(self._handle_settings_changed)
        self._sync_language_actions()

        # Note: keyboard shortcuts are now managed centrally by
        # AppShortcutManager, which is created in __init__ after all
        # coordinators are initialised.  Do not add QShortcut instances here.

    def _on_feature_created(self, feature: str, widget: object) -> None:
        """Wire optional pages that are constructed on their first visit."""

        ui = self._window.ui
        if feature == "map":
            map_view = getattr(ui, "map_view", None)
            if map_view is None:
                return
            self._activate_map_services()
            map_runtime = self._map_runtime()
            self._map_extension_download.set_package_root(
                self._resolve_map_package_root(map_runtime)
            )
            if hasattr(ui, "download_map_extension_action"):
                ui.download_map_extension_action.setEnabled(
                    supports_map_extension_download()
                )
            installing_bundled_extension = (
                self._map_extension_download.maybe_prompt_on_startup()
            )
            if not installing_bundled_extension:
                map_view.set_map_runtime(map_runtime)
            map_view.set_map_interaction_service(self._map_interaction_service())
            map_view.assetActivated.connect(self._on_map_asset_activated)
            map_view.clusterActivated.connect(self._on_cluster_activated)
            return

        if feature == "albums":
            dashboard = getattr(ui, "albums_dashboard_page", widget)
            dashboard.set_pinned_service(self._pinned_items_service)
            dashboard.albumSelected.connect(
                lambda path: self.gallery.open_album_from_path(path)
            )
            self._facade.albumCoverUpdated.connect(dashboard.update_album_cover)
            return

        if feature == "people":
            self._bind_people_feature(widget)

    def _handle_bundled_map_install_ready(self) -> None:
        """Refresh map capabilities after the background bundled install."""

        if self._is_shutting_down:
            return
        map_view = getattr(self._window.ui, "map_view", None)
        if map_view is None:
            return
        map_runtime = self._map_runtime()
        refresh = getattr(map_runtime, "refresh", None)
        if callable(refresh):
            refresh()
        map_view.set_map_runtime(map_runtime)

    def _toggle_info_panel(self) -> None:
        self._ensure_location_info_coordinator().toggle()

    def _ensure_location_info_coordinator(self):
        current = getattr(self, "_location_info", None)
        if current is not None:
            return current
        from iPhoto.gui.coordinators.location_info_coordinator import (
            LocationInfoCoordinator,
        )

        self._location_info = LocationInfoCoordinator(
            window=self._window,
            event_bus=self._event_bus,
            detail=self.detail,
            map_runtime_getter=self._map_runtime,
            package_root_resolver=self._resolve_map_package_root,
            map_extension_download=self._map_extension_download,
            library_root_getter=self._library_root,
            recognition_provider=self._ensure_recognition_coordinator,
            parent=self,
        )
        return self._location_info

    def _ensure_recognition_coordinator(self):
        current = getattr(self, "_recognition", None)
        if current is not None:
            return current
        from iPhoto.gui.coordinators.recognition_coordinator import RecognitionCoordinator

        self._recognition = RecognitionCoordinator(
            context=self._context,
            detail=self.detail,
            pinned_items_service=self._pinned_items_service,
            library_root_getter=self._library_root,
            people_service_getter=self._people_service,
            pet_service_getter=self._pet_service,
            recognition_query_getter=self._recognition_query_service,
            recognition_merge_getter=self._recognition_merge_service,
            recognition_edit_getter=self._recognition_edit_service,
            cluster_callback=self._on_people_cluster_activated,
            group_callback=self._on_people_group_activated,
            pet_callback=self._on_pet_activated,
            parent=self,
        )
        return self._recognition

    def _ensure_preview_window(self):
        self._window.ui.ensure_feature("preview")
        return self._window.ui.preview_window

    def _schedule_people_dashboard_refresh(self) -> None:
        people_page = getattr(self._window.ui, "people_page", None)
        if people_page is not None:
            people_page.schedule_index_refresh()

    def _bind_people_feature(self, people_page: object) -> None:
        """Attach the People dashboard only when the user first opens it."""
        recognition = self._ensure_recognition_coordinator()
        recognition.warm_dashboard_snapshot()
        recognition.bind_people_page(people_page)
        if not self._people_view_activation_bound:
            self._people_view_activation_bound = True
            self._view_router.peopleViewShown.connect(recognition.people_view_shown)

    def _on_library_tree_updated(self) -> None:
        root = self._library_root()
        self._logger.debug("_on_library_tree_updated: root=%s", root)
        library_epoch = DesktopCoordinatorRuntime._context_library_epoch(self)
        previous_epoch = getattr(self, "_observed_library_epoch", library_epoch)
        session_changed = library_epoch != previous_epoch
        self._observed_library_epoch = library_epoch
        if session_changed:
            edit = getattr(self, "_edit", None)
            invalidate = getattr(edit, "invalidate_library_binding", None)
            if callable(invalidate):
                invalidate()
        self.gallery.rebind_library()
        self.detail.rebind_library(
            library_epoch,
            session_changed=session_changed,
        )
        window = getattr(self, "_window", None)
        ui = getattr(window, "ui", None)
        map_feature_active = ui is not None and hasattr(ui, "map_view")
        if map_feature_active:
            self._activate_map_services()
        map_runtime = self._map_runtime() if map_feature_active else None
        map_interaction_service = (
            self._map_interaction_service() if map_feature_active else None
        )
        if map_feature_active:
            self._map_extension_download.set_package_root(
                self._resolve_map_package_root(map_runtime)
            )
            ui.map_view.set_map_runtime(map_runtime)
            ui.map_view.set_map_interaction_service(map_interaction_service)
        recognition = getattr(self, "_recognition", None)
        if recognition is not None:
            recognition.rebind_library()
        location_info = getattr(self, "_location_info", None)
        if location_info is not None:
            location_info.rebind_library()

    def _active_session(self):
        return getattr(self._context, "library_session", None)

    def _context_library_epoch(self) -> int:
        value = getattr(self._context, "library_epoch", 0)
        if isinstance(value, bool):
            return int(value)
        if not isinstance(value, int):
            return 0
        return max(0, value)

    def _library_root(self) -> Path | None:
        session = self._active_session()
        if session is not None:
            return getattr(session, "library_root", None)
        return self._context.library.root()

    def _asset_query_service(self):
        session = self._active_session()
        if session is not None:
            return getattr(session, "asset_queries", None)
        return getattr(self._context.library, "asset_query_service", None)

    def _asset_state_service(self):
        session = self._active_session()
        if session is not None:
            return getattr(session, "asset_state", None)
        return getattr(self._context.library, "asset_state_service", None)

    def _edit_service(self):
        session = self._active_session()
        if session is not None:
            return getattr(session, "edit", None)
        return getattr(self._context.library, "edit_service", None)

    def _people_service(self, library_root: Path | None = None):
        session = self._active_session()
        session_root = getattr(session, "library_root", None) if session is not None else None
        if session is not None and (library_root is None or session_root == library_root):
            return getattr(session, "people", None)
        return resolve_people_service(
            self._context.library,
            library_root=library_root,
        )

    def _pet_service(self, library_root: Path | None = None):
        session = self._active_session()
        session_root = getattr(session, "library_root", None) if session is not None else None
        if session is not None and (library_root is None or session_root == library_root):
            return getattr(session, "pets", None)
        service = getattr(self._context.library, "pet_service", None)
        if service is not None:
            bound_root = getattr(service, "library_root", lambda: None)()
            if library_root is None or bound_root == library_root:
                return service
        return None

    def _recognition_query_service(self, library_root: Path | None = None):
        session = self._active_session()
        session_root = getattr(session, "library_root", None) if session is not None else None
        if session is not None and (library_root is None or session_root == library_root):
            return getattr(session, "recognition_queries", None)
        return None

    def _recognition_merge_service(self, library_root: Path | None = None):
        session = self._active_session()
        session_root = getattr(session, "library_root", None) if session is not None else None
        if session is not None and (library_root is None or session_root == library_root):
            return getattr(session, "recognition_merges", None)
        return None

    def _recognition_edit_service(self, library_root: Path | None = None):
        session = self._active_session()
        session_root = getattr(session, "library_root", None) if session is not None else None
        if session is not None and (library_root is None or session_root == library_root):
            return getattr(session, "recognition_edits", None)
        return None

    def _map_runtime(self):
        session = self._active_session()
        if session is not None:
            return getattr(session, "maps", None)
        return getattr(self._context.library, "map_runtime", None)

    def _map_interaction_service(self):
        session = self._active_session()
        if session is not None:
            return getattr(session, "map_interactions", None)
        return getattr(self._context.library, "map_interaction_service", None)

    def _activate_map_services(self) -> None:
        session = self._active_session()
        if session is None:
            return
        location_service = getattr(session, "locations", None)
        map_runtime = getattr(session, "maps", None)
        map_interaction_service = getattr(session, "map_interactions", None)
        activate = getattr(self._context.library, "activate_map_services", None)
        if callable(activate):
            activate(location_service, map_runtime, map_interaction_service)
            return
        for name, service in (
            ("bind_location_service", location_service),
            ("bind_map_runtime", map_runtime),
            ("bind_map_interaction_service", map_interaction_service),
        ):
            binder = getattr(self._context.library, name, None)
            if callable(binder):
                binder(service)

    @staticmethod
    def _resolve_map_package_root(map_runtime: object | None) -> Path:
        package_root_getter = getattr(map_runtime, "package_root", None)
        if callable(package_root_getter):
            try:
                package_root = package_root_getter()
            except Exception:  # noqa: BLE001 - optional adapter fallback
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
        if library_root is not None and getattr(event, "library_root", None) == library_root:
            prune_kwargs: dict[str, object] = {
                "person_ids": tuple(getattr(event, "changed_person_ids", ()) or ()),
                "group_ids": tuple(getattr(event, "changed_group_ids", ()) or ()),
                "person_redirects": dict(getattr(event, "person_redirects", {}) or {}),
                "group_redirects": dict(getattr(event, "group_redirects", {}) or {}),
            }
            if hasattr(event, "changed_pet_ids"):
                prune_kwargs["pet_ids"] = tuple(getattr(event, "changed_pet_ids", ()) or ())
            if hasattr(event, "pet_redirects"):
                prune_kwargs["pet_redirects"] = dict(getattr(event, "pet_redirects", {}) or {})
            self._pinned_items_service.prune_missing_people_entities(library_root, **prune_kwargs)
        self._window.ui.sidebar.refresh_tree_model()
        recognition = getattr(self, "_recognition", None)
        if recognition is not None:
            recognition.handle_snapshot_committed(event)

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

        self._window.ui.notification_toast.show_toast(
            QCoreApplication.translate("MainCoordinator", "Moved", None),
        )

    def _handle_move_finished_pending_cleanup(
        self,
        _source: Path,
        _destination: Path,
        success: bool,
        _message: str,
    ) -> None:
        if success:
            return
        rollback = getattr(self._asset_list_vm, "rollback_pending_moves", None)
        if callable(rollback):
            rollback()

    def _handle_move_completed_pending_cleanup(
        self,
        _source_root: Path,
        _destination_root: Path,
        moved_pairs_raw: list,
        source_ok: bool,
        destination_ok: bool,
        _is_trash_destination: bool,
        _is_restore_operation: bool,
    ) -> None:
        if not moved_pairs_raw:
            return
        if not (source_ok and destination_ok):
            rollback = getattr(self._asset_list_vm, "rollback_pending_moves", None)
            if callable(rollback):
                rollback()
            return
        paths: list[Path] = []
        for entry in moved_pairs_raw:
            if isinstance(entry, (tuple, list)) and len(entry) == 2:
                paths.extend([Path(entry[0]), Path(entry[1])])
        clear_pending = getattr(self._asset_list_vm, "clear_pending_moves_for_paths", None)
        if callable(clear_pending):
            clear_pending(paths)

    def _handle_asset_reload_requested(
        self,
        root: Path,
        _announce_index: bool,
        _force_reload: bool,
    ) -> None:
        if not self._current_gallery_selection_contains_root(root):
            return
        self._gallery_store.reload_current_selection()

    def _current_gallery_selection_contains_root(self, root: Path) -> bool:
        active_root = self._gallery_store.active_root()
        if active_root is None:
            return False
        if self._paths_equal(active_root, root):
            return True
        library_root = self._gallery_store.library_root()
        query = self._gallery_store.current_query()
        if (
            library_root is not None
            and query is not None
            and query.album_path is None
            and self._paths_equal(active_root, library_root)
            and self._path_is_descendant(root, library_root)
        ):
            return True
        return False

    @staticmethod
    def _path_is_descendant(path: Path, root: Path) -> bool:
        try:
            Path(path).resolve().relative_to(Path(root).resolve())
        except (OSError, ValueError):
            return False
        return True

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
        if self._view_router.is_gallery_view_active():
            self._window.ui.grid_view.scrollTo(idx)

    def _handle_open_album_dialog(self):
        path = self._dialog.open_album_dialog()
        if path:
            self.gallery.open_album_from_path(path)

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

    def _on_pet_activated(self, pet_id: str) -> None:
        query = self._window.ui.people_page.build_pet_query(pet_id)
        if not query.asset_ids:
            return
        self._gallery_vm.open_people_cluster_gallery(
            query,
            kind="pet",
            entity_id=pet_id,
        )
        self._view_router.show_gallery()

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
        if show_face_names:
            self._ensure_recognition_coordinator().set_face_name_display_enabled(True)
        else:
            self.detail.set_face_name_display_enabled(False)

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

    def _handle_language_action_triggered(self, action: QAction) -> None:
        language = action.data()
        if not isinstance(language, str):
            return
        if self._context.settings.get("ui.language", "system") != language:
            self._context.settings.set("ui.language", language)

    def _handle_settings_changed(self, key: str, value: object) -> None:
        if key == "ui.language":
            self._sync_language_actions(str(value or "system"))

    def _sync_language_actions(self, language: str | None = None) -> None:
        ui = self._window.ui
        selected = language or str(self._context.settings.get("ui.language", "system") or "system")
        for action in ui.language_group.actions():
            action.setChecked(action.data() == selected)

    def _handle_face_name_toggle_changed(self, checked: bool) -> None:
        if self._context.settings.get("ui.show_face_names_in_detail") != checked:
            self._context.settings.set("ui.show_face_names_in_detail", checked)
        self._ensure_recognition_coordinator().set_face_name_display_enabled(checked)

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

__all__ = ["DesktopCoordinatorRuntime"]

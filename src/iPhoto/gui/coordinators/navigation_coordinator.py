"""Coordinator for handling sidebar navigation and album loading."""

from __future__ import annotations

import threading
import time
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Optional

from PySide6.QtCore import QObject, QTimer, Signal

from src.iPhoto.events.bus import EventBus
from src.iPhoto.application.services.album_service import AlbumService
from src.iPhoto.gui.coordinators.view_router import ViewRouter
from src.iPhoto.gui.ui.widgets.album_sidebar import AlbumSidebar
from src.iPhoto.gui.viewmodels.asset_list_viewmodel import AssetListViewModel
from src.iPhoto.config import RECENTLY_DELETED_DIR_NAME
from src.iPhoto.domain.models.query import AssetQuery
from src.iPhoto.domain.models.core import MediaType
from src.iPhoto.errors import AlbumOperationError

# Use legacy imports for context/facade compatibility until full migration
from src.iPhoto.appctx import AppContext
from src.iPhoto.gui.facade import AppFacade

if TYPE_CHECKING:
    from src.iPhoto.gui.coordinators.playback_coordinator import PlaybackCoordinator

LOGGER = logging.getLogger(__name__)


class NavigationCoordinator(QObject):
    """
    Handles interactions from the AlbumSidebar and orchestrates album loading.
    Replaces NavigationController.
    """

    bindLibraryRequested = Signal()

    _TRASH_CLEANUP_DELAY_MS = 750
    _TRASH_CLEANUP_THROTTLE_SEC = 300.0

    def __init__(
        self,
        sidebar: AlbumSidebar,
        router: ViewRouter,
        album_service: AlbumService,
        asset_vm: AssetListViewModel,
        event_bus: EventBus,
        # Legacy Dependencies
        context: AppContext,
        facade: AppFacade
    ):
        super().__init__()
        self._sidebar = sidebar
        self._router = router
        self._album_service = album_service
        self._asset_vm = asset_vm
        self._event_bus = event_bus
        self._context = context
        self._facade = facade

        self._static_selection: Optional[str] = None
        self._playback_coordinator: Optional[PlaybackCoordinator] = None

        # Trash Cleanup State
        self._trash_cleanup_running = False
        self._trash_cleanup_lock = threading.Lock()
        self._last_trash_cleanup_at: Optional[float] = None

        # Tree Suppression State
        self._suppress_tree_refresh = False
        self._tree_refresh_suppression_reason: Optional[Literal["edit", "operation"]] = None

        self._connect_signals()

    def set_playback_coordinator(self, coordinator: PlaybackCoordinator):
        self._playback_coordinator = coordinator

    def _connect_signals(self):
        self._sidebar.albumSelected.connect(self.open_album)
        self._sidebar.allPhotosSelected.connect(self.open_all_photos)
        self._sidebar.staticNodeSelected.connect(self._handle_static_node)
        self._sidebar.bindLibraryRequested.connect(self._handle_bind_library)

        # Watcher integration
        self._context.library.treeUpdated.connect(self.handle_tree_updated)

    def open_album(self, path: Path):
        """Loads an album and switches to gallery view."""
        if self._should_treat_as_refresh(path):
            return

        self._reset_playback()
        self._static_selection = None
        self._router.show_gallery()

        # Legacy Facade call to maintain backend state synchronization
        album = self._facade.open_album(path)
        if album:
            self._context.remember_album(album.root)
            self._sidebar.select_path(album.root)
        else:
            self._sidebar.select_path(path)

        # Update ViewModel
        # When opening an album folder, we usually want to see its contents.
        # SQLiteAssetRepository logic for album_path usually implies parent_album_path match.
        # For legacy behavior, we often want subalbums if it's a folder structure.
        # Let's set include_subalbums=True implicitly for file-system browsing behavior.
        query = AssetQuery(album_path=album.root.name if album else str(path.name))
        query.include_subalbums = True  # Ensure recursive view by default
        self._asset_vm.load_query(query)

    def open_all_photos(self):
        """Loads all photos."""
        self._reset_playback()
        self._router.show_gallery()
        self._static_selection = AlbumSidebar.ALL_PHOTOS_TITLE

        query = AssetQuery()  # No filters = All Photos
        self._asset_vm.load_query(query)

    def _handle_static_node(self, name: str):
        normalized = name.casefold()

        if normalized == "all photos":
            self.open_all_photos()
        elif normalized == "recently deleted":
            self.open_recently_deleted()
        elif normalized == "albums":
            self._reset_playback()
            self._router.show_albums_dashboard()
            self._static_selection = "Albums"
        elif normalized == "favorites":
            self._open_filtered_collection(name, is_favorite=True)
        elif normalized == "videos":
            self._open_filtered_collection(name, media_types=[MediaType.VIDEO])
        elif normalized == "live photos":
            self._open_filtered_collection(name, media_types=[MediaType.LIVE_PHOTO])

    def open_recently_deleted(self):
        """Open the trash collection."""
        root = self._context.library.root()
        if not root:
            self._handle_bind_library()
            return

        # Throttle cleanup
        self._schedule_trash_cleanup()

        try:
            deleted_root = self._context.library.ensure_deleted_directory()
        except AlbumOperationError as exc:
            LOGGER.error(f"Failed to open trash: {exc}")
            return

        self._reset_playback()
        self._router.show_gallery()
        self._static_selection = "Recently Deleted"

        # Legacy open
        self._facade.open_album(deleted_root)

        # ViewModel Update
        query = AssetQuery(album_path=RECENTLY_DELETED_DIR_NAME)
        self._asset_vm.load_query(query)

    def _open_filtered_collection(self, title: str, is_favorite=None, media_types=None):
        self._reset_playback()
        self._router.show_gallery()
        self._static_selection = title

        query = AssetQuery()
        if is_favorite:
            query.is_favorite = True

        if media_types:
            query.media_types = media_types

        self._asset_vm.load_query(query)

    def _handle_bind_library(self):
        self.bindLibraryRequested.emit()

    # --- Logic Ported from NavigationController ---

    def _should_treat_as_refresh(self, path: Path) -> bool:
        # Check if re-opening same album to avoid UI flicker
        if self._facade.current_album and self._facade.current_album.root.resolve() == path.resolve():
            return self._router.is_gallery_view_active()
        return False

    def _reset_playback(self):
        if self._playback_coordinator:
            self._playback_coordinator.reset_for_gallery()

    def _schedule_trash_cleanup(self):
        def _cleanup():
            try:
                self._context.library.cleanup_deleted_index()
            finally:
                with self._trash_cleanup_lock:
                    self._trash_cleanup_running = False

        with self._trash_cleanup_lock:
            should_start = not self._trash_cleanup_running and self._should_run_trash_cleanup()
            if should_start:
                self._trash_cleanup_running = True
                self._last_trash_cleanup_at = time.monotonic()

        if should_start:
            threading.Thread(target=_cleanup, daemon=True, name="trash-cleanup").start()

    def _should_run_trash_cleanup(self) -> bool:
        if self._last_trash_cleanup_at is None: return True
        return (time.monotonic() - self._last_trash_cleanup_at) >= self._TRASH_CLEANUP_THROTTLE_SEC

    # --- Tree Suppression Logic ---

    def handle_tree_updated(self):
        if self._router.is_edit_view_active():
            self._suppress_tree_refresh = True
            self._tree_refresh_suppression_reason = "edit"
            return

        if self._tree_refresh_suppression_reason == "edit" and self._suppress_tree_refresh:
            return

        # Check background ops via facade
        # if self._facade.is_performing_background_operation(): ...
        pass

    def suppress_tree_refresh_for_edit(self):
        self._suppress_tree_refresh = True
        self._tree_refresh_suppression_reason = "edit"

    def should_suppress_tree_refresh(self) -> bool:
        return self._suppress_tree_refresh

    def release_tree_refresh_suppression_if_edit(self):
        if self._tree_refresh_suppression_reason == "edit":
            self._suppress_tree_refresh = False
            self._tree_refresh_suppression_reason = None

    def clear_tree_refresh_suppression(self):
        self._suppress_tree_refresh = False
        self._tree_refresh_suppression_reason = None

    def suspend_library_watcher(self, duration: int = 250):
        manager = self._context.library
        manager.pause_watcher()
        QTimer.singleShot(duration, manager.resume_watcher)

    # --- Accessors ---

    def static_selection(self) -> Optional[str]:
        return self._static_selection

    def is_all_photos_view(self) -> bool:
        return self._static_selection and self._static_selection.casefold() == AlbumSidebar.ALL_PHOTOS_TITLE.casefold()

    def is_recently_deleted_view(self) -> bool:
        return bool(self._static_selection) and self._static_selection.casefold() == "recently deleted"

    def sidebar_model(self):
        return self._sidebar.tree_model()

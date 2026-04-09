"""Coordinator for handling sidebar navigation and album loading."""

from __future__ import annotations

import threading
import time
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal

from iPhoto.application.contracts.runtime_entry_contract import RuntimeEntryContract
from iPhoto.gui.coordinators.location_selection_session import LocationSelectionSession
from iPhoto.gui.coordinators.view_router import ViewRouter
from iPhoto.gui.ui.widgets.album_sidebar import AlbumSidebar
from iPhoto.gui.viewmodels.asset_list_viewmodel import AssetListViewModel
from iPhoto.config import RECENTLY_DELETED_DIR_NAME
from iPhoto.domain.models.query import AssetQuery
from iPhoto.domain.models.core import MediaType
from iPhoto.errors import AlbumOperationError
from iPhoto.gui.facade import AppFacade

if TYPE_CHECKING:
    from iPhoto.gui.coordinators.playback_coordinator import PlaybackCoordinator

LOGGER = logging.getLogger(__name__)


class _LocationAssetsSignals(QObject):
    """Signals emitted by the background geotagged-assets worker."""

    finished = Signal(int, object, list, float)
    failed = Signal(int, object, str)


class _LocationAssetsWorker(QRunnable):
    """Load geotagged assets off the UI thread so map navigation stays snappy."""

    def __init__(self, request_serial: int, root: Path, library) -> None:
        super().__init__()
        self._request_serial = int(request_serial)
        self._root = root
        self._library = library
        self.signals = _LocationAssetsSignals()

    def run(self) -> None:  # type: ignore[override]
        started = time.perf_counter()
        try:
            assets = list(self._library.get_geotagged_assets())
        except Exception as exc:  # pragma: no cover - defensive background propagation
            self.signals.failed.emit(self._request_serial, self._root, str(exc))
            return
        self.signals.finished.emit(
            self._request_serial,
            self._root,
            assets,
            (time.perf_counter() - started) * 1000.0,
        )


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
        asset_vm: AssetListViewModel,
        context: RuntimeEntryContract,
        facade: AppFacade
    ):
        super().__init__()
        self._sidebar = sidebar
        self._router = router
        self._asset_vm = asset_vm
        self._context = context
        self._facade = facade

        self._static_selection: Optional[str] = None
        self._playback_coordinator: Optional[PlaybackCoordinator] = None
        self._location_session = LocationSelectionSession()
        self._location_assets_pool = QThreadPool.globalInstance()
        self._location_assets_worker: _LocationAssetsWorker | None = None

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
        self._clear_cluster_gallery_mode()
        self._location_session.set_mode("inactive")
        self._static_selection = None
        self._router.show_gallery()

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
        active_root = album.root if album else path
        query = AssetQuery(album_path=self._album_path_for_query(active_root))
        query.include_subalbums = True  # Ensure recursive view by default
        self._asset_vm.load_selection(active_root, query=query)

    def open_all_photos(self):
        """Loads all photos."""
        LOGGER.debug("open_all_photos: library_root=%s", self._context.library.root())
        self._reset_playback()
        self._clear_cluster_gallery_mode()
        self._location_session.set_mode("inactive")
        self._router.show_gallery()
        self._static_selection = AlbumSidebar.ALL_PHOTOS_TITLE

        query = AssetQuery()  # No filters = All Photos
        self._asset_vm.load_selection(self._context.library.root(), query=query)

    def _handle_static_node(self, name: str):
        normalized = name.casefold()

        if normalized == "all photos":
            self.open_all_photos()
        elif normalized == "recently deleted":
            self.open_recently_deleted()
        elif normalized == "albums":
            self._reset_playback()
            self._clear_cluster_gallery_mode()
            self._location_session.set_mode("inactive")
            self._router.show_albums_dashboard()
            self._static_selection = "Albums"
        elif normalized == "favorites":
            self._open_filtered_collection(name, is_favorite=True)
        elif normalized == "videos":
            self._open_filtered_collection(name, media_types=[MediaType.VIDEO])
        elif normalized == "live photos":
            self._open_filtered_collection(name, media_types=[MediaType.LIVE_PHOTO])
        elif normalized == "location":
            self.open_location_view()

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
        self._clear_cluster_gallery_mode()
        self._location_session.set_mode("inactive")
        self._router.show_gallery()
        self._static_selection = "Recently Deleted"

        self._facade.open_album(deleted_root)

        # ViewModel Update
        query = AssetQuery(album_path=RECENTLY_DELETED_DIR_NAME)
        self._asset_vm.load_selection(deleted_root, query=query)

    def _open_filtered_collection(self, title: str, is_favorite=None, media_types=None):
        self._reset_playback()
        self._clear_cluster_gallery_mode()
        self._location_session.set_mode("inactive")
        self._router.show_gallery()
        self._static_selection = title

        query = AssetQuery()
        if is_favorite:
            query.is_favorite = True

        if media_types:
            query.media_types = media_types

        self._asset_vm.load_selection(self._context.library.root(), query=query)

    def open_location_view(self) -> None:
        """Display the map view populated with geotagged assets."""

        root = self._context.library.root()
        if root is None:
            self._handle_bind_library()
            return

        self._reset_playback()
        self._clear_cluster_gallery_mode()
        self._static_selection = "Location"
        self._location_session.set_mode("map")
        self._asset_vm.set_active_root(root)
        self._router.show_map()
        map_view = self._router.map_view()
        if map_view is not None:
            if (
                self._location_session.root == root
                and self._location_session.has_snapshot
                and not self._location_session.invalidated
            ):
                map_view.set_assets(self._location_session.full_assets(), root)
                return
            map_view.clear()
        request_serial = self._location_session.begin_load(root)
        QTimer.singleShot(
            0,
            lambda root=root, request_serial=request_serial: self._populate_location_view(root, request_serial),
        )

    def open_cluster_gallery(self, assets: list) -> None:
        """Open gallery view for a clicked map cluster.

        This method displays a gallery containing all media in the provided
        cluster. The sidebar remains on the Location section, and the gallery
        shows a back button to return to the map view.

        The operation achieves O(1) performance because the assets are already
        aggregated during the clustering phase - no database queries required.

        Args:
            assets: List of GeotaggedAsset objects from the clicked cluster.
        """
        root = self._context.library.root()
        if root is None:
            self._handle_bind_library()
            return

        self._reset_playback()
        # Keep the static selection as "Location" so the sidebar stays highlighted
        # on the Location section even when showing the gallery
        self._static_selection = "Location"
        self._location_session.set_mode("cluster_gallery")

        # Load the cluster assets directly - O(1) operation
        self._asset_vm.load_selection(root, direct_assets=assets, library_root=root)

        # Enable cluster gallery mode on gallery page (shows back button)
        gallery_page = self._router.gallery_page()
        if gallery_page is not None:
            gallery_page.set_cluster_gallery_mode(True)

        # Switch to gallery view
        self._router.show_gallery()

    def return_to_map_from_cluster_gallery(self) -> None:
        """Return from cluster gallery view to the map view.

        Called when the back button in the cluster gallery is clicked.
        Restores the map view while keeping the Location section selected.
        """
        if not self._location_session.is_cluster_gallery():
            return

        self._clear_cluster_gallery_mode()
        root = self._context.library.root()
        if root is None:
            return

        self._static_selection = "Location"
        self._location_session.set_mode("map")
        self._asset_vm.set_active_root(root)
        self._router.show_map()

        map_view = self._router.map_view()
        if map_view is not None:
            if (
                self._location_session.root == root
                and self._location_session.has_snapshot
                and not self._location_session.invalidated
            ):
                map_view.set_assets(self._location_session.full_assets(), root)
                return
            map_view.clear()

        request_serial = self._location_session.begin_load(root)
        QTimer.singleShot(
            0,
            lambda root=root, request_serial=request_serial: self._populate_location_view(root, request_serial),
        )

    def is_in_cluster_gallery(self) -> bool:
        """Return True if currently viewing a cluster gallery from the map."""
        return self._location_session.is_cluster_gallery()

    def open_location_asset(self, rel: str) -> None:
        """Open a single marker inside the full Location gallery context."""

        root = self._context.library.root()
        if root is None:
            self._handle_bind_library()
            return

        if (
            self._location_session.root != root
            or not self._location_session.has_snapshot
            or self._location_session.invalidated
        ):
            LOGGER.warning("Location asset requested without an active geotagged snapshot: %s", rel)
            return

        resolved_path = self._location_session.resolve_relative(rel)
        if resolved_path is None:
            LOGGER.warning("Unable to resolve Location asset from current snapshot: %s", rel)
            return

        full_assets = self._location_session.full_assets()
        self._static_selection = "Location"
        self._location_session.set_mode("gallery")
        self._asset_vm.load_selection(root, direct_assets=full_assets, library_root=root)

        row = self._asset_vm.row_for_path(resolved_path)
        if row is None:
            LOGGER.warning("Resolved Location asset is missing from gallery selection: %s", resolved_path)
            return
        if self._playback_coordinator is None:
            LOGGER.warning("Playback coordinator unavailable for Location asset: %s", resolved_path)
            return
        self._playback_coordinator.play_asset(row)

    def _album_path_for_query(self, path: Path) -> Optional[str]:
        library_root = self._context.library.root()
        if library_root is None:
            return path.name
        try:
            rel = path.resolve().relative_to(library_root.resolve())
        except (OSError, ValueError):
            try:
                rel = path.relative_to(library_root)
            except ValueError:
                return path.name
        rel_str = rel.as_posix()
        if rel_str in ("", "."):
            return None
        return rel_str

    def _handle_bind_library(self):
        self.bindLibraryRequested.emit()

    # --- Logic Ported from NavigationController ---

    def _should_treat_as_refresh(self, path: Path) -> bool:
        # Check if re-opening same album to avoid UI flicker.
        # When _static_selection is set we are viewing a non-album section
        # (e.g. "All Photos", "Favorites"), so navigating to an album is never
        # a refresh even if the facade still references the same path.
        if self._static_selection is not None:
            return False
        if self._facade.current_album and self._facade.current_album.root.resolve() == path.resolve():
            return self._router.is_gallery_view_active()
        return False

    def _reset_playback(self):
        if self._playback_coordinator:
            self._playback_coordinator.reset_for_gallery()

    def _populate_location_view(self, root: Path, request_serial: int) -> None:
        """Start background loading of geotagged assets for the visible map view."""

        if request_serial != self._location_session.request_serial:
            return
        if self._static_selection != "Location":
            return
        if self._context.library.root() != root:
            return
        if self._location_session.root != root:
            return

        map_view = self._router.map_view()
        if map_view is None:
            LOGGER.warning("Map view is unavailable; cannot display location section.")
            return

        worker = _LocationAssetsWorker(request_serial, root, self._context.library)
        worker.signals.finished.connect(self._handle_location_assets_loaded)
        worker.signals.failed.connect(self._handle_location_assets_failed)
        self._location_assets_worker = worker
        self._location_assets_pool.start(worker)

    def _handle_location_assets_loaded(
        self,
        request_serial: int,
        root: object,
        assets: list,
        _fetch_elapsed_ms: float,
    ) -> None:
        root_path = Path(root)
        if not self._location_session.accept_loaded(request_serial, root_path, assets):
            self._location_assets_worker = None
            return
        if self._context.library.root() != root_path:
            self._location_assets_worker = None
            return

        map_view = self._router.map_view()
        if map_view is None:
            self._location_assets_worker = None
            return

        map_view.set_assets(self._location_session.full_assets(), root_path)
        self._location_assets_worker = None

    def _handle_location_assets_failed(self, request_serial: int, root: object, message: str) -> None:
        root_path = Path(root)
        if request_serial != self._location_session.request_serial:
            self._location_assets_worker = None
            return
        if self._location_session.root != root_path:
            self._location_assets_worker = None
            return
        LOGGER.warning(
            "Failed to load geotagged assets for Location view at %s: %s",
            root_path,
            message,
        )
        self._location_assets_worker = None

    def _clear_cluster_gallery_mode(self) -> None:
        """Exit cluster gallery mode and hide the back button header.

        Called by every navigation method that leaves the cluster gallery so
        the header with the back button does not remain visible when the user
        switches to another view (e.g. All Photos, Favorites, Albums, …).
        """
        if not self._location_session.is_cluster_gallery():
            return
        next_mode: Literal["inactive", "map"] = "map" if self._static_selection == "Location" else "inactive"
        self._location_session.set_mode(next_mode)
        gallery_page = self._router.gallery_page()
        if gallery_page is not None:
            gallery_page.set_cluster_gallery_mode(False)

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
        self._location_session.invalidate()
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

    def invalidate_location_session(self) -> None:
        self._location_session.invalidate()

    def is_location_context_active(self) -> bool:
        return self._location_session.mode != "inactive"

    def suspend_library_watcher(self, duration: int = 250):
        manager = self._context.library
        manager.pause_watcher()
        QTimer.singleShot(duration, manager.resume_watcher)

    def pause_library_watcher(self) -> None:
        manager = self._context.library
        manager.pause_watcher()

    def resume_library_watcher(self) -> None:
        manager = self._context.library
        manager.resume_watcher()

    # --- Accessors ---

    def static_selection(self) -> Optional[str]:
        return self._static_selection

    def is_all_photos_view(self) -> bool:
        return self._static_selection and self._static_selection.casefold() == AlbumSidebar.ALL_PHOTOS_TITLE.casefold()

    def is_recently_deleted_view(self) -> bool:
        return bool(self._static_selection) and self._static_selection.casefold() == "recently deleted"

    def sidebar_model(self):
        return self._sidebar.tree_model()

"""Coordinate album navigation and sidebar selections."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional, TYPE_CHECKING

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMainWindow

# Support both package-style and legacy ``src`` imports during GUI
# bootstrap.
try:  # pragma: no cover - path-sensitive import
    from ...appctx import AppContext
except ImportError:  # pragma: no cover - executed in script mode
    from src.iPhoto.appctx import AppContext
from ...facade import AppFacade
from ....errors import AlbumOperationError
from ..models.asset_model import AssetModel
from ..widgets.album_sidebar import AlbumSidebar
from .dialog_controller import DialogController
from .view_controller import ViewController

if TYPE_CHECKING:  # pragma: no cover - runtime import cycle guard
    from ..widgets.chrome_status_bar import ChromeStatusBar
    from .playback_controller import PlaybackController


class NavigationController:
    """Handle opening albums and switching between static collections."""

    def __init__(
        self,
        context: AppContext,
        facade: AppFacade,
        asset_model: AssetModel,
        sidebar: AlbumSidebar,
        status_bar: ChromeStatusBar,
        dialog: DialogController,
        view_controller: ViewController,
        main_window: QMainWindow,
        playback_controller: "PlaybackController" | None = None,
    ) -> None:
        self._context = context
        self._facade = facade
        self._asset_model = asset_model
        self._sidebar = sidebar
        self._status = status_bar
        self._dialog = dialog
        self._view_controller = view_controller
        self._main_window = main_window
        # ``PlaybackController`` is injected lazily so the main controller can
        # finish instantiating the playback stack before wiring the navigation
        # callbacks.  When ``None`` the helper simply skips the playback reset.
        self._playback_controller: "PlaybackController" | None = playback_controller
        self._static_selection: Optional[str] = None
        # ``_last_open_was_refresh`` records whether ``open_album`` most recently
        # reissued the currently open album.  When ``True`` the main window can
        # keep the detail pane visible rather than reverting to the gallery.
        self._last_open_was_refresh: bool = False
        # ``_suppress_tree_refresh`` is toggled when the filesystem watcher
        # rebuilds the sidebar tree while a background worker (move/import) is
        # still shuffling files.  Those rebuilds re-select the current item in
        # the ``QTreeView``, which in turn emits navigation signals.  Deferring
        # the reaction keeps the gallery from reopening the album mid-operation
        # and avoids replacing the thumbnail grid with placeholders.
        self._suppress_tree_refresh: bool = False
        # ``_tree_refresh_suppression_reason`` records why suppression is
        # currently active so callers can distinguish between long-running
        # background workflows (which must remain suppressed) and short-lived
        # edit saves (where we only need to swallow the automatic sidebar
        # reselection once).  ``Literal`` keeps the intent self-documenting and
        # avoids mistyped sentinel strings.
        self._tree_refresh_suppression_reason: Optional[Literal["edit", "operation"]] = None

    def bind_playback_controller(self, playback: "PlaybackController") -> None:
        """Provide the playback controller once it has been constructed.

        ``MainController`` builds the navigation layer before creating the
        playback stack.  Supplying the reference afterwards avoids a circular
        dependency while allowing navigation actions to reset the playback state
        explicitly when returning to gallery-style views.
        """

        self._playback_controller = playback

    def _reset_playback_for_gallery_navigation(self) -> None:
        """Reset playback state when a navigation action shows a gallery view.

        Only a subset of navigation paths transition from the detail pane back
        to a gallery.  Triggering the playback reset at the start of each such
        path keeps the UI in sync without waiting for late ``galleryViewShown``
        signals, eliminating duplicate refreshes that previously caused flicker.
        """

        if self._playback_controller is not None:
            self._playback_controller.reset_for_gallery_navigation()

    # ------------------------------------------------------------------
    # Album management
    # ------------------------------------------------------------------
    def open_album(self, path: Path) -> None:
        # ``QFileSystemWatcher`` refreshes, library tree rebuilds and other
        # background activities occasionally reissue ``open_album`` for the
        # album the user is already browsing.  Those calls should be treated as
        # passive refreshes so the detail pane remains visible instead of
        # bouncing back to the gallery.  Compare the requested path with the
        # active album before touching any UI state so we can preserve the
        # current presentation when appropriate.
        target_root = path.resolve()
        current_root = (
            self._facade.current_album.root.resolve()
            if self._facade.current_album is not None
            else None
        )
        is_same_album = current_root == target_root

        # Treat any re-opening of the current album as a refresh, regardless of
        # whether the gallery is showing a virtual collection.  This ensures
        # that filesystem watcher events triggered by move operations do not
        # wipe the model and produce placeholder tiles while the asynchronous
        # reload repopulates the data.
        is_refresh = bool(is_same_album)

        # If the user is currently on the Albums Dashboard, clicking an album card
        # should always navigate to the album view, even if it is technically
        # the currently "open" album in the facade.
        if (
            is_refresh
            and self._static_selection
            and self._static_selection.casefold() == "albums"
        ):
            is_refresh = False

        self._last_open_was_refresh = is_refresh

        if is_refresh:
            # The album is already open and the caller is simply synchronising
            # sidebar state (for example after a manifest edit triggered by the
            # favorites button).  Returning early prevents a redundant call to
            # :meth:`AppFacade.open_album`, which would otherwise reset the
            # asset model, clear the playlist selection and bounce the detail
            # pane back to its placeholder.  The existing model already reflects
            # the manifest change via targeted data updates, so there is nothing
            # further to do.
            return

        self._reset_playback_for_gallery_navigation()
        self._static_selection = None
        self._asset_model.set_filter_mode(None)
        # Returning to a real album should always restore the traditional grid
        # presentation before the model finishes loading.
        self._view_controller.restore_default_gallery()
        self._view_controller.show_gallery_view()

        album = self._facade.open_album(path)
        if album is not None:
            self._context.remember_album(album.root)

    def handle_album_opened(self, root: Path) -> None:
        library_root = self._context.library.root()
        normalized_static = self._static_selection.casefold() if self._static_selection else ""

        if self._static_selection and library_root == root:
            title = self._static_selection
            self._sidebar.select_static_node(self._static_selection)
        elif (
            self._static_selection
            and normalized_static == "recently deleted"
        ):
            deleted_root = self._context.library.deleted_directory()
            is_deleted_target = False
            if deleted_root is not None:
                try:
                    deleted_resolved = deleted_root.resolve()
                except OSError:
                    deleted_resolved = deleted_root
                try:
                    root_resolved = root.resolve()
                except OSError:
                    root_resolved = root
                is_deleted_target = deleted_resolved == root_resolved
            if is_deleted_target:
                title = self._static_selection
                self._sidebar.select_static_node(self._static_selection)
                self._asset_model.set_filter_mode(None)
                self.update_status()
                return
            title = (
                self._facade.current_album.manifest.get("title")
                if self._facade.current_album
                else root.name
            )
            self._sidebar.select_path(root)
            self._static_selection = None
            self._asset_model.set_filter_mode(None)
        else:
            title = (
                self._facade.current_album.manifest.get("title")
                if self._facade.current_album
                else root.name
            )
            self._sidebar.select_path(root)
            self._static_selection = None
            self._asset_model.set_filter_mode(None)
        self.update_status()

    # ------------------------------------------------------------------
    # Static collections
    # ------------------------------------------------------------------
    def open_albums_dashboard(self) -> None:
        """Open the 'All Albums' dashboard view."""

        self._reset_playback_for_gallery_navigation()
        self._view_controller.show_albums_dashboard()
        self._static_selection = "Albums"
        self._asset_model.set_filter_mode(None)
        self._status.showMessage("Albums")

    def open_all_photos(self) -> None:
        self._view_controller.restore_default_gallery()
        self.open_static_collection(AlbumSidebar.ALL_PHOTOS_TITLE, None)

    def open_static_node(self, title: str) -> None:
        mapping = {
            "videos": "videos",
            "live photos": "live",
            "favorites": "favorites",
        }
        key = title.casefold()
        mode = mapping.get(key, None)
        self.open_static_collection(title, mode)

    def open_location_view(self) -> None:
        """Activate the Location view without forcing the gallery grid."""

        self.open_static_collection("Location", None, show_gallery=False)

    def open_recently_deleted(self) -> None:
        """Open the trash collection while ensuring the backing folder exists."""

        root = self._context.library.root()
        if root is None:
            self._dialog.bind_library_dialog()
            return

        is_refresh = bool(
            self._static_selection
            and self._static_selection.casefold() == "recently deleted"
        )
        self._last_open_was_refresh = is_refresh

        if is_refresh:
            return

        try:
            deleted_root = self._context.library.ensure_deleted_directory()
        except AlbumOperationError as exc:
            self._dialog.show_error(str(exc))
            return

        self._reset_playback_for_gallery_navigation()
        self._view_controller.restore_default_gallery()
        self._view_controller.show_gallery_view()
        self._asset_model.set_filter_mode(None)
        self._static_selection = "Recently Deleted"

        album = self._facade.open_album(deleted_root)
        if album is None:
            self._static_selection = None
            return

        album.manifest = {**album.manifest, "title": "Recently Deleted"}

    def open_static_collection(
        self,
        title: str,
        filter_mode: Optional[str],
        *,
        show_gallery: bool = True,
    ) -> None:
        self._reset_playback_for_gallery_navigation()
        target_root = self._context.library.root()
        if target_root is None:
            self._dialog.bind_library_dialog()
            return

        # Determine if we are navigating within the currently loaded physical library.
        current_root = (
            self._facade.current_album.root
            if self._facade.current_album
            else None
        )
        is_same_root = (
            current_root is not None
            and current_root.resolve() == target_root.resolve()
        )

        current_static = self._static_selection
        is_refresh = bool(
            current_static and current_static.casefold() == title.casefold()
        )
        self._last_open_was_refresh = is_refresh

        if is_refresh:
            return

        # ``open_static_collection`` is always a user-driven navigation request
        # (e.g. clicking "All Photos" or "Favorites"), so explicitly mark the
        # transition as a fresh navigation instead of a passive refresh.  This
        # prevents the caller that triggered the static switch from assuming
        # the previous album remained visible.
        #
        # Note: The ``is_refresh`` check above now guards against sidebar
        # reloads triggered by background workers, which maintains the previous
        # view state (e.g. keeping the detail pane active) when the user did
        # not initiate the navigation.

        # Reset the detail pane whenever a static collection (All Photos,
        # Favorites, etc.) is opened so the UI consistently shows the grid as
        # its entry point for that virtual album.
        if show_gallery:
            self._view_controller.restore_default_gallery()
            self._view_controller.show_gallery_view()

        self._static_selection = title

        if is_same_root:
            # --- OPTIMIZED PATH (In-Memory) ---
            # We are staying in the same library.
            # 1. Skip open_album() to prevent model destruction and reloading.
            # 2. Apply the filter directly. This is the only cost incurred.
            self._asset_model.set_filter_mode(filter_mode)
            self._asset_model.ensure_chronological_order()

            # Manually update UI state since open_album() was skipped
            if self._facade.current_album:
                self._facade.current_album.manifest["title"] = title
            self._main_window.setWindowTitle(title)
            self._sidebar.select_static_node(title)
        else:
            # --- STANDARD PATH (Context Switch) ---
            # We are switching from a different physical album root or loading the library for the first time.
            # 1. Destroy the old model FIRST.
            # This prevents wasting CPU cycles filtering the old dataset.
            album = self._facade.open_album(target_root)
            if album is None:
                self._static_selection = None
                self._asset_model.set_filter_mode(None)
                return

            # 2. Configure the new empty model
            self._asset_model.set_filter_mode(filter_mode)
            # Aggregated collections should always present assets chronologically so
            # that freshly captured media surfaces immediately after move/restore
            # operations rebuild the index.  Reapplying the sort each time keeps the
            # proxy aligned even if other workflows temporarily changed it.
            self._asset_model.ensure_chronological_order()

            album.manifest = {**album.manifest, "title": title}
            self._main_window.setWindowTitle(title)

        self.update_status()

    def consume_last_open_refresh(self) -> bool:
        """Return ``True`` if the previous :meth:`open_album` was a refresh."""

        was_refresh = self._last_open_was_refresh
        self._last_open_was_refresh = False
        return was_refresh

    def handle_tree_updated(self) -> None:
        """Record tree rebuilds triggered while background jobs are running."""

        if self._view_controller.is_edit_view_active():
            # Saving edits touches the filesystem, which in turn causes the
            # library watcher to rebuild the sidebar tree.  Those rebuilds
            # re-select the active virtual collection (e.g. "All Photos"),
            # emitting the corresponding navigation signal.  If the detail view
            # is still showing the edited asset we must ignore the signal to
            # avoid the gallery stealing focus.  Suppressing sidebar-triggered
            # navigation keeps the user anchored in the detail surface until the
            # edit workflow formally ends.
            self._suppress_tree_refresh = True
            self._tree_refresh_suppression_reason = "edit"
            return

        if (
            self._tree_refresh_suppression_reason == "edit"
            and self._suppress_tree_refresh
        ):
            # The edit view already closed, but the sidebar has not yet
            # reissued its selection change.  Keep the suppression active so the
            # automatic navigation will be ignored once before re-enabling the
            # normal behaviour.
            return

        if self._facade.is_performing_background_operation():
            # ``AlbumSidebar`` rebuilds the model whenever the library tree is
            # refreshed.  During a move/import this happens while the index is
            # still in flux, so we flag the refresh and let the controller skip
            # redundant navigation callbacks.
            self._suppress_tree_refresh = True
            self._tree_refresh_suppression_reason = "operation"
        else:
            # The tree settled without a concurrent background job, therefore
            # the controller can react to subsequent sidebar events normally.
            self._suppress_tree_refresh = False
            self._tree_refresh_suppression_reason = None

    def suppress_tree_refresh_for_edit(self) -> None:
        """Ignore sidebar reselections triggered by edit saves."""

        # Saving adjustments writes sidecar files, which in turn causes the
        # library watcher to rebuild the sidebar tree.  Those rebuilds reselect
        # the active virtual collection and emit navigation signals.  Mark the
        # tree as suppressed ahead of the disk write so the automatic callback
        # is swallowed exactly once while the detail pane remains visible.
        self._suppress_tree_refresh = True
        self._tree_refresh_suppression_reason = "edit"

    def should_suppress_tree_refresh(self) -> bool:
        """Return ``True`` when sidebar callbacks should be ignored temporarily."""

        return self._suppress_tree_refresh

    def release_tree_refresh_suppression_if_edit(self) -> None:
        """Stop suppressing sidebar callbacks when the last edit finished."""

        if self._tree_refresh_suppression_reason == "edit":
            self._suppress_tree_refresh = False
            self._tree_refresh_suppression_reason = None

    def clear_tree_refresh_suppression(self) -> None:
        """Allow sidebar selections to trigger navigation again."""

        self._suppress_tree_refresh = False
        self._tree_refresh_suppression_reason = None

    def suspend_library_watcher(self, duration: int = 250) -> None:
        """Pause the filesystem watcher to prevent auto-reloads during file operations."""

        manager = self._context.library
        manager.pause_watcher()
        QTimer.singleShot(duration, manager.resume_watcher)

    # ------------------------------------------------------------------
    # Status helpers
    # ------------------------------------------------------------------
    def update_status(self) -> None:
        count = self._asset_model.rowCount()
        if count == 0:
            message = "No assets indexed"
        elif count == 1:
            message = "1 asset indexed"
        else:
            message = f"{count} assets indexed"
        self._status.showMessage(message)

    def prompt_for_basic_library(self) -> None:
        if self._context.library.root() is not None:
            return
        self._dialog.prompt_for_basic_library()

    def static_selection(self) -> Optional[str]:
        return self._static_selection

    def clear_static_selection(self) -> None:
        self._static_selection = None

    def sidebar_model(self):
        """Expose the sidebar tree model for auxiliary controllers."""

        return self._sidebar.tree_model()

    def is_basic_library_virtual_view(self) -> bool:
        """Return ``True`` when a Basic Library virtual collection is active."""

        # ``_static_selection`` mirrors the last virtual node triggered from the
        # sidebar.  Whenever one of the built-in Basic Library collections is
        # active we want move operations to keep their optimistic updates, so
        # normalise the title and compare it against the known set of virtual
        # albums.
        if not self._static_selection:
            return False
        normalized_title = self._static_selection.casefold()
        virtual_views = {
            AlbumSidebar.ALL_PHOTOS_TITLE.casefold(),
            "videos",
            "live photos",
            "favorites",
            "albums",
        }
        return normalized_title in virtual_views

    def is_all_photos_view(self) -> bool:
        """Return ``True`` when the "All Photos" virtual collection is active."""

        # ``_static_selection`` mirrors the last sidebar node that activated a
        # static collection.  Compare it against the well-known label using a
        # case-insensitive check so localisation or theme adjustments that tweak
        # the capitalisation do not affect the outcome.
        if not self._static_selection:
            return False
        return (
            self._static_selection.casefold()
            == AlbumSidebar.ALL_PHOTOS_TITLE.casefold()
        )

    def is_recently_deleted_view(self) -> bool:
        """Return ``True`` when the trash collection is the active view."""

        return bool(
            self._static_selection
            and self._static_selection.casefold() == "recently deleted"
        )

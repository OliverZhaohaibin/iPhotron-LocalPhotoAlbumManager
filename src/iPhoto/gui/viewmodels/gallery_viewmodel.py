"""Pure Python screen-level view model for gallery and map navigation."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Literal, Optional

from iPhoto.application.services.asset_service import AssetService
from iPhoto.application.contracts.runtime_entry_contract import RuntimeEntryContract
from iPhoto.config import ALL_PHOTOS_TITLE, DEFAULT_EXCLUDE, DEFAULT_INCLUDE, RECENTLY_DELETED_DIR_NAME
from iPhoto.domain.models.core import MediaType
from iPhoto.domain.models.query import AssetQuery
from iPhoto.gui.coordinators.location_selection_session import LocationSelectionSession
from iPhoto.gui.facade import AppFacade

from .base import BaseViewModel
from .gallery_collection_store import GalleryCollectionStore
from .signal import ObservableProperty, Signal


class GalleryViewModel(BaseViewModel):
    """Own gallery, collection, and location-navigation state."""

    def __init__(
        self,
        *,
        store: GalleryCollectionStore,
        context: RuntimeEntryContract,
        facade: AppFacade,
        asset_service: AssetService,
        location_session: LocationSelectionSession | None = None,
    ) -> None:
        super().__init__()
        self._store = store
        self._context = context
        self._facade = facade
        self._asset_service = asset_service
        self._location_session = location_session or LocationSelectionSession()
        self._cluster_gallery_origin: Literal["location", "people", None] = None

        self.current_section = ObservableProperty("gallery")
        self.static_selection = ObservableProperty(None)
        self.selection_mode = ObservableProperty(False)
        self.active_root = ObservableProperty(context.library.root())
        self.current_query = ObservableProperty(None)
        self.current_direct_assets = ObservableProperty(None)
        self.can_return_to_map = ObservableProperty(False)

        self.route_requested = Signal()
        self.detail_requested = Signal()
        self.bind_library_requested = Signal()
        self.map_assets_changed = Signal()
        self.cluster_gallery_mode_changed = Signal()
        self.sidebar_path_requested = Signal()
        self.message_requested = Signal()

    @property
    def location_session(self) -> LocationSelectionSession:
        return self._location_session

    def open_album(self, path: Path) -> None:
        album = self._facade.open_album(path)
        active_root = album.root if album else path
        if album:
            self._context.remember_album(album.root)
            self.sidebar_path_requested.emit(album.root)
        else:
            self.sidebar_path_requested.emit(path)

        query = AssetQuery(album_path=self._album_path_for_query(active_root))
        query.include_subalbums = True
        self._clear_location_context()
        self._clear_cluster_gallery_context()
        self._load_query(
            section="album",
            static_selection=None,
            root=active_root,
            query=query,
        )

    def open_all_photos(self) -> None:
        root = self._context.library.root()
        if root is None:
            self.bind_library_requested.emit()
            return
        self._clear_location_context()
        self._clear_cluster_gallery_context()
        self._load_query(
            section="all_photos",
            static_selection=ALL_PHOTOS_TITLE,
            root=root,
            query=AssetQuery(),
        )

    def open_recently_deleted(self) -> None:
        root = self._context.library.root()
        if root is None:
            self.bind_library_requested.emit()
            return
        deleted_root = self._context.library.ensure_deleted_directory()
        self._facade.open_album(deleted_root)
        self._clear_location_context()
        self._clear_cluster_gallery_context()
        self._load_query(
            section="recently_deleted",
            static_selection="Recently Deleted",
            root=deleted_root,
            query=AssetQuery(album_path=RECENTLY_DELETED_DIR_NAME),
        )

    def open_filtered_collection(
        self,
        title: str,
        *,
        is_favorite: bool | None = None,
        media_types: list[MediaType] | None = None,
    ) -> None:
        root = self._context.library.root()
        if root is None:
            self.bind_library_requested.emit()
            return
        query = AssetQuery()
        if is_favorite:
            query.is_favorite = True
        if media_types:
            query.media_types = list(media_types)
        self._clear_location_context()
        self._clear_cluster_gallery_context()
        self._load_query(
            section=title.casefold().replace(" ", "_"),
            static_selection=title,
            root=root,
            query=query,
        )

    def open_albums_dashboard(self) -> None:
        root = self._context.library.root()
        self._clear_location_context()
        self._clear_cluster_gallery_context()
        self.current_section.value = "dashboard"
        self.static_selection.value = "Albums"
        self.active_root.value = root
        self.current_query.value = None
        self.current_direct_assets.value = None
        self.can_return_to_map.value = False
        self.route_requested.emit("albums_dashboard")

    def open_people_dashboard(self) -> None:
        root = self._context.library.root()
        self._clear_location_context()
        self._clear_cluster_gallery_context()
        self.current_section.value = "people_dashboard"
        self.static_selection.value = "People"
        self.active_root.value = root
        self.current_query.value = None
        self.current_direct_assets.value = None
        self.can_return_to_map.value = False
        self.route_requested.emit("people")

    def open_location_map(self) -> None:
        root = self._context.library.root()
        if root is None:
            self.bind_library_requested.emit()
            return
        self.current_section.value = "location_map"
        self.static_selection.value = "Location"
        self.active_root.value = root
        self.current_query.value = None
        self.current_direct_assets.value = None
        self.can_return_to_map.value = False
        self._clear_cluster_gallery_context()
        self._location_session.set_mode("map")
        self.route_requested.emit("map")

        if (
            self._location_session.root == root
            and self._location_session.has_snapshot
            and not self._location_session.invalidated
        ):
            self.map_assets_changed.emit(self._location_session.full_assets(), root)
            return

        request_serial = self._location_session.begin_load(root)
        assets = list(self._context.library.get_geotagged_assets())
        self._location_session.accept_loaded(request_serial, root, assets)
        self.map_assets_changed.emit(self._location_session.full_assets(), root)

    def open_location_asset(self, rel: str) -> None:
        root = self._context.library.root()
        if root is None:
            self.bind_library_requested.emit()
            return
        if (
            self._location_session.root != root
            or not self._location_session.has_snapshot
            or self._location_session.invalidated
        ):
            return
        resolved_path = self._location_session.resolve_relative(rel)
        if resolved_path is None:
            return
        assets = self._location_session.full_assets()
        self.current_section.value = "location_gallery"
        self.static_selection.value = "Location"
        self.active_root.value = root
        self.current_query.value = None
        self.current_direct_assets.value = list(assets)
        self.can_return_to_map.value = False
        self._clear_cluster_gallery_context()
        self._location_session.set_mode("gallery")
        self.cluster_gallery_mode_changed.emit(False)
        self._store.load_selection(root, direct_assets=assets, library_root=root)
        self.route_requested.emit("gallery")
        row = self._store.row_for_path(resolved_path)
        if row is not None:
            self.open_row(row)

    def open_cluster_gallery(self, assets: list[Any]) -> None:
        root = self._context.library.root()
        if root is None:
            self.bind_library_requested.emit()
            return
        self._cluster_gallery_origin = "location"
        self.current_section.value = "cluster_gallery"
        self.static_selection.value = "Location"
        self.active_root.value = root
        self.current_query.value = None
        self.current_direct_assets.value = list(assets)
        self.can_return_to_map.value = True
        self._location_session.set_mode("cluster_gallery")
        self._store.load_selection(root, direct_assets=assets, library_root=root)
        self.cluster_gallery_mode_changed.emit(True)
        self.route_requested.emit("gallery")

    def open_people_cluster_gallery(self, query: AssetQuery) -> None:
        root = self._context.library.root()
        if root is None:
            self.bind_library_requested.emit()
            return
        self._cluster_gallery_origin = "people"
        self._location_session.set_mode("inactive")
        self.current_section.value = "people_cluster_gallery"
        self.static_selection.value = "People"
        self.active_root.value = root
        self.current_query.value = query
        self.current_direct_assets.value = None
        self.can_return_to_map.value = True
        self._store.load_selection(root, query=query)
        self.cluster_gallery_mode_changed.emit(True)
        self.route_requested.emit("gallery")

    def return_from_cluster_gallery(self) -> None:
        if self._cluster_gallery_origin == "location" or (
            self._cluster_gallery_origin is None
            and self._location_session.mode == "cluster_gallery"
        ):
            self.cluster_gallery_mode_changed.emit(False)
            self.open_location_map()
            return
        if self._cluster_gallery_origin == "people":
            self.cluster_gallery_mode_changed.emit(False)
            self._clear_cluster_gallery_context()
            self.open_people_dashboard()

    def return_to_map_from_cluster_gallery(self) -> None:
        self.return_from_cluster_gallery()

    def on_library_tree_updated(self) -> bool:
        self._location_session.invalidate()
        if self.is_location_context_active():
            return False
        self._store.reload_current_selection()
        return True

    def set_selection_mode(self, enabled: bool) -> None:
        self.selection_mode.value = bool(enabled)

    def invalidate_location_session(self) -> None:
        self._location_session.invalidate()

    def is_location_context_active(self) -> bool:
        return self._location_session.mode != "inactive"

    def is_in_cluster_gallery(self) -> bool:
        return self._cluster_gallery_origin is not None or self._location_session.mode == "cluster_gallery"

    def cluster_gallery_back_tooltip(self) -> str:
        if self._cluster_gallery_origin == "people":
            return "Return to People"
        return "Return to Map"

    def open_row(self, row: int) -> None:
        if row < 0:
            return
        self.detail_requested.emit(row)

    def rescan_current(self) -> None:
        if self._facade.current_album is not None:
            self._facade.rescan_current_async()
            return

        library_root = self._context.library.root()
        if library_root is None:
            self.message_requested.emit("No album is currently open.", 3000)
            return

        self._context.library.start_scanning(
            library_root,
            DEFAULT_INCLUDE,
            DEFAULT_EXCLUDE,
        )

    def path_for_row(self, row: int) -> Optional[Path]:
        dto = self._store.asset_at(row)
        return dto.abs_path if dto is not None else None

    def paths_for_rows(self, rows: Iterable[int]) -> list[Path]:
        seen: set[Path] = set()
        paths: list[Path] = []
        for row in rows:
            path = self.path_for_row(row)
            if path is None or path in seen:
                continue
            seen.add(path)
            paths.append(path)
        return paths

    def toggle_favorite_row(self, row: int) -> Optional[bool]:
        path = self.path_for_row(row)
        if path is None:
            return None
        new_state = self._asset_service.toggle_favorite_by_path(path)
        self._store.update_favorite_status(row, new_state)
        return new_state

    def _load_query(
        self,
        *,
        section: str,
        static_selection: str | None,
        root: Path,
        query: AssetQuery,
    ) -> None:
        self.current_section.value = section
        self.static_selection.value = static_selection
        self.active_root.value = root
        self.current_query.value = query
        self.current_direct_assets.value = None
        self.can_return_to_map.value = False
        self._store.load_selection(root, query=query)
        self.cluster_gallery_mode_changed.emit(False)
        self.route_requested.emit("gallery")

    def _clear_location_context(self) -> None:
        self._location_session.set_mode("inactive")
        self.can_return_to_map.value = False

    def _clear_cluster_gallery_context(self) -> None:
        self._cluster_gallery_origin = None

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

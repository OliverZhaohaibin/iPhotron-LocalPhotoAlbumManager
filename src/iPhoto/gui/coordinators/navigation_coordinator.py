"""Coordinator that binds sidebar and map widgets to GalleryViewModel."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Optional

from PySide6.QtCore import QObject, QTimer, Signal

from iPhoto.application.contracts.runtime_entry_contract import RuntimeEntryContract
from iPhoto.bootstrap.library_people_service import create_people_service
from iPhoto.config import ALL_PHOTOS_TITLE
from iPhoto.gui.services.pinned_items_service import PinnedItemsService, PinnedSidebarItem
from iPhoto.gui.coordinators.view_router import ViewRouter
from iPhoto.gui.facade import AppFacade
from iPhoto.gui.ui.widgets import dialogs
from iPhoto.gui.ui.widgets.album_sidebar import AlbumSidebar
from iPhoto.gui.viewmodels.gallery_viewmodel import GalleryViewModel
from iPhoto.people.service import PeopleService

if TYPE_CHECKING:
    from iPhoto.gui.coordinators.playback_coordinator import PlaybackCoordinator


class NavigationCoordinator(QObject):
    """Thin binder around gallery navigation and location flows."""

    bindLibraryRequested = Signal()
    _logger = logging.getLogger(__name__)

    def __init__(
        self,
        sidebar: AlbumSidebar,
        router: ViewRouter,
        gallery_vm: GalleryViewModel,
        context: RuntimeEntryContract,
        facade: AppFacade,
        pinned_items_service: PinnedItemsService | None = None,
    ) -> None:
        super().__init__()
        self._sidebar = sidebar
        self._router = router
        self._gallery_vm = gallery_vm
        self._context = context
        self._facade = facade
        self._pinned_items_service = pinned_items_service

        self._playback_coordinator: Optional[PlaybackCoordinator] = None

        self._suppress_tree_refresh = False
        self._tree_refresh_suppression_reason: Optional[Literal["edit", "operation"]] = None

        self._connect_signals()

    def set_playback_coordinator(self, coordinator: PlaybackCoordinator) -> None:
        self._playback_coordinator = coordinator

    def _connect_signals(self) -> None:
        self._sidebar.albumSelected.connect(self.open_album)
        self._sidebar.pinnedItemSelected.connect(self.open_pinned_item)
        self._sidebar.allPhotosSelected.connect(self.open_all_photos)
        self._sidebar.staticNodeSelected.connect(self._handle_static_node)
        self._sidebar.bindLibraryRequested.connect(self._handle_bind_library)

        self._gallery_vm.route_requested.connect(self._handle_route_requested)
        self._gallery_vm.detail_requested.connect(self._handle_detail_requested)
        self._gallery_vm.bind_library_requested.connect(self._handle_bind_library)
        self._gallery_vm.map_assets_changed.connect(self._handle_map_assets_changed)
        self._gallery_vm.cluster_gallery_mode_changed.connect(self._handle_cluster_gallery_mode_changed)
        self._gallery_vm.sidebar_path_requested.connect(self._sidebar.select_path)

    def open_album(self, path: Path) -> None:
        if self._should_treat_as_refresh(path):
            return
        self._reset_playback()
        self._gallery_vm.open_album(path)

    def open_pinned_item(self, pinned_item: PinnedSidebarItem) -> None:
        self._reset_playback()
        library_root = self._context.library.root()
        if pinned_item.kind == "album":
            target = Path(pinned_item.item_id)
            if not target.exists():
                self._handle_missing_pinned_item(
                    pinned_item,
                    library_root=library_root,
                    message=f"Pinned album '{pinned_item.label or target.name}' is no longer available and will be removed from the sidebar.",
                )
                return
            self._gallery_vm.open_pinned_album(target)
            return

        if library_root is None:
            self.bindLibraryRequested.emit()
            return

        bound_people_service = getattr(self._context.library, "people_service", None)
        people_service = (
            bound_people_service
            if isinstance(bound_people_service, PeopleService)
            else create_people_service(library_root)
        )
        if pinned_item.kind == "person":
            try:
                query = people_service.build_cluster_query(pinned_item.item_id)
                entity_exists = people_service.has_cluster(pinned_item.item_id)
            except Exception:
                self._logger.warning(
                    "Failed to open pinned person '%s' (%s)",
                    pinned_item.label,
                    pinned_item.item_id,
                    exc_info=True,
                )
                return
            if not query.asset_ids and not entity_exists:
                self._handle_missing_pinned_item(
                    pinned_item,
                    library_root=library_root,
                    message=f"Pinned person '{pinned_item.label}' is no longer available and will be removed from the sidebar.",
                )
                return
            self._gallery_vm.open_pinned_people_query(
                query,
                kind="person",
                entity_id=pinned_item.item_id,
            )
            return

        if pinned_item.kind == "group":
            try:
                query = people_service.build_group_query(pinned_item.item_id)
                entity_exists = people_service.has_group(pinned_item.item_id)
            except Exception:
                self._logger.warning(
                    "Failed to open pinned group '%s' (%s)",
                    pinned_item.label,
                    pinned_item.item_id,
                    exc_info=True,
                )
                return
            if not query.asset_ids and not entity_exists:
                self._handle_missing_pinned_item(
                    pinned_item,
                    library_root=library_root,
                    message=f"Pinned group '{pinned_item.label}' is no longer available and will be removed from the sidebar.",
                )
                return
            self._gallery_vm.open_pinned_people_query(
                query,
                kind="group",
                entity_id=pinned_item.item_id,
            )

    def _handle_missing_pinned_item(
        self,
        pinned_item: PinnedSidebarItem,
        *,
        library_root: Path | None,
        message: str,
    ) -> None:
        dialogs.show_warning(self._sidebar, message)
        if self._pinned_items_service is None:
            return
        if pinned_item.kind == "album":
            self._pinned_items_service.prune_missing_album(
                Path(pinned_item.item_id),
                library_root=library_root,
            )
            return
        self._pinned_items_service.prune_missing_entity(
            kind=pinned_item.kind,
            item_id=pinned_item.item_id,
            library_root=library_root,
        )

    def open_all_photos(self) -> None:
        self._reset_playback()
        self._gallery_vm.open_all_photos()

    def open_recently_deleted(self) -> None:
        self._reset_playback()
        self._gallery_vm.open_recently_deleted()

    def open_location_view(self) -> None:
        self._reset_playback()
        self._gallery_vm.open_location_map()

    def open_people_view(self) -> None:
        self._reset_playback()
        self._gallery_vm.open_people_dashboard()

    def open_cluster_gallery(self, assets: list) -> None:
        self._reset_playback()
        self._gallery_vm.open_cluster_gallery(assets)

    def open_people_cluster_gallery(self, query) -> None:
        self._reset_playback()
        self._gallery_vm.open_people_cluster_gallery(query)

    def return_from_cluster_gallery(self) -> None:
        self._gallery_vm.return_from_cluster_gallery()

    def return_to_map_from_cluster_gallery(self) -> None:
        self.return_from_cluster_gallery()

    def open_location_asset(self, rel: str) -> None:
        self._gallery_vm.open_location_asset(rel)

    def _handle_static_node(self, name: str) -> None:
        normalized = name.casefold()
        if normalized == "all photos":
            self.open_all_photos()
        elif normalized == "recently deleted":
            self.open_recently_deleted()
        elif normalized == "albums":
            self._reset_playback()
            self._gallery_vm.open_albums_dashboard()
        elif normalized == "favorites":
            self._reset_playback()
            self._gallery_vm.open_filtered_collection(name, is_favorite=True)
        elif normalized == "videos":
            from iPhoto.domain.models.core import MediaType

            self._reset_playback()
            self._gallery_vm.open_filtered_collection(name, media_types=[MediaType.VIDEO])
        elif normalized == "live photos":
            from iPhoto.domain.models.core import MediaType

            self._reset_playback()
            self._gallery_vm.open_filtered_collection(name, media_types=[MediaType.LIVE_PHOTO])
        elif normalized == "people":
            self.open_people_view()
        elif normalized == "location":
            self.open_location_view()

    def _handle_route_requested(self, view: str) -> None:
        if view == "gallery":
            self._router.show_gallery()
        elif view == "map":
            self._router.show_map()
        elif view == "people":
            self._router.show_people()
        elif view == "albums_dashboard":
            self._router.show_albums_dashboard()
        elif view == "detail":
            self._router.show_detail()

    def _handle_detail_requested(self, row: int) -> None:
        if self._playback_coordinator is not None:
            self._playback_coordinator.play_asset(row)

    def _handle_map_assets_changed(self, assets: list, root: Path) -> None:
        map_view = self._router.map_view()
        if map_view is not None:
            map_view.set_assets(assets, root)

    def _handle_cluster_gallery_mode_changed(self, enabled: bool) -> None:
        gallery_page = self._router.gallery_page()
        if gallery_page is not None:
            gallery_page.set_cluster_gallery_mode(
                bool(enabled),
                back_tooltip=self._gallery_vm.cluster_gallery_back_tooltip(),
            )

    def _handle_bind_library(self) -> None:
        self.bindLibraryRequested.emit()

    def _should_treat_as_refresh(self, path: Path) -> bool:
        static_selection = self._gallery_vm.static_selection.value
        if static_selection is not None:
            return False
        if self._facade.current_album and self._facade.current_album.root.resolve() == path.resolve():
            return self._router.is_gallery_view_active()
        return False

    def _reset_playback(self) -> None:
        if self._playback_coordinator is not None:
            self._playback_coordinator.reset_for_gallery()

    def suppress_tree_refresh_for_edit(self) -> None:
        self._suppress_tree_refresh = True
        self._tree_refresh_suppression_reason = "edit"

    def should_suppress_tree_refresh(self) -> bool:
        return self._suppress_tree_refresh

    def release_tree_refresh_suppression_if_edit(self) -> None:
        if self._tree_refresh_suppression_reason == "edit":
            self._suppress_tree_refresh = False
            self._tree_refresh_suppression_reason = None

    def clear_tree_refresh_suppression(self) -> None:
        self._suppress_tree_refresh = False
        self._tree_refresh_suppression_reason = None

    def invalidate_location_session(self) -> None:
        self._gallery_vm.invalidate_location_session()

    def is_location_context_active(self) -> bool:
        return self._gallery_vm.is_location_context_active()

    def is_in_cluster_gallery(self) -> bool:
        return self._gallery_vm.is_in_cluster_gallery()

    def suspend_library_watcher(self, duration: int = 250) -> None:
        manager = self._context.library
        manager.pause_watcher()
        QTimer.singleShot(duration, manager.resume_watcher)

    def pause_library_watcher(self) -> None:
        self._context.library.pause_watcher()

    def resume_library_watcher(self) -> None:
        self._context.library.resume_watcher()

    def static_selection(self) -> Optional[str]:
        return self._gallery_vm.static_selection.value

    def is_all_photos_view(self) -> bool:
        value = self.static_selection()
        return bool(value) and value.casefold() == ALL_PHOTOS_TITLE.casefold()

    def is_recently_deleted_view(self) -> bool:
        value = self.static_selection()
        return bool(value) and value.casefold() == "recently deleted"

    def sidebar_model(self):
        return self._sidebar.tree_model()

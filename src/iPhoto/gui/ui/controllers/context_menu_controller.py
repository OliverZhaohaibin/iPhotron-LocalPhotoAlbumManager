"""Controller that encapsulates the gallery context menu logic."""

from __future__ import annotations

import subprocess
import sys
from functools import partial
from pathlib import Path
from typing import Callable, Optional, TYPE_CHECKING

from PySide6.QtCore import (
    QAbstractItemModel,
    QCoreApplication,
    QMimeData,
    QObject,
    QPoint,
    QUrl,
    Qt,
    QItemSelectionModel,
)
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QMenu

from iPhoto.gui.ui.menus.core import MenuContext, populate_menu
from iPhoto.gui.ui.menus.gallery_menu import GalleryMenuHandlers, gallery_action_specs
from iPhoto.gui.ui.menus.style import apply_menu_style
from iPhoto.people.service import PeopleService

from ...facade import AppFacade
from ..widgets.asset_grid import AssetGrid
from ..widgets.notification_toast import NotificationToast
from .selection_controller import SelectionController
from .status_bar_controller import StatusBarController

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ...coordinators.navigation_coordinator import NavigationCoordinator
    from ...viewmodels.gallery_viewmodel import GalleryViewModel


class ContextMenuController(QObject):
    """Manage the asset grid context menu and related clipboard interactions."""

    def __init__(
        self,
        *,
        grid_view: AssetGrid,
        asset_model: QAbstractItemModel,
        selected_paths_provider: Callable[[list[int]], list[Path]],
        facade: AppFacade,
        status_bar: StatusBarController,
        notification_toast: NotificationToast,
        selection_controller: SelectionController | None,
        navigation: "NavigationCoordinator" | None,
        export_callback: Callable[[], None],
        prepare_paths_for_mutation: Callable[[list[Path]], None] | None = None,
        gallery_viewmodel: "GalleryViewModel" | None = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._grid_view = grid_view
        self._asset_model = asset_model
        self._selected_paths_provider = selected_paths_provider
        self._facade = facade
        self._status_bar = status_bar
        self._toast = notification_toast
        self._selection_controller = selection_controller
        self._navigation = navigation
        self._export_callback = export_callback
        self._prepare_paths_for_mutation = prepare_paths_for_mutation
        self._gallery_viewmodel = gallery_viewmodel

        self._grid_view.customContextMenuRequested.connect(self._handle_context_menu)

    # ------------------------------------------------------------------
    # Context menu workflow
    # ------------------------------------------------------------------
    def _handle_context_menu(self, point: QPoint) -> None:
        """Construct and display a context menu based on where the user right-clicked."""

        index = self._grid_view.indexAt(point)
        menu = QMenu(self._grid_view)
        apply_menu_style(menu, self._grid_view)

        selection_model = self._grid_view.selectionModel()

        if index.isValid() and selection_model and not selection_model.isSelected(index):
            selection_model.setCurrentIndex(
                index,
                QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows,
            )

        context = self._menu_context()
        handlers = GalleryMenuHandlers(
            copy_selection=lambda _ctx: self._copy_selection_to_clipboard(),
            reveal_selection=lambda _ctx: self._reveal_selection_in_file_manager(),
            export_selection=lambda _ctx: self._export_callback(),
            delete_selection=lambda _ctx: self.delete_selection(),
            restore_selection=lambda _ctx: self._execute_restore(),
            paste_into_album=lambda _ctx: self._paste_from_clipboard(),
            open_current_folder=lambda _ctx: self._open_current_folder(),
            set_as_cover=self._set_as_cover,
            set_as_cover_visible=self._set_as_cover_visible,
            move_targets=lambda _ctx: self._collect_move_targets(),
            move_to_album=self._execute_move_to_album,
        )
        action_count = populate_menu(
            menu,
            context=context,
            action_specs=gallery_action_specs(context, handlers),
            anchor=self._grid_view,
        )
        if action_count <= 0:
            return

        global_pos = self._grid_view.viewport().mapToGlobal(point)
        menu.exec(global_pos)

    def delete_selection(self) -> bool:
        """Move the current selection into the deleted-items collection."""

        if self._navigation is not None and self._navigation.is_recently_deleted_view():
            self._status_bar.show_message(
                "Items inside Recently Deleted cannot be deleted again.",
                3000,
            )
            return False

        selection_model = self._grid_view.selectionModel()
        selected_indexes = (
            list(selection_model.selectedIndexes()) if selection_model else []
        )
        paths = self._selected_asset_paths()
        if not paths:
            self._status_bar.show_message("Select items to delete first.", 3000)
            return False

        if not self._apply_optimistic_move(paths, is_delete=True):
            self._remove_selection_rows(selected_indexes)

        try:
            self._prepare_file_mutation(paths)
            self._facade.delete_assets(paths)
        except Exception:
            # Rescanning the album restores the rows we removed optimistically.
            self._facade.rescan_current()
            raise
        finally:
            if self._selection_controller is not None:
                self._selection_controller.set_selection_mode(False)

        self._toast.show_toast("Deleted")
        return True

    def _execute_restore(self) -> None:
        """Restore the current selection to the original albums recorded in the index."""

        selection_model = self._grid_view.selectionModel()
        selected_indexes = (
            list(selection_model.selectedIndexes()) if selection_model else []
        )
        paths = self._selected_asset_paths()
        if not paths:
            self._status_bar.show_message("Select items to restore first.", 3000)
            return

        try:
            self._prepare_file_mutation(paths)
            queued_restore = self._facade.restore_assets(paths)
        except Exception:
            self._facade.rescan_current()
            raise
        finally:
            if self._selection_controller is not None:
                self._selection_controller.set_selection_mode(False)

        if queued_restore:
            if selected_indexes:
                # Removing the rows only after the restore task has been accepted
                # avoids hiding assets when the backend declined to queue any
                # work (for example because the user rejected every fallback).
                if not self._apply_optimistic_move(paths, is_delete=False):
                    self._remove_selection_rows(selected_indexes)
            self._toast.show_toast("Restoring ...")

    def _copy_selection_to_clipboard(self) -> None:
        """Copy the selected asset file paths into the system clipboard."""

        paths = self._selected_asset_paths()
        if not paths:
            self._status_bar.show_message("Select items to copy first.", 3000)
            return
        existing = [path for path in paths if path.exists()]
        if not existing:
            self._status_bar.show_message(
                "Selected files are unavailable on disk.",
                3000,
            )
            return
        mime_data = QMimeData()
        mime_data.setUrls([QUrl.fromLocalFile(str(path)) for path in existing])
        QGuiApplication.clipboard().setMimeData(mime_data)
        self._toast.show_toast("Copied to Clipboard")

    def _reveal_selection_in_file_manager(self) -> None:
        """Open the desktop file manager pointing to the first selected asset."""

        paths = self._selected_asset_paths()
        if not paths:
            self._status_bar.show_message("Select items to reveal first.", 3000)
            return

        path = paths[0]
        if not path.exists():
            self._status_bar.show_message(f"File not found: {path.name}", 3000)
            return

        # The command used to reveal a file varies per operating system. Each branch uses the
        # platform native tool to either highlight the file (Windows, macOS) or open the folder
        # containing the file (Linux and other POSIX systems).
        if sys.platform == "win32":
            subprocess.run(["explorer", "/select,", str(path)], check=False)
        elif sys.platform == "darwin":
            subprocess.run(["open", "-R", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path.parent)], check=False)

        self._status_bar.show_message(
            f"Revealed {path.name} in file manager.",
            3000,
        )

    def _paste_from_clipboard(self) -> None:
        """Import files referenced in the clipboard into the currently opened album."""

        clipboard = QGuiApplication.clipboard()
        mime_data = clipboard.mimeData()
        if not mime_data.hasUrls():
            self._status_bar.show_message("No files to paste from clipboard.", 3000)
            return

        files = [Path(url.toLocalFile()) for url in mime_data.urls()]
        album = self._facade.current_album
        if not album:
            self._status_bar.show_message("Open an album before pasting files.", 3000)
            return

        # Delegate importing to the facade so that all deduplication and bookkeeping logic is
        # reused. The toast provides quick feedback because importing can take a noticeable
        # amount of time on large selections.
        self._facade.import_files(files, destination=album.root)
        self._toast.show_toast("Pasting files...")

    def _open_current_folder(self) -> None:
        """Open the current album folder in the desktop file manager."""

        album = self._facade.current_album
        if not album:
            self._status_bar.show_message("No album is currently open.", 3000)
            return

        path = album.root
        if not path.exists():
            self._status_bar.show_message(f"Folder not found: {path}", 3000)
            return

        # The command mirrors the implementation in ``_reveal_selection_in_file_manager`` but we
        # open the folder itself, not a specific file.
        if sys.platform == "win32":
            subprocess.run(["explorer", str(path)], check=False)
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)

    def _execute_move_to_album(self, target: Path) -> None:
        """Move the currently selected assets to ``target`` while updating the view."""

        paths = self._selected_asset_paths()
        if not paths:
            self._status_bar.show_message("Select items to move first.", 3000)
            return

        self._apply_optimistic_move(paths, destination_root=target)

        try:
            self._prepare_file_mutation(paths)
            self._facade.move_assets(paths, target)
        except Exception:
            rollback = getattr(self._asset_model, "rollback_pending_moves", None)
            if callable(rollback):
                rollback()
            raise
        finally:
            if self._selection_controller is not None:
                self._selection_controller.set_selection_mode(False)

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------
    def _selected_asset_paths(self) -> list[Path]:
        """Return absolute paths for all selected assets without duplicates."""

        return self._selected_paths_provider(self._selected_rows())

    def _selected_rows(self) -> list[int]:
        selection_model = self._grid_view.selectionModel()
        if selection_model is None:
            return []
        return sorted(
            {
                index.row()
                for index in selection_model.selectedIndexes()
                if index.isValid()
            }
        )

    def _selected_assets(self, rows: list[int]) -> list:
        if self._gallery_viewmodel is None:
            return []
        items_for_rows = getattr(self._gallery_viewmodel, "items_for_rows", None)
        if not callable(items_for_rows):
            return []
        return list(items_for_rows(rows))

    def _menu_context(self) -> MenuContext:
        base_context = self._base_menu_context()
        rows = self._selected_rows()
        selection_kind = "assets" if rows else "empty"
        return base_context.with_selection(
            selection_kind=selection_kind,
            selected_assets=self._selected_assets(rows),
        )

    def _base_menu_context(self) -> MenuContext:
        if self._gallery_viewmodel is not None:
            state_getter = getattr(self._gallery_viewmodel, "context_menu_state", None)
            if callable(state_getter):
                state = state_getter()
                if isinstance(state, MenuContext):
                    return state

        is_recently_deleted = (
            self._navigation.is_recently_deleted_view()
            if self._navigation is not None
            else False
        )
        return MenuContext(
            surface="gallery",
            selection_kind="empty",
            gallery_section="recently_deleted" if is_recently_deleted else None,
            active_root=getattr(getattr(self._facade, "current_album", None), "root", None),
            is_recently_deleted=is_recently_deleted,
        )

    def _collect_move_targets(self) -> list[tuple[str, Path]]:
        """Build a list of (label, path) destinations excluding the currently open album."""

        if self._navigation is None:
            return []
        sidebar_model = getattr(self._navigation, "sidebar_model", None)
        if not callable(sidebar_model):
            return []
        model = sidebar_model()
        iter_album_entries = getattr(model, "iter_album_entries", None)
        if not callable(iter_album_entries):
            return []
        try:
            entries = list(iter_album_entries())
        except TypeError:
            return []
        current_album = self._facade.current_album
        current_root: Path | None = None
        if current_album is not None:
            try:
                current_root = current_album.root.resolve()
            except OSError:
                current_root = current_album.root

        destinations: list[tuple[str, Path]] = []
        for label, path in entries:
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if current_root is not None and resolved == current_root:
                continue
            destinations.append((label, path))
        return destinations

    def _set_as_cover_visible(self, context: MenuContext) -> bool:
        asset = context.selected_asset
        if asset is None or context.is_recently_deleted:
            return False
        if context.entity_kind == "album":
            return context.gallery_section in {"album", "pinned_album"}
        if context.entity_kind == "person" and context.entity_id:
            return (
                self._people_service_for_context(context).resolve_cluster_cover_face(
                    context.entity_id,
                    asset.id,
                )
                is not None
            )
        if context.entity_kind == "group" and context.entity_id:
            return (
                self._people_service_for_context(context).resolve_group_cover_asset(
                    context.entity_id,
                    asset.id,
                )
                is not None
            )
        return False

    def _set_as_cover(self, context: MenuContext) -> None:
        asset = context.selected_asset
        if asset is None:
            return

        success = False
        if context.entity_kind == "album":
            success = self._facade.set_cover(asset.rel_path.as_posix())
        elif context.entity_kind == "person" and context.entity_id:
            service = self._people_service_for_context(context)
            face_id = service.resolve_cluster_cover_face(context.entity_id, asset.id)
            success = bool(face_id) and service.set_cluster_cover(context.entity_id, face_id)
        elif context.entity_kind == "group" and context.entity_id:
            service = self._people_service_for_context(context)
            group_asset_id = service.resolve_group_cover_asset(context.entity_id, asset.id)
            success = bool(group_asset_id) and service.set_group_cover(context.entity_id, group_asset_id)

        if success:
            self._toast.show_toast("Cover Updated")
            return
        self._status_bar.show_message(
            "Unable to set cover for the selected item.",
            3000,
        )

    def _people_service_for_context(self, context: MenuContext) -> PeopleService:
        return PeopleService(context.active_root)

    def _remove_selection_rows(self, selected_indexes: list) -> None:
        if not selected_indexes:
            return
        source_model = getattr(self._asset_model, "source_model", None)
        if callable(source_model):
            resolved_model = source_model()
        else:
            resolved_model = None
        if resolved_model is not None and hasattr(resolved_model, "remove_rows"):
            resolved_model.remove_rows(selected_indexes)
            return
        remove_rows = getattr(self._asset_model, "remove_rows", None)
        if callable(remove_rows):
            remove_rows(selected_indexes)
            return
        remove_rows = getattr(self._asset_model, "removeRows", None)
        if callable(remove_rows):
            rows = sorted({index.row() for index in selected_indexes}, reverse=True)
            for row in rows:
                remove_rows(row, 1)

    def _apply_optimistic_move(
        self,
        paths: list[Path],
        *,
        destination_root: Path | None = None,
        is_delete: bool = False,
    ) -> bool:
        handler = getattr(self._asset_model, "optimistic_move_paths", None)
        if not callable(handler):
            return False
        if destination_root is None and is_delete:
            library = self._facade.library_manager
            if library is None:
                return False
            destination_root = library.deleted_directory()
        if destination_root is None:
            return False
        return bool(handler(paths, destination_root, is_delete=is_delete))

    def _prepare_file_mutation(self, paths: list[Path]) -> None:
        if self._prepare_paths_for_mutation is None or not paths:
            return
        self._prepare_paths_for_mutation(paths)

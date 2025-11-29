"""Controller for export operations."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QObject, QThreadPool, QRunnable, Signal
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QWidget, QFileDialog

from ....core.export import export_asset
from ....config import EXPORT_DIR_NAME
from ....library.manager import LibraryManager
from ....io import sidecar
from ....cache.index_store import IndexStore
from ..widgets.notification_toast import NotificationToast
from .status_bar_controller import StatusBarController
from ...ui.widgets.dialogs import show_error


class ExportSignals(QObject):
    """Signals emitted by export workers."""
    progress = Signal(int, int)
    finished = Signal(int, int)
    message = Signal(str)


class ExportWorker(QRunnable):
    """Background worker for exporting a specific list of assets."""

    def __init__(self, paths: list[Path], export_root: Path, library_root: Path):
        super().__init__()
        self._paths = paths
        self._export_root = export_root
        self._library_root = library_root
        self.signals = ExportSignals()

    def run(self) -> None:
        total = len(self._paths)
        success = 0
        fail = 0
        for i, path in enumerate(self._paths):
            if not path.exists():
                continue
            if export_asset(path, self._export_root, self._library_root):
                success += 1
            else:
                fail += 1
            self.signals.progress.emit(i + 1, total)
        self.signals.finished.emit(success, fail)


class LibraryExportWorker(QRunnable):
    """Background worker for scanning the library and exporting edited assets."""

    def __init__(self, library: LibraryManager, export_root: Path):
        super().__init__()
        self._library = library
        self._export_root = export_root
        self.signals = ExportSignals()

    def run(self) -> None:
        self.signals.message.emit("Scanning library for edited images...")
        root = self._library.root()
        if not root:
            self.signals.finished.emit(0, 0)
            return

        # Collect all album paths (Root + Level 1 + Level 2)
        album_paths = {root}
        top_albums = self._library.list_albums()
        for album in top_albums:
            album_paths.add(album.path)
            for child in self._library.list_children(album):
                album_paths.add(child.path)

        to_export = []
        for album_path in album_paths:
            try:
                rows = IndexStore(album_path).read_all()
            except Exception:
                continue

            for row in rows:
                if not isinstance(row, dict):
                    continue
                rel = row.get("rel")
                if not rel or not isinstance(rel, str):
                    continue
                abs_path = (album_path / rel).resolve()
                if sidecar.sidecar_path_for_asset(abs_path).exists():
                    to_export.append(abs_path)

        total = len(to_export)
        if total == 0:
            self.signals.finished.emit(0, 0)
            return

        self.signals.message.emit(f"Exporting {total} edited images...")

        success = 0
        fail = 0
        for i, path in enumerate(to_export):
            if export_asset(path, self._export_root, root):
                success += 1
            else:
                fail += 1
            self.signals.progress.emit(i + 1, total)

        self.signals.finished.emit(success, fail)


class ExportController(QObject):
    """Controller orchestrating asset export workflows."""

    def __init__(
        self,
        *,
        settings,
        library: LibraryManager,
        status_bar: StatusBarController,
        toast: NotificationToast,
        export_all_action: QAction,
        export_selected_action: QAction,
        destination_group: QActionGroup,
        destination_library: QAction,
        destination_ask: QAction,
        main_window: QWidget,
        selection_callback: Callable[[], list[Path]],
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._library = library
        self._status_bar = status_bar
        self._toast = toast
        self._export_all_action = export_all_action
        self._export_selected_action = export_selected_action
        self._destination_group = destination_group
        self._destination_library = destination_library
        self._destination_ask = destination_ask
        self._main_window = main_window
        self._get_selection = selection_callback

        self._export_all_action.triggered.connect(self._handle_export_all_edited)
        self._export_selected_action.triggered.connect(self._handle_export_selected)
        self._destination_group.triggered.connect(self._handle_destination_changed)

        self.restore_preference()

    def restore_preference(self) -> None:
        """Apply the persisted destination choice to the action group."""
        dest = self._settings.get("ui.export_destination", "library")
        if dest == "ask":
            self._destination_ask.setChecked(True)
        else:
            self._destination_library.setChecked(True)

    def _handle_destination_changed(self, action: QAction) -> None:
        if action is self._destination_ask:
            self._settings.set("ui.export_destination", "ask")
        else:
            self._settings.set("ui.export_destination", "library")

    def _resolve_export_root(self) -> Optional[Path]:
        dest = self._settings.get("ui.export_destination", "library")
        library_root = self._library.root()
        if not library_root:
            show_error(self._main_window, "Library not bound.")
            return None

        if dest == "library":
            path = library_root / EXPORT_DIR_NAME
            try:
                path.mkdir(exist_ok=True)
            except OSError as exc:
                show_error(self._main_window, f"Could not create export folder: {exc}")
                return None
            return path
        else:
            selected = QFileDialog.getExistingDirectory(self._main_window, "Select Export Destination")
            if not selected:
                return None
            return Path(selected)

    def _handle_export_selected(self) -> None:
        paths = self._get_selection()
        if not paths:
            self._status_bar.show_message("No items selected.", 3000)
            return

        export_root = self._resolve_export_root()
        if not export_root:
            return

        library_root = self._library.root()
        if not library_root:
            show_error(self._main_window, "Library not bound.")
            return

        worker = ExportWorker(paths, export_root, library_root)
        self._start_worker(worker)

    def _handle_export_all_edited(self) -> None:
        export_root = self._resolve_export_root()
        if not export_root:
            return

        worker = LibraryExportWorker(self._library, export_root)
        self._start_worker(worker)

    def _start_worker(self, worker: QRunnable) -> None:
        worker.signals.progress.connect(self._on_progress)
        worker.signals.finished.connect(self._on_finished)
        worker.signals.message.connect(self._status_bar.show_message)

        self._status_bar.show_message("Starting export...", 0)
        QThreadPool.globalInstance().start(worker)

    def _on_progress(self, current: int, total: int) -> None:
        self._status_bar.show_message(f"Exporting {current}/{total}...")

    def _on_finished(self, success: int, fail: int) -> None:
        msg = f"Export complete. {success} exported"
        if fail > 0:
            msg += f", {fail} failed"
        self._status_bar.show_message(msg, 5000)
        self._toast.show_toast(msg)

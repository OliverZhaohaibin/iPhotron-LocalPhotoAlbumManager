"""Dialog orchestration helpers for the main window."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import QWidget

# Allow both ``iPhoto.gui`` and legacy ``iPhoto.gui`` import paths.
try:  # pragma: no cover - depends on runtime packaging
    from ...appctx import AppContext
except ImportError:  # pragma: no cover - fallback for script execution
    from iPhoto.appctx import AppContext
from typing import TYPE_CHECKING
from ....errors import LibraryError
from ....config import DEFAULT_EXCLUDE, DEFAULT_INCLUDE, WORK_DIR_NAME
from ..widgets import dialogs

if TYPE_CHECKING:
    from ..widgets.chrome_status_bar import ChromeStatusBar


class DialogController:
    """Centralise dialog and message interactions."""

    def __init__(self, parent: QWidget, context: AppContext, status_bar: ChromeStatusBar) -> None:
        self._parent = parent
        self._context = context
        self._status = status_bar

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def open_album_dialog(self) -> Optional[Path]:
        return dialogs.select_directory(self._parent, "Select album")

    def bind_library_dialog(self) -> Optional[Path]:
        root = dialogs.select_directory(self._parent, "Select Basic Library")
        if root is None:
            return None
        try:
            if self._context.library.root() is not None:
                self._context.facade.cancel_active_scans()
            self._context.library.bind_path(root)
        except LibraryError as exc:
            dialogs.show_error(self._parent, str(exc))
            return None
        bound_root = self._context.library.root()
        if bound_root is not None:
            self._context.settings.set("basic_library_path", str(bound_root))
            self._start_initial_scan_if_needed(bound_root)
            self._status.showMessage(f"Basic Library bound to {bound_root}")
            try:
                self._context.facade.open_album(bound_root)
            except Exception:
                # Keep binding success even if the initial open fails.
                pass
            sidebar = getattr(getattr(self._parent, "ui", None), "sidebar", None)
            if sidebar is not None:
                sidebar.select_all_photos(emit_signal=True)
        return bound_root

    def _start_initial_scan_if_needed(self, bound_root: Path) -> None:
        work_dir = bound_root / WORK_DIR_NAME
        db_path = work_dir / "global_index.db"
        if work_dir.exists() and db_path.exists():
            return
        if self._context.library.is_scanning_path(bound_root):
            return
        self._context.library.start_scanning(
            bound_root,
            DEFAULT_INCLUDE,
            DEFAULT_EXCLUDE,
        )

    def show_error(self, message: str) -> None:
        dialogs.show_error(self._parent, message)

    def prompt_for_basic_library(self) -> None:
        dialogs.show_information(
            self._parent,
            "Select a folder to use as your Basic Library.",
            title="Bind Basic Library",
        )
        self.bind_library_dialog()

    def prompt_restore_to_root(self, filename: str) -> bool:
        """Ask whether *filename* should be restored to the library root."""

        message = (
            "The original album for '{name}' could not be found or its original "
            "location could not be determined. Do you want to restore this file "
            "to the main 'Basic Library' folder instead?"
        ).format(name=filename)
        return dialogs.confirm_action(
            self._parent,
            message,
            title="Restore Failed",
            yes_label="Yes",
            no_label="No",
        )

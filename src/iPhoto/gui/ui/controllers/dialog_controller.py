"""Dialog orchestration helpers for the main window."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import QWidget

from typing import TYPE_CHECKING
from ....application.contracts.runtime_entry_contract import RuntimeEntryContract
from ....errors import LibraryError
from ....config import DEFAULT_EXCLUDE, DEFAULT_INCLUDE
from ....utils.pathutils import resolve_work_dir
from ..widgets import dialogs

if TYPE_CHECKING:
    from ..widgets.chrome_status_bar import ChromeStatusBar

_logger = logging.getLogger(__name__)


class DialogController:
    """Centralise dialog and message interactions."""

    def __init__(
        self,
        parent: QWidget,
        context: RuntimeEntryContract,
        status_bar: ChromeStatusBar,
    ) -> None:
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
            _logger.info("bind_library_dialog: user cancelled folder selection")
            return None
        _logger.info("bind_library_dialog: user selected folder %s", root)
        try:
            if self._context.library.root() is not None:
                _logger.info("bind_library_dialog: cancelling active scans before rebind")
                self._context.facade.cancel_active_scans()
            self._context.library.bind_path(root)
            _logger.info("bind_library_dialog: bind_path succeeded, root=%s", self._context.library.root())
        except LibraryError as exc:
            _logger.error("bind_library_dialog: bind_path failed: %s", exc)
            dialogs.show_error(self._parent, str(exc))
            return None
        bound_root = self._context.library.root()
        if bound_root is not None:
            self._context.settings.set("basic_library_path", str(bound_root))
            self._start_initial_scan_if_needed(bound_root)
            self._status.showMessage(f"Basic Library bound to {bound_root}")
            try:
                self._context.facade.open_album(bound_root)
                _logger.info("bind_library_dialog: facade.open_album succeeded")
            except Exception:
                _logger.exception("bind_library_dialog: facade.open_album failed")
            sidebar = getattr(getattr(self._parent, "ui", None), "sidebar", None)
            if sidebar is not None:
                _logger.info("bind_library_dialog: selecting All Photos in sidebar")
                sidebar.select_all_photos(emit_signal=True)
            else:
                _logger.warning("bind_library_dialog: sidebar not found on parent")
        else:
            _logger.warning("bind_library_dialog: library.root() is None after bind_path")
        return bound_root

    def _start_initial_scan_if_needed(self, bound_root: Path) -> None:
        work_dir = resolve_work_dir(bound_root)
        db_path = work_dir / "global_index.db" if work_dir is not None else None
        if db_path is not None and db_path.exists():
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

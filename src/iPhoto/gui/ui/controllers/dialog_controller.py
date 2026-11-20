"""Dialog orchestration helpers for the main window."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QWidget

from ....application.contracts.runtime_entry_contract import RuntimeEntryContract
from ..widgets import dialogs

if TYPE_CHECKING:
    from ....bootstrap.library_probe import (
        LibraryProbeController,
        LibraryProbeFailure,
        ValidatedPreparedLibrary,
    )
    from ..widgets.chrome_status_bar import ChromeStatusBar

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _PendingLibrarySwitch:
    request_id: str
    library_epoch: int
    opened: Callable[[Path], None]
    failed: Callable[[str], None]


class DialogController:
    """Centralise dialog and message interactions."""

    def __init__(
        self,
        parent: QWidget,
        context: RuntimeEntryContract,
        status_bar: ChromeStatusBar,
        library_rebind_preflight: Callable[[], bool] | None = None,
    ) -> None:
        self._parent = parent
        self._context = context
        self._status = status_bar
        self._library_rebind_preflight = library_rebind_preflight
        self._library_probe: LibraryProbeController | None = None
        self._pending_library_switch: _PendingLibrarySwitch | None = None

    def set_library_probe_controller(self, probe: LibraryProbeController) -> None:
        self._library_probe = probe
        probe.ready.connect(self._on_library_prepared)
        probe.failed.connect(self._on_library_probe_failed)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def open_album_dialog(self) -> Path | None:
        return dialogs.select_directory(
            self._parent,
            QCoreApplication.translate("DialogController", "Select album", None),
        )

    def bind_library_dialog(self) -> Path | None:
        if not self._can_switch_library(self.show_error):
            return None
        root = dialogs.select_directory(
            self._parent,
            QCoreApplication.translate("DialogController", "Select Basic Library", None),
            use_qt_directory_dialog_on_macos=True,
        )
        if root is None:
            _logger.info("bind_library_dialog: user cancelled folder selection")
            return None
        _logger.info("bind_library_dialog: user selected folder %s", root)
        return root if self.request_library_switch(root, self._finish_bind_library_dialog) else None

    def request_library_switch(
        self,
        root: Path,
        on_opened: Callable[[Path], None],
        *,
        on_failed: Callable[[str], None] | None = None,
    ) -> bool:
        failed = on_failed or self.show_error
        if not self._can_switch_library(failed):
            return False
        probe = self._library_probe
        if probe is None:
            from ....bootstrap.library_probe import LibraryProbeController

            probe = LibraryProbeController(self._parent)
            self.set_library_probe_controller(probe)
        request = self._context.request_library_switch_probe(root)
        self._pending_library_switch = _PendingLibrarySwitch(
            request.request_id, self._context.library_epoch, on_opened, failed,
        )
        probe.start(request)
        return True

    def cancel_library_switch(self) -> None:
        if self._pending_library_switch is not None:
            self._pending_library_switch = None
            if self._library_probe is not None:
                self._library_probe.cancel()

    def has_pending_library_switch(self) -> bool:
        return self._pending_library_switch is not None

    def _can_switch_library(self, failed: Callable[[str], None]) -> bool:
        if self._library_rebind_preflight is None or self._library_rebind_preflight():
            return True
        failed(QCoreApplication.translate(
            "DialogController",
            "Finish the current edit with Done, or press Escape to cancel it, before switching libraries.",
            None,
        ))
        return False

    def _take_library_switch(self, request_id: str) -> _PendingLibrarySwitch | None:
        pending = self._pending_library_switch
        if pending is None or pending.request_id != request_id:
            return None
        self._pending_library_switch = None
        return pending if pending.library_epoch == self._context.library_epoch else None

    def _on_library_prepared(self, validated: ValidatedPreparedLibrary) -> None:
        pending = self._take_library_switch(validated.request_id)
        if pending is None or not self._can_switch_library(pending.failed):
            return
        try:
            session = self._context.commit_validated_library(validated, defer_scan=False)
        except Exception as exc:
            _logger.warning("Prepared library could not be bound", exc_info=True)
            pending.failed(str(exc))
            return
        pending.opened(session.library_root)

    def _on_library_probe_failed(self, failure: LibraryProbeFailure) -> None:
        pending = self._take_library_switch(failure.request_id)
        if pending is not None:
            pending.failed(failure.message)

    def _finish_bind_library_dialog(self, root: Path) -> None:
        self._context.settings.set("basic_library_path", str(root))
        self._status.showMessage(QCoreApplication.translate(
            "DialogController", "Basic Library bound to {path}", None,
        ).format(path=root))
        try:
            self._context.facade.open_album(root)
        except Exception:
            _logger.exception("bind_library_dialog: facade.open_album failed")
        sidebar = getattr(getattr(self._parent, "ui", None), "sidebar", None)
        if sidebar is not None:
            sidebar.select_all_photos(emit_signal=True)

    def show_error(self, message: str) -> None:
        dialogs.show_error(self._parent, message)

    def prompt_for_basic_library(self) -> None:
        dialogs.show_information(
            self._parent,
            QCoreApplication.translate(
                "DialogController",
                "Select a folder to use as your Basic Library.",
                None,
            ),
            title=QCoreApplication.translate(
                "DialogController",
                "Bind Basic Library",
                None,
            ),
        )
        self.bind_library_dialog()

    def prompt_restore_to_root(self, filename: str) -> bool:
        """Ask whether *filename* should be restored to the library root."""

        message = QCoreApplication.translate(
            "DialogController",
            "The original album for '{filename}' could not be found or its original "
            "location could not be determined. Do you want to restore this file "
            "to the main 'Basic Library' folder instead?",
            None,
        ).format(filename=filename)
        return dialogs.confirm_action(
            self._parent,
            message,
            title=QCoreApplication.translate("DialogController", "Restore Failed", None),
            yes_label=QCoreApplication.translate("DialogController", "Yes", None),
            no_label=QCoreApplication.translate("DialogController", "No", None),
        )

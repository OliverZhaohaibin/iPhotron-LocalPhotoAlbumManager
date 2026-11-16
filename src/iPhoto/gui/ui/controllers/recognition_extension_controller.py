"""Desktop menu, progress and recovery for the People & Pets model package."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThreadPool, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog, QProgressDialog, QWidget

from iPhoto.application.contracts.runtime_entry_contract import RuntimeEntryContract
from iPhoto.application.ports.extension_install import ExtensionInstallError
from iPhoto.application.ports.recognition_extension import (
    RecognitionExtensionRequest,
    RecognitionExtensionResult,
    RecognitionModelFailure,
)
from iPhoto.gui.i18n import tr
from iPhoto.gui.ui.tasks.recognition_extension_worker import RecognitionExtensionWorker
from iPhoto.gui.ui.widgets import dialogs
from iPhoto.gui.ui.widgets.information_popup import InformationPopup


class RecognitionExtensionController(QObject):
    def __init__(self, window: QWidget, context: RuntimeEntryContract):
        super().__init__(window)
        self._window = window
        self._context = context
        self._popup: InformationPopup | None = None
        self._visible_recovery: RecognitionModelFailure | None = None
        self._pending_recovery: RecognitionModelFailure | None = None
        self._recovery_shown = False
        self._progress_dialog: QProgressDialog | None = None
        self._worker: RecognitionExtensionWorker | None = None
        self._closing = False
        self._choosing_archive = False

    def show_options(self) -> None:
        if self._closing:
            return
        if self._progress_dialog is not None:
            self._progress_dialog.show()
            self._progress_dialog.raise_()
            return
        self._show_popup(
            self._tr("Choose how to install the People & Pets extension."),
            (
                (self._tr("Download"), self._start),
                *self._recovery_actions(self._tr("Cancel")),
            ),
        )

    def model_unavailable(self, failure: RecognitionModelFailure) -> None:
        if self._closing or self._recovery_shown or failure.reason == "download_disabled":
            return
        if self._context.library.recognition_model_failure_is_current(failure):
            self._pending_recovery = failure
            self._maybe_show_recovery()

    def library_changed(self) -> None:
        self._pending_recovery = None
        if self._visible_recovery is not None and self._popup is not None:
            self._popup.close()

    def shutdown(self) -> None:
        self._closing = True
        if self._popup is not None:
            self._popup.close()
        self._close_progress()

    def _maybe_show_recovery(self) -> None:
        failure = self._pending_recovery
        if (
            failure is None
            or self._closing
            or self._recovery_shown
            or self._choosing_archive
            or self._popup is not None
            or self._worker is not None
        ):
            return
        self._pending_recovery = None
        if not self._context.library.recognition_model_failure_is_current(failure):
            return
        self._recovery_shown = True
        self._show_popup(
            self._tr(
                "Could not automatically get all People & Pets recognition models. "
                "Download the package in your browser, then install it from a file."
            ),
            self._recovery_actions(self._tr("Later")),
            recovery=failure,
        )

    def _recovery_actions(self, close_label):
        return (
            (self._tr("Download in Browser"), self._open_browser),
            (self._tr("Install from File…"), self._install_from_file),
            (close_label, None),
        )

    def _show_popup(self, message, actions, *, recovery=None) -> None:
        if self._popup is not None:
            self._popup.close()
        popup = dialogs.open_action_popup(
            self._window, message, title=self._tr("People & Pets Extension"), actions=actions
        )
        self._popup = popup
        self._visible_recovery = recovery

        def closed() -> None:
            if self._popup is popup:
                self._popup = None
                self._visible_recovery = None
            QTimer.singleShot(0, self, self._maybe_show_recovery)

        popup.closed.connect(closed)

    def _open_browser(self) -> None:
        url = self._context.recognition_extensions.download_url
        if QDesktopServices.openUrl(QUrl(url)):
            message = self._tr("Select the archive after the download finishes.")
        else:
            message = self._tr("Could not open the browser. Copy this link to download the archive.")
            message = f"{message}\n\n{url}"
        self._show_popup(
            message,
            (
                (self._tr("Install from File…"), self._install_from_file),
                (self._tr("Close"), None),
            ),
        )

    def _install_from_file(self) -> None:
        self._choosing_archive = True
        try:
            path, _ = QFileDialog.getOpenFileName(
                self._window,
                self._tr("Install People & Pets Extension"),
                "",
                self._tr("Recognition extension archives (*.zip)"),
            )
        finally:
            self._choosing_archive = False
        if path:
            self._start(Path(path))
        else:
            self._maybe_show_recovery()

    def _start(self, archive: Path | None = None) -> None:
        if self._closing or self._worker is not None:
            return
        progress = QProgressDialog(
            self._tr("Preparing recognition extension…"), "", 0, 0, self._window
        )
        progress.setWindowTitle(self._tr("People & Pets Extension"))
        progress.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        progress.setCancelButton(None)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        self._progress_dialog = progress
        worker = RecognitionExtensionWorker(
            RecognitionExtensionRequest(archive), self._context.recognition_extensions
        )
        worker.signals.progress.connect(self._update_progress, Qt.ConnectionType.QueuedConnection)
        worker.signals.finished.connect(self._finished, Qt.ConnectionType.QueuedConnection)
        self._worker = worker
        progress.show()
        QThreadPool.globalInstance().start(worker, -1)

    def _update_progress(self, current: int, total: int, message: str) -> None:
        progress = self._progress_dialog
        if progress is not None:
            progress.setLabelText(self._tr(message))
            progress.setRange(0, total)
            progress.setValue(current)

    def _close_progress(self) -> None:
        progress = self._progress_dialog
        self._progress_dialog = None
        if progress is not None:
            progress.close()
            progress.deleteLater()

    def _finished(self, result: RecognitionExtensionResult | ExtensionInstallError) -> None:
        self._worker = None
        self._close_progress()
        self._pending_recovery = None
        if self._closing:
            return
        if isinstance(result, RecognitionExtensionResult):
            self._context.library.recognition_extension_installed()
            return

        if result.stage == "download":
            message = self._tr(
                "Could not download the People & Pets extension. "
                "Download it in your browser and install it from a file."
            )
        elif result.category in {
            "missing_file", "unknown_package", "integrity", "incomplete", "unsafe_archive",
        }:
            message = self._tr("The selected archive is missing, damaged, or unsupported.")
        elif result.category == "busy":
            message = self._tr("Another People & Pets extension installation is running.")
        else:
            message = self._tr("Could not install the People & Pets extension.")
        self._show_popup(f"{message}\n\n{result}", self._recovery_actions(self._tr("Close")))

    @staticmethod
    def _tr(message: str) -> str:
        return tr("RecognitionExtension", message)

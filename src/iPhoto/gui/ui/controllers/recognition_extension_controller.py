"""Desktop menu, progress and recovery for the People & Pets model package."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThreadPool, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog, QWidget

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
from iPhoto.gui.ui.widgets.information_popup import InformationPopup, ProgressPopup
from iPhoto.gui.ui.widgets.notification_toast import NotificationToast


class RecognitionExtensionController(QObject):
    def __init__(
        self,
        window: QWidget,
        context: RuntimeEntryContract,
        *,
        notification_toast: NotificationToast,
    ):
        super().__init__(window)
        self._window = window
        self._context = context
        self._toast = notification_toast
        self._popup: InformationPopup | None = None
        self._visible_recovery: RecognitionModelFailure | None = None
        self._pending_recovery: RecognitionModelFailure | None = None
        self._pending_override: RecognitionModelFailure | None = None
        self._recovery_shown = False
        self._override_warning_shown = False
        self._automatic_attempted = False
        self._silent_install = False
        self._progress_dialog: ProgressPopup | None = None
        self._worker: RecognitionExtensionWorker | None = None
        self._closing = False
        self._choosing_archive = False

    def show_options(self) -> None:
        if self._closing:
            return
        if self._worker is not None:
            self._show_progress()
            return
        self._show_popup(
            self._tr("Choose how to install the People & Pets extension."),
            (
                *(
                    ((self._tr("Download"), self._start),)
                    if self._context.recognition_extensions.download_url
                    else ()
                ),
                *self._recovery_actions(self._tr("Cancel")),
            ),
        )

    def model_unavailable(self, failure: RecognitionModelFailure) -> None:
        if self._closing or failure.reason == "download_disabled":
            return
        if not self._context.library.recognition_model_failure_is_current(failure):
            return
        if failure.reason == "invalid_override":
            if not self._override_warning_shown:
                self._pending_override = failure
        elif not self._recovery_shown:
            self._pending_recovery = failure
        self._resolve_pending_models()

    def library_changed(self) -> None:
        self._pending_recovery = None
        self._pending_override = None
        if self._visible_recovery is not None and self._popup is not None:
            self._popup.close()

    def shutdown(self) -> None:
        self._closing = True
        if self._worker is not None:
            self._worker.cancel()
        if self._popup is not None:
            self._popup.close()
        self._close_progress()

    def _resolve_pending_models(self) -> None:
        if self._closing or self._choosing_archive or self._worker is not None:
            return
        failure = self._pending_recovery
        if failure is not None and not self._context.library.recognition_model_failure_is_current(
            failure
        ):
            self._pending_recovery = None
            failure = None
        if failure is None:
            self._show_override_warning()
            return
        if self._popup is not None and (
            self._visible_recovery is None or self._visible_recovery.reason != "invalid_override"
        ):
            return
        if not self._automatic_attempted and self._context.recognition_extensions.download_url:
            self._automatic_attempted = True
            self._start(silent=True)
            return
        if self._popup is not None:
            return
        self._pending_recovery = None
        self._recovery_shown = True
        self._show_popup(
            self._tr(
                "Could not automatically get all People & Pets recognition models. "
                "Download the package in your browser, then install it from a file."
            )
            if self._context.recognition_extensions.download_url
            else self._unpublished_message(),
            self._recovery_actions(self._tr("Later")),
            recovery=failure,
        )

    def _show_override_warning(self) -> None:
        failure = self._pending_override
        if failure is None or self._popup is not None:
            return
        self._pending_override = None
        if not self._context.library.recognition_model_failure_is_current(failure):
            return
        self._override_warning_shown = True
        self._show_popup(
            self._tr(
                "The directory set in IPHOTO_FACE_MODEL_DIR is missing usable face models. "
                "Fix or unset IPHOTO_FACE_MODEL_DIR, then rescan."
            ),
            ((self._tr("Close"), None),),
            recovery=failure,
        )

    def _recovery_actions(self, close_label):
        return (
            *(
                ((self._tr("Download in Browser"), self._open_browser),)
                if self._context.recognition_extensions.download_url
                else ()
            ),
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
            QTimer.singleShot(0, self, self._resolve_pending_models)

        popup.closed.connect(closed)

    def _open_browser(self) -> None:
        url = self._context.recognition_extensions.download_url
        if not url:
            message = self._unpublished_message()
        elif QDesktopServices.openUrl(QUrl(url)):
            message = self._tr("Select the archive after the download finishes.")
        else:
            message = self._tr(
                "Could not open the browser. Copy this link to download the archive."
            )
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
            self._resolve_pending_models()

    def _start(self, archive: Path | None = None, *, silent: bool = False) -> None:
        if self._closing or self._worker is not None:
            return
        worker = RecognitionExtensionWorker(
            RecognitionExtensionRequest(archive), self._context.recognition_extensions
        )
        worker.signals.progress.connect(self._update_progress, Qt.ConnectionType.QueuedConnection)
        worker.signals.finished.connect(self._finished, Qt.ConnectionType.QueuedConnection)
        self._worker = worker
        self._silent_install = silent
        if not silent:
            self._show_progress()
        QThreadPool.globalInstance().start(worker, -1)

    def _show_progress(self) -> None:
        progress = self._progress_dialog
        if progress is None:
            self._progress_dialog = dialogs.open_progress_popup(
                self._window,
                title=self._tr("People & Pets Extension"),
                message=self._tr("Preparing recognition extension…"),
            )
        else:
            progress.show()
            progress.raise_()

    def _update_progress(self, current: int, total: int, message: str) -> None:
        if self._progress_dialog is not None:
            self._progress_dialog.update_progress(current, total, self._tr(message))

    def _close_progress(self) -> None:
        progress = self._progress_dialog
        self._progress_dialog = None
        if progress is not None:
            progress.close()
            progress.deleteLater()

    def _finished(self, result: RecognitionExtensionResult | ExtensionInstallError) -> None:
        silent = self._silent_install
        self._silent_install = False
        self._worker = None
        self._close_progress()
        if self._closing:
            return
        if isinstance(result, RecognitionExtensionResult):
            self._pending_recovery = None
            self._context.library.recognition_extension_installed()
            if not silent:
                self._toast.show_toast(tr("NotificationToast", "Installed"))
            self._resolve_pending_models()
            return
        if silent:
            self._resolve_pending_models()
            return

        self._pending_recovery = None
        if result.category == "unpublished":
            message = self._unpublished_message()
        elif result.stage == "download":
            message = self._tr(
                "Could not download the People & Pets extension. "
                "Download it in your browser and install it from a file."
            )
        elif result.category in {
            "missing_file",
            "unknown_package",
            "integrity",
            "incomplete",
            "unsafe_archive",
        }:
            message = self._tr("The selected archive is missing, damaged, or unsupported.")
        elif result.category == "busy":
            message = self._tr("Another People & Pets extension installation is running.")
        else:
            message = self._tr("Could not install the People & Pets extension.")
        self._show_popup(f"{message}\n\n{result}", self._recovery_actions(self._tr("Close")))

    def _unpublished_message(self) -> str:
        return tr(
            "RecognitionExtension",
            "The new People & Pets package is not available for download yet. "
            "Install a verified package from a file.",
        )

    @staticmethod
    def _tr(message: str) -> str:
        return tr("RecognitionExtension", message)

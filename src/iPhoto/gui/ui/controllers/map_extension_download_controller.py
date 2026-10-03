"""Shared UI controller for downloading and activating the map extension."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QProcess, Qt, QThreadPool, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from iPhoto.application.contracts.runtime_entry_contract import RuntimeEntryContract
from iPhoto.application.ports.map_extension import MapExtensionError
from iPhoto.gui.ui.tasks.map_extension_download_worker import (
    MapExtensionDownloadRequest,
    MapExtensionDownloadWorker,
)

_SHOW_STARTUP_PROMPT_KEY = "ui.show_map_extension_startup_prompt"


class _MapExtensionProgressDialog(QDialog):
    """Modal progress dialog used by every map-extension entry point."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setModal(True)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setMinimumWidth(420)
        self._allow_close = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        self._message_label = QLabel(self._tr("Preparing map extension download..."), self)
        self._message_label.setWordWrap(True)
        layout.addWidget(self._message_label)

        self._progress_bar = QProgressBar(self)
        self._progress_bar.setRange(0, 0)
        self._progress_bar.setValue(0)
        layout.addWidget(self._progress_bar)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self._status_label = QLabel("", self)
        footer.addWidget(self._status_label)
        layout.addLayout(footer)
        self.retranslate_ui()

    def update_progress(self, current: int, total: int, message: str) -> None:
        self._message_label.setText(message)
        if total > 0:
            self._progress_bar.setRange(0, total)
            self._progress_bar.setValue(max(0, min(current, total)))
            percent = int(round((current / total) * 100.0)) if total else 0
            self._status_label.setText(f"{percent}%")
        else:
            self._progress_bar.setRange(0, 0)
            self._status_label.clear()

    def allow_close(self) -> None:
        self._allow_close = True

    def retranslate_ui(self) -> None:
        self.setWindowTitle(self._tr("Map Extension"))

    def _tr(self, source_text: str) -> str:
        return QCoreApplication.translate("MapExtension", source_text, None)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if not self._allow_close:
            event.ignore()
            return
        super().closeEvent(event)


class MapExtensionDownloadController:
    """Own UI choices and serialize preparation before map surfaces are created."""

    def __init__(self, parent, context: RuntimeEntryContract, *, package_root: Path):
        self._parent = parent
        self._context = context
        self._package_root = Path(package_root).resolve()
        self._progress_dialog = None
        self._download_inflight = False
        self._latest_result = None
        self._latest_error = None
        self._active_worker = None
        self._last_request = None
        self._temporarily_hidden_windows = []
        self._startup_prompt = None
        self._runtime_prepared = False
        self._prepare_callbacks: list[Callable[[], None]] = []

    def _tr(self, text):
        return QCoreApplication.translate("MapExtension", text, None)

    def set_package_root(self, package_root):
        if package_root is not None:
            self._package_root = Path(package_root).resolve()

    def prepare_runtime(self, callback: Callable[[], None]) -> None:
        """Both map page and InfoPanel wait here before any capability probe."""
        if self._runtime_prepared:
            callback()
            return
        if callback not in self._prepare_callbacks:
            self._prepare_callbacks.append(callback)
        if not self._download_inflight:
            self._start(
                MapExtensionDownloadRequest(self._package_root, sys.platform, operation="prepare")
            )

    def maybe_prompt_on_startup(self) -> bool:
        if not self._runtime_prepared:
            self.prepare_runtime(lambda: self.maybe_prompt_on_startup())
            return True
        if (
            self._latest_result is None
            or self._latest_result.status != "missing"
            or self._startup_prompt is not None
            or not self._context.map_extensions.download_url(sys.platform)
            or not self._context.settings.get(_SHOW_STARTUP_PROMPT_KEY, True)
        ):
            return False
        box = QMessageBox(self._parent)
        box.setWindowTitle(self._tr("Map Extension"))
        box.setText(self._tr("Download the offline map extension now?"))
        download = box.addButton(self._tr("Download"), QMessageBox.ButtonRole.AcceptRole)
        local = box.addButton(self._tr("Install from File..."), QMessageBox.ButtonRole.ActionRole)
        box.addButton(self._tr("Not Now"), QMessageBox.ButtonRole.RejectRole)
        checkbox = QCheckBox(self._tr("Do not show again"), box)
        box.setCheckBox(checkbox)
        self._startup_prompt = box

        def finished(_result):
            self._startup_prompt = None
            if checkbox.isChecked():
                self._context.settings.set(_SHOW_STARTUP_PROMPT_KEY, False)
            if box.clickedButton() is download:
                self.start_download(source="startup")
            elif box.clickedButton() is local:
                self.install_from_file()
            box.deleteLater()

        box.finished.connect(finished)
        box.open()
        return False

    def show_options(self) -> None:
        if self._download_inflight:
            if self._progress_dialog is not None:
                self._progress_dialog.raise_()
            return
        box = QMessageBox(self._parent)
        box.setWindowTitle(self._tr("Map Extension"))
        box.setText(self._tr("Choose how to install the map extension."))
        online = box.addButton(self._tr("Download"), QMessageBox.ButtonRole.AcceptRole)
        browser = box.addButton(self._tr("Download in Browser"), QMessageBox.ButtonRole.ActionRole)
        local = box.addButton(self._tr("Install from File..."), QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        available = bool(self._context.map_extensions.download_url(sys.platform))
        online.setEnabled(available)
        browser.setEnabled(available)
        box.exec()
        if box.clickedButton() is online:
            self.start_download(source="settings")
        elif box.clickedButton() is browser:
            self.open_download_page()
        elif box.clickedButton() is local:
            self.install_from_file()

    def open_download_page(self):
        url = self._context.map_extensions.download_url(sys.platform)
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def install_from_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self._parent,
            self._tr("Install Map Extension"),
            "",
            self._tr("Map extension archives (*.zip *.tar.xz)"),
        )
        if path:
            self._start(
                MapExtensionDownloadRequest(
                    self._package_root,
                    sys.platform,
                    local_archive_path=Path(path),
                    defer_activation=self._runtime_prepared,
                )
            )

    def start_download(self, *, source: str, network_mode="system"):
        del source
        self._start(
            MapExtensionDownloadRequest(
                self._package_root,
                sys.platform,
                network_mode=network_mode,
                defer_activation=self._runtime_prepared,
            )
        )

    def _start(self, payload):
        if self._download_inflight:
            return
        self._download_inflight = True
        self._last_request = payload
        self._latest_result = None
        self._latest_error = None
        self._hide_blocking_top_level_windows()
        self._progress_dialog = _MapExtensionProgressDialog(self._parent)
        self._progress_dialog.show()
        worker = MapExtensionDownloadWorker(payload, self._context.map_extensions)
        worker.signals.progress.connect(self._handle_progress)
        worker.signals.ready.connect(self._handle_ready)
        worker.signals.error.connect(self._handle_error)
        worker.signals.finished.connect(self._handle_finished)
        self._active_worker = worker
        QThreadPool.globalInstance().start(worker, -1)

    def _handle_progress(self, current, total, message):
        if self._progress_dialog is not None:
            self._progress_dialog.update_progress(current, total, self._tr(message))

    def _handle_ready(self, result):
        self._latest_result = result

    def _handle_error(self, failure):
        self._latest_error = failure

    def _handle_finished(self):
        # Release the old worker before a dialog can start a replacement job.
        self._download_inflight = False
        self._active_worker = None
        payload = self._last_request
        if self._progress_dialog is not None:
            self._progress_dialog.allow_close()
            self._progress_dialog.close()
            self._progress_dialog.deleteLater()
            self._progress_dialog = None
        self._restore_temporarily_hidden_windows()
        if payload is None:
            return
        preparing = payload.operation == "prepare"
        failure = self._latest_error
        result = self._latest_result
        if preparing:
            self._runtime_prepared = True
            callbacks, self._prepare_callbacks = self._prepare_callbacks, []
            for callback in callbacks:
                callback()
        if failure is not None:
            self._show_failure(failure)
        elif result is not None and (not preparing or result.status == "pending_restart"):
            message = self._tr("Map extension is ready. Restart now to activate it?")
            if result.status == "pending_restart":
                message = self._tr("Map extension is staged and waiting for restart. Restart now?")
            answer = QMessageBox.question(
                self._parent,
                self._tr("Restart Required"),
                message,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                self._restart_application()
        # Map use may have been requested while installation was already busy.
        if not preparing and self._prepare_callbacks and not self._download_inflight:
            self._start(
                MapExtensionDownloadRequest(self._package_root, sys.platform, operation="prepare")
            )

    def _show_failure(self, failure):
        messages = {
            "refused": self._tr(
                "The connection was refused. Check your system proxy or try another download method."
            ),
            "timeout": self._tr("The map extension download timed out. Please try again."),
            "dns": self._tr("The download server name could not be resolved."),
            "tls": self._tr("The download server certificate could not be verified."),
            "http": self._tr("The download server returned an error."),
            "permission": self._tr(
                "The map extension folder is not writable or its files are in use. Check the folder shown in Details."
            ),
            "disk": self._tr("There is not enough disk space to install the map extension."),
            "busy": self._tr(
                "Another map extension installation is running. Try again when it finishes."
            ),
            "unknown_package": self._tr(
                "This is not a supported map extension package. Download the official package for this app version."
            ),
            "unsupported": self._tr(
                "This map extension package is incompatible with this app or platform."
            ),
            "integrity": self._tr(
                "The map extension package is damaged or incomplete. Download it again."
            ),
            "incomplete": self._tr(
                "The map extension is missing required map, search or runtime files."
            ),
            "unsafe_archive": self._tr("The map extension archive contains an unsafe path."),
            "missing_file": self._tr("The selected map extension archive could not be found."),
        }
        box = QMessageBox(self._parent)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(self._tr("Map Extension"))
        box.setText(
            messages.get(
                getattr(failure, "category", ""),
                self._tr(
                    "Map extension installation failed. Try again or install a downloaded archive."
                ),
            )
        )
        if isinstance(failure, MapExtensionError):
            box.setDetailedText(str(failure))
        retry = box.addButton(self._tr("Retry"), QMessageBox.ButtonRole.AcceptRole)
        direct = box.addButton(self._tr("Try Direct Connection"), QMessageBox.ButtonRole.ActionRole)
        browser = box.addButton(self._tr("Download in Browser"), QMessageBox.ButtonRole.ActionRole)
        local = box.addButton(self._tr("Install from File..."), QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        online = bool(self._context.map_extensions.download_url(sys.platform))
        direct.setEnabled(online and getattr(failure, "stage", "") == "download")
        browser.setEnabled(online)
        payload = self._last_request
        box.exec()
        if box.clickedButton() is retry and payload is not None:
            from dataclasses import replace

            self._start(replace(payload, defer_activation=self._runtime_prepared))
        elif box.clickedButton() is direct:
            self.start_download(source="recovery", network_mode="direct")
        elif box.clickedButton() is browser:
            self.open_download_page()
        elif box.clickedButton() is local:
            self.install_from_file()

    def _restart_application(self) -> None:
        app = QCoreApplication.instance()
        if app is None:
            self._restore_temporarily_hidden_windows()
            return

        program, arguments = self._restart_command(app)
        if not QProcess.startDetached(program, arguments):
            self._restore_temporarily_hidden_windows()
            QMessageBox.critical(
                self._parent,
                self._tr("Restart Failed"),
                self._tr(
                    "Failed to relaunch the application automatically. Please restart it manually."
                ),
            )
            return

        window = self._parent.window()
        if window is not None:
            window.close()
        else:
            app.quit()

    def _restart_command(self, app: QCoreApplication) -> tuple[str, list[str]]:
        if getattr(sys, "frozen", False) or "__compiled__" in globals():
            program = app.applicationFilePath()
            arguments = list(sys.argv[1:])
            return program, arguments
        return sys.executable, list(sys.argv)

    def _hide_blocking_top_level_windows(self) -> None:
        self._temporarily_hidden_windows.clear()
        owner = self._parent.window()
        if owner is None:
            return

        for widget in QApplication.topLevelWidgets():
            if widget is owner or widget is self._progress_dialog:
                continue
            if widget.parentWidget() is not owner:
                continue
            if not widget.isVisible():
                continue
            if not bool(widget.windowFlags() & Qt.WindowType.WindowStaysOnTopHint):
                continue
            self._temporarily_hidden_windows.append(widget)
            widget.hide()

    def _restore_temporarily_hidden_windows(self) -> None:
        while self._temporarily_hidden_windows:
            widget = self._temporarily_hidden_windows.pop()
            try:
                widget.show()
                widget.raise_()
            except RuntimeError:
                continue


__all__ = ["MapExtensionDownloadController"]

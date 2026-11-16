"""Shared UI controller for downloading and activating the map extension."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QProcess, Qt, QThreadPool, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QCheckBox, QFileDialog

from iPhoto.application.contracts.runtime_entry_contract import RuntimeEntryContract
from iPhoto.application.ports.map_extension import MapExtensionError
from iPhoto.gui.ui.tasks.map_extension_download_worker import (
    MapExtensionDownloadRequest,
    MapExtensionDownloadWorker,
)
from iPhoto.gui.ui.widgets import dialogs

_SHOW_STARTUP_PROMPT_KEY = "ui.show_map_extension_startup_prompt"


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
        self._package_generation = 0
        self._active_generation = 0
        self._prepare_callbacks: list[Callable[[], None]] = []

    def _tr(self, text):
        return QCoreApplication.translate("MapExtension", text, None)

    def set_package_root(self, package_root):
        if package_root is None:
            return
        root = Path(package_root).resolve()
        if root == self._package_root:
            return
        self._package_root = root
        self._package_generation += 1
        self._runtime_prepared = False
        self._latest_result = None
        self._latest_error = None
        self._prepare_callbacks.clear()

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
        generation = self._package_generation
        checkbox = QCheckBox(self._tr("Do not show again"))
        local = self._tr("Install from File...")
        local_available = self._context.map_extensions.supports_local_install(sys.platform)

        def current(action):
            if generation == self._package_generation:
                action()

        popup = dialogs.open_action_popup(
            self._parent,
            self._tr("Download the offline map extension now?"),
            title=self._tr("Map Extension"),
            actions=(
                (self._tr("Download"), lambda: current(self.start_download)),
                (local, lambda: current(self.install_from_file)),
                (self._tr("Not Now"), None),
            ),
            disabled=() if local_available else (local,),
            configure=lambda popup: popup.add_control(checkbox),
            modal=True,
        )
        self._startup_prompt = popup

        def finished():
            self._startup_prompt = None
            if generation == self._package_generation and checkbox.isChecked():
                self._context.settings.set(_SHOW_STARTUP_PROMPT_KEY, False)

        popup.closed.connect(finished)
        return False

    def show_options(self) -> None:
        if self._download_inflight:
            if self._progress_dialog is not None:
                self._progress_dialog.raise_()
            return
        available = bool(self._context.map_extensions.download_url(sys.platform))
        local_available = self._context.map_extensions.supports_local_install(sys.platform)
        if not available and not local_available:
            self._show_manual_install_unavailable()
            return
        online = self._tr("Download")
        browser = self._tr("Download in Browser")
        local = self._tr("Install from File...")
        disabled = (() if available else (online, browser)) + (() if local_available else (local,))
        dialogs.run_action_popup(
            self._parent,
            self._tr("Choose how to install the map extension."),
            title=self._tr("Map Extension"),
            actions=(
                (online, self.start_download),
                (browser, self.open_download_page),
                (local, self.install_from_file),
                (self._tr("Cancel"), None),
            ),
            disabled=disabled,
        )

    def open_download_page(self):
        url = self._context.map_extensions.download_url(sys.platform)
        if not url:
            return

        if QDesktopServices.openUrl(QUrl(url)):
            message = self._tr("Select the archive after the download finishes.")
        else:
            message = self._tr("Could not open the browser. Copy this link to download the archive.")
            message = f"{message}\n\n{url}"
        local = self._tr("Install from File...")
        local_available = self._context.map_extensions.supports_local_install(sys.platform)
        dialogs.run_action_popup(
            self._parent,
            message,
            title=self._tr("Map Extension"),
            actions=((local, self.install_from_file), (self._tr("Close"), None)),
            disabled=() if local_available else (local,),
        )

    def install_from_file(self):
        if not self._context.map_extensions.supports_local_install(sys.platform):
            self._show_manual_install_unavailable()
            return
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

    def _show_manual_install_unavailable(self):
        if sys.platform == "darwin":
            message = self._tr(
                "On macOS, the map extension is included with the app and prepared "
                "automatically the first time you use maps."
            )
        else:
            message = self._tr(
                "Manual map extension installation is not available on this platform."
            )
        dialogs.run_action_popup(
            self._parent, message, title=self._tr("Map Extension"),
            actions=((self._tr("Close"), None),),
        )

    def start_download(self) -> None:
        self._start(
            MapExtensionDownloadRequest(
                self._package_root,
                sys.platform,
                defer_activation=self._runtime_prepared,
            )
        )

    def _start(self, payload):
        if self._download_inflight:
            return
        self._download_inflight = True
        self._last_request = payload
        self._active_generation = self._package_generation
        self._latest_result = None
        self._latest_error = None
        if payload.operation == "install":
            self._show_progress_dialog()
        worker = MapExtensionDownloadWorker(payload, self._context.map_extensions)
        worker.signals.progress.connect(self._handle_progress)
        worker.signals.ready.connect(self._handle_ready)
        worker.signals.error.connect(self._handle_error)
        worker.signals.finished.connect(self._handle_finished)
        self._active_worker = worker
        QThreadPool.globalInstance().start(worker, -1)

    def _show_progress_dialog(self) -> None:
        self._hide_blocking_top_level_windows()
        self._progress_dialog = dialogs.open_progress_popup(
            self._parent,
            title=self._tr("Map Extension"),
            message=self._tr("Preparing map extension download..."),
            modal=True,
            dismissible=False,
        )

    def _handle_progress(self, current, total, message):
        if not self._download_inflight or self._active_generation != self._package_generation:
            return
        # Ordinary preparation emits no progress. Bundled installs and pending
        # activation request a dialog only when the installer starts work.
        if self._progress_dialog is None:
            self._show_progress_dialog()
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
        generation = self._active_generation
        if self._progress_dialog is not None:
            self._progress_dialog.allow_close()
            self._progress_dialog.close()
            self._progress_dialog.deleteLater()
            self._progress_dialog = None
        self._restore_temporarily_hidden_windows()
        if payload is None:
            return
        if generation != self._package_generation:
            # The old worker may finish after a library/runtime rebind. Its
            # result cannot release callbacks waiting for the new root.
            self._latest_result = None
            self._latest_error = None
            self._start_waiting_preparation()
            return
        preparing = payload.operation == "prepare"
        failure = self._latest_error
        result = self._latest_result
        if failure is not None:
            self._show_failure(failure)
            if self._download_inflight:
                return
        if preparing and generation == self._package_generation:
            # Keep capability probes queued until preparation succeeds or the
            # user dismisses recovery and proceeds with the available runtime.
            self._runtime_prepared = True
            callbacks, self._prepare_callbacks = self._prepare_callbacks, []
            for callback in callbacks:
                if generation != self._package_generation:
                    break
                callback()
        if generation != self._package_generation:
            self._start_waiting_preparation()
            return
        if failure is None and result is not None and result.status == "pending_restart":
            message = self._tr("Map extension is staged and waiting for restart. Restart now?")
            dialogs.run_action_popup(
                self._parent,
                message,
                title=self._tr("Restart Required"),
                actions=(
                    (self._tr("Restart"), self._restart_application),
                    (self._tr("Not Now"), None),
                ),
            )
        # Map use may have been requested while installation was already busy.
        self._start_waiting_preparation()

    def _start_waiting_preparation(self):
        if self._prepare_callbacks and not self._download_inflight:
            self._start(
                MapExtensionDownloadRequest(self._package_root, sys.platform, operation="prepare")
            )

    def _show_failure(self, failure):
        category = getattr(failure, "category", "")
        download_failed = getattr(failure, "stage", "") == "download"
        messages = {
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
        if download_failed:
            heading = self._tr("Could not download the map extension from GitHub.")
            guidance = self._tr(
                "Check your VPN or network proxy settings, or download the archive in your "
                "browser and install it from a file."
            )
            message = f"{heading}\n\n{guidance}"
        else:
            message = messages.get(
                category,
                self._tr(
                    "Map extension installation failed. Try again or install a downloaded archive."
                ),
            )
        payload = self._last_request
        generation = self._package_generation

        def current(action):
            if generation == self._package_generation:
                action()

        actions = []
        if not download_failed and payload is not None:
            actions.append((self._tr("Retry"), lambda: current(lambda: self._start(payload))))
        browser = self._tr("Download in Browser")
        local = self._tr("Install from File...")
        actions.extend((
            (browser, lambda: current(self.open_download_page)),
            (local, lambda: current(self.install_from_file)),
            (self._tr("Cancel"), None),
        ))
        disabled = []
        if not self._context.map_extensions.download_url(sys.platform):
            disabled.append(browser)
        if not self._context.map_extensions.supports_local_install(sys.platform):
            disabled.append(local)

        def configure(popup):
            if isinstance(failure, MapExtensionError):
                popup.add_details(self._tr("Details"), str(failure))

        dialogs.run_action_popup(
            self._parent,
            message,
            title=self._tr("Map Extension"),
            actions=tuple(actions),
            disabled=tuple(disabled),
            configure=configure,
        )

    def _restart_application(self) -> None:
        app = QCoreApplication.instance()
        if app is None:
            self._restore_temporarily_hidden_windows()
            return

        program, arguments = self._restart_command(app)
        started, _pid = QProcess.startDetached(program, arguments)
        if not started:
            self._restore_temporarily_hidden_windows()
            dialogs.run_action_popup(
                self._parent,
                self._tr(
                    "Failed to relaunch the application automatically. Please restart it manually."
                ),
                title=self._tr("Restart Failed"),
                actions=((self._tr("Close"), None),),
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

"""Shared UI controller for downloading and activating the map extension."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QProcess, QThreadPool, Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from iPhoto.application.contracts.runtime_entry_contract import RuntimeEntryContract
from iPhoto.gui.ui.tasks.map_extension_download_worker import (
    MapExtensionDownloadRequest,
    MapExtensionDownloadResult,
    MapExtensionDownloadWorker,
)
from maps.map_sources import (
    has_installed_osmand_extension,
    has_pending_osmand_extension_install,
    supports_map_extension_download,
)

LOGGER = logging.getLogger(__name__)
_SHOW_STARTUP_PROMPT_KEY = "ui.show_map_extension_startup_prompt"


class _MapExtensionProgressDialog(QDialog):
    """Modal progress dialog used by every map-extension entry point."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Map Extension")
        self.setModal(True)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setMinimumWidth(420)
        self._allow_close = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        self._message_label = QLabel("Preparing map extension download...", self)
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

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if not self._allow_close:
            event.ignore()
            return
        super().closeEvent(event)


class MapExtensionDownloadController:
    """Coordinate startup prompts, download progress, and restart requests."""

    def __init__(
        self,
        parent: QWidget,
        context: RuntimeEntryContract,
        *,
        package_root: Path,
    ) -> None:
        self._parent = parent
        self._context = context
        self._package_root = Path(package_root).resolve()
        self._progress_dialog: _MapExtensionProgressDialog | None = None
        self._download_inflight = False
        self._latest_result: MapExtensionDownloadResult | None = None
        self._temporarily_hidden_windows: list[QWidget] = []

    def maybe_prompt_on_startup(self) -> None:
        if not supports_map_extension_download():
            return
        if has_installed_osmand_extension(self._package_root):
            return
        if has_pending_osmand_extension_install(self._package_root):
            return
        if not bool(self._context.settings.get(_SHOW_STARTUP_PROMPT_KEY, True)):
            return

        message_box = QMessageBox(self._parent)
        message_box.setIcon(QMessageBox.Icon.Question)
        message_box.setWindowTitle("Map Extension")
        message_box.setText("Download the offline map extension now?")
        message_box.setInformativeText(
            "The map extension enables the bundled offline OsmAnd map runtime."
        )
        download_button = message_box.addButton("Download", QMessageBox.ButtonRole.AcceptRole)
        message_box.addButton("Not Now", QMessageBox.ButtonRole.RejectRole)
        do_not_show_checkbox = QCheckBox("Do not show again", message_box)
        message_box.setCheckBox(do_not_show_checkbox)
        message_box.exec()

        if message_box.clickedButton() is download_button:
            self.start_download(source="startup")
            return

        if do_not_show_checkbox.isChecked():
            self._context.settings.set(_SHOW_STARTUP_PROMPT_KEY, False)

    def start_download(self, *, source: str) -> None:
        del source
        if self._download_inflight:
            if self._progress_dialog is not None:
                self._progress_dialog.raise_()
                self._progress_dialog.activateWindow()
            return

        if not supports_map_extension_download():
            QMessageBox.warning(
                self._parent,
                "Map Extension",
                "Map extension downloads are unavailable on this platform.",
            )
            return

        self._download_inflight = True
        self._latest_result = None
        self._hide_blocking_top_level_windows()
        self._progress_dialog = _MapExtensionProgressDialog(self._parent)
        self._progress_dialog.show()
        self._progress_dialog.raise_()
        self._progress_dialog.activateWindow()

        worker = MapExtensionDownloadWorker(
            MapExtensionDownloadRequest(
                package_root=self._package_root,
                platform=sys.platform,
            )
        )
        worker.signals.progress.connect(self._handle_progress)
        worker.signals.ready.connect(self._handle_ready)
        worker.signals.error.connect(self._handle_error)
        worker.signals.finished.connect(self._handle_finished)
        QThreadPool.globalInstance().start(worker, -1)

    def _handle_progress(self, current: int, total: int, message: str) -> None:
        if self._progress_dialog is not None:
            self._progress_dialog.update_progress(int(current), int(total), str(message))

    def _handle_ready(self, result: object) -> None:
        if isinstance(result, MapExtensionDownloadResult):
            self._latest_result = result
        if self._progress_dialog is not None:
            self._progress_dialog.update_progress(100, 100, "Map extension is ready. Restart required.")
            self._progress_dialog.allow_close()
            self._progress_dialog.close()
            self._progress_dialog.deleteLater()
            self._progress_dialog = None

        restart_now = QMessageBox.question(
            self._parent,
            "Restart Required",
            "Map extension download finished. Restart now to activate it?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if restart_now != QMessageBox.StandardButton.Yes:
            self._restore_temporarily_hidden_windows()
        if restart_now == QMessageBox.StandardButton.Yes:
            self._restart_application()

    def _handle_error(self, message: str) -> None:
        if self._progress_dialog is not None:
            self._progress_dialog.allow_close()
            self._progress_dialog.close()
            self._progress_dialog.deleteLater()
            self._progress_dialog = None
        self._restore_temporarily_hidden_windows()
        QMessageBox.critical(
            self._parent,
            "Map Extension",
            str(message) or "Failed to download the map extension.",
        )

    def _handle_finished(self) -> None:
        self._download_inflight = False

    def _restart_application(self) -> None:
        app = QCoreApplication.instance()
        if app is None:
            return

        program, arguments = self._restart_command(app)
        if not QProcess.startDetached(program, arguments):
            QMessageBox.critical(
                self._parent,
                "Restart Failed",
                "Failed to relaunch the application automatically. Please restart it manually.",
            )
            return

        window = self._parent.window()
        if window is not None:
            window.close()
        else:
            app.quit()

    def _restart_command(self, app: QCoreApplication) -> tuple[str, list[str]]:
        if getattr(sys, "frozen", False):
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

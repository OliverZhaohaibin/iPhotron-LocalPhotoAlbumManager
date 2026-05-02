"""Controller dedicated to share-related toolbar interactions."""

from __future__ import annotations

import hashlib
import logging
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import (
    QMimeData,
    QObject,
    QRunnable,
    QThreadPool,
    QUrl,
    Signal,
)
from PySide6.QtGui import QAction, QActionGroup, QGuiApplication, QImage
from PySide6.QtWidgets import QPushButton

from ....application.ports import EditServicePort
from ....core.export import probe_duration_seconds, render_image, render_video
from ....errors import ExternalToolError
from ....media_classifier import VIDEO_EXTENSIONS
from ....utils.ffmpeg import probe_media
from ..widgets.notification_toast import NotificationToast
from .status_bar_controller import StatusBarController

_LOGGER = logging.getLogger(__name__)


class RenderClipboardSignals(QObject):
    """Signals emitted by :class:`RenderClipboardWorker`."""

    success = Signal(QImage)
    """Emitted with the fully rendered image."""

    failed = Signal(str)
    """Emitted when rendering fails."""


class RenderClipboardWorker(QRunnable):
    """Render the current asset with adjustments for clipboard copy."""

    def __init__(self, path: Path, *, edit_service: EditServicePort | None = None) -> None:
        super().__init__()
        self._path = path
        self._edit_service = edit_service
        self.signals = RenderClipboardSignals()

    def run(self) -> None:
        try:
            self._do_work()
        except Exception as exc:  # noqa: BLE001 - keep worker failures from escaping QRunnable.run()
            _LOGGER.exception("Failed to render image for clipboard")
            self.signals.failed.emit(str(exc))

    def _do_work(self) -> None:
        image = render_image(self._path, edit_service=self._edit_service)
        if image is None or image.isNull():
            self.signals.failed.emit("No adjustments found")
            return
        self.signals.success.emit(image)


class RenderVideoClipboardSignals(QObject):
    """Signals emitted by :class:`RenderVideoClipboardWorker`."""

    success = Signal(str)
    failed = Signal(str)


_SHARE_DIR_MAX_AGE_SEC = 24 * 3600  # prune temp video files older than 24 hours


def _prune_share_dir(directory: Path) -> None:
    """Remove MP4 files in *directory* that are older than ``_SHARE_DIR_MAX_AGE_SEC``."""
    cutoff = time.time() - _SHARE_DIR_MAX_AGE_SEC
    for item in directory.glob("*.mp4"):
        try:
            if item.stat().st_mtime < cutoff:
                item.unlink(missing_ok=True)
        except OSError:
            pass


class RenderVideoClipboardWorker(QRunnable):
    """Render the current video with sidecar edits and expose the exported file."""

    def __init__(self, path: Path, *, edit_service: EditServicePort | None = None) -> None:
        super().__init__()
        self._path = path
        self._edit_service = edit_service
        self.signals = RenderVideoClipboardSignals()

    def run(self) -> None:
        try:
            output_dir = Path(tempfile.gettempdir()) / "iPhoto-share"
            output_dir.mkdir(parents=True, exist_ok=True)
            _prune_share_dir(output_dir)
            path_hash = hashlib.sha256(str(self._path.resolve()).encode()).hexdigest()[:12]
            destination = output_dir / f"{self._path.stem}_{path_hash}.mp4"
            if render_video(self._path, destination, edit_service=self._edit_service):
                self.signals.success.emit(str(destination))
            else:
                self.signals.failed.emit("Failed to render edited video")
        except Exception as exc:  # noqa: BLE001 - keep worker failures from escaping QRunnable.run()
            _LOGGER.exception("Failed to render edited video for sharing")
            self.signals.failed.emit(str(exc))


class ShareController(QObject):
    """Encapsulate the share button workflow used by the main window."""

    def __init__(
        self,
        *,
        settings,
        current_path_provider: Callable[[], Optional[Path]],
        status_bar: StatusBarController,
        notification_toast: NotificationToast,
        share_button: QPushButton,
        share_action_group: QActionGroup,
        copy_file_action: QAction,
        copy_path_action: QAction,
        reveal_action: QAction,
        edit_service_getter: Callable[[], EditServicePort | None] | None = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._current_path_provider = current_path_provider
        self._status_bar = status_bar
        self._toast = notification_toast
        self._share_button = share_button
        self._share_action_group = share_action_group
        self._copy_file_action = copy_file_action
        self._copy_path_action = copy_path_action
        self._reveal_action = reveal_action
        self._edit_service_getter = edit_service_getter

        self._share_action_group.triggered.connect(self._handle_action_changed)
        self._share_button.clicked.connect(self._handle_share_requested)

    # ------------------------------------------------------------------
    # Preference lifecycle
    # ------------------------------------------------------------------
    def restore_preference(self) -> None:
        """Apply the persisted share choice to the action group."""

        share_action = self._settings.get("ui.share_action", "reveal_file")
        mapping = {
            "copy_file": self._copy_file_action,
            "copy_path": self._copy_path_action,
            "reveal_file": self._reveal_action,
        }
        target = mapping.get(share_action, self._reveal_action)
        target.setChecked(True)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------
    def _handle_action_changed(self, action: QAction) -> None:
        if action is self._copy_file_action:
            self._settings.set("ui.share_action", "copy_file")
        elif action is self._copy_path_action:
            self._settings.set("ui.share_action", "copy_path")
        else:
            self._settings.set("ui.share_action", "reveal_file")

    def _handle_share_requested(self) -> None:
        file_path = self._current_path_provider()
        if file_path is None:
            self._status_bar.show_message("No item selected to share.", 3000)
            return

        share_action = self._settings.get("ui.share_action", "reveal_file")

        if share_action == "copy_file":
            self._copy_file_to_clipboard(file_path)
        elif share_action == "copy_path":
            self._copy_path_to_clipboard(file_path)
        else:
            self._reveal_in_file_manager(file_path)

    # ------------------------------------------------------------------
    # Clipboard helpers
    # ------------------------------------------------------------------
    def _copy_file_to_clipboard(self, path: Path) -> None:
        if not path.exists():
            self._status_bar.show_message(f"File not found: {path.name}", 3000)
            return

        edit_service = self._edit_service_getter() if self._edit_service_getter else None
        if edit_service is not None and edit_service.sidecar_exists(path):
            if path.suffix.lower() in VIDEO_EXTENSIONS:
                # Probe duration so trim_is_non_default can compare against the
                # full clip length; without it, any stored trimOutSec would be
                # treated as an edit even when it equals the clip duration.
                video_duration: float | None = None
                try:
                    video_duration = probe_duration_seconds(probe_media(path))
                except ExternalToolError:
                    pass
                if edit_service.describe_adjustments(
                    path,
                    duration_hint=video_duration,
                ).has_visible_edits:
                    self._copy_rendered_video_to_clipboard(path)
                else:
                    mime_data = self._build_file_mime_data(path)
                    QGuiApplication.clipboard().setMimeData(mime_data)
                    self._toast.show_toast("Copied to Clipboard")
                return
            self._copy_rendered_image_to_clipboard(path)
            return

        mime_data = self._build_file_mime_data(path)
        QGuiApplication.clipboard().setMimeData(mime_data)
        self._toast.show_toast("Copied to Clipboard")

    def _copy_rendered_image_to_clipboard(self, path: Path) -> None:
        self._toast.show_toast("Preparing image...")
        edit_service = self._edit_service_getter() if self._edit_service_getter else None
        worker = RenderClipboardWorker(path, edit_service=edit_service)

        def _on_success(image: QImage):
            QGuiApplication.clipboard().setImage(image)
            self._toast.show_toast("Copied to Clipboard")

        def _on_failure(message: str):
            # Fallback to file copy if rendering fails
            mime_data = self._build_file_mime_data(path)
            QGuiApplication.clipboard().setMimeData(mime_data)
            self._toast.show_toast("Copied Original File")

        worker.signals.success.connect(_on_success)
        worker.signals.failed.connect(_on_failure)
        QThreadPool.globalInstance().start(worker)

    def _copy_rendered_video_to_clipboard(self, path: Path) -> None:
        self._toast.show_toast("Preparing video...")
        edit_service = self._edit_service_getter() if self._edit_service_getter else None
        worker = RenderVideoClipboardWorker(path, edit_service=edit_service)

        def _on_success(rendered_path: str):
            mime_data = self._build_file_mime_data(Path(rendered_path))
            QGuiApplication.clipboard().setMimeData(mime_data)
            self._toast.show_toast("Copied to Clipboard")

        def _on_failure(_message: str):
            mime_data = self._build_file_mime_data(path)
            QGuiApplication.clipboard().setMimeData(mime_data)
            self._toast.show_toast("Copied Original File")

        worker.signals.success.connect(_on_success)
        worker.signals.failed.connect(_on_failure)
        QThreadPool.globalInstance().start(worker)

    def _copy_path_to_clipboard(self, path: Path) -> None:
        QGuiApplication.clipboard().setText(str(path))
        self._toast.show_toast("Copied to Clipboard")

    def _reveal_in_file_manager(self, path: Path) -> None:
        if not path.exists():
            self._status_bar.show_message(f"File not found: {path.name}", 3000)
            return

        if sys.platform == "win32":
            subprocess.run(["explorer", "/select,", str(path)], check=False)
        elif sys.platform == "darwin":
            subprocess.run(["open", "-R", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path.parent)], check=False)
        self._status_bar.show_message(f"Revealed {path.name} in file manager.", 3000)

    def _build_file_mime_data(self, path: Path) -> QMimeData:
        mime_data = QMimeData()
        mime_data.setUrls([QUrl.fromLocalFile(str(path))])
        return mime_data

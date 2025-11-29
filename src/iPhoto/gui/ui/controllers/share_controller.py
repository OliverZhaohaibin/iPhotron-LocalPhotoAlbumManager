"""Controller dedicated to share-related toolbar interactions."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QMimeData, QUrl, QThreadPool, QRunnable, Signal
from PySide6.QtGui import QAction, QGuiApplication, QImage, QTransform
from PySide6.QtWidgets import QPushButton
from PySide6.QtGui import QActionGroup

from ....io import sidecar
from ....core.filters.facade import apply_adjustments
from ....utils import image_loader
from ..media import PlaylistController
from ..models.asset_model import AssetModel, Roles
from ..widgets.notification_toast import NotificationToast

from .status_bar_controller import StatusBarController


class RenderClipboardSignals(QObject):
    """Signals emitted by :class:`RenderClipboardWorker`."""

    success = Signal(QImage)
    """Emitted with the fully rendered image."""

    failed = Signal(str)
    """Emitted when rendering fails."""


class RenderClipboardWorker(QRunnable):
    """Render the current asset with adjustments for clipboard copy."""

    def __init__(self, path: Path) -> None:
        super().__init__()
        self._path = path
        self.signals = RenderClipboardSignals()

    def run(self) -> None:
        try:
            self._do_work()
        except Exception as exc:
            self.signals.failed.emit(str(exc))

    def _do_work(self) -> None:
        # 1. Load adjustments
        raw_adjustments = sidecar.load_adjustments(self._path)
        if not raw_adjustments:
            self.signals.failed.emit("No adjustments found")
            return

        # 2. Load original image
        image = image_loader.load_qimage(self._path)
        if image is None or image.isNull():
            self.signals.failed.emit("Failed to load image")
            return

        # 3. Apply Filters (Tone/Color)
        # We must use resolve_render_adjustments to combine master sliders with
        # individual deltas into the final shader-friendly values expected by the facade.
        resolved_adjustments = sidecar.resolve_render_adjustments(raw_adjustments)

        filtered_image = apply_adjustments(image, resolved_adjustments)

        # 4. Apply Geometry (Crop -> Flip -> Rotate)
        # Crop logic mirrors sidecar._normalised_crop_components
        cx = self._clamp(float(raw_adjustments.get("Crop_CX", 0.5)))
        cy = self._clamp(float(raw_adjustments.get("Crop_CY", 0.5)))
        w = self._clamp(float(raw_adjustments.get("Crop_W", 1.0)))
        h = self._clamp(float(raw_adjustments.get("Crop_H", 1.0)))

        # Constrain crop to image bounds
        half_w = w * 0.5
        half_h = h * 0.5
        cx = max(half_w, min(1.0 - half_w, cx))
        cy = max(half_h, min(1.0 - half_h, cy))

        # Calculate pixel rect
        img_w = filtered_image.width()
        img_h = filtered_image.height()

        rect_w = int(round(w * img_w))
        rect_h = int(round(h * img_h))
        rect_left = int(round((cx - half_w) * img_w))
        rect_top = int(round((cy - half_h) * img_h))

        # Clamp pixels
        rect_left = max(0, rect_left)
        rect_top = max(0, rect_top)
        rect_w = min(rect_w, img_w - rect_left)
        rect_h = min(rect_h, img_h - rect_top)

        if rect_w > 0 and rect_h > 0:
            filtered_image = filtered_image.copy(rect_left, rect_top, rect_w, rect_h)

        # Flip Horizontal
        if bool(raw_adjustments.get("Crop_FlipH", False)):
            filtered_image = filtered_image.mirrored(True, False)

        # Rotate 90
        rotate_steps = int(float(raw_adjustments.get("Crop_Rotate90", 0.0))) % 4
        if rotate_steps > 0:
            transform = QTransform().rotate(rotate_steps * 90)
            filtered_image = filtered_image.transformed(transform)

        self.signals.success.emit(filtered_image)

    def _clamp(self, val: float) -> float:
        return max(0.0, min(1.0, val))


class ShareController(QObject):
    """Encapsulate the share button workflow used by the main window."""

    def __init__(
        self,
        *,
        settings,
        playlist: PlaylistController,
        asset_model: AssetModel,
        status_bar: StatusBarController,
        notification_toast: NotificationToast,
        share_button: QPushButton,
        share_action_group: QActionGroup,
        copy_file_action: QAction,
        copy_path_action: QAction,
        reveal_action: QAction,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._playlist = playlist
        self._asset_model = asset_model
        self._status_bar = status_bar
        self._toast = notification_toast
        self._share_button = share_button
        self._share_action_group = share_action_group
        self._copy_file_action = copy_file_action
        self._copy_path_action = copy_path_action
        self._reveal_action = reveal_action

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
        current_row = self._playlist.current_row()
        if current_row < 0:
            self._status_bar.show_message("No item selected to share.", 3000)
            return

        index = self._asset_model.index(current_row, 0)
        if not index.isValid():
            return

        file_path_str = index.data(Roles.ABS)
        if not file_path_str:
            return

        file_path = Path(file_path_str)
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

        # Check for sidecar adjustments
        sidecar_path = sidecar.sidecar_path_for_asset(path)
        if sidecar_path.exists():
            self._copy_rendered_image_to_clipboard(path)
            return

        mime_data = self._build_file_mime_data(path)
        QGuiApplication.clipboard().setMimeData(mime_data)
        self._toast.show_toast("Copied to Clipboard")

    def _copy_rendered_image_to_clipboard(self, path: Path) -> None:
        self._toast.show_toast("Preparing image...")
        worker = RenderClipboardWorker(path)

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

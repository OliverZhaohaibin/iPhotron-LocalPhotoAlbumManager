"""Export engine for rendering and saving assets."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from PySide6.QtGui import QImage, QTransform

from ..io import sidecar
from .filters.facade import apply_adjustments
from ..utils import image_loader
from ..media_classifier import VIDEO_EXTENSIONS

_LOGGER = logging.getLogger(__name__)


def render_image(path: Path) -> QImage | None:
    """Render the asset at *path* with adjustments applied."""

    # 1. Load adjustments
    raw_adjustments = sidecar.load_adjustments(path)
    if not raw_adjustments:
        # Prompt implies we only render if adjustments exist (Case A).
        # If this function is called, caller expects rendering.
        return None

    # 2. Load original image
    image = image_loader.load_qimage(path)
    if image is None or image.isNull():
        return None

    # 3. Apply Filters
    resolved_adjustments = sidecar.resolve_render_adjustments(raw_adjustments)
    filtered_image = apply_adjustments(image, resolved_adjustments)

    # 4. Apply Geometry
    cx = _clamp(float(raw_adjustments.get("Crop_CX", 0.5)))
    cy = _clamp(float(raw_adjustments.get("Crop_CY", 0.5)))
    w = _clamp(float(raw_adjustments.get("Crop_W", 1.0)))
    h = _clamp(float(raw_adjustments.get("Crop_H", 1.0)))

    # Constrain crop to image bounds
    half_w = w * 0.5
    half_h = h * 0.5
    cx = max(half_w, min(1.0 - half_w, cx))
    cy = max(half_h, min(1.0 - half_h, cy))

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

    return filtered_image


def _clamp(val: float) -> float:
    return max(0.0, min(1.0, val))


def get_unique_destination(destination: Path) -> Path:
    """Return *destination* or a variant with a counter if it exists."""
    if not destination.exists():
        return destination

    parent = destination.parent
    stem = destination.stem
    suffix = destination.suffix
    counter = 1
    while True:
        candidate = parent / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def resolve_export_path(source_path: Path, export_root: Path, library_root: Path) -> Path:
    """Return the destination path mirroring the library structure."""
    try:
        relative = source_path.parent.relative_to(library_root)
    except ValueError:
        # Fallback if source is not under library root
        relative = Path(source_path.parent.name)

    return export_root / relative / source_path.name


def export_asset(source_path: Path, export_root: Path, library_root: Path) -> bool:
    """Export the asset at *source_path* to *export_root* mirroring directory structure.

    Returns True if successful.
    """
    try:
        destination_path = resolve_export_path(source_path, export_root, library_root)
        destination_dir = destination_path.parent
        destination_dir.mkdir(parents=True, exist_ok=True)

        is_video = source_path.suffix.lower() in VIDEO_EXTENSIONS
        has_sidecar = sidecar.sidecar_path_for_asset(source_path).exists()

        # Determine handling strategy
        should_render = not is_video and has_sidecar

        if should_render:
            image = render_image(source_path)
            if image is not None:
                final_dest = destination_path.with_suffix(".jpg")
                final_dest = get_unique_destination(final_dest)
                image.save(str(final_dest), "JPG", 100)
                return True
            else:
                _LOGGER.error("Failed to render image with adjustments for %s; skipping export", source_path)
                return False

        # Case B: Unedited or Video -> Copy
        final_dest = get_unique_destination(destination_path)
        shutil.copy2(source_path, final_dest)
        return True

    except Exception:
        _LOGGER.exception("Export failed for %s", source_path)
        return False

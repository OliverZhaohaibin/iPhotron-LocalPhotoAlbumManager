"""Thumbnail image rendering pipeline (scaling, EXIF rotation, adjustment application, video frame extraction)."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage, QPainter, QTransform

import numpy as np

from ....config import THUMBNAIL_SEEK_GUARD_SEC
from ....utils import image_loader
from ....core.image_filters import apply_adjustments
from ....core.color_resolver import compute_color_statistics
from ....io import sidecar
from .video_frame_grabber import grab_video_frame
from . import geo_utils
from .thumbnail_compositor import composite_canvas


def render_image(abs_path: Path, size: QSize) -> Optional[QImage]:  # pragma: no cover - worker helper
    """Load an image, apply sidecar adjustments and return a composited thumbnail."""
    image = image_loader.load_qimage(abs_path, size)
    if image is None:
        return None
    raw_adjustments = sidecar.load_adjustments(abs_path)
    stats = compute_color_statistics(image) if raw_adjustments else None
    adjustments = sidecar.resolve_render_adjustments(
        raw_adjustments,
        color_stats=stats,
    )

    if adjustments:
        image = apply_geometry_and_crop(image, adjustments)
        if image is None:
            return None
        image = apply_adjustments(image, adjustments, color_stats=stats)
    return composite_canvas(image, size)


def render_video(
    abs_path: Path,
    size: QSize,
    *,
    still_image_time: Optional[float],
    duration: Optional[float],
) -> Optional[QImage]:  # pragma: no cover - worker helper
    """Grab a video frame, apply sidecar adjustments and return a composited thumbnail."""
    image = grab_video_frame(
        abs_path,
        size,
        still_image_time=still_image_time,
        duration=duration,
    )
    if image is None:
        return None

    raw_adjustments = sidecar.load_adjustments(abs_path)
    stats = compute_color_statistics(image) if raw_adjustments else None
    adjustments = sidecar.resolve_render_adjustments(
        raw_adjustments,
        color_stats=stats,
    )

    if adjustments:
        image = apply_geometry_and_crop(image, adjustments)
        if image is None:
            return None
        image = apply_adjustments(image, adjustments, color_stats=stats)

    return composite_canvas(image, size)


def apply_geometry_and_crop(image: QImage, adjustments: Dict[str, float]) -> Optional[QImage]:
    """
    Apply geometric transformations (rotation, perspective, straighten) and crop to the image
    to replicate the OpenGL viewer's visual result on the CPU.

    Args:
        image (QImage): The input image to transform.
        adjustments (Dict[str, float]): Dictionary of geometric adjustment parameters.

    Returns:
        Optional[QImage]: The transformed and cropped image, or None if the operation fails.
    """
    rotate_steps = int(adjustments.get("Crop_Rotate90", 0))
    flip_h = bool(adjustments.get("Crop_FlipH", False))
    straighten = float(adjustments.get("Crop_Straighten", 0.0))
    p_vert = float(adjustments.get("Perspective_Vertical", 0.0))
    p_horz = float(adjustments.get("Perspective_Horizontal", 0.0))

    tex_crop = (
        float(adjustments.get("Crop_CX", 0.5)),
        float(adjustments.get("Crop_CY", 0.5)),
        float(adjustments.get("Crop_W", 1.0)),
        float(adjustments.get("Crop_H", 1.0))
    )

    log_cx, log_cy, log_w, log_h = geo_utils.texture_crop_to_logical(tex_crop, rotate_steps)

    w, h = image.width(), image.height()

    if (rotate_steps == 0 and not flip_h and abs(straighten) < 1e-5 and
        abs(p_vert) < 1e-5 and abs(p_horz) < 1e-5 and
        log_w >= 0.999 and log_h >= 0.999):
        return image

    if rotate_steps % 2 == 1:
        logical_aspect = float(h) / float(w) if w > 0 else 1.0
    else:
        logical_aspect = float(w) / float(h) if h > 0 else 1.0

    matrix_inv = geo_utils.build_perspective_matrix(
        vertical=p_vert,
        horizontal=p_horz,
        image_aspect_ratio=logical_aspect,
        straighten_degrees=straighten,
        rotate_steps=0,
        flip_horizontal=flip_h
    )

    try:
        matrix = np.linalg.inv(matrix_inv)
    except np.linalg.LinAlgError:
        matrix = np.identity(3)

    qt_perspective = QTransform(
        matrix[0, 0], matrix[1, 0], matrix[2, 0],
        matrix[0, 1], matrix[1, 1], matrix[2, 1],
        matrix[0, 2], matrix[1, 2], matrix[2, 2]
    )

    t_to_norm = QTransform().scale(1.0 / w, 1.0 / h)

    t_rot = QTransform()
    t_rot.translate(0.5, 0.5)
    t_rot.rotate(rotate_steps * 90)
    t_rot.translate(-0.5, -0.5)

    t_to_ndc = QTransform().translate(-1.0, -1.0).scale(2.0, 2.0)
    t_from_ndc = QTransform().translate(0.5, 0.5).scale(0.5, 0.5)

    log_w_px = h if rotate_steps % 2 else w
    log_h_px = w if rotate_steps % 2 else h
    t_to_pixels = QTransform().scale(log_w_px, log_h_px)

    transform = t_to_norm * t_rot * t_to_ndc * qt_perspective * t_from_ndc * t_to_pixels

    crop_x_px = log_cx * log_w_px - (log_w * log_w_px * 0.5)
    crop_y_px = log_cy * log_h_px - (log_h * log_h_px * 0.5)
    crop_w_px = log_w * log_w_px
    crop_h_px = log_h * log_h_px

    t_final = transform * QTransform().translate(-crop_x_px, -crop_y_px)

    out_w = max(1, int(round(crop_w_px)))
    out_h = max(1, int(round(crop_h_px)))

    result_img = QImage(out_w, out_h, QImage.Format.Format_ARGB32_Premultiplied)
    result_img.fill(Qt.transparent)

    painter = QPainter(result_img)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)

    painter.setTransform(t_final)
    painter.drawImage(0, 0, image)
    painter.end()

    return result_img


def seek_targets(
    is_video: bool,
    still_image_time: Optional[float],
    duration: Optional[float],
) -> List[Optional[float]]:
    """
    Return a list of seek offsets (in seconds) for video thumbnails, applying guard rails
    to avoid seeking too close to the start or end of the video. For non-video files,
    returns a list containing a single None value.
    """
    if not is_video:
        return [None]

    targets: List[Optional[float]] = []
    seen: set[Optional[float]] = set()

    def add(candidate: Optional[float]) -> None:
        if candidate is None:
            key: Optional[float] = None
            value: Optional[float] = None
        else:
            value = max(candidate, 0.0)
            if duration and duration > 0:
                guard = min(
                    max(THUMBNAIL_SEEK_GUARD_SEC, duration * 0.1),
                    duration / 2.0,
                )
                max_seek = max(duration - guard, 0.0)
                if value > max_seek:
                    value = max_seek
            key = value
        if key in seen:
            return
        seen.add(key)
        targets.append(value)

    if still_image_time is not None:
        add(still_image_time)
    elif duration is not None and duration > 0:
        add(duration / 2.0)
    add(None)
    return targets

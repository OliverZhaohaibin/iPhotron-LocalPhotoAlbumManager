"""CPU-side geometric transformation and crop helper.

This module provides ``apply_geometry_and_crop``, the pure-CPU counterpart of
the OpenGL geometry pipeline.  Keeping it in ``core`` lets both the export
engine and the GUI thumbnail renderer reuse the same implementation without
creating a cross-layer dependency on GUI task code.
"""

from __future__ import annotations

from typing import Dict, Optional

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter, QTransform

from . import geo_utils


def apply_geometry_and_crop(image: QImage, adjustments: Dict[str, float]) -> Optional[QImage]:
    """Apply geometric transformations (rotation, perspective, straighten) and crop.

    Replicates the OpenGL viewer's visual result on the CPU.

    Args:
        image: The input image to transform.
        adjustments: Dictionary of geometric adjustment parameters.

    Returns:
        The transformed and cropped image, or ``None`` if the operation fails.
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
        float(adjustments.get("Crop_H", 1.0)),
    )

    log_cx, log_cy, log_w, log_h = geo_utils.texture_crop_to_logical(tex_crop, rotate_steps)

    w, h = image.width(), image.height()

    if (
        rotate_steps == 0
        and not flip_h
        and abs(straighten) < 1e-5
        and abs(p_vert) < 1e-5
        and abs(p_horz) < 1e-5
        and log_w >= 0.999
        and log_h >= 0.999
    ):
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
        flip_horizontal=flip_h,
    )

    try:
        matrix = np.linalg.inv(matrix_inv)
    except np.linalg.LinAlgError:
        matrix = np.identity(3)

    qt_perspective = QTransform(
        matrix[0, 0], matrix[1, 0], matrix[2, 0],
        matrix[0, 1], matrix[1, 1], matrix[2, 1],
        matrix[0, 2], matrix[1, 2], matrix[2, 2],
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

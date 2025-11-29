"""
Geometry utilities for thumbnail generation.

This module contains pure mathematical functions for coordinate transformations,
isolated from UI widgets to avoid circular imports.
"""

from __future__ import annotations

import math
from typing import Mapping

import numpy as np


# ------------------------------------------------------------------
# From perspective_math.py
# ------------------------------------------------------------------

def build_perspective_matrix(
    vertical: float,
    horizontal: float,
    *,
    image_aspect_ratio: float,
    straighten_degrees: float = 0.0,
    rotate_steps: int = 0,
    flip_horizontal: bool = False,
) -> np.ndarray:
    """Return the 3×3 matrix that maps projected UVs back to texture UVs."""

    clamped_v = max(-1.0, min(1.0, float(vertical)))
    clamped_h = max(-1.0, min(1.0, float(horizontal)))
    has_perspective = abs(clamped_v) > 1e-5 or abs(clamped_h) > 1e-5

    if has_perspective:
        angle_scale = math.radians(20.0)
        angle_x = clamped_v * angle_scale
        angle_y = clamped_h * angle_scale

        cos_x = math.cos(angle_x)
        sin_x = math.sin(angle_x)
        cos_y = math.cos(angle_y)
        sin_y = math.sin(angle_y)

        rx = np.array(
            [
                [1.0, 0.0, 0.0],
                [0.0, cos_x, -sin_x],
                [0.0, sin_x, cos_x],
            ],
            dtype=np.float32,
        )
        ry = np.array(
            [
                [cos_y, 0.0, sin_y],
                [0.0, 1.0, 0.0],
                [-sin_y, 0.0, cos_y],
            ],
            dtype=np.float32,
        )
        matrix = np.matmul(ry, rx)
    else:
        matrix = np.identity(3, dtype=np.float32)

    safe_aspect = float(image_aspect_ratio)
    if not math.isfinite(safe_aspect) or safe_aspect <= 1e-6:
        safe_aspect = 1.0

    total_degrees = float(straighten_degrees) + float(int(rotate_steps)) * -90.0
    if abs(total_degrees) > 1e-5:
        theta = math.radians(total_degrees)
        cos_z = math.cos(theta)
        sin_z = math.sin(theta)
        rz = np.array(
            [
                [cos_z, -sin_z, 0.0],
                [sin_z, cos_z, 0.0],
                [0.0, 0.0, 1.0],
            ],
            dtype=np.float32,
        )
        if abs(safe_aspect - 1.0) > 1e-6:
            stretch = np.array(
                [
                    [safe_aspect, 0.0, 0.0],
                    [0.0, 1.0, 0.0],
                    [0.0, 0.0, 1.0],
                ],
                dtype=np.float32,
            )
            shrink = np.array(
                [
                    [1.0 / safe_aspect, 0.0, 0.0],
                    [0.0, 1.0, 0.0],
                    [0.0, 0.0, 1.0],
                ],
                dtype=np.float32,
            )
            rz = shrink @ rz @ stretch
        matrix = np.matmul(rz, matrix)

    if flip_horizontal:
        flip = np.array(
            [
                [-1.0, 0.0, 0.0],
                [0.0, 1.0, 0.0],
                [0.0, 0.0, 1.0],
            ],
            dtype=np.float32,
        )
        matrix = np.matmul(flip, matrix)

    return matrix.astype(np.float32)


# ------------------------------------------------------------------
# From geometry.py
# ------------------------------------------------------------------

def clamp_unit(value: float) -> float:
    """Clamp *value* into the ``[0, 1]`` interval."""
    return max(0.0, min(1.0, float(value)))


def texture_crop_to_logical(
    crop: tuple[float, float, float, float], rotate_steps: int
) -> tuple[float, float, float, float]:
    """Map texture-space crop values into logical space for UI rendering."""
    tcx, tcy, tw, th = crop
    if rotate_steps == 0:
        return (tcx, tcy, tw, th)
    if rotate_steps == 1:
        # Step 1: 90° CW (270° CCW) - texture TOP becomes visual RIGHT
        # Transformation: (x', y') = (1-y, x)
        return (
            clamp_unit(1.0 - tcy),
            clamp_unit(tcx),
            clamp_unit(th),
            clamp_unit(tw),
        )
    if rotate_steps == 2:
        return (
            clamp_unit(1.0 - tcx),
            clamp_unit(1.0 - tcy),
            clamp_unit(tw),
            clamp_unit(th),
        )
    # Step 3: 90° CCW (270° CW) - texture TOP becomes visual LEFT
    # Transformation: (x', y') = (y, 1-x)
    return (
        clamp_unit(tcy),
        clamp_unit(1.0 - tcx),
        clamp_unit(th),
        clamp_unit(tw),
    )

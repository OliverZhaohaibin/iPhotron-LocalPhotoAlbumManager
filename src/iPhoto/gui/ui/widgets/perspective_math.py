"""Perspective projection helpers shared by the renderer and crop logic."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence

import numpy as np


@dataclass(frozen=True)
class NormalisedRect:
    """Axis-aligned rectangle described in normalised [0, 1] coordinates."""

    left: float
    top: float
    right: float
    bottom: float

    @property
    def width(self) -> float:
        return max(0.0, float(self.right) - float(self.left))

    @property
    def height(self) -> float:
        return max(0.0, float(self.bottom) - float(self.top))

    @property
    def center(self) -> tuple[float, float]:
        return (
            float(self.left + self.right) * 0.5,
            float(self.top + self.bottom) * 0.5,
        )


def build_perspective_matrix(vertical: float, horizontal: float) -> np.ndarray:
    """Return the 3×3 matrix that maps projected UVs back to texture UVs."""

    clamped_v = max(-1.0, min(1.0, float(vertical)))
    clamped_h = max(-1.0, min(1.0, float(horizontal)))
    if abs(clamped_v) <= 1e-5 and abs(clamped_h) <= 1e-5:
        return np.identity(3, dtype=np.float32)

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
    return matrix.astype(np.float32)


def compute_projected_quad(matrix: np.ndarray) -> list[tuple[float, float]]:
    """Return the projected quad for the unit texture using *matrix*."""

    try:
        forward = np.linalg.inv(matrix)
    except np.linalg.LinAlgError:
        forward = np.identity(3, dtype=np.float32)

    def _project_point(uv: tuple[float, float]) -> tuple[float, float]:
        x, y = uv
        centered = np.array([(x * 2.0) - 1.0, (y * 2.0) - 1.0, 1.0], dtype=np.float32)
        warped = forward @ centered
        denom = float(warped[2])
        if abs(denom) < 1e-6:
            denom = 1e-6 if denom >= 0.0 else -1e-6
        nx = float(warped[0]) / denom
        ny = float(warped[1]) / denom
        return ((nx + 1.0) * 0.5, (ny + 1.0) * 0.5)

    corners = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    return [_project_point(corner) for corner in corners]


def quad_centroid(quad: Sequence[tuple[float, float]]) -> tuple[float, float]:
    """Return the arithmetic centroid of *quad* as a convenience."""

    if not quad:
        return (0.5, 0.5)
    sx = sum(pt[0] for pt in quad)
    sy = sum(pt[1] for pt in quad)
    count = max(1, len(quad))
    return (sx / count, sy / count)


def _cross(ax: float, ay: float, bx: float, by: float) -> float:
    return ax * by - ay * bx


def _point_orientation(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
    return _cross(b[0] - a[0], b[1] - a[1], c[0] - a[0], c[1] - a[1])


def point_in_convex_polygon(point: tuple[float, float], polygon: Sequence[tuple[float, float]]) -> bool:
    """Return ``True`` if *point* lies inside the convex *polygon*."""

    if len(polygon) < 3:
        return False
    last_sign = 0.0
    for i in range(len(polygon)):
        a = polygon[i]
        b = polygon[(i + 1) % len(polygon)]
        orient = _point_orientation(a, b, point)
        if abs(orient) <= 1e-6:
            continue
        sign = 1.0 if orient > 0.0 else -1.0
        if last_sign == 0.0:
            last_sign = sign
        elif sign != last_sign:
            return False
    return True


def rect_inside_quad(rect: NormalisedRect, quad: Sequence[tuple[float, float]]) -> bool:
    """Return ``True`` when *rect* is fully contained inside *quad*."""

    corners = [
        (rect.left, rect.top),
        (rect.right, rect.top),
        (rect.right, rect.bottom),
        (rect.left, rect.bottom),
    ]
    return all(point_in_convex_polygon(corner, quad) for corner in corners)


def _ray_segment_intersection(
    origin: tuple[float, float],
    direction: tuple[float, float],
    seg_a: tuple[float, float],
    seg_b: tuple[float, float],
) -> float | None:
    """Return ray parameter for intersection with *seg_a*→*seg_b* or ``None``."""

    ox, oy = origin
    dx, dy = direction
    sx, sy = seg_a
    ex, ey = seg_b
    rx = ex - sx
    ry = ey - sy
    denom = _cross(dx, dy, rx, ry)
    if abs(denom) < 1e-8:
        return None
    diff_x = sx - ox
    diff_y = sy - oy
    t = _cross(diff_x, diff_y, rx, ry) / denom
    u = _cross(diff_x, diff_y, dx, dy) / denom
    if t < 0.0:
        return None
    if u < -1e-6 or u > 1.0 + 1e-6:
        return None
    return t


def _ray_polygon_hit(
    origin: tuple[float, float],
    direction: tuple[float, float],
    quad: Sequence[tuple[float, float]],
) -> float | None:
    """Return smallest positive ``t`` where the ray hits *quad* or ``None``."""

    best_t: float | None = None
    for i in range(len(quad)):
        edge_t = _ray_segment_intersection(origin, direction, quad[i], quad[(i + 1) % len(quad)])
        if edge_t is None:
            continue
        if best_t is None or edge_t < best_t:
            best_t = edge_t
    return best_t


def calculate_min_zoom_to_fit(rect: NormalisedRect, quad: Sequence[tuple[float, float]]) -> float:
    """Return the minimum uniform zoom needed so *rect* fits inside *quad*."""

    cx, cy = rect.center
    corners = [
        (rect.left, rect.top),
        (rect.right, rect.top),
        (rect.right, rect.bottom),
        (rect.left, rect.bottom),
    ]
    max_scale = 1.0
    for corner in corners:
        direction = (corner[0] - cx, corner[1] - cy)
        if abs(direction[0]) <= 1e-9 and abs(direction[1]) <= 1e-9:
            continue
        hit = _ray_polygon_hit((cx, cy), direction, quad)
        if hit is None or hit <= 1e-6:
            continue
        if hit >= 1.0:
            continue
        scale = 1.0 / max(hit, 1e-6)
        if scale > max_scale:
            max_scale = scale
    return max_scale


def unit_quad() -> list[tuple[float, float]]:
    """Return the default quad covering the full normalised texture."""

    return [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]

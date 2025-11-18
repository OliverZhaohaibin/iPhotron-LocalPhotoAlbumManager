"""Perspective projection helpers shared by the renderer and crop logic."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

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
    """Return the 3x3 matrix that maps projected UVs back to texture UVs."""

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


def _point_orientation(
    a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]
) -> float:
    return _cross(b[0] - a[0], b[1] - a[1], c[0] - a[0], c[1] - a[1])


def point_in_convex_polygon(
    point: tuple[float, float], polygon: Sequence[tuple[float, float]]
) -> bool:
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


def inverse_project_point(
    screen_point: tuple[float, float], matrix: np.ndarray
) -> tuple[float, float]:
    """Map a screen-space point back to texture UV coordinates.
    
    This performs the inverse transformation: given a point in the projected
    (screen) coordinate space, returns its corresponding UV texture coordinate.
    
    Parameters
    ----------
    screen_point:
        Point in normalized screen space [0, 1] x [0, 1].
    matrix:
        The perspective transformation matrix (maps texture to screen).
        
    Returns
    -------
    tuple[float, float]
        UV coordinates in texture space [0, 1] x [0, 1].
    """
    x, y = screen_point
    # Convert from [0, 1] to [-1, 1] (NDC space)
    centered = np.array([(x * 2.0) - 1.0, (y * 2.0) - 1.0, 1.0], dtype=np.float32)
    
    # Apply the forward matrix (which is actually the inverse of the projection)
    # Note: build_perspective_matrix returns the matrix that maps projected->texture,
    # so we can use it directly here
    warped = matrix @ centered
    
    # Perspective divide
    denom = float(warped[2])
    if abs(denom) < 1e-6:
        denom = 1e-6 if denom >= 0.0 else -1e-6
    
    nx = float(warped[0]) / denom
    ny = float(warped[1]) / denom
    
    # Convert back from [-1, 1] to [0, 1]
    return ((nx + 1.0) * 0.5, (ny + 1.0) * 0.5)


def calculate_texture_safety_padding(
    texture_width: int, texture_height: int, padding_pixels: int = 3
) -> tuple[float, float]:
    """Calculate safety padding in normalized UV space based on texture resolution.
    
    This ensures that the crop box stays a safe distance from the texture edges
    to prevent bilinear filtering from sampling outside the valid texture region,
    which would cause black borders.
    
    Parameters
    ----------
    texture_width:
        Width of the texture in pixels.
    texture_height:
        Height of the texture in pixels.
    padding_pixels:
        Number of pixels to pad (default: 3 pixels).
        
    Returns
    -------
    tuple[float, float]
        (epsilon_u, epsilon_v) - normalized padding for U and V coordinates.
    """
    if texture_width <= 0 or texture_height <= 0:
        return (0.0, 0.0)
    
    epsilon_u = float(padding_pixels) / float(texture_width)
    epsilon_v = float(padding_pixels) / float(texture_height)
    
    return (epsilon_u, epsilon_v)


def validate_crop_corners_in_uv_space(
    rect: NormalisedRect,
    matrix: np.ndarray,
    texture_size: tuple[int, int],
    padding_pixels: int = 3,
) -> tuple[bool, list[tuple[float, float]]]:
    """Validate that crop box corners stay within safe UV bounds.
    
    This is the "truth test" - it checks whether the crop corners, when
    inverse-projected to texture UV space, fall within the valid region
    with appropriate safety padding to prevent texture filtering artifacts.
    
    Parameters
    ----------
    rect:
        The crop rectangle in normalized screen space.
    matrix:
        The perspective transformation matrix.
    texture_size:
        (width, height) of the texture in pixels.
    padding_pixels:
        Number of pixels to pad (default: 3 pixels).
        
    Returns
    -------
    tuple[bool, list[tuple[float, float]]]
        (is_valid, uv_corners) - Whether all corners are within bounds,
        and the UV coordinates of all four corners.
    """
    tex_w, tex_h = texture_size
    if tex_w <= 0 or tex_h <= 0:
        return (True, [])
    
    # Calculate safety padding in UV space
    epsilon_u, epsilon_v = calculate_texture_safety_padding(tex_w, tex_h, padding_pixels)
    
    # Get crop box corners in screen space
    corners = [
        (rect.left, rect.top),
        (rect.right, rect.top),
        (rect.right, rect.bottom),
        (rect.left, rect.bottom),
    ]
    
    # Project each corner to UV space and check bounds
    uv_corners = []
    all_valid = True
    
    for corner in corners:
        u, v = inverse_project_point(corner, matrix)
        uv_corners.append((u, v))
        
        # Check if UV coordinates are within safe bounds [epsilon, 1-epsilon]
        if u < epsilon_u or u > (1.0 - epsilon_u):
            all_valid = False
        if v < epsilon_v or v > (1.0 - epsilon_v):
            all_valid = False
    
    return (all_valid, uv_corners)


def constrain_rect_to_uv_bounds(
    rect: NormalisedRect,
    matrix: np.ndarray,
    texture_size: tuple[int, int],
    padding_pixels: int = 3,
    max_iterations: int = 20,
) -> NormalisedRect:
    """Iteratively constrain a crop rectangle to stay within UV texture bounds.
    
    This implements the core iterative solver: it repeatedly checks if the crop
    corners are within safe UV bounds, and if not, shrinks the rectangle uniformly
    until all corners are valid.
    
    Parameters
    ----------
    rect:
        The initial crop rectangle in normalized screen space.
    matrix:
        The perspective transformation matrix.
    texture_size:
        (width, height) of the texture in pixels.
    padding_pixels:
        Number of pixels to pad (default: 3 pixels).
    max_iterations:
        Maximum number of shrinking iterations (default: 20).
        
    Returns
    -------
    NormalisedRect
        A constrained rectangle that guarantees all corners are within safe UV bounds.
    """
    current_rect = rect
    
    for _ in range(max_iterations):
        is_valid, uv_corners = validate_crop_corners_in_uv_space(
            current_rect, matrix, texture_size, padding_pixels
        )
        
        if is_valid:
            return current_rect
        
        # Calculate how far out of bounds we are
        epsilon_u, epsilon_v = calculate_texture_safety_padding(
            texture_size[0], texture_size[1], padding_pixels
        )
        
        max_violation = 0.0
        for u, v in uv_corners:
            if u < epsilon_u:
                max_violation = max(max_violation, epsilon_u - u)
            elif u > (1.0 - epsilon_u):
                max_violation = max(max_violation, u - (1.0 - epsilon_u))
            if v < epsilon_v:
                max_violation = max(max_violation, epsilon_v - v)
            elif v > (1.0 - epsilon_v):
                max_violation = max(max_violation, v - (1.0 - epsilon_v))
        
        # Shrink more aggressively if we're far out of bounds
        if max_violation > 0.1:
            shrink_factor = 0.90  # 10% shrink for large violations
        elif max_violation > 0.05:
            shrink_factor = 0.95  # 5% shrink for medium violations
        else:
            shrink_factor = 0.98  # 2% shrink for small violations
        
        cx, cy = current_rect.center
        new_width = current_rect.width * shrink_factor
        new_height = current_rect.height * shrink_factor
        
        current_rect = NormalisedRect(
            left=cx - new_width * 0.5,
            top=cy - new_height * 0.5,
            right=cx + new_width * 0.5,
            bottom=cy + new_height * 0.5,
        )
    
    # If we couldn't converge, return the last attempt
    return current_rect

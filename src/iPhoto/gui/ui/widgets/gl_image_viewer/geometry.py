"""
Coordinate transformation utilities for GL image viewer.

This module provides pure mathematical functions for converting between texture-space
and logical-space coordinates, handling rotation transformations for image display.

## Coordinate System Refactoring

After the coordinate system refactoring, the usage of these functions has changed:

**Texture Space**: The canonical storage format where coordinates remain fixed regardless
of rotation. Used for persistence (saving/loading from .ipo sidecar files).

**Logical Space**: The user's visual coordinate system after rotation. This is what the
user sees on screen and interacts with.

### New Usage Pattern:

1. **Shader-side**: The fragment shader now performs coordinate transformations internally.
   It receives crop parameters in logical space and converts them to texture space for
   sampling. This eliminates the need for Python to perform complex transformations during
   rendering.

2. **Python UI Layer**: Crop interaction controllers work entirely in logical space. The
   boundary checks and user interactions are simplified because they always operate in the
   coordinate system the user sees.

3. **I/O Persistence**: The transformation functions (texture_crop_to_logical,
   logical_crop_to_texture) are now primarily used for:
   - Converting texture coordinates from disk (sidecar files) to logical coordinates for UI
   - Converting logical coordinates back to texture coordinates when saving

This design keeps the stored data immutable with respect to rotation and prevents
floating-point error accumulation across repeated rotations.
"""

from __future__ import annotations

from collections.abc import Mapping


def clamp_unit(value: float) -> float:
    """Clamp *value* into the ``[0, 1]`` interval."""
    return max(0.0, min(1.0, float(value)))


def normalised_crop_from_mapping(
    values: Mapping[str, float],
) -> tuple[float, float, float, float]:
    """Extract a normalised crop tuple from the provided mapping.
    
    Parameters
    ----------
    values:
        Mapping containing crop parameters (Crop_CX, Crop_CY, Crop_W, Crop_H)
        
    Returns
    -------
    tuple[float, float, float, float]
        Normalised (cx, cy, width, height) tuple clamped to [0, 1]
    """
    cx = clamp_unit(values.get("Crop_CX", 0.5))
    cy = clamp_unit(values.get("Crop_CY", 0.5))
    width = clamp_unit(values.get("Crop_W", 1.0))
    height = clamp_unit(values.get("Crop_H", 1.0))
    return (cx, cy, width, height)


def get_rotate_steps(values: Mapping[str, float]) -> int:
    """Return the normalised quarter-turn rotation counter.
    
    Parameters
    ----------
    values:
        Mapping containing the Crop_Rotate90 parameter
        
    Returns
    -------
    int
        Number of 90° rotation steps (0-3)
    """
    return int(float(values.get("Crop_Rotate90", 0.0))) % 4


def texture_crop_to_logical(
    crop: tuple[float, float, float, float], rotate_steps: int
) -> tuple[float, float, float, float]:
    """Map texture-space crop values into logical space for UI rendering.

    Texture coordinates remain the canonical storage format (``Crop_*`` in the session),
    while logical coordinates mirror whatever orientation is currently visible to the user.
    The mapping therefore rotates the centre and swaps the width/height whenever the image is
    turned by 90° increments so that overlays and zoom-to-crop computations operate in the same
    frame as the on-screen preview.
    
    Parameters
    ----------
    crop:
        Tuple of (center_x, center_y, width, height) in texture space
    rotate_steps:
        Number of 90° clockwise rotations (0-3)
        
    Returns
    -------
    tuple[float, float, float, float]
        Crop coordinates in logical/display space
    """
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


def logical_crop_to_texture(
    crop: tuple[float, float, float, float], rotate_steps: int
) -> tuple[float, float, float, float]:
    """Convert logical crop values back into the invariant texture-space frame.

    This is the inverse of :func:`texture_crop_to_logical`. Interaction handlers edit crops in
    logical space (matching the rotated display), so we rotate the updated rectangle back into
    the texture frame before persisting it. Keeping the stored data immutable with respect to
    rotation prevents accumulation of floating-point error across repeated 90° turns and keeps
    the controller logic aligned with the shader's texture-space crop uniforms.
    
    Parameters
    ----------
    crop:
        Tuple of (center_x, center_y, width, height) in logical/display space
    rotate_steps:
        Number of 90° clockwise rotations (0-3)
        
    Returns
    -------
    tuple[float, float, float, float]
        Crop coordinates in texture space
    """
    lcx, lcy, lw, lh = crop
    if rotate_steps == 0:
        return (
            clamp_unit(lcx),
            clamp_unit(lcy),
            clamp_unit(lw),
            clamp_unit(lh),
        )
    if rotate_steps == 1:
        # Step 1 inverse: (x, y) = (y', 1-x') 
        # (reverse of the forward 90° CW transformation)
        return (
            clamp_unit(lcy),
            clamp_unit(1.0 - lcx),
            clamp_unit(lh),
            clamp_unit(lw),
        )
    if rotate_steps == 2:
        return (
            clamp_unit(1.0 - lcx),
            clamp_unit(1.0 - lcy),
            clamp_unit(lw),
            clamp_unit(lh),
        )
    # Step 3 inverse: (x, y) = (1-y', x')
    # (reverse of the forward 90° CCW transformation)
    return (
        clamp_unit(1.0 - lcy),
        clamp_unit(lcx),
        clamp_unit(lh),
        clamp_unit(lw),
    )


def logical_crop_from_texture(
    values: Mapping[str, float],
) -> tuple[float, float, float, float]:
    """Convert texture-space crop values into the rotation-aware logical space.
    
    Parameters
    ----------
    values:
        Mapping containing crop and rotation parameters
        
    Returns
    -------
    tuple[float, float, float, float]
        Crop coordinates in logical/display space
    """
    cx, cy, width, height = normalised_crop_from_mapping(values)
    rotate_steps = get_rotate_steps(values)
    return texture_crop_to_logical((cx, cy, width, height), rotate_steps)


def logical_crop_mapping_from_texture(
    values: Mapping[str, float],
) -> dict[str, float]:
    """Return a mapping of logical crop values derived from texture space.
    
    Parameters
    ----------
    values:
        Mapping containing crop and rotation parameters
        
    Returns
    -------
    dict[str, float]
        Dictionary with Crop_CX, Crop_CY, Crop_W, Crop_H in logical space
    """
    logical_cx, logical_cy, logical_w, logical_h = logical_crop_from_texture(values)
    return {
        "Crop_CX": logical_cx,
        "Crop_CY": logical_cy,
        "Crop_W": logical_w,
        "Crop_H": logical_h,
    }

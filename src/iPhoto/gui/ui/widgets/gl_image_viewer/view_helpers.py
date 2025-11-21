"""
View constraint helpers for GL image viewer.

This module contains pure mathematical functions for constraining the viewport,
ensuring the camera doesn't expose empty pixels beyond texture bounds.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF


def clamp_center_to_texture_bounds(
    center: QPointF,
    scale: float,
    texture_dimensions: tuple[int, int],
    view_dimensions: tuple[int, int],
    has_texture: bool,
) -> QPointF:
    """Return *center* limited so the viewport never exposes empty pixels.
    
    The clamp logic ensures the viewport center stays within bounds such that
    the viewport edges never exceed the texture boundaries. When the viewport
    is larger than the texture, the center is locked to the texture midpoint.
    
    Parameters
    ----------
    center:
        Current viewport center position
    scale:
        Current zoom scale factor
    texture_dimensions:
        Logical texture dimensions (width, height), rotation-aware
    view_dimensions:
        Viewport dimensions (width, height) in device pixels
    has_texture:
        Whether a texture is currently loaded
        
    Returns
    -------
    QPointF
        Clamped center position
    """
    if not has_texture or scale <= 1e-9:
        return center
    
    tex_w, tex_h = texture_dimensions
    vw, vh = view_dimensions
    
    # Calculate half extents of viewport in texture space
    half_view_w = (float(vw) / float(scale)) * 0.5
    half_view_h = (float(vh) / float(scale)) * 0.5
    
    tex_half_w = float(tex_w) * 0.5
    tex_half_h = float(tex_h) * 0.5
    
    # Calculate allowed center range
    min_center_x = half_view_w
    max_center_x = float(tex_w) - half_view_w
    
    # When viewport is larger than texture, lock to center
    if min_center_x > max_center_x:
        min_center_x = tex_half_w
        max_center_x = tex_half_w
    
    min_center_y = half_view_h
    max_center_y = float(tex_h) - half_view_h
    
    if min_center_y > max_center_y:
        min_center_y = tex_half_h
        max_center_y = tex_half_h
    
    # Apply clamping
    clamped_x = max(min_center_x, min(max_center_x, float(center.x())))
    clamped_y = max(min_center_y, min(max_center_y, float(center.y())))
    
    # Additional bounds check to ensure we stay within [0, tex_size]
    clamped_x = max(0.0, min(float(tex_w), clamped_x))
    clamped_y = max(0.0, min(float(tex_h), clamped_y))
    
    return QPointF(clamped_x, clamped_y)

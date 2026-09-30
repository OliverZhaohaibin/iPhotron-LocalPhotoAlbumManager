"""
Crop calculation and view locking logic for GL image viewer.

This module handles the computation of crop rectangles in pixel space and
manages the auto-lock behavior for crop view framing.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF


def has_valid_crop(crop_w: float, crop_h: float) -> bool:
    """Return ``True`` when the adjustments describe a cropped image.
    
    Parameters
    ----------
    crop_w:
        Crop width in logical image coordinates.
    crop_h:
        Crop height in logical image coordinates.
        
    Returns
    -------
    bool
        True if the crop dimensions indicate an actual crop (not full image)
    """
    epsilon = 1e-3
    tolerance = epsilon + 1e-9
    differs_from_full = abs(crop_w - 1.0) > tolerance or abs(crop_h - 1.0) > tolerance
    return differs_from_full and crop_w > 0.0 and crop_h > 0.0


def compute_crop_rect_pixels(
    crop_cx: float,
    crop_cy: float,
    crop_w: float,
    crop_h: float,
    tex_w: int,
    tex_h: int,
) -> QRectF | None:
    """Return the crop rectangle expressed in texture pixels.
    
    Converts logical crop coordinates into the viewer's image-plane pixels.
    Valid perspective-corrected coordinates may extend beyond the original
    texture rectangle.
    
    Parameters
    ----------
    crop_cx:
        Crop center X coordinate (normalized, 0-1)
    crop_cy:
        Crop center Y coordinate (normalized, 0-1)
    crop_w:
        Crop width (normalized, 0-1)
    crop_h:
        Crop height (normalized, 0-1)
    tex_w:
        Texture width in pixels
    tex_h:
        Texture height in pixels
        
    Returns
    -------
    QRectF | None
        Rectangle in pixel coordinates, or None if crop is invalid or covers entire image
    """
    if tex_w <= 0 or tex_h <= 0:
        return None
    
    if not has_valid_crop(crop_w, crop_h):
        return None

    tex_w_f = float(tex_w)
    tex_h_f = float(tex_h)
    width_px = max(1.0, crop_w * tex_w_f)
    height_px = max(1.0, crop_h * tex_h_f)

    center_x = crop_cx * tex_w_f
    center_y = crop_cy * tex_h_f

    half_w = width_px * 0.5
    half_h = height_px * 0.5

    left = center_x - half_w
    top = center_y - half_h
    right = center_x + half_w
    bottom = center_y + half_h

    rect_width = max(1.0, right - left)
    rect_height = max(1.0, bottom - top)
    return QRectF(left, top, rect_width, rect_height)

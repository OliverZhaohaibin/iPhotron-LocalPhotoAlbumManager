"""
Crop-related data structures and utility functions for the GL image viewer.

This module provides backward compatibility by re-exporting from the
refactored gl_crop package.
"""

from __future__ import annotations

from .gl_crop import (
    CropBoxState,
    CropHandle,
    cursor_for_handle,
    ease_in_quad,
    ease_out_cubic,
)

__all__ = [
    "CropBoxState",
    "CropHandle",
    "cursor_for_handle",
    "ease_in_quad",
    "ease_out_cubic",
]

# Backward compatibility - these classes are now in gl_crop.utils
# All functionality has been refactored into the gl_crop package

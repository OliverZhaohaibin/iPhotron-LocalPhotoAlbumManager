"""
GL Crop interaction module.

This package provides modular crop interaction logic for the GL image viewer,
implementing Strategy and State patterns for better maintainability.
"""

from .controller import CropInteractionController
from .utils import CropBoxState, CropHandle, cursor_for_handle, ease_in_quad, ease_out_cubic

__all__ = [
    "CropBoxState",
    "CropHandle",
    "CropInteractionController",
    "cursor_for_handle",
    "ease_in_quad",
    "ease_out_cubic",
]

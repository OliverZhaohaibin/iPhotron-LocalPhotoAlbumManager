"""
Crop interaction controller for the GL image viewer.

This module provides backward compatibility by re-exporting from the
refactored gl_crop package.
"""

from __future__ import annotations

from .gl_crop import CropInteractionController

__all__ = ["CropInteractionController"]

# Backward compatibility - this class is now in gl_crop.controller
# All functionality has been refactored into the gl_crop package

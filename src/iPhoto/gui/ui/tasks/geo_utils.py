"""
Geometry utilities for thumbnail generation.

This shim re-exports everything from ``iPhoto.core.geo_utils``, which is the
canonical location.  Keeping this module avoids breaking existing callers that
import from the gui/ui/tasks package.
"""

from __future__ import annotations

from iPhoto.core.geo_utils import (  # noqa: F401
    build_perspective_matrix,
    clamp_unit,
    texture_crop_to_logical,
)

__all__ = ["build_perspective_matrix", "clamp_unit", "texture_crop_to_logical"]

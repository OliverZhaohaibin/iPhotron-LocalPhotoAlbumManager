"""Tone mapping helpers powering the non-destructive edit pipeline.

This module has been refactored into a modular package structure under
iPhoto.core.filters for improved maintainability. This file now serves
as a compatibility layer, re-exporting the main API.
"""

from __future__ import annotations

from .light_resolver import LIGHT_KEYS


def apply_adjustments(*args, **kwargs):
    """Load the CPU/JIT edit stack only when a render is actually requested."""

    from .filters.facade import apply_adjustments as implementation

    return implementation(*args, **kwargs)

__all__ = ["LIGHT_KEYS", "apply_adjustments"]

"""Modular image filtering package for non-destructive photo editing.

This package provides image adjustment functionality through a clean separation
of concerns:
- algorithms: Pure mathematical functions for image processing
- executors: Different implementation strategies (JIT, Pillow, NumPy, fallback)
- utils: Platform-specific utilities
"""

from __future__ import annotations

def apply_adjustments(*args, **kwargs):
    """Import executors lazily so browsing startup never imports Numba."""

    from .facade import apply_adjustments as implementation

    return implementation(*args, **kwargs)

__all__ = ["apply_adjustments"]

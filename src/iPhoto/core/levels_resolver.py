"""Levels adjustment data structures and LUT generation utilities."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

import numpy as np


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


# Identity handles – the 5 fixed anchor positions that map input to output
# unchanged: (0, 0.25, 0.5, 0.75, 1.0).
DEFAULT_LEVELS_HANDLES: List[float] = [0.0, 0.25, 0.50, 0.75, 1.0]


@dataclass
class LevelsParams:
    """Complete levels adjustment parameters."""

    handles: List[float] = field(default_factory=lambda: list(DEFAULT_LEVELS_HANDLES))
    enabled: bool = False

    def is_identity(self) -> bool:
        """Return True when the handles represent an identity mapping."""
        if len(self.handles) != 5:
            return False
        for actual, default in zip(self.handles, DEFAULT_LEVELS_HANDLES):
            if abs(actual - default) > 1e-6:
                return False
        return True

    def to_dict(self) -> dict:
        return {
            "Levels_Enabled": self.enabled,
            "Levels_Handles": list(self.handles),
        }

    @staticmethod
    def from_dict(data: dict) -> "LevelsParams":
        params = LevelsParams()
        params.enabled = bool(data.get("Levels_Enabled", False))
        raw = data.get("Levels_Handles")
        if isinstance(raw, list) and len(raw) == 5:
            params.handles = [float(v) for v in raw]
        return params


def build_levels_lut(handles: List[float]) -> np.ndarray:
    """Build a (256, 3) float32 LUT from 5 handle positions.

    The five fixed output anchors are (0, 0.25, 0.5, 0.75, 1.0).  The
    *handles* list controls the input x-position for each anchor so the
    curve passes through:

        (x0, 0.00), (x1, 0.25), (x2, 0.50), (x3, 0.75), (x4, 1.00)

    When ``handles == [0, 0.25, 0.5, 0.75, 1]`` the result is the identity
    curve (y = x).
    """

    if len(handles) != 5:
        raise ValueError("handles must be length 5")

    xs = [_clamp01(float(v)) for v in handles]
    for i in range(1, 5):
        if xs[i] < xs[i - 1]:
            xs[i] = xs[i - 1]

    ys = [0.0, 0.25, 0.50, 0.75, 1.0]

    t = np.linspace(0.0, 1.0, 256, dtype=np.float32)
    out = np.empty_like(t)

    x0, x1, x2, x3, x4 = xs
    y0, y1, y2, y3, y4 = ys

    out[t <= x0] = y0
    out[t >= x4] = y4

    def _interp_segment(mask: np.ndarray, xa: float, xb: float, ya: float, yb: float) -> None:
        denom = xb - xa
        if denom <= 1e-8:
            out[mask] = yb
        else:
            u = (t[mask] - xa) / denom
            out[mask] = ya + u * (yb - ya)

    _interp_segment((t > x0) & (t < x1), x0, x1, y0, y1)
    _interp_segment((t >= x1) & (t < x2), x1, x2, y1, y2)
    _interp_segment((t >= x2) & (t < x3), x2, x3, y2, y3)
    _interp_segment((t >= x3) & (t < x4), x3, x4, y3, y4)

    out = np.clip(out, 0.0, 1.0).astype(np.float32)
    lut = np.stack([out, out, out], axis=1).astype(np.float32)
    return lut


def apply_levels_lut_to_image(
    image_array: np.ndarray,
    lut: np.ndarray,
) -> np.ndarray:
    """Apply a levels LUT to an image array.

    Args:
        image_array: (H, W, 3) or (H, W, 4) uint8 array.
        lut: (256, 3) float32 array with values in [0, 1].

    Returns:
        Array of the same shape with the LUT applied.
    """
    result = image_array.copy()
    for c in range(min(3, result.shape[2])):
        lut_channel = (lut[:, c] * 255).astype(np.uint8)
        result[:, :, c] = lut_channel[result[:, :, c]]
    return result


# Session key constants
LEVELS_KEYS = (
    "Levels_Enabled",
    "Levels_Handles",
)

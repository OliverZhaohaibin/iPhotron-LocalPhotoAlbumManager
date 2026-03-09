"""Vignette adjustment data structures and CPU implementation.

The Vignette effect darkens the edges of an image to draw the viewer's
attention to the centre.  The GPU path implements the effect directly in the
fragment shader for real-time performance while this module provides the
equivalent NumPy implementation used by the CPU preview backend, thumbnail
generation, and export pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Default values – no vignette.
DEFAULT_VIGNETTE_STRENGTH: float = 0.0
DEFAULT_VIGNETTE_RADIUS: float = 0.50
DEFAULT_VIGNETTE_SOFTNESS: float = 0.0


@dataclass
class VignetteParams:
    """Complete vignette adjustment parameters."""

    strength: float = 0.0
    radius: float = 0.50
    softness: float = 0.0
    enabled: bool = False

    def is_identity(self) -> bool:
        """Return True when no vignette effect is applied."""
        return not self.enabled or abs(self.strength) < 1e-6

    def to_dict(self) -> dict:
        return {
            "Vignette_Enabled": self.enabled,
            "Vignette_Strength": self.strength,
            "Vignette_Radius": self.radius,
            "Vignette_Softness": self.softness,
        }

    @staticmethod
    def from_dict(data: dict) -> "VignetteParams":
        params = VignetteParams()
        params.enabled = bool(data.get("Vignette_Enabled", False))
        params.strength = float(data.get("Vignette_Strength", 0.0))
        params.radius = float(data.get("Vignette_Radius", 0.50))
        params.softness = float(data.get("Vignette_Softness", 0.0))
        return params


def map_softness(ui_value: float) -> float:
    """Map UI softness ``[0, 1]`` → actual softness ``[0.1, 1.0]``."""
    ui_value = max(0.0, min(1.0, float(ui_value)))
    return 0.1 + ui_value * 0.9


def apply_vignette(
    image_array: np.ndarray,
    strength: float,
    radius: float,
    softness_ui: float,
) -> np.ndarray:
    """Apply a vignette effect to *image_array*.

    Parameters
    ----------
    image_array:
        ``(H, W, 3)`` or ``(H, W, 4)`` uint8 array.
    strength:
        Edge-darkening intensity in ``[0.0, 1.0]``.
    radius:
        Inner edge of the vignette in ``[0.0, 1.0]``.
    softness_ui:
        UI softness value in ``[0.0, 1.0]``, mapped internally to ``[0.1, 1.0]``.

    Returns
    -------
    Array of the same shape with the vignette applied.
    """
    if abs(strength) < 1e-6:
        return image_array

    h, w = image_array.shape[:2]
    has_alpha = image_array.shape[2] == 4

    # Build coordinate grids centred at (0.5, 0.5), normalised to [0, 1].
    ys = np.linspace(0.0, 1.0, h, dtype=np.float32)
    xs = np.linspace(0.0, 1.0, w, dtype=np.float32)
    xg, yg = np.meshgrid(xs, ys)

    centred_x = xg - 0.5
    centred_y = yg - 0.5
    dist = np.sqrt(centred_x * centred_x + centred_y * centred_y) * 1.41421356

    inner = float(np.clip(radius, 0.0, 1.0))
    soft = float(np.clip(map_softness(softness_ui), 0.1, 1.0))
    strength = float(np.clip(strength, 0.0, 1.0))

    # smoothstep
    edge0 = inner
    edge1 = inner + soft
    t = np.clip((dist - edge0) / max(edge1 - edge0, 1e-8), 0.0, 1.0)
    vignette = t * t * (3.0 - 2.0 * t)

    darken = 1.0 - vignette * strength

    result = image_array.copy()
    for c in range(3):
        result[:, :, c] = np.clip(
            image_array[:, :, c].astype(np.float32) * darken, 0.0, 255.0
        ).astype(np.uint8)
    return result


# Session key constants
VIGNETTE_KEYS = (
    "Vignette_Enabled",
    "Vignette_Strength",
    "Vignette_Radius",
    "Vignette_Softness",
)

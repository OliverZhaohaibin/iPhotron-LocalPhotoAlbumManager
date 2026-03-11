"""Definition (Clarity) adjustment data structures and CPU implementation.

The Definition effect enhances local contrast, texture, and structure by
extracting high-frequency detail from multi-scale blurs and re-injecting it
with a midtone-protecting mask.  The GPU path uses mipmap-based sampling for
real-time performance while this module provides the equivalent NumPy
implementation used by the CPU preview backend, thumbnail generation, and
export pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Default value – no definition adjustment.
DEFAULT_DEFINITION: float = 0.0


@dataclass
class DefinitionParams:
    """Complete definition adjustment parameters."""

    value: float = 0.0
    enabled: bool = False

    def is_identity(self) -> bool:
        """Return True when no definition adjustment is applied."""
        return abs(self.value) < 1e-6

    def to_dict(self) -> dict:
        return {
            "Definition_Enabled": self.enabled,
            "Definition_Value": self.value,
        }

    @staticmethod
    def from_dict(data: dict) -> "DefinitionParams":
        params = DefinitionParams()
        params.enabled = bool(data.get("Definition_Enabled", False))
        params.value = float(data.get("Definition_Value", 0.0))
        return params


def apply_definition(image_array: np.ndarray, strength: float) -> np.ndarray:
    """Apply mipmap-style local contrast enhancement to *image_array*.

    Parameters
    ----------
    image_array:
        ``(H, W, 3)`` or ``(H, W, 4)`` uint8 array.
    strength:
        UI value in ``[0.0, 1.0]``.  Internally mapped to ``[0.0, 0.2]``
        matching the GPU shader's ``uDefinition`` range.

    Returns
    -------
    Array of the same shape with definition applied.
    """

    if abs(strength) < 1e-6:
        return image_array

    import cv2

    rgb = image_array[:, :, :3].astype(np.float32) / 255.0

    # Approximate mipmap LOD 3 / 5 / 7 using box filters of increasing radius.
    # LOD N ≈ a 2^N pixel neighbourhood – we use box sizes 8, 32, 128 to match.
    blur1 = cv2.blur(rgb, (8, 8))
    blur2 = cv2.blur(rgb, (32, 32))
    blur3 = cv2.blur(rgb, (128, 128))

    local_mean = (blur1 + blur2 + blur3) / 3.0
    detail = rgb - local_mean

    luma = rgb @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    midtone_mask = 1.0 - np.power(np.abs(2.0 * luma - 1.0), 2.0)

    # Map UI [0, 1] → internal [0, 0.2], then apply 3× amplification.
    internal = strength * 0.2
    amount = internal * 3.0

    enhanced = rgb + detail * amount * (0.3 + 0.7 * midtone_mask[..., np.newaxis])
    enhanced = np.clip(enhanced, 0.0, 1.0)

    result = image_array.copy()
    result[:, :, :3] = (enhanced * 255.0).astype(np.uint8)
    return result


# Session key constants
DEFINITION_KEYS = (
    "Definition_Enabled",
    "Definition_Value",
)

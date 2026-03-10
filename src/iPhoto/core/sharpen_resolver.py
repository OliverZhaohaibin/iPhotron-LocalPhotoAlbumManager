"""Sharpen adjustment data structures and CPU implementation.

The Sharpen effect uses an Unsharp Mask (USM) technique to enhance local
contrast and edge detail.  The GPU path implements the filter directly in
the fragment shader for real-time performance while this module provides
the equivalent NumPy/OpenCV implementation used by the CPU preview
backend, thumbnail generation, and export pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Default value – no sharpening.
DEFAULT_SHARPEN: float = 0.0


@dataclass
class SharpenParams:
    """Complete sharpen adjustment parameters."""

    amount: float = 0.0
    enabled: bool = False

    def is_identity(self) -> bool:
        """Return True when no sharpening is applied."""
        return not self.enabled or abs(self.amount) < 1e-6

    def to_dict(self) -> dict:
        return {
            "Sharpen_Enabled": self.enabled,
            "Sharpen_Amount": self.amount,
        }

    @staticmethod
    def from_dict(data: dict) -> "SharpenParams":
        params = SharpenParams()
        params.enabled = bool(data.get("Sharpen_Enabled", False))
        params.amount = float(data.get("Sharpen_Amount", 0.0))
        return params


def apply_sharpen(image_array: np.ndarray, amount: float) -> np.ndarray:
    """Apply unsharp-mask sharpening to *image_array*.

    Parameters
    ----------
    image_array:
        ``(H, W, 3)`` or ``(H, W, 4)`` uint8 array.
    amount:
        UI value in ``[0.0, 3.0]``.  Internally used as the gain factor
        for the high-frequency detail layer.

    Returns
    -------
    Array of the same shape with sharpening applied.
    """

    if abs(amount) < 1e-6:
        return image_array

    import cv2

    has_alpha = image_array.shape[2] == 4
    if has_alpha:
        rgb = image_array[:, :, :3]
    else:
        rgb = image_array

    # Gaussian blur as the low-frequency reference.
    blurred = cv2.GaussianBlur(rgb, (3, 3), 1.0)

    # Unsharp mask: sharpened = original + amount * (original - blurred)
    sharpened = cv2.addWeighted(rgb, 1.0 + amount, blurred, -amount, 0)

    result = image_array.copy()
    result[:, :, :3] = sharpened
    return result


# Session key constants
SHARPEN_KEYS = (
    "Sharpen_Enabled",
    "Sharpen_Amount",
)

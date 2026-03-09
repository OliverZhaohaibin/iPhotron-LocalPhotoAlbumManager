"""Noise Reduction (Denoise) adjustment data structures and CPU implementation.

The Denoise effect uses a bilateral filter for edge-preserving smoothing,
reducing luminance and colour noise while retaining sharp transitions.  The
GPU path implements the filter directly in the fragment shader for real-time
performance while this module provides the equivalent NumPy/OpenCV
implementation used by the CPU preview backend, thumbnail generation, and
export pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Default value – no noise reduction.
DEFAULT_DENOISE: float = 0.0


@dataclass
class DenoiseParams:
    """Complete noise-reduction adjustment parameters."""

    amount: float = 0.0
    enabled: bool = False

    def is_identity(self) -> bool:
        """Return True when no noise reduction is applied."""
        return not self.enabled or abs(self.amount) < 1e-6

    def to_dict(self) -> dict:
        return {
            "Denoise_Enabled": self.enabled,
            "Denoise_Amount": self.amount,
        }

    @staticmethod
    def from_dict(data: dict) -> "DenoiseParams":
        params = DenoiseParams()
        params.enabled = bool(data.get("Denoise_Enabled", False))
        params.amount = float(data.get("Denoise_Amount", 0.0))
        return params


def apply_denoise(image_array: np.ndarray, amount: float) -> np.ndarray:
    """Apply bilateral-filter noise reduction to *image_array*.

    Parameters
    ----------
    image_array:
        ``(H, W, 3)`` or ``(H, W, 4)`` uint8 array.
    amount:
        UI value in ``[0.0, 5.0]``.  Internally mapped to the ``sigmaColor``
        parameter of a bilateral filter.

    Returns
    -------
    Array of the same shape with noise reduction applied.
    """

    if abs(amount) < 1e-6:
        return image_array

    import cv2

    has_alpha = image_array.shape[2] == 4
    if has_alpha:
        rgb = image_array[:, :, :3]
        alpha = image_array[:, :, 3:4]
    else:
        rgb = image_array

    # Map UI amount → sigmaColor (matches the GPU shader logic: amount * 0.075
    # on the [0,1] float domain → here on [0,255] uint8 domain).
    sigma_color = max(amount * 0.075 * 255.0, 0.1)
    sigma_space = 1.5
    radius = 3
    d = 2 * radius + 1

    filtered = cv2.bilateralFilter(rgb, d, sigma_color, sigma_space)

    result = image_array.copy()
    result[:, :, :3] = filtered
    return result


# Session key constants
DENOISE_KEYS = (
    "Denoise_Enabled",
    "Denoise_Amount",
)

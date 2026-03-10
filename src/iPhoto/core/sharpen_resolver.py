"""Sharpen adjustment data structures and CPU implementation.

The Sharpen effect uses an Unsharp Mask combined with edge masking to enhance
high-frequency detail while protecting flat areas from noise amplification.
Three parameters control the behaviour:

* **Intensity** – overall sharpening strength ``[0.0, 1.0]``
* **Edges** – threshold for local contrast masking ``[0.0, 1.0]``
* **Falloff** – smoothness of the edge mask transition ``[0.0, 1.0]``

The GPU path implements the filter directly in the fragment shader for
real-time performance while this module provides the equivalent NumPy/OpenCV
implementation used by the CPU preview backend, thumbnail generation, and
export pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Default values – no sharpening.
DEFAULT_SHARPEN_INTENSITY: float = 0.0
DEFAULT_SHARPEN_EDGES: float = 0.0
DEFAULT_SHARPEN_FALLOFF: float = 0.0


@dataclass
class SharpenParams:
    """Complete sharpening adjustment parameters."""

    intensity: float = 0.0
    edges: float = 0.0
    falloff: float = 0.0
    enabled: bool = False

    def is_identity(self) -> bool:
        """Return True when no sharpening is applied."""
        return not self.enabled or abs(self.intensity) < 1e-6

    def to_dict(self) -> dict:
        return {
            "Sharpen_Enabled": self.enabled,
            "Sharpen_Intensity": self.intensity,
            "Sharpen_Edges": self.edges,
            "Sharpen_Falloff": self.falloff,
        }

    @staticmethod
    def from_dict(data: dict) -> "SharpenParams":
        params = SharpenParams()
        params.enabled = bool(data.get("Sharpen_Enabled", False))
        params.intensity = float(data.get("Sharpen_Intensity", 0.0))
        params.edges = float(data.get("Sharpen_Edges", 0.0))
        params.falloff = float(data.get("Sharpen_Falloff", 0.0))
        return params


def apply_sharpen(
    image_array: np.ndarray,
    intensity: float,
    edges: float,
    falloff: float,
) -> np.ndarray:
    """Apply Unsharp Mask with edge masking to *image_array*.

    Parameters
    ----------
    image_array:
        ``(H, W, 3)`` or ``(H, W, 4)`` uint8 array.
    intensity:
        UI value in ``[0.0, 1.0]``.  Internally mapped to ``[0.0, 5.0]``
        as the actual sharpening multiplier.
    edges:
        Edge detection threshold in ``[0.0, 1.0]``.  Higher values restrict
        sharpening to stronger edges only.
    falloff:
        Smoothness of the edge mask transition in ``[0.0, 1.0]``.

    Returns
    -------
    Array of the same shape with sharpening applied.
    """

    if abs(intensity) < 1e-6:
        return image_array

    import cv2

    has_alpha = image_array.shape[2] == 4
    if has_alpha:
        rgb = image_array[:, :, :3].astype(np.float32) / 255.0
    else:
        rgb = image_array.astype(np.float32) / 255.0

    # 1. Approximate Gaussian blur (3×3 kernel)
    blur = cv2.GaussianBlur(rgb, (3, 3), 0)

    # 2. High-pass detail (Unsharp Mask)
    high_pass = rgb - blur

    # 3. Local luminance contrast for edge detection
    luma_coef = np.array([0.299, 0.587, 0.114], dtype=np.float32)
    luma = np.sum(rgb * luma_coef, axis=2)

    # Compute local min/max using morphological ops for efficiency
    kernel = np.ones((3, 3), np.uint8)
    luma_u8 = (np.clip(luma, 0.0, 1.0) * 255.0).astype(np.uint8)
    local_min = cv2.erode(luma_u8, kernel).astype(np.float32) / 255.0
    local_max = cv2.dilate(luma_u8, kernel).astype(np.float32) / 255.0
    local_contrast = local_max - local_min

    # 4. Edge masking (matches shader smoothstep logic)
    threshold = edges * 0.4
    band = max(falloff * 0.4, 0.001)
    # smoothstep implementation
    t = np.clip((local_contrast - threshold) / band, 0.0, 1.0)
    mask = t * t * (3.0 - 2.0 * t)

    # 5. Apply sharpening
    amount = intensity * 5.0
    mask_3d = mask[:, :, np.newaxis]
    sharpened = rgb + high_pass * amount * mask_3d
    sharpened = np.clip(sharpened, 0.0, 1.0)

    # Convert back to uint8
    result_rgb = (sharpened * 255.0).astype(np.uint8)

    result = image_array.copy()
    if has_alpha:
        result[:, :, :3] = result_rgb
    else:
        result[:, :, :] = result_rgb
    return result


# Session key constants
SHARPEN_KEYS = (
    "Sharpen_Enabled",
    "Sharpen_Intensity",
    "Sharpen_Edges",
    "Sharpen_Falloff",
)

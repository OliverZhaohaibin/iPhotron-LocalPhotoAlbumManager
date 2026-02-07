"""White Balance adjustment resolver.

Provides the :class:`WBParams` data class and helpers for applying warmth,
temperature and tint corrections on the CPU.  The GPU path uses equivalent GLSL
code defined in the fragment shader.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PySide6.QtGui import QImage


WB_KEYS = ("WB_Warmth", "WB_Temperature", "WB_Tint")

WB_DEFAULTS: dict[str, float] = {
    "WB_Warmth": 0.0,
    "WB_Temperature": 0.0,
    "WB_Tint": 0.0,
}


@dataclass(frozen=True)
class WBParams:
    """Immutable snapshot of the white-balance controls.

    All three parameters are normalised to ``[-1, 1]``.
    """

    warmth: float = 0.0
    temperature: float = 0.0
    tint: float = 0.0

    def is_identity(self) -> bool:
        """Return ``True`` when no correction would be applied."""

        return (
            abs(self.warmth) < 1e-6
            and abs(self.temperature) < 1e-6
            and abs(self.tint) < 1e-6
        )


# ------------------------------------------------------------------
# CPU application helpers (mirrors the GLSL in gl_image_viewer.frag)
# ------------------------------------------------------------------

def _warmth_adjust(rgb: np.ndarray, w: float) -> np.ndarray:
    """Apply warmth shift to an ``(H, W, 3)`` float32 array in-place."""

    if abs(w) < 1e-6:
        return rgb

    scale = 0.15 * w
    temp_gain = np.array([1.0 + scale, 1.0, 1.0 - scale], dtype=np.float32)
    luma_coeff = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)

    orig_luma = np.sum(rgb * luma_coeff, axis=-1, keepdims=True)
    rgb = rgb * temp_gain
    new_luma = np.sum(rgb * luma_coeff, axis=-1, keepdims=True)
    safe_luma = np.where(new_luma > 0.001, new_luma, 1.0)
    rgb = rgb * (orig_luma / safe_luma)
    return rgb


def _temp_tint_adjust(rgb: np.ndarray, temp: float, tint: float) -> np.ndarray:
    """Apply temperature and tint correction to an ``(H, W, 3)`` float32 array."""

    if abs(temp) < 1e-6 and abs(tint) < 1e-6:
        return rgb

    luma_coeff = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    orig_luma = np.sum(rgb * luma_coeff, axis=-1, keepdims=True)

    temp_scale = 0.3 * temp
    temp_gain = np.array(
        [1.0 + temp_scale * 0.8, 1.0, 1.0 - temp_scale], dtype=np.float32
    )

    tint_scale = 0.2 * tint
    tint_gain = np.array(
        [1.0 + tint_scale * 0.5, 1.0 - tint_scale * 0.5, 1.0 + tint_scale * 0.5],
        dtype=np.float32,
    )

    rgb = rgb * temp_gain * tint_gain
    new_luma = np.sum(rgb * luma_coeff, axis=-1, keepdims=True)
    safe_luma = np.where(new_luma > 0.001, new_luma, 1.0)
    rgb = rgb * (orig_luma / safe_luma)
    return rgb


def apply_wb(image: QImage, params: WBParams) -> QImage:
    """Return a copy of *image* with white-balance adjustments applied.

    Used by the CPU export / thumbnail path.
    """

    if params.is_identity():
        return QImage(image)

    img = image.convertToFormat(QImage.Format.Format_RGBA8888)
    width, height = img.width(), img.height()
    ptr = img.bits()
    byte_count = img.sizeInBytes()
    if hasattr(ptr, "setsize"):
        ptr.setsize(byte_count)

    arr = np.frombuffer(ptr, dtype=np.uint8).reshape((height, width, 4)).copy()
    rgb = arr[:, :, :3].astype(np.float32) / 255.0

    rgb = _warmth_adjust(rgb, params.warmth)
    rgb = _temp_tint_adjust(rgb, params.temperature, params.tint)

    rgb = np.clip(rgb * 255.0, 0, 255).astype(np.uint8)
    arr[:, :, :3] = rgb

    result = QImage(arr.data, width, height, arr.strides[0], QImage.Format.Format_RGBA8888).copy()
    return result.convertToFormat(QImage.Format.Format_ARGB32)

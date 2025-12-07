"""NumPy vectorized executor for image adjustments.

This module provides efficient vectorized implementations of image adjustment
effects using NumPy operations. It serves as a robust fallback when Numba JIT
compilation is unavailable (e.g., in Nuitka-packaged builds without AOT).
"""

from __future__ import annotations

import math
import numpy as np
from PySide6.QtGui import QImage

from .utils import _resolve_pixel_buffer


def _np_mix(a: np.ndarray, b: np.ndarray, t: float) -> np.ndarray:
    """Vectorized equivalent of GLSL's ``mix`` helper."""
    return a * (1.0 - t) + b * t


def _np_clamp01(arr: np.ndarray) -> np.ndarray:
    """Clamp array values to [0.0, 1.0]."""
    return np.clip(arr, 0.0, 1.0)


def _np_apply_channel_adjustments(
    channel: np.ndarray,
    exposure: float,
    brightness: float,
    brilliance: float,
    highlights: float,
    shadows: float,
    contrast_factor: float,
    black_point: float,
) -> np.ndarray:
    """Apply tone curve adjustments to a normalized channel array."""

    # 1. Exposure & Brightness
    adjusted = channel + exposure + brightness

    # 2. Brilliance
    mid_distance = channel - 0.5
    adjusted += brilliance * (1.0 - (mid_distance * 2.0) ** 2)

    # 3. Highlights & Shadows
    cond_high = adjusted > 0.65
    cond_low = adjusted < 0.35

    ratio_high = (adjusted - 0.65) / 0.35
    delta_high = highlights * ratio_high

    ratio_low = (0.35 - adjusted) / 0.35
    delta_low = shadows * ratio_low

    val_high = adjusted + delta_high
    val_low = adjusted + delta_low

    adjusted = np.select([cond_high, cond_low], [val_high, val_low], default=adjusted)

    # 4. Contrast
    adjusted = (adjusted - 0.5) * contrast_factor + 0.5

    # 5. Black Point
    if black_point > 0:
        adjusted -= black_point * (1.0 - adjusted)
    elif black_point < 0:
        adjusted -= black_point * adjusted

    return _np_clamp01(adjusted)


def _np_apply_color_transform(
    r: np.ndarray,
    g: np.ndarray,
    b: np.ndarray,
    saturation: float,
    vibrance: float,
    cast: float,
    gain_r: float,
    gain_g: float,
    gain_b: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Apply color adjustments to RGB channel arrays."""

    mix_r = (1.0 - cast) + gain_r * cast
    mix_g = (1.0 - cast) + gain_g * cast
    mix_b = (1.0 - cast) + gain_b * cast

    r = r * mix_r
    g = g * mix_g
    b = b * mix_b

    luma = 0.299 * r + 0.587 * g + 0.114 * b

    chroma_r = r - luma
    chroma_g = g - luma
    chroma_b = b - luma

    sat_amt = 1.0 + saturation
    vib_amt = 1.0 + vibrance

    w_term = np.abs(luma - 0.5) * 2.0
    w = 1.0 - np.clip(w_term, 0.0, 1.0)

    chroma_scale = sat_amt * (1.0 + (vib_amt - 1.0) * w)

    chroma_r *= chroma_scale
    chroma_g *= chroma_scale
    chroma_b *= chroma_scale

    r = _np_clamp01(luma + chroma_r)
    g = _np_clamp01(luma + chroma_g)
    b = _np_clamp01(luma + chroma_b)

    return r, g, b


def _bw_unsigned_to_signed(value: float) -> float:
    """Remap a value from [0, 1] to [-1, 1] range."""
    return float(max(-1.0, min(1.0, float(value) * 2.0 - 1.0)))

def _np_gamma_neutral_signed(gray: np.ndarray, neutral_adjust: float) -> np.ndarray:
    """Apply a gamma-based neutral adjustment to a grayscale array.

    The `neutral_adjust` parameter (in [-1.0, 1.0]) modulates the gamma curve,
    shifting midtones toward lighter or darker values for neutral balance.
    """
    neutral = float(max(-1.0, min(1.0, neutral_adjust)))
    magnitude = 0.6 * abs(neutral)
    gamma = math.pow(2.0, -magnitude) if neutral >= 0.0 else math.pow(2.0, magnitude)
    clamped = np.clip(gray, 0.0, 1.0).astype(np.float32, copy=False)
    np.power(clamped, gamma, out=clamped)
    return np.clip(clamped, 0.0, 1.0)

def _np_contrast_tone_signed(gray: np.ndarray, tone_adjust: float) -> np.ndarray:
    """
    Apply a signed contrast adjustment via a tone curve to a grayscale array.
    The `tone_adjust` parameter (in [-1.0, 1.0]) controls the steepness of the tone curve:
    - Positive values steepen the curve, increasing contrast.
    - Negative values flatten the curve, decreasing contrast.
    In this context, "tone" refers to the shape of the curve mapping input luminance to output luminance,
    affecting overall image contrast.

    Positive values increase contrast (steepen the curve), negative values decrease contrast (flatten the curve).
    """
    tone_value = float(max(-1.0, min(1.0, tone_adjust)))
    if tone_value >= 0.0:
        k = 1.0 + (2.2 - 1.0) * tone_value
    else:
        k = 1.0 + (0.6 - 1.0) * -tone_value

    x = np.clip(gray, 0.0, 1.0).astype(np.float32, copy=False)
    epsilon = 1e-6
    clamped = np.clip(x, epsilon, 1.0 - epsilon)
    logit = np.log(clamped / np.clip(1.0 - clamped, epsilon, 1.0))
    result = 1.0 / (1.0 + np.exp(-logit * k))
    return np.clip(result.astype(np.float32, copy=False), 0.0, 1.0)

def _generate_grain_field(width: int, height: int) -> np.ndarray:
    """Generate a deterministic pseudo-random grain field for the given dimensions.

    Uses a sine-based hash to create a repeatable noise pattern in [0.0, 1.0].
    """
    if width <= 0 or height <= 0:
        return np.zeros((max(1, height), max(1, width)), dtype=np.float32)

    x = np.arange(width, dtype=np.float32)
    y = np.arange(height, dtype=np.float32)
    if width > 1:
        u = x / float(width - 1)
    else:
        u = np.zeros_like(x)
    if height > 1:
        v = y / float(height - 1)
    else:
        v = np.zeros_like(y)

    seed = u[None, :] * np.float32(12.9898) + v[:, None] * np.float32(78.233)
    noise = np.sin(seed).astype(np.float32, copy=False) * np.float32(43758.5453)
    fraction = noise - np.floor(noise)
    return np.clip(fraction.astype(np.float32), 0.0, 1.0)

# Restored original BW functions required by facade.py
def apply_bw_vectorized(
    image: QImage,
    intensity: float,
    neutrals: float,
    tone: float,
    grain: float,
) -> bool:
    """Attempt to apply the Black & White effect using a fully vectorised path."""
    width = image.width()
    height = image.height()
    bytes_per_line = image.bytesPerLine()

    if width <= 0 or height <= 0:
        return True

    try:
        view, guard = _resolve_pixel_buffer(image)
    except (BufferError, RuntimeError, TypeError):
        return False

    # Keep a reference to the Qt buffer wrapper to prevent premature deallocation
    _ = guard

    # Reuse the buffer implementation
    expected_size = bytes_per_line * height
    buffer = np.frombuffer(view, dtype=np.uint8, count=expected_size)


    # Using the previous implementation for Black & White effect to ensure stability and avoid regressions.
    try:
        surface = buffer.reshape((height, bytes_per_line))
    except ValueError:
        return False

    rgb_region = surface[:, : width * 4].reshape((height, width, 4))
    bgr = rgb_region[..., :3].astype(np.float32, copy=False)
    rgb = bgr[:, :, ::-1] / np.float32(255.0)

    intensity_signed = _bw_unsigned_to_signed(intensity)
    neutrals_signed = _bw_unsigned_to_signed(neutrals)
    tone_signed = _bw_unsigned_to_signed(tone)
    grain_amount = float(max(0.0, min(1.0, grain)))

    if (
        abs(intensity_signed) <= 1e-6
        and abs(neutrals_signed) <= 1e-6
        and abs(tone_signed) <= 1e-6
        and grain_amount <= 1e-6
    ):
        return True

    luma = (
        rgb[:, :, 0] * 0.2126
        + rgb[:, :, 1] * 0.7152
        + rgb[:, :, 2] * 0.0722
    ).astype(np.float32)

    luma_clamped = np.clip(luma, 0.0, 1.0).astype(np.float32, copy=False)
    g_soft = np.power(luma_clamped, 0.85).astype(np.float32, copy=False)
    g_neutral = luma
    g_rich = _np_contrast_tone_signed(luma, 0.35)

    if intensity_signed >= 0.0:
        gray = _np_mix(g_neutral, g_rich, intensity_signed)
    else:
        gray = _np_mix(g_soft, g_neutral, intensity_signed + 1.0)

    gray = _np_gamma_neutral_signed(gray, neutrals_signed)
    gray = _np_contrast_tone_signed(gray, tone_signed)

    if grain_amount > 1e-6:
        noise = _generate_grain_field(width, height)
        gray = gray + (noise - 0.5) * 0.2 * grain_amount

    gray = np.clip(gray, 0.0, 1.0).astype(np.float32, copy=False)
    gray_bytes = np.rint(gray * np.float32(255.0)).astype(np.uint8)

    rgb_region[..., 0] = gray_bytes
    rgb_region[..., 1] = gray_bytes
    rgb_region[..., 2] = gray_bytes

    return True

def apply_bw_only(
    image: QImage,
    intensity: float,
    neutrals: float,
    tone: float,
    grain: float,
) -> bool:
    """Apply the Black & White pass to *image* in-place."""
    if image.isNull():
        return True
    return apply_bw_vectorized(image, intensity, neutrals, tone, grain)


def _prepare_pixel_view(
    buffer: np.ndarray,
    width: int,
    height: int,
    bytes_per_line: int,
) -> np.ndarray | None:
    """Validate buffer dimensions and return a reshaped view (H, W, 4).

    Returns None if validation fails.
    """
    if width <= 0 or height <= 0:
        return None

    expected_size = bytes_per_line * height
    if buffer.size < expected_size:
        return None

    try:
        lines = buffer[:expected_size].reshape((height, bytes_per_line))
    except ValueError:
        return None

    valid_width = width * 4
    if valid_width > bytes_per_line:
        return None

    return lines[:, :valid_width].reshape((height, width, 4))


def apply_adjustments_buffer(
    buffer: np.ndarray,
    width: int,
    height: int,
    bytes_per_line: int,
    exposure_term: float,
    brightness_term: float,
    brilliance_strength: float,
    highlights: float,
    shadows: float,
    contrast_factor: float,
    black_point: float,
    saturation: float,
    vibrance: float,
    cast: float,
    gain_r: float,
    gain_g: float,
    gain_b: float,
    apply_color: bool,
    apply_bw: bool,
    bw_intensity: float,
    bw_neutrals: float,
    bw_tone: float,
    bw_grain: float,
) -> None:
    """Apply adjustments using NumPy vectorization on a raw buffer."""

    pixels_view = _prepare_pixel_view(buffer, width, height, bytes_per_line)
    if pixels_view is None:
        return

    b_float = pixels_view[..., 0].astype(np.float32) / 255.0
    g_float = pixels_view[..., 1].astype(np.float32) / 255.0
    r_float = pixels_view[..., 2].astype(np.float32) / 255.0

    # 1. Tone Curve
    channel_args = (exposure_term, brightness_term, brilliance_strength, highlights, shadows, contrast_factor, black_point)
    r_float = _np_apply_channel_adjustments(r_float, *channel_args)
    g_float = _np_apply_channel_adjustments(g_float, *channel_args)
    b_float = _np_apply_channel_adjustments(b_float, *channel_args)

    # 2. Color Transform
    if apply_color:
        r_float, g_float, b_float = _np_apply_color_transform(
            r_float, g_float, b_float,
            saturation, vibrance, cast,
            gain_r, gain_g, gain_b
        )

    # 3. BW
    if apply_bw:
        luma = (0.2126 * r_float + 0.7152 * g_float + 0.0722 * b_float)
        luma = np.clip(luma, 0.0, 1.0)

        intensity_s = _bw_unsigned_to_signed(bw_intensity)
        neutrals_s = _bw_unsigned_to_signed(bw_neutrals)
        tone_s = _bw_unsigned_to_signed(bw_tone)
        grain_amt = bw_grain

        soft_base = np.power(luma, 0.82)

        soft_curve = _np_contrast_tone_signed(soft_base, -1.0)
        g_soft = (soft_curve + soft_base) * 0.5
        g_neutral = luma
        g_rich = _np_contrast_tone_signed(np.power(luma, 1.0/1.22), -0.3)

        if intensity_s >= 0.0:
            gray = _np_mix(g_neutral, g_rich, intensity_s)
        else:
            gray = _np_mix(g_soft, g_neutral, intensity_s + 1.0)

        gray = _np_gamma_neutral_signed(gray, neutrals_s)
        gray = _np_contrast_tone_signed(gray, tone_s)

        if abs(grain_amt) > 1e-6:
            noise = _generate_grain_field(width, height)
            gray += (noise - 0.5) * 0.2 * grain_amt

        gray = np.clip(gray, 0.0, 1.0)

        r_float = gray
        g_float = gray
        b_float = gray

    pixels_view[..., 0] = (b_float * 255.0).astype(np.uint8)
    pixels_view[..., 1] = (g_float * 255.0).astype(np.uint8)
    pixels_view[..., 2] = (r_float * 255.0).astype(np.uint8)


def apply_color_adjustments_inplace_buffer(
    buffer: np.ndarray,
    width: int,
    height: int,
    bytes_per_line: int,
    saturation: float,
    vibrance: float,
    cast: float,
    gain_r: float,
    gain_g: float,
    gain_b: float,
) -> None:
    """Apply only color adjustments using NumPy vectorization."""

    pixels_view = _prepare_pixel_view(buffer, width, height, bytes_per_line)
    if pixels_view is None:
        return

    b_float = pixels_view[..., 0].astype(np.float32) / 255.0
    g_float = pixels_view[..., 1].astype(np.float32) / 255.0
    r_float = pixels_view[..., 2].astype(np.float32) / 255.0

    r_float, g_float, b_float = _np_apply_color_transform(
        r_float, g_float, b_float,
        saturation, vibrance, cast,
        gain_r, gain_g, gain_b
    )

    pixels_view[..., 0] = (b_float * 255.0).astype(np.uint8)
    pixels_view[..., 1] = (g_float * 255.0).astype(np.uint8)
    pixels_view[..., 2] = (r_float * 255.0).astype(np.uint8)

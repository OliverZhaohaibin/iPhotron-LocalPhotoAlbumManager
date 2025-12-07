"""JIT-compiled kernels for image processing.

This module contains Numba JIT-decorated functions that are used for
runtime compilation. These functions are separated from the main executor
to improve testability and maintainability.
"""

from __future__ import annotations

import numpy as np

from numba import jit

from .algorithms import (
    _apply_bw_channels,
    _apply_channel_adjustments,
    _apply_color_transform,
    _float_to_uint8,
    _grain_noise,
)


@jit(nopython=True, cache=True)
def _apply_adjustments_fast(
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
    """JIT-compiled pixel processing kernel."""
    if width <= 0 or height <= 0:
        return

    for y in range(height):
        row_offset = y * bytes_per_line
        for x in range(width):
            pixel_offset = row_offset + x * 4

            b = buffer[pixel_offset] / 255.0
            g = buffer[pixel_offset + 1] / 255.0
            r = buffer[pixel_offset + 2] / 255.0

            r = _apply_channel_adjustments(
                r,
                exposure_term,
                brightness_term,
                brilliance_strength,
                highlights,
                shadows,
                contrast_factor,
                black_point,
            )
            g = _apply_channel_adjustments(
                g,
                exposure_term,
                brightness_term,
                brilliance_strength,
                highlights,
                shadows,
                contrast_factor,
                black_point,
            )
            b = _apply_channel_adjustments(
                b,
                exposure_term,
                brightness_term,
                brilliance_strength,
                highlights,
                shadows,
                contrast_factor,
                black_point,
            )

            if apply_color:
                r, g, b = _apply_color_transform(
                    r,
                    g,
                    b,
                    saturation,
                    vibrance,
                    cast,
                    gain_r,
                    gain_g,
                    gain_b,
                )

            if apply_bw:
                noise = 0.0
                if abs(bw_grain) > 1e-6:
                    noise = _grain_noise(x, y, width, height)
                r, g, b = _apply_bw_channels(
                    r,
                    g,
                    b,
                    bw_intensity,
                    bw_neutrals,
                    bw_tone,
                    bw_grain,
                    noise,
                )

            buffer[pixel_offset] = _float_to_uint8(b)
            buffer[pixel_offset + 1] = _float_to_uint8(g)
            buffer[pixel_offset + 2] = _float_to_uint8(r)


@jit(nopython=True, cache=True)
def _apply_color_adjustments_inplace(
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
    """JIT-compiled color adjustment kernel."""
    if width <= 0 or height <= 0:
        return

    apply_color = abs(saturation) > 1e-6 or abs(vibrance) > 1e-6 or cast > 1e-6
    if not apply_color:
        return

    for y in range(height):
        row_offset = y * bytes_per_line
        for x in range(width):
            pixel_offset = row_offset + x * 4
            b = buffer[pixel_offset] / 255.0
            g = buffer[pixel_offset + 1] / 255.0
            r = buffer[pixel_offset + 2] / 255.0

            r, g, b = _apply_color_transform(
                r,
                g,
                b,
                saturation,
                vibrance,
                cast,
                gain_r,
                gain_g,
                gain_b,
            )

            buffer[pixel_offset] = _float_to_uint8(b)
            buffer[pixel_offset + 1] = _float_to_uint8(g)
            buffer[pixel_offset + 2] = _float_to_uint8(r)

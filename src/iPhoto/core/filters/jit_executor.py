"""JIT-accelerated image adjustment executor using Numba.

This module provides the fastest execution path for image adjustments,
using Numba JIT compilation to process pixels directly in the QImage buffer.
It gracefully falls back to AOT compiled extensions or NumPy vectorization
when runtime JIT is unavailable (e.g., in Nuitka packaged builds).
"""

from __future__ import annotations

import logging
import os
import sys
import numpy as np
from PySide6.QtGui import QImage

from .utils import _resolve_pixel_buffer

logger = logging.getLogger(__name__)

# Execution Strategy Resolution
# -----------------------------
# 1. AOT (Ahead-Of-Time): Preferred for production builds.
# 2. JIT (Just-In-Time): Preferred for development (if Numba is present).
# 3. NumPy: Fallback for packaged builds where AOT is missing and JIT is impossible.

_AOT_AVAILABLE = False
_JIT_AVAILABLE = False

# Function placeholders
_apply_adjustments_fast = None
_apply_color_adjustments_inplace = None

# 1. Try AOT
# Check if we are in build mode (set by build_jit.py). If so, we skip AOT
# to ensure the build script sees the original Python functions.
_IN_BUILD_MODE = os.environ.get("IPHOTO_BUILD_AOT") == "1"

if not _IN_BUILD_MODE:
    try:
        from . import _jit_compiled

        _apply_adjustments_fast = _jit_compiled._apply_adjustments_fast
        _apply_color_adjustments_inplace = _jit_compiled._apply_color_adjustments_inplace
        _AOT_AVAILABLE = True
        logger.info("Loaded AOT-compiled image filters.")
    except ImportError:
        logger.debug("AOT compiled module not found.")

# 2. Try JIT (if AOT not used)
if not _AOT_AVAILABLE:
    # Check if we are running in a Nuitka-compiled environment.
    # Numba JIT requires raw Python bytecode. Nuitka strips this, so JIT will crash.
    # Use robust check for compiled environments (Nuitka, PyInstaller, etc.)
    _IS_COMPILED = (
        "__compiled__" in globals() or
        hasattr(sys, "frozen") or
        hasattr(sys, "_MEIPASS")
    )

    if not _IS_COMPILED:
        try:
            from numba import jit
            _JIT_AVAILABLE = True
        except ImportError:
            logger.debug("Numba is not available; falling back to NumPy implementation.")

    if _JIT_AVAILABLE:
        logger.debug("Using runtime Numba JIT compilation.")

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

    else:
        # 3. Fallback: NumPy Vectorization
        # Used when AOT is missing AND (Numba is missing OR we are in Nuitka)
        logger.warning(
            "AOT module missing and runtime JIT unavailable (IsCompiledBuild=%s, NumbaAvailable=%s). "
            "Falling back to slower NumPy implementation.",
            _IS_COMPILED, _JIT_AVAILABLE
        )

        from .numpy_executor import (
            apply_adjustments_buffer as _apply_adjustments_numpy,
            apply_color_adjustments_inplace_buffer as _apply_color_adjustments_numpy,
        )

        # Bridge the API
        _apply_adjustments_fast = _apply_adjustments_numpy
        _apply_color_adjustments_inplace = _apply_color_adjustments_numpy


def apply_adjustments_fast_qimage(
    image: QImage,
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
    apply_bw: bool,
    bw_intensity: float,
    bw_neutrals: float,
    bw_tone: float,
    bw_grain: float,
) -> None:
    """Mutate ``image`` in-place using the available adjustment kernel (AOT, JIT, or NumPy)."""

    view, buffer_guard = _resolve_pixel_buffer(image)
    buffer_handle = buffer_guard
    _ = buffer_handle

    if getattr(view, "readonly", False):
        raise BufferError("QImage pixel buffer is read-only")

    if width <= 0 or height <= 0:
        return

    expected_size = bytes_per_line * height
    buffer = np.frombuffer(view, dtype=np.uint8, count=expected_size)
    if buffer.size < expected_size:
        raise BufferError("QImage pixel buffer is smaller than expected")

    apply_color = abs(saturation) > 1e-6 or abs(vibrance) > 1e-6 or cast > 1e-6

    if _apply_adjustments_fast is None:
        logger.error("No image adjustment kernel available.")
        return

    _apply_adjustments_fast(
        buffer,
        width,
        height,
        bytes_per_line,
        exposure_term,
        brightness_term,
        brilliance_strength,
        highlights,
        shadows,
        contrast_factor,
        black_point,
        saturation,
        vibrance,
        cast,
        gain_r,
        gain_g,
        gain_b,
        apply_color,
        apply_bw,
        bw_intensity,
        bw_neutrals,
        bw_tone,
        bw_grain,
    )


def apply_color_adjustments_inplace_qimage(
    image: QImage,
    saturation: float,
    vibrance: float,
    cast: float,
    gain_r: float,
    gain_g: float,
    gain_b: float,
) -> None:
    """Apply only color adjustments to a QImage in-place."""
    if image.isNull():
        return

    apply_color = abs(saturation) > 1e-6 or abs(vibrance) > 1e-6 or cast > 1e-6
    if not apply_color:
        return

    view, guard = _resolve_pixel_buffer(image)
    buffer_handle = guard
    _ = buffer_handle

    if getattr(view, "readonly", False):
        raise BufferError("QImage pixel buffer is read-only")

    width = image.width()
    height = image.height()
    bytes_per_line = image.bytesPerLine()

    if width <= 0 or height <= 0:
        return

    expected_size = bytes_per_line * height
    buffer = np.frombuffer(view, dtype=np.uint8, count=expected_size)
    if buffer.size < expected_size:
        raise BufferError("QImage pixel buffer is smaller than expected")

    if _apply_color_adjustments_inplace is None:
        logger.error("No color adjustment kernel available.")
        return

    _apply_color_adjustments_inplace(
        buffer,
        width,
        height,
        bytes_per_line,
        saturation,
        vibrance,
        cast,
        gain_r,
        gain_g,
        gain_b,
    )

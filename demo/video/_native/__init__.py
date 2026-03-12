"""Python wrapper for the fast_thumb C extension.

Provides accelerated pixel processing functions with pure-Python
fallbacks when the shared library is not available.

Usage::

    from _native import split_strip_bgra, bgra_to_rgb
"""

from __future__ import annotations

import ctypes
import os
import platform
import subprocess
import sys

_lib = None


def _find_or_build_lib():
    """Locate or JIT-compile the fast_thumb shared library."""
    global _lib
    if _lib is not None:
        return _lib

    native_dir = os.path.dirname(os.path.abspath(__file__))

    # Determine platform-specific library name
    if sys.platform == 'win32':
        lib_name = 'fast_thumb.dll'
    elif sys.platform == 'darwin':
        lib_name = 'fast_thumb.dylib'
    else:
        lib_name = 'fast_thumb.so'

    lib_path = os.path.join(native_dir, lib_name)

    # JIT compile if missing
    if not os.path.isfile(lib_path):
        src_path = os.path.join(native_dir, 'fast_thumb.c')
        if not os.path.isfile(src_path):
            return None
        try:
            if sys.platform == 'win32':
                subprocess.run(
                    ['cl', '/O2', '/LD', src_path, f'/Fe:{lib_path}'],
                    check=True, capture_output=True,
                )
            else:
                subprocess.run(
                    ['gcc', '-O3', '-march=native', '-shared', '-fPIC',
                     '-o', lib_path, src_path],
                    check=True, capture_output=True,
                )
        except (FileNotFoundError, subprocess.CalledProcessError) as e:
            print(f"[fast_thumb] JIT compile failed: {e}")
            return None

    try:
        _lib = ctypes.CDLL(lib_path)
        # Configure function signatures
        _lib.split_strip_bgra.argtypes = [
            ctypes.c_char_p, ctypes.c_char_p,
            ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ]
        _lib.split_strip_bgra.restype = None

        _lib.bgra_to_rgb.argtypes = [
            ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int,
        ]
        _lib.bgra_to_rgb.restype = None
        return _lib
    except OSError as e:
        print(f"[fast_thumb] Load failed: {e}")
        return None


def split_strip_bgra(strip_buf: bytes, thumb_w: int, thumb_h: int,
                     count: int) -> list[bytes]:
    """Split a horizontal BGRA strip into individual frame buffers.

    Uses C extension when available, falls back to pure Python.
    """
    lib = _find_or_build_lib()
    frame_bytes = thumb_w * thumb_h * 4

    if lib is not None:
        out = ctypes.create_string_buffer(frame_bytes * count)
        lib.split_strip_bgra(strip_buf, out, thumb_w, thumb_h, count)
        raw = out.raw
        return [raw[i * frame_bytes:(i + 1) * frame_bytes] for i in range(count)]

    # Pure Python fallback
    row_bytes = thumb_w * count * 4
    frames: list[bytearray] = [bytearray(frame_bytes) for _ in range(count)]
    mv = memoryview(strip_buf)
    for y in range(thumb_h):
        row_start = y * row_bytes
        for i in range(count):
            src_off = row_start + i * thumb_w * 4
            dst_off = y * thumb_w * 4
            frames[i][dst_off:dst_off + thumb_w * 4] = \
                mv[src_off:src_off + thumb_w * 4]
    return [bytes(f) for f in frames]


def bgra_to_rgb(bgra_buf: bytes, n_pixels: int) -> bytes:
    """Convert BGRA buffer to RGB888.

    Uses C extension when available, falls back to pure Python.
    """
    lib = _find_or_build_lib()

    if lib is not None:
        out = ctypes.create_string_buffer(n_pixels * 3)
        lib.bgra_to_rgb(bgra_buf, out, n_pixels)
        return out.raw

    # Pure Python fallback
    rgb = bytearray(n_pixels * 3)
    mv = memoryview(bgra_buf)
    for i in range(n_pixels):
        s = i * 4
        d = i * 3
        rgb[d] = mv[s + 2]
        rgb[d + 1] = mv[s + 1]
        rgb[d + 2] = mv[s]
    return bytes(rgb)

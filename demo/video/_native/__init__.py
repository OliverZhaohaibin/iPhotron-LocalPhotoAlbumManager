"""Python wrapper for the fast_thumb C extension.

Provides accelerated pixel processing functions with pure-Python
fallbacks when the shared library is not available.

Exported functions::

    split_strip_bgra        Split BGRA tile strip → N BGRA frames
    bgra_to_rgb             Convert BGRA → RGB888 (single buffer)
    split_strip_bgra_to_rgb Split + convert in one pass (no intermediates)
    bgra_to_rgb_multi       Batch convert N concatenated BGRA → RGB
    rotate_bgra             Rotate BGRA frame (90/180/270°, optional vflip)
    snap_to_keyframes       Binary-search snap targets → keyframes
    scale_bilinear_bgra     Fast bilinear downscale of BGRA frame
"""

from __future__ import annotations

import ctypes
import os
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
                # Try with -march=native first (optimal for the local CPU).
                # Fall back to -O2 without -march=native for broader compat.
                try:
                    subprocess.run(
                        ['gcc', '-O3', '-march=native', '-shared', '-fPIC',
                         '-o', lib_path, src_path, '-lm'],
                        check=True, capture_output=True,
                    )
                except subprocess.CalledProcessError:
                    subprocess.run(
                        ['gcc', '-O2', '-shared', '-fPIC',
                         '-o', lib_path, src_path, '-lm'],
                        check=True, capture_output=True,
                    )
        except (FileNotFoundError, subprocess.CalledProcessError) as e:
            print(f"[fast_thumb] JIT compile failed: {e}")
            return None

    try:
        _lib = ctypes.CDLL(lib_path)

        # -- existing functions --
        _lib.split_strip_bgra.argtypes = [
            ctypes.c_char_p, ctypes.c_char_p,
            ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ]
        _lib.split_strip_bgra.restype = None

        _lib.bgra_to_rgb.argtypes = [
            ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int,
        ]
        _lib.bgra_to_rgb.restype = None

        # -- new combined split+convert --
        _lib.split_strip_bgra_to_rgb.argtypes = [
            ctypes.c_char_p, ctypes.c_char_p,
            ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ]
        _lib.split_strip_bgra_to_rgb.restype = None

        # -- batch BGRA→RGB --
        _lib.bgra_to_rgb_multi.argtypes = [
            ctypes.c_char_p, ctypes.c_char_p,
            ctypes.c_int, ctypes.c_int,
        ]
        _lib.bgra_to_rgb_multi.restype = None

        # -- rotate BGRA --
        _lib.rotate_bgra.argtypes = [
            ctypes.c_char_p, ctypes.c_char_p,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ]
        _lib.rotate_bgra.restype = None

        # -- snap to keyframes --
        _lib.snap_to_keyframes.argtypes = [
            ctypes.POINTER(ctypes.c_double), ctypes.c_int,
            ctypes.POINTER(ctypes.c_double), ctypes.c_int,
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_double),
        ]
        _lib.snap_to_keyframes.restype = None

        # -- bilinear scale --
        _lib.scale_bilinear_bgra.argtypes = [
            ctypes.c_char_p, ctypes.c_int, ctypes.c_int,
            ctypes.c_char_p, ctypes.c_int, ctypes.c_int,
        ]
        _lib.scale_bilinear_bgra.restype = None

        return _lib
    except OSError as e:
        print(f"[fast_thumb] Load failed: {e}")
        return None


# =====================================================================
# split_strip_bgra — Split BGRA strip → N BGRA frames
# =====================================================================

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


# =====================================================================
# bgra_to_rgb — BGRA → RGB888 (single buffer)
# =====================================================================

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


# =====================================================================
# split_strip_bgra_to_rgb — Combined split + convert in one pass
# =====================================================================

def split_strip_bgra_to_rgb(strip_buf: bytes, thumb_w: int, thumb_h: int,
                            count: int) -> bytes:
    """Split a BGRA strip and convert to concatenated RGB888 in one pass.

    Returns a single ``bytes`` of size ``thumb_w * thumb_h * 3 * count``
    containing *count* RGB frames laid out sequentially (frame 0 first,
    frame 1 next, etc.).

    This eliminates intermediate BGRA frame allocations and N separate
    ``bgra_to_rgb`` ctypes calls — a single C call does all the work.
    """
    lib = _find_or_build_lib()
    rgb_total = thumb_w * thumb_h * 3 * count

    if lib is not None:
        out = ctypes.create_string_buffer(rgb_total)
        lib.split_strip_bgra_to_rgb(strip_buf, out, thumb_w, thumb_h, count)
        return out.raw

    # Pure Python fallback: split then convert row-by-row
    strip_row_bytes = thumb_w * count * 4
    rgb_frame_bytes = thumb_w * thumb_h * 3
    rgb = bytearray(rgb_total)
    mv = memoryview(strip_buf)
    for y in range(thumb_h):
        row_start = y * strip_row_bytes
        for i in range(count):
            src_off = row_start + i * thumb_w * 4
            dst_off = i * rgb_frame_bytes + y * thumb_w * 3
            for x in range(thumb_w):
                s = src_off + x * 4
                d = dst_off + x * 3
                rgb[d] = mv[s + 2]
                rgb[d + 1] = mv[s + 1]
                rgb[d + 2] = mv[s]
    return bytes(rgb)


# =====================================================================
# bgra_to_rgb_multi — Batch convert N concatenated BGRA frames → RGB
# =====================================================================

def bgra_to_rgb_multi(bgra_buf: bytes, pixels_per_frame: int,
                      n_frames: int) -> bytes:
    """Batch convert N concatenated BGRA frames to RGB888.

    Equivalent to calling ``bgra_to_rgb`` *n_frames* times but performs
    only one ctypes call, eliminating per-frame overhead.
    """
    lib = _find_or_build_lib()
    total_pixels = pixels_per_frame * n_frames

    if lib is not None:
        out = ctypes.create_string_buffer(total_pixels * 3)
        lib.bgra_to_rgb_multi(bgra_buf, out, pixels_per_frame, n_frames)
        return out.raw

    # Pure Python fallback — same as bgra_to_rgb but for all pixels
    rgb = bytearray(total_pixels * 3)
    mv = memoryview(bgra_buf)
    for i in range(total_pixels):
        s = i * 4
        d = i * 3
        rgb[d] = mv[s + 2]
        rgb[d + 1] = mv[s + 1]
        rgb[d + 2] = mv[s]
    return bytes(rgb)


# =====================================================================
# rotate_bgra — Rotate BGRA frame (90/180/270°, optional vflip)
# =====================================================================

def rotate_bgra(bgra_buf: bytes, src_w: int, src_h: int,
                degrees: int = 0, vflip: bool = False) -> bytes:
    """Rotate a BGRA frame by 0/90/180/270 degrees, with optional vflip.

    Returns the rotated BGRA buffer.  Dimensions swap for 90°/270°.
    """
    lib = _find_or_build_lib()

    if degrees in (90, 270):
        dst_w, dst_h = src_h, src_w
    else:
        dst_w, dst_h = src_w, src_h

    dst_size = dst_w * dst_h * 4

    if lib is not None:
        out = ctypes.create_string_buffer(dst_size)
        lib.rotate_bgra(bgra_buf, out, src_w, src_h,
                        degrees, 1 if vflip else 0)
        return out.raw

    # Pure Python fallback
    dst = bytearray(dst_size)
    mv = memoryview(bgra_buf)
    for sy in range(src_h):
        for sx in range(src_w):
            if degrees == 90:
                dx, dy = src_h - 1 - sy, sx
            elif degrees == 180:
                dx, dy = src_w - 1 - sx, src_h - 1 - sy
            elif degrees == 270:
                dx, dy = sy, src_w - 1 - sx
            else:
                dx, dy = sx, sy
            if vflip:
                dy = dst_h - 1 - dy
            s = (sy * src_w + sx) * 4
            d = (dy * dst_w + dx) * 4
            dst[d:d + 4] = mv[s:s + 4]
    return bytes(dst)


# =====================================================================
# snap_to_keyframes — Binary-search snap targets → keyframes
# =====================================================================

def snap_to_keyframes(target_times: list[float],
                      keyframes: list[float]) -> list[tuple[int, float]]:
    """Map each target time to the nearest keyframe timestamp.

    Uses C binary search when available, falls back to Python bisect.
    Returns list of ``(original_index, snapped_time)`` pairs.
    """
    if not keyframes:
        return list(enumerate(target_times))

    lib = _find_or_build_lib()

    if lib is not None:
        n_t = len(target_times)
        n_k = len(keyframes)
        c_targets = (ctypes.c_double * n_t)(*target_times)
        c_kf = (ctypes.c_double * n_k)(*keyframes)
        c_indices = (ctypes.c_int * n_t)()
        c_times = (ctypes.c_double * n_t)()
        lib.snap_to_keyframes(c_targets, n_t, c_kf, n_k, c_indices, c_times)
        return [(c_indices[i], c_times[i]) for i in range(n_t)]

    # Pure Python fallback
    import bisect
    snapped = []
    for i, t in enumerate(target_times):
        pos = bisect.bisect_left(keyframes, t)
        candidates = []
        if pos < len(keyframes):
            candidates.append(keyframes[pos])
        if pos > 0:
            candidates.append(keyframes[pos - 1])
        best = min(candidates, key=lambda k: abs(k - t))
        snapped.append((i, best))
    return snapped


# =====================================================================
# scale_bilinear_bgra — Fast bilinear downscale of BGRA frame
# =====================================================================

def scale_bilinear_bgra(bgra_buf: bytes, src_w: int, src_h: int,
                        dst_w: int, dst_h: int) -> bytes:
    """Bilinear downscale a BGRA frame to *dst_w* × *dst_h*.

    Uses C extension when available.  Pure-Python fallback is provided
    but will be slow for large frames.
    """
    lib = _find_or_build_lib()
    dst_size = dst_w * dst_h * 4

    if lib is not None:
        out = ctypes.create_string_buffer(dst_size)
        lib.scale_bilinear_bgra(bgra_buf, src_w, src_h, out, dst_w, dst_h)
        return out.raw

    # Pure Python fallback (bilinear)
    dst = bytearray(dst_size)
    mv = memoryview(bgra_buf)
    x_ratio = (src_w - 1) / max(dst_w - 1, 1)
    y_ratio = (src_h - 1) / max(dst_h - 1, 1)
    for dy in range(dst_h):
        gy = dy * y_ratio
        sy = int(gy)
        fy = gy - sy
        if sy >= src_h - 1:
            sy = src_h - 2
            fy = 1.0
        for dx in range(dst_w):
            gx = dx * x_ratio
            sx = int(gx)
            fx = gx - sx
            if sx >= src_w - 1:
                sx = src_w - 2
                fx = 1.0
            for c in range(4):
                p00 = mv[(sy * src_w + sx) * 4 + c]
                p10 = mv[(sy * src_w + sx + 1) * 4 + c]
                p01 = mv[((sy + 1) * src_w + sx) * 4 + c]
                p11 = mv[((sy + 1) * src_w + sx + 1) * 4 + c]
                v = (p00 * (1 - fx) * (1 - fy) + p10 * fx * (1 - fy)
                     + p01 * (1 - fx) * fy + p11 * fx * fy)
                dst[(dy * dst_w + dx) * 4 + c] = max(0, min(255, int(v + 0.5)))
    return bytes(dst)

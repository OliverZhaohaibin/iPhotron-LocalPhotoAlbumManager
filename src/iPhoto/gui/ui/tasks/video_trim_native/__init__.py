"""Native helpers reused from the video timeline demo.

The shared library is optional. When compilation or loading fails, we fall back
to pure-Python implementations so the trim bar still works.
"""

from __future__ import annotations

import bisect
import ctypes
import os
import subprocess
import sys
from pathlib import Path

_LIB = None


def _native_dir() -> Path:
    return Path(__file__).resolve().parent


def _library_path() -> Path:
    if sys.platform == "win32":
        name = "fast_thumb.dll"
    elif sys.platform == "darwin":
        name = "fast_thumb.dylib"
    else:
        name = "fast_thumb.so"
    return _native_dir() / name


def _source_path() -> Path:
    return _native_dir() / "fast_thumb.c"


def _build_library() -> bool:
    """Ensure the native helper library is available.

    If a prebuilt shared library already exists next to this module, it is used
    unconditionally. Compilation is only attempted when the environment
    variable ``IPHOTO_BUILD_NATIVE_THUMB`` is set to ``1``. This avoids
    unexpected toolchain invocations on end-user systems, in sandboxes, or
    during normal app startup while still allowing packaged native builds to
    load normally.
    """
    lib_path = _library_path()
    if lib_path.exists():
        return True

    if os.environ.get("IPHOTO_BUILD_NATIVE_THUMB") != "1":
        return False

    src_path = _source_path()
    if not src_path.exists():
        return False

    try:
        if sys.platform == "win32":
            commands = [
                ["cl", "/O2", "/LD", str(src_path), f"/Fe:{lib_path}"],
                ["gcc", "-O3", "-shared", "-o", str(lib_path), str(src_path), "-lm"],
            ]
        else:
            commands = [
                [
                    "gcc", "-O3", "-march=native", "-shared", "-fPIC",
                    "-o", str(lib_path), str(src_path), "-lm",
                ],
                [
                    "gcc", "-O2", "-shared", "-fPIC",
                    "-o", str(lib_path), str(src_path), "-lm",
                ],
            ]

        for command in commands:
            try:
                subprocess.run(command, check=True, capture_output=True)
                return lib_path.exists()
            except (FileNotFoundError, subprocess.CalledProcessError):
                continue
    except OSError:
        return False
    return False


def _load_library():
    global _LIB
    if _LIB is not None:
        return _LIB
    if not _build_library():
        return None

    try:
        lib = ctypes.CDLL(str(_library_path()))
        lib.split_strip_bgra.argtypes = [
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
        ]
        lib.split_strip_bgra.restype = None
        lib.split_strip_bgra_to_rgb.argtypes = [
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
        ]
        lib.split_strip_bgra_to_rgb.restype = None
        lib.snap_to_keyframes.argtypes = [
            ctypes.POINTER(ctypes.c_double),
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_double),
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_double),
        ]
        lib.snap_to_keyframes.restype = None
        _LIB = lib
        return _LIB
    except OSError:
        return None


def split_strip_bgra(strip_buf: bytes, thumb_w: int, thumb_h: int, count: int) -> list[bytes]:
    """Split a horizontal BGRA strip into individual frame buffers."""

    lib = _load_library()
    frame_bytes = thumb_w * thumb_h * 4

    if lib is not None:
        out = ctypes.create_string_buffer(frame_bytes * count)
        lib.split_strip_bgra(strip_buf, out, thumb_w, thumb_h, count)
        raw = out.raw
        return [raw[index * frame_bytes:(index + 1) * frame_bytes] for index in range(count)]

    row_bytes = thumb_w * count * 4
    frames = [bytearray(frame_bytes) for _ in range(count)]
    source = memoryview(strip_buf)
    for y in range(thumb_h):
        row_start = y * row_bytes
        for index in range(count):
            src_off = row_start + index * thumb_w * 4
            dst_off = y * thumb_w * 4
            frames[index][dst_off:dst_off + thumb_w * 4] = source[src_off:src_off + thumb_w * 4]
    return [bytes(frame) for frame in frames]


def split_strip_bgra_to_rgb(strip_buf: bytes, thumb_w: int, thumb_h: int, count: int) -> bytes:
    """Split a BGRA strip and convert it into concatenated RGB frames."""

    lib = _load_library()
    total = thumb_w * thumb_h * 3 * count
    if lib is not None:
        out = ctypes.create_string_buffer(total)
        lib.split_strip_bgra_to_rgb(strip_buf, out, thumb_w, thumb_h, count)
        return out.raw

    rgb_frame_bytes = thumb_w * thumb_h * 3
    strip_row_bytes = thumb_w * count * 4
    rgb = bytearray(total)
    source = memoryview(strip_buf)
    for y in range(thumb_h):
        row_start = y * strip_row_bytes
        for index in range(count):
            src_off = row_start + index * thumb_w * 4
            dst_off = index * rgb_frame_bytes + y * thumb_w * 3
            for x in range(thumb_w):
                src = src_off + x * 4
                dst = dst_off + x * 3
                rgb[dst] = source[src + 2]
                rgb[dst + 1] = source[src + 1]
                rgb[dst + 2] = source[src]
    return bytes(rgb)


def snap_to_keyframes(target_times: list[float], keyframes: list[float]) -> list[tuple[int, float]]:
    """Map each target time to the nearest keyframe."""

    if not keyframes:
        return list(enumerate(target_times))

    lib = _load_library()
    if lib is not None:
        n_targets = len(target_times)
        n_keyframes = len(keyframes)
        c_targets = (ctypes.c_double * n_targets)(*target_times)
        c_keyframes = (ctypes.c_double * n_keyframes)(*keyframes)
        out_indices = (ctypes.c_int * n_targets)()
        out_times = (ctypes.c_double * n_targets)()
        lib.snap_to_keyframes(c_targets, n_targets, c_keyframes, n_keyframes, out_indices, out_times)
        return [(out_indices[index], out_times[index]) for index in range(n_targets)]

    snapped: list[tuple[int, float]] = []
    for index, target in enumerate(target_times):
        pos = bisect.bisect_left(keyframes, target)
        candidates: list[float] = []
        if pos < len(keyframes):
            candidates.append(keyframes[pos])
        if pos > 0:
            candidates.append(keyframes[pos - 1])
        snapped.append((index, min(candidates, key=lambda item: abs(item - target))))
    return snapped


__all__ = [
    "snap_to_keyframes",
    "split_strip_bgra",
    "split_strip_bgra_to_rgb",
]

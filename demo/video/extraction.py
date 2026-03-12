"""Frame extraction strategies — optimised for sub-1 s thumbnail generation.

Strategy priority (fastest first):
  0. **Contact-sheet** (ffmpeg ``tile=Nx1``): one process, one output image,
     one BGRA buffer.  Eliminates N signal/slot + N QImage + N scaledToHeight
     + N repaint costs.
  1. **PyAV sequential forward-decode**: single container, ``thread_count=2``,
     keyframe-only skip via ``codec_context.skip_frame``, sequential
     forward seeks with deduplication.  Zero subprocess overhead.
  2. Single-pass ffmpeg pipe (GPU + keyframe-only)
  3. Single-pass ffmpeg pipe (keyframe-only, no GPU)
  4. Single-pass ffmpeg pipe (full decode)
  5. Sliced multi-process extraction
  6. Parallel individual extraction (slowest fallback)
"""

from __future__ import annotations

import bisect
import concurrent.futures
import os
import subprocess


try:
    import av as _av_module
    HAS_PYAV = True
except ImportError:
    _av_module = None
    HAS_PYAV = False

try:
    from PIL import Image as _PILImage
except ImportError:
    _PILImage = None

from config import PYAV_MAX_WORKERS, MAX_FFMPEG_SLICES, FRAME_READ_BUFFER
from hwaccel import _detect_hwaccel, _build_hwaccel_output_format

# Try to import C-accelerated helpers; fall back to pure-Python
try:
    from _native import split_strip_bgra as _c_split_strip_bgra
except ImportError:
    _c_split_strip_bgra = None

try:
    from _native import split_strip_bgra_to_rgb as _c_split_strip_bgra_to_rgb
except ImportError:
    _c_split_strip_bgra_to_rgb = None

try:
    from _native import snap_to_keyframes as _c_snap_to_keyframes
except ImportError:
    _c_snap_to_keyframes = None

try:
    from _native import rotate_bgra as _c_rotate_bgra
except ImportError:
    _c_rotate_bgra = None

try:
    from _native import scale_bilinear_bgra as _c_scale_bilinear_bgra
except ImportError:
    _c_scale_bilinear_bgra = None


# =====================================================================
# Contact-sheet strategy (ffmpeg tile=Nx1) — Strategy 0
# =====================================================================

def _build_contact_sheet_cmd(video_path, thumb_w, thumb_h, num_frames,
                             fps_rate, keyframe_only=True):
    """Build an ffmpeg command that produces a single horizontal strip image.

    The ``tile=Nx1`` filter assembles all frames into one row.  Combined
    with ``-frames:v 1`` this outputs exactly one BGRA image of size
    ``(thumb_w * num_frames) x thumb_h``.  One process, one seek, one
    output buffer.

    Uses ``-hwaccel auto`` for transparent GPU decode and ``-skip_frame
    nokey`` for keyframe-only decoding (~100× fewer frames for H.264/H.265).
    """
    cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error',
           '-nostdin',
           '-probesize', '32768', '-analyzeduration', '0',
           '-fflags', '+nobuffer',
           '-hwaccel', 'auto']

    if keyframe_only:
        cmd.extend(['-skip_frame', 'nokey'])

    cmd.extend(['-i', video_path])

    # Filter chain: fps → scale → tile → format
    # -skip_frame nokey already limits the decoder to keyframes so there
    # is no need for an extra ``select='eq(pict_type,I)'`` filter.
    vf_parts = [
        f"fps={fps_rate:.6f}",
        f"scale={thumb_w}:{thumb_h}",
        f"tile={num_frames}x1",
        "format=bgra",
    ]
    vf = ",".join(vf_parts)
    cmd.extend(['-vf', vf, '-frames:v', '1',
                '-an', '-f', 'rawvideo', '-vsync', 'vfr',
                'pipe:1'])
    return cmd


def _run_contact_sheet(video_path, thumb_w, thumb_h, num_frames,
                       fps_rate, keyframe_only=True):
    """Execute a contact-sheet command and return the raw strip buffer.

    Returns ``(strip_w, strip_h, bytes)`` on success, or *None*.
    ``strip_w == thumb_w * num_frames``, ``strip_h == thumb_h``.
    """
    cmd = _build_contact_sheet_cmd(
        video_path, thumb_w, thumb_h, num_frames, fps_rate,
        keyframe_only=keyframe_only,
    )
    strip_w = thumb_w * num_frames
    expected_size = strip_w * thumb_h * 4

    try:
        startupinfo, popen_kwargs = _build_popen_priority_kwargs()
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            startupinfo=startupinfo,
            **popen_kwargs,
        )
        if proc.returncode != 0:
            stderr_msg = proc.stderr[:300] if proc.stderr else b''
            if isinstance(stderr_msg, bytes):
                stderr_msg = stderr_msg.decode('utf-8', errors='replace')
            print(f"[contact-sheet] exit={proc.returncode}: {stderr_msg.strip()}")
            return None

        data = proc.stdout
        if len(data) < expected_size:
            # Tile may produce fewer frames if not enough keyframes;
            # accept any complete row as long as it's at least one frame
            if len(data) >= thumb_w * thumb_h * 4:
                actual_frames = len(data) // (thumb_w * thumb_h * 4)
                actual_w = thumb_w * actual_frames
                actual_size = actual_w * thumb_h * 4
                if len(data) >= actual_size:
                    return actual_w, thumb_h, data[:actual_size]
            print(f"[contact-sheet] Short output: got {len(data)}, "
                  f"expected {expected_size}")
            return None

        return strip_w, thumb_h, data[:expected_size]

    except Exception as e:
        print(f"[contact-sheet] Error: {e}")
        return None


# =====================================================================
# PyAV extraction — Strategy 1 (keyframe-aware, zero subprocess)
# =====================================================================

def _get_keyframe_timestamps_pyav(video_path):
    """Extract all keyframe timestamps using PyAV packet-level demux.

    Iterates over demuxed packets (no decoding!) to read PTS and keyframe
    flag.  This is orders of magnitude faster than decoding frames —
    typically <100 ms even for long 4K videos, vs 15+ s when decoding
    every I-frame at full resolution.

    Returns a sorted list of float timestamps in seconds.
    """
    keyframes = []
    container = None
    try:
        container = _av_module.open(video_path)
        stream = container.streams.video[0]
        time_base = stream.time_base
        for packet in container.demux(stream):
            if packet.pts is None:
                continue  # flush packet at end of stream
            if packet.is_keyframe:
                t = float(packet.pts * time_base)
                keyframes.append(t)
    except Exception as e:
        print(f"[pyav-keyframes] Error: {e}")
    finally:
        if container:
            try:
                container.close()
            except Exception:
                pass
    if not keyframes:
        return keyframes
    return sorted(set(keyframes))


def _snap_to_keyframes(target_times, keyframes):
    """Map each target time to the nearest keyframe timestamp.

    If no keyframes are available, returns the original target_times.
    Uses C binary search when available for maximum speed.

    Args:
        target_times: List of desired float timestamps.
        keyframes: Sorted list of keyframe float timestamps.

    Returns:
        List of (original_index, snapped_timestamp) pairs.
    """
    if not keyframes:
        return list(enumerate(target_times))

    if _c_snap_to_keyframes is not None:
        return _c_snap_to_keyframes(target_times, keyframes)

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


def _pyav_extract_segment(video_path, indices, thumb_w, thumb_h,
                          rotation=0, vflip=False):
    """
    Extract a subset of frames using individual seeks.

    For sparse sampling (e.g. 38 thumbnails from 195 keyframes), seeking
    directly to each target is faster than continuous decode through all
    intervening keyframes:
    - Seek + decode 1 frame: ~100 ms per thumbnail
    - Continuous decode of all keyframes: ~80 ms × (all keyframes in range)

    Each worker limits ``thread_count = 2`` to avoid CPU contention when
    multiple workers run in parallel.

    Args:
        video_path: Path to video file.
        indices: List of (global_index, target_time) pairs for this segment.
        thumb_w: Target thumbnail display width (post-rotation).
        thumb_h: Target thumbnail display height (post-rotation).
        rotation: Rotation in degrees (0, 90, 180, 270).
        vflip: Whether to apply vertical flip.

    Returns:
        List of (global_index, width, height, rgb_bytes) tuples.
    """
    if not indices:
        return []

    results = []
    container = None
    try:
        container = _av_module.open(video_path)
        stream = container.streams.video[0]
        stream.thread_type = 'AUTO'
        # Limit threads per worker to avoid contention across workers
        stream.codec_context.thread_count = 2
        time_base = stream.time_base

        # PyAV gives raw (unrotated) frames → extract at raw dimensions
        if rotation in (90, 270):
            raw_w, raw_h = thumb_h, thumb_w
        else:
            raw_w, raw_h = thumb_w, thumb_h

        for global_idx, target_time in indices:
            # Seek directly to target timestamp (finds nearest prior keyframe)
            target_pts = int(target_time / float(time_base))
            container.seek(max(0, target_pts), stream=stream)

            for frame in container.decode(stream):
                img = frame.to_image(
                    width=raw_w, height=raw_h,
                    interpolation='FAST_BILINEAR',
                )

                # Apply orientation transforms (PyAV does NOT auto-rotate)
                if rotation == 90:
                    img = img.transpose(_PILImage.Transpose.ROTATE_270)
                elif rotation == 180:
                    img = img.transpose(_PILImage.Transpose.ROTATE_180)
                elif rotation == 270:
                    img = img.transpose(_PILImage.Transpose.ROTATE_90)
                if vflip:
                    img = img.transpose(_PILImage.Transpose.FLIP_TOP_BOTTOM)

                rgb_data = img.tobytes("raw", "RGB")
                results.append((
                    global_idx, thumb_w, thumb_h, rgb_data,
                ))
                break  # Only need first frame after seek

    except Exception as e:
        print(f"[pyav-segment] Error: {e}")
    finally:
        if container:
            try:
                container.close()
            except Exception:
                pass
    return results


def _extract_thumbnails_pyav(video_path, num_frames, thumb_w, thumb_h,
                             callback=None, rotation=0, vflip=False):
    """
    Extract thumbnails using keyframe-aware multi-threaded PyAV.

    Pipeline:
    1. Optionally pre-scan keyframe timestamps to get a rough index.
    2. Compute uniform target times and map them to seek positions.
    3. Split target indices across worker threads; each thumbnail is
       obtained via a dedicated seek/decode operation.

    This improves performance over naive linear decode of every frame by:
    - Using keyframe information to choose efficient seek positions.
    - Parallelising independent seeks/decodes across multiple threads.

    Args:
        video_path: Path to video file.
        num_frames: Number of thumbnails to extract.
        thumb_w: Target thumbnail display width (post-rotation).
        thumb_h: Target thumbnail display height (post-rotation).
        callback: Optional callable(index, rgb_bytes, w, h) called for
                  each frame as it's extracted (progressive display).
        rotation: Rotation in degrees (0, 90, 180, 270) for PyAV frames.
        vflip: Whether to apply vertical flip.

    Returns:
        List of (width, height, bytes) tuples in RGB888 format, or empty
        list on failure.
    """
    try:
        container = _av_module.open(video_path)
        stream = container.streams.video[0]

        duration = 0.0
        if stream.duration and stream.time_base:
            duration = float(stream.duration * stream.time_base)
        if duration <= 0 and container.duration:
            duration = container.duration / _av_module.time_base
        container.close()

        if duration <= 0:
            return []

        step = duration / num_frames
        target_times = [i * step for i in range(num_frames)]

        # --- Keyframe-aware sampling ---
        keyframes = _get_keyframe_timestamps_pyav(video_path)
        if keyframes:
            all_indices = _snap_to_keyframes(target_times, keyframes)
            print(f"[pyav] Snapped {num_frames} targets to "
                  f"{len(keyframes)} keyframes")
        else:
            all_indices = list(enumerate(target_times))

        # Determine number of worker threads
        num_workers = min(PYAV_MAX_WORKERS,
                          max(1, (os.cpu_count() or 4) // 2))
        num_workers = min(num_workers, num_frames)

        if num_workers <= 1:
            segment_results = _pyav_extract_segment(
                video_path, all_indices, thumb_w, thumb_h,
                rotation=rotation, vflip=vflip,
            )
            all_results = segment_results
        else:
            # Split into contiguous time-sorted segments for efficient
            # continuous decode within each worker thread.
            sorted_all = sorted(all_indices, key=lambda x: x[1])
            per_worker = max(1, len(sorted_all) // num_workers)
            segments = []
            for w in range(num_workers):
                start = w * per_worker
                if w == num_workers - 1:
                    seg = sorted_all[start:]
                else:
                    seg = sorted_all[start:start + per_worker]
                if seg:
                    segments.append(seg)

            with concurrent.futures.ThreadPoolExecutor(
                max_workers=len(segments),
            ) as pool:
                futures = [
                    pool.submit(
                        _pyav_extract_segment,
                        video_path, seg, thumb_w, thumb_h,
                        rotation, vflip,
                    )
                    for seg in segments
                ]
                all_results = []
                for f in concurrent.futures.as_completed(futures):
                    try:
                        all_results.extend(f.result())
                    except Exception as e:
                        print(f"[pyav-mt] Segment error: {e}")

        # Sort by global index to restore frame order
        all_results.sort(key=lambda x: x[0])

        thumbnails = []
        for global_idx, w, h, rgb_data in all_results:
            thumbnails.append((w, h, rgb_data))
            if callback:
                callback(global_idx, rgb_data, w, h)

        return thumbnails

    except Exception as e:
        print(f"[pyav] Extraction error: {e}")
        return []


# =====================================================================
# C-accelerated pixel helpers (ctypes, with pure-Python fallback)
# =====================================================================

def _split_strip_bgra(strip_buf: bytes, thumb_w: int, thumb_h: int,
                      count: int) -> list[bytes]:
    """Split a horizontal BGRA strip into *count* individual frame buffers.

    Uses the C extension when available for ~50× speedup over pure Python.
    Falls back to memoryview slicing otherwise.
    """
    if _c_split_strip_bgra is not None:
        return _c_split_strip_bgra(strip_buf, thumb_w, thumb_h, count)

    # Pure-Python fallback using memoryview slicing
    frame_bytes = thumb_w * thumb_h * 4
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


def _split_strip_bgra_to_rgb(strip_buf: bytes, thumb_w: int, thumb_h: int,
                              count: int) -> bytes:
    """Split a BGRA strip and convert to concatenated RGB888 in one pass.

    Returns a single ``bytes`` of size ``thumb_w * thumb_h * 3 * count``
    containing *count* RGB frames laid out sequentially.  A single C call
    replaces N separate split + convert operations — the hot path for
    caching after contact-sheet extraction.
    """
    if _c_split_strip_bgra_to_rgb is not None:
        return _c_split_strip_bgra_to_rgb(strip_buf, thumb_w, thumb_h, count)

    # Pure-Python fallback: split then convert row-by-row
    strip_row_bytes = thumb_w * count * 4
    rgb_frame_bytes = thumb_w * thumb_h * 3
    rgb_total = rgb_frame_bytes * count
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
# Pipe-based frame extraction (GPU-accelerated or software fallback)
# =====================================================================

def _build_popen_priority_kwargs():
    """Build OS-specific kwargs to lower the priority of ffmpeg child processes.

    Returns ``(startupinfo, popen_kwargs)``.  On POSIX, priority is lowered
    *after* process creation via :func:`_lower_process_priority` instead of
    ``preexec_fn`` (which is not safe in multi-threaded processes such as
    ``QThread``).
    """
    popen_kwargs = {}
    startupinfo = None
    if os.name == 'nt':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        # BELOW_NORMAL_PRIORITY_CLASS on Windows
        popen_kwargs['creationflags'] = 0x00004000
    return startupinfo, popen_kwargs


def _lower_process_priority(proc):
    """Best-effort lowering of a child process's scheduling priority (POSIX).

    Called immediately after ``Popen`` to replace the former ``preexec_fn``
    approach, which is not safe in multi-threaded programs.
    """
    if os.name != 'nt' and proc and proc.pid:
        try:
            os.setpriority(os.PRIO_PROCESS, proc.pid, 10)
        except (OSError, PermissionError):
            pass


def _extract_frame_pipe(video_path, timestamp, thumb_w, thumb_h):
    """
    Extract a single frame as raw BGRA pixels via pipe.

    Fallback order:
      1. Specific GPU decode + GPU scale (d3d11va/cuda/videotoolbox/vaapi)
      2. Auto GPU decode + CPU scale (-hwaccel auto)
      3. Software decode + CPU scale

    Returns (width, height, bytes) on success, or None on failure.
    """
    hw = _detect_hwaccel()

    # --- Attempt 1: Specific GPU decode + GPU/CPU scale ---
    if hw['hwaccel'] is not None:
        result = _try_extract_pipe_hwaccel(
            video_path, timestamp, thumb_w, thumb_h, hw,
        )
        if result is not None:
            return result

    # --- Attempt 2: Auto GPU decode + CPU scale ---
    result = _try_extract_pipe_auto(video_path, timestamp, thumb_w, thumb_h)
    if result is not None:
        return result

    # --- Attempt 3: Software decode + pipe ---
    result = _try_extract_pipe_sw(video_path, timestamp, thumb_w, thumb_h)
    if result is not None:
        return result

    return None


def _try_extract_pipe_hwaccel(video_path, timestamp, thumb_w, thumb_h, hw):
    """
    GPU-accelerated single-frame extraction via rawvideo pipe.

    The pipeline:
      ffmpeg -hwaccel <X> -hwaccel_output_format <X>
             -ss <T> -i <video>
             -frames:v 1
             -vf "<gpu_scale>=<W>:<H>,hwdownload,format=bgra"
             -f rawvideo pipe:1

    Note: hwdownload outputs the native GPU pixel format (e.g. nv12 for
    CUDA). The format=bgra filter converts it after download. These must
    be separate filters — hwdownload cannot output bgra directly.
    """
    hwaccel = hw['hwaccel']
    hw_out_fmt = _build_hwaccel_output_format(hwaccel)
    scale_filter = hw['scale_filter']
    download = hw['download_filter']

    # Build the -vf filter chain
    if scale_filter.startswith('scale_') and download:
        # GPU scale + hwdownload (native fmt) + convert to bgra
        vf = f"{scale_filter}={thumb_w}:{thumb_h},{download},format=bgra"
    elif download:
        # hwdownload (native fmt) + CPU scale + convert to bgra
        vf = f"{download},scale={thumb_w}:{thumb_h},format=bgra"
    else:
        vf = f"scale={thumb_w}:{thumb_h},format=bgra"

    cmd = [
        'ffmpeg', '-hide_banner', '-loglevel', 'error',
        '-nostdin',
        '-probesize', '32768', '-analyzeduration', '0',
        '-fflags', '+nobuffer',
        '-hwaccel', hwaccel,
        '-hwaccel_output_format', hw_out_fmt,
        '-ss', f'{timestamp:.4f}',
        '-i', video_path,
        '-frames:v', '1',
        '-vf', vf,
        '-f', 'rawvideo',
        'pipe:1',
    ]

    return _run_pipe_cmd(cmd, thumb_w, thumb_h)


def _try_extract_pipe_sw(video_path, timestamp, thumb_w, thumb_h):
    """
    Software-only single-frame extraction via rawvideo pipe.
    No temp files — pixels are piped directly to Python.
    """
    cmd = [
        'ffmpeg', '-hide_banner', '-loglevel', 'error',
        '-nostdin',
        '-probesize', '32768', '-analyzeduration', '0',
        '-fflags', '+nobuffer',
        '-ss', f'{timestamp:.4f}',
        '-i', video_path,
        '-frames:v', '1',
        '-vf', f'scale={thumb_w}:{thumb_h},format=bgra',
        '-f', 'rawvideo',
        'pipe:1',
    ]

    return _run_pipe_cmd(cmd, thumb_w, thumb_h)


def _try_extract_pipe_auto(video_path, timestamp, thumb_w, thumb_h):
    """
    GPU auto-detect decode + CPU scale via rawvideo pipe.

    Uses '-hwaccel auto' which lets ffmpeg pick the best available hardware
    decoder (NVDEC, D3D11VA, DXVA2, VideoToolbox, VAAPI, etc.) without
    requiring specific output format or GPU scale filters.
    """
    cmd = [
        'ffmpeg', '-hide_banner', '-loglevel', 'error',
        '-nostdin',
        '-probesize', '32768', '-analyzeduration', '0',
        '-fflags', '+nobuffer',
        '-hwaccel', 'auto',
        '-ss', f'{timestamp:.4f}',
        '-i', video_path,
        '-frames:v', '1',
        '-vf', f'scale={thumb_w}:{thumb_h},format=bgra',
        '-f', 'rawvideo',
        'pipe:1',
    ]

    return _run_pipe_cmd(cmd, thumb_w, thumb_h)


def _run_pipe_cmd(cmd, expected_w, expected_h):
    """
    Run an ffmpeg command that outputs rawvideo BGRA to stdout pipe.
    Returns (width, height, bytes) or None on failure.
    """
    expected_size = expected_w * expected_h * 4

    try:
        startupinfo, popen_kwargs = _build_popen_priority_kwargs()
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            startupinfo=startupinfo,
            **popen_kwargs,
        )

        if proc.returncode != 0:
            stderr_msg = proc.stderr[:300] if proc.stderr else b''
            if isinstance(stderr_msg, bytes):
                stderr_msg = stderr_msg.decode('utf-8', errors='replace')
            hwaccel_in_cmd = any(x in cmd for x in ['-hwaccel'])
            label = "GPU" if hwaccel_in_cmd else "SW"
            print(f"[ffmpeg {label}] exit={proc.returncode}: {stderr_msg.strip()}")
            return None

        if len(proc.stdout) != expected_size:
            print(f"[ffmpeg] Unexpected frame size: got {len(proc.stdout)}, "
                  f"expected {expected_size} ({expected_w}x{expected_h}x4)")
            return None

        return (expected_w, expected_h, proc.stdout)
    except Exception as e:
        print(f"[ffmpeg] Pipe extraction error: {e}")
        return None


def _extract_single_frame(args):
    """
    Extract exactly one frame at a specific timestamp.

    First tries the fast pipe-based path (GPU-accel → SW pipe → file fallback).
    The pipe path avoids temp files and JPEG encode/decode overhead entirely.

    Returns either:
      - ('pipe', width, height, bytes)  for pipe-based extraction, or
      - ('file', path)                  for file-based fallback, or
      - None                            on total failure.
    """
    video_path = args[0]
    timestamp = args[1]
    target_height = args[2]
    out_path = args[3]

    # Extended args format: (video_path, timestamp, target_height, out_path, thumb_w)
    if len(args) == 5:
        thumb_w = args[4]
    else:
        thumb_w = None

    if thumb_w is not None and thumb_w > 0:
        result = _extract_frame_pipe(video_path, timestamp, thumb_w, target_height)
        if result is not None:
            w, h, buf = result
            return ('pipe', w, h, buf)

    # --- Fallback: file-based extraction (original approach) ---
    cmd = [
        'ffmpeg', '-nostdin',
        '-probesize', '32768', '-analyzeduration', '0',
        '-fflags', '+nobuffer',
        '-ss', f'{timestamp:.4f}',
        '-i', video_path,
        '-vf', f'scale=-1:{target_height}',
        '-frames:v', '1',
        '-q:v', '3',
        '-y',
        out_path,
    ]

    try:
        startupinfo, popen_kwargs = _build_popen_priority_kwargs()
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            startupinfo=startupinfo,
            **popen_kwargs,
        )
        _lower_process_priority(proc)
        proc.wait()
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            return ('file', out_path)
    except Exception as e:
        print(f"FFmpeg frame extraction error: {e}")
    return None


# =====================================================================
# Single-pass thumbnail extraction (professional NLE approach)
# =====================================================================

def _build_single_pass_cmd(video_path, thumb_w, thumb_h, fps_rate,
                           hwaccel=True, keyframe_only=True):
    """
    Build a single ffmpeg command that extracts ALL timeline thumbnails
    in one pass, outputting a continuous rawvideo BGRA stream to stdout.

    This is the approach used by professional NLEs (Shotcut/MLT, Kdenlive):
    - One process startup (vs N individual processes)
    - One container parse, one GPU context
    - -skip_frame nokey decodes only keyframes (~100x fewer for H.264/H.265)
    - fps filter subsamples to the desired thumbnail rate
    - Continuous pipe output for progressive display
    """
    cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error',
           '-nostdin',
           '-probesize', '32768', '-analyzeduration', '0',
           '-fflags', '+nobuffer']
    if hwaccel:
        cmd.extend(['-hwaccel', 'auto'])
    if keyframe_only:
        cmd.extend(['-skip_frame', 'nokey'])
    cmd.extend(['-i', video_path])
    # -skip_frame nokey already limits decode to keyframes, so adding a
    # select='eq(pict_type,I)' filter here would be redundant and add
    # overhead on this hot path.  We rely solely on skip_frame for
    # keyframe-only behavior.
    vf = f"fps={fps_rate:.6f},scale={thumb_w}:{thumb_h},format=bgra"
    cmd.extend(['-vf', vf, '-an', '-f', 'rawvideo', '-vsync', 'vfr',
                'pipe:1'])
    return cmd

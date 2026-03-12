import sys
import os
import subprocess
import tempfile
import shutil
import json
import concurrent.futures
import time

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

from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                               QHBoxLayout, QPushButton, QLabel, QFileDialog,
                               QScrollArea, QFrame, QSizePolicy)
from PySide6.QtCore import Qt, QUrl, QSize, QThread, Signal
from PySide6.QtGui import QIcon, QPixmap, QImage, QPalette, QPainter, QPen
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget

# --- 1. 图标路径配置 ---
BASE_PATH = r"D:\python_code\iPhoto\iPhotos\src\iPhoto\gui\ui\icon"
ICON_PLAY = os.path.join(BASE_PATH, "play.fill.svg")
ICON_LEFT = os.path.join(BASE_PATH, "chevron.left.svg")
ICON_RIGHT = os.path.join(BASE_PATH, "chevron.right.svg")

# --- 2. 尺寸与样式配置 ---
BAR_HEIGHT = 50
THUMB_WIDTH = 70  # 仅作为 fallback 默认宽度
CORNER_RADIUS = 6
BORDER_THICKNESS = 4
THUMB_LOGICAL_HEIGHT = BAR_HEIGHT - 2 * BORDER_THICKNESS
ARROW_THICKNESS = 3
THEME_COLOR = "#3a3a3a"
HOVER_COLOR = "#505050"

# --- Parallelism tuning ---
# Max PyAV worker threads (each opens its own container, so memory scales)
PYAV_MAX_WORKERS = 4
# Max concurrent ffmpeg slices for the sliced subprocess strategy
MAX_FFMPEG_SLICES = 3
# Extra frames to read beyond expected count (fps filter rounding tolerance)
FRAME_READ_BUFFER = 3

# --- 3. 样式表 (QSS) ---
STYLESHEET = f"""
QMainWindow {{
    background-color: #1e1e1e;
}}

/* 底部区域背景 */
QFrame#BottomControlFrame {{
    background-color: #252525;
    border-top: 1px solid #333;
}}

/* === 播放按钮 === */
QPushButton#PlayButton {{
    background-color: {THEME_COLOR};
    border: none;
    border-top-left-radius: {CORNER_RADIUS}px;
    border-bottom-left-radius: {CORNER_RADIUS}px;
    border-top-right-radius: 0px;
    border-bottom-right-radius: 0px;
    color: white;
}}
QPushButton#PlayButton:hover {{ background-color: {HOVER_COLOR}; }}

/* === 缩略图条中间容器 === */
QWidget#StripContainer {{
    background-color: {THEME_COLOR};
}}

QScrollArea {{
    background-color: transparent;
    border: none;
}}
"""


def _get_video_info(video_path):
    """Probe video display dimensions, duration, rotation and vflip via ffprobe.

    Returns (display_w, display_h, duration, rotation, vflip) where
    display_w/display_h are the dimensions after applying rotation metadata,
    rotation is 0/90/180/270 degrees, and vflip indicates vertical flip.
    """
    try:
        cmd = [
            'ffprobe',
            '-v', 'error',
            '-probesize', '32768', '-analyzeduration', '0',
            '-select_streams', 'v:0',
            '-show_entries',
            'stream=width,height,duration:stream_tags=rotate'
            ':stream_side_data=rotation,displaymatrix',
            '-of', 'json',
            video_path,
        ]
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        result = subprocess.run(
            cmd, capture_output=True, text=True, startupinfo=startupinfo,
        )
        data = json.loads(result.stdout)
        stream = data['streams'][0]
        width = int(stream['width'])
        height = int(stream['height'])
        duration = float(stream.get('duration', 0))
        if duration == 0:
            cmd_fmt = [
                'ffprobe', '-v', 'error',
                '-probesize', '32768', '-analyzeduration', '0',
                '-show_entries', 'format=duration',
                '-of', 'json', video_path,
            ]
            res = subprocess.run(
                cmd_fmt, capture_output=True, text=True,
                startupinfo=startupinfo,
            )
            data_fmt = json.loads(res.stdout)
            duration = float(data_fmt['format']['duration'])

        # --- Detect rotation / flip ---
        rotation, vflip = _parse_rotation_from_ffprobe(stream)

        # Swap dimensions for 90°/270° rotation
        if rotation in (90, 270):
            width, height = height, width

        return width, height, duration, rotation, vflip
    except Exception as e:
        print(f"Error getting video info: {e}")
        return 0, 0, 0, 0, False


def _parse_rotation_from_ffprobe(stream_dict):
    """Extract rotation and vflip from ffprobe stream dict.

    Checks both ``tags.rotate`` (older containers) and
    ``side_data_list[].rotation`` (modern display-matrix).

    Returns (rotation_degrees, vflip) where rotation is normalised
    to 0/90/180/270.
    """
    rotation = 0
    vflip = False

    # Method 1: stream_tags.rotate (MP4/MOV with older muxers)
    tags = stream_dict.get('tags', {})
    if 'rotate' in tags:
        try:
            rotation = int(tags['rotate'])
        except (ValueError, TypeError):
            pass

    # Method 2: side_data_list[].rotation (display-matrix, ffprobe >= 4.x)
    if rotation == 0:
        for sd in stream_dict.get('side_data_list', []):
            if sd.get('side_data_type') == 'Display Matrix':
                try:
                    r = float(sd.get('rotation', 0))
                    # ffprobe reports CW rotation as negative
                    rotation = int(-r) % 360
                except (ValueError, TypeError):
                    pass
                # Detect vflip from display matrix string
                dm = sd.get('displaymatrix', '')
                if dm:
                    vflip = _displaymatrix_has_vflip(dm)
                break

    # Normalise to [0, 360)
    rotation = rotation % 360
    # Snap to nearest 90° (some encoders write e.g. 89 or 91)
    if rotation not in (0, 90, 180, 270):
        rotation = min((0, 90, 180, 270), key=lambda x: abs(x - rotation))

    return rotation, vflip


def _displaymatrix_has_vflip(dm_string):
    """Heuristic: detect vertical flip from ffprobe displaymatrix string.

    The display matrix is printed as 3 rows of hex values. A pure vflip
    has a negative [1][1] element (second row, second value).
    """
    try:
        lines = [l.strip() for l in dm_string.strip().split('\n') if l.strip()]
        if len(lines) >= 2:
            # Each line has 3 hex values like "00010000 00000000 00000000"
            parts = lines[1].split()
            if len(parts) >= 2:
                val = int(parts[1], 16)
                # Sign-extend 32-bit value
                if val >= 0x80000000:
                    val -= 0x100000000
                return val < 0
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# PyAV-based extraction (no subprocess, direct C API via libav)
# ---------------------------------------------------------------------------

def _get_video_info_pyav(video_path):
    """Probe video display dimensions, duration, rotation and vflip via PyAV.

    Returns (display_w, display_h, duration, rotation, vflip).
    """
    try:
        container = _av_module.open(video_path)
        stream = container.streams.video[0]
        width = stream.codec_context.width
        height = stream.codec_context.height
        duration = 0.0
        if stream.duration and stream.time_base:
            duration = float(stream.duration * stream.time_base)
        if duration <= 0 and container.duration:
            duration = container.duration / _av_module.time_base

        # Detect rotation from stream metadata or frame display-matrix
        rotation, vflip = _detect_rotation_pyav(stream, container)
        container.close()

        # Swap to display dimensions for 90°/270° rotation
        if rotation in (90, 270):
            width, height = height, width

        return width, height, duration, rotation, vflip
    except Exception as e:
        print(f"[pyav] Probe failed: {e}")
        return 0, 0, 0, 0, False


def _detect_rotation_pyav(stream, container=None):
    """Detect rotation and vflip from a PyAV video stream.

    Checks two sources in order:
    1. ``stream.metadata['rotate']`` — older MP4/MOV with rotation tag.
    2. First decoded frame — modern containers using display-matrix side
       data (most modern phones).  Requires *container* so we can decode
       one frame.  Checks ``frame.rotation`` first, then falls back to
       ``frame.side_data['DISPLAYMATRIX']`` dict access.

    Returns (rotation_degrees, vflip) normalised to 0/90/180/270.
    """
    rotation = 0
    vflip = False

    # Method 1: metadata tag (common in MP4/MOV from older phones)
    try:
        rotate_val = stream.metadata.get('rotate', '')
        if rotate_val:
            rotation = int(rotate_val)
    except (ValueError, TypeError, AttributeError):
        pass

    # Method 2: decode one frame and read rotation from frame-level data.
    # PyAV exposes the display-matrix rotation on *frames*, not streams.
    # frame.rotation returns CCW degrees in [-180, 180].
    if rotation == 0 and container is not None:
        try:
            container.seek(0, stream=stream)
            for frame in container.decode(stream):
                # Try frame.rotation (direct attribute, PyAV >= 12)
                fr = getattr(frame, 'rotation', None)
                if fr is not None and fr != 0:
                    rotation = int(-fr) % 360  # CCW → CW
                else:
                    # Fallback: frame.side_data dict
                    fsd = getattr(frame, 'side_data', None)
                    if fsd and hasattr(fsd, 'get'):
                        dm = fsd.get('DISPLAYMATRIX')
                        if dm is not None:
                            if isinstance(dm, dict):
                                r = float(dm.get('rotation', 0))
                                rotation = int(-r) % 360
                                # Detect vflip from matrix values
                                sy = dm.get('sy') or dm.get('d')
                                if sy is not None:
                                    try:
                                        if float(sy) < 0:
                                            vflip = True
                                    except (ValueError, TypeError):
                                        pass
                            elif isinstance(dm, (int, float)):
                                rotation = int(-dm) % 360
                break  # only need the first frame
        except Exception:
            pass

    # Normalise
    rotation = rotation % 360
    if rotation not in (0, 90, 180, 270):
        rotation = min((0, 90, 180, 270), key=lambda x: abs(x - rotation))

    return rotation, vflip


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
    # Ensure timestamps are sorted (and deduplicated) as promised.
    if not keyframes:
        return keyframes
    return sorted(set(keyframes))


def _snap_to_keyframes(target_times, keyframes):
    """Map each target time to the nearest keyframe timestamp.

    If no keyframes are available, returns the original target_times.

    Args:
        target_times: List of desired float timestamps.
        keyframes: Sorted list of keyframe float timestamps.

    Returns:
        List of (original_index, snapped_timestamp) pairs.
    """
    if not keyframes:
        return list(enumerate(target_times))

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


# ---------------------------------------------------------------------------
# Hardware-acceleration detection (cached per process)
# ---------------------------------------------------------------------------
_hwaccel_cache = None


def _detect_hwaccel():
    """
    Detect the best available ffmpeg hardware acceleration and GPU scale filter.

    Returns a dict with keys:
      - 'hwaccel': str or None  (e.g. 'd3d11va', 'cuda', 'videotoolbox', None)
      - 'scale_filter': str     (e.g. 'scale_d3d11', 'scale_cuda', 'scale')
      - 'download_filter': str  (e.g. 'hwdownload' or '')
      - 'pix_fmt': str          (output pixel format, always 'bgra')
    """
    global _hwaccel_cache
    if _hwaccel_cache is not None:
        return _hwaccel_cache

    _hwaccel_cache = {
        'hwaccel': None,
        'scale_filter': 'scale',
        'download_filter': '',
        'pix_fmt': 'bgra',
    }

    try:
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        result = subprocess.run(
            ['ffmpeg', '-hide_banner', '-hwaccels'],
            capture_output=True, text=True, startupinfo=startupinfo,
        )
        # Check both stdout AND stderr — ffmpeg versions vary in output routing
        hwaccels_text = (result.stdout + '\n' + result.stderr).lower()

        # Also check available filters for GPU scaling
        filter_result = subprocess.run(
            ['ffmpeg', '-hide_banner', '-filters'],
            capture_output=True, text=True, startupinfo=startupinfo,
        )
        filters_text = (filter_result.stdout + '\n' + filter_result.stderr).lower()

        skip_words = ('hardware', 'acceleration', 'methods:')
        avail = [x for x in hwaccels_text.split() if x not in skip_words]
        print(f"[hwaccel] Available accelerators: {avail}")

        # Platform-dependent preference order:
        #   Windows: cuda (NVIDIA) > d3d11va (all GPUs) > qsv (Intel)
        #   macOS:   videotoolbox
        #   Linux:   cuda > vaapi
        # Each entry: (hwaccel_name, gpu_scale_filter_name)
        if os.name == 'nt':
            candidates = [
                ('cuda', 'scale_cuda'),
                ('d3d11va', 'scale_d3d11'),
                ('qsv', 'scale_qsv'),
            ]
        elif sys.platform == 'darwin':
            candidates = [
                ('videotoolbox', 'scale_vt'),
            ]
        else:
            candidates = [
                ('cuda', 'scale_cuda'),
                ('vaapi', 'scale_vaapi'),
            ]

        for hwaccel_name, gpu_scale in candidates:
            if hwaccel_name in hwaccels_text:
                _hwaccel_cache['hwaccel'] = hwaccel_name
                if gpu_scale in filters_text:
                    _hwaccel_cache['scale_filter'] = gpu_scale
                else:
                    _hwaccel_cache['scale_filter'] = 'scale'
                _hwaccel_cache['download_filter'] = 'hwdownload'
                print(f"[hwaccel] Selected: {hwaccel_name}, "
                      f"GPU scale: {gpu_scale if gpu_scale in filters_text else 'N/A (CPU scale)'}")
                break

        if _hwaccel_cache['hwaccel'] is None:
            print("[hwaccel] No hardware acceleration detected, will use software decode")

    except Exception as e:
        print(f"[hwaccel] Detection failed: {e}")

    return _hwaccel_cache


def _build_hwaccel_output_format(hwaccel):
    """Return the -hwaccel_output_format value for a given hwaccel."""
    mapping = {
        'cuda': 'cuda',
        'd3d11va': 'd3d11',
        'videotoolbox': 'videotoolbox_vld',
        'vaapi': 'vaapi',
        'qsv': 'qsv',
    }
    return mapping.get(hwaccel, hwaccel)


# ---------------------------------------------------------------------------
# Pipe-based frame extraction (GPU-accelerated or software fallback)
# ---------------------------------------------------------------------------

def _build_popen_priority_kwargs():
    """Build OS-specific kwargs to lower the priority of ffmpeg child processes."""
    popen_kwargs = {}
    startupinfo = None
    if os.name == 'nt':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        # BELOW_NORMAL_PRIORITY_CLASS on Windows
        popen_kwargs['creationflags'] = 0x00004000
    else:
        popen_kwargs['preexec_fn'] = lambda: os.nice(10)
    return startupinfo, popen_kwargs


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
    # Uses -hwaccel auto which lets ffmpeg pick the best GPU decoder.
    # This catches cases where specific hwaccel detection fails but
    # the GPU is still available.
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

    This is the most robust GPU path — it works on any system where ffmpeg
    has GPU support, even if the specific hwaccel detection in _detect_hwaccel()
    fails.
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
            # Only log at debug level for expected fallback scenarios
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
        proc.wait()
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            return ('file', out_path)
    except Exception as e:
        print(f"FFmpeg frame extraction error: {e}")
    return None


# ---------------------------------------------------------------------------
# Single-pass thumbnail extraction (professional NLE approach)
# ---------------------------------------------------------------------------

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
    # Use select='eq(pict_type,I)' for precise keyframe-only filtering;
    # combined with -skip_frame nokey this ensures only I-frames are
    # decoded AND passed through the filter chain.
    if keyframe_only:
        vf = (f"select='eq(pict_type\\,I)',"
              f"fps={fps_rate:.6f},"
              f"scale={thumb_w}:{thumb_h},format=bgra")
    else:
        vf = f"fps={fps_rate:.6f},scale={thumb_w}:{thumb_h},format=bgra"
    cmd.extend(['-vf', vf, '-an', '-f', 'rawvideo', '-vsync', 'vfr',
                'pipe:1'])
    return cmd


class ThumbnailWorker(QThread):
    """
    Background thread that generates timeline thumbnails using a single
    ffmpeg process with keyframe-only decoding — the same approach used
    by professional NLEs (Shotcut/MLT, Kdenlive).

    Performance comparison for a 3-min 4K HEVC video (13 thumbnails):
      Old (N parallel processes): ~2-5s (N startups, N seeks, N GPU contexts)
      New (single-pass keyframe): ~200-500ms (1 startup, keyframe-only decode)

    Key optimisations:
    - Single ffmpeg process (eliminates N process startups)
    - -skip_frame nokey: only decodes keyframes (~0.4% of frames for H.264)
    - fps filter subsamples to exactly the needed thumbnail rate
    - Progressive display: each thumbnail appears as soon as it's decoded
    - -hwaccel auto: GPU decode when available
    - Falls back to parallel extraction if single-pass fails
    """
    # Progressive: emits one thumbnail at a time as it arrives
    thumbnail_ready = Signal(object)
    # Batch fallback: emits all results at once (parallel path)
    thumbnails_ready = Signal(list)
    error_occurred = Signal(str)

    def __init__(self, video_path, target_height, visible_width, temp_dir,
                 num_workers=None, dpr=1.0, parent=None):
        super().__init__(parent)
        self.video_path = video_path
        self.target_height = target_height
        self.visible_width = visible_width
        self.temp_dir = temp_dir
        self.dpr = dpr
        self._abort = False
        self._proc = None
        if num_workers is None:
            num_workers = os.cpu_count() or 4
        self.num_workers = num_workers

    def abort(self):
        """Request the worker to stop. Kills any running ffmpeg process."""
        self._abort = True
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.kill()
            except OSError:
                pass

    def run(self):
        try:
            t0 = time.perf_counter()

            # --- Probe video info (prefer PyAV, fallback to ffprobe) ---
            # Both probes now return (display_w, display_h, duration,
            # rotation, vflip) where display dimensions are post-rotation.
            rotation = 0
            vflip = False
            if HAS_PYAV:
                v_w, v_h, duration, rotation, vflip = \
                    _get_video_info_pyav(self.video_path)
            else:
                v_w, v_h, duration = 0, 0, 0
            if v_w <= 0 or v_h <= 0 or duration <= 0:
                v_w, v_h, duration, rotation, vflip = \
                    _get_video_info(self.video_path)
            if v_w <= 0 or v_h <= 0 or duration <= 0:
                self.error_occurred.emit("Failed to probe video")
                return

            # Pre-compute thumbnail dimensions from video aspect ratio
            thumb_w = int(v_w * (self.target_height / v_h))
            thumb_w = max(2, thumb_w + (thumb_w % 2))
            target_h = max(2, self.target_height + (self.target_height % 2))

            scaled_width = v_w * (self.target_height / v_h)
            if scaled_width <= 0:
                scaled_width = THUMB_WIDTH
            # scaled_width is in physical pixels; convert to logical for
            # count_needed (visible_width is in logical pixels)
            logical_thumb_w = scaled_width / max(self.dpr, 1.0)
            count_needed = int(self.visible_width / logical_thumb_w) + 2
            count_needed = max(count_needed, 5)
            count_needed = min(count_needed, 60)

            print(f"Video: {v_w}x{v_h}, Duration: {duration:.1f}s, "
                  f"Thumbnails: {count_needed} @ {thumb_w}x{target_h}, "
                  f"rotation={rotation}, vflip={vflip}, "
                  f"PyAV={'yes' if HAS_PYAV else 'no'}")

            # --- Strategy 0: PyAV in-process extraction (fastest) ---
            if HAS_PYAV and not self._abort:
                if self._try_pyav(thumb_w, target_h, count_needed,
                                  rotation, vflip):
                    elapsed = time.perf_counter() - t0
                    print(f"[thumbnail] Done in {elapsed:.2f}s (PyAV)")
                    return

            fps_rate = count_needed / max(duration, 0.01)
            frame_size = thumb_w * target_h * 4

            # Strategy 1: Single-pass with GPU + keyframe-only (fastest)
            if self._try_single_pass(
                thumb_w, target_h, count_needed, fps_rate, frame_size,
                hwaccel=True, keyframe_only=True,
            ):
                elapsed = time.perf_counter() - t0
                print(f"[thumbnail] Done in {elapsed:.2f}s "
                      f"(single-pass gpu+keyframe)")
                return

            # Strategy 2: Single-pass with keyframe-only, no GPU
            if self._try_single_pass(
                thumb_w, target_h, count_needed, fps_rate, frame_size,
                hwaccel=False, keyframe_only=True,
            ):
                elapsed = time.perf_counter() - t0
                print(f"[thumbnail] Done in {elapsed:.2f}s "
                      f"(single-pass keyframe)")
                return

            # Strategy 3: Single-pass without keyframe skip
            if self._try_single_pass(
                thumb_w, target_h, count_needed, fps_rate, frame_size,
                hwaccel=False, keyframe_only=False,
            ):
                elapsed = time.perf_counter() - t0
                print(f"[thumbnail] Done in {elapsed:.2f}s "
                      f"(single-pass full)")
                return

            # Strategy 4: Sliced multi-process extraction
            if self._try_sliced_single_pass(
                thumb_w, target_h, count_needed, duration,
                keyframe_only=True,
            ):
                elapsed = time.perf_counter() - t0
                print(f"[thumbnail] Done in {elapsed:.2f}s "
                      f"(sliced keyframe)")
                return

            # Strategy 5: Parallel individual extraction (slowest fallback)
            self._fallback_parallel(
                thumb_w, target_h, count_needed, duration,
            )
            elapsed = time.perf_counter() - t0
            print(f"[thumbnail] Done in {elapsed:.2f}s (parallel fallback)")
        except Exception as e:
            self.error_occurred.emit(str(e))

    def _try_pyav(self, thumb_w, thumb_h, count_needed,
                  rotation=0, vflip=False):
        """
        Extract thumbnails using PyAV — zero subprocess overhead.

        PyAV calls FFmpeg's C API directly in-process, eliminating:
          - Process startup overhead
          - Pipe I/O serialization
          - JPEG encode/decode overhead

        Rotation and vflip are applied manually since PyAV does NOT
        auto-rotate frames like the ffmpeg CLI does.

        Frames are emitted progressively via thumbnail_ready signal
        using RGB888 format for direct QImage construction.
        """
        def on_frame(index, rgb_data, w, h):
            if not self._abort:
                self.thumbnail_ready.emit(('pyav', w, h, rgb_data))

        results = _extract_thumbnails_pyav(
            self.video_path, count_needed, thumb_w, thumb_h,
            callback=on_frame,
            rotation=rotation, vflip=vflip,
        )
        return len(results) > 0

    def _try_single_pass(self, thumb_w, thumb_h, count_needed, fps_rate,
                         frame_size, hwaccel=True, keyframe_only=True):
        """
        Single ffmpeg process that outputs all thumbnails as a continuous
        rawvideo BGRA stream. Frames are emitted progressively via the
        thumbnail_ready signal — the UI shows each thumbnail the instant
        it arrives from the pipe.
        """
        mode = "gpu" if hwaccel else "cpu"
        if keyframe_only:
            mode += "+keyframe"
        cmd = _build_single_pass_cmd(
            self.video_path, thumb_w, thumb_h, fps_rate,
            hwaccel=hwaccel, keyframe_only=keyframe_only,
        )
        print(f"[thumbnail] Trying single-pass ({mode})")

        try:
            startupinfo, popen_kwargs = _build_popen_priority_kwargs()
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=frame_size,
                startupinfo=startupinfo,
                **popen_kwargs,
            )
            self._proc = proc

            count = 0
            # Allow a few extra frames beyond count_needed to handle
            # fps filter rounding/alignment differences
            max_frames = count_needed + 5
            while count < max_frames and not self._abort:
                data = proc.stdout.read(frame_size)
                if len(data) < frame_size:
                    break
                self.thumbnail_ready.emit(
                    ('pipe', thumb_w, thumb_h, bytes(data)),
                )
                count += 1

            proc.stdout.close()
            try:
                stderr = proc.stderr.read()
                proc.stderr.close()
            except Exception:
                stderr = b''
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            self._proc = None

            if count > 0:
                print(f"[thumbnail] Single-pass ({mode}): "
                      f"got {count} frames")
                return True

            if stderr:
                msg = stderr[:300].decode('utf-8', errors='replace')
                print(f"[thumbnail] Single-pass ({mode}) failed: "
                      f"{msg.strip()}")
            return False

        except Exception as e:
            print(f"[thumbnail] Single-pass ({mode}) error: {e}")
            self._proc = None
            return False

    def _fallback_parallel(self, thumb_w, target_h, count_needed,
                           duration):
        """Fall back to N parallel individual frame extractions."""
        print("[thumbnail] Falling back to parallel extraction")

        timestamps = [
            i * duration / count_needed for i in range(count_needed)
        ]
        tasks = []
        for i, ts in enumerate(timestamps):
            out_path = os.path.join(
                self.temp_dir, f"thumb_{i:04d}.jpg",
            )
            tasks.append(
                (self.video_path, ts, target_h, out_path, thumb_w),
            )

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=self.num_workers,
        ) as pool:
            results = list(pool.map(_extract_single_frame, tasks))

        valid = [r for r in results if r is not None]
        self.thumbnails_ready.emit(valid)

    def _try_sliced_single_pass(self, thumb_w, thumb_h, count_needed,
                                duration, keyframe_only=True):
        """
        Distribute thumbnail extraction across 2-3 concurrent ffmpeg
        processes, each handling a time segment of the video.

        On multi-core CPUs this achieves near-linear speedup because
        each process decodes only its portion of the timeline.
        """
        num_slices = min(MAX_FFMPEG_SLICES, max(1, (os.cpu_count() or 2) // 2))
        num_slices = min(num_slices, count_needed)
        if num_slices <= 1:
            return False

        # Distribute frames across slices
        frames_per_slice = count_needed // num_slices
        remainder = count_needed % num_slices
        slices = []  # (start_time, segment_duration, n_frames)
        step = duration / count_needed
        offset = 0
        for s in range(num_slices):
            n = frames_per_slice + (1 if s < remainder else 0)
            start_time = offset * step
            seg_duration = n * step
            slices.append((start_time, seg_duration, n))
            offset += n

        frame_size = thumb_w * thumb_h * 4
        mode = "sliced"
        if keyframe_only:
            mode += "+keyframe"
        print(f"[thumbnail] Trying {mode} ({num_slices} slices)")

        def run_slice(slice_args):
            """Run one ffmpeg slice and return list of (index, data)."""
            s_idx, (start_t, seg_dur, n_frames) = slice_args
            fps_rate = n_frames / max(seg_dur, 0.01)
            cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error',
                   '-nostdin',
                   '-probesize', '32768', '-analyzeduration', '0',
                   '-fflags', '+nobuffer']
            if keyframe_only:
                cmd.extend(['-skip_frame', 'nokey'])
            cmd.extend(['-ss', f'{start_t:.4f}',
                        '-t', f'{seg_dur:.4f}',
                        '-i', self.video_path])
            vf = (f"fps={fps_rate:.6f},"
                  f"scale={thumb_w}:{thumb_h},format=bgra")
            cmd.extend(['-vf', vf, '-an', '-f', 'rawvideo', 'pipe:1'])

            try:
                startupinfo, popen_kwargs = _build_popen_priority_kwargs()
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    bufsize=frame_size,
                    startupinfo=startupinfo,
                    **popen_kwargs,
                )
                frames = []
                max_read = n_frames + FRAME_READ_BUFFER
                for _ in range(max_read):
                    data = proc.stdout.read(frame_size)
                    if len(data) < frame_size:
                        break
                    frames.append(bytes(data))
                proc.stdout.close()
                try:
                    proc.stderr.close()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                return s_idx, frames
            except Exception as e:
                print(f"[sliced] Slice {s_idx} error: {e}")
                return s_idx, []

        try:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=num_slices,
            ) as pool:
                futures = [
                    pool.submit(run_slice, (i, s))
                    for i, s in enumerate(slices)
                ]
                slice_results = {}
                for f in concurrent.futures.as_completed(futures):
                    if self._abort:
                        break
                    s_idx, frames = f.result()
                    slice_results[s_idx] = frames

            # Merge slices in order and emit
            total = 0
            for s_idx in range(num_slices):
                for data in slice_results.get(s_idx, []):
                    if self._abort:
                        break
                    self.thumbnail_ready.emit(
                        ('pipe', thumb_w, thumb_h, data),
                    )
                    total += 1

            if total > 0:
                print(f"[thumbnail] Sliced ({mode}): got {total} frames")
                return True
            return False

        except Exception as e:
            print(f"[thumbnail] Sliced ({mode}) error: {e}")
            return False


class HandleButton(QPushButton):
    """
    自定义手柄按钮：绘制加粗白色箭头
    """

    def __init__(self, arrow_type="left", parent=None):
        super().__init__(parent)
        self.arrow_type = arrow_type
        self.setFixedWidth(24)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.setCursor(Qt.PointingHandCursor)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        pen = QPen(Qt.white)
        pen.setWidth(ARROW_THICKNESS)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)

        w = self.width()
        h = self.height()
        arrow_w = 8
        arrow_h = 14
        cx = w / 2
        cy = h / 2

        if self.arrow_type == "left":
            p1 = (cx + arrow_w / 2, cy - arrow_h / 2)
            p2 = (cx - arrow_w / 2, cy)
            p3 = (cx + arrow_w / 2, cy + arrow_h / 2)
        else:
            p1 = (cx - arrow_w / 2, cy - arrow_h / 2)
            p2 = (cx + arrow_w / 2, cy)
            p3 = (cx - arrow_w / 2, cy + arrow_h / 2)

        painter.drawLine(int(p1[0]), int(p1[1]), int(p2[0]), int(p2[1]))
        painter.drawLine(int(p2[0]), int(p2[1]), int(p3[0]), int(p3[1]))


class ThumbnailBar(QWidget):
    """
    Thumbnail strip that draws all pixmaps directly via QPainter in
    paintEvent, avoiding N child QLabel widgets and layout relayout.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(BAR_HEIGHT)
        self._pixmaps = []  # list[QPixmap] — drawn in paintEvent

        self._main_layout = QHBoxLayout(self)
        self._main_layout.setContentsMargins(0, 0, 0, 0)
        self._main_layout.setSpacing(0)

        # 1. 左手柄
        self.btn_left = HandleButton(arrow_type="left")
        self.btn_left.setObjectName("HandleLeft")
        self.btn_left.setStyleSheet(f"""
            QPushButton {{
                background-color: {THEME_COLOR};
                border: none;
                border-radius: 0px;
            }}
            QPushButton:hover {{ background-color: {HOVER_COLOR}; }}
        """)

        # 2. 缩略图画布 (custom painted, no child widgets)
        self._canvas = _ThumbnailCanvas(self)

        # 3. 右手柄
        self.btn_right = HandleButton(arrow_type="right")
        self.btn_right.setObjectName("HandleRight")
        self.btn_right.setStyleSheet(f"""
            QPushButton {{
                background-color: {THEME_COLOR};
                border: none;
                border-top-left-radius: 0px;
                border-bottom-left-radius: 0px;
                border-top-right-radius: {CORNER_RADIUS}px;
                border-bottom-right-radius: {CORNER_RADIUS}px;
            }}
            QPushButton:hover {{ background-color: {HOVER_COLOR}; }}
        """)

        self._main_layout.addWidget(self.btn_left)
        self._main_layout.addWidget(self._canvas, stretch=1)
        self._main_layout.addWidget(self.btn_right)

    @property
    def scroll_area(self):
        """Compatibility: ThumbnailWorker uses .scroll_area.width() to
        compute how many thumbnails to generate."""
        return self._canvas

    def add_thumbnail(self, pixmap):
        """Append a pixmap and trigger a repaint — no layout relayout.

        Scales to physical pixel height (logical * DPR) for crisp HiDPI
        rendering, then sets devicePixelRatio on the pixmap so QPainter
        draws it at the correct logical size.
        """
        dpr = self.devicePixelRatioF()
        target_height_phys = int(THUMB_LOGICAL_HEIGHT * dpr)
        scaled = pixmap.scaledToHeight(
            target_height_phys, Qt.SmoothTransformation,
        )
        scaled.setDevicePixelRatio(dpr)
        self._pixmaps.append(scaled)
        self._canvas.set_pixmaps(self._pixmaps)

    def clear(self):
        self._pixmaps.clear()
        self._canvas.set_pixmaps(self._pixmaps)


class _ThumbnailCanvas(QWidget):
    """
    Lightweight widget that paints a list of QPixmaps side by side
    using QPainter.drawPixmap in paintEvent — zero child widgets,
    zero layout overhead.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmaps = []
        self.setObjectName("StripContainer")
        self.setAttribute(Qt.WA_OpaquePaintEvent)

    def set_pixmaps(self, pixmaps):
        self._pixmaps = pixmaps
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        h = self.height()
        y_offset = BORDER_THICKNESS
        draw_h = h - 2 * BORDER_THICKNESS

        # Fill background using the widget's palette Window color
        bg = self.palette().color(QPalette.Window)
        painter.fillRect(self.rect(), bg)

        x = 0
        for pm in self._pixmaps:
            # Stop drawing beyond visible width (no scrolling)
            if x >= self.width():
                break
            # Use logical width/height — QPainter handles DPR scaling
            dpr = pm.devicePixelRatio() or 1.0
            logical_w = round(pm.width() / dpr)
            painter.drawPixmap(x, y_offset, logical_w, draw_h, pm)
            x += logical_w
        painter.end()


class VideoEditor(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Video Trimmer UI - Dynamic Fill")
        self.resize(1000, 700)
        self.setStyleSheet(STYLESHEET)

        self.temp_dir = None
        self._thumb_worker = None

        # --- 主布局 ---
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 1. 顶部导入
        self.btn_import = QPushButton("Import Video")
        self.btn_import.setStyleSheet("background-color: #222; color: #888; padding: 5px; border:none;")
        self.btn_import.clicked.connect(self.open_file)
        main_layout.addWidget(self.btn_import)

        # 2. 视频区域
        self.video_widget = QVideoWidget()
        pal = self.video_widget.palette()
        pal.setColor(QPalette.Window, Qt.black)
        self.video_widget.setPalette(pal)
        self.video_widget.setAutoFillBackground(True)
        main_layout.addWidget(self.video_widget, stretch=1)

        # 3. 底部控制条
        self.bottom_frame = QFrame()
        self.bottom_frame.setObjectName("BottomControlFrame")
        self.bottom_frame.setFixedHeight(BAR_HEIGHT + 30)

        bottom_layout = QHBoxLayout(self.bottom_frame)
        bottom_layout.setContentsMargins(20, 15, 20, 15)
        bottom_layout.setSpacing(0)

        # 组合控件容器
        self.controls_layout = QHBoxLayout()
        self.controls_layout.setSpacing(2)
        self.controls_layout.setContentsMargins(0, 0, 0, 0)

        # 3.1 播放按钮
        self.play_btn = QPushButton()
        self.play_btn.setObjectName("PlayButton")
        self.play_btn.setFixedSize(50, BAR_HEIGHT)

        if os.path.exists(ICON_PLAY):
            self.play_btn.setIcon(QIcon(ICON_PLAY))
            self.play_btn.setIconSize(QSize(20, 20))
        else:
            self.play_btn.setText("▶")

        self.play_btn.clicked.connect(self.toggle_play)

        # 3.2 缩略图条
        self.thumb_strip = ThumbnailBar()

        self.controls_layout.addWidget(self.play_btn)
        self.controls_layout.addWidget(self.thumb_strip, stretch=1)

        bottom_layout.addLayout(self.controls_layout)
        main_layout.addWidget(self.bottom_frame)

        # --- 播放器 ---
        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_widget)
        self.player.mediaStatusChanged.connect(self.on_media_status)

    def open_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Video", "", "Video Files (*.mp4 *.mov *.avi *.mkv)")
        if file_path:
            self.btn_import.hide()
            self.load_video(file_path)

    def load_video(self, file_path):
        self.player.setSource(QUrl.fromLocalFile(file_path))
        self.player.play()
        self.generate_thumbnails(file_path)

    def toggle_play(self):
        is_playing = (self.player.playbackState() == QMediaPlayer.PlayingState)

        if is_playing:
            self.player.pause()
        else:
            self.player.play()

        if not os.path.exists(ICON_PLAY):
            self.play_btn.setText("▶" if is_playing else "⏸")

    def on_media_status(self, status):
        if status == QMediaPlayer.EndOfMedia:
            self.player.setPosition(0)
            self.player.pause()
            if not os.path.exists(ICON_PLAY):
                self.play_btn.setText("▶")

    def get_video_info(self, video_path):
        """使用 ffprobe 获取视频时长和分辨率 (kept for API compat, delegates to module fn)"""
        return _get_video_info(video_path)

    def generate_thumbnails(self, video_path):
        self.thumb_strip.clear()

        if self.temp_dir and os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)
        self.temp_dir = tempfile.mkdtemp()

        # Generate at physical pixel resolution for crisp HiDPI display
        dpr = self.devicePixelRatioF()
        target_height = int(THUMB_LOGICAL_HEIGHT * dpr)

        visible_width = self.thumb_strip.scroll_area.width()
        if visible_width < 100:
            visible_width = 1000

        # Launch worker — uses single-pass approach (NLE-style)
        self._thumb_worker = ThumbnailWorker(
            video_path, target_height, visible_width, self.temp_dir,
            dpr=dpr,
        )
        # Progressive display (single-pass path — each thumb as it arrives)
        self._thumb_worker.thumbnail_ready.connect(
            self._on_single_thumbnail,
        )
        # Batch display (parallel fallback path)
        self._thumb_worker.thumbnails_ready.connect(
            self._on_thumbnails_ready,
        )
        self._thumb_worker.error_occurred.connect(
            self._on_thumbnail_error,
        )
        self._thumb_worker.start()

    def _on_single_thumbnail(self, result):
        """Slot: add one thumbnail as soon as it arrives from the pipe."""
        if result[0] == 'pyav':
            # PyAV path: RGB888 format
            _, w, h, buf = result
            img = QImage(
                buf, w, h, w * 3, QImage.Format.Format_RGB888,
            ).copy()
            pix = QPixmap.fromImage(img)
        elif result[0] == 'pipe':
            # ffmpeg subprocess path: BGRA format
            _, w, h, buf = result
            img = QImage(
                buf, w, h, w * 4, QImage.Format.Format_ARGB32,
            ).copy()
            pix = QPixmap.fromImage(img)
        else:
            pix = QPixmap(result[1])
        if not pix.isNull():
            self.thumb_strip.add_thumbnail(pix)

    def _on_thumbnails_ready(self, results):
        """Slot: called on the main/UI thread when all thumbnails are done.

        Each result is either:
          ('pipe', width, height, bytes) — raw BGRA pixels from pipe
          ('file', path)                — JPEG file on disk
        """
        if not results:
            self._on_thumbnail_error("No thumbnails generated")
            return
        for r in results:
            if r[0] == 'pipe':
                _, w, h, buf = r
                # Note: ffmpeg outputs BGRA byte order. On little-endian systems
                # (Windows/Linux x86), QImage.Format_ARGB32 stores pixels as
                # B-G-R-A in memory, which matches ffmpeg's BGRA output exactly.
                img = QImage(buf, w, h, w * 4, QImage.Format.Format_ARGB32).copy()
                pix = QPixmap.fromImage(img)
            else:
                # File-based fallback
                pix = QPixmap(r[1])
            if not pix.isNull():
                self.thumb_strip.add_thumbnail(pix)

    def _on_thumbnail_error(self, msg):
        """Slot: fallback grey rectangles when generation fails."""
        print(f"Thumbnail generation error: {msg}")
        dpr = self.devicePixelRatioF()
        phys_h = int(THUMB_LOGICAL_HEIGHT * dpr)
        phys_w = int(THUMB_WIDTH * dpr)
        fallback = QPixmap(phys_w, phys_h)
        fallback.setDevicePixelRatio(dpr)
        fallback.fill(Qt.darkGray)
        for _ in range(10):
            self.thumb_strip.add_thumbnail(fallback)

    def closeEvent(self, event):
        if self._thumb_worker and self._thumb_worker.isRunning():
            self._thumb_worker.abort()
            self._thumb_worker.wait(3000)
        if self.temp_dir and os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = VideoEditor()
    window.show()
    sys.exit(app.exec())
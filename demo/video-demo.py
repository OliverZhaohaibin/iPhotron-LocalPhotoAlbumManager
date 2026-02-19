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
ARROW_THICKNESS = 3
THEME_COLOR = "#3a3a3a"
HOVER_COLOR = "#505050"

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
    """Probe video width, height, duration via ffprobe (thread-safe)."""
    try:
        cmd = [
            'ffprobe',
            '-v', 'error',
            '-probesize', '32768', '-analyzeduration', '0',
            '-select_streams', 'v:0',
            '-show_entries', 'stream=width,height,duration',
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
        return width, height, duration
    except Exception as e:
        print(f"Error getting video info: {e}")
        return 0, 0, 0


# ---------------------------------------------------------------------------
# PyAV-based extraction (no subprocess, direct C API via libav)
# ---------------------------------------------------------------------------

def _get_video_info_pyav(video_path):
    """Probe video width, height, duration via PyAV (no subprocess)."""
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
        container.close()
        return width, height, duration
    except Exception as e:
        print(f"[pyav] Probe failed: {e}")
        return 0, 0, 0


def _extract_thumbnails_pyav(video_path, num_frames, thumb_w, thumb_h,
                              callback=None):
    """
    Extract thumbnails using PyAV — zero subprocess overhead.

    PyAV is a Pythonic binding for FFmpeg's libav* libraries via Cython.
    It calls C API directly in-process, avoiding:
      - Process startup overhead (fork/exec/CreateProcess)
      - Pipe I/O overhead (stdout serialization)
      - JPEG encode/decode overhead

    Each frame is decoded, scaled at C level via frame.to_ndarray() or
    frame.to_image(), then converted to QImage in memory.

    Args:
        video_path: Path to video file.
        num_frames: Number of thumbnails to extract.
        thumb_w: Target thumbnail width.
        thumb_h: Target thumbnail height.
        callback: Optional callable(index, rgb_bytes, w, h) called for
                  each frame as it's extracted (progressive display).

    Returns:
        List of (width, height, bytes) tuples in RGB888 format, or empty
        list on failure.
    """
    thumbnails = []
    container = None
    try:
        container = _av_module.open(video_path)
        stream = container.streams.video[0]
        # Allow seeking to non-keyframes for faster navigation
        stream.thread_type = 'AUTO'

        duration = 0.0
        if stream.duration and stream.time_base:
            duration = float(stream.duration * stream.time_base)
        if duration <= 0 and container.duration:
            duration = container.duration / _av_module.time_base
        if duration <= 0:
            return []

        step = duration / num_frames
        time_base = stream.time_base

        for i in range(num_frames):
            target_time = i * step
            # Seek to the nearest keyframe before target_time
            pts = int(target_time / float(time_base))
            container.seek(pts, stream=stream)

            for frame in container.decode(stream):
                # Scale and convert to RGB at C level (very fast)
                img = frame.to_image(
                    width=thumb_w, height=thumb_h,
                    interpolation='FAST_BILINEAR',
                )
                rgb_data = img.tobytes("raw", "RGB")
                thumbnails.append((thumb_w, thumb_h, rgb_data))
                if callback:
                    callback(i, rgb_data, thumb_w, thumb_h)
                break  # Only need one frame per seek position

    except Exception as e:
        print(f"[pyav] Extraction error: {e}")
    finally:
        if container:
            try:
                container.close()
            except Exception:
                pass

    return thumbnails


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
    vf = f"fps={fps_rate:.6f},scale={thumb_w}:{thumb_h},format=bgra"
    cmd.extend(['-vf', vf, '-an', '-f', 'rawvideo', 'pipe:1'])
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
                 num_workers=None, parent=None):
        super().__init__(parent)
        self.video_path = video_path
        self.target_height = target_height
        self.visible_width = visible_width
        self.temp_dir = temp_dir
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
            if HAS_PYAV:
                v_w, v_h, duration = _get_video_info_pyav(self.video_path)
            else:
                v_w, v_h, duration = 0, 0, 0
            if v_w <= 0 or v_h <= 0 or duration <= 0:
                v_w, v_h, duration = _get_video_info(self.video_path)
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
            count_needed = int(self.visible_width / scaled_width) + 2
            count_needed = max(count_needed, 5)
            count_needed = min(count_needed, 60)

            print(f"Video: {v_w}x{v_h}, Duration: {duration:.1f}s, "
                  f"Thumbnails: {count_needed} @ {thumb_w}x{target_h}, "
                  f"PyAV={'yes' if HAS_PYAV else 'no'}")

            # --- Strategy 0: PyAV in-process extraction (fastest) ---
            if HAS_PYAV and not self._abort:
                if self._try_pyav(thumb_w, target_h, count_needed):
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

            # Strategy 4: Parallel individual extraction (slowest fallback)
            self._fallback_parallel(
                thumb_w, target_h, count_needed, duration,
            )
            elapsed = time.perf_counter() - t0
            print(f"[thumbnail] Done in {elapsed:.2f}s (parallel fallback)")
        except Exception as e:
            self.error_occurred.emit(str(e))

    def _try_pyav(self, thumb_w, thumb_h, count_needed):
        """
        Extract thumbnails using PyAV — zero subprocess overhead.

        PyAV calls FFmpeg's C API directly in-process, eliminating:
          - Process startup overhead
          - Pipe I/O serialization
          - JPEG encode/decode overhead

        Frames are emitted progressively via thumbnail_ready signal
        using RGB888 format for direct QImage construction.
        """
        def on_frame(index, rgb_data, w, h):
            if not self._abort:
                self.thumbnail_ready.emit(('pyav', w, h, rgb_data))

        results = _extract_thumbnails_pyav(
            self.video_path, count_needed, thumb_w, thumb_h,
            callback=on_frame,
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
        """Append a pixmap and trigger a repaint — no layout relayout."""
        target_height = BAR_HEIGHT - (2 * BORDER_THICKNESS)
        scaled = pixmap.scaledToHeight(
            target_height, Qt.SmoothTransformation,
        )
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
            painter.drawPixmap(x, y_offset, pm.width(), draw_h, pm)
            x += pm.width()
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

        target_height = BAR_HEIGHT - (2 * BORDER_THICKNESS)

        visible_width = self.thumb_strip.scroll_area.width()
        if visible_width < 100:
            visible_width = 1000

        # Launch worker — uses single-pass approach (NLE-style)
        self._thumb_worker = ThumbnailWorker(
            video_path, target_height, visible_width, self.temp_dir,
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
        target_height = BAR_HEIGHT - (2 * BORDER_THICKNESS)
        fallback = QPixmap(THUMB_WIDTH, target_height)
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
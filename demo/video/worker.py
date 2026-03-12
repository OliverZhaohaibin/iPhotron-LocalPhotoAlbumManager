"""ThumbnailWorker — background thread for timeline thumbnail generation.

Strategy hierarchy (fastest first):
  0. Disk cache (instant on repeated open)
  1. Contact-sheet via ffmpeg tile=Nx1 (one image, one process) — ~200 ms
  2. PyAV in-process extraction (zero subprocess overhead) — ~300-600 ms
  3. Single-pass GPU + keyframe-only pipe
  4. Single-pass keyframe-only pipe (no GPU)
  5. Single-pass full decode pipe
  6. Sliced multi-process extraction
  7. Parallel individual extraction (slowest fallback)
"""

from __future__ import annotations

import concurrent.futures
import os
import subprocess
import time

try:
    import av as _av_module
    HAS_PYAV = True
except ImportError:
    _av_module = None
    HAS_PYAV = False

from PySide6.QtCore import QThread, Signal

from config import (
    THUMB_WIDTH, MAX_FFMPEG_SLICES, FRAME_READ_BUFFER,
)
from probe import _get_video_info, _get_video_info_pyav
from extraction import (
    _run_contact_sheet,
    _split_strip_bgra,
    _extract_thumbnails_pyav,
    _extract_single_frame,
    _build_single_pass_cmd,
    _build_popen_priority_kwargs,
    _lower_process_priority,
)
from cache import cache_get, cache_put

# Try to import C-accelerated BGRA→RGB conversion
try:
    from _native import bgra_to_rgb as _c_bgra_to_rgb
except ImportError:
    _c_bgra_to_rgb = None


class ThumbnailWorker(QThread):
    """
    Background thread that generates timeline thumbnails.

    Primary strategy: contact-sheet via ffmpeg ``tile=Nx1`` generates a
    single horizontal strip in one ffmpeg process.  This is the fastest
    possible path (~200 ms for any video) because it avoids N process
    startups, N seeks, and N separate frame transfers.

    Falls back through progressively slower strategies if the primary
    one fails.
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
            finally:
                self._proc = None

    def run(self):
        try:
            t0 = time.perf_counter()

            # --- Probe video info (prefer PyAV, fallback to ffprobe) ---
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
            logical_thumb_w = scaled_width / max(self.dpr, 1.0)
            count_needed = int(self.visible_width / logical_thumb_w) + 2
            count_needed = max(count_needed, 5)
            count_needed = min(count_needed, 60)

            print(f"Video: {v_w}x{v_h}, Duration: {duration:.1f}s, "
                  f"Thumbnails: {count_needed} @ {thumb_w}x{target_h}, "
                  f"rotation={rotation}, vflip={vflip}, "
                  f"PyAV={'yes' if HAS_PYAV else 'no'}")

            fps_rate = count_needed / max(duration, 0.01)
            frame_size = thumb_w * target_h * 4

            # --- Strategy 0: Disk cache (instant) ---
            if not self._abort:
                cached = cache_get(self.video_path, target_h, count_needed)
                if cached is not None:
                    cw, ch, cn, cdata = cached
                    # Emit individual frames from cached RGB data
                    rgb_frame_sz = cw * ch * 3
                    for i in range(cn):
                        if self._abort:
                            break
                        offset = i * rgb_frame_sz
                        self.thumbnail_ready.emit(
                            ('pyav', cw, ch, cdata[offset:offset + rgb_frame_sz]),
                        )
                    elapsed = time.perf_counter() - t0
                    print(f"[thumbnail] Done in {elapsed:.3f}s (disk cache)")
                    return

            # --- Strategy 1: Contact-sheet (one process, one image) ---
            if not self._abort:
                if self._try_contact_sheet(
                    thumb_w, target_h, count_needed, fps_rate,
                ):
                    elapsed = time.perf_counter() - t0
                    print(f"[thumbnail] Done in {elapsed:.2f}s (contact-sheet)")
                    return

            # --- Strategy 2: PyAV in-process extraction ---
            if HAS_PYAV and not self._abort:
                if self._try_pyav(thumb_w, target_h, count_needed,
                                  rotation, vflip):
                    elapsed = time.perf_counter() - t0
                    print(f"[thumbnail] Done in {elapsed:.2f}s (PyAV)")
                    return

            # Strategy 3: Single-pass with GPU + keyframe-only
            if self._try_single_pass(
                thumb_w, target_h, count_needed, fps_rate, frame_size,
                hwaccel=True, keyframe_only=True,
            ):
                elapsed = time.perf_counter() - t0
                print(f"[thumbnail] Done in {elapsed:.2f}s "
                      f"(single-pass gpu+keyframe)")
                return

            # Strategy 4: Single-pass with keyframe-only, no GPU
            if self._try_single_pass(
                thumb_w, target_h, count_needed, fps_rate, frame_size,
                hwaccel=False, keyframe_only=True,
            ):
                elapsed = time.perf_counter() - t0
                print(f"[thumbnail] Done in {elapsed:.2f}s "
                      f"(single-pass keyframe)")
                return

            # Strategy 5: Single-pass without keyframe skip
            if self._try_single_pass(
                thumb_w, target_h, count_needed, fps_rate, frame_size,
                hwaccel=False, keyframe_only=False,
            ):
                elapsed = time.perf_counter() - t0
                print(f"[thumbnail] Done in {elapsed:.2f}s "
                      f"(single-pass full)")
                return

            # Strategy 6: Sliced multi-process extraction
            if self._try_sliced_single_pass(
                thumb_w, target_h, count_needed, duration,
                keyframe_only=True,
            ):
                elapsed = time.perf_counter() - t0
                print(f"[thumbnail] Done in {elapsed:.2f}s "
                      f"(sliced keyframe)")
                return

            # Strategy 7: Parallel individual extraction (slowest)
            self._fallback_parallel(
                thumb_w, target_h, count_needed, duration,
            )
            elapsed = time.perf_counter() - t0
            print(f"[thumbnail] Done in {elapsed:.2f}s (parallel fallback)")
        except Exception as e:
            self.error_occurred.emit(str(e))

    # -----------------------------------------------------------------
    # Contact-sheet strategy
    # -----------------------------------------------------------------

    def _try_contact_sheet(self, thumb_w, thumb_h, count_needed, fps_rate):
        """Generate all thumbnails as a single horizontal strip image.

        Uses ffmpeg ``tile=Nx1`` filter: one process, one container parse,
        one output buffer.  The strip is split into individual frames
        client-side using memoryview slicing.
        """
        print("[thumbnail] Trying contact-sheet")
        result = _run_contact_sheet(
            self.video_path, thumb_w, thumb_h, count_needed, fps_rate,
            keyframe_only=True,
        )
        if result is None:
            # Retry without keyframe-only (some containers have few keyframes)
            result = _run_contact_sheet(
                self.video_path, thumb_w, thumb_h, count_needed, fps_rate,
                keyframe_only=False,
            )
        if result is None:
            return False

        strip_w, strip_h, strip_data = result
        actual_count = strip_w // thumb_w

        # Split the strip into individual frame buffers and emit
        frames = _split_strip_bgra(strip_data, thumb_w, strip_h, actual_count)
        for frame_buf in frames:
            if self._abort:
                break
            self.thumbnail_ready.emit(
                ('pipe', thumb_w, strip_h, frame_buf),
            )

        # Cache as RGB for future instant load
        self._cache_results_bgra(thumb_w, strip_h, actual_count, frames)

        return actual_count > 0

    def _cache_results_bgra(self, thumb_w, thumb_h, count, bgra_frames):
        """Convert BGRA frames to RGB and store in disk cache."""
        try:
            n_pixels = thumb_w * thumb_h
            rgb_parts = []
            for buf in bgra_frames:
                if _c_bgra_to_rgb is not None:
                    rgb_parts.append(_c_bgra_to_rgb(buf, n_pixels))
                else:
                    # Pure Python BGRA → RGB conversion
                    mv = memoryview(buf)
                    rgb = bytearray(n_pixels * 3)
                    src_off = 0
                    dst_off = 0
                    for _ in range(n_pixels):
                        rgb[dst_off] = mv[src_off + 2]
                        rgb[dst_off + 1] = mv[src_off + 1]
                        rgb[dst_off + 2] = mv[src_off]
                        src_off += 4
                        dst_off += 3
                    rgb_parts.append(bytes(rgb))
            all_rgb = b"".join(rgb_parts)
            cache_put(self.video_path, thumb_w, thumb_h, count, all_rgb)
        except Exception as e:
            print(f"[cache] Store error: {e}")

    # -----------------------------------------------------------------
    # PyAV strategy
    # -----------------------------------------------------------------

    def _try_pyav(self, thumb_w, thumb_h, count_needed,
                  rotation=0, vflip=False):
        """Extract thumbnails using PyAV — zero subprocess overhead."""
        def on_frame(index, rgb_data, w, h):
            if not self._abort:
                self.thumbnail_ready.emit(('pyav', w, h, rgb_data))

        results = _extract_thumbnails_pyav(
            self.video_path, count_needed, thumb_w, thumb_h,
            callback=on_frame,
            rotation=rotation, vflip=vflip,
        )
        return len(results) > 0

    # -----------------------------------------------------------------
    # Single-pass strategies
    # -----------------------------------------------------------------

    def _try_single_pass(self, thumb_w, thumb_h, count_needed, fps_rate,
                         frame_size, hwaccel=True, keyframe_only=True):
        """Single ffmpeg process outputting continuous rawvideo BGRA stream."""
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
            _lower_process_priority(proc)
            self._proc = proc

            count = 0
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

    # -----------------------------------------------------------------
    # Sliced strategy
    # -----------------------------------------------------------------

    def _try_sliced_single_pass(self, thumb_w, thumb_h, count_needed,
                                duration, keyframe_only=True):
        """Distribute thumbnail extraction across 2-3 concurrent ffmpeg processes."""
        num_slices = min(MAX_FFMPEG_SLICES, max(1, (os.cpu_count() or 2) // 2))
        num_slices = min(num_slices, count_needed)
        if num_slices <= 1:
            return False

        frames_per_slice = count_needed // num_slices
        remainder = count_needed % num_slices
        slices = []
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
                _lower_process_priority(proc)
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

    # -----------------------------------------------------------------
    # Parallel fallback
    # -----------------------------------------------------------------

    def _fallback_parallel(self, thumb_w, target_h, count_needed, duration):
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

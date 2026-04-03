"""Background worker that generates timeline thumbnails for the trim bar.

This worker reuses the extraction strategy from ``demo/video``:

1. Disk cache of previously generated RGB strips.
2. ffmpeg contact-sheet extraction (tile=Nx1).
3. ffmpeg single-pass rawvideo pipe.
4. Per-frame fallback via the shared frame grabber.

When available, the contact-sheet strip is split through the demo's optional
native ``fast_thumb`` helper for the same behavior as the demo.
"""

from __future__ import annotations

import hashlib
import os
import struct
import subprocess
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QSize, Signal
from PySide6.QtGui import QImage

from ....errors import ExternalToolError
from ....utils.logging import get_logger
from ....utils.ffmpeg import probe_media, probe_video_rotation
from .video_frame_grabber import grab_video_frame
from .video_trim_native import split_strip_bgra, split_strip_bgra_to_rgb

_CACHE_DIR = Path.home() / ".iPhoto" / "cache" / "video_trim"
_FFMPEG_BELOW_NORMAL = 0x00004000
_LOGGER = get_logger().getChild("video_trim.worker")


class VideoTrimThumbnailSignals(QObject):
    """Signals emitted by :class:`VideoTrimThumbnailWorker`."""

    thumbnail = Signal(QImage, int)
    ready = Signal(object, int)
    error = Signal(int, str)
    finished = Signal(int)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)


class VideoTrimThumbnailWorker(QRunnable):
    """Generate representative thumbnails across a video's duration."""

    def __init__(
        self,
        source: Path,
        *,
        generation: int,
        duration_sec: float | None,
        target_height: int,
        target_width: int,
        count: int = 10,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._source = source
        self._generation = int(generation)
        self._duration_sec = duration_sec
        self._target_height = max(int(target_height), 48)
        self._target_width = max(int(target_width), 64)
        self._count = max(int(count), 1)
        self.signals = VideoTrimThumbnailSignals()

    def run(self) -> None:  # pragma: no cover - worker thread
        try:
            duration_sec = self._resolved_duration_sec()
            self._diag(
                "start",
                source=self._source,
                generation=self._generation,
                duration_sec=duration_sec,
                target=f"{self._target_width}x{self._target_height}",
                count=self._count,
            )
            images = self._load_cached_images()
            if not images:
                images = self._extract_contact_sheet(duration_sec)
            if not images:
                images = self._extract_single_pass_pipe(duration_sec)
            if not images:
                images = self._extract_individual_frames(duration_sec)
            if images:
                self._diag("success", generation=self._generation, image_count=len(images))
                self.signals.ready.emit(images, self._generation)
            else:
                self._diag("empty", generation=self._generation)
                self.signals.error.emit(self._generation, "Timeline thumbnails were empty")
        except Exception as exc:
            self._diag("exception", generation=self._generation, error=exc)
            self.signals.error.emit(self._generation, str(exc))
        finally:
            self.signals.finished.emit(self._generation)

    def _sample_times(self, duration: float | None = None) -> list[float]:
        if duration is None:
            duration = self._duration_sec
        if duration is None or duration <= 0.0:
            return []
        if self._count == 1:
            return [duration * 0.5]
        segment = duration / float(self._count)
        return [min(duration, (index + 0.5) * segment) for index in range(self._count)]

    def _resolved_duration_sec(self) -> float | None:
        duration = self._duration_sec
        if duration is not None and duration > 0.0:
            return duration
        duration = self._probe_duration_sec()
        self._duration_sec = duration
        return duration

    def _probe_duration_sec(self) -> float | None:
        self._diag("probe_begin", generation=self._generation, source=self._source)
        try:
            meta = probe_media(self._source)
        except ExternalToolError as exc:
            self._diag("probe_fail", generation=self._generation, error=exc)
            return None

        candidates: list[object] = []
        format_info = meta.get("format", {})
        if isinstance(format_info, dict):
            candidates.append(format_info.get("duration"))

        streams = meta.get("streams", [])
        if isinstance(streams, list):
            for stream in streams:
                if not isinstance(stream, dict):
                    continue
                if stream.get("codec_type") != "video":
                    continue
                candidates.append(stream.get("duration"))
                break

        for raw in candidates:
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if value > 0.0:
                self._diag("probe_ok", generation=self._generation, duration_sec=round(value, 3))
                return value

        self._diag("probe_empty", generation=self._generation)
        return None

    def _load_cached_images(self) -> list[QImage]:
        thumb_w, thumb_h = self._contact_sheet_size()
        cached = _cache_get(self._source, thumb_h, self._count)
        if cached is None:
            self._diag("cache_miss", generation=self._generation, thumb_h=thumb_h, count=self._count)
            return []
        cached_w, cached_h, cached_count, rgb_data = cached
        self._diag(
            "cache_hit",
            generation=self._generation,
            cached_size=f"{cached_w}x{cached_h}",
            cached_count=cached_count,
            rgb_bytes=len(rgb_data),
        )
        frame_bytes = cached_w * cached_h * 3
        images: list[QImage] = []
        for index in range(cached_count):
            start = index * frame_bytes
            frame = rgb_data[start:start + frame_bytes]
            image = _qimage_from_rgb(frame, cached_w, cached_h)
            if image is None:
                continue
            images.append(image)
            self.signals.thumbnail.emit(QImage(image), self._generation)
        return images

    def _extract_contact_sheet(self, duration: float | None) -> list[QImage]:
        if duration is None or duration <= 0.0:
            self._diag("contact_skip", generation=self._generation, reason="invalid_duration", duration=duration)
            return []

        thumb_w, thumb_h = self._contact_sheet_size()
        fps_rate = self._count / max(duration, 0.01)
        self._diag(
            "contact_begin",
            generation=self._generation,
            thumb_size=f"{thumb_w}x{thumb_h}",
            fps_rate=round(fps_rate, 6),
        )
        for keyframe_only in (True, False):
            result = _run_contact_sheet(
                self._source,
                thumb_w,
                thumb_h,
                self._count,
                fps_rate,
                keyframe_only=keyframe_only,
            )
            if result is None:
                self._diag(
                    "contact_fail",
                    generation=self._generation,
                    keyframe_only=keyframe_only,
                )
                continue

            strip_w, strip_h, strip_data = result
            actual_count = max(1, strip_w // max(thumb_w, 1))
            self._diag(
                "contact_ok",
                generation=self._generation,
                keyframe_only=keyframe_only,
                strip_size=f"{strip_w}x{strip_h}",
                strip_bytes=len(strip_data),
                actual_count=actual_count,
            )
            frames = split_strip_bgra(strip_data, thumb_w, strip_h, actual_count)
            images: list[QImage] = []
            for frame in frames:
                image = _qimage_from_bgra(frame, thumb_w, strip_h)
                if image is None:
                    continue
                images.append(image)
                self.signals.thumbnail.emit(QImage(image), self._generation)

            if images:
                try:
                    _cache_put(
                        self._source,
                        thumb_w,
                        strip_h,
                        actual_count,
                        split_strip_bgra_to_rgb(strip_data, thumb_w, strip_h, actual_count),
                    )
                    self._diag("cache_write", generation=self._generation, actual_count=actual_count)
                except Exception:
                    self._diag("cache_write_fail", generation=self._generation)
                    pass
                return images

        return []

    def _extract_single_pass_pipe(self, duration: float | None) -> list[QImage]:
        if duration is None or duration <= 0.0:
            self._diag("pipe_skip", generation=self._generation, reason="invalid_duration", duration=duration)
            return []

        thumb_w, thumb_h = self._contact_sheet_size()
        fps_rate = self._count / max(duration, 0.01)
        frame_size = thumb_w * thumb_h * 4

        for hwaccel, keyframe_only in ((True, True), (False, True), (False, False)):
            self._diag(
                "pipe_begin",
                generation=self._generation,
                hwaccel=hwaccel,
                keyframe_only=keyframe_only,
                thumb_size=f"{thumb_w}x{thumb_h}",
            )
            command = _build_single_pass_command(
                self._source,
                thumb_w,
                thumb_h,
                fps_rate,
                hwaccel=hwaccel,
                keyframe_only=keyframe_only,
            )
            images: list[QImage] = []
            process = None
            try:
                startupinfo, popen_kwargs = _build_popen_priority_kwargs()
                process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    bufsize=frame_size,
                    startupinfo=startupinfo,
                    **popen_kwargs,
                )
                _lower_process_priority(process)

                while len(images) < self._count:
                    if process.stdout is None:
                        break
                    frame = process.stdout.read(frame_size)
                    if len(frame) < frame_size:
                        break
                    image = _qimage_from_bgra(frame, thumb_w, thumb_h)
                    if image is None:
                        continue
                    images.append(image)
                    self.signals.thumbnail.emit(QImage(image), self._generation)

                if process.stdout is not None:
                    process.stdout.close()
                if process.stderr is not None:
                    process.stderr.close()
                process.wait(timeout=10)
            except Exception as exc:
                self._diag(
                    "pipe_exception",
                    generation=self._generation,
                    hwaccel=hwaccel,
                    keyframe_only=keyframe_only,
                    error=exc,
                )
                images = []
            finally:
                if process is not None and process.poll() is None:
                    try:
                        process.kill()
                    except OSError:
                        pass

            if images:
                self._diag(
                    "pipe_ok",
                    generation=self._generation,
                    hwaccel=hwaccel,
                    keyframe_only=keyframe_only,
                    image_count=len(images),
                )
                return images
            self._diag(
                "pipe_empty",
                generation=self._generation,
                hwaccel=hwaccel,
                keyframe_only=keyframe_only,
            )

        return []

    def _extract_individual_frames(self, duration: float | None) -> list[QImage]:
        images: list[QImage] = []
        sample_times = self._sample_times(duration)
        self._diag(
            "frame_grab_begin",
            generation=self._generation,
            sample_count=len(sample_times),
        )
        if not sample_times:
            image = grab_video_frame(
                self._source,
                QSize(self._target_width, self._target_height),
                still_image_time=None,
                duration=duration,
            )
            if image is not None and not image.isNull():
                copied = QImage(image)
                images.append(copied)
                self.signals.thumbnail.emit(QImage(copied), self._generation)
                self._diag("frame_grab_single_ok", generation=self._generation)
            else:
                self._diag("frame_grab_single_empty", generation=self._generation)
            return images

        for sample_time in sample_times:
            image = grab_video_frame(
                self._source,
                QSize(self._target_width, self._target_height),
                still_image_time=sample_time,
                duration=duration,
            )
            if image is None or image.isNull():
                self._diag(
                    "frame_grab_empty",
                    generation=self._generation,
                    sample_time=round(sample_time, 3),
                )
                continue
            copied = QImage(image)
            images.append(copied)
            self.signals.thumbnail.emit(QImage(copied), self._generation)
        self._diag("frame_grab_done", generation=self._generation, image_count=len(images))
        return images

    def _contact_sheet_size(self) -> tuple[int, int]:
        rotation, raw_w, raw_h = probe_video_rotation(self._source)
        if rotation in {90, 270}:
            display_w, display_h = raw_h, raw_w
        else:
            display_w, display_h = raw_w, raw_h

        if display_w <= 0 or display_h <= 0:
            return (_make_even(self._target_width), _make_even(self._target_height))

        scale = min(self._target_width / float(display_w), self._target_height / float(display_h))
        if scale <= 0.0:
            return (_make_even(self._target_width), _make_even(self._target_height))
        return (
            _make_even(max(int(display_w * scale), 2)),
            _make_even(max(int(display_h * scale), 2)),
        )

    def _diag(self, stage: str, **fields: object) -> None:
        parts = [f"{key}={value}" for key, value in fields.items()]
        message = f"[video-trim-worker] {stage}"
        if parts:
            message += " | " + ", ".join(parts)
        print(message)
        _LOGGER.warning(message)


def _qimage_from_bgra(data: bytes, width: int, height: int) -> QImage | None:
    image = QImage(data, width, height, width * 4, QImage.Format.Format_ARGB32).copy()
    if image.isNull():
        return None
    return image


def _qimage_from_rgb(data: bytes, width: int, height: int) -> QImage | None:
    image = QImage(data, width, height, width * 3, QImage.Format.Format_RGB888).copy()
    if image.isNull():
        return None
    return image


def _build_single_pass_command(
    source: Path,
    thumb_w: int,
    thumb_h: int,
    fps_rate: float,
    *,
    hwaccel: bool,
    keyframe_only: bool,
) -> list[str]:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-probesize",
        "32768",
        "-analyzeduration",
        "0",
        "-fflags",
        "+nobuffer",
    ]
    if hwaccel:
        command += ["-hwaccel", "auto"]
    if keyframe_only:
        command += ["-skip_frame", "nokey"]
    command += [
        "-i",
        str(source.absolute()),
        "-vf",
        f"fps={fps_rate:.6f},scale={thumb_w}:{thumb_h},format=bgra",
        "-an",
        "-f",
        "rawvideo",
        "-vsync",
        "vfr",
        "pipe:1",
    ]
    return command


def _run_contact_sheet(
    source: Path,
    thumb_w: int,
    thumb_h: int,
    count: int,
    fps_rate: float,
    *,
    keyframe_only: bool,
) -> tuple[int, int, bytes] | None:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-probesize",
        "32768",
        "-analyzeduration",
        "0",
        "-fflags",
        "+nobuffer",
        "-hwaccel",
        "auto",
    ]
    if keyframe_only:
        command += ["-skip_frame", "nokey"]
    command += [
        "-i",
        str(source.absolute()),
        "-vf",
        ",".join(
            [
                f"fps={fps_rate:.6f}",
                f"scale={thumb_w}:{thumb_h}",
                f"tile={count}x1",
                "format=bgra",
            ]
        ),
        "-frames:v",
        "1",
        "-an",
        "-f",
        "rawvideo",
        "-vsync",
        "vfr",
        "pipe:1",
    ]

    try:
        startupinfo, popen_kwargs = _build_popen_priority_kwargs()
        process = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            startupinfo=startupinfo,
            **popen_kwargs,
        )
    except OSError:
        return None

    if process.returncode != 0 or not process.stdout:
        return None

    data = process.stdout
    expected_size = thumb_w * thumb_h * count * 4
    if len(data) < thumb_w * thumb_h * 4:
        return None
    if len(data) >= expected_size:
        return (thumb_w * count, thumb_h, data[:expected_size])

    actual_count = len(data) // (thumb_w * thumb_h * 4)
    if actual_count <= 0:
        return None
    actual_w = thumb_w * actual_count
    actual_size = actual_w * thumb_h * 4
    if len(data) < actual_size:
        return None
    return (actual_w, thumb_h, data[:actual_size])


def _build_popen_priority_kwargs() -> tuple[subprocess.STARTUPINFO | None, dict]:
    startupinfo = None
    popen_kwargs: dict[str, int] = {}
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        popen_kwargs["creationflags"] = _FFMPEG_BELOW_NORMAL
    return startupinfo, popen_kwargs


def _lower_process_priority(process: subprocess.Popen[bytes]) -> None:
    if os.name != "nt" and process.pid:
        try:
            os.setpriority(os.PRIO_PROCESS, process.pid, 10)
        except (OSError, PermissionError):
            pass


def _make_even(value: int) -> int:
    clamped = max(int(value), 2)
    if clamped % 2 == 1:
        clamped -= 1
    return max(clamped, 2)


def _cache_get(source: Path, thumb_h: int, count: int) -> tuple[int, int, int, bytes] | None:
    key = _cache_key(source, thumb_h, count)
    if not key:
        return None
    path = _CACHE_DIR / key
    if not path.exists():
        return None
    try:
        with path.open("rb") as handle:
            header = handle.read(12)
            if len(header) < 12:
                return None
            width, height, actual_count = struct.unpack("<III", header)
            expected = width * height * 3 * actual_count
            data = handle.read(expected)
            if len(data) != expected:
                return None
            return (width, height, actual_count, data)
    except OSError:
        return None


def _cache_put(source: Path, thumb_w: int, thumb_h: int, count: int, data: bytes) -> None:
    key = _cache_key(source, thumb_h, count)
    if not key:
        return
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = _CACHE_DIR / key
        with path.open("wb") as handle:
            handle.write(struct.pack("<III", thumb_w, thumb_h, count))
            handle.write(data)
    except OSError:
        return


def _cache_key(source: Path, thumb_h: int, count: int) -> str:
    try:
        stat = source.stat()
    except OSError:
        return ""
    blob = f"{source.resolve()}\x00{stat.st_size}\x00{stat.st_mtime_ns}\x00{thumb_h}\x00{count}"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


__all__ = ["VideoTrimThumbnailWorker", "VideoTrimThumbnailSignals"]

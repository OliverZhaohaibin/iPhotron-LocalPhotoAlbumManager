"""Lightweight wrappers around the ``ffmpeg`` toolchain."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, TYPE_CHECKING

from ..errors import ExternalToolError

if TYPE_CHECKING:
    from PIL import Image

try:  # pragma: no cover - optional dependency detection
    import av  # type: ignore
except Exception:  # pragma: no cover - PyAV not available or broken
    av = None  # type: ignore[assignment]

try:  # pragma: no cover - optional dependency detection
    import cv2  # type: ignore
except Exception:  # pragma: no cover - OpenCV not available or broken
    cv2 = None  # type: ignore[assignment]

_FFMPEG_LOG_LEVEL = "error"


def _run_command(command: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
    """Execute *command* and return the completed process."""

    # Define startupinfo to hide the window on Windows
    startupinfo = None
    if os.name == 'nt':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE

    try:
        process = subprocess.run(
            list(command),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            startupinfo=startupinfo,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0,
        )
    except FileNotFoundError as exc:  # pragma: no cover - depends on environment
        raise ExternalToolError("ffmpeg executable not found on PATH") from exc
    return process


def extract_frame_with_pyav(
    source: Path,
    *,
    at: Optional[float] = None,
    scale: Optional[tuple[int, int]] = None,
) -> Optional["Image.Image"]:
    """Return a still frame extracted from *source* using PyAV.

    This method decodes directly to memory, avoiding process overhead.
    Returns a PIL Image on success, or ``None`` if PyAV is unavailable or
    decoding fails.

    Parameters
    ----------
    at : Optional[float], optional
        Timestamp in seconds at which to extract the frame. If not specified,
        the first frame is used.
    scale : Optional[tuple[int, int]], optional
        Optional tuple of (max_width, max_height) specifying the maximum
        dimensions for the output image. The aspect ratio is preserved and
        the image is resized to fit within the given box if necessary.
    """
    if av is None:
        return None

    try:
        with av.open(str(source)) as container:
            if not container.streams.video:
                return None
            stream = container.streams.video[0]
            stream.thread_type = 'AUTO'  # Use multi-threading if available

            target_pts = 0
            if at is not None and at > 0:
                # Seek to the keyframe before the timestamp
                # time_base is usually 1/timescale
                target_pts = int(at / stream.time_base)
                container.seek(target_pts, stream=stream)

            for frame in container.decode(stream):
                # We seeked to the nearest keyframe, so we may need to decode
                # forward to reach the exact target time.
                if frame.pts is None or frame.pts < target_pts:
                    continue

                # Once we reach or pass the target, use this frame
                image = frame.to_image()

                # Handle scaling if requested
                if (
                    scale is not None
                    and scale[0] > 0
                    and scale[1] > 0
                ):
                    max_width, max_height = scale
                    # Calculate new size preserving aspect ratio, ensuring it fits in box
                    # This logic mirrors the ffmpeg 'force_original_aspect_ratio=decrease'
                    w, h = image.size
                    ratio = min(max_width / w, max_height / h)

                    if ratio < 1.0:
                        # Calculate new size preserving aspect ratio, ensuring it fits in box
                        # Use max(2, trunc(x/2)*2) to match ffmpeg's behavior and ensure even dimensions
                        new_width = max(2, int((w * ratio) / 2) * 2)
                        new_height = max(2, int((h * ratio) / 2) * 2)

                        image = image.resize((new_width, new_height), resample=3) # LANCZOS = 3 (usually)

                return image

            return None

    except (av.FFmpegError, ValueError, IndexError, Exception):
        # Fallback to other methods if PyAV fails for any reason
        return None


def extract_video_frame(
    source: Path,
    *,
    at: Optional[float] = None,
    scale: Optional[tuple[int, int]] = None,
    format: str = "jpeg",
) -> bytes:
    """Return a still frame extracted from *source*.

    Parameters
    ----------
    source:
        Path to the input video file.
    at:
        Timestamp in seconds to sample. When ``None`` the first frame is used.
    scale:
        Optional ``(width, height)`` hint used to scale the output frame while
        preserving aspect ratio.
    format:
        Output image format. ``"jpeg"`` is used by default because Qt decoders
        handle it more reliably on Windows. ``"png"`` remains available for
        callers that prefer lossless output.
    """

    fmt = format.lower()
    if fmt not in {"png", "jpeg"}:
        raise ValueError("format must be either 'png' or 'jpeg'")

    try:
        return _extract_with_ffmpeg(source, at=at, scale=scale, format=fmt)
    except ExternalToolError as exc:
        fallback = _extract_with_opencv(source, at=at, scale=scale, format=fmt)
        if fallback is not None:
            return fallback
        raise exc


def _extract_with_ffmpeg(
    source: Path,
    *,
    at: Optional[float],
    scale: Optional[tuple[int, int]],
    format: str,
) -> bytes:
    codec = "png" if format == "png" else "mjpeg"

    command: list[str] = [
        "ffmpeg",
        "-hwaccel",
        "auto",
        "-hide_banner",
        "-loglevel",
        _FFMPEG_LOG_LEVEL,
        "-nostdin",
        "-y",
    ]
    if at is not None:
        command += ["-ss", f"{max(at, 0):.3f}"]
    # Security: Ensure absolute path to prevent argument injection if filename starts with '-'
    command += [
        "-i",
        str(source.absolute()),
        "-an",
        "-frames:v",
        "1",
        "-vsync",
        "0",
    ]
    filters: list[str] = []
    if scale is not None:
        width, height = scale
        if width > 0 and height > 0:
            # Note: ffmpeg syntax for force_original_aspect_ratio requires specific handling
            # In complex filtergraphs, we just construct the scale filter carefully.
            # Using 'decrease' ensures the output fits within the bounding box.
            filters.append(
                "scale='min({w},iw)':'min({h},ih)':force_original_aspect_ratio=decrease".format(
                    w=width,
                    h=height,
                )
            )
    if format == "jpeg":
        if not filters:
            filters.append("scale=iw:ih")
        # Ensure dimensions are even for MJPEG
        filters.append("scale='max(2,trunc(iw/2)*2)':'max(2,trunc(ih/2)*2)'")
    if format == "png":
        filters.append("format=rgba")
    else:
        filters.append("format=yuv420p")
    if filters:
        command += ["-vf", ",".join(filters)]
    command += ["-f", "image2", "-vcodec", codec]
    if format == "jpeg":
        command += ["-q:v", "2"]

    command.append("pipe:1")
    process = _run_command(command)

    if process.returncode != 0 or not process.stdout:
        stderr = process.stderr.decode("utf-8", "ignore").strip()
        raise ExternalToolError(
            f"ffmpeg failed to extract frame from {source}: {stderr or 'unknown error'}"
        )
    return process.stdout


def _extract_with_opencv(
    source: Path,
    *,
    at: Optional[float],
    scale: Optional[tuple[int, int]],
    format: str,
) -> Optional[bytes]:
    if cv2 is None:
        return None

    try:
        # Security: Ensure absolute path to prevent argument injection if filename starts with '-'
        capture = cv2.VideoCapture(str(source.absolute()))
    except Exception:
        return None

    is_opened = True
    try:
        is_opened = bool(capture.isOpened())
    except Exception:
        is_opened = False
    if not is_opened:
        try:
            capture.release()
        except Exception:
            pass
        return None

    try:
        if at is not None and at >= 0:
            seconds = max(at, 0.0)
            try:
                positioned = capture.set(getattr(cv2, "CAP_PROP_POS_MSEC", 0), seconds * 1000.0)
            except Exception:
                positioned = False
            if not positioned:
                try:
                    fps = capture.get(getattr(cv2, "CAP_PROP_FPS", 5.0))
                except Exception:
                    fps = 0.0
                if fps and fps > 0:
                    try:
                        capture.set(
                            getattr(cv2, "CAP_PROP_POS_FRAMES", 1),
                            max(int(round(fps * seconds)), 0),
                        )
                    except Exception:
                        pass
        ok, frame = capture.read()
    except Exception:
        return None
    finally:
        try:
            capture.release()
        except Exception:
            pass

    if not ok or frame is None:
        return None

    try:
        height, width = frame.shape[:2]
    except Exception:
        return None

    target_frame = frame
    if (
        scale is not None
        and width > 0
        and height > 0
        and scale[0] > 0
        and scale[1] > 0
    ):
        max_width, max_height = scale
        ratio = min(max_width / width, max_height / height)
        if ratio < 1.0:
            new_width = max(int(width * ratio), 1)
            new_height = max(int(height * ratio), 1)
            if format == "jpeg":
                if new_width % 2 == 1 and new_width > 1:
                    new_width -= 1
                if new_height % 2 == 1 and new_height > 1:
                    new_height -= 1
            interpolation = getattr(cv2, "INTER_AREA", 3)
            try:
                target_frame = cv2.resize(target_frame, (new_width, new_height), interpolation=interpolation)
            except Exception:
                return None

    extension = ".png" if format == "png" else ".jpg"
    params: list[int] = []
    if format == "jpeg":
        jpeg_quality = getattr(cv2, "IMWRITE_JPEG_QUALITY", None)
        if jpeg_quality is not None:
            params = [int(jpeg_quality), 92]

    try:
        success, buffer = cv2.imencode(extension, target_frame, params)
    except Exception:
        return None

    if not success:
        return None

    try:
        return bytes(buffer)
    except Exception:
        return None

def probe_media(source: Path) -> Dict[str, Any]:
    """Return ffprobe metadata for *source*.

    The JSON structure mirrors ffprobe's ``show_format`` and ``show_streams``
    output. ``ExternalToolError`` is raised when the toolchain is unavailable or
    returns an error.
    """

    command = [
        "ffprobe",
        "-hide_banner",
        "-loglevel",
        _FFMPEG_LOG_LEVEL,
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(source.absolute()),
    ]

    process = _run_command(command)
    if process.returncode != 0 or not process.stdout:
        stderr = process.stderr.decode("utf-8", "ignore").strip()
        raise ExternalToolError(
            f"ffprobe failed to inspect {source}: {stderr or 'unknown error'}"
        )
    try:
        return json.loads(process.stdout.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ExternalToolError("ffprobe returned invalid JSON output") from exc

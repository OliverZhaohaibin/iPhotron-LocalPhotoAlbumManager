"""Export engine for rendering and saving assets."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any

from PySide6.QtGui import QImage, QTransform

from ..application.ports import EditServicePort
from ..errors import ExternalToolError
from ..io import sidecar
from ..media_classifier import VIDEO_EXTENSIONS
from ..utils import image_loader
from ..utils.ffmpeg import probe_media, probe_video_rotation
from .color_resolver import compute_color_statistics
from .filters.facade import apply_adjustments
from .geometry import apply_geometry_and_crop
from .raw_processor import RAW_EXTENSIONS

_LOGGER = logging.getLogger(__name__)
_OPTIONAL_MODULE_UNSET = object()
av: Any = _OPTIONAL_MODULE_UNSET


def _load_av() -> Any | None:
    """Import PyAV only when edited video export needs frame decoding."""

    global av
    if av is None:
        return None
    if av is not _OPTIONAL_MODULE_UNSET:
        return av
    try:  # pragma: no cover - optional dependency
        import av as imported_av  # type: ignore
    except Exception:  # pragma: no cover - optional dependency unavailable/broken
        av = None
        return None
    av = imported_av
    return imported_av

# Mapping of user-facing export format names to the Qt format string and file
# suffix used when saving rendered images.  This is the single source of truth
# for supported export formats referenced by the settings schema.
EXPORT_FORMATS: dict[str, tuple[str, str]] = {
    "jpg":  ("JPEG", ".jpg"),
    "png":  ("PNG",  ".png"),
    "tiff": ("TIFF", ".tiff"),
}

DEFAULT_EXPORT_FORMAT = "jpg"


def render_image(path: Path, *, edit_service: EditServicePort | None = None) -> QImage | None:
    """Render the asset at *path* with adjustments applied."""

    # 2. Load original image
    image = image_loader.load_qimage(path)
    if image is None or image.isNull():
        return None

    if edit_service is not None:
        state = edit_service.describe_adjustments(
            path,
            color_stats=compute_color_statistics(image),
        )
        raw_adjustments = state.raw_adjustments
        resolved_adjustments = state.resolved_adjustments
    else:
        raw_adjustments = sidecar.load_adjustments(path)
        resolved_adjustments = sidecar.resolve_render_adjustments(raw_adjustments)

    if not raw_adjustments:
        # Prompt implies we only render if adjustments exist (Case A).
        # If this function is called, caller expects rendering.
        return None

    # 3. Apply Filters
    filtered_image = apply_adjustments(image, resolved_adjustments)

    # 4. Apply Geometry
    cx = _clamp(float(raw_adjustments.get("Crop_CX", 0.5)))
    cy = _clamp(float(raw_adjustments.get("Crop_CY", 0.5)))
    w = _clamp(float(raw_adjustments.get("Crop_W", 1.0)))
    h = _clamp(float(raw_adjustments.get("Crop_H", 1.0)))

    # Constrain crop to image bounds
    half_w = w * 0.5
    half_h = h * 0.5
    cx = max(half_w, min(1.0 - half_w, cx))
    cy = max(half_h, min(1.0 - half_h, cy))

    img_w = filtered_image.width()
    img_h = filtered_image.height()

    rect_w = int(round(w * img_w))
    rect_h = int(round(h * img_h))
    rect_left = int(round((cx - half_w) * img_w))
    rect_top = int(round((cy - half_h) * img_h))

    # Clamp pixels
    rect_left = max(0, rect_left)
    rect_top = max(0, rect_top)
    rect_w = min(rect_w, img_w - rect_left)
    rect_h = min(rect_h, img_h - rect_top)

    if rect_w > 0 and rect_h > 0:
        filtered_image = filtered_image.copy(rect_left, rect_top, rect_w, rect_h)

    # Flip Horizontal
    if bool(raw_adjustments.get("Crop_FlipH", False)):
        filtered_image = filtered_image.mirrored(True, False)

    # Rotate 90
    rotate_steps = int(float(raw_adjustments.get("Crop_Rotate90", 0.0))) % 4
    if rotate_steps > 0:
        transform = QTransform().rotate(rotate_steps * 90)
        filtered_image = filtered_image.transformed(transform)

    return filtered_image


def render_video(
    path: Path,
    destination: Path,
    *,
    edit_service: EditServicePort | None = None,
) -> bool:
    """Render *path* to *destination* as MP4 with trim and adjustments applied."""

    av_module = _load_av()
    if av_module is None:
        _LOGGER.error("Video export requires PyAV for frame decoding")
        return False

    try:
        probe = probe_media(path)
    except ExternalToolError:
        _LOGGER.exception("Failed to probe video metadata for %s", path)
        return False

    duration_sec = probe_duration_seconds(probe)
    if edit_service is not None:
        initial_state = edit_service.describe_adjustments(
            path,
            duration_hint=duration_sec,
        )
        raw_adjustments = initial_state.raw_adjustments
    else:
        raw_adjustments = sidecar.load_adjustments(path)
    trim_in_sec, trim_out_sec = sidecar.normalise_video_trim(raw_adjustments, duration_sec)
    rotation_cw, _, _ = probe_video_rotation(path)

    try:
        with av_module.open(str(path)) as container:
            if not container.streams.video:
                return False
            stream = container.streams.video[0]
            fps = _probe_frame_rate(probe, stream)
            frame_iterator = _iter_export_frames(
                container,
                stream,
                trim_in_sec=trim_in_sec,
                trim_out_sec=trim_out_sec,
                rotation_cw=rotation_cw,
            )
            first_source = next(frame_iterator, None)
            if first_source is None:
                return False

            try:
                color_stats = compute_color_statistics(first_source)
            except Exception:
                _LOGGER.exception("Failed to compute video export color statistics")
                color_stats = None
            if edit_service is not None:
                resolved_adjustments = edit_service.describe_adjustments(
                    path,
                    duration_hint=duration_sec,
                    color_stats=color_stats,
                ).resolved_adjustments
            else:
                resolved_adjustments = sidecar.resolve_render_adjustments(
                    raw_adjustments,
                    color_stats=color_stats,
                )

            first_rendered = _render_video_frame(first_source, raw_adjustments, resolved_adjustments, color_stats)
            if first_rendered is None or first_rendered.isNull():
                return False
            first_rendered = _ensure_even_video_frame(first_rendered)
            if first_rendered.isNull():
                return False

            encoder = _start_video_encoder(
                source=path,
                destination=destination,
                width=first_rendered.width(),
                height=first_rendered.height(),
                fps=fps,
                trim_in_sec=trim_in_sec,
                trim_out_sec=trim_out_sec,
            )
            try:
                _write_rgba_frame(encoder, first_rendered)
                for frame_image in frame_iterator:
                    rendered = _render_video_frame(
                        frame_image,
                        raw_adjustments,
                        resolved_adjustments,
                        color_stats,
                    )
                    if rendered is None or rendered.isNull():
                        continue
                    rendered = _ensure_even_video_frame(rendered)
                    if rendered.isNull():
                        continue
                    _write_rgba_frame(encoder, rendered)
            finally:
                stderr = _finalise_video_encoder(encoder)

    except Exception:
        _LOGGER.exception("Video export failed for %s", path)
        try:
            destination.unlink(missing_ok=True)
        except OSError:
            pass
        return False

    if stderr:
        _LOGGER.debug("ffmpeg video export stderr for %s: %s", path, stderr)
    return True


def _clamp(val: float) -> float:
    return max(0.0, min(1.0, val))


def get_unique_destination(destination: Path) -> Path:
    """Return *destination* or a variant with a counter if it exists."""
    if not destination.exists():
        return destination

    parent = destination.parent
    stem = destination.stem
    suffix = destination.suffix
    counter = 1
    while True:
        candidate = parent / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def resolve_export_path(source_path: Path, export_root: Path, library_root: Path) -> Path:
    """Return the destination path mirroring the library structure."""
    try:
        relative = source_path.parent.relative_to(library_root)
    except ValueError:
        # Fallback if source is not under library root
        relative = Path(source_path.parent.name)

    return export_root / relative / source_path.name


def export_asset(
    source_path: Path,
    export_root: Path,
    library_root: Path,
    export_format: str = DEFAULT_EXPORT_FORMAT,
    *,
    edit_service: EditServicePort | None = None,
) -> bool:
    """Export the asset at *source_path* to *export_root* mirroring directory structure.

    Parameters
    ----------
    export_format:
        One of ``"jpg"``, ``"png"``, ``"tiff"``.  Controls the output format for
        rendered images (edited or RAW).  Ignored when copying unedited raster
        or video files.

    Returns True if successful.
    """
    try:
        destination_path = resolve_export_path(source_path, export_root, library_root)
        destination_dir = destination_path.parent
        destination_dir.mkdir(parents=True, exist_ok=True)

        is_video = source_path.suffix.lower() in VIDEO_EXTENSIONS
        is_raw = source_path.suffix.lower() in RAW_EXTENSIONS
        has_sidecar = (
            edit_service.sidecar_exists(source_path)
            if edit_service is not None
            else sidecar.sidecar_path_for_asset(source_path).exists()
        )
        raw_adjustments = (
            edit_service.read_adjustments(source_path)
            if is_video and has_sidecar and edit_service is not None
            else (sidecar.load_adjustments(source_path) if is_video and has_sidecar else {})
        )
        video_duration = None
        if is_video and has_sidecar:
            try:
                video_duration = probe_duration_seconds(probe_media(source_path))
            except ExternalToolError:
                video_duration = None

        qt_fmt, suffix = EXPORT_FORMATS.get(export_format, EXPORT_FORMATS[DEFAULT_EXPORT_FORMAT])

        # RAW files always need rendering because they cannot be opened by
        # standard image viewers.  Edited raster images are also rendered.
        should_render = (not is_video) and (has_sidecar or is_raw)
        should_render_video = is_video and has_sidecar and sidecar.video_has_visible_edits(raw_adjustments, video_duration)

        if should_render:
            image = render_image(source_path, edit_service=edit_service)
            if image is None and is_raw:
                # render_image returns None when there are no sidecar adjustments;
                # for RAW we still need to produce a viewable file.
                image = image_loader.load_qimage(source_path)
            if image is not None:
                final_dest = destination_path.with_suffix(suffix)
                final_dest = get_unique_destination(final_dest)
                image.save(str(final_dest), qt_fmt, 100)
                return True
            else:
                _LOGGER.error(
                    "Failed to render image for %s; skipping export",
                    source_path,
                )
                return False

        if should_render_video:
            final_dest = destination_path.with_suffix(".mp4")
            final_dest = get_unique_destination(final_dest)
            return render_video(source_path, final_dest, edit_service=edit_service)

        # Case B: Unedited raster or Video -> Copy
        final_dest = get_unique_destination(destination_path)
        shutil.copy2(source_path, final_dest)
        return True

    except Exception:
        _LOGGER.exception("Export failed for %s", source_path)
        return False


def probe_duration_seconds(metadata: dict) -> float | None:
    fmt = metadata.get("format", {}) if isinstance(metadata, dict) else {}
    if isinstance(fmt, dict):
        # Preferred: format.duration is already expressed in seconds.
        duration = _coerce_float(fmt.get("duration"))
        if duration and duration > 0.0:
            return duration
        # Secondary: format.tags["DURATION"] (HH:MM:SS, common in Matroska).
        fmt_tags = fmt.get("tags")
        if isinstance(fmt_tags, dict):
            tag_dur = _parse_hhmmss_duration(fmt_tags.get("DURATION"))
            if tag_dur is not None and tag_dur > 0.0:
                return tag_dur
    streams = metadata.get("streams", []) if isinstance(metadata, dict) else []
    if not isinstance(streams, list):
        return None
    for stream in streams:
        if not isinstance(stream, dict) or stream.get("codec_type") != "video":
            continue
        # stream["duration"] is already expressed in seconds in ffprobe JSON.
        stream_duration = _coerce_float(stream.get("duration"))
        if stream_duration is not None and stream_duration > 0.0:
            return stream_duration
        # Fallback: duration_ts (timebase units) × time_base.
        duration_ts = _coerce_float(stream.get("duration_ts"))
        time_base = _parse_ratio(stream.get("time_base"))
        if duration_ts is not None and time_base is not None and duration_ts > 0.0 and time_base > 0.0:
            return duration_ts * time_base
    return None


def _parse_hhmmss_duration(value: object) -> float | None:
    """Parse an HH:MM:SS.sss duration string (as used in ffprobe tags) to seconds."""
    if not isinstance(value, str):
        return None
    parts = value.strip().split(":")
    if len(parts) != 3:
        return None
    try:
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = float(parts[2])
    except (ValueError, TypeError):
        return None
    if hours < 0 or minutes < 0 or minutes >= 60 or seconds < 0.0 or seconds >= 60.0:
        return None
    total = hours * 3600 + minutes * 60 + seconds
    return total if total > 0 else None


def _probe_frame_rate(metadata: dict, stream) -> float:
    streams = metadata.get("streams", []) if isinstance(metadata, dict) else []
    if isinstance(streams, list):
        for entry in streams:
            if not isinstance(entry, dict) or entry.get("codec_type") != "video":
                continue
            for key in ("avg_frame_rate", "r_frame_rate"):
                rate = _parse_ratio(entry.get(key))
                if rate and rate > 0.0:
                    return min(rate, 240.0)
    average_rate = getattr(stream, "average_rate", None)
    if average_rate:
        try:
            rate = float(average_rate)
        except (TypeError, ValueError, ZeroDivisionError):
            rate = 0.0
        if rate > 0.0:
            return min(rate, 240.0)
    return 30.0


def _iter_export_frames(
    container,
    stream,
    *,
    trim_in_sec: float,
    trim_out_sec: float,
    rotation_cw: int,
):
    try:
        time_base = float(stream.time_base) if stream.time_base is not None else None
    except (TypeError, ValueError, ZeroDivisionError):
        time_base = None

    if trim_in_sec > 0.0 and time_base and time_base > 0.0:
        try:
            container.seek(int(trim_in_sec / time_base), stream=stream)
        except Exception:
            pass

    for frame in container.decode(stream):
        frame_time = getattr(frame, "time", None)
        if frame_time is None and frame.pts is not None and time_base:
            frame_time = float(frame.pts) * time_base
        if frame_time is None:
            frame_time = 0.0
        if frame_time + 1e-6 < trim_in_sec:
            continue
        if trim_out_sec > trim_in_sec and frame_time >= trim_out_sec - 1e-6:
            break
        pil_image = frame.to_image()
        if rotation_cw in {90, 180, 270}:
            pil_image = pil_image.rotate(-rotation_cw, expand=True)
        qimage = image_loader.qimage_from_pil(pil_image)
        if qimage is None or qimage.isNull():
            continue
        yield qimage


def _render_video_frame(
    image: QImage,
    raw_adjustments: dict,
    resolved_adjustments: dict,
    color_stats,
) -> QImage | None:
    transformed = apply_geometry_and_crop(image, raw_adjustments)
    if transformed is None:
        return None
    return apply_adjustments(transformed, resolved_adjustments, color_stats=color_stats)


def _ensure_even_video_frame(image: QImage) -> QImage:
    width = image.width()
    height = image.height()
    even_width = max(2, width - (width % 2))
    even_height = max(2, height - (height % 2))
    if even_width == width and even_height == height:
        return image
    return image.copy(0, 0, even_width, even_height)


def _start_video_encoder(
    *,
    source: Path,
    destination: Path,
    width: int,
    height: int,
    fps: float,
    trim_in_sec: float,
    trim_out_sec: float,
) -> subprocess.Popen:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgba",
        "-s",
        f"{width}x{height}",
        "-r",
        f"{max(fps, 1.0):.6f}",
        "-i",
        "pipe:0",
    ]
    if trim_in_sec > 0.0:
        command += ["-ss", f"{trim_in_sec:.3f}"]
    if trim_out_sec > trim_in_sec:
        command += ["-to", f"{trim_out_sec:.3f}"]
    command += [
        "-i",
        str(source.absolute()),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0?",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        "-shortest",
        str(destination),
    ]

    startupinfo = None
    creationflags = 0
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    return subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        startupinfo=startupinfo,
        creationflags=creationflags,
    )


def _write_rgba_frame(process: subprocess.Popen, image: QImage) -> None:
    if process.stdin is None:
        raise RuntimeError("ffmpeg encoder stdin is not available")
    process.stdin.write(_qimage_to_rgba_bytes(image))


def _finalise_video_encoder(process: subprocess.Popen) -> str:
    if process.stdin is not None:
        process.stdin.close()
    _, stderr_bytes = process.communicate()
    returncode = process.returncode
    message = stderr_bytes.decode("utf-8", "ignore").strip() if stderr_bytes else ""
    if returncode != 0:
        raise ExternalToolError(message or "ffmpeg video encode failed")
    return message


def _qimage_to_rgba_bytes(image: QImage) -> bytes:
    converted = image.convertToFormat(QImage.Format.Format_RGBA8888)
    ptr = converted.bits()
    ptr.setsize(converted.sizeInBytes())
    raw = bytes(ptr)
    row_stride = converted.bytesPerLine()
    row_width = converted.width() * 4
    if row_stride == row_width:
        return raw
    rows = [
        raw[row * row_stride: row * row_stride + row_width]
        for row in range(converted.height())
    ]
    return b"".join(rows)


def _parse_ratio(value) -> float | None:
    if value in (None, "", "0/0"):
        return None
    try:
        if isinstance(value, str) and "/" in value:
            ratio = float(Fraction(value))
        else:
            ratio = float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    if ratio <= 0.0:
        return None
    return ratio


def _coerce_float(value) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric <= 0.0:
        return None
    return numeric

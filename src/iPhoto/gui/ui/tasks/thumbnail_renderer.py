"""Thumbnail image rendering pipeline (scaling, EXIF rotation, adjustment application, video frame extraction)."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QSize
from PySide6.QtGui import QImage

from ....config import THUMBNAIL_SEEK_GUARD_SEC
from ....core.color_resolver import compute_color_statistics
from ....core.geometry import apply_geometry_and_crop
from ....core.image_filters import apply_adjustments
from ....io import sidecar
from ....utils import image_loader
from .thumbnail_compositor import composite_canvas
from .video_frame_grabber import grab_video_frame


def render_image(abs_path: Path, size: QSize) -> Optional[QImage]:  # pragma: no cover - worker helper
    """Load an image, apply sidecar adjustments and return a composited thumbnail."""
    image = image_loader.load_qimage(abs_path, size)
    if image is None:
        return None
    raw_adjustments = sidecar.load_adjustments(abs_path)
    stats = compute_color_statistics(image) if raw_adjustments else None
    adjustments = sidecar.resolve_render_adjustments(
        raw_adjustments,
        color_stats=stats,
    )

    if adjustments:
        image = apply_geometry_and_crop(image, adjustments)
        if image is None:
            return None
        image = apply_adjustments(image, adjustments, color_stats=stats)
    return composite_canvas(image, size)


def render_video(
    abs_path: Path,
    size: QSize,
    *,
    still_image_time: Optional[float],
    duration: Optional[float],
) -> Optional[QImage]:  # pragma: no cover - worker helper
    """Grab a video frame, apply sidecar adjustments and return a composited thumbnail."""
    raw_adjustments = sidecar.load_adjustments(abs_path)
    trim_in_sec, trim_out_sec = sidecar.normalise_video_trim(raw_adjustments, duration)

    image = grab_video_frame(
        abs_path,
        size,
        still_image_time=still_image_time,
        duration=duration,
        trim_in_sec=trim_in_sec,
        trim_out_sec=trim_out_sec,
    )
    if image is None:
        return None

    stats = compute_color_statistics(image) if raw_adjustments else None
    adjustments = sidecar.resolve_render_adjustments(
        raw_adjustments,
        color_stats=stats,
    )

    if adjustments:
        image = apply_geometry_and_crop(image, adjustments)
        if image is None:
            return None
        image = apply_adjustments(image, adjustments, color_stats=stats)

    return composite_canvas(image, size)

def seek_targets(
    is_video: bool,
    still_image_time: Optional[float],
    duration: Optional[float],
    *,
    trim_in_sec: Optional[float] = None,
    trim_out_sec: Optional[float] = None,
) -> List[Optional[float]]:
    """
    Return a list of seek offsets (in seconds) for video thumbnails, applying guard rails
    to avoid seeking too close to the start or end of the video. For non-video files,
    returns a list containing a single None value.
    """
    if not is_video:
        return [None]

    targets: List[Optional[float]] = []
    seen: set[Optional[float]] = set()

    def add(candidate: Optional[float]) -> None:
        if candidate is None:
            key: Optional[float] = None
            value: Optional[float] = None
        else:
            value = max(candidate, 0.0)
            if duration and duration > 0:
                guard = min(
                    max(THUMBNAIL_SEEK_GUARD_SEC, duration * 0.1),
                    duration / 2.0,
                )
                max_seek = max(duration - guard, 0.0)
                if value > max_seek:
                    value = max_seek
            key = value
        if key in seen:
            return
        seen.add(key)
        targets.append(value)

    full_duration = duration if duration and duration > 0 else None
    trim_in = max(float(trim_in_sec or 0.0), 0.0)
    trim_out = float(trim_out_sec) if trim_out_sec is not None else (full_duration or trim_in)
    if full_duration is not None:
        trim_in = min(trim_in, full_duration)
        trim_out = min(max(trim_out, trim_in), full_duration)
    if trim_out <= trim_in:
        trim_in = 0.0
        trim_out = full_duration if full_duration is not None else max(trim_out, trim_in)
    window_duration = max(trim_out - trim_in, 0.0)

    if still_image_time is not None:
        if trim_in <= still_image_time <= trim_out:
            add(still_image_time)
        elif window_duration > 0:
            add(trim_in + window_duration / 2.0)
        else:
            add(still_image_time)
    elif window_duration > 0:
        add(trim_in + window_duration / 2.0)
    elif duration is not None and duration > 0:
        add(duration / 2.0)
    add(None)
    return targets

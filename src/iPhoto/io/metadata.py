"""Metadata readers for still images and video clips."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from ..errors import ExternalToolError
from ..utils.deps import load_pillow
from ..utils.exiftool import get_metadata_batch
from ..utils.ffmpeg import probe_media
from ..utils.logging import get_logger
from .metadata_extractors import (
    _coerce_decimal,
    _coerce_fractional,
    _empty_media_info,
    _extract_content_id_from_exiftool,
    _extract_datetime_from_exiftool,
    _extract_duration_from_exiftool,
    _extract_gps_from_exiftool,
    _extract_group,
    _normalise_exif_datetime,
    _parse_duration_value,
    _pick_string,
)

_PILLOW = load_pillow()

if _PILLOW is not None:
    Image = _PILLOW.Image
    UnidentifiedImageError = _PILLOW.UnidentifiedImageError
else:  # pragma: no cover - exercised only when Pillow is missing
    Image = None  # type: ignore[assignment]
    UnidentifiedImageError = None  # type: ignore[assignment]

LOGGER = get_logger()


def read_image_meta_with_exiftool(
    path: Path, metadata: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """Read metadata for ``path`` using a pre-fetched ExifTool payload."""

    info = _empty_media_info()
    exif_payload: Optional[Any] = None

    if isinstance(metadata, dict):
        exif_group = _extract_group(metadata, "EXIF") or {}
        ifd0_group = _extract_group(metadata, "IFD0") or {}
        exif_ifd_group = _extract_group(metadata, "ExifIFD") or {}
        maker_notes_group = _extract_group(metadata, "MakerNotes") or {}
        composite_group = _extract_group(metadata, "Composite") or {}
        quicktime_group = _extract_group(metadata, "QuickTime") or {}
        xmp_group = _extract_group(metadata, "XMP") or {}

        file_group = _extract_group(metadata, "File")
        if file_group:
            width = file_group.get("ImageWidth")
            height = file_group.get("ImageHeight")
            mime = file_group.get("MIMEType")

            if isinstance(width, (int, float, str)):
                try:
                    info["w"] = int(float(width))
                except (TypeError, ValueError):
                    info["w"] = None
            if isinstance(height, (int, float, str)):
                try:
                    info["h"] = int(float(height))
                except (TypeError, ValueError):
                    info["h"] = None
            if isinstance(mime, str):
                info["mime"] = mime or None

        # Check for orientation-based dimension swapping
        orientation = None
        # Try finding Orientation in common groups
        for group in (ifd0_group, exif_group, exif_ifd_group, quicktime_group):
            val = group.get("Orientation")
            if val is not None:
                # ExifTool often returns integers, but sometimes strings if -n is not used?
                # We assume standard integer values or numeric strings.
                try:
                    orientation = int(val)
                    break
                except (ValueError, TypeError):
                    continue

        # If not found in groups, try top-level fallback
        if orientation is None:
            val = metadata.get("Orientation")
            if val is not None:
                try:
                    orientation = int(val)
                except (ValueError, TypeError):
                    pass

        # Orientation flags 5-8 indicate 90 or 270 degree rotation
        if orientation in (5, 6, 7, 8) and info["w"] and info["h"]:
            info["w"], info["h"] = info["h"], info["w"]

        gps_payload = _extract_gps_from_exiftool(metadata)
        if gps_payload is not None:
            info["gps"] = gps_payload

        dt_value = _extract_datetime_from_exiftool(metadata)
        if dt_value:
            info["dt"] = dt_value

        content_id = _extract_content_id_from_exiftool(metadata)
        if content_id:
            info["content_id"] = content_id

        # Camera make and model fields are spread across multiple ExifTool groups
        # depending on the originating device (EXIF/IFD0 for still images,
        # QuickTime for iOS videos, etc.). We therefore walk through each
        # candidate group in descending priority to capture a non-empty value.
        make_value = _pick_string(
            info.get("make"),
            ifd0_group.get("Make"),
            exif_group.get("Make"),
            maker_notes_group.get("Make"),
            quicktime_group.get("Make"),
            xmp_group.get("Make"),
        )
        if make_value is not None:
            info["make"] = make_value

        model_value = _pick_string(
            info.get("model"),
            ifd0_group.get("Model"),
            exif_group.get("Model"),
            maker_notes_group.get("Model"),
            composite_group.get("Model"),
            quicktime_group.get("Model"),
            xmp_group.get("Model"),
        )
        if model_value is not None:
            info["model"] = model_value

        lens_value = _pick_string(
            # A: explicit lens name sources
            exif_ifd_group.get("LensModel"),
            exif_group.get("LensModel"),
            maker_notes_group.get("LensType"),
            maker_notes_group.get("LensModel"),
            composite_group.get("LensID"),
            composite_group.get("Lens"),
            xmp_group.get("Lens"),
            xmp_group.get("LensModel"),
            # B: lens spec string (e.g. Fujifilm ExifIFD:LensInfo = "23mm f/2")
            exif_ifd_group.get("LensInfo"),
            exif_group.get("LensSpecification"),
            exif_group.get("LensSpec"),
            maker_notes_group.get("LensSpec"),
            maker_notes_group.get("LensInfo"),
            composite_group.get("LensInfo"),
        )
        if lens_value is not None:
            info["lens"] = lens_value

        iso_value = _coerce_decimal(exif_ifd_group.get("ISO"))
        if iso_value is None:
            iso_value = _coerce_decimal(exif_group.get("ISO"))
        if iso_value is None:
            iso_value = _coerce_decimal(quicktime_group.get("ISO"))
        if iso_value is not None:
            info["iso"] = int(round(iso_value))

        f_number_value = _coerce_fractional(exif_ifd_group.get("FNumber"))
        if f_number_value is None:
            f_number_value = _coerce_fractional(exif_group.get("FNumber"))
        if f_number_value is None:
            f_number_value = _coerce_fractional(composite_group.get("Aperture"))
        if f_number_value is not None:
            info["f_number"] = f_number_value

        exposure_time_value = _coerce_fractional(exif_ifd_group.get("ExposureTime"))
        if exposure_time_value is None:
            exposure_time_value = _coerce_fractional(composite_group.get("ShutterSpeed"))
        if exposure_time_value is None:
            exposure_time_value = _coerce_fractional(quicktime_group.get("ExposureTime"))
        if exposure_time_value is not None:
            info["exposure_time"] = exposure_time_value

        exposure_comp_value = _coerce_fractional(
            exif_ifd_group.get("ExposureCompensation")
        )
        if exposure_comp_value is None:
            exposure_comp_value = _coerce_fractional(
                exif_ifd_group.get("ExposureBiasValue")
            )
        if exposure_comp_value is None:
            exposure_comp_value = _coerce_fractional(
                composite_group.get("ExposureCompensation")
            )
        if exposure_comp_value is None:
            exposure_comp_value = _coerce_fractional(
                quicktime_group.get("ExposureCompensation")
            )
        if exposure_comp_value is not None:
            info["exposure_compensation"] = exposure_comp_value

        focal_length_value = _coerce_fractional(
            exif_ifd_group.get("FocalLength")
        )
        if focal_length_value is None:
            focal_length_value = _coerce_fractional(
                composite_group.get("FocalLength")
            )
        if focal_length_value is None:
            focal_length_value = _coerce_fractional(
                quicktime_group.get("FocalLength")
            )
        if focal_length_value is not None:
            info["focal_length"] = focal_length_value

    geometry_missing = info["w"] is None or info["h"] is None
    need_dt_fallback = info["dt"] is None

    if (geometry_missing or need_dt_fallback) and Image is not None and UnidentifiedImageError is not None:
        LOGGER.debug("Opening %s with Pillow to backfill metadata", path)
        try:
            with Image.open(path) as img:
                if geometry_missing:
                    info["w"] = img.width
                    info["h"] = img.height
                    if info["mime"] is None:
                        info["mime"] = Image.MIME.get(img.format, None)
                if need_dt_fallback:
                    exif_payload = img.getexif() if hasattr(img, "getexif") else None
        except UnidentifiedImageError as exc:
            raise ExternalToolError(f"Unable to read image metadata for {path}") from exc
        except OSError as exc:
            raise ExternalToolError(f"OS error while reading {path}: {exc}") from exc

    if info["dt"] is None and exif_payload:
        fallback_dt = exif_payload.get(36867) or exif_payload.get(306)
        if isinstance(fallback_dt, str):
            info["dt"] = _normalise_exif_datetime(fallback_dt, exif_payload)

    return info


def read_image_meta(path: Path) -> Dict[str, Any]:
    """Compatibility wrapper that fetches ExifTool data for a single image."""

    metadata_block: Optional[Dict[str, Any]] = None
    try:
        payload = get_metadata_batch([path])
        if payload:
            metadata_block = payload[0]
    except ExternalToolError as exc:
        LOGGER.warning("Could not use ExifTool for %s: %s", path, exc)

    return read_image_meta_with_exiftool(path, metadata_block)


def read_video_meta(path: Path, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return metadata for a video file, enriching it with ExifTool payloads."""

    info = _empty_media_info()
    info["mime"] = "video/quicktime" if path.suffix.lower() in {".mov", ".qt"} else "video/mp4"

    if isinstance(metadata, dict):
        exif_group = _extract_group(metadata, "EXIF") or {}
        ifd0_group = _extract_group(metadata, "IFD0") or {}
        exif_ifd_group = _extract_group(metadata, "ExifIFD") or {}
        maker_notes_group = _extract_group(metadata, "MakerNotes") or {}
        composite_group = _extract_group(metadata, "Composite") or {}
        quicktime_group = _extract_group(metadata, "QuickTime") or {}
        xmp_group = _extract_group(metadata, "XMP") or {}
        item_list_group = _extract_group(metadata, "ItemList") or {}

        gps_payload = _extract_gps_from_exiftool(metadata)
        if gps_payload is not None:
            info["gps"] = gps_payload

        dt_value = _extract_datetime_from_exiftool(metadata)
        if dt_value:
            info["dt"] = dt_value

        content_id = _extract_content_id_from_exiftool(metadata)
        if content_id:
            info["content_id"] = content_id

        # Extract the camera metadata from all common QuickTime and EXIF groups.
        make_value = _pick_string(
            info.get("make"),
            ifd0_group.get("Make"),
            exif_group.get("Make"),
            quicktime_group.get("Make"),
            item_list_group.get("Make"),
        )
        if make_value is not None:
            info["make"] = make_value

        model_value = _pick_string(
            info.get("model"),
            ifd0_group.get("Model"),
            exif_group.get("Model"),
            quicktime_group.get("Model"),
            composite_group.get("Model"),
            maker_notes_group.get("Model"),
            item_list_group.get("Model"),
            xmp_group.get("Model"),
        )
        if model_value is not None:
            info["model"] = model_value

        # Lens extraction — full priority chain for cross-brand compatibility.
        #
        # A: explicit lens name (highest priority, closest to user intent)
        #   1. QuickTime Keys/VideoKeys (iOS/macOS video native tags)
        #   2. Language-tagged variant, e.g. VideoKeys:LensModel-eng-DE
        #   3. EXIF/ExifIFD/MakerNotes LensModel, Composite:LensID
        #
        # B: lens spec string when no explicit name is available
        #   ExifIFD:LensInfo (e.g. Fujifilm "23mm f/2"), EXIF:LensSpecification, etc.
        keys_group = _extract_group(metadata, "Keys") or {}
        video_keys_group = _extract_group(metadata, "VideoKeys") or {}
        # Scan both groups for the first language-tagged LensModel variant
        # (e.g. "LensModel-eng-DE") and use it when the bare tag is absent.
        video_lens_lang: Optional[str] = next(
            (
                _v.strip()
                for _grp in (keys_group, video_keys_group)
                for _k, _v in _grp.items()
                if _k.startswith("LensModel-") and isinstance(_v, str) and _v.strip()
            ),
            None,
        )
        lens_value = _pick_string(
            # A: explicit lens name
            keys_group.get("LensModel"),
            video_keys_group.get("LensModel"),
            video_lens_lang,
            exif_ifd_group.get("LensModel"),
            exif_group.get("LensModel"),
            maker_notes_group.get("LensModel"),
            composite_group.get("LensID"),
            quicktime_group.get("LensModel"),
            # B: lens spec string fallback
            exif_ifd_group.get("LensInfo"),
            exif_group.get("LensSpecification"),
            exif_group.get("LensSpec"),
            maker_notes_group.get("LensSpec"),
            maker_notes_group.get("LensInfo"),
            composite_group.get("Lens"),
            composite_group.get("LensInfo"),
        )
        if lens_value is not None:
            info["lens"] = lens_value

        # Extract focal length from video-native and EXIF groups.
        # Prefer the 35mm-equivalent value for consistent cross-brand display.
        for _fl_candidate in (
            video_keys_group.get("FocalLengthIn35mmFormat"),
            keys_group.get("FocalLengthIn35mmFormat"),
            composite_group.get("FocalLengthIn35mmFormat"),
            exif_ifd_group.get("FocalLengthIn35mmFormat"),
            exif_group.get("FocalLengthIn35mmFormat"),
            exif_ifd_group.get("FocalLength"),
            exif_group.get("FocalLength"),
            maker_notes_group.get("FocalLength"),
            quicktime_group.get("FocalLength"),
        ):
            fl = _coerce_fractional(_fl_candidate)
            if fl is not None:
                info["focal_length"] = fl
                break

        # Extract duration from ExifTool as an initial estimate.  ffprobe
        # values (parsed below) are generally more precise and will overwrite
        # this if available.
        exiftool_dur = _extract_duration_from_exiftool(metadata)
        if exiftool_dur is not None:
            info["dur"] = exiftool_dur

    try:
        ffprobe_meta = probe_media(path)
    except ExternalToolError:
        return info

    fmt = ffprobe_meta.get("format", {}) if isinstance(ffprobe_meta, dict) else {}
    duration = fmt.get("duration") if isinstance(fmt, dict) else None
    # Accept both string and numeric duration values from ffprobe.
    parsed_dur = _parse_duration_value(duration)
    # Track whether `format.duration` provided a value.  When it did, that is
    # the most reliable container-level source and subsequent tag/stream
    # fallbacks are unnecessary.  When it did *not*, any ffprobe-derived
    # tag or stream duration should override an earlier ExifTool estimate
    # because ffprobe values are generally more precise.
    got_format_duration = parsed_dur is not None
    if got_format_duration:
        info["dur"] = parsed_dur

    # Matroska containers on Linux often omit ``format.duration`` but populate
    # the ``DURATION`` tag inside ``format.tags`` in HH:MM:SS format.
    # When ``format.duration`` was not available, prefer this ffprobe-derived
    # tag over any earlier estimate (e.g. from ExifTool).
    if not got_format_duration and isinstance(fmt, dict):
        fmt_tags = fmt.get("tags")
        if isinstance(fmt_tags, dict):
            tag_dur = _parse_duration_value(fmt_tags.get("DURATION"))
            if tag_dur is not None:
                info["dur"] = tag_dur

    if isinstance(fmt, dict):
        size_value = fmt.get("size")
        if isinstance(size_value, (int, float)):
            info["bytes"] = int(size_value)
        elif isinstance(size_value, str):
            try:
                info["bytes"] = int(float(size_value))
            except ValueError:
                info["bytes"] = info.get("bytes")

        # ``ffprobe`` sometimes exposes the codec through the container tags;
        # prefer an explicit stream codec but remember the fallback for later.
        container_codec = fmt.get("codec" if "codec" in fmt else "format_name")
        if isinstance(container_codec, str) and container_codec and not info.get("codec"):
            info["codec"] = container_codec

    if isinstance(fmt, dict):
        top_level_tags = fmt.get("tags")
        if isinstance(top_level_tags, dict):
            if not info.get("content_id"):
                content_id = top_level_tags.get("com.apple.quicktime.content.identifier")
                if isinstance(content_id, str) and content_id:
                    info["content_id"] = content_id

            if not info.get("make"):
                ffprobe_make = top_level_tags.get("com.apple.quicktime.make")
                if isinstance(ffprobe_make, str):
                    trimmed_make = ffprobe_make.strip()
                    if trimmed_make:
                        # ``ffprobe`` exposes QuickTime-style camera metadata at the
                        # container level for many iOS captures.  When ExifTool did
                        # not populate ``make`` we reuse the ffprobe tag instead so
                        # downstream features (e.g. device grouping) remain accurate.
                        info["make"] = trimmed_make

            if not info.get("model"):
                ffprobe_model = top_level_tags.get("com.apple.quicktime.model")
                if isinstance(ffprobe_model, str):
                    trimmed_model = ffprobe_model.strip()
                    if trimmed_model:
                        # See the note above for ``make``: ffprobe's top-level
                        # QuickTime tags often include the device model while
                        # ExifTool sometimes omits it for video clips.
                        info["model"] = trimmed_model

    streams = ffprobe_meta.get("streams", []) if isinstance(ffprobe_meta, dict) else []
    if isinstance(streams, list):
        for stream in streams:
            if not isinstance(stream, dict):
                continue

            tags = stream.get("tags") if isinstance(stream.get("tags"), dict) else {}
            if tags and not info.get("content_id"):
                content_id = tags.get("com.apple.quicktime.content.identifier")
                if isinstance(content_id, str) and content_id:
                    info["content_id"] = content_id

            codec_type = stream.get("codec_type")
            if codec_type == "video":
                codec = stream.get("codec_name")
                if isinstance(codec, str) and codec:
                    info["codec"] = codec
                elif isinstance(stream.get("codec_long_name"), str) and stream.get("codec_long_name"):
                    info["codec"] = str(stream["codec_long_name"])

                width = stream.get("width")
                height = stream.get("height")
                if isinstance(width, int) and isinstance(height, int):
                    info["w"] = width
                    info["h"] = height

                frame_rate = _coerce_fractional(stream.get("avg_frame_rate"))
                if frame_rate is None:
                    frame_rate = _coerce_fractional(stream.get("r_frame_rate"))
                if frame_rate is None:
                    frame_rate = _coerce_fractional(stream.get("frame_rate"))
                if frame_rate is not None and frame_rate > 0:
                    info["frame_rate"] = frame_rate

                if not got_format_duration:
                    # Fall back to stream-level duration when format.duration is
                    # absent (e.g. MKV/WebM containers on Linux).  Only the first
                    # video stream is considered; subsequent ones are ignored.
                    # When present, prefer this ffprobe-derived value over any
                    # earlier ExifTool estimate.
                    stream_dur = _parse_duration_value(stream.get("duration"))
                    if stream_dur is not None:
                        info["dur"] = stream_dur

                    if stream_dur is None and tags:
                        # Matroska containers on Linux frequently populate a
                        # ``DURATION`` tag (``HH:MM:SS.microseconds``) in stream
                        # tags when neither ``format.duration`` nor
                        # ``stream.duration`` are present.  If present, prefer
                        # this ffprobe-derived duration over any earlier estimate
                        # (e.g. from ExifTool).
                        tag_dur = _parse_duration_value(tags.get("DURATION"))
                        if tag_dur is not None:
                            info["dur"] = tag_dur

                if tags:
                    still_time = tags.get("com.apple.quicktime.still-image-time")
                    if isinstance(still_time, str):
                        try:
                            info["still_image_time"] = float(still_time)
                        except ValueError:
                            info["still_image_time"] = None
            elif codec_type == "audio":
                codec = stream.get("codec_name")
                if isinstance(codec, str) and not info.get("codec"):
                    info["codec"] = codec

    return info


__all__ = ["read_image_meta", "read_image_meta_with_exiftool", "read_video_meta"]

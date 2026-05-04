"""Location metadata adapter backed by ExifTool and media parsers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...application.ports import LocationMetadataPort
from ...errors import ExternalToolError
from ...io.metadata import read_image_meta_with_exiftool, read_video_meta
from ...utils.exiftool import get_metadata_batch, write_gps_metadata


class ExifToolLocationMetadataService(LocationMetadataPort):
    """Read and write assigned GPS metadata through existing infrastructure."""

    def write_gps_metadata(
        self,
        path: Path,
        *,
        latitude: float,
        longitude: float,
        is_video: bool,
    ) -> None:
        write_gps_metadata(
            path,
            latitude=latitude,
            longitude=longitude,
            is_video=is_video,
        )

    def read_back_metadata(
        self,
        path: Path,
        *,
        is_video: bool,
        existing_metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        try:
            exif_batch = get_metadata_batch([path])
            exif_payload = exif_batch[0] if exif_batch else None
        except (ExternalToolError, OSError):
            exif_payload = None

        if is_video:
            metadata = read_video_meta(path, exif_payload)
        else:
            metadata = read_image_meta_with_exiftool(path, exif_payload)

        return self._merge_metadata(existing_metadata, metadata)

    def _merge_metadata(
        self,
        existing_metadata: dict[str, Any] | None,
        refreshed_metadata: dict[str, Any],
    ) -> dict[str, Any]:
        merged = dict(existing_metadata or {})
        for key, value in refreshed_metadata.items():
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            merged[key] = value
        return merged


__all__ = ["ExifToolLocationMetadataService"]

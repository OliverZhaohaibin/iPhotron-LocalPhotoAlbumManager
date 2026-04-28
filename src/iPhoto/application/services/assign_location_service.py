"""Application service for embedding and persisting user-assigned locations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from iPhoto.cache.index_store.repository import get_global_repository
from iPhoto.errors import ExternalToolError
from iPhoto.io.metadata import read_image_meta_with_exiftool, read_video_meta
from iPhoto.utils.exiftool import get_metadata_batch, write_gps_metadata


@dataclass(frozen=True)
class AssignedLocationResult:
    asset_path: Path
    asset_rel: str
    display_name: str
    gps: dict[str, float]
    metadata: dict[str, Any]
    file_write_error: str | None = None


class AssignLocationService:
    """Persist assigned locations and best-effort embed GPS metadata in files."""

    def __init__(self, library_root: Path) -> None:
        self._library_root = Path(library_root)

    def assign(
        self,
        *,
        asset_path: Path,
        asset_rel: str,
        display_name: str,
        latitude: float,
        longitude: float,
        is_video: bool,
        existing_metadata: dict[str, Any] | None = None,
    ) -> AssignedLocationResult:
        normalized_name = display_name.strip()
        gps = {"lat": float(latitude), "lon": float(longitude)}
        file_write_error: str | None = None

        try:
            write_gps_metadata(
                asset_path,
                latitude=gps["lat"],
                longitude=gps["lon"],
                is_video=is_video,
            )
        except (ExternalToolError, OSError) as exc:
            file_write_error = str(exc)
            refreshed_metadata = dict(existing_metadata or {})
        else:
            refreshed_metadata = self._read_back_metadata(
                asset_path,
                is_video=is_video,
                existing_metadata=existing_metadata,
            )
        refreshed_metadata["gps"] = dict(gps)
        refreshed_metadata["location"] = normalized_name
        refreshed_metadata["location_name"] = normalized_name

        repository = get_global_repository(self._library_root)
        repository.update_asset_geodata(
            asset_rel,
            gps=gps,
            location=normalized_name,
            metadata_updates=refreshed_metadata,
        )
        return AssignedLocationResult(
            asset_path=Path(asset_path),
            asset_rel=str(asset_rel),
            display_name=normalized_name,
            gps=gps,
            metadata=refreshed_metadata,
            file_write_error=file_write_error,
        )

    def _read_back_metadata(
        self,
        asset_path: Path,
        *,
        is_video: bool,
        existing_metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        try:
            exif_batch = get_metadata_batch([asset_path])
            exif_payload = exif_batch[0] if exif_batch else None
        except (ExternalToolError, OSError):
            exif_payload = None

        if is_video:
            metadata = read_video_meta(asset_path, exif_payload)
        else:
            metadata = read_image_meta_with_exiftool(asset_path, exif_payload)

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


__all__ = ["AssignLocationService", "AssignedLocationResult"]

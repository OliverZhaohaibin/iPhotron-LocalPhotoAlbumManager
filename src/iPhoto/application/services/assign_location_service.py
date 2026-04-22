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


class AssignLocationService:
    """Write GPS metadata to the original file and persist it in the library DB."""

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

        write_gps_metadata(
            asset_path,
            latitude=gps["lat"],
            longitude=gps["lon"],
            is_video=is_video,
        )
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

        if not metadata and existing_metadata:
            metadata = dict(existing_metadata)
        return dict(metadata)


__all__ = ["AssignLocationService", "AssignedLocationResult"]

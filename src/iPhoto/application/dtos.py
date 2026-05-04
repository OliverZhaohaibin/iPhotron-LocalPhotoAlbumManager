from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Dict, Any, Literal
from datetime import datetime

@dataclass
class AlbumDTO:
    id: str
    path: Path
    name: str
    asset_count: int
    cover_path: Optional[Path]

@dataclass
class AssetDTO:
    id: str
    abs_path: Path
    rel_path: Path
    media_type: str
    created_at: Optional[datetime]
    width: int
    height: int
    duration: float
    size_bytes: int
    metadata: Dict[str, Any]
    is_favorite: bool
    face_status: Optional[str] = None

    # Derived flags
    is_live: bool = False
    is_pano: bool = False

    # For UI
    micro_thumbnail: Optional[Any] = None

    @property
    def is_video(self) -> bool:
        return self.media_type == "video"

    @property
    def is_image(self) -> bool:
        return self.media_type == "photo" or self.media_type == "image"

@dataclass
class OpenAlbumRequest:
    path: Path

@dataclass
class OpenAlbumResponse:
    album_id: str
    title: str
    asset_count: int

@dataclass
class ScanAlbumRequest:
    album_id: str
    force_rescan: bool = False

@dataclass
class ScanAlbumResponse:
    added_count: int
    updated_count: int
    deleted_count: int

@dataclass
class PairLivePhotosRequest:
    album_id: str

@dataclass
class PairLivePhotosResponse:
    paired_count: int


@dataclass(slots=True, frozen=True)
class GeotaggedAsset:
    """Lightweight descriptor describing an asset with GPS metadata."""

    library_relative: str
    album_relative: str
    absolute_path: Path
    album_path: Path
    asset_id: str
    latitude: float
    longitude: float
    is_image: bool
    is_video: bool
    still_image_time: Optional[float]
    duration: Optional[float]
    location_name: Optional[str]
    live_photo_group_id: Optional[str]
    live_partner_rel: Optional[str]


MapMarkerActivationKind = Literal["none", "asset", "cluster"]


@dataclass(slots=True, frozen=True)
class MapMarkerActivation:
    """Application-level routing decision for a clicked map marker."""

    kind: MapMarkerActivationKind
    asset_relative: Optional[str] = None
    assets: tuple[GeotaggedAsset, ...] = ()

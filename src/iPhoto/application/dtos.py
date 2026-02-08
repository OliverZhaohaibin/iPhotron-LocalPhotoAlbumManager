from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Dict, Any
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

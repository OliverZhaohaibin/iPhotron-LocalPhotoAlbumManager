from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

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

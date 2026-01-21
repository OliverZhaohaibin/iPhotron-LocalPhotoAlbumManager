from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, List, Any
import uuid

# Define MediaType here as the single source of truth for the domain
class MediaType(str, Enum):
    # Reverting to "photo" for compatibility with existing DB
    IMAGE = "photo"
    # Alias
    PHOTO = "photo"

    VIDEO = "video"
    LIVE_PHOTO = "live"

@dataclass
class Asset:
    id: str
    album_id: str
    path: Path  # Relative path within the album
    media_type: MediaType
    size_bytes: int
    created_at: Optional[datetime] = None
    width: Optional[int] = None
    height: Optional[int] = None
    duration: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    # New fields for query support
    is_favorite: bool = False
    parent_album_path: Optional[str] = None

    # Live Photo support
    content_identifier: Optional[str] = None
    live_photo_group_id: Optional[str] = None

    @property
    def is_video(self) -> bool:
        return self.media_type == MediaType.VIDEO

@dataclass
class Album:
    id: str
    path: Path
    title: str
    created_at: Optional[datetime] = None
    description: Optional[str] = None
    cover_asset_id: Optional[str] = None

    # Using a simpler model where assets are queried via repository
    # rather than loaded entirely into memory list

    @classmethod
    def create(cls, path: Path, title: Optional[str] = None) -> Album:
        return cls(
            id=str(uuid.uuid4()),
            path=path,
            title=title or path.name,
            created_at=datetime.now()
        )

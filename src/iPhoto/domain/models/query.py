from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional

# Import MediaType from core domain model to be Single Source of Truth
# Import from .core to avoid circular dependency with __init__.py
from .core import MediaType

class SortOrder(Enum):
    ASC = "ASC"
    DESC = "DESC"

@dataclass
class AssetQuery:
    """Asset query object - Fluent API for building query conditions"""

    album_id: Optional[str] = None
    album_path: Optional[str] = None
    include_subalbums: bool = False
    media_types: List[MediaType] = field(default_factory=list)
    is_favorite: Optional[bool] = None
    is_deleted: Optional[bool] = None
    has_gps: Optional[bool] = None
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    limit: Optional[int] = None
    offset: int = 0
    order_by: str = "created_at"  # Changed default from 'ts' to 'created_at' to match model
    order: SortOrder = SortOrder.DESC

    def with_album_id(self, album_id: str):
        self.album_id = album_id
        return self

    def with_album_path(self, album_path: str, include_sub: bool = False):
        """Fluent API: Set album path"""
        self.album_path = album_path
        self.include_subalbums = include_sub
        return self

    def only_images(self):
        self.media_types = [MediaType.IMAGE]
        return self

    def only_videos(self):
        self.media_types = [MediaType.VIDEO]
        return self

    def only_favorites(self):
        self.is_favorite = True
        return self

    def paginate(self, page: int, page_size: int):
        self.offset = (page - 1) * page_size
        self.limit = page_size
        return self

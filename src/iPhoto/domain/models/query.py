from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, List, Optional

# Import MediaType from core domain model to be Single Source of Truth
# Import from .core to avoid circular dependency with __init__.py
from .core import MediaType


class SortOrder(Enum):
    ASC = "ASC"
    DESC = "DESC"


class CollectionType(str, Enum):
    ALL_PHOTOS = "all_photos"
    ALBUM = "album"
    FAVORITES = "favorites"
    VIDEOS = "videos"
    MAP = "map"
    PEOPLE = "people"
    SEARCH = "search"


class SortDirection(str, Enum):
    ASC = "ASC"
    DESC = "DESC"


class ThumbnailState(str, Enum):
    READY = "ready"
    PENDING = "pending"
    FAILED = "failed"
    STALE = "stale"


@dataclass(frozen=True)
class ThumbnailReadyResult:
    state: ThumbnailState
    micro_thumbnail: bytes | None = None
    thumb_cache_key: str | None = None
    thumb_error: str | None = None


@dataclass(frozen=True)
class CollectionQuery:
    collection_type: CollectionType = CollectionType.ALL_PHOTOS
    album_path: str | None = None
    include_subalbums: bool = True
    media_types: tuple[int, ...] = ()
    is_favorite: bool | None = None
    has_gps: bool | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    search_text: str | None = None
    sort_key: str = "sort_ts"
    sort_direction: SortDirection = SortDirection.DESC
    min_thumbnail_state: str | None = ThumbnailState.READY.value


@dataclass(frozen=True)
class PageCursor:
    sort_ts: int
    asset_id: str
    sort_value: Any | None = None
    asset_rel: str | None = None


@dataclass(frozen=True)
class PageResult:
    rows: list[dict]
    next_cursor: PageCursor | None
    total_count: int | None
    collection_revision: int


@dataclass(frozen=True)
class WindowResult:
    first: int
    rows: list[dict]
    total_count: int
    collection_revision: int

@dataclass
class AssetQuery:
    """Asset query object - Fluent API for building query conditions"""

    asset_ids: List[str] = field(default_factory=list)
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

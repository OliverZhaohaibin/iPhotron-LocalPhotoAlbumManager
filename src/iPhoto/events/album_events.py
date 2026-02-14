from dataclasses import dataclass, field

from .domain_events import DomainEvent


@dataclass(frozen=True)
class AlbumOpenedEvent(DomainEvent):
    album_id: str = ""
    album_path: str = ""


@dataclass(frozen=True)
class ScanProgressEvent(DomainEvent):
    album_id: str = ""
    processed: int = 0
    total: int = 0


@dataclass(frozen=True)
class ScanCompletedEvent(DomainEvent):
    album_id: str = ""
    asset_count: int = 0
    duration_seconds: float = 0.0


@dataclass(frozen=True)
class AssetImportedEvent(DomainEvent):
    asset_ids: list[str] = field(default_factory=list)
    album_id: str = ""


@dataclass(frozen=True)
class ThumbnailReadyEvent(DomainEvent):
    asset_id: str = ""
    thumbnail_path: str = ""

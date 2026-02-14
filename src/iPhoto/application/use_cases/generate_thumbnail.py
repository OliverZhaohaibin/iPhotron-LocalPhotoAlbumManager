import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .base import UseCase, UseCaseRequest, UseCaseResponse
from iPhoto.domain.repositories import IAlbumRepository, IAssetRepository
from iPhoto.application.interfaces import IThumbnailGenerator
from iPhoto.events.bus import EventBus
from iPhoto.events.album_events import ThumbnailReadyEvent

@dataclass(frozen=True)
class GenerateThumbnailRequest(UseCaseRequest):
    asset_id: str = ""

@dataclass(frozen=True)
class GenerateThumbnailResponse(UseCaseResponse):
    thumbnail_data: Optional[str] = None

class GenerateThumbnailUseCase(UseCase):
    def __init__(
        self,
        asset_repo: IAssetRepository,
        album_repo: IAlbumRepository,
        thumbnail_generator: IThumbnailGenerator,
        event_bus: EventBus,
    ):
        self._asset_repo = asset_repo
        self._album_repo = album_repo
        self._thumbnail_gen = thumbnail_generator
        self._event_bus = event_bus
        self._logger = logging.getLogger(__name__)

    def execute(self, request: GenerateThumbnailRequest) -> GenerateThumbnailResponse:
        asset = self._asset_repo.get(request.asset_id)
        if asset is None:
            return GenerateThumbnailResponse(success=False, error="Asset not found")
        
        album = self._album_repo.get(asset.album_id)
        if album is None:
            return GenerateThumbnailResponse(success=False, error="Album not found")
        
        abs_path = album.path / asset.path
        thumb = self._thumbnail_gen.generate_micro_thumbnail(abs_path)
        
        if thumb:
            self._event_bus.publish(ThumbnailReadyEvent(
                asset_id=asset.id,
                thumbnail_path=str(abs_path),
            ))
        
        return GenerateThumbnailResponse(thumbnail_data=thumb)

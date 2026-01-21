import logging
import uuid
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass

from src.iPhoto.application.dtos import OpenAlbumRequest, OpenAlbumResponse
from src.iPhoto.domain.models import Album
from src.iPhoto.domain.repositories import IAlbumRepository, IAssetRepository
from src.iPhoto.events.bus import EventBus, Event

@dataclass(kw_only=True)
class AlbumOpenedEvent(Event):
    album_id: str
    path: Path

class OpenAlbumUseCase:
    def __init__(
        self,
        album_repo: IAlbumRepository,
        asset_repo: IAssetRepository,
        event_bus: EventBus
    ):
        self._album_repo = album_repo
        self._asset_repo = asset_repo
        self._events = event_bus
        self._logger = logging.getLogger(__name__)

    def execute(self, request: OpenAlbumRequest) -> OpenAlbumResponse:
        self._logger.info(f"Opening album at {request.path}")

        album = self._album_repo.get_by_path(request.path)

        if not album:
            # If not in DB, try to load from disk (e.g. manifest.json) or create new
            # For this phase, we'll assume creation if not exists
            # In real migration, we would read manifest.json here
            album = Album.create(request.path)
            self._album_repo.save(album)
            self._logger.info(f"Created new album entry for {request.path}")

        assets = self._asset_repo.get_by_album(album.id)

        self._events.publish(AlbumOpenedEvent(
            album_id=album.id,
            path=album.path
        ))

        return OpenAlbumResponse(
            album_id=album.id,
            title=album.title,
            asset_count=len(assets)
        )

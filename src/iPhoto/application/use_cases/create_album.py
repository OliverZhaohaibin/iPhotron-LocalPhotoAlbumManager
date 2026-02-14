import logging
from dataclasses import dataclass
from pathlib import Path

from .base import UseCase, UseCaseRequest, UseCaseResponse
from iPhoto.domain.models import Album
from iPhoto.domain.repositories import IAlbumRepository
from iPhoto.events.bus import EventBus
from iPhoto.events.album_events import AlbumOpenedEvent

@dataclass(frozen=True)
class CreateAlbumRequest(UseCaseRequest):
    path: Path = Path(".")
    title: str = ""

@dataclass(frozen=True) 
class CreateAlbumResponse(UseCaseResponse):
    album_id: str = ""
    title: str = ""

class CreateAlbumUseCase(UseCase):
    def __init__(self, album_repo: IAlbumRepository, event_bus: EventBus):
        self._album_repo = album_repo
        self._event_bus = event_bus
        self._logger = logging.getLogger(__name__)

    def execute(self, request: CreateAlbumRequest) -> CreateAlbumResponse:
        existing = self._album_repo.get_by_path(request.path)
        if existing is not None:
            return CreateAlbumResponse(
                success=False,
                error=f"Album already exists at {request.path}",
            )
        
        album = Album.create(path=request.path, title=request.title or None)
        self._album_repo.save(album)
        
        self._event_bus.publish(AlbumOpenedEvent(
            album_id=album.id,
            album_path=str(album.path),
        ))
        
        self._logger.info(f"Created album '{album.title}' at {album.path}")
        return CreateAlbumResponse(album_id=album.id, title=album.title)

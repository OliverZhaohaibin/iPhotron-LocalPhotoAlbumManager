import logging
from dataclasses import dataclass

from .base import UseCase, UseCaseRequest, UseCaseResponse
from iPhoto.domain.repositories import IAlbumRepository
from iPhoto.events.bus import EventBus

@dataclass(frozen=True)
class DeleteAlbumRequest(UseCaseRequest):
    album_id: str = ""

@dataclass(frozen=True)
class DeleteAlbumResponse(UseCaseResponse):
    pass

class DeleteAlbumUseCase(UseCase):
    def __init__(self, album_repo: IAlbumRepository, event_bus: EventBus):
        self._album_repo = album_repo
        self._event_bus = event_bus
        self._logger = logging.getLogger(__name__)

    def execute(self, request: DeleteAlbumRequest) -> DeleteAlbumResponse:
        album = self._album_repo.get(request.album_id)
        if album is None:
            return DeleteAlbumResponse(success=False, error="Album not found")
        
        self._album_repo.delete(request.album_id)
        self._logger.info(f"Deleted album {request.album_id}")
        return DeleteAlbumResponse(success=True)

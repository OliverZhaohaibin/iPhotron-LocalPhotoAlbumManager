import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .base import UseCase, UseCaseRequest, UseCaseResponse
from iPhoto.domain.repositories import IAlbumRepository, IAssetRepository
from iPhoto.events.bus import EventBus

@dataclass(frozen=True)
class MoveAssetsRequest(UseCaseRequest):
    asset_ids: list[str] = field(default_factory=list)
    target_album_id: str = ""

@dataclass(frozen=True)
class MoveAssetsResponse(UseCaseResponse):
    moved_count: int = 0

class MoveAssetsUseCase(UseCase):
    def __init__(
        self,
        asset_repo: IAssetRepository,
        album_repo: IAlbumRepository,
        event_bus: EventBus,
    ):
        self._asset_repo = asset_repo
        self._album_repo = album_repo
        self._event_bus = event_bus
        self._logger = logging.getLogger(__name__)

    def execute(self, request: MoveAssetsRequest) -> MoveAssetsResponse:
        target_album = self._album_repo.get(request.target_album_id)
        if target_album is None:
            return MoveAssetsResponse(success=False, error="Target album not found")

        moved = 0
        for asset_id in request.asset_ids:
            asset = self._asset_repo.get(asset_id)
            if asset is None:
                continue
            
            source_album = self._album_repo.get(asset.album_id)
            if source_album is None:
                continue
            
            src_path = source_album.path / asset.path
            dst_path = target_album.path / asset.path.name
            
            try:
                if not src_path.exists():
                    self._logger.error(f"Source file does not exist for asset {asset_id}: {src_path}")
                    continue

                if dst_path.exists():
                    self._logger.error(f"Destination file already exists for asset {asset_id}: {dst_path}")
                    continue

                shutil.move(str(src_path), str(dst_path))
                asset.album_id = target_album.id
                asset.path = Path(asset.path.name)
                self._asset_repo.save(asset)
                moved += 1
            except Exception as e:
                self._logger.error(f"Failed to move asset {asset_id}: {e}")

        return MoveAssetsResponse(moved_count=moved)

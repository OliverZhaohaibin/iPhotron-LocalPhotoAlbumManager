import logging
from dataclasses import dataclass, field
from typing import Any, Dict

from .base import UseCase, UseCaseRequest, UseCaseResponse
from iPhoto.domain.repositories import IAssetRepository

@dataclass(frozen=True)
class UpdateMetadataRequest(UseCaseRequest):
    asset_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class UpdateMetadataResponse(UseCaseResponse):
    pass

class UpdateMetadataUseCase(UseCase):
    def __init__(self, asset_repo: IAssetRepository):
        self._asset_repo = asset_repo
        self._logger = logging.getLogger(__name__)

    def execute(self, request: UpdateMetadataRequest) -> UpdateMetadataResponse:
        asset = self._asset_repo.get(request.asset_id)
        if asset is None:
            return UpdateMetadataResponse(success=False, error="Asset not found")
        
        asset.metadata.update(request.metadata)
        self._asset_repo.save(asset)
        
        self._logger.info(f"Updated metadata for asset {request.asset_id}")
        return UpdateMetadataResponse(success=True)

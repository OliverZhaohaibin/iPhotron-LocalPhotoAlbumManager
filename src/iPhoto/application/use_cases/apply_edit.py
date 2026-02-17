# apply_edit.py
import logging
from dataclasses import dataclass, field
from typing import Any, Dict

from .base import UseCase, UseCaseRequest, UseCaseResponse
from iPhoto.domain.repositories import IAlbumRepository, IAssetRepository
from iPhoto.events.bus import EventBus


@dataclass(frozen=True)
class ApplyEditRequest(UseCaseRequest):
    asset_id: str = ""
    adjustments: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ApplyEditResponse(UseCaseResponse):
    asset_id: str = ""
    applied_adjustments: list[str] = field(default_factory=list)


class ApplyEditUseCase(UseCase):
    """Applies editing adjustments to an asset and persists the sidecar."""

    def __init__(
        self,
        asset_repo: IAssetRepository,
        album_repo: IAlbumRepository,
        event_bus: EventBus,
        save_adjustments_fn=None,
    ):
        self._asset_repo = asset_repo
        self._album_repo = album_repo
        self._event_bus = event_bus
        self._save_fn = save_adjustments_fn
        self._logger = logging.getLogger(__name__)

    def execute(self, request: ApplyEditRequest) -> ApplyEditResponse:
        asset = self._asset_repo.get(request.asset_id)
        if asset is None:
            return ApplyEditResponse(success=False, error="Asset not found")

        album = self._album_repo.get(asset.album_id)
        if album is None:
            return ApplyEditResponse(success=False, error="Album not found")

        if not request.adjustments:
            return ApplyEditResponse(success=False, error="No adjustments provided")

        applied = list(request.adjustments.keys())

        if self._save_fn is not None:
            try:
                abs_path = album.path / asset.path
                self._save_fn(abs_path, request.adjustments)
            except Exception as exc:
                self._logger.error("Failed to save adjustments for %s: %s", request.asset_id, exc)
                return ApplyEditResponse(success=False, error=str(exc))

        return ApplyEditResponse(
            asset_id=request.asset_id,
            applied_adjustments=applied,
        )

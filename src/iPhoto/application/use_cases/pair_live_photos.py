import uuid
import logging
from dataclasses import dataclass
from typing import List, Dict, Optional

from src.iPhoto.application.dtos import PairLivePhotosRequest, PairLivePhotosResponse
from src.iPhoto.domain.models import Asset, MediaType
from src.iPhoto.domain.models.query import AssetQuery
from src.iPhoto.domain.repositories import IAssetRepository
from src.iPhoto.events.bus import EventBus, Event

@dataclass(kw_only=True)
class LivePhotosPairedEvent(Event):
    album_id: str
    paired_count: int

class PairLivePhotosUseCase:
    def __init__(
        self,
        asset_repo: IAssetRepository,
        event_bus: EventBus
    ):
        self._asset_repo = asset_repo
        self._events = event_bus
        self._logger = logging.getLogger(__name__)

    def execute(self, request: PairLivePhotosRequest) -> PairLivePhotosResponse:
        self._logger.info(f"Pairing live photos for album {request.album_id}")

        # 1. Fetch all assets in album
        query = AssetQuery().with_album_id(request.album_id)
        assets = self._asset_repo.find_by_query(query)

        # 2. Group by potential content identifier or filename
        # Simplified logic: match by filename stem (IMG_1234.JPG + IMG_1234.MOV)
        # Real logic would use content_identifier from metadata

        assets_by_stem: Dict[str, List[Asset]] = {}
        for asset in assets:
            stem = str(asset.path.with_suffix(""))
            if stem not in assets_by_stem:
                assets_by_stem[stem] = []
            assets_by_stem[stem].append(asset)

        paired_count = 0
        to_update: List[Asset] = []

        for stem, group in assets_by_stem.items():
            images = [a for a in group if a.media_type == MediaType.IMAGE]
            videos = [a for a in group if a.media_type == MediaType.VIDEO]

            if images and videos:
                # Potential pair
                # Take first image and first video for simplicity
                image = images[0]
                video = videos[0]

                # Check if they are already paired or have metadata linking them
                # Here we force pairing by filename convention

                # Mark image as LIVE_PHOTO if not already
                # But wait, MediaType.LIVE_PHOTO usually implies the container or the image part acting as key
                # The architecture might treat them as separate assets linked by group_id

                group_id = image.live_photo_group_id or video.live_photo_group_id or str(uuid.uuid4())

                if image.live_photo_group_id != group_id or video.live_photo_group_id != group_id:
                    image.live_photo_group_id = group_id
                    video.live_photo_group_id = group_id

                    # Update media type if appropriate
                    # image.media_type = MediaType.LIVE_PHOTO # Optional depending on how we model it

                    to_update.append(image)
                    to_update.append(video)
                    paired_count += 1

        if to_update:
            self._asset_repo.save_batch(to_update)

        self._events.publish(LivePhotosPairedEvent(
            album_id=request.album_id,
            paired_count=paired_count
        ))

        return PairLivePhotosResponse(paired_count=paired_count)

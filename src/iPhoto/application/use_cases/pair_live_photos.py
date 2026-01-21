import logging
from dataclasses import dataclass
from collections import defaultdict
from pathlib import Path

from src.iPhoto.application.dtos import PairLivePhotosRequest, PairLivePhotosResponse
from src.iPhoto.domain.models import MediaType
from src.iPhoto.domain.repositories import IAssetRepository
from src.iPhoto.events.bus import EventBus, Event

@dataclass(kw_only=True)
class LivePhotosPairedEvent(Event):
    album_id: str
    count: int

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
        assets = self._asset_repo.get_by_album(request.album_id)

        # Group by parent + stem (filename without extension)
        # This is a naive implementation (Weak pairing)
        # Real implementation should also check content identifiers

        by_key = defaultdict(list)
        for asset in assets:
            if asset.live_photo_group_id:
                continue # Already paired
            # Key is (parent_path, stem) to ensure we don't pair files in different folders
            key = (str(asset.path.parent), asset.path.stem)
            by_key[key].append(asset)

        paired_count = 0
        updates = []

        for key, group in by_key.items():
            photos = [a for a in group if a.media_type == MediaType.PHOTO]
            videos = [a for a in group if a.media_type == MediaType.VIDEO]

            # Simple case: 1 photo + 1 video with same name in same folder
            if len(photos) == 1 and len(videos) == 1:
                import uuid
                group_id = str(uuid.uuid4())

                photo = photos[0]
                video = videos[0]

                photo.live_photo_group_id = group_id
                video.live_photo_group_id = group_id

                updates.append(photo)
                updates.append(video)
                paired_count += 1

        if updates:
            self._asset_repo.save_all(updates)

        if paired_count > 0:
            self._events.publish(LivePhotosPairedEvent(
                album_id=request.album_id,
                count=paired_count
            ))

        return PairLivePhotosResponse(paired_count=paired_count)

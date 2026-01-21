import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Set, Dict
from dataclasses import dataclass

from src.iPhoto.application.dtos import ScanAlbumRequest, ScanAlbumResponse
from src.iPhoto.domain.models import Album, Asset, MediaType
from src.iPhoto.domain.repositories import IAlbumRepository, IAssetRepository
from src.iPhoto.events.bus import EventBus, Event

@dataclass(kw_only=True)
class AlbumScannedEvent(Event):
    album_id: str
    added_count: int
    updated_count: int
    deleted_count: int

class ScanAlbumUseCase:
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

    def execute(self, request: ScanAlbumRequest) -> ScanAlbumResponse:
        self._logger.info(f"Scanning album {request.album_id}")

        album = self._album_repo.get(request.album_id)
        if not album:
            raise ValueError(f"Album {request.album_id} not found")

        # 1. Load existing assets to handle duplicates/updates/deletes
        existing_assets = self._asset_repo.get_by_album(album.id)
        existing_map: Dict[str, Asset] = {str(a.path): a for a in existing_assets}

        found_assets: List[Asset] = []
        found_paths: Set[str] = set()

        # Valid extensions
        image_exts = {'.jpg', '.jpeg', '.png', '.heic', '.webp'}
        video_exts = {'.mp4', '.mov', '.avi', '.mkv'}

        added_count = 0
        updated_count = 0

        for root, dirs, files in os.walk(album.path):
            for file in files:
                file_path = Path(root) / file
                suffix = file_path.suffix.lower()

                media_type = None
                if suffix in image_exts:
                    media_type = MediaType.IMAGE
                elif suffix in video_exts:
                    media_type = MediaType.VIDEO

                if media_type:
                    rel_path = file_path.relative_to(album.path)
                    str_rel_path = str(rel_path)
                    found_paths.add(str_rel_path)

                    stat = file_path.stat()

                    # Check if exists
                    existing = existing_map.get(str_rel_path)

                    if existing:
                        # Update existing, BUT PRESERVE METADATA
                        asset_id = existing.id
                        updated_count += 1

                        asset = Asset(
                            id=asset_id,
                            album_id=album.id,
                            path=rel_path,
                            media_type=media_type,
                            size_bytes=stat.st_size,
                            created_at=datetime.fromtimestamp(stat.st_mtime),
                            parent_album_path=None,
                            is_favorite=existing.is_favorite,
                            # Preserved metadata fields
                            width=existing.width,
                            height=existing.height,
                            duration=existing.duration,
                            metadata=existing.metadata,
                            content_identifier=existing.content_identifier,
                            live_photo_group_id=existing.live_photo_group_id
                        )
                    else:
                        # Create new
                        asset_id = str(uuid.uuid4())
                        added_count += 1

                        asset = Asset(
                            id=asset_id,
                            album_id=album.id,
                            path=rel_path,
                            media_type=media_type,
                            size_bytes=stat.st_size,
                            created_at=datetime.fromtimestamp(stat.st_mtime),
                            parent_album_path=None,
                            is_favorite=False
                        )

                    found_assets.append(asset)

        # 2. Identify deletions
        deleted_ids = []
        for path_str, asset in existing_map.items():
            if path_str not in found_paths:
                deleted_ids.append(asset.id)

        # 3. Batch operations
        if found_assets:
            self._asset_repo.save_batch(found_assets)

        for del_id in deleted_ids:
            self._asset_repo.delete(del_id)

        deleted_count = len(deleted_ids)

        self._events.publish(AlbumScannedEvent(
            album_id=album.id,
            added_count=added_count,
            updated_count=updated_count,
            deleted_count=deleted_count
        ))

        return ScanAlbumResponse(
            added_count=added_count,
            updated_count=updated_count,
            deleted_count=deleted_count
        )

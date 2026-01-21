import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass

from src.iPhoto.application.dtos import ScanAlbumRequest, ScanAlbumResponse
from src.iPhoto.domain.models import Asset, MediaType
from src.iPhoto.domain.repositories import IAlbumRepository, IAssetRepository
from src.iPhoto.events.bus import EventBus, Event

@dataclass(kw_only=True)
class AlbumScannedEvent(Event):
    album_id: str
    added: int
    updated: int

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
        album = self._album_repo.get(request.album_id)
        if not album:
            raise ValueError(f"Album {request.album_id} not found")

        self._logger.info(f"Scanning album {album.title} at {album.path}")

        # Simple implementation for Phase 2: synchronous scan
        # In later phases this should be async/parallel

        existing_assets = {a.path: a for a in self._asset_repo.get_by_album(album.id)}
        found_files = []

        # Walk directory
        for root, _, files in os.walk(album.path):
            for file in files:
                file_path = Path(root) / file
                rel_path = file_path.relative_to(album.path)

                # Check extension
                ext = file_path.suffix.lower()
                media_type = None
                if ext in {'.jpg', '.jpeg', '.png', '.heic', '.webp'}:
                    media_type = MediaType.PHOTO
                elif ext in {'.mp4', '.mov', '.avi', '.mkv'}:
                    media_type = MediaType.VIDEO

                if media_type:
                    found_files.append((rel_path, media_type, file_path.stat().st_size))

        added_count = 0
        updated_count = 0
        new_assets = []

        for rel_path, media_type, size in found_files:
            if rel_path in existing_assets:
                asset = existing_assets[rel_path]
                if asset.size_bytes != size:
                    asset.size_bytes = size
                    # In real app, we would re-read metadata here
                    self._asset_repo.save(asset)
                    updated_count += 1
            else:
                asset = Asset(
                    id=str(uuid.uuid4()),
                    album_id=album.id,
                    path=rel_path,
                    media_type=media_type,
                    size_bytes=size,
                    created_at=datetime.now()
                )
                new_assets.append(asset)
                added_count += 1

        if new_assets:
            self._asset_repo.save_all(new_assets)

        # Determine deleted assets
        found_paths = {f[0] for f in found_files}
        deleted_count = 0
        for path, asset in existing_assets.items():
            if path not in found_paths:
                self._asset_repo.delete(asset.id)
                deleted_count += 1

        self._events.publish(AlbumScannedEvent(
            album_id=album.id,
            added=added_count,
            updated=updated_count
        ))

        return ScanAlbumResponse(
            added_count=added_count,
            updated_count=updated_count,
            deleted_count=deleted_count
        )

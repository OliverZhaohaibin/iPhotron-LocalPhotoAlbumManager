import logging
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .base import UseCase, UseCaseRequest, UseCaseResponse
from iPhoto.domain.models import Asset, MediaType
from iPhoto.domain.repositories import IAlbumRepository, IAssetRepository
from iPhoto.events.bus import EventBus
from iPhoto.events.album_events import AssetImportedEvent

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".tiff", ".tif", ".bmp", ".webp"}
_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".m4v"}

@dataclass(frozen=True)
class ImportAssetsRequest(UseCaseRequest):
    source_paths: list[Path] = field(default_factory=list)
    target_album_id: str = ""
    copy_files: bool = True

@dataclass(frozen=True)
class ImportAssetsResponse(UseCaseResponse):
    imported_count: int = 0
    skipped_count: int = 0
    failed_paths: list[str] = field(default_factory=list)

class ImportAssetsUseCase(UseCase):
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

    def execute(self, request: ImportAssetsRequest) -> ImportAssetsResponse:
        album = self._album_repo.get(request.target_album_id)
        if album is None:
            return ImportAssetsResponse(success=False, error="Album not found")

        imported = 0
        skipped = 0
        failed = []
        imported_ids = []

        for path in request.source_paths:
            try:
                existing = self._asset_repo.get_by_path(path)
                if existing is not None:
                    skipped += 1
                    continue

                target = album.path / path.name
                if request.copy_files:
                    shutil.copy2(str(path), str(target))

                ext = path.suffix.lower()
                if ext in _VIDEO_EXTS:
                    media_type = MediaType.VIDEO
                else:
                    media_type = MediaType.IMAGE

                size = target.stat().st_size if target.exists() else 0

                asset = Asset(
                    id=str(uuid.uuid4()),
                    album_id=album.id,
                    path=Path(path.name),
                    media_type=media_type,
                    size_bytes=size,
                )
                self._asset_repo.save(asset)
                imported_ids.append(asset.id)
                imported += 1
            except Exception as e:
                failed.append(str(path))
                self._logger.error(f"Import failed for {path}: {e}")

        if imported > 0:
            self._event_bus.publish(AssetImportedEvent(
                album_id=album.id,
                asset_ids=imported_ids,
            ))

        return ImportAssetsResponse(
            imported_count=imported,
            skipped_count=skipped,
            failed_paths=failed,
        )

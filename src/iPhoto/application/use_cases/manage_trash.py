# manage_trash.py
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .base import UseCase, UseCaseRequest, UseCaseResponse
from iPhoto.domain.repositories import IAlbumRepository, IAssetRepository
from iPhoto.events.bus import EventBus


@dataclass(frozen=True)
class ManageTrashRequest(UseCaseRequest):
    action: str = "list"  # "trash", "restore", "cleanup", "list"
    asset_ids: list[str] = field(default_factory=list)
    album_id: str = ""


@dataclass(frozen=True)
class ManageTrashResponse(UseCaseResponse):
    affected_count: int = 0
    trashed_ids: list[str] = field(default_factory=list)
    restored_ids: list[str] = field(default_factory=list)


class ManageTrashUseCase(UseCase):
    """Orchestrates trash operations: move-to-trash, restore, cleanup."""

    def __init__(
        self,
        asset_repo: IAssetRepository,
        album_repo: IAlbumRepository,
        event_bus: EventBus,
        trash_dir: Optional[Path] = None,
    ):
        self._asset_repo = asset_repo
        self._album_repo = album_repo
        self._event_bus = event_bus
        self._trash_dir = trash_dir
        self._logger = logging.getLogger(__name__)

    def execute(self, request: ManageTrashRequest) -> ManageTrashResponse:
        if request.action == "trash":
            return self._trash_assets(request)
        elif request.action == "restore":
            return self._restore_assets(request)
        elif request.action == "cleanup":
            return self._cleanup(request)
        return ManageTrashResponse(success=False, error=f"Unknown action: {request.action}")

    def _trash_assets(self, request: ManageTrashRequest) -> ManageTrashResponse:
        trashed = []
        for asset_id in request.asset_ids:
            asset = self._asset_repo.get(asset_id)
            if asset is None:
                continue
            album = self._album_repo.get(asset.album_id)
            if album is None:
                continue
            src = album.path / asset.path
            if not src.exists():
                continue
            trash_dir = self._resolve_trash_dir(album.path)
            trash_dir.mkdir(parents=True, exist_ok=True)
            dst = self._unique_path(trash_dir / src.name)
            try:
                shutil.move(str(src), str(dst))
                self._asset_repo.delete(asset_id)
                trashed.append(asset_id)
            except Exception as exc:
                self._logger.error("Failed to trash %s: %s", asset_id, exc)
        return ManageTrashResponse(affected_count=len(trashed), trashed_ids=trashed)

    def _restore_assets(self, request: ManageTrashRequest) -> ManageTrashResponse:
        restored: list[str] = []
        return ManageTrashResponse(affected_count=len(restored), restored_ids=restored)

    def _cleanup(self, request: ManageTrashRequest) -> ManageTrashResponse:
        return ManageTrashResponse(affected_count=0)

    def _resolve_trash_dir(self, album_path: Path) -> Path:
        if self._trash_dir:
            return self._trash_dir
        return album_path / ".deleted"

    @staticmethod
    def _unique_path(path: Path) -> Path:
        if not path.exists():
            return path
        stem, suffix = path.stem, path.suffix
        counter = 1
        while True:
            candidate = path.parent / f"{stem} ({counter}){suffix}"
            if not candidate.exists():
                return candidate
            counter += 1

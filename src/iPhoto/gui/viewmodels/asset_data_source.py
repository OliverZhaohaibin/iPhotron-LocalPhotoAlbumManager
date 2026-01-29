from dataclasses import dataclass
from typing import List, Optional, cast
from pathlib import Path
from PySide6.QtCore import QObject, Signal

from src.iPhoto.domain.models import Asset
from src.iPhoto.domain.models.core import MediaType
from src.iPhoto.domain.models.query import AssetQuery
from src.iPhoto.domain.repositories import IAssetRepository
from src.iPhoto.application.dtos import AssetDTO
from src.iPhoto.utils import image_loader
from src.iPhoto.config import RECENTLY_DELETED_DIR_NAME

@dataclass(frozen=True)
class _PendingMove:
    dto: AssetDTO
    source_abs: Path
    destination_root: Path
    destination_album_path: str
    destination_abs: Path
    destination_rel: Path
    is_delete: bool


class AssetDataSource(QObject):
    """
    Intermediary between Repository and ViewModel.
    Handles paging logic and data fetching, and converts Domain Entities to DTOs.
    """

    dataChanged = Signal()

    def __init__(self, repository: IAssetRepository, library_root: Optional[Path] = None):
        super().__init__()
        self._repo = repository
        self._library_root = library_root
        self._current_query: Optional[AssetQuery] = None
        self._cached_dtos: List[AssetDTO] = []
        self._total_count: int = 0
        self._page_size = 1000
        self._pending_moves: List[_PendingMove] = []
        self._pending_paths: set[str] = set()

    def set_library_root(self, root: Path):
        self._library_root = root

    def load(self, query: AssetQuery):
        """Loads data for the given query."""
        self._current_query = query
        self._cached_dtos.clear()

        # Default limit if not set
        if not query.limit:
            query.limit = 5000

        assets = self._repo.find_by_query(query)
        self._cached_dtos = [self._to_dto(a) for a in assets]
        self._apply_pending_moves(query)
        self._total_count = len(self._cached_dtos)

        self.dataChanged.emit()

    def asset_at(self, index: int) -> Optional[AssetDTO]:
        if 0 <= index < len(self._cached_dtos):
            return self._cached_dtos[index]
        return None

    def count(self) -> int:
        return len(self._cached_dtos)

    def update_favorite_status(self, row: int, is_favorite: bool):
        """Updates the favorite status of the cached DTO at the given row."""
        if 0 <= row < len(self._cached_dtos):
            dto = self._cached_dtos[row]
            dto.is_favorite = is_favorite

    def remove_rows(self, rows: List[int], *, emit: bool = True) -> None:
        if not rows:
            return
        for row in sorted(set(rows), reverse=True):
            if 0 <= row < len(self._cached_dtos):
                self._cached_dtos.pop(row)
        if emit:
            self.dataChanged.emit()

    def apply_optimistic_move(
        self,
        paths: List[Path],
        destination_root: Path,
        *,
        is_delete: bool,
    ) -> tuple[list[int], list[AssetDTO]]:
        if not paths:
            return [], []
        destination_album_path = self._album_path_for_root(destination_root)
        removed_rows: list[int] = []
        inserted_dtos: list[AssetDTO] = []
        cached_map = {str(dto.abs_path): (idx, dto) for idx, dto in enumerate(self._cached_dtos)}
        for path in paths:
            key = str(path)
            if key in self._pending_paths:
                continue
            found = cached_map.get(key)
            if found is None:
                continue
            row, dto = found
            removed_rows.append(row)
            destination_abs = destination_root / path.name
            destination_rel = self._rel_path_for_abs(destination_abs)
            moved_dto = AssetDTO(
                id=dto.id,
                abs_path=destination_abs,
                rel_path=destination_rel,
                media_type=dto.media_type,
                created_at=dto.created_at,
                width=dto.width,
                height=dto.height,
                duration=dto.duration,
                size_bytes=dto.size_bytes,
                metadata=dto.metadata,
                is_favorite=dto.is_favorite,
                is_live=dto.is_live,
                is_pano=dto.is_pano,
                micro_thumbnail=dto.micro_thumbnail,
            )
            pending = _PendingMove(
                dto=moved_dto,
                source_abs=path,
                destination_root=destination_root,
                destination_album_path=destination_album_path,
                destination_abs=destination_abs,
                destination_rel=destination_rel,
                is_delete=is_delete,
            )
            self._pending_moves.append(pending)
            self._pending_paths.add(key)
            if self._current_query and self._should_include_pending(pending, self._current_query):
                inserted_dtos.append(moved_dto)
        return removed_rows, inserted_dtos

    def append_dtos(self, dtos: List[AssetDTO]) -> None:
        if not dtos:
            return
        self._cached_dtos.extend(dtos)

    def _to_dto(self, asset: Asset) -> AssetDTO:
        # Resolve absolute path
        abs_path = asset.path # Default to path if already absolute
        if not asset.path.is_absolute():
            if self._library_root:
                try:
                    abs_path = (self._library_root / asset.path).resolve()
                except OSError:
                    abs_path = self._library_root / asset.path
            else:
                # Fallback if no library root (should be rare in valid app state)
                abs_path = Path(asset.path).resolve()

        # Determine derived flags
        # Robust conversion: handle both str-Enum and IntEnum/integer cases
        mt_raw = asset.media_type
        if hasattr(mt_raw, "value"):
            mt = str(mt_raw.value)
        else:
            mt = str(mt_raw)

        # Map integer/legacy values to DTO expectations
        if mt in ("1", "2", "MediaType.VIDEO"):
            mt = "video"
        elif mt in ("0", "MediaType.IMAGE"):
            mt = "image"

        is_video = (mt == "video")
        # Live photo check: if asset has live_photo_group_id or explicit type
        is_live = (mt == "live") or (asset.live_photo_group_id is not None)
        if not is_live and asset.metadata:
            live_partner = asset.metadata.get("live_partner_rel")
            live_role = asset.metadata.get("live_role")
            if live_partner and live_role != 1:
                is_live = True

        # Pano check: usually in metadata
        is_pano = False
        if asset.metadata and asset.metadata.get("is_pano"):
            is_pano = True

        micro_thumbnail = asset.metadata.get("micro_thumbnail") if asset.metadata else None
        micro_thumbnail_image = None
        if isinstance(micro_thumbnail, (bytes, bytearray, memoryview)):
            micro_thumbnail_image = image_loader.qimage_from_bytes(bytes(micro_thumbnail))

        return AssetDTO(
            id=asset.id,
            abs_path=abs_path,
            rel_path=asset.path,
            media_type=mt,
            created_at=asset.created_at,
            width=asset.width or 0,
            height=asset.height or 0,
            duration=asset.duration or 0.0,
            size_bytes=asset.size_bytes,
            metadata=asset.metadata,
            is_favorite=asset.is_favorite,
            is_live=is_live,
            is_pano=is_pano,
            micro_thumbnail=micro_thumbnail_image,
        )

    def _apply_pending_moves(self, query: AssetQuery) -> None:
        if not self._pending_moves:
            return
        updated = False
        existing_abs = {str(dto.abs_path) for dto in self._cached_dtos}
        remaining: List[_PendingMove] = []
        for pending in self._pending_moves:
            if str(pending.destination_abs) in existing_abs:
                updated = True
                self._pending_paths.discard(str(pending.source_abs))
                continue
            if not self._should_include_pending(pending, query):
                remaining.append(pending)
                continue
            self._cached_dtos.append(pending.dto)
            existing_abs.add(str(pending.destination_abs))
            updated = True
            remaining.append(pending)
        if updated:
            self._pending_moves = remaining

    def _should_include_pending(self, pending: _PendingMove, query: AssetQuery) -> bool:
        if query.is_favorite is True and not pending.dto.is_favorite:
            return False
        if query.media_types:
            is_video = pending.dto.is_video
            allowed = False
            for media_type in query.media_types:
                if media_type == MediaType.VIDEO and is_video:
                    allowed = True
                    break
                if media_type == MediaType.IMAGE and not is_video:
                    allowed = True
                    break
            if not allowed:
                return False
        if pending.is_delete:
            return query.album_path == RECENTLY_DELETED_DIR_NAME
        if query.album_path is None:
            return True
        dest_path = pending.destination_album_path
        if query.include_subalbums and dest_path.startswith(f"{query.album_path}/"):
            return True
        if dest_path == query.album_path:
            return True
        return False

    def _find_cached_dto(self, path: Path) -> Optional[AssetDTO]:
        for dto in self._cached_dtos:
            if dto.abs_path == path:
                return dto
        return None

    def _album_path_for_root(self, root: Path) -> str:
        if self._library_root is None:
            return root.name
        try:
            rel = root.resolve().relative_to(self._library_root.resolve())
        except (OSError, ValueError):
            try:
                rel = root.relative_to(self._library_root)
            except ValueError:
                return root.name
        return rel.as_posix()

    def _rel_path_for_abs(self, path: Path) -> Path:
        if self._library_root is None:
            return Path(path.name)
        try:
            return path.resolve().relative_to(self._library_root.resolve())
        except (OSError, ValueError):
            try:
                return path.relative_to(self._library_root)
            except ValueError:
                return Path(path.name)

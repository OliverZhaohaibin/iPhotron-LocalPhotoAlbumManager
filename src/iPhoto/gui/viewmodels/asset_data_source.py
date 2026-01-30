from dataclasses import dataclass
from datetime import datetime
import os
import re
from pathlib import Path
from typing import List, Optional, cast
from PySide6.QtCore import QObject, Signal

from src.iPhoto.domain.models import Asset
from src.iPhoto.domain.models.core import MediaType
from src.iPhoto.domain.models.query import AssetQuery
from src.iPhoto.domain.repositories import IAssetRepository
from src.iPhoto.application.dtos import AssetDTO
from src.iPhoto.utils import image_loader
from src.iPhoto.config import RECENTLY_DELETED_DIR_NAME, WORK_DIR_NAME

THUMBNAIL_SUFFIX_RE = re.compile(r"_(\d{2,4})x(\d{2,4})(?=\.[^.]+$)")
THUMBNAIL_MAX_DIMENSION = 512
THUMBNAIL_MAX_BYTES = 350_000
LEGACY_THUMB_DIRS = {WORK_DIR_NAME, ".photo"}

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
        self._active_root: Optional[Path] = None
        self._seen_abs_paths: set[str] = set()

    def set_library_root(self, root: Optional[Path]):
        if self._library_root == root:
            return
        self._library_root = root
        self._cached_dtos.clear()
        self._total_count = 0
        self._seen_abs_paths.clear()
        self.dataChanged.emit()

    def set_repository(self, repo: IAssetRepository) -> None:
        if self._repo is repo:
            return
        self._repo = repo
        self._cached_dtos.clear()
        self._total_count = 0
        self._seen_abs_paths.clear()
        self.dataChanged.emit()

    def set_active_root(self, root: Optional[Path]) -> None:
        self._active_root = root

    def library_root(self) -> Optional[Path]:
        """Return the configured library root for absolute-path resolution."""
        return self._library_root

    def load(self, query: AssetQuery):
        """Loads data for the given query."""
        self._current_query = query
        self._cached_dtos.clear()

        # Default limit if not set
        if not query.limit:
            query.limit = 5000

        assets = self._repo.find_by_query(query)
        dtos: List[AssetDTO] = []
        for asset in assets:
            if self._is_thumbnail_asset(asset):
                continue
            dtos.append(self._to_dto(asset))
        self._cached_dtos = dtos
        self._apply_pending_moves(query)
        self._total_count = len(self._cached_dtos)
        self._seen_abs_paths = {
            self._normalize_abs_key(dto.abs_path) for dto in self._cached_dtos
        }

        self.dataChanged.emit()

    def reload_current_query(self) -> None:
        if self._current_query is None:
            return
        self.load(self._current_query)

    def asset_at(self, index: int) -> Optional[AssetDTO]:
        if 0 <= index < len(self._cached_dtos):
            return self._cached_dtos[index]
        return None

    def find_dto_by_path(self, path: Path) -> Optional[AssetDTO]:
        """Find a cached DTO by its absolute path."""
        # Linear search is acceptable for small selections in restore/delete operations.
        # For larger datasets, we might need a hash map.
        resolved = str(path.resolve())
        for dto in self._cached_dtos:
            if str(dto.abs_path) == resolved:
                return dto
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
        for dto in dtos:
            self._seen_abs_paths.add(self._normalize_abs_key(dto.abs_path))

    def handle_scan_chunk(self, scan_root: Path, chunk: List[dict]) -> None:
        if not chunk or self._current_query is None:
            return

        if self._active_root is None:
            return

        try:
            scan_root_resolved = scan_root.resolve()
            view_root_resolved = self._active_root.resolve()
        except OSError:
            return

        is_direct_match = scan_root_resolved == view_root_resolved
        is_scan_parent = scan_root_resolved in view_root_resolved.parents
        is_scan_child = view_root_resolved in scan_root_resolved.parents

        if not (is_direct_match or is_scan_parent or is_scan_child):
            return

        appended: List[AssetDTO] = []

        for row in chunk:
            raw_rel = row.get("rel")
            if not isinstance(raw_rel, str) or not raw_rel:
                continue
            if self._scan_row_is_thumbnail(raw_rel, row):
                continue

            view_rel = self._resolve_view_rel(
                raw_rel,
                scan_root_resolved,
                view_root_resolved,
                is_scan_parent=is_scan_parent,
                is_scan_child=is_scan_child,
            )
            if view_rel is None:
                continue

            dto = self._scan_row_to_dto(view_root_resolved, view_rel, row)
            if dto is None:
                continue

            if not self._scan_row_matches_query(dto, row, self._current_query):
                continue

            abs_key = self._normalize_abs_key(dto.abs_path)
            if abs_key in self._seen_abs_paths:
                continue

            appended.append(dto)

        if not appended:
            return

        self.append_dtos(appended)
        self._total_count = len(self._cached_dtos)
        self.dataChanged.emit()

    def _resolve_view_rel(
        self,
        raw_rel: str,
        scan_root: Path,
        view_root: Path,
        *,
        is_scan_parent: bool,
        is_scan_child: bool,
    ) -> Optional[str]:
        if scan_root == view_root:
            return raw_rel

        if is_scan_parent:
            full_path = scan_root / raw_rel
            try:
                return full_path.relative_to(view_root).as_posix()
            except ValueError:
                return None
            except OSError:
                return None

        if is_scan_child:
            try:
                prefix = scan_root.relative_to(view_root).as_posix()
            except ValueError:
                return None
            prefix_slash = f"{prefix}/" if prefix else ""
            return f"{prefix_slash}{raw_rel}" if prefix_slash else raw_rel

        return None

    def _scan_row_to_dto(
        self,
        view_root: Path,
        view_rel: str,
        row: dict,
    ) -> Optional[AssetDTO]:
        abs_path = view_root / view_rel
        rel_path = Path(view_rel)

        media_type_value = row.get("media_type")
        is_video = False
        if isinstance(media_type_value, str):
            is_video = media_type_value.lower() in {"1", "video"}
        elif isinstance(media_type_value, int):
            is_video = media_type_value == 1

        if not is_video and row.get("is_video"):
            is_video = True

        is_live = bool(
            row.get("is_live")
            or row.get("live_photo_group_id")
            or row.get("live_partner_rel")
        )
        if is_video:
            is_live = False

        media_type = MediaType.VIDEO.value if is_video else MediaType.IMAGE.value
        if is_live:
            media_type = MediaType.LIVE_PHOTO.value

        created_at = None
        dt_raw = row.get("dt")
        if isinstance(dt_raw, str):
            try:
                created_at = datetime.fromisoformat(dt_raw.replace("Z", "+00:00"))
            except ValueError:
                created_at = None

        width = row.get("w") or row.get("width") or 0
        height = row.get("h") or row.get("height") or 0
        duration = row.get("dur") or row.get("duration") or 0.0
        size_bytes = row.get("bytes") or 0
        is_favorite = bool(row.get("featured") or row.get("favorite") or row.get("is_favorite"))
        is_pano = bool(row.get("is_pano"))

        return AssetDTO(
            id=str(row.get("id") or abs_path),
            abs_path=abs_path,
            rel_path=rel_path,
            media_type=media_type,
            created_at=created_at,
            width=int(width or 0),
            height=int(height or 0),
            duration=float(duration or 0.0),
            size_bytes=int(size_bytes or 0),
            metadata=dict(row),
            is_favorite=is_favorite,
            is_live=is_live,
            is_pano=is_pano,
            micro_thumbnail=row.get("micro_thumbnail"),
        )

    def _scan_row_matches_query(
        self,
        dto: AssetDTO,
        row: dict,
        query: AssetQuery,
    ) -> bool:
        if query.media_types:
            allowed = {media_type.value for media_type in query.media_types}
            if dto.media_type not in allowed:
                return False

        if query.is_favorite:
            is_favorite = bool(row.get("featured") or row.get("favorite") or row.get("is_favorite"))
            if not is_favorite:
                return False

        return True

    def _to_dto(self, asset: Asset) -> AssetDTO:
        def _coerce_positive_number(value: object) -> Optional[float]:
            if isinstance(value, bool):
                return None
            if isinstance(value, (int, float)):
                return float(value) if value > 0 else None
            if isinstance(value, str):
                try:
                    parsed = float(value)
                except ValueError:
                    return None
                return parsed if parsed > 0 else None
            return None

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
        is_image_type = mt in {"image", "photo"}
        # Live photo check: if asset has live_photo_group_id or explicit type
        is_live = (mt == "live") or (asset.live_photo_group_id is not None)
        if is_video and asset.live_photo_group_id is not None:
            is_live = False
        if not is_live and asset.metadata:
            live_partner = asset.metadata.get("live_partner_rel")
            live_role = asset.metadata.get("live_role")
            if live_partner and live_role != 1 and not is_video:
                is_live = True

        if asset.live_photo_group_id and asset.metadata is not None:
            asset.metadata.setdefault("live_photo_group_id", asset.live_photo_group_id)

        # Pano check: usually in metadata, otherwise infer from dimensions.
        is_pano = False
        metadata = asset.metadata or {}
        if metadata.get("is_pano"):
            is_pano = True
        else:
            width = _coerce_positive_number(asset.width) or _coerce_positive_number(metadata.get("w"))
            if width is None:
                width = _coerce_positive_number(metadata.get("width"))
            height = _coerce_positive_number(asset.height) or _coerce_positive_number(metadata.get("h"))
            if height is None:
                height = _coerce_positive_number(metadata.get("height"))
            aspect_ratio = _coerce_positive_number(metadata.get("aspect_ratio"))
            size_bytes = _coerce_positive_number(asset.size_bytes)
            if size_bytes is None:
                size_bytes = _coerce_positive_number(metadata.get("bytes"))

            if is_image_type and width > 0 and height > 0:
                aspect_ratio = width / height
                if aspect_ratio >= 2.0:
                    if size_bytes is not None and size_bytes > 1 * 1024 * 1024:
                        is_pano = True
                    elif size_bytes is None and width * height >= 1_000_000:
                        is_pano = True
            elif is_image_type and aspect_ratio is not None and aspect_ratio >= 2.0:
                if size_bytes is None or size_bytes > 1 * 1024 * 1024:
                    is_pano = True

        micro_thumbnail = metadata.get("micro_thumbnail")
        micro_thumbnail_image = None
        if isinstance(micro_thumbnail, (bytes, bytearray, memoryview)):
            micro_thumbnail_image = image_loader.qimage_from_bytes(bytes(micro_thumbnail))

        width_value = (
            _coerce_positive_number(asset.width)
            or _coerce_positive_number(metadata.get("w"))
            or _coerce_positive_number(metadata.get("width"))
            or 0
        )
        height_value = (
            _coerce_positive_number(asset.height)
            or _coerce_positive_number(metadata.get("h"))
            or _coerce_positive_number(metadata.get("height"))
            or 0
        )

        return AssetDTO(
            id=asset.id,
            abs_path=abs_path,
            rel_path=asset.path,
            media_type=mt,
            created_at=asset.created_at,
            width=int(width_value),
            height=int(height_value),
            duration=asset.duration or 0.0,
            size_bytes=asset.size_bytes,
            metadata=metadata,
            is_favorite=asset.is_favorite,
            is_live=is_live,
            is_pano=is_pano,
            micro_thumbnail=micro_thumbnail_image,
        )

    def _apply_pending_moves(self, query: AssetQuery) -> None:
        if not self._pending_moves:
            return
        updated = False
        existing_abs = {self._normalize_abs_key(dto.abs_path) for dto in self._cached_dtos}
        remaining: List[_PendingMove] = []
        for pending in self._pending_moves:
            if self._normalize_abs_key(pending.destination_abs) in existing_abs:
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

    def _normalize_abs_key(self, path: Path) -> str:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        return os.path.normcase(str(resolved))

    def _is_thumbnail_asset(self, asset: Asset) -> bool:
        rel_path = asset.path
        if any(part in LEGACY_THUMB_DIRS for part in rel_path.parts):
            return True

        match = THUMBNAIL_SUFFIX_RE.search(rel_path.name)
        if not match:
            return False
        try:
            width = int(match.group(1))
            height = int(match.group(2))
        except ValueError:
            return False
        if max(width, height) > THUMBNAIL_MAX_DIMENSION:
            return False

        meta = asset.metadata or {}
        row_w = asset.width or meta.get("w") or meta.get("width")
        row_h = asset.height or meta.get("h") or meta.get("height")
        try:
            if row_w is not None and row_h is not None:
                if int(row_w) != width or int(row_h) != height:
                    return False
        except (TypeError, ValueError):
            return False

        size_bytes = asset.size_bytes or meta.get("bytes")
        try:
            if size_bytes is not None and int(size_bytes) > THUMBNAIL_MAX_BYTES:
                return False
        except (TypeError, ValueError):
            return False

        return True

    def _scan_row_is_thumbnail(self, rel: str, row: dict) -> bool:
        rel_path = Path(rel)
        if any(part in LEGACY_THUMB_DIRS for part in rel_path.parts):
            return True
        match = THUMBNAIL_SUFFIX_RE.search(rel_path.name)
        if not match:
            return False
        try:
            width = int(match.group(1))
            height = int(match.group(2))
        except ValueError:
            return False
        if max(width, height) > THUMBNAIL_MAX_DIMENSION:
            return False
        row_w = row.get("w") or row.get("width")
        row_h = row.get("h") or row.get("height")
        try:
            if row_w is not None and row_h is not None:
                if int(row_w) != width or int(row_h) != height:
                    return False
        except (TypeError, ValueError):
            return False
        size_bytes = row.get("bytes")
        try:
            if size_bytes is not None and int(size_bytes) > THUMBNAIL_MAX_BYTES:
                return False
        except (TypeError, ValueError):
            return False
        return True

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

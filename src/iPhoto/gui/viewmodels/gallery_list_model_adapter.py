"""Qt adapter exposing :class:`GalleryCollectionStore` as a list model."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from PySide6.QtCore import QAbstractListModel, QModelIndex, QSize, Qt, Slot

from iPhoto.application.ports import EditServicePort
from iPhoto.application.dtos import AssetDTO
from iPhoto.gui.ui.models.roles import Roles, role_names
from iPhoto.infrastructure.services.thumbnail_cache_service import ThumbnailCacheService
from iPhoto.utils.geocoding import resolve_location_name

from .gallery_collection_store import GalleryCollectionStore


_SNAPSHOT_SEPARATOR = b"\x00"
_SNAPSHOT_NULL_MARKER = b"\xff"


class GalleryListModelAdapter(QAbstractListModel):
    """Expose a pure Python collection store to Qt item views."""

    def __init__(
        self,
        store: GalleryCollectionStore,
        thumbnail_service: ThumbnailCacheService,
        edit_service_getter: Callable[[], EditServicePort | None] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._thumbnails = thumbnail_service
        self._edit_service_getter = edit_service_getter
        self._thumb_size = QSize(512, 512)
        self._current_row = -1
        self._last_snapshot: Optional[tuple[int, bytes]] = None
        self._duration_cache: dict[Path, float] = {}

        self._store.window_changed.connect(self._on_window_changed)
        self._store.data_changed.connect(self._on_source_changed)
        self._store.row_changed.connect(self._on_row_changed)
        self._thumbnails.thumbnailReady.connect(self._on_thumbnail_ready)

    @classmethod
    def create(
        cls,
        *,
        asset_query_service,
        thumbnail_service: ThumbnailCacheService,
        edit_service_getter: Callable[[], EditServicePort | None] | None = None,
        library_root: Optional[Path] = None,
        parent=None,
    ) -> "GalleryListModelAdapter":
        store = GalleryCollectionStore(asset_query_service, library_root)
        return cls(
            store,
            thumbnail_service,
            edit_service_getter=edit_service_getter,
            parent=parent,
        )

    @property
    def store(self) -> GalleryCollectionStore:
        return self._store

    def roleNames(self) -> Dict[int, bytes]:  # type: ignore[override]
        return role_names(super().roleNames())

    def rowCount(self, parent=QModelIndex()) -> int:  # type: ignore[override]
        return self._store.count()

    def columnCount(self, parent=QModelIndex()) -> int:  # type: ignore[override]
        return 1

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:  # type: ignore[override]
        if not index.isValid():
            return None

        row = index.row()
        role_int = int(role)

        if role_int == Roles.IS_CURRENT:
            return row == self._current_row
        if role_int == Roles.IS_SPACER:
            return False

        asset: Optional[AssetDTO] = self._store.asset_at(row)
        if not asset:
            return None

        if role_int == Qt.ItemDataRole.DisplayRole:
            return asset.rel_path.name
        if role_int == Qt.DecorationRole:
            return self._thumbnails.get_thumbnail(asset.abs_path, self._thumb_size)
        if role_int == Qt.ItemDataRole.ToolTipRole:
            return str(asset.abs_path)

        if role_int == Roles.REL:
            return str(asset.rel_path)
        if role_int == Roles.ABS:
            return str(asset.abs_path)
        if role_int == Roles.ASSET_ID:
            return asset.id
        if role_int == Roles.IS_IMAGE:
            return asset.is_image
        if role_int == Roles.IS_VIDEO:
            return asset.is_video
        if role_int == Roles.IS_LIVE:
            return asset.is_live
        if role_int == Roles.LIVE_GROUP_ID:
            metadata = asset.metadata or {}
            return metadata.get("live_photo_group_id")
        if role_int in (Roles.LIVE_MOTION_REL, Roles.LIVE_MOTION_ABS):
            motion_rel, motion_abs = self._resolve_live_motion(asset)
            if role_int == Roles.LIVE_MOTION_ABS:
                return str(motion_abs) if motion_abs else None
            return str(motion_rel) if motion_rel else None
        if role_int == Roles.SIZE:
            return {
                "duration": self._effective_video_duration(asset),
                "width": asset.width,
                "height": asset.height,
                "bytes": asset.size_bytes,
            }
        if role_int == Roles.DT:
            return asset.created_at
        if role_int == Roles.FEATURED:
            return asset.is_favorite
        if role_int == Roles.LOCATION:
            metadata = asset.metadata or {}
            location = metadata.get("location") or metadata.get("place")
            if isinstance(location, str) and location.strip():
                return location
            gps = metadata.get("gps")
            if isinstance(gps, dict):
                resolved = resolve_location_name(gps)
                if resolved:
                    metadata["location"] = resolved
                    return resolved
            components = [metadata.get("city"), metadata.get("state"), metadata.get("country")]
            normalized = [str(item).strip() for item in components if item]
            return ", ".join(normalized) if normalized else None
        if role_int == Roles.MICRO_THUMBNAIL:
            return asset.micro_thumbnail
        if role_int == Roles.INFO:
            return self.info_for_row(row)
        if role_int == Roles.IS_PANO:
            return asset.is_pano
        return None

    def info_for_row(self, row: int) -> Optional[dict[str, Any]]:
        asset = self._store.asset_at(row)
        if asset is None:
            return None
        info = asset.metadata.copy() if asset.metadata else {}
        info.update(
            {
                "rel": str(asset.rel_path),
                "abs": str(asset.abs_path),
                "name": asset.rel_path.name,
                "is_video": asset.is_video,
                "w": asset.width,
                "h": asset.height,
                "dur": asset.duration,
                "bytes": asset.size_bytes,
            }
        )
        return info

    def asset_dto(self, row: int) -> Optional[AssetDTO]:
        return self._store.asset_at(row)

    @Slot(int, result="QVariant")
    def get(self, row: int):
        idx = self.index(row, 0)
        return self.data(idx, Roles.ABS)

    def row_for_path(self, path: Path) -> int | None:
        return self._store.row_for_path(path)

    def prioritize_rows(self, first: int, last: int) -> None:
        self._store.prioritize_rows(first, last)

    def pin_row(self, row: int) -> None:
        self._store.pin_row(row)

    def rebind_asset_query_service(
        self,
        asset_query_service,
        library_root: Optional[Path],
    ) -> None:
        self._last_snapshot = None
        self._duration_cache.clear()
        self._store.rebind_asset_query_service(asset_query_service, library_root)

    def invalidate_thumbnail(self, path_str: str) -> None:
        path = Path(path_str)
        self._thumbnails.invalidate(path, size=self._thumb_size)
        self._duration_cache.pop(path, None)
        row = self._store.row_for_path(path)
        if row is None:
            return
        idx = self.index(row, 0)
        if idx.isValid():
            self.dataChanged.emit(idx, idx, [Qt.DecorationRole, Roles.SIZE])

    def update_favorite(self, row: int, is_favorite: bool) -> None:
        self._store.update_favorite_status(row, is_favorite)
        idx = self.index(row, 0)
        if idx.isValid():
            self.dataChanged.emit(idx, idx, [Roles.FEATURED])

    def optimistic_move_paths(self, paths: list[Path], destination_root: Path, *, is_delete: bool) -> bool:
        removed_rows, inserted_dtos = self._store.apply_optimistic_move(
            paths,
            destination_root,
            is_delete=is_delete,
        )
        if removed_rows:
            rows = sorted(set(removed_rows), reverse=True)
            for row in rows:
                self.beginRemoveRows(QModelIndex(), row, row)
                self._store.remove_rows([row], emit=False)
                self.endRemoveRows()
        if inserted_dtos:
            start = self.rowCount()
            end = start + len(inserted_dtos) - 1
            self.beginInsertRows(QModelIndex(), start, end)
            self._store.append_dtos(inserted_dtos)
            self.endInsertRows()
        return bool(removed_rows or inserted_dtos)

    def removeRows(self, row: int, count: int, parent: QModelIndex = QModelIndex()) -> bool:  # type: ignore[override]
        if count <= 0 or row < 0:
            return False
        rows = list(range(row, row + count))
        self.beginRemoveRows(parent, row, row + count - 1)
        self._store.remove_rows(rows, emit=False)
        self.endRemoveRows()
        return True

    def set_current_row(self, row: int) -> None:
        if self._current_row == row:
            return
        old_row = self._current_row
        self._current_row = row
        if row >= 0:
            self.pin_row(row)
        if old_row >= 0:
            idx = self.index(old_row, 0)
            if idx.isValid():
                self.dataChanged.emit(idx, idx, [Roles.IS_CURRENT, Qt.ItemDataRole.SizeHintRole])
        if row >= 0:
            idx = self.index(row, 0)
            if idx.isValid():
                self.dataChanged.emit(idx, idx, [Roles.IS_CURRENT, Qt.ItemDataRole.SizeHintRole])

    def metadata_for_path(self, path: Path) -> Optional[Dict[str, Any]]:
        dto = self._store.find_dto_by_path(path)
        if not dto:
            return None
        meta = dto.metadata.copy() if dto.metadata else {}
        meta.update(
            {
                "is_live": dto.is_live,
                "rel": str(dto.rel_path),
                "abs": str(dto.abs_path),
            }
        )
        if dto.is_live:
            motion_rel, motion_abs = self._resolve_live_motion(dto)
            if motion_abs:
                meta["live_motion_abs"] = str(motion_abs)
            if motion_rel:
                meta["live_motion_rel"] = str(motion_rel)
        return meta

    def _resolve_live_motion(self, asset: AssetDTO) -> tuple[Optional[Path], Optional[Path]]:
        metadata = asset.metadata or {}
        live_partner_rel = metadata.get("live_partner_rel")
        live_role = metadata.get("live_role")
        if isinstance(live_partner_rel, str) and live_partner_rel and live_role != 1:
            rel_path = Path(live_partner_rel)
            if rel_path.is_absolute():
                return rel_path, rel_path
            library_root = self._store.library_root()
            if library_root is not None:
                return rel_path, (library_root / rel_path).resolve()
            return rel_path, None

        group_id = metadata.get("live_photo_group_id")
        if not group_id:
            return None, None
        for row in range(self._store.count()):
            candidate = self._store.asset_at(row)
            if candidate is None or not candidate.is_video:
                continue
            candidate_group = (candidate.metadata or {}).get("live_photo_group_id")
            if candidate_group == group_id:
                return candidate.rel_path, candidate.abs_path
        return None, None

    def _on_source_changed(self) -> None:
        count = self._store.count()
        current_hash = self._snapshot_hash(count)
        current_snapshot = (count, current_hash)
        if self._last_snapshot == current_snapshot:
            return
        self._duration_cache.clear()
        self.beginResetModel()
        self.endResetModel()
        self._last_snapshot = current_snapshot
        if self._current_row >= count:
            self._current_row = -1

    def _on_window_changed(self, first: int, last: int) -> None:
        count = self.rowCount()
        if count <= 0:
            return
        first = max(0, min(first, count - 1))
        last = max(first, min(last, count - 1))
        top = self.index(first, 0)
        bottom = self.index(last, 0)
        if top.isValid() and bottom.isValid():
            self.dataChanged.emit(top, bottom, [])

    def _on_thumbnail_ready(self, path: Path) -> None:
        row = self._store.row_for_path(path)
        if row is None:
            return
        idx = self.index(row, 0)
        if idx.isValid():
            self.dataChanged.emit(idx, idx, [Qt.DecorationRole])

    def _on_row_changed(self, row: int) -> None:
        idx = self.index(row, 0)
        if idx.isValid():
            self.dataChanged.emit(
                idx,
                idx,
                [Roles.FEATURED, Roles.INFO, Roles.LOCATION, Roles.SIZE],
            )

    def _snapshot_hash(self, count: int) -> bytes:
        digest = hashlib.blake2b(digest_size=16)
        for row in range(count):
            asset = self._store.asset_at(row)
            if asset is None:
                digest.update(_SNAPSHOT_NULL_MARKER)
            else:
                digest.update(self._get_asset_path_bytes(asset))
            digest.update(_SNAPSHOT_SEPARATOR)
        return digest.digest()

    @staticmethod
    def _get_asset_path_bytes(asset: object) -> bytes:
        abs_path = getattr(asset, "abs_path", None) or getattr(asset, "path", None)
        return b"" if abs_path is None else str(abs_path).encode("utf-8")

    def _effective_video_duration(self, asset: AssetDTO) -> float:
        if not asset.is_video:
            return asset.duration
        if asset.abs_path in self._duration_cache:
            return self._duration_cache[asset.abs_path]
        edit_service = self._edit_service_getter() if self._edit_service_getter else None
        if edit_service is not None:
            state = edit_service.describe_adjustments(
                asset.abs_path,
                duration_hint=asset.duration,
            )
            effective = (
                state.effective_duration_sec
                if state.effective_duration_sec is not None
                else asset.duration
            )
        else:
            effective = asset.duration
        self._duration_cache[asset.abs_path] = effective
        return effective

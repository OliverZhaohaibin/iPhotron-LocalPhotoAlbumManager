from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, cast
import logging

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QObject,
    QSize,
    Qt,
    Slot,
)
from src.iPhoto.application.dtos import AssetDTO
from src.iPhoto.domain.models.query import AssetQuery
from src.iPhoto.gui.ui.models.roles import Roles
from src.iPhoto.gui.viewmodels.asset_data_source import AssetDataSource
from src.iPhoto.infrastructure.services.thumbnail_cache_service import ThumbnailCacheService
from src.iPhoto.utils.geocoding import resolve_location_name


_LOGGER = logging.getLogger(__name__)


def _build_non_layout_roles() -> tuple[int, ...]:
    roles = {
        int(Qt.ItemDataRole.DisplayRole),
        int(Qt.ItemDataRole.DecorationRole),
        int(Qt.ItemDataRole.ToolTipRole),
    }
    roles.update(
        int(role) for role in Roles if role not in (Roles.IS_CURRENT, Roles.IS_SPACER)
    )
    return tuple(sorted(roles))


class AssetListViewModel(QAbstractListModel):
    """
    ViewModel backing the asset grid and filmstrip.
    Adapts AssetDTOs to Qt Roles expected by AssetGridDelegate.
    """

    _NON_LAYOUT_ROLES = _build_non_layout_roles()

    def __init__(self, data_source: AssetDataSource, thumbnail_service: ThumbnailCacheService, parent=None):
        super().__init__(parent)
        self._data_source = data_source
        self._thumbnails = thumbnail_service
        self._thumb_size = QSize(512, 512)
        self._current_row = -1

        # Connect signals
        self._data_source.dataChanged.connect(self._on_source_changed)
        self._thumbnails.thumbnailReady.connect(self._on_thumbnail_ready)
        # Track the last observed asset count; None means no prior snapshot yet.
        self._last_count: int | None = None

    def load_query(self, query: AssetQuery):
        """Triggers data loading for a new query."""
        self._last_count = None
        self._data_source.load(query)

    def set_active_root(self, root: Optional[Path]) -> None:
        """Update the active root so scan chunks map to the current view."""
        self._data_source.set_active_root(root)

    def rowCount(self, parent=QModelIndex()) -> int:
        return self._data_source.count()

    def columnCount(self, parent=QModelIndex()) -> int:
        """Explicitly return 1 column to avoid PySide6/Qt proxy model issues."""
        return 1

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None

        row = index.row()
        asset: Optional[AssetDTO] = self._data_source.asset_at(row)
        if not asset:
            return None

        # Convert Enum/Flag roles to int if necessary (PySide6 usually passes int)
        role_int = int(role)

        # --- Standard Roles ---
        if role_int == Qt.ItemDataRole.DisplayRole:
            # Delegate handles drawing, but we return filename for accessibility/fallback.
            # If the delegate is missing, this causes text to appear.
            # With AssetGridDelegate now set, this shouldn't be visible, but if it is,
            # we could return None. However, returning name is correct semantic behavior.
            return asset.rel_path.name

        if role_int == Qt.DecorationRole:
            # Main thumbnail - Async: returns None if not ready
            return self._thumbnails.get_thumbnail(asset.abs_path, self._thumb_size)

        if role_int == Qt.ItemDataRole.ToolTipRole:
            return str(asset.abs_path)

        # --- Custom Roles (AssetModel contract) ---

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
            # Delegate expects a dict with 'duration', 'width', 'height' etc.
            # or just for duration extraction.
            return {
                "duration": asset.duration,
                "width": asset.width,
                "height": asset.height,
                "bytes": asset.size_bytes
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
            components = [
                metadata.get("city"),
                metadata.get("state"),
                metadata.get("country"),
            ]
            normalized = [str(item).strip() for item in components if item]
            return ", ".join(normalized) if normalized else None

        if role_int == Roles.MICRO_THUMBNAIL:
            # Optional: Return a tiny cached image if available
            return asset.micro_thumbnail

        if role_int == Roles.INFO:
            # Metadata dictionary
            info = asset.metadata.copy() if asset.metadata else {}
            # Ensure critical keys exist for InfoPanel
            info.update({
                "rel": str(asset.rel_path),
                "abs": str(asset.abs_path),
                "w": asset.width,
                "h": asset.height,
                "dur": asset.duration,
                "bytes": asset.size_bytes
            })
            return info

        if role_int == Roles.IS_PANO:
            return asset.is_pano

        if role_int == Roles.IS_SPACER:
            return False # ViewModels usually don't have spacers, unless inserted

        if role_int == Roles.IS_CURRENT:
            return row == self._current_row

        return None

    def _resolve_live_motion(self, asset: AssetDTO) -> tuple[Optional[Path], Optional[Path]]:
        """Return the Live Photo motion relative/absolute paths if known."""
        metadata = asset.metadata or {}
        live_partner_rel = metadata.get("live_partner_rel")
        live_role = metadata.get("live_role")
        if isinstance(live_partner_rel, str) and live_partner_rel and live_role != 1:
            rel_path = Path(live_partner_rel)
            if rel_path.is_absolute():
                return rel_path, rel_path
            library_root = self._data_source.library_root()
            if library_root is not None:
                return rel_path, (library_root / rel_path).resolve()
            return rel_path, None

        group_id = metadata.get("live_photo_group_id")
        if not group_id:
            return None, None
        for idx in range(self._data_source.count()):
            candidate = self._data_source.asset_at(idx)
            if candidate is None or not candidate.is_video:
                continue
            candidate_group = (candidate.metadata or {}).get("live_photo_group_id")
            if candidate_group == group_id:
                return candidate.rel_path, candidate.abs_path
        return None, None

    def _on_source_changed(self):
        count = self._data_source.count()
        if self._last_count is not None and count == self._last_count:
            bottom_row = max(count - 1, 0)
            top = self.index(0, 0)
            bottom = self.index(bottom_row, 0)
            # Defensive: QModelIndex validity can fail during Qt reset/layout churn.
            if top.isValid() and bottom.isValid():
                self.dataChanged.emit(top, bottom, self._NON_LAYOUT_ROLES)
            elif count != 0:
                _LOGGER.warning(
                    "Skipped dataChanged emission due to invalid indices (top=%s, bottom=%s)",
                    top.isValid(),
                    bottom.isValid(),
                )
            return
        self.beginResetModel()
        self.endResetModel()
        self._last_count = count

    def _on_thumbnail_ready(self, path: Path):
        # Find index for path and emit dataChanged
        # Linear search for now (optimization: use a dict map in DataSource)
        count = self.rowCount()
        for row in range(count):
            asset = self._data_source.asset_at(row)
            if asset and asset.abs_path == path:
                idx = self.index(row, 0)
                self.dataChanged.emit(idx, idx, [Qt.DecorationRole])
                break

    # --- QML / Scriptable Helpers ---

    @Slot(int, result="QVariant")
    def get(self, row: int):
        idx = self.index(row, 0)
        return self.data(idx, Roles.ABS)

    def invalidate_thumbnail(self, path_str: str):
        """Forces a thumbnail refresh for the given path."""
        path = Path(path_str)
        self._thumbnails.invalidate(path, size=self._thumb_size)
        # Notify views
        count = self.rowCount()
        for row in range(count):
            asset = self._data_source.asset_at(row)
            if asset:
                if str(asset.abs_path) == path_str or str(asset.rel_path) == path_str:
                    idx = self.index(row, 0)
                    self.dataChanged.emit(idx, idx, [Qt.DecorationRole])
                    break

    def thumbnail_loader(self):
        pass

    def prioritize_rows(self, first: int, last: int):
        pass

    def update_favorite(self, row: int, is_favorite: bool):
        """Updates the favorite status in the data source and notifies views."""
        self._data_source.update_favorite_status(row, is_favorite)
        idx = self.index(row, 0)
        self.dataChanged.emit(idx, idx, [Roles.FEATURED])

    def optimistic_move_paths(self, paths: list[Path], destination_root: Path, *, is_delete: bool) -> bool:
        removed_rows, inserted_dtos = self._data_source.apply_optimistic_move(
            paths,
            destination_root,
            is_delete=is_delete,
        )
        if removed_rows:
            rows = sorted(set(removed_rows), reverse=True)
            for row in rows:
                self.beginRemoveRows(QModelIndex(), row, row)
                self._data_source.remove_rows([row], emit=False)
                self.endRemoveRows()
        if inserted_dtos:
            start = self.rowCount()
            end = start + len(inserted_dtos) - 1
            self.beginInsertRows(QModelIndex(), start, end)
            self._data_source.append_dtos(inserted_dtos)
            self.endInsertRows()
        return bool(removed_rows or inserted_dtos)

    def removeRows(
        self,
        row: int,
        count: int,
        parent: QModelIndex = QModelIndex(),
    ) -> bool:  # type: ignore[override]
        if count <= 0 or row < 0:
            return False
        rows = list(range(row, row + count))
        self.beginRemoveRows(parent, row, row + count - 1)
        self._data_source.remove_rows(rows)
        self.endRemoveRows()
        return True

    def set_current_row(self, row: int):
        """Update the currently active row (for filmstrip highlighting)."""
        if self._current_row == row:
            return

        old_row = self._current_row
        self._current_row = row

        # Notify old row
        if old_row >= 0:
            idx = self.index(old_row, 0)
            if idx.isValid():
                self.dataChanged.emit(idx, idx, [Roles.IS_CURRENT, Qt.ItemDataRole.SizeHintRole])

        # Notify new row
        if row >= 0:
            idx = self.index(row, 0)
            if idx.isValid():
                self.dataChanged.emit(idx, idx, [Roles.IS_CURRENT, Qt.ItemDataRole.SizeHintRole])

    def metadata_for_path(self, path: Path) -> Optional[Dict[str, Any]]:
        """Return metadata for the given path to support legacy Facade operations."""
        dto = self._data_source.find_dto_by_path(path)
        if not dto:
            return None

        # Construct legacy-compatible metadata dict
        meta = dto.metadata.copy() if dto.metadata else {}
        meta.update({
            "is_live": dto.is_live,
            "rel": str(dto.rel_path),
            "abs": str(dto.abs_path),
        })

        # Resolve live motion if needed
        if dto.is_live:
            motion_rel, motion_abs = self._resolve_live_motion(dto)
            if motion_abs:
                meta["live_motion_abs"] = str(motion_abs)
            if motion_rel:
                meta["live_motion_rel"] = str(motion_rel)

        return meta

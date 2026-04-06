from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, Optional

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QSize,
    Qt,
    Slot,
)
from iPhoto.application.dtos import AssetDTO
from iPhoto.core.adjustment_mapping import normalise_video_trim
from iPhoto.domain.models.query import AssetQuery
from iPhoto.gui.ui.models.roles import Roles
from iPhoto.gui.viewmodels.asset_data_source import AssetDataSource
from iPhoto.infrastructure.services.thumbnail_cache_service import ThumbnailCacheService
from iPhoto.io import sidecar as _io_sidecar
from iPhoto.utils.geocoding import resolve_location_name


_SNAPSHOT_SEPARATOR = b"\x00"
_SNAPSHOT_NULL_MARKER = b"\xff"


class AssetListViewModel(QAbstractListModel):
    """
    ViewModel backing the asset grid and filmstrip.
    Adapts AssetDTOs to Qt Roles expected by AssetGridDelegate.
    """

    def __init__(self, data_source: AssetDataSource, thumbnail_service: ThumbnailCacheService, parent=None):
        super().__init__(parent)
        self._data_source = data_source
        self._thumbnails = thumbnail_service
        self._thumb_size = QSize(512, 512)
        self._current_row = -1

        # Connect signals
        window_changed = getattr(self._data_source, "windowChanged", None)
        if window_changed is not None and hasattr(window_changed, "connect"):
            window_changed.connect(self._on_window_changed)
        count_changed = getattr(self._data_source, "countChanged", None)
        if count_changed is not None and hasattr(count_changed, "connect"):
            count_changed.connect(self._on_count_changed)
        self._thumbnails.thumbnailReady.connect(self._on_thumbnail_ready)
        # Track the last observed asset signature; None means no prior snapshot yet.
        self._last_snapshot: Optional[tuple[int, bytes]] = None
        # Cache of effective (sidecar-trimmed) durations keyed by asset abs_path.
        # Populated lazily in _effective_video_duration(); invalidated on thumbnail
        # invalidation (post-edit save) and model reset (album switch).
        self._duration_cache: dict[Path, float] = {}

    def load_query(self, query: AssetQuery):
        """Triggers data loading for a new query."""
        self._last_snapshot = None
        self.beginResetModel()
        try:
            self._data_source.load(query)
        finally:
            self.endResetModel()

    def load_geotagged_assets(self, assets: list, library_root: Path) -> None:
        """Load a pre-computed list of geotagged assets for cluster gallery view.

        This enables O(1) cluster gallery opening by directly accepting assets
        already aggregated during map clustering, avoiding database queries.

        Args:
            assets: List of GeotaggedAsset objects from the clicked map cluster.
            library_root: The library root path for resolving absolute paths.
        """
        self._last_snapshot = None
        self.beginResetModel()
        try:
            self._data_source.load_geotagged_assets(assets, library_root)
        finally:
            self.endResetModel()

    def reload_current_query(self) -> None:
        """Reload the active query with a controlled model reset."""
        self._last_snapshot = None
        self.beginResetModel()
        try:
            self._data_source.reload_current_query()
        finally:
            self.endResetModel()

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
        role_int = int(role)

        if role_int == Roles.IS_CURRENT:
            return row == self._current_row

        if role_int == Roles.IS_SPACER:
            return False

        asset: Optional[AssetDTO] = self._data_source.asset_at(row)
        if not asset:
            return None

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
                "duration": self._effective_video_duration(asset),
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
        iter_cached_rows = getattr(self._data_source, "_iter_cached_rows", None)
        if callable(iter_cached_rows):
            candidates = [candidate for _, candidate in iter_cached_rows()]
        else:
            candidates = [
                self._data_source.asset_at(idx) for idx in range(self._data_source.count())
            ]
        for candidate in candidates:
            if candidate is None or not candidate.is_video:
                continue
            candidate_group = (candidate.metadata or {}).get("live_photo_group_id")
            if candidate_group == group_id:
                return candidate.rel_path, candidate.abs_path
        return None, None

    def _on_source_changed(self):
        count = self._data_source.count()
        current_hash = self._snapshot_hash(count)
        current_snapshot = (count, current_hash)
        if self._last_snapshot == current_snapshot:
            # No changes detected; avoid unnecessary model reset to prevent full filmstrip refresh.
            return
        self._duration_cache.clear()
        self.beginResetModel()
        self.endResetModel()
        self._last_snapshot = current_snapshot

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

    def _on_count_changed(self, old_count: int, new_count: int) -> None:
        if old_count == new_count:
            return
        if self._current_row >= new_count:
            self._current_row = -1
        self._last_snapshot = None
        self._duration_cache.clear()
        self.beginResetModel()
        self.endResetModel()

    def _on_thumbnail_ready(self, path: Path):
        row_for_path = getattr(self._data_source, "row_for_path", None)
        row = row_for_path(path) if callable(row_for_path) else None
        if row is None:
            return
        idx = self.index(row, 0)
        if idx.isValid():
            self.dataChanged.emit(idx, idx, [Qt.DecorationRole])

    def _snapshot_hash(self, count: int) -> bytes:
        digest = hashlib.blake2b(digest_size=16)
        for row in range(count):
            asset = self._data_source.asset_at(row)
            if asset is None:
                digest.update(_SNAPSHOT_NULL_MARKER)
            else:
                digest.update(self._get_asset_path_bytes(asset))
            # Separate entries so ordering changes alter the signature.
            digest.update(_SNAPSHOT_SEPARATOR)
        return digest.digest()

    @staticmethod
    def _get_asset_path_bytes(asset: object) -> bytes:
        abs_path = getattr(asset, "abs_path", None) or getattr(asset, "path", None)
        return b"" if abs_path is None else str(abs_path).encode("utf-8")

    # --- QML / Scriptable Helpers ---

    @Slot(int, result="QVariant")
    def get(self, row: int):
        idx = self.index(row, 0)
        return self.data(idx, Roles.ABS)

    def invalidate_thumbnail(self, path_str: str):
        """Forces a thumbnail refresh for the given path."""
        path = Path(path_str)
        self._thumbnails.invalidate(path, size=self._thumb_size)
        # Clear any cached effective duration so the gallery badge re-reads the
        # sidecar the next time this cell is painted (picks up trim edits).
        self._duration_cache.pop(path, None)
        row_for_path = getattr(self._data_source, "row_for_path", None)
        row = row_for_path(path) if callable(row_for_path) else None
        if row is None:
            return
        idx = self.index(row, 0)
        if idx.isValid():
            self.dataChanged.emit(idx, idx, [Qt.DecorationRole, Roles.SIZE])

    def thumbnail_loader(self):
        pass

    def _effective_video_duration(self, asset: AssetDTO) -> float:
        """Return the effective playable duration for *asset*, respecting sidecar trim.

        For non-video assets the raw file duration is returned unchanged.  For
        video assets the sidecar is consulted once per path (result is cached)
        to apply any saved trim in/out points, so the gallery badge reflects the
        trimmed clip length rather than the full container duration.

        The sidecar read is performed only on first access per path; subsequent
        calls are served from ``_duration_cache`` with no I/O overhead.
        """
        if not asset.is_video:
            return asset.duration
        if asset.abs_path in self._duration_cache:
            return self._duration_cache[asset.abs_path]
        adjustments = _io_sidecar.load_adjustments(asset.abs_path)
        if adjustments:
            trim_in, trim_out = normalise_video_trim(adjustments, asset.duration)
            effective = trim_out - trim_in
        else:
            effective = asset.duration
        self._duration_cache[asset.abs_path] = effective
        return effective

    def prioritize_rows(self, first: int, last: int):
        prioritize = getattr(self._data_source, "prioritize_rows", None)
        if callable(prioritize):
            prioritize(first, last)

    def pin_row(self, row: int) -> None:
        pin = getattr(self._data_source, "pin_row", None)
        if callable(pin):
            pin(row)

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
        self._data_source.remove_rows(rows, emit=False)
        self.endRemoveRows()
        return True

    def set_current_row(self, row: int):
        """Update the currently active row (for filmstrip highlighting)."""
        if self._current_row == row:
            return

        old_row = self._current_row
        self._current_row = row
        if row >= 0:
            self.pin_row(row)

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

"""Qt adapter exposing :class:`GalleryCollectionStore` as a list model."""

from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Any, Callable, Deque, Dict, Optional

from PySide6.QtCore import QAbstractListModel, QModelIndex, QSize, Qt, QTimer, Slot

from iPhoto.application.ports import EditServicePort
from iPhoto.application.dtos import AssetDTO
from iPhoto.gui.ui.models.roles import Roles, role_names
from iPhoto.infrastructure.services.thumbnail_cache_service import ThumbnailCacheService
from iPhoto.utils.geocoding import resolve_location_name
from iPhoto.utils import image_loader

from .gallery_collection_store import GalleryCollectionStore
from .gallery_page_loader import GalleryPageLoader, GalleryPageRequest, GalleryPageResult

class GalleryListModelAdapter(QAbstractListModel):
    """Expose a pure Python collection store to Qt item views."""

    THUMBNAIL_TIERS = (256, 384, 512, 768)
    MICRO_PREVIEW_BATCH_SIZE = 8
    FULL_THUMBNAIL_PREFETCH_BATCH_SIZE = 24

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
        self._last_selection_revision = int(getattr(store, "selection_revision", 0))
        self._last_count = int(store.count())
        self._duration_cache: dict[Path, float] = {}
        self._visible_row_range: tuple[int, int] | None = None
        self._pending_micro_rows: set[int] = set()
        self._micro_preview_queue: Deque[int] = deque()
        self._micro_preview_timer = QTimer(self)
        self._micro_preview_timer.setSingleShot(True)
        self._micro_preview_timer.timeout.connect(self._drain_micro_preview_queue)
        self._pending_full_thumbnail_rows: set[int] = set()
        self._full_thumbnail_prefetch_queue: Deque[int] = deque()
        self._full_thumbnail_prefetch_timer = QTimer(self)
        self._full_thumbnail_prefetch_timer.setSingleShot(True)
        self._full_thumbnail_prefetch_timer.timeout.connect(
            self._drain_full_thumbnail_prefetch_queue
        )
        self._page_loader = GalleryPageLoader(self)

        self._store.window_changed.connect(self._on_window_changed)
        self._store.data_changed.connect(self._on_source_changed)
        self._store.row_changed.connect(self._on_row_changed)
        window_load_requested = getattr(self._store, "window_load_requested", None)
        if window_load_requested is not None:
            window_load_requested.connect(self._on_window_load_requested)
        self._thumbnails.thumbnailReady.connect(self._on_thumbnail_ready)
        self._page_loader.pageLoaded.connect(self._on_window_load_result)
        self._page_loader.pageFailed.connect(self._on_window_load_failed)

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
        first = max(0, int(first))
        last = max(first, int(last))
        self._visible_row_range = (first, last)
        self._store.prioritize_rows(first, last)
        self._schedule_visible_micro_preview_backfill()
        self._schedule_visible_full_thumbnail_prefetch(first, last)

    def pin_row(self, row: int) -> None:
        self._store.pin_row(row)

    @Slot(QSize)
    def set_thumbnail_target_size(self, size: QSize) -> None:
        if not size.isValid() or size.isEmpty():
            return
        longest_edge = max(size.width(), size.height())
        tier = self._thumbnail_tier_for_edge(longest_edge)
        next_size = QSize(tier, tier)
        if self._thumb_size == next_size:
            return
        self._thumb_size = next_size
        self._emit_visible_range_update([Qt.DecorationRole, Roles.MICRO_THUMBNAIL])
        self._schedule_visible_full_thumbnail_prefetch()

    def rebind_asset_query_service(
        self,
        asset_query_service,
        library_root: Optional[Path],
    ) -> None:
        self._last_selection_revision = -1
        self._last_count = -1
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
            # Full-store scans must stay synchronous so live-photo resolution
            # does not enqueue and cancel background paging requests.
            candidate = self._store.asset_at_sync(row)
            if candidate is None or not candidate.is_video:
                continue
            candidate_group = (candidate.metadata or {}).get("live_photo_group_id")
            if candidate_group == group_id:
                return candidate.rel_path, candidate.abs_path
        return None, None

    def _on_source_changed(self) -> None:
        count = self._store.count()
        selection_revision = int(getattr(self._store, "selection_revision", 0))
        if (
            selection_revision == self._last_selection_revision
            and count == self._last_count
        ):
            return
        self._duration_cache.clear()
        self._visible_row_range = None
        self._micro_preview_timer.stop()
        self._pending_micro_rows.clear()
        self._micro_preview_queue.clear()
        self._full_thumbnail_prefetch_timer.stop()
        self._pending_full_thumbnail_rows.clear()
        self._full_thumbnail_prefetch_queue.clear()
        self.beginResetModel()
        self.endResetModel()
        self._last_selection_revision = selection_revision
        self._last_count = count
        if self._current_row >= count:
            self._current_row = -1

    def _on_window_changed(self, first: int, last: int) -> None:
        self._emit_range_update(first, last, [])
        self._schedule_visible_micro_preview_backfill()
        self._schedule_visible_full_thumbnail_prefetch()

    def _on_thumbnail_ready(self, path: Path, size: QSize) -> None:
        if QSize(size) != self._thumb_size:
            return
        cached_row_for_path = getattr(self._store, "cached_row_for_path", None)
        if callable(cached_row_for_path):
            row = cached_row_for_path(path)
        else:
            row = self._store.row_for_path(path)
        if row is None:
            return
        asset = self._store.asset_at(row)
        if asset is not None and asset.micro_thumbnail is not None:
            asset.micro_thumbnail = None
        idx = self.index(row, 0)
        if idx.isValid():
            self.dataChanged.emit(idx, idx, [Qt.DecorationRole, Roles.MICRO_THUMBNAIL])

    def _on_row_changed(self, row: int) -> None:
        idx = self.index(row, 0)
        if idx.isValid():
            self.dataChanged.emit(
                idx,
                idx,
                [Roles.FEATURED, Roles.INFO, Roles.LOCATION, Roles.SIZE],
            )

    def _on_window_load_requested(self, request: GalleryPageRequest) -> None:
        self._page_loader.load(
            asset_query_service=self._store.asset_query_service(),
            library_root=self._store.library_root(),
            request=request,
        )

    def _on_window_load_result(self, result: GalleryPageResult) -> None:
        self._store.handle_window_load_result(result)

    def _on_window_load_failed(self, request_id: int, selection_revision: int) -> None:
        self._store.handle_window_load_failed(request_id, selection_revision)

    @classmethod
    def _thumbnail_tier_for_edge(cls, edge: int) -> int:
        required = max(1, int(edge))
        for tier in cls.THUMBNAIL_TIERS:
            if tier >= required:
                return tier
        return cls.THUMBNAIL_TIERS[-1]

    def _emit_visible_range_update(self, roles: list[int]) -> None:
        visible = self._visible_row_range
        if visible is None:
            return
        self._emit_range_update(visible[0], visible[1], roles)

    def _emit_range_update(self, first: int, last: int, roles: list[int]) -> None:
        count = self.rowCount()
        if count <= 0:
            return
        first = max(0, min(first, count - 1))
        last = max(first, min(last, count - 1))
        top = self.index(first, 0)
        bottom = self.index(last, 0)
        if top.isValid() and bottom.isValid():
            self.dataChanged.emit(top, bottom, roles)

    def _schedule_visible_micro_preview_backfill(self) -> None:
        visible = self._visible_row_range
        if visible is None:
            return
        first, last = visible
        count = self.rowCount()
        if count <= 0:
            return
        first = max(0, min(first, count - 1))
        last = max(first, min(last, count - 1))
        for row in range(first, last + 1):
            if row in self._pending_micro_rows:
                continue
            asset = self._store.asset_at(row)
            if asset is None or asset.micro_thumbnail is not None:
                continue
            metadata = asset.metadata or {}
            raw = metadata.get("micro_thumbnail")
            if not isinstance(raw, (bytes, bytearray, memoryview)):
                continue
            self._pending_micro_rows.add(row)
            self._micro_preview_queue.append(row)
        if self._micro_preview_queue and not self._micro_preview_timer.isActive():
            self._micro_preview_timer.start(0)

    def _drain_micro_preview_queue(self) -> None:
        processed = 0
        while self._micro_preview_queue and processed < self.MICRO_PREVIEW_BATCH_SIZE:
            row = self._micro_preview_queue.popleft()
            self._pending_micro_rows.discard(row)
            processed += 1
            if not self._row_is_visible(row):
                continue
            asset = self._store.asset_at(row)
            if asset is None or asset.micro_thumbnail is not None:
                continue
            metadata = asset.metadata or {}
            raw = metadata.get("micro_thumbnail")
            if not isinstance(raw, (bytes, bytearray, memoryview)):
                continue
            image = image_loader.qimage_from_bytes(bytes(raw))
            if image is None or image.isNull():
                continue
            asset.micro_thumbnail = image
            idx = self.index(row, 0)
            if idx.isValid():
                self.dataChanged.emit(idx, idx, [Qt.DecorationRole, Roles.MICRO_THUMBNAIL])
        if self._micro_preview_queue:
            self._micro_preview_timer.start(0)

    def _schedule_visible_full_thumbnail_prefetch(
        self,
        first: int | None = None,
        last: int | None = None,
    ) -> None:
        visible = self._visible_row_range
        if first is None or last is None:
            if visible is None:
                return
            first, last = visible

        count = self.rowCount()
        if count <= 0:
            return
        first = max(0, min(int(first), count - 1))
        last = max(first, min(int(last), count - 1))

        for row in range(first, last + 1):
            if row in self._pending_full_thumbnail_rows:
                continue
            asset = self._store.asset_at(row)
            if asset is None:
                continue
            self._pending_full_thumbnail_rows.add(row)
            self._full_thumbnail_prefetch_queue.append(row)

        if (
            self._full_thumbnail_prefetch_queue
            and not self._full_thumbnail_prefetch_timer.isActive()
        ):
            self._full_thumbnail_prefetch_timer.start(0)

    def _drain_full_thumbnail_prefetch_queue(self) -> None:
        processed = 0
        while (
            self._full_thumbnail_prefetch_queue
            and processed < self.FULL_THUMBNAIL_PREFETCH_BATCH_SIZE
        ):
            row = self._full_thumbnail_prefetch_queue.popleft()
            self._pending_full_thumbnail_rows.discard(row)
            processed += 1
            asset = self._store.asset_at(row)
            if asset is None:
                continue
            self._thumbnails.get_thumbnail(asset.abs_path, self._thumb_size)
        if self._full_thumbnail_prefetch_queue:
            self._full_thumbnail_prefetch_timer.start(0)

    def _row_is_visible(self, row: int) -> bool:
        visible = self._visible_row_range
        if visible is None:
            return False
        first, last = visible
        return first <= row <= last

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

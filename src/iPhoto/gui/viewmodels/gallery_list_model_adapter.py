"""Qt adapter exposing :class:`GalleryCollectionStore` as a list model."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, Optional

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QSize,
    Qt,
    QThread,
    QTimer,
    Slot,
)
from PySide6.QtCore import (
    Signal as QtSignal,
)
from PySide6.QtGui import QImage, QPixmap

from iPhoto.application.dtos import AssetDTO
from iPhoto.application.ports import EditServicePort
from iPhoto.domain.models.query import AssetQuery
from iPhoto.gui.gallery_demand import GalleryViewportDemand
from iPhoto.gui.ui.models.roles import Roles, role_names
from iPhoto.infrastructure.services.performance_events import (
    emit_perf_event,
    perf_logging_enabled,
)
from iPhoto.infrastructure.services.thumbnail_cache_service import (
    ThumbnailCacheService,
    ThumbnailPrefetchCandidate,
)
from iPhoto.utils.geocoding import resolve_location_name

from .gallery_collection_store import GalleryCollectionStore
from .gallery_thumbnail_hint_loader import (
    GalleryThumbnailCandidate,
    GalleryThumbnailHintLoader,
    GalleryThumbnailHintRequest,
    GalleryThumbnailHintResult,
)
from .gallery_tile import GalleryTileRecord, GalleryTileSnapshot
from .gallery_window_loader import GalleryWindowLoader, GalleryWindowResult


class GalleryListModelAdapter(QAbstractListModel):
    """Expose a pure Python collection store to Qt item views."""

    _scan_batch_received = QtSignal(object)
    thumbnailBackfillProgress = QtSignal(Path, int, int)

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
        self._last_snapshot: Optional[tuple[int, Optional[tuple[int, int]], int]] = None
        self._last_selection_signature: tuple[str, str, str] | None = None
        self._last_window_identity_signature: tuple[str, ...] | None = None
        self._duration_cache: dict[Path, float] = {}
        self._pending_prioritize_range: tuple[int, int] | None = None
        self._viewport_generation = 0
        self._viewport_demand: GalleryViewportDemand | None = None
        self._pending_thumbnail_rows: set[int] = set()
        self._window_loader = GalleryWindowLoader(self)
        self._window_loader.resultReady.connect(self._on_window_result)
        self._window_loader.requestsDropped.connect(self._store.discard_window_requests)
        self._thumbnail_hint_loader = GalleryThumbnailHintLoader(self)
        self._thumbnail_hint_loader.resultReady.connect(self._on_thumbnail_hint_result)
        self._thumbnail_hint_candidates_by_row: dict[int, GalleryThumbnailCandidate] = {}
        self._thumbnail_hint_request_id = 0
        self._pending_scan_batch_count = 0
        self._backfill_completion_source: Any | None = None
        self._prioritize_timer = QTimer(self)
        self._prioritize_timer.setSingleShot(True)
        self._prioritize_timer.setInterval(16)
        self._prioritize_timer.timeout.connect(self._flush_pending_prioritize_rows)
        self._thumbnail_update_timer = QTimer(self)
        self._thumbnail_update_timer.setSingleShot(True)
        self._thumbnail_update_timer.setInterval(16)
        self._thumbnail_update_timer.timeout.connect(self._flush_thumbnail_updates)
        self._scan_batch_timer = QTimer(self)
        self._scan_batch_timer.setSingleShot(True)
        self._scan_batch_timer.setInterval(150)
        self._scan_batch_timer.timeout.connect(self._flush_pending_scan_batches)
        self._scan_batch_received.connect(
            self._enqueue_scan_batch_on_ui_thread,
            Qt.ConnectionType.QueuedConnection,
        )
        self._store.set_window_request_handler(self._window_loader.request)

        self._store.window_changed.connect(self._on_window_changed)
        self._store.data_changed.connect(self._on_source_changed)
        self._store.row_changed.connect(self._on_row_changed)
        self._thumbnails.thumbnailReady.connect(self._on_thumbnail_ready)
        self._bind_backfill_completion_signal(self._current_asset_query_service())

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
            if role_int == Roles.TILE_SNAPSHOT:
                return GalleryTileSnapshot(
                    record=None,
                    micro_image=None,
                    full_pixmap=None,
                    loading_state="placeholder",
                    is_current=row == self._current_row,
                )
            return None

        if role_int == Qt.ItemDataRole.DisplayRole:
            return asset.rel_path.name
        if role_int == Roles.TILE_SNAPSHOT:
            full = self._thumbnails.peek_full_thumbnail(asset.abs_path, self._thumb_size)
            micro = asset.micro_thumbnail if isinstance(asset.micro_thumbnail, QImage) else None
            loading_state = (
                "full"
                if isinstance(full, QPixmap) and not full.isNull()
                else "micro"
                if micro is not None and not micro.isNull()
                else "placeholder"
            )
            return GalleryTileSnapshot(
                record=GalleryTileRecord(
                    asset_id=str(asset.id),
                    abs_path=asset.abs_path,
                    rel_path=asset.rel_path,
                    media_type=asset.media_type,
                    duration=asset.duration,
                    is_favorite=asset.is_favorite,
                    is_live=asset.is_live,
                    is_pano=asset.is_pano,
                ),
                micro_image=micro,
                full_pixmap=full if isinstance(full, QPixmap) else None,
                loading_state=loading_state,
                is_current=row == self._current_row,
            )
        if role_int == Qt.DecorationRole:
            return self._thumbnails.peek_full_thumbnail(asset.abs_path, self._thumb_size)
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
        self._pending_prioritize_range = (first, last)
        if not self._prioritize_timer.isActive():
            self._prioritize_timer.start()

    @Slot(object)
    def update_viewport(self, state: object) -> None:
        """Continuously reconcile micro warm-up and full-thumbnail demand."""

        if not isinstance(state, GalleryViewportDemand):
            return
        self._thumb_size = QSize(state.display_bucket, state.display_bucket)
        self._viewport_generation = state.generation
        self._viewport_demand = state
        self._prune_thumbnail_hint_candidates(state)
        self._store.reconcile_viewport_demand(state)
        self._request_thumbnail_hints(state)
        self._reconcile_full_thumbnail_demand()

    @Slot()
    def _flush_pending_prioritize_rows(self) -> None:
        pending = self._pending_prioritize_range
        self._pending_prioritize_range = None
        if pending is None:
            return
        self._store.prioritize_rows(*pending)

    @Slot(object)
    def _on_window_result(self, result: GalleryWindowResult) -> None:
        if self._store.apply_window_result(result):
            self._reconcile_full_thumbnail_demand()

    @Slot(object)
    def handle_scan_batch(self, batch: object) -> None:
        """Queue ready scan/backfill batches onto the adapter's Qt thread."""

        if QThread.currentThread() is self.thread():
            self._enqueue_scan_batch_on_ui_thread(batch)
        else:
            self._scan_batch_received.emit(batch)

    @Slot(object)
    def _enqueue_scan_batch_on_ui_thread(self, batch: object) -> None:
        record_batch = getattr(self._store, "record_scan_batch", None)
        if callable(record_batch):
            if record_batch(batch):
                self._pending_scan_batch_count += 1
                if not self._scan_batch_timer.isActive():
                    self._scan_batch_timer.start()
            return

        handle_batch = getattr(self._store, "handle_scan_batch", None)
        if callable(handle_batch):
            handle_batch(batch)

    @Slot()
    def _flush_pending_scan_batches(self) -> None:
        if self._pending_scan_batch_count <= 0:
            return
        self._pending_scan_batch_count = 0
        flush = getattr(self._store, "flush_pending_scan_refresh", None)
        if callable(flush):
            flush()
        else:
            self._on_source_changed()

    def _schedule_thumbnail_backfill_refresh(self) -> None:
        """Compatibility no-op for old tests/fakes that emit this signal."""

    def _flush_pending_thumbnail_backfill(self) -> None:
        """Compatibility no-op after backfill completion became event-driven."""

    def pin_row(self, row: int) -> None:
        self._store.pin_row(row)

    def rebind_asset_query_service(
        self,
        asset_query_service,
        library_root: Optional[Path],
    ) -> None:
        self._last_snapshot = None
        self._last_selection_signature = None
        self._last_window_identity_signature = None
        self._duration_cache.clear()
        self._thumbnail_hint_candidates_by_row.clear()
        self._thumbnail_hint_request_id += 1
        self._thumbnail_hint_loader.cancel_pending()
        self._store.rebind_asset_query_service(asset_query_service, library_root)
        self._bind_backfill_completion_signal(asset_query_service)

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
            self.dataChanged.emit(idx, idx, [Roles.FEATURED, Roles.TILE_SNAPSHOT])

    def optimistic_move_paths(
        self,
        paths: list[Path],
        destination_root: Path,
        *,
        is_delete: bool,
    ) -> bool:
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

    def clear_pending_moves_for_paths(self, paths: list[Path]) -> bool:
        changed = self._store.clear_pending_moves_for_paths(paths)
        if changed:
            self._store.reload_current_selection()
        return changed

    def rollback_pending_moves(self) -> bool:
        changed = self._store.clear_all_pending_moves()
        if changed:
            self._store.reload_current_selection()
        return changed

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
                self.dataChanged.emit(
                    idx,
                    idx,
                    [Roles.IS_CURRENT, Roles.TILE_SNAPSHOT, Qt.ItemDataRole.SizeHintRole],
                )
        if row >= 0:
            idx = self.index(row, 0)
            if idx.isValid():
                self.dataChanged.emit(
                    idx,
                    idx,
                    [Roles.IS_CURRENT, Roles.TILE_SNAPSHOT, Qt.ItemDataRole.SizeHintRole],
                )

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
                return rel_path, library_root / rel_path
            return rel_path, None

        group_id = metadata.get("live_photo_group_id")
        if not group_id:
            return None, None
        partner = self._store.live_partner_for(asset.id, self._store.library_root())
        if partner is not None and partner.is_video:
            return partner.rel_path, partner.abs_path
        return None, None

    def _on_source_changed(self) -> None:
        count = self._store.count()
        current_snapshot = self._store.snapshot_signature()
        current_selection_signature = self._selection_signature()
        current_window_identity = self._window_identity_signature(current_snapshot[1])
        if (
            self._last_snapshot == current_snapshot
            and self._last_selection_signature == current_selection_signature
            and self._last_window_identity_signature == current_window_identity
        ):
            return
        self._duration_cache.clear()
        old_snapshot = self._last_snapshot
        old_selection_signature = self._last_selection_signature
        old_window_identity = self._last_window_identity_signature
        self._last_snapshot = current_snapshot
        self._last_selection_signature = current_selection_signature
        self._last_window_identity_signature = current_window_identity
        if old_snapshot is not None and old_snapshot[2] != current_snapshot[2]:
            self._thumbnail_hint_candidates_by_row.clear()
        if (
            old_snapshot is not None
            and old_selection_signature == current_selection_signature
            and old_window_identity == current_window_identity
            and old_snapshot[0] == count
        ):
            self._emit_data_refresh(old_snapshot[1], current_snapshot[1])
            if self._current_row >= count:
                self._current_row = -1
            return
        emit_perf_event(
            "gallery_model_reset",
            rows=count,
            window=current_snapshot[1],
            collection_revision=current_snapshot[2],
        )
        self.beginResetModel()
        self.endResetModel()
        if self._current_row >= count:
            self._current_row = -1

    def _emit_data_refresh(
        self,
        previous_window: Optional[tuple[int, int]],
        current_window: Optional[tuple[int, int]],
    ) -> None:
        count = self.rowCount()
        if count <= 0:
            return
        windows = [
            window
            for window in (previous_window, current_window)
            if window is not None
        ]
        if windows:
            first = min(window[0] for window in windows)
            last = max(window[1] for window in windows)
        else:
            first = 0
            last = count - 1
        first = max(0, min(first, count - 1))
        last = max(first, min(last, count - 1))
        emit_perf_event(
            "gallery_model_data_refresh",
            rows=count,
            first=first,
            last=last,
        )
        top = self.index(first, 0)
        bottom = self.index(last, 0)
        if top.isValid() and bottom.isValid():
            self.dataChanged.emit(top, bottom, [])

    def _window_identity_signature(
        self,
        window: Optional[tuple[int, int]],
    ) -> tuple[str, ...] | None:
        count = self.rowCount()
        if count <= 0:
            return ()
        if window is None:
            return None
        first = max(0, min(window[0], count - 1))
        last = max(first, min(window[1], count - 1))
        identity: list[str] = []
        for row in range(first, last + 1):
            asset = self._store.asset_at(row)
            if asset is None:
                return None
            key = (
                getattr(asset, "id", None)
                or getattr(asset, "abs_path", None)
                or getattr(asset, "rel_path", None)
            )
            if key is None:
                return None
            identity.append(str(key))
        return tuple(identity)

    def _selection_signature(self) -> tuple[str, str, str]:
        active_root = getattr(self._store, "active_root", lambda: None)()
        current_query = getattr(self._store, "current_query", lambda: None)()
        current_direct = getattr(self._store, "current_direct_assets", lambda: None)()
        direct_key = ""
        if current_direct is not None:
            direct_key = repr(
                [
                    str(getattr(asset, "abs_path", None) or getattr(asset, "path", ""))
                    for asset in current_direct
                ]
            )
        return (str(active_root), repr(current_query), direct_key)

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
        row = self._store.cached_row_for_path(path)
        if row is None:
            return
        self._pending_thumbnail_rows.add(row)
        if not self._thumbnail_update_timer.isActive():
            self._thumbnail_update_timer.start()

    @Slot()
    def _flush_thumbnail_updates(self) -> None:
        rows = sorted(self._pending_thumbnail_rows)
        self._pending_thumbnail_rows.clear()
        if not rows:
            return
        range_first = rows[0]
        range_last = rows[0]
        for row in rows[1:]:
            if row == range_last + 1:
                range_last = row
                continue
            self._emit_thumbnail_range(range_first, range_last)
            range_first = range_last = row
        self._emit_thumbnail_range(range_first, range_last)

    def _emit_thumbnail_range(self, first: int, last: int) -> None:
        top = self.index(first, 0)
        bottom = self.index(last, 0)
        if top.isValid() and bottom.isValid():
            self.dataChanged.emit(
                top,
                bottom,
                [Qt.DecorationRole, Roles.TILE_SNAPSHOT],
            )

    def _reconcile_full_thumbnail_demand(self) -> None:
        demand = self._viewport_demand
        if demand is None:
            return
        visible_rows = self._store.cached_rows(*demand.visible_range)
        prefetched_rows = dict(self._store.cached_rows(*demand.full_prefetch_range))
        visible_count = demand.visible_last - demand.visible_first + 1
        ordered_prefetch_rows = tuple(demand.iter_full_prefetch_rows())
        predictive_rows = (
            frozenset(ordered_prefetch_rows[:visible_count])
            if demand.prefetch_direction
            else frozenset()
        )
        cached_candidates = self._cached_thumbnail_candidates(
            ordered_prefetch_rows,
            prefetched_rows,
            predictive_rows,
        )
        hint_candidates = self._current_hint_candidates(
            ordered_prefetch_rows,
            predictive_rows,
        )
        cached_prefetch_paths = [
            prefetched_rows[row].abs_path
            for row in ordered_prefetch_rows
            if row in prefetched_rows
        ]
        candidate_by_path = {
            candidate.path: candidate
            for candidate in (*cached_candidates, *hint_candidates)
        }
        ordered_candidate_paths = [
            candidate.path
            for candidate in sorted(
                candidate_by_path.values(),
                key=lambda candidate: candidate.rank,
            )
        ]
        prefetch_paths = list(
            dict.fromkeys((*ordered_candidate_paths, *cached_prefetch_paths))
        )
        if perf_logging_enabled():
            full_count = 0
            micro_count = 0
            for _row, dto in visible_rows:
                if self._thumbnails.has_full_thumbnail(dto.abs_path, self._thumb_size) is True:
                    full_count += 1
                elif isinstance(dto.micro_thumbnail, QImage) and not dto.micro_thumbnail.isNull():
                    micro_count += 1
            next_screen_rows = ordered_prefetch_rows[:visible_count]
            next_screen_resident = sum(
                1
                for row in next_screen_rows
                if row in prefetched_rows
                and self._thumbnails.has_full_thumbnail(
                    prefetched_rows[row].abs_path,
                    self._thumb_size,
                )
            )
            emit_perf_event(
                "gallery_visible_layers",
                generation=demand.generation,
                phase=demand.phase,
                visible=visible_count,
                full=full_count,
                micro=micro_count,
                placeholder=max(0, visible_count - full_count - micro_count),
            )
            emit_perf_event(
                "gallery_full_resident_before_visible",
                generation=demand.generation,
                next_screen_rows=len(next_screen_rows),
                predictive_deadline_rows=len(next_screen_rows),
                resident=next_screen_resident,
                l2_key_source_cached=len(cached_candidates),
                l2_key_source_hint=len(hint_candidates),
            )
        self._thumbnails.reconcile_demand(
            visible_paths=[dto.abs_path for _row, dto in visible_rows],
            prefetch_paths=prefetch_paths,
            size=self._thumb_size,
            generation=demand.generation,
            phase=demand.phase,
            intent=demand.intent,
            prefetch_candidates=tuple(candidate_by_path.values()),
        )

    def _request_thumbnail_hints(self, demand: GalleryViewportDemand) -> None:
        if demand.intent == "continuous_burst":
            self._thumbnail_hint_request_id += 1
            self._thumbnail_hint_loader.cancel_pending()
            return
        query = self._store.current_query()
        root = self._store.active_root() or self._store.library_root()
        query_service = self._current_asset_query_service()
        reader = getattr(query_service, "read_thumbnail_hint_window", None)
        ordered_rows = tuple(demand.iter_full_prefetch_rows())
        if (
            not isinstance(query, AssetQuery)
            or root is None
            or not callable(reader)
            or not ordered_rows
        ):
            return
        visible_count = demand.visible_last - demand.visible_first + 1
        predictive_rows = (
            frozenset(ordered_rows[:visible_count])
            if demand.prefetch_direction
            else frozenset()
        )
        emit_perf_event(
            "gallery_thumbnail_hint_requested",
            generation=demand.generation,
            intent=demand.intent,
            first=demand.full_prefetch_first,
            limit=demand.full_prefetch_last - demand.full_prefetch_first + 1,
            predictive_rows=len(predictive_rows),
        )
        self._thumbnail_hint_request_id += 1
        request_id = self._thumbnail_hint_request_id
        self._thumbnail_hint_loader.request(
            GalleryThumbnailHintRequest(
                request_id=request_id,
                generation=demand.generation,
                collection_revision=self._current_collection_revision(),
                root=Path(root),
                query=query,
                query_service=query_service,
                first=demand.full_prefetch_first,
                limit=demand.full_prefetch_last - demand.full_prefetch_first + 1,
                ordered_rows=ordered_rows,
                predictive_rows=predictive_rows,
            )
        )

    def _cached_thumbnail_candidates(
        self,
        ordered_rows: tuple[int, ...],
        prefetched_rows: dict[int, AssetDTO],
        predictive_rows: frozenset[int],
    ) -> tuple[ThumbnailPrefetchCandidate, ...]:
        candidates: list[ThumbnailPrefetchCandidate] = []
        for rank, row in enumerate(ordered_rows):
            dto = prefetched_rows.get(row)
            if dto is None:
                continue
            l2_key = dto.thumb_cache_key
            if not isinstance(l2_key, str) or not l2_key.strip():
                metadata_key = dto.metadata.get("thumb_cache_key") if dto.metadata else None
                l2_key = metadata_key if isinstance(metadata_key, str) else None
            if not isinstance(l2_key, str) or not l2_key.strip():
                continue
            candidates.append(
                ThumbnailPrefetchCandidate(
                    row=row,
                    path=dto.abs_path,
                    l2_cache_key=l2_key,
                    kind="predictive" if row in predictive_rows else "far_speculative",
                    rank=rank,
                )
            )
        return tuple(candidates)

    def _current_hint_candidates(
        self,
        ordered_rows: tuple[int, ...],
        predictive_rows: frozenset[int],
    ) -> tuple[GalleryThumbnailCandidate, ...]:
        candidates: list[GalleryThumbnailCandidate] = []
        for rank, row in enumerate(ordered_rows):
            candidate = self._thumbnail_hint_candidates_by_row.get(row)
            if candidate is None:
                continue
            candidates.append(
                GalleryThumbnailCandidate(
                    row=row,
                    path=candidate.path,
                    l2_cache_key=candidate.l2_cache_key,
                    rank=rank,
                    kind="predictive" if row in predictive_rows else "far_speculative",
                )
            )
        return tuple(candidates)

    def _prune_thumbnail_hint_candidates(self, demand: GalleryViewportDemand) -> None:
        if not self._thumbnail_hint_candidates_by_row:
            return
        first, last = demand.full_prefetch_range
        self._thumbnail_hint_candidates_by_row = {
            row: candidate
            for row, candidate in self._thumbnail_hint_candidates_by_row.items()
            if first <= row <= last
        }

    @Slot(object)
    def _on_thumbnail_hint_result(self, result: GalleryThumbnailHintResult) -> None:
        demand = self._viewport_demand
        if (
            demand is None
            or result.error is not None
            or demand.intent == "continuous_burst"
            or not self._thumbnail_hint_result_matches_current_selection(result)
            or not self._thumbnail_hint_result_matches_current_revision(result)
        ):
            return
        first, last = demand.full_prefetch_range
        relevant = {
            candidate.row: candidate
            for candidate in result.candidates
            if first <= candidate.row <= last
        }
        if not relevant:
            return
        self._thumbnail_hint_candidates_by_row.update(relevant)
        emit_perf_event(
            "gallery_next_screen_hint_coverage",
            generation=result.generation,
            current_generation=demand.generation,
            request_id=result.request_id,
            current_request_id=self._thumbnail_hint_request_id,
            predictive=sum(
                candidate.kind == "predictive" for candidate in relevant.values()
            ),
            total=len(relevant),
        )
        self._reconcile_full_thumbnail_demand()

    def _thumbnail_hint_result_matches_current_selection(
        self,
        result: GalleryThumbnailHintResult,
    ) -> bool:
        root = self._store.active_root() or self._store.library_root()
        query = self._store.current_query()
        if root is None:
            return False
        return Path(result.root) == Path(root) and result.query == query

    def _thumbnail_hint_result_matches_current_revision(
        self,
        result: GalleryThumbnailHintResult,
    ) -> bool:
        current_revision = self._current_collection_revision()
        result_revision = int(result.collection_revision)
        return (
            result_revision <= 0
            or current_revision <= 0
            or result_revision == current_revision
        )

    def _current_collection_revision(self) -> int:
        try:
            revision = self._store.snapshot_signature()[2]
        except (IndexError, TypeError, AttributeError):
            return 0
        return revision if isinstance(revision, int) else 0

    def _on_row_changed(self, row: int) -> None:
        idx = self.index(row, 0)
        if idx.isValid():
            self.dataChanged.emit(
                idx,
                idx,
                [Roles.FEATURED, Roles.INFO, Roles.LOCATION, Roles.SIZE, Roles.TILE_SNAPSHOT],
            )

    def _current_asset_query_service(self) -> Any | None:
        return getattr(self._store, "asset_query_service", None)

    def _bind_backfill_completion_signal(self, asset_query_service: Any | None) -> None:
        if asset_query_service is self._backfill_completion_source:
            return

        old_signal = getattr(
            self._backfill_completion_source,
            "thumbnail_backfill_completed",
            None,
        )
        old_disconnect = getattr(old_signal, "disconnect", None)
        if callable(old_disconnect):
            try:
                old_disconnect(self.handle_scan_batch)
            except (RuntimeError, TypeError, ValueError):
                pass
        old_progress = getattr(
            self._backfill_completion_source,
            "thumbnail_backfill_progress",
            None,
        )
        old_progress_disconnect = getattr(old_progress, "disconnect", None)
        if callable(old_progress_disconnect):
            try:
                old_progress_disconnect(self._handle_thumbnail_backfill_progress)
            except (RuntimeError, TypeError, ValueError):
                pass

        self._backfill_completion_source = asset_query_service
        new_signal = getattr(asset_query_service, "thumbnail_backfill_completed", None)
        new_connect = getattr(new_signal, "connect", None)
        if callable(new_connect):
            new_connect(self.handle_scan_batch)
        new_progress = getattr(asset_query_service, "thumbnail_backfill_progress", None)
        new_progress_connect = getattr(new_progress, "connect", None)
        if callable(new_progress_connect):
            new_progress_connect(self._handle_thumbnail_backfill_progress)

    def _handle_thumbnail_backfill_progress(
        self,
        root: Path,
        current: int,
        total: int,
    ) -> None:
        self.thumbnailBackfillProgress.emit(Path(root), int(current), int(total))

    def _snapshot_hash(self, count: int) -> bytes:
        del count
        return repr(self._store.snapshot_signature()).encode("utf-8")

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

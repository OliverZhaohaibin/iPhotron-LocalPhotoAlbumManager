from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtGui import QImage

from iPhoto.application.dtos import AssetDTO, GalleryTileDTO
from iPhoto.gui.ui.models.roles import Roles
from iPhoto.gui.viewmodels.gallery_collection_store import GalleryCollectionStore
from iPhoto.gui.viewmodels.gallery_list_model_adapter import GalleryListModelAdapter
from iPhoto.infrastructure.services.thumbnail_cache_service import ThumbnailCacheService


class _Signal:
    def __init__(self) -> None:
        self.handlers = []

    def connect(self, handler) -> None:
        if handler not in self.handlers:
            self.handlers.append(handler)

    def disconnect(self, handler) -> None:
        self.handlers.remove(handler)

    def emit(self, *args) -> None:
        for handler in list(self.handlers):
            handler(*args)


class _BackfillService:
    def __init__(self) -> None:
        self.thumbnail_backfill_completed = _Signal()
        self.thumbnail_backfill_progress = _Signal()


@pytest.fixture(autouse=True)
def _qt_app(qapp):
    return qapp


@pytest.fixture
def mock_store():
    store = MagicMock(spec=GalleryCollectionStore)
    store.data_changed = MagicMock()
    store.window_changed = MagicMock()
    store.row_changed = MagicMock()
    store.thumbnail_backfill_scheduled = MagicMock()
    store.count.return_value = 0
    return store


@pytest.fixture
def mock_thumb_service():
    service = MagicMock(spec=ThumbnailCacheService)
    service.peek.return_value = None
    return service


@pytest.fixture
def adapter(mock_store, mock_thumb_service):
    return GalleryListModelAdapter(mock_store, mock_thumb_service)


def _make_dto(**overrides) -> AssetDTO:
    defaults = dict(
        id="1",
        abs_path=Path("photo.jpg"),
        rel_path=Path("photo.jpg"),
        media_type="image",
        created_at=None,
        width=100,
        height=100,
        duration=0.0,
        size_bytes=100,
        metadata={},
        is_favorite=False,
    )
    defaults.update(overrides)
    return AssetDTO(**defaults)


def _make_tile(**overrides) -> GalleryTileDTO:
    defaults = dict(
        id="1",
        abs_path=Path("photo.jpg"),
        rel_path=Path("photo.jpg"),
        media_type="image",
        created_at=None,
        width=100,
        height=100,
        duration=0.0,
        size_bytes=100,
        is_favorite=False,
    )
    defaults.update(overrides)
    return GalleryTileDTO(**defaults)


def test_adapter_init(adapter):
    assert adapter.rowCount() == 0


def test_info_role_contains_required_keys(adapter, mock_store):
    mock_store.count.return_value = 1
    mock_store.asset_at.return_value = _make_dto(
        rel_path=Path("clip.mov"),
        abs_path=Path("/lib/clip.mov"),
        media_type="video",
        width=1920,
        height=1080,
        duration=8.5,
        size_bytes=1_000_000,
    )

    index = adapter.index(0, 0)
    info = adapter.data(index, Roles.INFO)

    for key in ("rel", "abs", "name", "is_video", "w", "h", "dur", "bytes"):
        assert key in info


def test_lightweight_tile_roles_use_store_metadata_view(adapter, mock_store):
    mock_store.count.return_value = 1
    mock_store.asset_at.return_value = _make_tile(
        live_partner_rel="motion.mov",
        location="Berlin",
    )
    mock_store.metadata_for_asset.return_value = {
        "live_partner_rel": "motion.mov",
        "location": "Berlin",
    }

    index = adapter.index(0, 0)

    assert adapter.data(index, Roles.LOCATION) == "Berlin"
    assert adapter.data(index, Roles.INFO)["location"] == "Berlin"
    assert adapter.data(index, Roles.LIVE_MOTION_REL) == "motion.mov"


def test_data_display_role(adapter, mock_store):
    mock_store.count.return_value = 1
    mock_store.asset_at.return_value = _make_dto(rel_path=Path("photo.jpg"))

    index = adapter.index(0, 0)
    assert adapter.data(index, Qt.DisplayRole) == "photo.jpg"


def test_row_for_path_delegates_to_store(adapter, mock_store):
    path = Path("/library/photo.jpg")
    mock_store.row_for_path.return_value = 7

    assert adapter.row_for_path(path) == 7
    mock_store.row_for_path.assert_called_once_with(path)


def test_prioritize_rows_delegates_to_store_after_frame_coalescing(adapter, mock_store):
    adapter.prioritize_rows(10, 25)
    adapter._flush_pending_prioritize_rows()

    mock_store.prioritize_rows.assert_called_once_with(10, 25)
    assert adapter._pending_micro_prefetch_range == (10, 25)


def test_buffered_prioritize_rows_does_not_request_full_thumbnails(
    adapter,
    mock_store,
    mock_thumb_service,
):
    adapter.prioritize_rows(10, 12)
    adapter._flush_pending_prioritize_rows()

    mock_thumb_service.cancel_pending_except.assert_not_called()
    mock_thumb_service.request_many.assert_not_called()


def test_full_viewport_rows_request_only_loaded_full_thumbnails_center_out(
    adapter,
    mock_store,
    mock_thumb_service,
):
    assets = {
        row: _make_dto(abs_path=Path(f"/library/{row}.jpg"))
        for row in range(10, 13)
    }
    mock_store.asset_at.side_effect = assets.get

    adapter.prioritize_full_rows(10, 12)

    mock_thumb_service.cancel_pending_except.assert_called_once_with(
        {Path("/library/10.jpg"), Path("/library/11.jpg"), Path("/library/12.jpg")},
        adapter._thumb_size,
    )
    mock_thumb_service.request_many.assert_called_once_with(
        [Path("/library/11.jpg"), Path("/library/10.jpg"), Path("/library/12.jpg")],
        adapter._thumb_size,
        priority="visible",
        allow_generate=True,
    )


def test_full_viewport_rows_keep_latest_range_and_cancel_old_work(
    adapter,
    mock_store,
    mock_thumb_service,
):
    latest = _make_dto(abs_path=Path("/library/latest.jpg"))
    mock_store.asset_at.side_effect = lambda row: latest if row == 5 else None

    adapter.prioritize_full_rows(10, 25)
    adapter.prioritize_full_rows(20, 60)
    adapter.prioritize_full_rows(5, 15)

    assert adapter._full_visible_range == (5, 15)
    mock_thumb_service.cancel_pending_except.assert_called_with(
        {Path("/library/latest.jpg")},
        adapter._thumb_size,
    )
    mock_thumb_service.request_many.assert_called_with(
        [Path("/library/latest.jpg")],
        adapter._thumb_size,
        priority="visible",
        allow_generate=True,
    )


def test_visible_window_result_restarts_stable_warm_timer(
    adapter,
    mock_store,
    mock_thumb_service,
):
    request = SimpleNamespace(first=10, last=20, tier="visible")
    result = SimpleNamespace(request=request)
    mock_store.apply_window_result.return_value = True
    visible = _make_dto(abs_path=Path("/library/visible.jpg"))
    mock_store.asset_at.side_effect = lambda row: visible if row == 10 else None
    adapter._full_visible_range = (10, 20)

    adapter._apply_window_result_on_ui_thread(result)

    assert adapter._pending_thumbnail_range == (10, 20)
    assert adapter._thumbnail_timer.isActive()
    assert adapter._pending_micro_prefetch_range == (10, 20)
    assert adapter._micro_prefetch_timer.isActive()
    mock_thumb_service.request_many.assert_called_once_with(
        [Path("/library/visible.jpg")],
        adapter._thumb_size,
        priority="visible",
        allow_generate=True,
    )


def test_visible_window_result_without_viewport_does_not_request_buffered_full_rows(
    adapter,
    mock_store,
    mock_thumb_service,
):
    request = SimpleNamespace(first=10, last=60, tier="visible")
    result = SimpleNamespace(request=request)
    mock_store.apply_window_result.return_value = True
    mock_store.asset_at.side_effect = lambda row: _make_dto(
        abs_path=Path(f"/library/{row}.jpg")
    )

    adapter._apply_window_result_on_ui_thread(result)

    mock_thumb_service.cancel_pending_except.assert_not_called()
    mock_thumb_service.request_many.assert_not_called()
    assert adapter._pending_thumbnail_range is None
    assert adapter._pending_micro_prefetch_range == (10, 60)


def test_warm_window_result_does_not_restart_stable_timer(
    adapter,
    mock_store,
):
    request = SimpleNamespace(first=10, last=20, tier="warm")
    result = SimpleNamespace(request=request)
    mock_store.apply_window_result.return_value = True
    adapter._thumbnail_timer.stop()

    adapter._apply_window_result_on_ui_thread(result)

    assert adapter._pending_thumbnail_range is None
    assert not adapter._thumbnail_timer.isActive()


def test_micro_prefetch_timer_keeps_latest_range_without_restart(adapter, mock_store):
    adapter._micro_prefetch_timer.stop()
    adapter._micro_prefetch_timer.start = MagicMock()
    adapter._micro_prefetch_timer.isActive = MagicMock(side_effect=[False, True, True])

    adapter.prioritize_rows(10, 20)
    adapter.prioritize_rows(20, 30)
    adapter.prioritize_rows(30, 40)

    adapter._micro_prefetch_timer.start.assert_called_once_with()
    assert adapter._pending_micro_prefetch_range == (30, 40)

    adapter._flush_pending_micro_prefetch()

    mock_store.prefetch_rows.assert_called_once_with(30, 40)


def test_stable_timer_only_prefetches_neighbor_full_thumbnails(
    adapter,
    mock_store,
    mock_thumb_service,
):
    mock_store.count.return_value = 100
    mock_store.asset_at.side_effect = lambda row: _make_dto(
        abs_path=Path(f"/library/{row}.jpg")
    )
    mock_thumb_service.peek.return_value = object()
    adapter._full_visible_range = (40, 50)
    adapter._pending_thumbnail_range = (40, 50)

    adapter._flush_pending_thumbnail_rows()

    mock_store.prefetch_rows.assert_not_called()
    call = mock_thumb_service.request_many.call_args
    assert call.kwargs["priority"] == "low"
    assert call.kwargs["allow_generate"] is False
    assert call.kwargs["speculative"] is True
    assert call.args[0] == [
        *(Path(f"/library/{row}.jpg") for row in range(62, 71)),
        *(Path(f"/library/{row}.jpg") for row in range(20, 29)),
    ]


def test_completed_viewport_immediately_warms_nearest_full_rows(
    adapter,
    mock_store,
    mock_thumb_service,
):
    mock_store.count.return_value = 100
    mock_store.asset_at.side_effect = lambda row: _make_dto(
        abs_path=Path(f"/library/{row}.jpg")
    )
    mock_thumb_service.peek.return_value = object()
    adapter._full_visible_range = (40, 50)

    adapter._record_full_viewport_completion_if_ready()

    mock_thumb_service.request_many.assert_called_once_with(
        [
            Path("/library/51.jpg"),
            Path("/library/39.jpg"),
            Path("/library/52.jpg"),
            Path("/library/38.jpg"),
            Path("/library/53.jpg"),
            Path("/library/37.jpg"),
            Path("/library/54.jpg"),
            Path("/library/36.jpg"),
            Path("/library/55.jpg"),
            Path("/library/35.jpg"),
            Path("/library/56.jpg"),
            Path("/library/34.jpg"),
            Path("/library/57.jpg"),
            Path("/library/33.jpg"),
            Path("/library/58.jpg"),
            Path("/library/32.jpg"),
            Path("/library/59.jpg"),
            Path("/library/31.jpg"),
            Path("/library/60.jpg"),
            Path("/library/30.jpg"),
            Path("/library/61.jpg"),
            Path("/library/29.jpg"),
        ],
        adapter._thumb_size,
        priority="normal",
        allow_generate=True,
        speculative=True,
    )
    assert adapter._full_warmup_range == (40, 50)


def test_full_warmup_is_not_requeued_for_same_viewport(
    adapter,
    mock_store,
    mock_thumb_service,
):
    mock_store.count.return_value = 100
    mock_store.asset_at.side_effect = lambda row: _make_dto(
        abs_path=Path(f"/library/{row}.jpg")
    )
    mock_thumb_service.peek.return_value = object()
    adapter._full_visible_range = (40, 50)

    adapter._record_full_viewport_completion_if_ready()
    adapter._record_full_viewport_completion_if_ready()

    mock_thumb_service.request_many.assert_called_once()


def test_warm_result_retries_neighbors_for_stable_viewport(
    adapter,
    mock_store,
    mock_thumb_service,
):
    request = SimpleNamespace(first=40, last=50, tier="warm")
    result = SimpleNamespace(request=request)
    mock_store.apply_window_result.return_value = True
    mock_store.visible_range.return_value = (40, 50)
    mock_store.count.return_value = 100
    mock_store.asset_at.side_effect = lambda row: _make_dto(
        abs_path=Path(f"/library/{row}.jpg")
    )
    mock_thumb_service.peek.return_value = object()
    adapter._full_visible_range = (40, 50)
    adapter._stable_thumbnail_range = (40, 50)

    adapter._apply_window_result_on_ui_thread(result)

    assert mock_thumb_service.request_many.call_count == 3
    assert mock_thumb_service.request_many.call_args_list[0].kwargs["priority"] == "visible"
    assert mock_thumb_service.request_many.call_args_list[0].kwargs["allow_generate"] is True
    assert mock_thumb_service.request_many.call_args_list[1].kwargs == {
        "priority": "normal",
        "allow_generate": True,
        "speculative": True,
    }
    assert mock_thumb_service.request_many.call_args_list[2].kwargs["priority"] == "low"
    assert mock_thumb_service.request_many.call_args_list[2].kwargs["allow_generate"] is False
    assert mock_thumb_service.request_many.call_args_list[2].kwargs["speculative"] is True


def test_stable_viewport_waits_for_all_full_thumbnails_before_neighbor_prefetch(
    adapter,
    mock_store,
    mock_thumb_service,
):
    mock_store.count.return_value = 100
    mock_store.asset_at.side_effect = lambda row: _make_dto(
        abs_path=Path(f"/library/{row}.jpg")
    )
    mock_thumb_service.peek.return_value = None
    adapter._full_visible_range = (40, 50)
    adapter._pending_thumbnail_range = (40, 50)

    adapter._flush_pending_thumbnail_rows()

    mock_thumb_service.request_many.assert_not_called()


def test_thumbnail_ready_starts_neighbors_after_stable_viewport_completes(
    adapter,
    mock_store,
    mock_thumb_service,
):
    mock_store.count.return_value = 100
    mock_store.row_for_path.return_value = 40
    mock_store.asset_at.side_effect = lambda row: _make_dto(
        abs_path=Path(f"/library/{row}.jpg")
    )
    mock_thumb_service.peek.return_value = object()
    adapter._full_visible_range = (40, 50)
    adapter._stable_thumbnail_range = (40, 50)
    adapter._full_visible_pending_paths = {Path("/library/40.jpg")}

    adapter._on_thumbnail_ready(Path("/library/40.jpg"))

    call = mock_thumb_service.request_many.call_args
    assert call.kwargs == {
        "priority": "low",
        "allow_generate": False,
        "speculative": True,
    }
    assert adapter._neighbor_prefetched_range == (40, 50)


def test_scan_batches_are_coalesced_before_store_flush(adapter, mock_store):
    mock_store.record_scan_batch.return_value = True

    adapter.handle_scan_batch(SimpleNamespace(rows=[{"rel": "a.jpg"}]))
    adapter.handle_scan_batch(SimpleNamespace(rows=[{"rel": "b.jpg"}]))
    adapter._flush_pending_scan_batches()

    assert adapter._scan_batch_timer.interval() == 150
    assert mock_store.record_scan_batch.call_count == 2
    mock_store.flush_pending_scan_refresh.assert_called_once_with()


def test_backfill_completion_event_queues_scan_batch(mock_thumb_service):
    service = _BackfillService()
    store = MagicMock(spec=GalleryCollectionStore)
    store.data_changed = MagicMock()
    store.window_changed = MagicMock()
    store.row_changed = MagicMock()
    store.count.return_value = 0
    store.asset_query_service = service
    store.record_scan_batch.return_value = True
    adapter = GalleryListModelAdapter(store, mock_thumb_service)
    batch = SimpleNamespace(rows=[{"rel": "ready.jpg"}])

    service.thumbnail_backfill_completed.emit(batch)
    adapter._flush_pending_scan_batches()

    store.record_scan_batch.assert_called_once_with(batch)
    store.flush_pending_scan_refresh.assert_called_once_with()


def test_rebind_asset_query_service_moves_backfill_completion_signal(
    mock_store,
    mock_thumb_service,
):
    old_service = _BackfillService()
    new_service = _BackfillService()
    mock_store.asset_query_service = old_service
    adapter = GalleryListModelAdapter(mock_store, mock_thumb_service)

    adapter.rebind_asset_query_service(new_service, Path("/library"))

    assert adapter.handle_scan_batch not in old_service.thumbnail_backfill_completed.handlers
    assert adapter.handle_scan_batch in new_service.thumbnail_backfill_completed.handlers
    assert adapter._handle_thumbnail_backfill_progress not in old_service.thumbnail_backfill_progress.handlers
    assert adapter._handle_thumbnail_backfill_progress in new_service.thumbnail_backfill_progress.handlers
    mock_store.rebind_asset_query_service.assert_called_once_with(
        new_service,
        Path("/library"),
    )


def test_backfill_progress_is_relayed_from_query_service(mock_thumb_service):
    service = _BackfillService()
    store = MagicMock(spec=GalleryCollectionStore)
    store.data_changed = MagicMock()
    store.window_changed = MagicMock()
    store.row_changed = MagicMock()
    store.count.return_value = 0
    store.asset_query_service = service
    adapter = GalleryListModelAdapter(store, mock_thumb_service)
    progress: list[tuple[Path, int, int]] = []
    adapter.thumbnailBackfillProgress.connect(
        lambda root, current, total: progress.append((root, current, total))
    )

    service.thumbnail_backfill_progress.emit(Path("/library"), 2, 5)

    assert progress == [(Path("/library"), 2, 5)]


def test_decoration_role_uses_full_size_thumbnail_even_with_micro_fallback(
    adapter,
    mock_store,
    mock_thumb_service,
):
    micro = QImage(2, 2, QImage.Format.Format_RGB32)
    full_size = object()
    mock_store.count.return_value = 1
    mock_store.asset_at.return_value = _make_dto(micro_thumbnail=micro)
    mock_thumb_service.peek.return_value = full_size

    result = adapter.data(adapter.index(0, 0), Qt.DecorationRole)

    assert result is full_size
    mock_thumb_service.peek.assert_called_once_with(
        Path("photo.jpg"),
        adapter._thumb_size,
    )
    mock_thumb_service.get_thumbnail.assert_not_called()


def test_decoration_role_miss_leaves_micro_thumbnail_for_delegate_fallback(
    adapter,
    mock_store,
    mock_thumb_service,
):
    micro = QImage(2, 2, QImage.Format.Format_RGB32)
    mock_store.count.return_value = 1
    mock_store.asset_at.return_value = _make_dto(micro_thumbnail=micro)
    mock_thumb_service.peek.return_value = None

    index = adapter.index(0, 0)

    assert adapter.data(index, Qt.DecorationRole) is None
    assert adapter.data(index, Roles.MICRO_THUMBNAIL) is micro
    mock_thumb_service.peek.assert_called_once()
    mock_thumb_service.get_thumbnail.assert_not_called()


def test_decoration_role_uses_memory_thumbnail_when_micro_thumbnail_is_not_drawable(
    adapter,
    mock_store,
    mock_thumb_service,
):
    fallback = object()
    mock_store.count.return_value = 1
    mock_store.asset_at.return_value = _make_dto(micro_thumbnail=b"jpeg-bytes")
    mock_thumb_service.peek.return_value = fallback

    result = adapter.data(adapter.index(0, 0), Qt.DecorationRole)

    assert result is fallback
    mock_thumb_service.peek.assert_called_once()
    mock_thumb_service.get_thumbnail.assert_not_called()


def test_data_miss_returns_stable_placeholder_without_loading(
    adapter,
    mock_store,
    mock_thumb_service,
):
    mock_store.count.return_value = 1
    mock_store.asset_at.return_value = None
    index = adapter.index(0, 0)

    assert adapter.data(index, Qt.DisplayRole) == ""
    assert adapter.data(index, Qt.DecorationRole) is None
    assert adapter.data(index, Roles.IS_VIDEO) is False
    assert adapter.data(index, Roles.SIZE) == {
        "duration": 0.0,
        "width": 0,
        "height": 0,
        "bytes": 0,
    }

    mock_store.ensure_row_loaded.assert_not_called()
    mock_thumb_service.peek.assert_not_called()
    mock_thumb_service.get_thumbnail.assert_not_called()


def test_rebind_asset_query_service_updates_store(adapter, mock_store):
    query_service = MagicMock()
    root = Path("/library")

    adapter.rebind_asset_query_service(query_service, root)

    mock_store.rebind_asset_query_service.assert_called_once_with(query_service, root)


def test_invalidate_thumbnail_clears_duration_cache_and_emits_size_role(adapter, mock_store):
    path = Path("/videos/clip.mp4")
    adapter._duration_cache[path] = 8.0
    mock_store.row_for_path.return_value = 0
    mock_store.count.return_value = 1

    emitted_roles = []
    adapter.dataChanged.connect(lambda _top, _bottom, roles: emitted_roles.extend(roles))

    with patch.object(adapter._thumbnails, "invalidate"):
        adapter.invalidate_thumbnail(str(path))

    assert path not in adapter._duration_cache
    assert Roles.SIZE in emitted_roles


def test_size_role_returns_trimmed_duration_for_video(adapter, mock_store):
    mock_store.count.return_value = 1
    mock_store.asset_at.return_value = _make_dto(
        abs_path=Path("/videos/clip.mp4"),
        media_type="video",
        duration=10.0,
    )

    edit_service = MagicMock()
    edit_service.describe_adjustments.return_value = SimpleNamespace(
        effective_duration_sec=5.0,
    )
    adapter._edit_service_getter = lambda: edit_service

    index = adapter.index(0, 0)
    result = adapter.data(index, Roles.SIZE)

    assert result["duration"] == pytest.approx(5.0)
    edit_service.describe_adjustments.assert_called_once_with(
        Path("/videos/clip.mp4"),
        duration_hint=10.0,
    )


def test_invalid_index_returns_none(adapter):
    assert adapter.data(QModelIndex(), Qt.DisplayRole) is None


def test_row_changed_emits_targeted_favorite_update(adapter, mock_store):
    mock_store.count.return_value = 1
    mock_store.asset_at.return_value = _make_dto()

    emitted_roles = []
    adapter.dataChanged.connect(lambda _top, _bottom, roles: emitted_roles.extend(roles))

    adapter._on_row_changed(0)

    assert Roles.FEATURED in emitted_roles


def test_source_change_same_selection_and_count_emits_data_changed_not_model_reset(
    adapter,
    mock_store,
):
    assets = [
        _make_dto(id="a", abs_path=Path("/library/a.jpg")),
        _make_dto(id="b", abs_path=Path("/library/b.jpg")),
    ]
    mock_store.count.return_value = 2
    mock_store.active_root.return_value = Path("/library")
    mock_store.current_query.return_value = "all"
    mock_store.current_direct_assets.return_value = None
    mock_store.asset_at.side_effect = lambda row: assets[row]
    mock_store.snapshot_signature.return_value = (2, (0, 1), 1)
    reset_count = 0
    changed_ranges: list[tuple[int, int]] = []

    def _record_reset() -> None:
        nonlocal reset_count
        reset_count += 1

    adapter.modelReset.connect(_record_reset)
    adapter.dataChanged.connect(
        lambda top, bottom, _roles: changed_ranges.append((top.row(), bottom.row()))
    )

    adapter._on_source_changed()
    mock_store.snapshot_signature.return_value = (2, (0, 1), 2)
    adapter._on_source_changed()

    assert reset_count == 1
    assert changed_ranges == [(0, 1)]


def test_source_change_same_count_with_reordered_rows_resets_model(
    adapter,
    mock_store,
):
    first_assets = [
        _make_dto(id="a", abs_path=Path("/library/a.jpg")),
        _make_dto(id="b", abs_path=Path("/library/b.jpg")),
    ]
    reordered_assets = [first_assets[1], first_assets[0]]
    visible_assets = first_assets
    mock_store.count.return_value = 2
    mock_store.active_root.return_value = Path("/library")
    mock_store.current_query.return_value = "all"
    mock_store.current_direct_assets.return_value = None
    mock_store.asset_at.side_effect = lambda row: visible_assets[row]
    mock_store.snapshot_signature.return_value = (2, (0, 1), 1)
    reset_count = 0
    changed_ranges: list[tuple[int, int]] = []

    def _record_reset() -> None:
        nonlocal reset_count
        reset_count += 1

    adapter.modelReset.connect(_record_reset)
    adapter.dataChanged.connect(
        lambda top, bottom, _roles: changed_ranges.append((top.row(), bottom.row()))
    )

    adapter._on_source_changed()
    visible_assets = reordered_assets
    mock_store.snapshot_signature.return_value = (2, (0, 1), 2)
    adapter._on_source_changed()

    assert reset_count == 2
    assert changed_ranges == []


def test_source_change_count_change_still_resets_model(adapter, mock_store):
    assets = [
        _make_dto(id="a", abs_path=Path("/library/a.jpg")),
        _make_dto(id="b", abs_path=Path("/library/b.jpg")),
        _make_dto(id="c", abs_path=Path("/library/c.jpg")),
    ]
    mock_store.count.return_value = 2
    mock_store.active_root.return_value = Path("/library")
    mock_store.current_query.return_value = "all"
    mock_store.current_direct_assets.return_value = None
    mock_store.asset_at.side_effect = lambda row: assets[row]
    mock_store.snapshot_signature.return_value = (2, (0, 1), 1)
    reset_count = 0
    changed_ranges: list[tuple[int, int]] = []

    def _record_reset() -> None:
        nonlocal reset_count
        reset_count += 1

    adapter.modelReset.connect(_record_reset)
    adapter.dataChanged.connect(
        lambda top, bottom, _roles: changed_ranges.append((top.row(), bottom.row()))
    )

    adapter._on_source_changed()
    mock_store.count.return_value = 3
    mock_store.snapshot_signature.return_value = (3, (0, 2), 2)
    adapter._on_source_changed()

    assert reset_count == 2
    assert changed_ranges == []

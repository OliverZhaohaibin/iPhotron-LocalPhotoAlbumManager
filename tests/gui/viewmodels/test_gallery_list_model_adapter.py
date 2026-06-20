from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtGui import QImage

from iPhoto.application.dtos import AssetDTO
from iPhoto.domain.models.query import AssetQuery
from iPhoto.gui.gallery_demand import build_viewport_demand
from iPhoto.gui.ui.models.roles import Roles
from iPhoto.gui.viewmodels.gallery_collection_store import GalleryCollectionStore
from iPhoto.gui.viewmodels.gallery_list_model_adapter import GalleryListModelAdapter
from iPhoto.gui.viewmodels.asset_dto_converter import scan_row_to_dto
from iPhoto.gui.viewmodels.gallery_thumbnail_hint_loader import (
    GalleryThumbnailCandidate,
    GalleryThumbnailHintResult,
)
from iPhoto.gui.viewmodels.gallery_tile import GalleryTileSnapshot
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
    service.peek_full_thumbnail.return_value = None
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


def test_prioritize_rows_delegates_to_store(adapter, mock_store):
    adapter.prioritize_rows(10, 25)
    adapter._flush_pending_prioritize_rows()
    mock_store.prioritize_rows.assert_called_once_with(10, 25)


def test_prioritize_rows_keeps_only_latest_fast_scroll_request(adapter, mock_store):
    adapter.prioritize_rows(10, 25)
    adapter.prioritize_rows(20, 60)
    adapter.prioritize_rows(5, 15)
    adapter._flush_pending_prioritize_rows()

    mock_store.prioritize_rows.assert_called_once_with(5, 15)


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
    assert (
        adapter._handle_thumbnail_backfill_progress
        not in old_service.thumbnail_backfill_progress.handlers
    )
    assert (
        adapter._handle_thumbnail_backfill_progress
        in new_service.thumbnail_backfill_progress.handlers
    )
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
    mock_thumb_service.peek_full_thumbnail.return_value = full_size

    result = adapter.data(adapter.index(0, 0), Qt.DecorationRole)

    assert result is full_size
    mock_thumb_service.peek_full_thumbnail.assert_called_once_with(
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
    mock_thumb_service.peek_full_thumbnail.return_value = None

    index = adapter.index(0, 0)

    assert adapter.data(index, Qt.DecorationRole) is None
    assert adapter.data(index, Roles.MICRO_THUMBNAIL) is micro
    mock_thumb_service.get_thumbnail.assert_not_called()


def test_decoration_role_never_schedules_full_size_from_paint(
    adapter,
    mock_store,
    mock_thumb_service,
):
    fallback = object()
    mock_store.count.return_value = 1
    mock_store.asset_at.return_value = _make_dto(micro_thumbnail=b"jpeg-bytes")
    mock_thumb_service.peek_full_thumbnail.return_value = fallback

    result = adapter.data(adapter.index(0, 0), Qt.DecorationRole)

    assert result is fallback
    mock_thumb_service.get_thumbnail.assert_not_called()


def test_tile_snapshot_is_micro_first_and_memory_only(adapter, mock_store, mock_thumb_service):
    micro = QImage(2, 2, QImage.Format.Format_RGB32)
    mock_store.count.return_value = 1
    mock_store.asset_at.return_value = _make_dto(micro_thumbnail=micro)
    mock_thumb_service.peek_full_thumbnail.return_value = None

    snapshot = adapter.data(adapter.index(0, 0), Roles.TILE_SNAPSHOT)

    assert isinstance(snapshot, GalleryTileSnapshot)
    assert snapshot.loading_state == "micro"
    assert snapshot.micro_image is micro
    assert snapshot.full_pixmap is None
    mock_store.ensure_row_loaded.assert_not_called()
    mock_thumb_service.request_many.assert_not_called()


def test_tile_snapshot_miss_does_not_synchronously_load(adapter, mock_store):
    mock_store.count.return_value = 1
    mock_store.asset_at.return_value = None

    snapshot = adapter.data(adapter.index(0, 0), Roles.TILE_SNAPSHOT)

    assert isinstance(snapshot, GalleryTileSnapshot)
    assert snapshot.loading_state == "placeholder"
    mock_store.ensure_row_loaded.assert_not_called()


def test_scan_row_to_dto_preserves_thumb_cache_key() -> None:
    dto = scan_row_to_dto(
        Path("/library"),
        "ready.jpg",
        {
            "id": "ready",
            "rel": "ready.jpg",
            "thumbnail_state": "ready",
            "thumb_cache_key": "l2-ready",
        },
    )

    assert dto is not None
    assert dto.thumb_cache_key == "l2-ready"


def test_fast_viewport_warms_micro_and_still_requests_visible_full(
    adapter,
    mock_store,
    mock_thumb_service,
):
    dto = _make_dto(abs_path=Path("/library/photo.jpg"))
    mock_store.cached_rows.side_effect = [[(100, dto)], [(100, dto)]]
    demand = build_viewport_demand(
        generation=7,
        row_count=10_000,
        visible_first=100,
        visible_last=119,
        direction=1,
        screens_per_second=9.0,
        actively_scrolling=True,
    )

    adapter.update_viewport(demand)

    mock_store.reconcile_viewport_demand.assert_called_once_with(demand)
    snapshot = mock_thumb_service.reconcile_demand.call_args.args[0]
    assert snapshot.visible_paths == (Path("/library/photo.jpg"),)
    assert snapshot.guard_paths == ()
    assert snapshot.speculative_paths == ()
    assert snapshot.revision == 7
    assert demand.phase == "fast"
    assert demand.full_prefetch_range == demand.visible_range
    assert demand.warm_last - demand.warm_first + 1 == 2000


@pytest.mark.parametrize(("speed", "phase"), [(1.0, "slow"), (4.0, "medium")])
def test_scrolling_phase_immediately_requests_visible_full(
    adapter,
    mock_store,
    mock_thumb_service,
    speed,
    phase,
):
    dto = _make_dto(abs_path=Path("/library/photo.jpg"))
    mock_store.cached_rows.side_effect = [[(100, dto)], [(100, dto)]]
    demand = build_viewport_demand(
        generation=7,
        row_count=10_000,
        visible_first=100,
        visible_last=119,
        direction=1,
        screens_per_second=speed,
        actively_scrolling=True,
    )

    adapter.update_viewport(demand)

    assert demand.phase == phase
    mock_thumb_service.reconcile_demand.assert_called_once()
    assert mock_thumb_service.reconcile_demand.call_args.args[0].visible_paths == (
        Path("/library/photo.jpg"),
    )


def test_settled_viewport_requests_visible_and_ordered_prefetch_full(
    adapter,
    mock_store,
    mock_thumb_service,
):
    visible = _make_dto(abs_path=Path("/library/visible.jpg"))
    before = _make_dto(abs_path=Path("/library/before.jpg"))
    after = _make_dto(abs_path=Path("/library/after.jpg"))
    mock_store.cached_rows.side_effect = [
        [(100, visible)],
        [(99, before), (100, visible), (120, after)],
    ]
    demand = build_viewport_demand(
        generation=8,
        row_count=10_000,
        visible_first=100,
        visible_last=119,
        direction=1,
        screens_per_second=0.0,
        actively_scrolling=False,
    )

    adapter.update_viewport(demand)

    mock_store.reconcile_viewport_demand.assert_called_once_with(demand)
    snapshot = mock_thumb_service.reconcile_demand.call_args.args[0]
    assert snapshot.visible_paths == (Path("/library/visible.jpg"),)
    assert snapshot.guard_paths == (
        Path("/library/before.jpg"),
        Path("/library/after.jpg"),
    )
    assert demand.phase == "settled"
    assert demand.full_prefetch_first < demand.visible_first
    assert demand.full_prefetch_last > demand.visible_last


def test_cached_thumb_cache_key_becomes_prefetch_candidate(
    adapter,
    mock_store,
    mock_thumb_service,
):
    visible = _make_dto(abs_path=Path("/library/visible.jpg"))
    prefetch = _make_dto(
        abs_path=Path("/library/prefetch.jpg"),
        thumb_cache_key="l2-prefetch",
    )
    mock_store.cached_rows.side_effect = [
        [(100, visible)],
        [(120, prefetch)],
    ]
    demand = build_viewport_demand(
        generation=9,
        row_count=10_000,
        visible_first=100,
        visible_last=119,
        direction=1,
        screens_per_second=1.0,
        actively_scrolling=True,
    )
    adapter._viewport_demand = demand

    adapter._reconcile_full_thumbnail_demand()

    snapshot = mock_thumb_service.reconcile_demand.call_args.args[0]
    candidates = snapshot.candidates
    assert len(candidates) == 1
    assert candidates[0].path == Path("/library/prefetch.jpg")
    assert candidates[0].l2_cache_key == "l2-prefetch"
    assert candidates[0].kind == "guard"


def test_cached_and_hint_prefetch_paths_keep_viewport_order(
    adapter,
    mock_store,
    mock_thumb_service,
):
    visible = _make_dto(abs_path=Path("/library/visible.jpg"))
    next_screen = _make_dto(
        abs_path=Path("/library/next.jpg"),
        thumb_cache_key="l2-next",
    )
    far_hint = GalleryThumbnailCandidate(
        121,
        Path("/library/far.jpg"),
        "l2-far",
        5,
        "far_speculative",
    )
    mock_store.cached_rows.side_effect = [
        [(100, visible)],
        [(120, next_screen)],
    ]
    demand = build_viewport_demand(
        generation=10,
        row_count=10_000,
        visible_first=100,
        visible_last=119,
        direction=1,
        screens_per_second=1.0,
        actively_scrolling=True,
    )
    adapter._viewport_demand = demand
    adapter._thumbnail_hint_candidates_by_row = {121: far_hint}

    adapter._reconcile_full_thumbnail_demand()

    snapshot = mock_thumb_service.reconcile_demand.call_args.args[0]
    assert snapshot.guard_paths[:2] == (
        Path("/library/next.jpg"),
        Path("/library/far.jpg"),
    )


def test_full_thumbnail_snapshot_separates_guard_from_speculation(
    adapter,
    mock_store,
    mock_thumb_service,
):
    visible_rows = [
        (row, _make_dto(abs_path=Path(f"/library/visible-{row}.jpg")))
        for row in range(100, 103)
    ]
    demand = build_viewport_demand(
        generation=11,
        row_count=10_000,
        visible_first=100,
        visible_last=102,
        direction=1,
        screens_per_second=1.0,
        actively_scrolling=True,
    )
    prefetch_rows = [
        (row, _make_dto(abs_path=Path(f"/library/prefetch-{row}.jpg")))
        for row in demand.iter_full_prefetch_rows()
    ]
    mock_store.cached_rows.side_effect = [
        visible_rows,
        [*visible_rows, *prefetch_rows],
    ]
    adapter._viewport_demand = demand

    adapter._reconcile_full_thumbnail_demand()

    snapshot = mock_thumb_service.reconcile_demand.call_args.args[0]
    assert len(snapshot.guard_paths) == len(tuple(demand.iter_full_guard_rows()))
    assert len(snapshot.speculative_paths) == len(
        tuple(demand.iter_full_speculative_rows())
    )
    assert set(snapshot.guard_paths).isdisjoint(snapshot.speculative_paths)


def test_viewport_update_prunes_only_irrelevant_thumbnail_hints(
    adapter,
    mock_store,
):
    mock_store.cached_rows.return_value = []
    demand = build_viewport_demand(
        generation=10,
        row_count=10_000,
        visible_first=100,
        visible_last=119,
        direction=1,
        screens_per_second=1.0,
        actively_scrolling=True,
    )
    ordered_rows = tuple(demand.iter_full_prefetch_rows())
    retained_row = ordered_rows[0]
    stale_row = max(ordered_rows) + 100
    adapter._thumbnail_hint_candidates_by_row = {
        retained_row: GalleryThumbnailCandidate(
            retained_row,
            Path("/library/retained.jpg"),
            "retained-key",
            99,
            "far_speculative",
        ),
        stale_row: GalleryThumbnailCandidate(
            stale_row,
            Path("/library/stale.jpg"),
            "stale-key",
            0,
            "guard",
        ),
    }

    adapter.update_viewport(demand)

    assert set(adapter._thumbnail_hint_candidates_by_row) == {retained_row}


def test_retained_thumbnail_hints_are_reranked_for_current_demand(adapter):
    demand = build_viewport_demand(
        generation=10,
        row_count=10_000,
        visible_first=100,
        visible_last=119,
        direction=1,
        screens_per_second=1.0,
        actively_scrolling=True,
    )
    ordered_rows = tuple(demand.iter_full_prefetch_rows())
    visible_count = demand.visible_last - demand.visible_first + 1
    guard_rows = frozenset(ordered_rows[:visible_count])
    first_row = ordered_rows[0]
    later_row = ordered_rows[-1]
    adapter._thumbnail_hint_candidates_by_row = {
        first_row: GalleryThumbnailCandidate(
            first_row,
            Path("/library/first.jpg"),
            "first-key",
            99,
            "far_speculative",
        ),
        later_row: GalleryThumbnailCandidate(
            later_row,
            Path("/library/later.jpg"),
            "later-key",
            0,
            "guard",
        ),
    }

    candidates = adapter._current_hint_candidates(ordered_rows, guard_rows)

    by_row = {candidate.row: candidate for candidate in candidates}
    assert by_row[first_row].rank == 0
    assert by_row[first_row].kind == "guard"
    assert by_row[later_row].rank == len(ordered_rows) - 1
    assert by_row[later_row].kind == "far_speculative"


def test_thumbnail_hint_request_uses_full_ordered_rows(adapter, mock_store):
    query_service = MagicMock()
    query_service.read_thumbnail_hint_window = MagicMock()
    mock_store.current_query.return_value = AssetQuery()
    mock_store.active_root.return_value = Path("/library")
    mock_store.library_root.return_value = Path("/library")
    mock_store.snapshot_signature.return_value = (10_000, (0, 100), 5)
    mock_store.asset_query_service = query_service
    demand = build_viewport_demand(
        generation=11,
        row_count=10_000,
        visible_first=100,
        visible_last=119,
        direction=1,
        screens_per_second=1.0,
        actively_scrolling=True,
    )

    with patch.object(adapter._thumbnail_hint_loader, "request") as request_hint:
        adapter._request_thumbnail_hints(demand)

    request = request_hint.call_args.args[0]
    assert request.collection_revision == 5
    assert request.ordered_rows == tuple(demand.iter_full_prefetch_rows())
    assert request.guard_rows == frozenset(demand.iter_full_guard_rows())


def test_slow_thumbnail_hint_request_uses_single_guard_first_query(adapter, mock_store):
    query_service = MagicMock()
    query_service.read_thumbnail_hint_window = MagicMock()
    mock_store.current_query.return_value = AssetQuery()
    mock_store.active_root.return_value = Path("/library")
    mock_store.library_root.return_value = Path("/library")
    mock_store.snapshot_signature.return_value = (10_000, (0, 100), 5)
    mock_store.asset_query_service = query_service
    demand = build_viewport_demand(
        generation=12,
        row_count=10_000,
        visible_first=100,
        visible_last=119,
        direction=1,
        screens_per_second=9.0,
        actively_scrolling=True,
        intent="slow_continuous",
    )
    ordered_rows = tuple(demand.iter_full_prefetch_rows())

    with patch.object(adapter._thumbnail_hint_loader, "request") as request_hint:
        adapter._request_thumbnail_hints(demand)

    assert request_hint.call_count == 1
    request = request_hint.call_args.args[0]
    assert request.ordered_rows == ordered_rows
    assert request.guard_rows == frozenset(demand.iter_full_guard_rows())


def test_continuous_burst_discards_queued_thumbnail_hint(adapter):
    demand = build_viewport_demand(
        generation=13,
        row_count=10_000,
        visible_first=100,
        visible_last=119,
        direction=1,
        screens_per_second=9.0,
        actively_scrolling=True,
        intent="continuous_burst",
    )

    with patch.object(adapter._thumbnail_hint_loader, "discard_queued") as discard:
        adapter._request_thumbnail_hints(demand)

    discard.assert_called_once_with()


def test_rebind_asset_query_service_updates_store(adapter, mock_store):
    query_service = MagicMock()
    root = Path("/library")

    adapter.rebind_asset_query_service(query_service, root)

    mock_store.rebind_asset_query_service.assert_called_once_with(query_service, root)


def test_old_generation_matching_thumbnail_hint_result_is_merged(
    adapter,
    mock_store,
    mock_thumb_service,
):
    query = AssetQuery()
    mock_store.current_query.return_value = query
    mock_store.active_root.return_value = Path("/library")
    mock_store.library_root.return_value = Path("/library")
    mock_store.snapshot_signature.return_value = (100, (0, 99), 3)
    adapter._viewport_demand = build_viewport_demand(
        generation=9,
        row_count=100,
        visible_first=10,
        visible_last=19,
        direction=1,
        screens_per_second=0.0,
        actively_scrolling=False,
        intent="directional_dwell",
        prefetch_direction=1,
    )

    adapter._on_thumbnail_hint_result(
        GalleryThumbnailHintResult(
            request_id=adapter._thumbnail_hint_request_id,
            generation=8,
            collection_revision=3,
            root=Path("/library"),
            query=query,
            first=0,
            limit=100,
            candidates=(
                GalleryThumbnailCandidate(
                    20,
                    Path("/library/ahead.jpg"),
                    "ahead-key",
                    0,
                    "guard",
                ),
            ),
            elapsed_ms=1.0,
        )
    )

    candidates = mock_thumb_service.reconcile_demand.call_args.args[0].candidates
    assert any(candidate.path == Path("/library/ahead.jpg") for candidate in candidates)


def test_thumbnail_hint_result_for_old_collection_revision_is_discarded(
    adapter,
    mock_store,
    mock_thumb_service,
):
    query = AssetQuery()
    mock_store.current_query.return_value = query
    mock_store.active_root.return_value = Path("/library")
    mock_store.library_root.return_value = Path("/library")
    mock_store.snapshot_signature.return_value = (100, (0, 99), 4)
    adapter._viewport_demand = build_viewport_demand(
        generation=9,
        row_count=100,
        visible_first=10,
        visible_last=19,
        direction=1,
        screens_per_second=0.0,
        actively_scrolling=False,
        intent="directional_dwell",
        prefetch_direction=1,
    )

    adapter._on_thumbnail_hint_result(
        GalleryThumbnailHintResult(
            request_id=adapter._thumbnail_hint_request_id,
            generation=8,
            collection_revision=3,
            root=Path("/library"),
            query=query,
            first=0,
            limit=100,
            candidates=(
                GalleryThumbnailCandidate(
                    20,
                    Path("/library/stale-row.jpg"),
                    "stale-row-key",
                    0,
                    "guard",
                ),
            ),
            elapsed_ms=1.0,
        )
    )

    mock_thumb_service.reconcile_demand.assert_not_called()


def test_thumbnail_hint_result_for_other_root_is_discarded(
    adapter,
    mock_store,
    mock_thumb_service,
):
    query = AssetQuery()
    mock_store.current_query.return_value = query
    mock_store.active_root.return_value = Path("/library")
    mock_store.library_root.return_value = Path("/library")
    mock_store.snapshot_signature.return_value = (100, (0, 99), 3)
    adapter._viewport_demand = build_viewport_demand(
        generation=9,
        row_count=100,
        visible_first=10,
        visible_last=19,
        direction=1,
        screens_per_second=0.0,
        actively_scrolling=False,
        intent="directional_dwell",
        prefetch_direction=1,
    )

    adapter._on_thumbnail_hint_result(
        GalleryThumbnailHintResult(
            request_id=adapter._thumbnail_hint_request_id,
            generation=9,
            collection_revision=3,
            root=Path("/other-library"),
            query=query,
            first=0,
            limit=100,
            candidates=(
                GalleryThumbnailCandidate(
                    20,
                    Path("/other-library/ahead.jpg"),
                    "ahead-key",
                    0,
                    "guard",
                ),
            ),
            elapsed_ms=1.0,
        )
    )

    mock_thumb_service.reconcile_demand.assert_not_called()


def test_old_thumbnail_hint_request_id_with_matching_selection_is_merged(
    adapter,
    mock_store,
    mock_thumb_service,
):
    query = AssetQuery()
    mock_store.current_query.return_value = query
    mock_store.active_root.return_value = Path("/library")
    mock_store.library_root.return_value = Path("/library")
    mock_store.snapshot_signature.return_value = (100, (0, 99), 3)
    adapter._viewport_demand = build_viewport_demand(
        generation=9,
        row_count=100,
        visible_first=10,
        visible_last=19,
        direction=1,
        screens_per_second=0.0,
        actively_scrolling=False,
        intent="directional_dwell",
        prefetch_direction=1,
    )
    adapter._thumbnail_hint_request_id = 2

    adapter._on_thumbnail_hint_result(
        GalleryThumbnailHintResult(
            request_id=1,
            generation=9,
            collection_revision=3,
            root=Path("/library"),
            query=query,
            first=0,
            limit=100,
            candidates=(
                GalleryThumbnailCandidate(
                    20,
                    Path("/library/late.jpg"),
                    "late-key",
                    0,
                    "guard",
                ),
            ),
            elapsed_ms=1.0,
        )
    )

    candidates = mock_thumb_service.reconcile_demand.call_args.args[0].candidates
    assert any(candidate.path == Path("/library/late.jpg") for candidate in candidates)


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


def test_source_change_with_new_revision_clears_thumbnail_hints(adapter, mock_store):
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
    adapter._thumbnail_hint_candidates_by_row = {
        1: GalleryThumbnailCandidate(
            1,
            Path("/library/b.jpg"),
            "b-key",
            0,
            "guard",
        )
    }

    adapter._on_source_changed()
    mock_store.snapshot_signature.return_value = (2, (0, 1), 2)
    adapter._on_source_changed()

    assert adapter._thumbnail_hint_candidates_by_row == {}


def test_source_revision_change_republishes_static_viewport_demand(
    adapter,
    mock_store,
):
    assets = [
        _make_dto(id="a", abs_path=Path("/library/a.jpg")),
        _make_dto(id="b", abs_path=Path("/library/b.jpg")),
    ]
    query = AssetQuery()
    mock_store.count.return_value = 2
    mock_store.active_root.return_value = Path("/library")
    mock_store.current_query.return_value = query
    mock_store.current_direct_assets.return_value = None
    mock_store.asset_at.side_effect = lambda row: assets[row]
    mock_store.snapshot_signature.return_value = (2, (0, 1), 1)
    adapter._on_source_changed()
    demand = build_viewport_demand(
        generation=1,
        row_count=2,
        visible_first=0,
        visible_last=0,
        direction=0,
        screens_per_second=0.0,
        actively_scrolling=False,
    )
    adapter._ensure_coordinator_viewport(demand)
    previous_generation = adapter._viewport_demand.generation

    mock_store.snapshot_signature.return_value = (2, (0, 1), 2)
    with (
        patch.object(adapter, "_request_thumbnail_hints") as request_hints,
        patch.object(adapter, "_reconcile_full_thumbnail_demand") as reconcile_full,
    ):
        adapter._on_source_changed()

    assert adapter._demand_coordinator.collection_revision == 2
    assert adapter._viewport_demand.generation > previous_generation
    request_hints.assert_called_once_with(adapter._viewport_demand)
    reconcile_full.assert_called_once_with()


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

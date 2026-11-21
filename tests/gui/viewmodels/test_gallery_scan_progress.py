from __future__ import annotations

from dataclasses import replace
from io import BytesIO
from pathlib import Path
from threading import Event
from time import monotonic
from unittest.mock import MagicMock

import pytest
from PIL import Image
from PySide6.QtCore import QPersistentModelIndex
from PySide6.QtTest import QTest

from iPhoto.bootstrap.library_asset_query_service import LibraryAssetQueryService
from iPhoto.bootstrap.library_scan_service import LibraryScanService
from iPhoto.cache.index_store.repository import AssetRepository
from iPhoto.domain.models.query import AssetQuery
from iPhoto.domain.models.scan import ScanBatchCommitted
from iPhoto.gui.gallery_demand import build_viewport_demand
from iPhoto.gui.ui.models.roles import Roles
from iPhoto.gui.ui.widgets.asset_delegate import AssetGridDelegate
from iPhoto.gui.ui.widgets.gallery_grid_view import GalleryGridView
from iPhoto.gui.viewmodels.gallery_list_model_adapter import GalleryListModelAdapter
from iPhoto.infrastructure.services.thumbnail_cache_service import ThumbnailCacheService


def _wait_for(qapp, condition) -> None:
    deadline = monotonic() + 5
    while not condition() and monotonic() < deadline:
        qapp.processEvents()
        QTest.qWait(1)
    assert condition()


class _PreparedScanner:
    rows: list[dict]

    def scan(self, *_args, **_kwargs):
        yield from self.rows


@pytest.fixture
def gallery(tmp_path: Path, qapp, monkeypatch):
    root = tmp_path.resolve()
    repository = AssetRepository(root)
    service = LibraryAssetQueryService(root, repository_factory=lambda _root: repository)
    monkeypatch.setattr(service, "request_thumbnail_backfill", lambda *_args: 0)
    thumbnails = MagicMock(spec=ThumbnailCacheService)
    adapter = GalleryListModelAdapter.create(
        asset_query_service=service, thumbnail_service=thumbnails, library_root=root,
    )
    try:
        yield root, repository, service, adapter
    finally:
        adapter.rebind_asset_query_service(None, None)
        for loader in (adapter._window_loader, adapter._thumbnail_hint_loader):
            loader.shutdown()
            assert loader._pool.waitForDone(5_000)
        qapp.processEvents()
        adapter.deleteLater()
        service.shutdown()
        repository.close()


@pytest.fixture
def make_rows():
    buffer = BytesIO()
    Image.new("RGB", (12, 12), "red").save(buffer, format="JPEG")
    micro = buffer.getvalue()

    def make(first: int, count: int) -> list[dict]:
        return [
            {
                "rel": f"photo-{i:04}.jpg", "id": f"asset-{i}", "sort_ts": i,
                "ts": i, "w": 12, "h": 12, "media_type": 0,
                "thumbnail_state": "ready", "thumb_cache_key": f"key-{i}",
                "thumb_revision": f"rev-{i}", "micro_thumbnail": micro,
            }
            for i in range(first, first + count)
        ]

    return make


@pytest.mark.parametrize("result_before_timer", [False, True])
def test_scan_batches_and_finish_survive_an_active_old_window(
    gallery, make_rows, qapp, monkeypatch, result_before_timer: bool,
) -> None:
    root, repository, service, model = gallery
    loader, store = model._window_loader, model.store
    query = AssetQuery()
    store.load_selection(root, query=query)
    _wait_for(qapp, lambda: loader._active_request is None)
    # Flush the same production coalescer explicitly to control the ordering.
    model._scan_batch_timer.setInterval(60_000)
    scan_rows = make_rows(0, 301)
    for row in scan_rows:
        (root / row["rel"]).touch()
    scanner = _PreparedScanner()
    scans = LibraryScanService(root, scanner=scanner, repository_factory=lambda _root: repository)
    jobs = []

    def commit(first: int, stop: int) -> None:
        scanner.rows = scan_rows[first:stop]
        jobs.append(scans.scan_album(
            root, include=["*.jpg"], persist_chunks=True, scan_batch_callback=model.handle_scan_batch,
        ))

    read_done, release = Event(), Event()
    original_reader = service.read_gallery_asset_window
    results = []

    def pause_first_window(*args):
        window = original_reader(*args)
        if not read_done.is_set():
            read_done.set()
            assert release.wait(5)
        return window

    monkeypatch.setattr(service, "read_gallery_asset_window", pause_first_window)
    loader.resultReady.connect(results.append)
    resets, insertions, layouts = [], [], []
    model.modelReset.connect(lambda: resets.append(model.rowCount()))
    model.rowsInserted.connect(lambda _parent, first, last: insertions.append((first, last, model.rowCount())))
    model.layoutChanged.connect(lambda: layouts.append(model.rowCount()))
    try:
        commit(0, 1)
        model._flush_pending_scan_batches()
        _wait_for(qapp, read_done.is_set)
        commit(1, 101)
        commit(101, 201)
        assert service.count_query_assets(query) == 201
        if not result_before_timer:
            model._flush_pending_scan_batches()
            assert len(loader._queued_requests) == 1

        release.set()
        _wait_for(qapp, lambda: bool(results))
        assert results[0].total_count == 1
        if result_before_timer:
            assert model.rowCount() == 1
            model._flush_pending_scan_batches()
        _wait_for(qapp, lambda: model.rowCount() == 201)
        selected = QPersistentModelIndex(model.index(200, 0))
        assert selected.data(Roles.ABS) == str(root / scan_rows[0]["rel"])

        commit(201, 301)
        scans.finalize_scan_result(
            root, scan_rows, current_scan_job_id=jobs[-1].scan_job_id,
            preserve_modified_after_ms=jobs[-1].scan_started_at_ms,
        )
        store.handle_scan_finished(root, True)
        model._flush_pending_scan_batches()
        _wait_for(qapp, lambda: model.rowCount() == 301 and loader._active_request is None)
        assert insertions == [(0, 0, 1), (1, 200, 201), (201, 300, 301)]
        assert layouts == [201, 301]
        assert not resets
        assert selected.row() == 300
        assert selected.data(Roles.ABS) == str(root / scan_rows[0]["rel"])

        # The store must also reject an older result that is still pending.
        requests = []
        store.set_window_request_handler(requests.append)
        store.handle_scan_finished(root, True)
        assert not store.apply_window_result(replace(results[0], generation=requests[-1].generation))
        assert model.rowCount() == 301
        fresh_repository = AssetRepository(root)
        fresh_service = LibraryAssetQueryService(root, repository_factory=lambda _root: fresh_repository)
        try:
            expected = [row["rel"] for row in reversed(scan_rows)]
            fresh = fresh_service.read_gallery_asset_window(root, query, 0, 301)
            assert [row["rel"] for row in fresh.rows] == expected
            assert [store.asset_at(i).rel_path.as_posix() for i in range(301)] == expected
        finally:
            fresh_service.shutdown()
            fresh_repository.close()
    finally:
        release.set()


def test_ready_and_backfill_bursts_leave_one_followup(gallery, make_rows, qapp, monkeypatch) -> None:
    root, repository, service, model = gallery
    loader, store, query = model._window_loader, model.store, AssetQuery()
    rows = make_rows(0, 11)
    repository.write_rows([rows[0], *(
        {**row, "thumbnail_state": "stale", "micro_thumbnail": None} for row in rows[1:]
    )])
    read_done, release = Event(), Event()
    original_reader = service.read_gallery_asset_window

    def pause_first_window(*args):
        window = original_reader(*args)
        if not read_done.is_set():
            read_done.set()
            assert release.wait(5)
        return window

    monkeypatch.setattr(service, "read_gallery_asset_window", pause_first_window)
    model._scan_batch_timer.setInterval(60_000)
    try:
        store.load_selection(root, query=query)
        _wait_for(qapp, read_done.is_set)
        for start in (1, 4, 7):
            for row in rows[start:start + 3]:
                assert service.publish_thumbnail_ready(
                    root / row["rel"], revision=row["thumb_revision"],
                    cache_key=row["thumb_cache_key"], micro_thumbnail=row["micro_thumbnail"],
                )
            model._flush_pending_scan_batches()
            assert len(loader._queued_requests) == 1

        last = rows[-1]
        repository.merge_scan_rows([last])
        service.thumbnail_backfill_completed.emit(ScanBatchCommitted(
            "thumbnail-backfill:draw", root, repository.collection_revision(), 1, [last], {},
        ))
        service.thumbnail_backfill_completed.emit(ScanBatchCommitted(
            "thumbnail-backfill:empty", root, 0, 0, [], {},
        ))
        model._flush_pending_scan_batches()
        assert len(loader._queued_requests) == 1
        release.set()
        _wait_for(qapp, lambda: model.rowCount() == 11 and loader._active_request is None)
        assert store.asset_at(0).rel_path.as_posix() == last["rel"]
        assert store.asset_at(10).rel_path.as_posix() == rows[0]["rel"]
    finally:
        release.set()


def test_thumbnail_only_window_keeps_other_visible_rows(gallery, make_rows, qapp) -> None:
    root, repository, _service, model = gallery
    loader, store = model._window_loader, model.store
    rows = make_rows(0, 12)
    repository.write_rows(rows)
    store.load_selection(root, query=AssetQuery())
    _wait_for(qapp, lambda: model.rowCount() == 12)
    unaffected = store.asset_at(11)
    target = rows[-1]
    changed, resets = [], []
    model.dataChanged.connect(lambda first, last, _roles: changed.append((first.row(), last.row())))
    model.modelReset.connect(lambda: resets.append(True))
    assert repository.update_thumbnail_ready(
        target["rel"], expected_revision=target["thumb_revision"],
        thumb_cache_key=target["thumb_cache_key"], micro_thumbnail=b"new-preview",
    )
    store._request_async_chunk(0, 0, demand_generation=0, priority=0, request_backfill=False)
    _wait_for(qapp, lambda: loader._active_request is None)
    assert model.rowCount() == 12
    assert store.asset_at(11) is unaffected
    assert changed == [(0, 0)]
    assert not resets


def test_order_change_stages_both_visible_surfaces(gallery, make_rows, qapp) -> None:
    root, repository, _service, model = gallery
    loader, store = model._window_loader, model.store
    rows = make_rows(0, 300)
    repository.write_rows(rows)
    store.load_selection(root, query=AssetQuery())
    _wait_for(qapp, lambda: model.rowCount() == 300)
    for surface, first in (("gallery", 0), ("filmstrip", 290)):
        model.update_viewport(build_viewport_demand(
            generation=1, surface_id=surface, row_count=300,
            visible_first=first, visible_last=first + 3, direction=0,
            screens_per_second=0, actively_scrolling=False,
        ))
    _wait_for(qapp, lambda: store.asset_at(290) is not None and loader._active_request is None)
    published = []
    model.rowsInserted.connect(lambda *_args: published.append((store.asset_at(0), store.asset_at(290))))
    repository.merge_scan_rows(make_rows(300, 1))
    store._request_async_chunk(290, 293, demand_generation=0, priority=0, surface_id="filmstrip")
    _wait_for(qapp, lambda: model.rowCount() == 301 and loader._active_request is None)
    assert store.asset_at(0).id == "asset-300"
    assert store.asset_at(290).id == "asset-10"
    assert len(published) == 1
    assert all(asset is not None for asset in published[0])


def test_scrolled_grid_keeps_anchor_offset_when_a_batch_sorts_ahead(gallery, make_rows, qapp) -> None:
    root, repository, _service, model = gallery
    loader, store = model._window_loader, model.store
    repository.write_rows(make_rows(0, 300))
    grid = GalleryGridView()
    grid.resize(1_000, 610)
    grid.setModel(model)
    grid.setItemDelegate(AssetGridDelegate(grid, filmstrip_mode=False))
    grid.viewportStateChanged.connect(model.update_viewport)
    grid.show()
    try:
        store.load_selection(root, query=AssetQuery())
        _wait_for(qapp, lambda: model.rowCount() == 300 and grid._visible_range is not None)
        grid.verticalScrollBar().setValue(1_100)
        _wait_for(qapp, lambda: grid._visible_range[0] > 0 and loader._active_request is None)
        grid.setCurrentIndex(model.index(grid._visible_range[0] + 7, 0))
        selected_path = grid.currentIndex().data(Roles.ABS)
        top = model.index(grid._visible_range[0], 0)
        before = grid.visualRect(top).top()
        anchor_path = Path(top.data(Roles.ABS))

        repository.merge_scan_rows(make_rows(300, 20))
        store.handle_scan_finished(root, True)
        _wait_for(qapp, lambda: model.rowCount() == 320 and not grid._scroll_restore_timer.isActive())
        restored = model.index(store.cached_row_for_path(anchor_path), 0)
        assert grid.visualRect(restored).top() == before
        assert grid.currentIndex().data(Roles.ABS) == selected_path
    finally:
        grid.hide()
        grid.setModel(None)
        grid.deleteLater()

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from threading import Event
from time import monotonic, sleep
from unittest.mock import MagicMock

import pytest
from PIL import Image
from PySide6.QtCore import QItemSelection, QItemSelectionModel, QPersistentModelIndex, Qt
from PySide6.QtTest import QAbstractItemModelTester, QTest
from PySide6.QtWidgets import QPushButton

from iPhoto.bootstrap.library_asset_query_service import LibraryAssetQueryService
from iPhoto.bootstrap.library_scan_service import LibraryScanService
from iPhoto.cache.index_store.repository import AssetRepository
from iPhoto.domain.models.query import AssetQuery
from iPhoto.domain.models.scan import ScanBatchCommitted
from iPhoto.gui.gallery_demand import build_viewport_demand
from iPhoto.gui.ui.controllers.selection_controller import SelectionController
from iPhoto.gui.ui.models.roles import Roles
from iPhoto.gui.ui.models.spacer_proxy_model import SpacerProxyModel
from iPhoto.gui.ui.models.thumbnail_surface_proxy_model import ThumbnailSurfaceProxyModel
from iPhoto.gui.ui.widgets.asset_delegate import AssetGridDelegate
from iPhoto.gui.ui.widgets.gallery_grid_view import GalleryGridView
from iPhoto.gui.viewmodels.gallery_list_model_adapter import GalleryListModelAdapter
from iPhoto.infrastructure.services.thumbnail_cache_service import ThumbnailCacheService


def _wait_for(qapp, condition) -> None:
    deadline = monotonic() + 5
    while not condition() and monotonic() < deadline:
        qapp.processEvents()
        # Release the GIL so the Python QRunnable can produce its result.
        sleep(0.001)
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
    thumbnails.peek_full_thumbnail.return_value = None
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
        assert read_done.wait(5)
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
        assert read_done.wait(5)
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


@pytest.mark.parametrize(
    ("notify_before_old_result", "cancel"),
    [(False, False), (True, False), (True, True)],
    ids=["result-first", "notification-first", "cancel-after-commit"],
)
def test_real_jpeg_scan_draws_committed_batches_before_scan_finishes(
    gallery, qapp, monkeypatch, notify_before_old_result: bool, cancel: bool,
) -> None:
    root, repository, service, model = gallery
    store, loader = model.store, model._window_loader
    for i in range(205):
        path = root / f"photo-{i:03}.jpg"
        Image.new("RGB", (48, 32), (i, 30, 60)).save(path)
        os.utime(path, ns=(0, 1_700_000_000_000_000_000 + i * 1_000_000_000))

    store.load_selection(root, query=AssetQuery())
    _wait_for(qapp, lambda: loader._active_request is None)
    assert model.rowCount() == 0
    real_read = service.read_gallery_asset_window
    read_old_window, deliver_old_window = Event(), Event()
    permit_second, permit_final = Event(), Event()
    cancelled = Event()
    batches, resets = [], []
    model.modelReset.connect(lambda: resets.append(True))
    model._scan_batch_timer.setInterval(60_000)

    def pause_first_snapshot(*args):
        window = real_read(*args)
        if not read_old_window.is_set():
            assert window.total_count == 100
            read_old_window.set()
            assert deliver_old_window.wait(5)
        return window

    def on_commit(batch):
        batches.append(batch)
        if len(batches) == 1:
            model.handle_scan_batch(batch)
            assert permit_second.wait(5)
        elif len(batches) == 2:
            if notify_before_old_result:
                model.handle_scan_batch(batch)
            assert permit_final.wait(5)
        else:
            model.handle_scan_batch(batch)

    monkeypatch.setattr(service, "read_gallery_asset_window", pause_first_snapshot)
    scan = LibraryScanService(root, repository_factory=lambda _root: repository)
    with ThreadPoolExecutor(max_workers=1) as executor:
        result = executor.submit(
            scan.scan_album, root, include=["*.jpg"], persist_chunks=True,
            chunk_size=100, scan_batch_callback=on_commit, is_cancelled=cancelled.is_set,
        )
        try:
            _wait_for(qapp, lambda: model._pending_scan_batch_count == 1)
            model._flush_pending_scan_batches()
            assert read_old_window.wait(5)
            expected_first = root / real_read(root, AssetQuery(), 0, 1).rows[0]["rel"]
            permit_second.set()
            _wait_for(qapp, lambda: len(batches) == 2)
            assert service.count_query_assets(AssetQuery()) == 200
            if notify_before_old_result:
                _wait_for(qapp, lambda: model._pending_scan_batch_count == 1)
                model._flush_pending_scan_batches()

            first_rows = []
            model.rowsInserted.connect(lambda *_args: first_rows.append((
                model.rowCount(), model.index(0, 0).data(Roles.ABS),
                model.index(0, 0).data(Roles.MICRO_THUMBNAIL),
            )))
            deliver_old_window.set()
            _wait_for(qapp, lambda: bool(first_rows))
            assert first_rows[0][0] == 100
            assert first_rows[0][1] == str(expected_first)
            assert not first_rows[0][2].isNull()
            if not notify_before_old_result:
                assert model.rowCount() == 100
                model.handle_scan_batch(batches[1])
                model._flush_pending_scan_batches()
            _wait_for(qapp, lambda: model.rowCount() == 200)
            assert not result.done()
            assert model.index(0, 0).data(Roles.ABS) == str(
                root / real_read(root, AssetQuery(), 0, 1).rows[0]["rel"]
            )

            if cancel:
                cancelled.set()
            permit_final.set()
            expected_total = 200 if cancel else 205
            assert len(result.result(timeout=5).rows) == expected_total
            store.handle_scan_finished(root, not cancel)
            _wait_for(qapp, lambda: model.rowCount() == expected_total and loader._active_request is None)
            fresh = real_read(root, AssetQuery(), 0, 50)
            assert fresh.total_count == expected_total
            assert [store.asset_at(i).id for i in range(50)] == [row["id"] for row in fresh.rows]
            assert not resets
        finally:
            permit_second.set()
            permit_final.set()
            deliver_old_window.set()


@pytest.mark.parametrize("switch_to", ["album", "favorites", "library"])
def test_scan_refresh_cannot_publish_into_superseding_selection(
    gallery, make_rows, qapp, monkeypatch, switch_to: str,
) -> None:
    root, repository, service, model = gallery
    loader, store = model._window_loader, model.store
    rows = make_rows(0, 5)
    rows[0].update(rel="Trip/favorite.jpg", is_favorite=1)
    repository.write_rows(rows[:4])
    store.load_selection(root, query=AssetQuery())
    _wait_for(qapp, lambda: model.rowCount() == 4 and loader._active_request is None)
    read, deliver = Event(), Event()
    real_read = service.read_gallery_asset_window

    def pause_old_snapshot(*args):
        window = real_read(*args)
        if not read.is_set():
            read.set()
            assert deliver.wait(5)
        return window

    monkeypatch.setattr(service, "read_gallery_asset_window", pause_old_snapshot)
    observed = []
    model.rowsInserted.connect(lambda *_args: observed.append(model.index(0, 0).data(Roles.ABS)))
    replacement_repository = replacement_service = None
    try:
        repository.merge_scan_rows([rows[4]])
        store.handle_scan_finished(root, True)
        assert read.wait(5)
        expected = root / rows[0]["rel"]
        if switch_to == "album":
            store.load_selection(root / "Trip", query=AssetQuery(album_path="Trip"))
        elif switch_to == "favorites":
            store.load_selection(root, query=AssetQuery(is_favorite=True))
        else:
            other = root / "other-library"
            other.mkdir()
            replacement_repository = AssetRepository(other)
            replacement_repository.write_rows([rows[1]])
            replacement_service = LibraryAssetQueryService(
                other, repository_factory=lambda _root: replacement_repository,
            )
            model.rebind_asset_query_service(replacement_service, other)
            store.load_selection(other, query=AssetQuery())
            expected = other / rows[1]["rel"]
        deliver.set()
        _wait_for(qapp, lambda: model.rowCount() == 1 and loader._active_request is None)
        assert observed == [str(expected)]
    finally:
        deliver.set()
        if replacement_service is not None:
            replacement_service.shutdown()
            replacement_repository.close()


def test_offscreen_anchor_does_not_report_missing_against_an_older_window(
    gallery, make_rows, qapp, monkeypatch,
) -> None:
    root, repository, service, model = gallery
    loader, store = model._window_loader, model.store
    rows = make_rows(0, 300)
    repository.write_rows(rows)
    store.load_selection(root, query=AssetQuery())
    _wait_for(qapp, lambda: model.rowCount() == 300 and loader._active_request is None)
    current = root / rows[0]["rel"]
    store.pin_path(current, asset_id=rows[0]["id"], previous_row=299)
    read, deliver = Event(), Event()
    real_read = service.read_gallery_asset_window

    def pause_old_snapshot(*args):
        window = real_read(*args)
        if not read.is_set():
            read.set()
            assert deliver.wait(5)
        return window

    monkeypatch.setattr(service, "read_gallery_asset_window", pause_old_snapshot)
    results = []
    loader.resultReady.connect(results.append)
    try:
        store._request_async_chunk(0, 5, demand_generation=0, priority=0, request_backfill=False)
        assert read.wait(5)
        repository.remove_rows([rows[0]["rel"]])
        deliver.set()
        _wait_for(qapp, lambda: bool(results))
        assert results[0].selection_anchor_result.status == "retry"
        assert model.rowCount() == 300
        store.handle_scan_finished(root, True)
        _wait_for(qapp, lambda: model.rowCount() == 299)
        assert store.selection_anchor_status(current) == "missing"
    finally:
        deliver.set()


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


def test_order_change_stages_both_visible_surfaces(gallery, make_rows, qapp, capfd) -> None:
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
    surface = ThumbnailSurfaceProxyModel("filmstrip", model)
    surface.setSourceModel(model)
    filmstrip = SpacerProxyModel(model)
    filmstrip.setSourceModel(surface)
    for tested in (model, surface, filmstrip):
        QAbstractItemModelTester(tested, QAbstractItemModelTester.FailureReportingMode.Warning, tested)
    current_path = store.asset_at(290).abs_path
    model.set_current_asset(290, current_path)
    persistent = QPersistentModelIndex(filmstrip.index(291, 0))
    published, layouts, resets = [], [], []
    filmstrip.modelReset.connect(lambda: resets.append(True))

    def on_insert(_parent, first, last):
        published.append((
            first, last, model.rowCount(), model.index(0, 0).data(Roles.ASSET_ID),
            persistent.data(Roles.ASSET_ID), model.index(last, 0).data(Roles.ASSET_ID),
            model.flags(model.index(last, 0)) & Qt.ItemFlag.ItemIsSelectable,
        ))

    model.rowsInserted.connect(on_insert)
    filmstrip.layoutChanged.connect(lambda: layouts.append((
        persistent.row(), persistent.data(Roles.ASSET_ID), persistent.data(Roles.IS_CURRENT),
        filmstrip.index(291, 0).data(Roles.IS_CURRENT),
    )))
    repository.merge_scan_rows([
        *make_rows(300, 1), {**rows[4], "sort_ts": 299},
    ])
    store._request_async_chunk(290, 293, demand_generation=0, priority=0, surface_id="filmstrip")
    _wait_for(qapp, lambda: model.rowCount() == 301 and loader._active_request is None)
    assert store.asset_at(0).id == "asset-300"
    assert store.asset_at(290).id == "asset-11"
    assert published == [(300, 300, 301, "asset-299", "asset-9", None, Qt.ItemFlag.NoItemFlags)]
    assert layouts == [(293, "asset-9", True, False)]
    assert not resets
    assert "qt.modeltest: FAIL" not in capfd.readouterr().err


def test_filmstrip_structural_signals_include_spacers_at_empty_boundary(gallery, make_rows, qapp, capfd) -> None:
    root, repository, _service, model = gallery
    store, loader = model.store, model._window_loader
    surface = ThumbnailSurfaceProxyModel("filmstrip", model)
    surface.setSourceModel(model)
    filmstrip = SpacerProxyModel(model)
    filmstrip.setSourceModel(surface)
    QAbstractItemModelTester(filmstrip, QAbstractItemModelTester.FailureReportingMode.Warning, filmstrip)
    store.load_selection(root, query=AssetQuery())
    _wait_for(qapp, lambda: loader._active_request is None)
    inserted, removed, resets = [], [], []
    filmstrip.rowsInserted.connect(lambda _parent, first, last: inserted.append((first, last, filmstrip.rowCount())))
    filmstrip.rowsRemoved.connect(lambda _parent, first, last: removed.append((first, last, filmstrip.rowCount())))
    filmstrip.modelReset.connect(lambda: resets.append(True))

    rows = make_rows(0, 2)
    repository.append_rows(rows)
    store.handle_scan_finished(root, True)
    _wait_for(qapp, lambda: filmstrip.rowCount() == 4 and loader._active_request is None)
    assert inserted == [(0, 3, 4)]
    assert [filmstrip.index(row, 0).data(Roles.IS_SPACER) for row in range(4)] == [True, False, False, True]
    last_asset = QPersistentModelIndex(filmstrip.index(2, 0))
    repository.remove_rows([row["rel"] for row in rows])
    store.handle_scan_finished(root, True)
    _wait_for(qapp, lambda: filmstrip.rowCount() == 0 and loader._active_request is None)
    assert removed == [(0, 3, 0)]
    assert not last_asset.isValid()
    assert not resets
    assert "qt.modeltest: FAIL" not in capfd.readouterr().err


def test_offscreen_indexes_and_selection_survive_delete_and_reorder(
    gallery, make_rows, qapp, monkeypatch, capfd,
) -> None:
    root, repository, _service, model = gallery
    loader, store = model._window_loader, model.store
    rows = make_rows(0, 750)
    repository.write_rows(rows)
    store.load_selection(root, query=AssetQuery())
    _wait_for(qapp, lambda: model.rowCount() == 750)
    for selected_row in (389, 390, 391):
        store.ensure_row_loaded(selected_row)
    _wait_for(qapp, lambda: store.asset_at(389) is not None and loader._active_request is None)
    grid = GalleryGridView()
    grid.setModel(model)
    controller = SelectionController(QPushButton(grid), grid, None, MagicMock(), parent=grid)
    controller.set_selection_mode(True)
    selection = grid.selectionModel()
    indexes = [QPersistentModelIndex(model.index(row, 0)) for row in (389, 390, 391)]
    selection.select(
        QItemSelection(model.index(389, 0), model.index(391, 0)),
        QItemSelectionModel.SelectionFlag.Select,
    )
    thumbnail_surface = ThumbnailSurfaceProxyModel("filmstrip", model)
    thumbnail_surface.setSourceModel(model)
    filmstrip = SpacerProxyModel(model)
    filmstrip.setSourceModel(thumbnail_surface)
    filmstrip_index = QPersistentModelIndex(filmstrip.index(391, 0))
    assert [index.data(Roles.ASSET_ID) for index in indexes] == ["asset-360", "asset-359", "asset-358"]
    with monkeypatch.context() as trim:
        trim.setattr("iPhoto.gui.viewmodels.gallery_collection_store.MICRO_WARM_LIMIT", 10)
        for row, _ in store.cached_rows(0, 749):
            if row not in (389, 390, 391):
                store.asset_at(row)
        store._trim_row_cache()
    assert all(store.asset_at(row) is None for row in (389, 390, 391))

    QAbstractItemModelTester(model, QAbstractItemModelTester.FailureReportingMode.Warning, model)
    repository.remove_rows([rows[360]["rel"], rows[746]["rel"]])
    repository.merge_scan_rows([{**rows[358], "sort_ts": 1000}])
    store.handle_scan_finished(root, True)
    _wait_for(qapp, lambda: model.rowCount() == 748 and loader._active_request is None)
    assert not indexes[0].isValid()
    assert (indexes[1].row(), indexes[1].data(Roles.ASSET_ID)) == (389, "asset-359")
    assert (indexes[2].row(), indexes[2].data(Roles.ASSET_ID)) == (0, "asset-358")
    assert (filmstrip_index.row(), filmstrip_index.data(Roles.ASSET_ID)) == (390, "asset-359")
    assert {index.data(Roles.ASSET_ID) for index in selection.selectedIndexes()} == {"asset-359", "asset-358"}
    assert "qt.modeltest: FAIL" not in capfd.readouterr().err
    grid.setModel(None)
    grid.deleteLater()


@pytest.mark.parametrize("replacement_visible", [True, False])
def test_scan_does_not_extend_a_large_select_all_to_replacement_assets(
    gallery, make_rows, qapp, replacement_visible: bool,
) -> None:
    root, repository, _service, model = gallery
    loader, store = model._window_loader, model.store
    rows = make_rows(0, 1001)
    repository.write_rows(rows)
    store.load_selection(root, query=AssetQuery())
    _wait_for(qapp, lambda: model.rowCount() == len(rows) and loader._active_request is None)
    store._request_async_chunk(0, len(rows) - 1, demand_generation=0, priority=0)
    _wait_for(qapp, lambda: loader._active_request is None)

    grid = GalleryGridView()
    grid.setModel(model)
    controller = SelectionController(QPushButton(grid), grid, None, MagicMock(), parent=grid)
    controller.set_selection_mode(True)
    try:
        grid.selectAll()
        selection = grid.selectionModel()
        assert {index.data(Roles.ASSET_ID) for index in selection.selectedIndexes()} == {
            row["id"] for row in rows
        }

        repository.remove_rows([rows[0]["rel"]])
        replacement = make_rows(len(rows), 1)[0]
        if not replacement_visible:
            replacement["sort_ts"] = -1
        repository.merge_scan_rows([replacement])
        store.handle_scan_finished(root, True)
        _wait_for(qapp, lambda: loader._active_request is None)

        assert model.rowCount() == len(rows)
        assert {index.data(Roles.ASSET_ID) for index in selection.selectedIndexes()} == {
            row["id"] for row in rows[1:]
        }
        replacement_row = 0 if replacement_visible else len(rows) - 1
        store.ensure_row_loaded(replacement_row)
        _wait_for(qapp, lambda: loader._active_request is None)
        replacement_index = model.index(replacement_row, 0)
        assert replacement_index.data(Roles.ASSET_ID) == replacement["id"]
        assert not selection.isSelected(replacement_index)
    finally:
        grid.setModel(None)
        grid.deleteLater()


@pytest.mark.parametrize("inserted", [20, 450])
def test_scrolled_grid_keeps_anchor_offset_when_a_batch_sorts_ahead(gallery, make_rows, qapp, inserted) -> None:
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

        repository.merge_scan_rows(make_rows(300, inserted))
        store.handle_scan_finished(root, True)
        _wait_for(qapp, lambda: model.rowCount() == 300 + inserted and not grid._scroll_restore_timer.isActive())
        restored = model.index(store.cached_row_for_path(anchor_path), 0)
        assert grid.visualRect(restored).top() == before
        assert grid.currentIndex().data(Roles.ABS) == selected_path
    finally:
        grid.hide()
        grid.setModel(None)
        grid.deleteLater()


def test_gallery_click_keeps_pressed_asset_when_a_scan_changes_its_row(gallery, make_rows, qapp) -> None:
    root, repository, _service, model = gallery
    store = model.store
    repository.write_rows(make_rows(0, 4))
    grid = GalleryGridView()
    grid.resize(800, 450)
    grid.setModel(model)
    grid.setItemDelegate(AssetGridDelegate(grid, filmstrip_mode=False))
    grid.set_preview_enabled(False)
    grid.show()
    try:
        store.load_selection(root, query=AssetQuery())
        _wait_for(qapp, lambda: model.rowCount() == 4 and grid._visible_range is not None)
        clicked = []
        grid.itemClicked.connect(lambda index: clicked.append(index.data(Roles.ASSET_ID)))
        start = grid.visualRect(model.index(0, 0)).center()
        QTest.mousePress(grid.viewport(), Qt.MouseButton.LeftButton, pos=start)
        repository.merge_scan_rows(make_rows(4, 1))
        store.handle_scan_finished(root, True)
        _wait_for(qapp, lambda: model.rowCount() == 5)
        assert model.index(0, 0).data(Roles.ASSET_ID) == "asset-4"
        QTest.mouseRelease(grid.viewport(), Qt.MouseButton.LeftButton, pos=start)
        assert clicked == ["asset-3"]
    finally:
        grid.hide()
        grid.setModel(None)
        grid.deleteLater()

from __future__ import annotations

import time
from pathlib import Path
from threading import Event

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtGui import QImage

from iPhoto.gui.detail_decode_backend import DecodedSurface
from iPhoto.gui.detail_pipeline import (
    AssetSourceIdentity,
    DetailDecodeKey,
    DetailGeometryState,
    DetailRenderRequest,
)
from iPhoto.gui.detail_request_scheduler import DetailStillRequestScheduler


class _WorkerSignals(QObject):
    started = Signal(object)
    completed = Signal(object)
    failed = Signal(Path, str)
    finished = Signal(object)


class _FakeWorker:
    def __init__(self, request: DetailRenderRequest) -> None:
        self.request = request.with_decode_level()
        self.source = request.source_identity.path
        self.signals = _WorkerSignals()
        self.frame_identity = None
        self.cache_hit = False
        self.cancelled = False
        self.auto_delete = False

    def cancel(self) -> None:
        self.cancelled = True

    def update_request(self, request: DetailRenderRequest) -> None:
        self.request = request.with_decode_level()

    def setAutoDelete(self, enabled: bool) -> None:
        self.auto_delete = bool(enabled)


class _FakePool:
    def __init__(self) -> None:
        self.queued: list[_FakeWorker] = []
        self.starts: list[tuple[_FakeWorker, int]] = []
        self.cleared = False
        self.wait_timeout: int | None = None
        self.wait_result = True

    def start(self, worker: _FakeWorker, priority: int) -> None:
        self.starts.append((worker, priority))
        self.queued.append(worker)

    def tryTake(self, worker: _FakeWorker) -> bool:
        if worker not in self.queued:
            return False
        self.queued.remove(worker)
        return True

    def mark_running(self, worker: _FakeWorker) -> None:
        if worker in self.queued:
            self.queued.remove(worker)
        worker.signals.started.emit(worker)

    def complete(self, worker: _FakeWorker) -> None:
        image = QImage(8, 8, QImage.Format.Format_RGBA8888)
        image.fill(0xFF112233)
        surface = DecodedSurface(
            image=image,
            decode_key=DetailDecodeKey.from_request(worker.request),
            source_size=(8, 8),
            decoded_size=(8, 8),
            decode_level=worker.request.decode_level or "full",
            backend="fake",
        )
        worker.signals.completed.emit(surface)
        worker.signals.finished.emit(worker)

    def clear(self) -> None:
        self.cleared = True
        self.queued.clear()

    def waitForDone(self, timeout_ms: int) -> bool:
        self.wait_timeout = timeout_ms
        return self.wait_result


class _BlockingWorker(QRunnable):
    def __init__(self, request: DetailRenderRequest, release: Event) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self.request = request.with_decode_level()
        self.source = request.source_identity.path
        self.signals = _WorkerSignals()
        self.release = release
        self.started = Event()
        self.cancelled = Event()
        self.cache_hit = False

    def cancel(self) -> None:
        self.cancelled.set()

    def update_request(self, request: DetailRenderRequest) -> None:
        self.request = request.with_decode_level()

    def run(self) -> None:
        self.signals.started.emit(self)
        self.started.set()
        try:
            if not self.release.wait(5) or self.cancelled.is_set():
                return
            image = QImage(8, 8, QImage.Format.Format_RGBA8888)
            image.fill(0xFF112233)
            self.signals.completed.emit(
                DecodedSurface(
                    image=image,
                    decode_key=DetailDecodeKey.from_request(self.request),
                    source_size=(8, 8),
                    decoded_size=(8, 8),
                    decode_level=self.request.decode_level or "full",
                    backend="blocking-fake",
                )
            )
        finally:
            self.signals.finished.emit(self)


def _spin_until(qapp, predicate, *, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    qapp.processEvents()
    return bool(predicate())


def _harness() -> tuple[
    DetailStillRequestScheduler,
    _FakePool,
    list[_FakeWorker],
]:
    pool = _FakePool()
    workers: list[_FakeWorker] = []

    def factory(request: DetailRenderRequest) -> _FakeWorker:
        worker = _FakeWorker(request)
        workers.append(worker)
        return worker

    scheduler = DetailStillRequestScheduler(pool=pool, worker_factory=factory)
    return scheduler, pool, workers


def _request(
    source: Path,
    *,
    asset_id: str,
    generation: int,
    level: int = 1024,
    revision: int = 1,
    adjustments: dict | None = None,
    residency_slot: str | None = None,
    window_generation: int = 0,
) -> DetailRenderRequest:
    return DetailRenderRequest(
        generation=generation,
        asset_id=asset_id,
        source_identity=AssetSourceIdentity.create(
            source,
            size_bytes=100,
            source_mtime_ns=revision,
            width=4000,
            height=3000,
        ),
        viewport_physical_size=(800, 600),
        device_pixel_ratio=1.0,
        geometry=DetailGeometryState(),
        reason="prefetch" if generation == 0 else "initial",
        decode_level=level,
        raw_adjustments=adjustments,
        residency_slot=residency_slot,  # type: ignore[arg-type]
        window_generation=window_generation,
    )


def test_queued_prefetch_is_promoted_without_creating_a_second_worker(
    tmp_path: Path,
) -> None:
    scheduler, pool, workers = _harness()
    source = tmp_path / "photo.jpg"

    assert scheduler.prefetch(_request(source, asset_id="asset-1", generation=0))
    assert scheduler.request(_request(source, asset_id="asset-1", generation=7))

    assert len(workers) == 1
    assert [priority for _, priority in pool.starts] == [-1, 1]
    assert pool.starts[0][0] is pool.starts[1][0]


def test_running_prefetch_is_reused_and_decodes_once(tmp_path: Path) -> None:
    scheduler, pool, workers = _harness()
    source = tmp_path / "photo.heic"
    presented: list[tuple[int, Path]] = []
    scheduler.ready.connect(
        lambda generation, surface, *_args: presented.append(
            (generation, surface.decode_key.source)
        )
    )

    assert scheduler.prefetch(_request(source, asset_id="asset-1", generation=0))
    pool.mark_running(workers[0])
    assert scheduler.request(_request(source, asset_id="asset-1", generation=3))
    pool.complete(workers[0])

    assert len(workers) == 1
    assert presented == [(3, source)]


def test_reused_surface_worker_adopts_latest_adjustments(tmp_path: Path) -> None:
    scheduler, pool, workers = _harness()
    source = tmp_path / "photo.jpg"

    assert scheduler.prefetch(
        _request(
            source,
            asset_id="asset-1",
            generation=0,
            adjustments={"Crop_W": 1.0},
        )
    )
    pool.mark_running(workers[0])
    assert scheduler.request(
        _request(
            source,
            asset_id="asset-1",
            generation=3,
            adjustments={"Crop_W": 0.75},
        )
    )

    assert len(workers) == 1
    assert dict(workers[0].request.raw_adjustments or {}) == {"Crop_W": 0.75}


def test_new_asset_bypasses_running_stale_decoder_and_stale_never_presents(
    tmp_path: Path,
) -> None:
    scheduler, pool, workers = _harness()
    source_a = tmp_path / "a.raw"
    source_b = tmp_path / "b.jpg"
    presented: list[tuple[int, Path]] = []
    scheduler.ready.connect(
        lambda generation, surface, *_args: presented.append(
            (generation, surface.decode_key.source)
        )
    )

    assert scheduler.request(_request(source_a, asset_id="A", generation=1))
    worker_a = workers[0]
    pool.mark_running(worker_a)
    assert scheduler.request(_request(source_b, asset_id="B", generation=2))
    worker_b = workers[1]
    pool.mark_running(worker_b)

    # B has started on the second lane before uninterruptible A completes.
    assert len(pool.starts) == 2
    assert worker_a.cancelled
    pool.complete(worker_a)
    pool.complete(worker_b)

    assert presented == [(2, source_b)]


def test_real_two_lane_pool_cancels_stale_workers_and_starts_latest_next(
    qapp,
    tmp_path: Path,
) -> None:
    pool = QThreadPool()
    pool.setMaxThreadCount(2)
    workers: dict[str, _BlockingWorker] = {}
    releases: dict[str, Event] = {}

    def factory(request: DetailRenderRequest) -> _BlockingWorker:
        release = Event()
        worker = _BlockingWorker(request, release)
        workers[request.asset_id] = worker
        releases[request.asset_id] = release
        return worker

    scheduler = DetailStillRequestScheduler(pool=pool, worker_factory=factory)
    presented: list[tuple[int, Path]] = []
    scheduler.ready.connect(
        lambda generation, surface: presented.append(
            (generation, surface.decode_key.source)
        )
    )
    source_a = tmp_path / "a.raw"
    source_b = tmp_path / "b.raw"
    source_c = tmp_path / "c.jpg"

    try:
        assert scheduler.request(_request(source_a, asset_id="A", generation=1))
        assert workers["A"].started.wait(5)
        assert _spin_until(
            qapp,
            lambda: scheduler._inflight_by_key[
                DetailDecodeKey.from_request(
                    _request(source_a, asset_id="A", generation=1)
                )
            ].state
            == "running",
        )

        assert scheduler.request(_request(source_b, asset_id="B", generation=2))
        assert workers["B"].started.wait(5)
        assert _spin_until(
            qapp,
            lambda: scheduler._inflight_by_key[
                DetailDecodeKey.from_request(
                    _request(source_b, asset_id="B", generation=2)
                )
            ].state
            == "running",
        )

        assert scheduler.request(_request(source_c, asset_id="C", generation=3))

        assert workers["A"].cancelled.is_set()
        assert workers["B"].cancelled.is_set()
        assert not workers["C"].started.is_set()

        releases["B"].set()
        assert workers["C"].started.wait(5)
        assert not releases["A"].is_set()

        releases["C"].set()
        assert _spin_until(qapp, lambda: presented == [(3, source_c)])
    finally:
        for release in releases.values():
            release.set()
        assert pool.waitForDone(5000)
        qapp.processEvents()
        scheduler.shutdown(timeout_ms=1000)

    assert presented == [(3, source_c)]


def test_repeated_click_updates_generation_without_parallel_same_key_decoder(
    tmp_path: Path,
) -> None:
    scheduler, pool, workers = _harness()
    source = tmp_path / "photo.png"
    presented: list[int] = []
    scheduler.ready.connect(
        lambda generation, *_args: presented.append(generation)
    )

    assert scheduler.request(_request(source, asset_id="asset-1", generation=1))
    pool.mark_running(workers[0])
    assert scheduler.request(_request(source, asset_id="asset-1", generation=2))
    pool.complete(workers[0])

    assert len(workers) == 1
    assert presented == [2]


def test_cancelled_running_key_is_not_reused_after_a_b_a_navigation(
    tmp_path: Path,
) -> None:
    scheduler, pool, workers = _harness()
    source_a = tmp_path / "a.raw"
    source_b = tmp_path / "b.raw"

    assert scheduler.request(_request(source_a, asset_id="A", generation=1))
    first_a = workers[0]
    pool.mark_running(first_a)

    assert scheduler.request(_request(source_b, asset_id="B", generation=2))
    worker_b = workers[1]
    pool.mark_running(worker_b)
    assert first_a.cancelled

    assert scheduler.request(_request(source_a, asset_id="A", generation=3))

    assert worker_b.cancelled
    assert len(workers) == 3
    assert workers[2] is not first_a
    assert workers[2].request.generation == 3


def test_different_decode_levels_do_not_reuse_the_same_worker(tmp_path: Path) -> None:
    scheduler, pool, workers = _harness()
    source = tmp_path / "photo.jpg"
    assert scheduler.request(
        _request(source, asset_id="asset-1", generation=1, level=1024)
    )
    pool.mark_running(workers[0])

    assert scheduler.request(
        _request(source, asset_id="asset-1", generation=2, level=2048)
    )

    assert len(workers) == 2
    assert workers[0].request.decode_level == 1024
    assert workers[1].request.decode_level == 2048


def test_neighbor_window_runs_one_speculative_decoder_at_a_time_and_warms_both(
    tmp_path: Path,
) -> None:
    scheduler, pool, workers = _harness()
    warmed: list[tuple[str | None, Path]] = []
    scheduler.warmed.connect(
        lambda request, surface: warmed.append(
            (request.residency_slot, surface.decode_key.source)
        )
    )
    previous = _request(
        tmp_path / "previous.jpg",
        asset_id="previous",
        generation=0,
        residency_slot="previous",
        window_generation=4,
    )
    following = _request(
        tmp_path / "next.jpg",
        asset_id="next",
        generation=0,
        residency_slot="next",
        window_generation=4,
    )

    assert scheduler.prefetch_window(previous, following)
    assert len(workers) == 1
    pool.mark_running(workers[0])
    pool.complete(workers[0])
    assert len(workers) == 2
    pool.mark_running(workers[1])
    pool.complete(workers[1])

    assert warmed == [
        ("previous", previous.source_identity.path),
        ("next", following.source_identity.path),
    ]


def test_old_neighbor_window_result_is_not_published(tmp_path: Path) -> None:
    scheduler, pool, workers = _harness()
    warmed: list[Path] = []
    scheduler.warmed.connect(
        lambda _request, surface: warmed.append(surface.decode_key.source)
    )
    old = _request(
        tmp_path / "old.jpg",
        asset_id="old",
        generation=0,
        residency_slot="previous",
        window_generation=1,
    )
    new = _request(
        tmp_path / "new.jpg",
        asset_id="new",
        generation=0,
        residency_slot="next",
        window_generation=2,
    )
    assert scheduler.prefetch(old)
    pool.mark_running(workers[0])
    assert scheduler.prefetch(new)
    pool.complete(workers[0])

    assert warmed == []


def test_shutdown_cancels_and_releases_queued_workers(tmp_path: Path) -> None:
    scheduler, pool, workers = _harness()
    source = tmp_path / "photo.jpg"
    assert scheduler.request(_request(source, asset_id="asset-1", generation=1))

    scheduler.shutdown(timeout_ms=25)

    assert workers[0].cancelled
    assert not workers[0].auto_delete
    assert scheduler.inflight_count == 0
    assert pool.cleared
    assert pool.wait_timeout == 25


def test_shutdown_retains_worker_when_pool_wait_times_out(tmp_path: Path) -> None:
    scheduler, pool, workers = _harness()
    source = tmp_path / "slow.raw"
    assert scheduler.request(_request(source, asset_id="asset-1", generation=1))
    worker = workers[0]
    pool.mark_running(worker)
    pool.wait_result = False

    scheduler.shutdown(timeout_ms=25)

    assert worker.cancelled
    assert not worker.auto_delete
    assert scheduler.inflight_count == 1

    pool.complete(worker)

    assert not worker.auto_delete
    assert scheduler.inflight_count == 0

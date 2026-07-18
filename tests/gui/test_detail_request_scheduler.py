from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage

from iPhoto.gui.detail_request_scheduler import DetailStillRequestScheduler


class _WorkerSignals(QObject):
    started = Signal(object)
    completed = Signal(Path, QImage, dict)
    failed = Signal(Path, str)
    finished = Signal(object)


class _FakeWorker:
    def __init__(self, source: Path) -> None:
        self.source = source
        self.signals = _WorkerSignals()
        self.frame_identity = None
        self.cache_hit = False
        self.cancelled = False
        self.auto_delete = False

    def cancel(self) -> None:
        self.cancelled = True

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
        worker.signals.completed.emit(worker.source, image, {})
        worker.signals.finished.emit(worker)

    def clear(self) -> None:
        self.cleared = True
        self.queued.clear()

    def waitForDone(self, timeout_ms: int) -> bool:
        self.wait_timeout = timeout_ms
        return self.wait_result


def _harness() -> tuple[
    DetailStillRequestScheduler,
    _FakePool,
    list[_FakeWorker],
]:
    pool = _FakePool()
    workers: list[_FakeWorker] = []

    def factory(source: Path) -> _FakeWorker:
        worker = _FakeWorker(source)
        workers.append(worker)
        return worker

    scheduler = DetailStillRequestScheduler(pool=pool, worker_factory=factory)
    return scheduler, pool, workers


def test_queued_prefetch_is_promoted_without_creating_a_second_worker(
    tmp_path: Path,
) -> None:
    scheduler, pool, workers = _harness()
    source = tmp_path / "photo.jpg"

    assert scheduler.prefetch(asset_id="asset-1", source=source)
    assert scheduler.request(asset_id="asset-1", source=source, generation=7)

    assert len(workers) == 1
    assert [priority for _, priority in pool.starts] == [-1, 1]
    assert pool.starts[0][0] is pool.starts[1][0]


def test_running_prefetch_is_reused_and_decodes_once(tmp_path: Path) -> None:
    scheduler, pool, workers = _harness()
    source = tmp_path / "photo.heic"
    presented: list[tuple[int, Path]] = []
    scheduler.ready.connect(
        lambda generation, path, *_args: presented.append((generation, path))
    )

    assert scheduler.prefetch(asset_id="asset-1", source=source)
    pool.mark_running(workers[0])
    assert scheduler.request(asset_id="asset-1", source=source, generation=3)
    pool.complete(workers[0])

    assert len(workers) == 1
    assert presented == [(3, source)]


def test_new_asset_bypasses_running_stale_decoder_and_stale_never_presents(
    tmp_path: Path,
) -> None:
    scheduler, pool, workers = _harness()
    source_a = tmp_path / "a.raw"
    source_b = tmp_path / "b.jpg"
    presented: list[tuple[int, Path]] = []
    scheduler.ready.connect(
        lambda generation, path, *_args: presented.append((generation, path))
    )

    assert scheduler.request(asset_id="A", source=source_a, generation=1)
    worker_a = workers[0]
    pool.mark_running(worker_a)
    assert scheduler.request(asset_id="B", source=source_b, generation=2)
    worker_b = workers[1]
    pool.mark_running(worker_b)

    # B has started on the second lane before uninterruptible A completes.
    assert len(pool.starts) == 2
    pool.complete(worker_a)
    pool.complete(worker_b)

    assert presented == [(2, source_b)]


def test_repeated_click_updates_generation_without_parallel_same_key_decoder(
    tmp_path: Path,
) -> None:
    scheduler, pool, workers = _harness()
    source = tmp_path / "photo.png"
    presented: list[int] = []
    scheduler.ready.connect(
        lambda generation, *_args: presented.append(generation)
    )

    assert scheduler.request(asset_id="asset-1", source=source, generation=1)
    pool.mark_running(workers[0])
    assert scheduler.request(asset_id="asset-1", source=source, generation=2)
    pool.complete(workers[0])

    assert len(workers) == 1
    assert presented == [2]


def test_shutdown_cancels_and_releases_queued_workers(tmp_path: Path) -> None:
    scheduler, pool, workers = _harness()
    source = tmp_path / "photo.jpg"
    assert scheduler.request(asset_id="asset-1", source=source, generation=1)

    scheduler.shutdown(timeout_ms=25)

    assert workers[0].cancelled
    assert workers[0].auto_delete
    assert scheduler.inflight_count == 0
    assert pool.cleared
    assert pool.wait_timeout == 25


def test_shutdown_retains_worker_when_pool_wait_times_out(tmp_path: Path) -> None:
    scheduler, pool, workers = _harness()
    source = tmp_path / "slow.raw"
    assert scheduler.request(asset_id="asset-1", source=source, generation=1)
    worker = workers[0]
    pool.mark_running(worker)
    pool.wait_result = False

    scheduler.shutdown(timeout_ms=25)

    assert worker.cancelled
    assert not worker.auto_delete
    assert scheduler.inflight_count == 1

    pool.complete(worker)

    assert worker.auto_delete
    assert scheduler.inflight_count == 0

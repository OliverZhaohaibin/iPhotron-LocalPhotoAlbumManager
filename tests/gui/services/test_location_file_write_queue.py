from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6", exc_type=ImportError)

from PySide6.QtCore import QCoreApplication

from iPhoto.application.ports import LocationWriteJobRecord
from iPhoto.events.asset_events import LocationFileWriteFailed, LocationFileWriteVerified
from iPhoto.events.bus import EventBus
from iPhoto.gui.services.location_file_write_queue import LocationFileWriteQueue


class _FakeRepository:
    def __init__(self) -> None:
        self.writing: list[str] = []
        self.verified: list[str] = []
        self.failed: list[tuple[str, str]] = []
        self.queued: list[tuple[str, str | None]] = []
        self.failed_jobs: list[LocationWriteJobRecord] = []
        self.recoverable_jobs: list[LocationWriteJobRecord] = []

    def mark_writing(self, job_id: str) -> None:
        self.writing.append(job_id)

    def mark_verified(self, job_id: str) -> None:
        self.verified.append(job_id)

    def mark_failed(self, job_id: str, error: str) -> None:
        self.failed.append((job_id, error))

    def mark_queued(self, job_id: str, *, last_error: str | None = None) -> None:
        self.queued.append((job_id, last_error))

    def list_recoverable_jobs(self) -> list[LocationWriteJobRecord]:
        return list(self.recoverable_jobs)

    def list_failed_jobs(self, *, asset_rel: str | None = None) -> list[LocationWriteJobRecord]:
        if asset_rel is None:
            return list(self.failed_jobs)
        return [job for job in self.failed_jobs if job.asset_rel == asset_rel]


class _FakeWriter:
    def __init__(self, *, fail: bool = False, verified: bool = False) -> None:
        self.fail = fail
        self.verified = verified
        self.write_calls = 0

    def write_location(self, path: Path, **kwargs) -> dict:
        del path, kwargs
        self.write_calls += 1
        if self.fail:
            raise RuntimeError("write failed")
        return {"gps": {"lat": 48.137154, "lon": 11.576124}}

    def verify_location(self, path: Path, **kwargs) -> dict | None:
        del path, kwargs
        if self.verified:
            return {"gps": {"lat": 48.137154, "lon": 11.576124}}
        return None


@pytest.fixture()
def qcore_app() -> QCoreApplication:
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    return app


def _job(tmp_path: Path) -> LocationWriteJobRecord:
    return LocationWriteJobRecord(
        job_id="job-1",
        asset_rel="photo.jpg",
        asset_path=tmp_path / "photo.jpg",
        gps={"lat": 48.137154, "lon": 11.576124},
        location="Munich",
        media_kind="image",
        status="queued",
    )


def test_location_file_write_queue_marks_verified_and_publishes_event(
    qcore_app: QCoreApplication,
    tmp_path: Path,
) -> None:
    del qcore_app
    event_bus = EventBus()
    verified_events: list[LocationFileWriteVerified] = []
    event_bus.subscribe(LocationFileWriteVerified, verified_events.append)
    queue = LocationFileWriteQueue(event_bus=event_bus, writer=_FakeWriter())
    repository = _FakeRepository()
    queue._repository = repository  # test seam for avoiding a real library DB

    queue.enqueue(_job(tmp_path))

    assert queue.drain(timeout=5.0) is True
    assert repository.writing == ["job-1"]
    assert repository.verified == ["job-1"]
    assert verified_events and verified_events[0].job_id == "job-1"
    queue.shutdown(wait=True)
    event_bus.shutdown()


def test_location_file_write_queue_marks_failed_and_publishes_event(
    qcore_app: QCoreApplication,
    tmp_path: Path,
) -> None:
    del qcore_app
    event_bus = EventBus()
    failed_events: list[LocationFileWriteFailed] = []
    event_bus.subscribe(LocationFileWriteFailed, failed_events.append)
    queue = LocationFileWriteQueue(event_bus=event_bus, writer=_FakeWriter(fail=True))
    repository = _FakeRepository()
    queue._repository = repository

    queue.enqueue(_job(tmp_path))

    assert queue.drain(timeout=5.0) is True
    assert repository.failed == [("job-1", "write failed")]
    assert failed_events and failed_events[0].error == "write failed"
    queue.shutdown(wait=True)
    event_bus.shutdown()


def test_location_file_write_queue_allows_retry_after_failure(
    qcore_app: QCoreApplication,
    tmp_path: Path,
) -> None:
    del qcore_app
    event_bus = EventBus()
    writer = _FakeWriter(fail=True)
    queue = LocationFileWriteQueue(event_bus=event_bus, writer=writer)
    repository = _FakeRepository()
    job = _job(tmp_path)
    repository.failed_jobs = [job]
    queue._repository = repository

    queue.enqueue(job)
    assert queue.drain(timeout=5.0) is True

    writer.fail = False
    retried = queue.retry_failed_jobs()

    assert retried == 1
    assert queue.drain(timeout=5.0) is True
    assert repository.queued == [("job-1", None)]
    assert repository.verified == ["job-1"]
    queue.shutdown(wait=True)
    event_bus.shutdown()


def test_location_file_write_queue_verifies_recovered_writing_job_without_rewrite(
    qcore_app: QCoreApplication,
    tmp_path: Path,
) -> None:
    del qcore_app
    event_bus = EventBus()
    verified_events: list[LocationFileWriteVerified] = []
    event_bus.subscribe(LocationFileWriteVerified, verified_events.append)
    writer = _FakeWriter(verified=True)
    queue = LocationFileWriteQueue(event_bus=event_bus, writer=writer)
    repository = _FakeRepository()
    recovered = _job(tmp_path)
    recovered = LocationWriteJobRecord(
        job_id=recovered.job_id,
        asset_rel=recovered.asset_rel,
        asset_path=recovered.asset_path,
        gps=recovered.gps,
        location=recovered.location,
        media_kind=recovered.media_kind,
        status="writing",
    )
    repository.recoverable_jobs = [recovered]
    queue._repository = repository

    queue.recover_pending_jobs()

    assert queue.drain(timeout=5.0) is True
    assert repository.verified == ["job-1"]
    assert writer.write_calls == 0
    assert verified_events and verified_events[0].job_id == "job-1"
    queue.shutdown(wait=True)
    event_bus.shutdown()

"""Dedicated queue for original-file GPS write-back jobs."""

from __future__ import annotations

import concurrent.futures
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from iPhoto.application.ports import LocationWriteJobRecord
from iPhoto.events.asset_events import (
    LocationFileWriteFailed,
    LocationFileWriteVerified,
)
from iPhoto.events.bus import EventBus
from iPhoto.infrastructure.repositories.metadata_write_job_repository import (
    MetadataWriteJobRepository,
)
from iPhoto.infrastructure.services.exiftool_metadata_writer import ExifToolMetadataWriter


@dataclass(frozen=True)
class LocationFileWriteResult:
    job_id: str
    asset_path: Path
    gps: dict[str, float]
    location: str
    metadata: dict[str, Any] | None = None
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None


class LocationFileWriteQueue(QObject):
    """Serialize metadata writes and make shutdown waitable."""

    writeStarted = Signal(object)
    writeVerified = Signal(object)
    writeFailed = Signal(object)

    def __init__(
        self,
        *,
        event_bus: EventBus | None = None,
        parent: QObject | None = None,
        writer: ExifToolMetadataWriter | None = None,
    ) -> None:
        super().__init__(parent)
        self._event_bus = event_bus
        self._writer = writer or ExifToolMetadataWriter()
        self._repository: MetadataWriteJobRepository | None = None
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="iPhotoLocationWrite",
        )
        self._lock = threading.RLock()
        self._futures: set[concurrent.futures.Future] = set()
        self._submitted_job_ids: set[str] = set()
        self._shutdown = False

    def bind_library_root(self, library_root: Path | None) -> None:
        with self._lock:
            self._repository = (
                MetadataWriteJobRepository(Path(library_root))
                if library_root is not None
                else None
            )
        if library_root is not None:
            self.recover_pending_jobs()

    def enqueue(self, job: LocationWriteJobRecord) -> None:
        with self._lock:
            if self._shutdown:
                raise RuntimeError("Location file write queue has been shut down")
            if job.job_id in self._submitted_job_ids:
                return
            repository = self._repository
            if repository is None:
                raise RuntimeError("Location file write queue is not bound to a library")
            self._submitted_job_ids.add(job.job_id)
            future = self._executor.submit(self._run_job, repository, job)
            self._futures.add(future)
            future.add_done_callback(
                lambda completed, job_id=job.job_id: self._discard_future(job_id, completed)
            )

    def recover_pending_jobs(self) -> None:
        repository = self._repository
        if repository is None:
            return
        for job in repository.list_recoverable_jobs():
            self.enqueue(job)

    def retry_failed_jobs(self, *, asset_rel: str | None = None) -> int:
        repository = self._repository
        if repository is None:
            return 0
        jobs = repository.list_failed_jobs(asset_rel=asset_rel)
        for job in jobs:
            repository.mark_queued(job.job_id, last_error=job.last_error)
            self.enqueue(job)
        return len(jobs)

    def is_busy(self) -> bool:
        with self._lock:
            return any(not future.done() for future in self._futures)

    def drain(self, timeout: float | None = None) -> bool:
        """Wait until all submitted write jobs have completed."""

        while True:
            with self._lock:
                futures = [future for future in self._futures if not future.done()]
            if not futures:
                return True
            done, not_done = concurrent.futures.wait(futures, timeout=timeout)
            if not_done:
                return False
            if not done:
                return True

    def shutdown(self, *, wait: bool = True) -> None:
        with self._lock:
            self._shutdown = True
        if wait:
            self.drain(timeout=None)
        self._executor.shutdown(wait=wait, cancel_futures=not wait)

    def _run_job(
        self,
        repository: MetadataWriteJobRepository,
        job: LocationWriteJobRecord,
    ) -> LocationFileWriteResult:
        is_superseded = getattr(repository, "is_superseded", None)
        if callable(is_superseded) and is_superseded(job.job_id):
            return LocationFileWriteResult(
                job_id=job.job_id,
                asset_path=job.asset_path,
                gps=dict(job.gps),
                location=job.location,
                error="superseded",
            )
        if job.status == "writing":
            recovered = self._verify_interrupted_job(repository, job)
            if recovered is not None:
                return recovered
            repository.mark_queued(
                job.job_id,
                last_error="Recovered interrupted metadata write",
            )
        repository.mark_writing(job.job_id)
        self.writeStarted.emit(job)
        try:
            metadata = self._writer.write_location(
                job.asset_path,
                latitude=float(job.gps["lat"]),
                longitude=float(job.gps["lon"]),
                is_video=job.is_video,
            )
        except Exception as exc:  # noqa: BLE001 - convert write failures to durable job state
            message = str(exc)
            repository.mark_failed(job.job_id, message)
            result = LocationFileWriteResult(
                job_id=job.job_id,
                asset_path=job.asset_path,
                gps=dict(job.gps),
                location=job.location,
                error=message,
            )
            if self._event_bus is not None:
                self._event_bus.publish(
                    LocationFileWriteFailed(
                        asset_path=job.asset_path,
                        gps=dict(job.gps),
                        location=job.location,
                        job_id=job.job_id,
                        error=message,
                        recoverable=True,
                    )
                )
            self.writeFailed.emit(result)
            return result

        repository.mark_verified(job.job_id)
        result = LocationFileWriteResult(
            job_id=job.job_id,
            asset_path=job.asset_path,
            gps=dict(job.gps),
            location=job.location,
            metadata=metadata,
        )
        if self._event_bus is not None:
            self._event_bus.publish(
                LocationFileWriteVerified(
                    asset_path=job.asset_path,
                    gps=dict(job.gps),
                    location=job.location,
                    job_id=job.job_id,
                )
            )
        self.writeVerified.emit(result)
        return result

    def _verify_interrupted_job(
        self,
        repository: MetadataWriteJobRepository,
        job: LocationWriteJobRecord,
    ) -> LocationFileWriteResult | None:
        try:
            metadata = self._writer.verify_location(
                job.asset_path,
                latitude=float(job.gps["lat"]),
                longitude=float(job.gps["lon"]),
                is_video=job.is_video,
            )
        except Exception:  # noqa: BLE001 - failed verification falls back to retry
            metadata = None
        if metadata is None:
            return None
        repository.mark_verified(job.job_id)
        result = LocationFileWriteResult(
            job_id=job.job_id,
            asset_path=job.asset_path,
            gps=dict(job.gps),
            location=job.location,
            metadata=metadata,
        )
        if self._event_bus is not None:
            self._event_bus.publish(
                LocationFileWriteVerified(
                    asset_path=job.asset_path,
                    gps=dict(job.gps),
                    location=job.location,
                    job_id=job.job_id,
                )
            )
        self.writeVerified.emit(result)
        return result

    def _discard_future(self, job_id: str, future: concurrent.futures.Future) -> None:
        with self._lock:
            self._futures.discard(future)
            self._submitted_job_ids.discard(job_id)


__all__ = ["LocationFileWriteQueue", "LocationFileWriteResult"]

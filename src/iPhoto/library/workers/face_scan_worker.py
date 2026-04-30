"""Background worker that performs low-pressure face scanning."""

from __future__ import annotations

import queue
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import QThread, Signal

from ...people.index_coordinator import (
    PeopleIndexCoordinator,
    PeopleSnapshotCommittedError,
)
from ...people.pipeline import FaceClusterPipeline
from ...people.service import PeopleService, face_library_paths
from ...people.status import (
    FACE_STATUS_FAILED,
    FACE_STATUS_PENDING,
    FACE_STATUS_RETRY,
    is_face_scan_candidate,
    normalize_face_status,
)
from ...utils.logging import get_logger

LOGGER = get_logger()


class FaceScanWorker(QThread):
    """Consume pending People assets from the session service."""

    peopleIndexUpdated = Signal()
    statusChanged = Signal(str)

    BATCH_SIZE = 4
    QUEUE_TARGET_SIZE = 16

    def __init__(
        self,
        library_root: Path,
        parent=None,
        *,
        people_service: PeopleService | None = None,
    ) -> None:
        super().__init__(parent)
        self._library_root = Path(library_root)
        if people_service is None:
            from ...bootstrap.library_people_service import create_people_service

            people_service = create_people_service(self._library_root)
        self._people_service = people_service
        self._queue: queue.Queue[dict] = queue.Queue()
        self._queued_ids: set[str] = set()
        self._input_closed = False
        self._cancelled = False

    def enqueue_rows(self, rows: Iterable[dict]) -> None:
        for row in rows:
            asset_id = str(row.get("id") or "")
            status = normalize_face_status(row.get("face_status"))
            if not asset_id or asset_id in self._queued_ids:
                continue
            if status not in {None, FACE_STATUS_RETRY, FACE_STATUS_PENDING}:
                continue
            if not is_face_scan_candidate(row):
                continue
            self._queued_ids.add(asset_id)
            self._queue.put(dict(row))

    def finish_input(self) -> None:
        self._input_closed = True

    def cancel(self) -> None:
        self._cancelled = True
        self._input_closed = True

    def run(self) -> None:  # type: ignore[override]
        self._prime_pending_rows()
        if self._cancelled:
            return

        paths = face_library_paths(self._library_root)
        pipeline = FaceClusterPipeline(model_root=paths.model_dir)
        coordinator = self._people_service.coordinator
        if coordinator is None:
            self.statusChanged.emit("Face scanning is unavailable for this library.")
            return

        while not self._cancelled:
            self._top_up_pending_rows()
            batch = self._next_batch()
            if not batch:
                if self._input_closed:
                    self._top_up_pending_rows()
                    if self._queue.empty():
                        return
                continue

            try:
                committed = self._process_batch(
                    batch,
                    coordinator,
                    pipeline,
                    paths.thumbnail_dir,
                )
                for asset_id in [str(row.get("id") or "") for row in batch if row.get("id")]:
                    self._queued_ids.discard(asset_id)
                if committed:
                    self.peopleIndexUpdated.emit()
            except PeopleSnapshotCommittedError as exc:
                LOGGER.error("Face scan bookkeeping failed after commit: %s", exc, exc_info=True)
                for asset_id in [str(row.get("id") or "") for row in batch if row.get("id")]:
                    self._queued_ids.discard(asset_id)
                self.statusChanged.emit(str(exc))
                return
            except RuntimeError as exc:
                self._mark_remaining_failed(batch)
                self.statusChanged.emit(str(exc))
                return
            except Exception as exc:  # pragma: no cover - defensive runtime guard
                LOGGER.warning("Face scan batch failed: %s", exc, exc_info=True)
                # The batch is retried so the assets remain pending/retry in the
                # store and will be re-detected on the next scan.  We do NOT
                # extend pending_done_ids here because we cannot guarantee
                # session.commit() will succeed for partially staged results.
                self._mark_rows_retry(batch)
                reason = str(exc).strip() or exc.__class__.__name__
                self.statusChanged.emit(f"Face scanning paused: {reason}")
                if self._input_closed:
                    return

    def _prime_pending_rows(self) -> None:
        self._top_up_pending_rows()

    def _top_up_pending_rows(self) -> None:
        store = self._people_service.asset_repository
        if store is None:
            return
        attempts = 0
        while self._queue.qsize() < self.QUEUE_TARGET_SIZE and attempts < 3 and not self._cancelled:
            queue_size_before = self._queue.qsize()
            deficit = max(self.QUEUE_TARGET_SIZE - queue_size_before, self.BATCH_SIZE)
            self.enqueue_rows(
                store.read_rows_by_face_status(
                    [FACE_STATUS_PENDING, FACE_STATUS_RETRY],
                    limit=max(deficit * 4, self.BATCH_SIZE),
                )
            )
            attempts += 1
            if self._queue.qsize() == queue_size_before:
                break

    def _next_batch(self) -> list[dict]:
        try:
            first = self._queue.get(timeout=0.25)
        except queue.Empty:
            return []

        batch = [first]
        while len(batch) < self.BATCH_SIZE:
            try:
                batch.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return batch

    def _process_batch(
        self,
        batch: list[dict],
        coordinator: PeopleIndexCoordinator,
        pipeline: FaceClusterPipeline,
        thumbnail_dir: Path,
    ) -> bool:
        """Detect faces for *batch* and commit a realtime People snapshot."""
        if self._cancelled:
            self._mark_rows_retry(batch)
            return False
        detected = list(
            pipeline.detect_faces_for_rows(
                batch,
                library_root=self._library_root,
                thumbnail_dir=thumbnail_dir,
                is_cancelled=lambda: self._cancelled,
            )
        )

        if self._cancelled:
            self._mark_rows_retry(batch)
            return False

        retry_items = [item for item in detected if item.asset_id and item.error]
        for item in retry_items:
            LOGGER.warning(
                "Face scan failed for asset %s (%s): %s",
                item.asset_id,
                item.asset_rel,
                item.error,
            )
        retry_id_set = {str(item.asset_id) for item in retry_items}
        retry_source_ids = {
            str(row.get("id") or "")
            for row in batch
            if str(row.get("id") or "") in retry_id_set
            and normalize_face_status(row.get("face_status")) == FACE_STATUS_RETRY
        }
        first_retry_ids = [asset_id for asset_id in retry_id_set if asset_id not in retry_source_ids]
        failed_ids = [asset_id for asset_id in retry_id_set if asset_id in retry_source_ids]

        if first_retry_ids:
            self.statusChanged.emit("Some assets need a face-scan retry.")
        if failed_ids:
            self._update_face_statuses(
                failed_ids,
                FACE_STATUS_FAILED,
            )
            self.statusChanged.emit(
                "Some assets could not be face scanned and will be retried after a rescan."
            )
        retry_detected = [
            item
            for item in detected
            if not item.asset_id or str(item.asset_id) not in failed_ids
        ]

        event = coordinator.submit_detected_batch(
            retry_detected,
            distance_threshold=pipeline.distance_threshold,
            min_samples=pipeline.min_samples,
        )
        return event is not None

    def _mark_rows_retry(self, rows: Iterable[dict]) -> None:
        ids = [str(row.get("id") or "") for row in rows if row.get("id")]
        self._update_face_statuses(ids, FACE_STATUS_RETRY)
        for asset_id in ids:
            self._queued_ids.discard(asset_id)

    def _mark_remaining_retry(self, initial_rows: Iterable[dict]) -> None:
        self._mark_rows_retry(initial_rows)
        remaining = list(
            self._read_rows_by_face_status([FACE_STATUS_PENDING, FACE_STATUS_RETRY])
        )
        self._mark_rows_retry(remaining)

    def _mark_rows_failed(self, rows: Iterable[dict]) -> None:
        ids = [str(row.get("id") or "") for row in rows if row.get("id")]
        self._update_face_statuses(ids, FACE_STATUS_FAILED)
        for asset_id in ids:
            self._queued_ids.discard(asset_id)

    def _mark_remaining_failed(self, initial_rows: Iterable[dict]) -> None:
        self._mark_rows_failed(initial_rows)
        remaining = list(self._read_rows_by_face_status([FACE_STATUS_PENDING, FACE_STATUS_RETRY]))
        self._mark_rows_failed(remaining)

    def _read_rows_by_face_status(
        self,
        statuses: Iterable[str],
        *,
        limit: int | None = None,
    ) -> Iterable[dict]:
        store = self._people_service.asset_repository
        if store is None:
            return ()
        return store.read_rows_by_face_status(statuses, limit=limit)

    def _update_face_statuses(self, asset_ids: Iterable[str], status: str) -> None:
        store = self._people_service.asset_repository
        if store is None:
            return
        store.update_face_statuses(asset_ids, status)

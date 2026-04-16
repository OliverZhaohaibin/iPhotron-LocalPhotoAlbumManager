"""Background worker that performs low-pressure face scanning."""

from __future__ import annotations

import queue
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import QThread, Signal

from ...cache.index_store import get_global_repository
from ...people.pipeline import FaceClusterPipeline
from ...people.repository import FaceRepository
from ...people.scan_session import FaceScanSession
from ...people.service import face_library_paths
from ...people.status import (
    FACE_STATUS_DONE,
    FACE_STATUS_FAILED,
    FACE_STATUS_PENDING,
    FACE_STATUS_RETRY,
    is_face_scan_candidate,
    normalize_face_status,
)
from ...utils.logging import get_logger

LOGGER = get_logger()


class FaceScanWorker(QThread):
    """Consume pending assets from the global index and build People clusters."""

    peopleIndexUpdated = Signal()
    statusChanged = Signal(str)

    BATCH_SIZE = 4
    QUEUE_TARGET_SIZE = 16

    def __init__(self, library_root: Path, parent=None) -> None:
        super().__init__(parent)
        self._library_root = Path(library_root)
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
        repository = FaceRepository(paths.index_db_path, paths.state_db_path)
        pipeline = FaceClusterPipeline(model_root=paths.model_dir)
        session = FaceScanSession()

        while not self._cancelled:
            self._top_up_pending_rows()
            batch = self._next_batch()
            if not batch:
                if self._input_closed:
                    self._top_up_pending_rows()
                    if self._queue.empty():
                        if session.commit(
                            repository,
                            distance_threshold=pipeline.distance_threshold,
                            min_samples=pipeline.min_samples,
                        ):
                            self.peopleIndexUpdated.emit()
                        return
                continue

            try:
                self._process_batch(
                    batch,
                    pipeline,
                    session,
                    paths.thumbnail_dir,
                )
            except RuntimeError as exc:
                self._mark_remaining_failed(batch)
                self.statusChanged.emit(str(exc))
                return
            except Exception as exc:  # pragma: no cover - defensive runtime guard
                LOGGER.warning("Face scan batch failed: %s", exc, exc_info=True)
                self._mark_rows_retry(batch)
                self.statusChanged.emit("Face scanning paused due to an unexpected error.")
                if self._input_closed:
                    return

    def _prime_pending_rows(self) -> None:
        self._top_up_pending_rows()

    def _top_up_pending_rows(self) -> None:
        store = get_global_repository(self._library_root)
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
        pipeline: FaceClusterPipeline,
        session: FaceScanSession,
        thumbnail_dir: Path,
    ) -> None:
        detected = pipeline.detect_faces_for_rows(
            batch,
            library_root=self._library_root,
            thumbnail_dir=thumbnail_dir,
        )

        for item in detected:
            if item.asset_id:
                self._queued_ids.discard(item.asset_id)

        done_ids, retry_ids = session.stage_detection_results(detected)

        store = get_global_repository(self._library_root)
        store.update_face_statuses(done_ids, FACE_STATUS_DONE)
        store.update_face_statuses(retry_ids, FACE_STATUS_RETRY)
        if retry_ids:
            self.statusChanged.emit("Some assets need a face-scan retry.")

    def _mark_rows_retry(self, rows: Iterable[dict]) -> None:
        ids = [str(row.get("id") or "") for row in rows if row.get("id")]
        get_global_repository(self._library_root).update_face_statuses(ids, FACE_STATUS_RETRY)
        for asset_id in ids:
            self._queued_ids.discard(asset_id)

    def _mark_remaining_retry(self, initial_rows: Iterable[dict]) -> None:
        self._mark_rows_retry(initial_rows)
        remaining = list(get_global_repository(self._library_root).read_rows_by_face_status(["pending", "retry"]))
        self._mark_rows_retry(remaining)

    def _mark_rows_failed(self, rows: Iterable[dict]) -> None:
        ids = [str(row.get("id") or "") for row in rows if row.get("id")]
        get_global_repository(self._library_root).update_face_statuses(ids, FACE_STATUS_FAILED)
        for asset_id in ids:
            self._queued_ids.discard(asset_id)

    def _mark_remaining_failed(self, initial_rows: Iterable[dict]) -> None:
        self._mark_rows_failed(initial_rows)
        remaining = list(
            get_global_repository(self._library_root).read_rows_by_face_status(
                [FACE_STATUS_PENDING, FACE_STATUS_RETRY]
            )
        )
        self._mark_rows_failed(remaining)

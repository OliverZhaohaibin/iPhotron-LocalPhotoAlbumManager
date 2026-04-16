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
        # Accumulate done IDs across batches; only written to the store after a
        # successful session.commit() so that a commit failure cannot leave
        # assets marked "done" without a corresponding People snapshot.
        pending_done_ids: list[str] = []

        while not self._cancelled:
            self._top_up_pending_rows()
            batch = self._next_batch()
            if not batch:
                if self._input_closed:
                    self._top_up_pending_rows()
                    if self._queue.empty():
                        try:
                            committed = session.commit(
                                repository,
                                distance_threshold=pipeline.distance_threshold,
                                min_samples=pipeline.min_samples,
                            )
                        except Exception as exc:
                            LOGGER.warning(
                                "Face scan commit failed: %s", exc, exc_info=True
                            )
                            get_global_repository(self._library_root).update_face_statuses(
                                pending_done_ids, FACE_STATUS_RETRY
                            )
                            for asset_id in pending_done_ids:
                                self._queued_ids.discard(asset_id)
                            pending_done_ids.clear()
                            self.statusChanged.emit(
                                "Face scanning paused due to a commit error."
                            )
                            return
                        store = get_global_repository(self._library_root)
                        store.update_face_statuses(pending_done_ids, FACE_STATUS_DONE)
                        # Retire in-flight IDs now that the DB reflects DONE.
                        for asset_id in pending_done_ids:
                            self._queued_ids.discard(asset_id)
                        pending_done_ids.clear()
                        if committed:
                            self.peopleIndexUpdated.emit()
                        return
                continue

            try:
                batch_done_ids = self._process_batch(
                    batch,
                    pipeline,
                    session,
                    paths.thumbnail_dir,
                )
                pending_done_ids.extend(batch_done_ids)
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
    ) -> list[str]:
        """Detect faces for *batch*, stage results, and return the done asset IDs.

        Ordering is deliberate:
        1. Retry IDs are written to the store *before* done items are staged so
           that a DB failure cannot leave the session with staged faces for assets
           that are still ``pending/retry`` in the index.
        2. Done IDs are **not** removed from ``_queued_ids`` here; they stay in
           the set until ``run()`` writes ``FACE_STATUS_DONE`` after a successful
           ``session.commit()``.  This prevents ``_top_up_pending_rows()`` from
           re-fetching and re-enqueuing those assets while they are still
           ``pending`` in the DB but already processed in-memory.
        """
        detected = list(
            pipeline.detect_faces_for_rows(
                batch,
                library_root=self._library_root,
                thumbnail_dir=thumbnail_dir,
            )
        )

        # Split into done / retry items up-front so we can write retry
        # statuses to the DB before any staging occurs.
        done_items = [item for item in detected if item.asset_id and not item.error]
        retry_items = [item for item in detected if item.asset_id and item.error]
        retry_ids = [str(item.asset_id) for item in retry_items]

        # Write retry statuses first; if this raises, the session is still clean.
        store = get_global_repository(self._library_root)
        store.update_face_statuses(retry_ids, FACE_STATUS_RETRY)

        # Retire retry IDs from the in-flight set immediately.
        for asset_id in retry_ids:
            self._queued_ids.discard(asset_id)

        if retry_ids:
            self.statusChanged.emit("Some assets need a face-scan retry.")

        # Stage done items only after retry DB writes succeed.
        # The second return value contains retry IDs discovered by the session;
        # since we pre-filtered to done_items only, it will always be empty.
        done_ids, _ignored_retry_ids = session.stage_detection_results(done_items)

        return done_ids

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

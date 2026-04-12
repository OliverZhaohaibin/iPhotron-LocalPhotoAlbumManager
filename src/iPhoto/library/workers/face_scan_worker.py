"""Background worker that performs low-pressure face scanning."""

from __future__ import annotations

import queue
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import QThread, Signal

from ...cache.index_store import get_global_repository
from ...people.pipeline import (
    FaceClusterPipeline,
    canonicalize_cluster_identities,
    cluster_face_records,
)
from ...people.repository import FaceRecord, FaceRepository
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
                self._process_batch(batch, pipeline, repository, paths.thumbnail_dir)
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
        repository,
        thumbnail_dir: Path,
    ) -> None:
        repository.remove_faces_for_assets(
            [str(row.get("id") or "") for row in batch],
            [str(row.get("rel") or "") for row in batch],
        )

        detected = pipeline.detect_faces_for_rows(
            batch,
            library_root=self._library_root,
            thumbnail_dir=thumbnail_dir,
        )

        new_faces: list[FaceRecord] = []
        done_ids: list[str] = []
        retry_ids: list[str] = []
        for item in detected:
            self._queued_ids.discard(item.asset_id)
            if item.error:
                retry_ids.append(item.asset_id)
                continue
            done_ids.append(item.asset_id)
            new_faces.extend(item.faces)

        all_faces = repository.get_all_faces() + new_faces
        if all_faces:
            clustered_faces, persons = cluster_face_records(
                all_faces,
                distance_threshold=pipeline.distance_threshold,
                min_samples=pipeline.min_samples,
            )
            state_repository = repository.state_repository
            if state_repository is not None:
                clustered_faces, persons = canonicalize_cluster_identities(
                    clustered_faces,
                    persons,
                    state_repository,
                    distance_threshold=pipeline.distance_threshold,
                )
            repository.replace_all(clustered_faces, persons)
            if state_repository is not None:
                state_repository.sync_scan_results(persons, clustered_faces)
        else:
            repository.replace_all([], [])

        store = get_global_repository(self._library_root)
        store.update_face_statuses(done_ids, FACE_STATUS_DONE)
        store.update_face_statuses(retry_ids, FACE_STATUS_RETRY)
        self.peopleIndexUpdated.emit()
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

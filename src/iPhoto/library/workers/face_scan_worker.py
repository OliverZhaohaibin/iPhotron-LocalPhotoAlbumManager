"""Background worker that performs low-pressure face scanning."""

from __future__ import annotations

import queue
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import QCoreApplication, QThread, Signal

from ...people.errors import FaceModelUnavailableError, FaceRuntimeUnavailableError
from ...people.index_coordinator import (
    PeopleIndexCoordinator,
    PeopleSnapshotCommittedError,
)
from ...people.model_paths import face_model_override_dir, resolve_face_model_dir
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
    modelUnavailable = Signal(str)

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
        model_override = face_model_override_dir()
        pipeline = FaceClusterPipeline(
            model_root=model_override or resolve_face_model_dir(paths.model_dir)
        )
        try:
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
                except FaceModelUnavailableError as exc:
                    if not self._cancelled:
                        if model_override is not None:
                            self.statusChanged.emit(
                                QCoreApplication.translate(
                                    "RecognitionExtension",
                                    "Face scanning unavailable: '{path}' is missing usable face "
                                    "models. Fix or unset IPHOTO_FACE_MODEL_DIR, then rescan.",
                                ).format(path=model_override)
                            )
                            self.modelUnavailable.emit("invalid_override")
                        else:
                            self.statusChanged.emit(str(exc))
                            self.modelUnavailable.emit(exc.reason)
                    return
                except FaceRuntimeUnavailableError as exc:
                    if not self._cancelled:
                        self.statusChanged.emit(str(exc))
                    return
                except RuntimeError as exc:
                    if not self._cancelled:
                        self.statusChanged.emit(str(exc))
                    return
                except Exception as exc:
                    LOGGER.warning("Face scan batch failed: %s", exc, exc_info=True)
                    if not self._cancelled:
                        reason = str(exc).strip() or exc.__class__.__name__
                        self.statusChanged.emit(f"Face scanning paused: {reason}")
                    return
        finally:
            close = getattr(pipeline, "close", None)
            if close is not None:
                close()

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
            return False
        attempted_statuses = {
            Path(str(row["rel"])).as_posix(): normalize_face_status(row.get("face_status"))
            for row in batch if row.get("rel")
        }
        detected = list(
            pipeline.detect_faces_for_rows(
                batch,
                library_root=self._library_root,
                thumbnail_dir=thumbnail_dir,
                is_cancelled=lambda: self._cancelled,
            )
        )

        if self._cancelled:
            return False

        retry_items = [item for item in detected if item.asset_id and item.error]
        for item in retry_items:
            LOGGER.warning(
                "Face scan failed for asset %s (%s): %s",
                item.asset_id,
                item.asset_rel,
                item.error,
            )
        event = coordinator.submit_detected_batch(
            detected,
            distance_threshold=pipeline.distance_threshold,
            min_samples=pipeline.min_samples,
            is_cancelled=lambda: self._cancelled,
            attempted_statuses=attempted_statuses,
        )
        statuses: list[str | None] = []
        store = self._people_service.asset_repository
        if retry_items and not self._cancelled and store is not None:
            rows = store.get_rows_by_rels(item.asset_rel for item in retry_items)
            statuses = [
                normalize_face_status(row.get("face_status"))
                for item in retry_items
                if (row := rows.get(item.asset_rel)) is not None and row.get("id") == item.asset_id
            ]
        if FACE_STATUS_RETRY in statuses:
            self.statusChanged.emit("Some assets need a face-scan retry.")
        if FACE_STATUS_FAILED in statuses:
            self.statusChanged.emit(
                "Some assets could not be face scanned and will be retried after a rescan."
            )
        metrics = getattr(pipeline, "last_scan_metrics", None)
        LOGGER.info(
            "Face scan batch processed for %s: assets=%d candidates=%d accepted=%d "
            "low_confidence=%d too_small=%d tiny_area=%d relative_area=%d "
            "retry=%d failed=%d quality_version=%s",
            self._library_root,
            len(batch),
            getattr(metrics, "face_candidates_total", 0),
            getattr(metrics, "face_accepted", 0),
            getattr(metrics, "face_rejected_confidence", 0),
            getattr(metrics, "face_rejected_size", 0),
            getattr(metrics, "face_rejected_tiny_area", 0),
            getattr(metrics, "face_rejected_relative_area", 0),
            statuses.count(FACE_STATUS_RETRY),
            statuses.count(FACE_STATUS_FAILED),
            getattr(pipeline, "candidate_quality_version", "legacy"),
        )

        return event is not None

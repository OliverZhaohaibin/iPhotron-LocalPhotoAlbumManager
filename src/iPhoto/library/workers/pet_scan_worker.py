"""Background worker that performs low-pressure pet scanning."""

from __future__ import annotations

import os
import queue
import time
from collections.abc import Iterable
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ...pets.index_coordinator import (
    PetIndexCoordinator,
    PetSnapshotCommittedError,
)
from ...pets.pipeline import PetClusterPipeline
from ...pets.service import PetService, pet_library_paths
from ...pets.status import (
    PET_STATUS_FAILED,
    PET_STATUS_PENDING,
    PET_STATUS_RETRY,
    is_pet_scan_candidate,
    normalize_pet_status,
)
from ...utils.logging import get_logger

LOGGER = get_logger()


class PetScanWorker(QThread):
    """Consume pending Pets assets from the session service."""

    petIndexUpdated = Signal()  # noqa: N815
    statusChanged = Signal(str)  # noqa: N815

    BATCH_SIZE = 2
    QUEUE_TARGET_SIZE = 8
    CPU_BACKOFF_SECONDS = 0.08

    def __init__(
        self,
        library_root: Path,
        parent=None,
        *,
        pet_service: PetService | None = None,
    ) -> None:
        super().__init__(parent)
        self._library_root = Path(library_root)
        if pet_service is None:
            from ...bootstrap.library_pet_service import create_pet_service

            pet_service = create_pet_service(self._library_root)
        self._pet_service = pet_service
        self._queue: queue.Queue[dict] = queue.Queue()
        self._queued_ids: set[str] = set()
        self._input_closed = False
        self._cancelled = False

    def enqueue_rows(self, rows: Iterable[dict]) -> None:
        for row in rows:
            asset_id = str(row.get("id") or "")
            status = normalize_pet_status(row.get("pet_status"))
            if not asset_id or asset_id in self._queued_ids:
                continue
            if status not in {None, PET_STATUS_RETRY, PET_STATUS_PENDING}:
                continue
            if not is_pet_scan_candidate(row):
                continue
            self._queued_ids.add(asset_id)
            self._queue.put(dict(row))

    def finish_input(self) -> None:
        self._input_closed = True

    def cancel(self) -> None:
        self._cancelled = True
        self._input_closed = True

    def run(self) -> None:  # type: ignore[override]
        if str(os.environ.get("IPHOTO_PET_SCAN_DISABLED", "")).strip() == "1":
            self.statusChanged.emit("Pet scanning is disabled.")
            return

        self._prime_pending_rows()
        if self._cancelled:
            return

        paths = pet_library_paths(self._library_root)
        pipeline = PetClusterPipeline(model_root=paths.model_dir)
        coordinator = self._pet_service.coordinator
        if coordinator is None:
            self.statusChanged.emit("Pet scanning is unavailable for this library.")
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
                    self.petIndexUpdated.emit()
                time.sleep(self.CPU_BACKOFF_SECONDS)
            except PetSnapshotCommittedError as exc:
                LOGGER.error("Pet scan bookkeeping failed after commit: %s", exc, exc_info=True)
                for asset_id in [str(row.get("id") or "") for row in batch if row.get("id")]:
                    self._queued_ids.discard(asset_id)
                self.statusChanged.emit(str(exc))
                return
            except RuntimeError as exc:
                # Missing optional dependencies or models are runtime-level
                # availability problems. Keep pending/retry rows untouched so
                # installing the runtime lets scanning resume without rescan.
                LOGGER.warning("Pet scanning unavailable: %s", exc)
                for asset_id in [str(row.get("id") or "") for row in batch if row.get("id")]:
                    self._queued_ids.discard(asset_id)
                self.statusChanged.emit(str(exc))
                return
            except Exception as exc:  # noqa: BLE001  # pragma: no cover
                LOGGER.warning("Pet scan batch failed: %s", exc, exc_info=True)
                self._mark_rows_retry(batch)
                reason = str(exc).strip() or exc.__class__.__name__
                self.statusChanged.emit(f"Pet scanning paused: {reason}")
                if self._input_closed:
                    return

    def _prime_pending_rows(self) -> None:
        self._top_up_pending_rows()

    def _top_up_pending_rows(self) -> None:
        store = self._pet_service.asset_repository
        if store is None:
            return
        attempts = 0
        while self._queue.qsize() < self.QUEUE_TARGET_SIZE and attempts < 3 and not self._cancelled:
            queue_size_before = self._queue.qsize()
            deficit = max(self.QUEUE_TARGET_SIZE - queue_size_before, self.BATCH_SIZE)
            self.enqueue_rows(
                store.read_rows_by_pet_status(
                    [PET_STATUS_PENDING, PET_STATUS_RETRY],
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
        coordinator: PetIndexCoordinator,
        pipeline: PetClusterPipeline,
        thumbnail_dir: Path,
    ) -> bool:
        if self._cancelled:
            self._mark_rows_retry(batch)
            return False
        detected = list(
            pipeline.detect_pets_for_rows(
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
                "Pet scan failed for asset %s (%s): %s",
                item.asset_id,
                item.asset_rel,
                item.error,
            )
        retry_id_set = {str(item.asset_id) for item in retry_items}
        retry_source_ids = {
            str(row.get("id") or "")
            for row in batch
            if str(row.get("id") or "") in retry_id_set
            and normalize_pet_status(row.get("pet_status")) == PET_STATUS_RETRY
        }
        first_retry_ids = [
            asset_id for asset_id in retry_id_set if asset_id not in retry_source_ids
        ]
        failed_ids = [asset_id for asset_id in retry_id_set if asset_id in retry_source_ids]

        if first_retry_ids:
            self.statusChanged.emit("Some assets need a pet-scan retry.")
        if failed_ids:
            self._update_pet_statuses(failed_ids, PET_STATUS_FAILED)
            self.statusChanged.emit(
                "Some assets could not be pet scanned and will be retried after a rescan."
            )
        retry_detected = [
            item for item in detected if not item.asset_id or str(item.asset_id) not in failed_ids
        ]
        event = coordinator.submit_detected_batch(
            retry_detected,
            distance_threshold=pipeline.distance_threshold,
            min_samples=pipeline.min_samples,
        )
        return event is not None

    def _mark_rows_retry(self, rows: Iterable[dict]) -> None:
        ids = [str(row.get("id") or "") for row in rows if row.get("id")]
        self._update_pet_statuses(ids, PET_STATUS_RETRY)
        for asset_id in ids:
            self._queued_ids.discard(asset_id)

    def _update_pet_statuses(self, asset_ids: Iterable[str], status: str) -> None:
        store = self._pet_service.asset_repository
        if store is None:
            return
        store.update_pet_statuses(asset_ids, status)

"""Background worker that performs low-pressure pet scanning."""

from __future__ import annotations

import os
import queue
import shutil
import time
import uuid
from collections.abc import Iterable
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ...pets.errors import PetModelUnavailableError, PetRuntimeUnavailableError
from ...pets.index_coordinator import (
    PetIndexCoordinator,
    PetSnapshotCommittedError,
)
from ...pets.model_bootstrap import ensure_pet_model_artifacts
from ...pets.pipeline import (
    PET_CLUSTERING_PIPELINE_VERSION,
    PET_DETECTOR_PIPELINE_VERSION,
    PetClusterPipeline,
)
from ...pets.service import PetService, pet_library_paths
from ...pets.status import (
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

    BATCH_SIZE = 16
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
        self._model_artifacts_ready = False

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
        if self._cancelled:
            return

        paths = pet_library_paths(self._library_root)
        self._cleanup_stale_thumbnail_staging(paths.thumbnail_dir)
        pipeline = PetClusterPipeline(model_root=paths.model_dir)
        if self._cancelled:
            return
        self._prepare_detector_migration()
        if self._cancelled:
            return
        self._prepare_clustering_pipeline()
        self._prime_pending_rows()
        if self._cancelled:
            return

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
                        self._mark_backfill_complete_if_drained()
                        if not self._cancelled and self._has_pending_clustering_consolidation():
                            try:
                                if self._consolidate_pending_clustering(pipeline):
                                    self.petIndexUpdated.emit()
                            except PetSnapshotCommittedError as exc:
                                LOGGER.error(
                                    "Pet scan consolidation bookkeeping failed after commit: %s",
                                    exc,
                                    exc_info=True,
                                )
                                self.statusChanged.emit(str(exc))
                                return
                            except (PetRuntimeUnavailableError, PetModelUnavailableError) as exc:
                                LOGGER.warning("Pet scan consolidation unavailable: %s", exc)
                                self.statusChanged.emit(str(exc))
                                return
                            except Exception as exc:  # noqa: BLE001  # pragma: no cover
                                LOGGER.warning(
                                    "Pet scan consolidation failed: %s", exc, exc_info=True
                                )
                                reason = str(exc).strip() or exc.__class__.__name__
                                self.statusChanged.emit(f"Pet scan consolidation paused: {reason}")
                                return
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
            except (PetRuntimeUnavailableError, PetModelUnavailableError) as exc:
                LOGGER.warning("Pet scanning unavailable: %s", exc)
                for asset_id in [str(row.get("id") or "") for row in batch if row.get("id")]:
                    self._queued_ids.discard(asset_id)
                self.statusChanged.emit(str(exc))
                return
            except Exception as exc:  # noqa: BLE001  # pragma: no cover
                LOGGER.warning("Pet scan batch failed: %s", exc, exc_info=True)
                for asset_id in [str(row.get("id") or "") for row in batch if row.get("id")]:
                    self._queued_ids.discard(asset_id)
                reason = str(exc).strip() or exc.__class__.__name__
                self.statusChanged.emit(f"Pet scanning paused: {reason}")
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
        self._ensure_model_artifacts_ready()
        batch_asset_ids = [str(row.get("id") or "") for row in batch if row.get("id")]
        people_boxes = self._pet_service.people_boxes_by_asset_ids(batch_asset_ids)
        staging_dir = thumbnail_dir / ".staging" / uuid.uuid4().hex
        detected = list(
            pipeline.detect_pets_for_rows(
                batch,
                library_root=self._library_root,
                thumbnail_dir=staging_dir,
                published_thumbnail_dir=thumbnail_dir,
                is_cancelled=lambda: self._cancelled,
                people_boxes_by_asset_id=people_boxes,
            )
        )
        if self._cancelled:
            shutil.rmtree(staging_dir, ignore_errors=True)
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
            self.statusChanged.emit("Some assets could not be pet scanned and were marked failed.")
        retry_detected = [
            item for item in detected if not item.asset_id or str(item.asset_id) not in failed_ids
        ]
        metrics = getattr(pipeline, "last_scan_metrics", None)
        LOGGER.info(
            "Pet scan batch processed for %s: assets=%d candidates=%d accepted=%d "
            "unsupported_species=%d too_small=%d quality_rejected=%d people_overlaps=%d "
            "retry=%d failed=%d quality_version=%s",
            self._library_root,
            len(batch),
            getattr(metrics, "candidate_boxes", 0),
            getattr(metrics, "accepted_detections", 0),
            getattr(metrics, "unsupported_species", 0),
            getattr(metrics, "too_small", 0),
            getattr(metrics, "pet_quality_rejected", 0),
            getattr(metrics, "people_overlaps", 0),
            len(first_retry_ids),
            len(failed_ids),
            getattr(pipeline, "candidate_quality_version", "legacy"),
        )
        try:
            event = coordinator.submit_detected_batch(
                retry_detected,
                distance_threshold=pipeline.distance_threshold,
                detector_pipeline_version=pipeline.detector_pipeline_version,
                clustering_pipeline_target=PET_CLUSTERING_PIPELINE_VERSION,
                people_boxes_provider=self._pet_service.people_boxes_by_asset_ids,
                staged_thumbnail_dir=staging_dir,
                published_thumbnail_dir=thumbnail_dir,
                failed_asset_ids=failed_ids,
            )
        finally:
            shutil.rmtree(staging_dir, ignore_errors=True)
        return event is not None

    def _ensure_model_artifacts_ready(self) -> None:
        if self._model_artifacts_ready:
            return
        ensure_pet_model_artifacts()
        self._model_artifacts_ready = True

    def _prepare_clustering_pipeline(self) -> bool:
        coordinator = self._pet_service.coordinator
        if coordinator is None:
            return False
        queued_count = coordinator.prepare_clustering_pipeline(
            clustering_pipeline_target=PET_CLUSTERING_PIPELINE_VERSION,
        )
        return queued_count > 0

    def _has_pending_clustering_consolidation(self) -> bool:
        coordinator = self._pet_service.coordinator
        if coordinator is None:
            return False
        return coordinator.has_pending_clustering_consolidation(
            clustering_pipeline_target=PET_CLUSTERING_PIPELINE_VERSION,
        )

    def _consolidate_pending_clustering(self, pipeline: PetClusterPipeline) -> bool:
        coordinator = self._pet_service.coordinator
        if coordinator is None or self._cancelled:
            return False
        result = coordinator.consolidate_pending_clustering(
            clustering_pipeline_target=PET_CLUSTERING_PIPELINE_VERSION,
            distance_threshold=pipeline.distance_threshold,
            is_cancelled=lambda: self._cancelled,
        )
        return bool(result is not None and result.changed)

    def _prepare_detector_migration(self) -> None:
        repository = self._pet_service.repository()
        store = self._pet_service.asset_repository
        if repository is None or store is None:
            return
        current_version = repository.get_scan_metadata("detector_pipeline_version")
        target_version = repository.get_scan_metadata("detector_migration_target")
        migration_state = repository.get_scan_metadata("detector_migration_state")
        legacy_backfill = repository.get_scan_metadata("pet_backfill_required") == "1"
        if (
            current_version == PET_DETECTOR_PIPELINE_VERSION
            and legacy_backfill
            and migration_state not in {"pending", "running"}
        ):
            repository.set_scan_metadata_many(
                {
                    "detector_migration_target": PET_DETECTOR_PIPELINE_VERSION,
                    "detector_migration_state": "running",
                }
            )
            return
        if current_version == PET_DETECTOR_PIPELINE_VERSION and migration_state not in {
            "pending",
            "running",
        }:
            repository.set_scan_metadata_many(
                {
                    "detector_migration_target": PET_DETECTOR_PIPELINE_VERSION,
                    "detector_migration_state": "complete",
                }
            )
            return

        if target_version != PET_DETECTOR_PIPELINE_VERSION or migration_state not in {
            "pending",
            "running",
        }:
            repository.set_scan_metadata_many(
                {
                    "detector_migration_target": PET_DETECTOR_PIPELINE_VERSION,
                    "detector_migration_state": "pending",
                    "pet_backfill_required": "1",
                }
            )
            migration_state = "pending"

        if migration_state == "running":
            return

        reset_count = store.reset_pet_statuses_for_pipeline_upgrade()
        if reset_count:
            LOGGER.info(
                "Reset %d pet-scanned assets to pending for detector pipeline upgrade "
                "%s -> %s in %s",
                reset_count,
                current_version or "<missing>",
                PET_DETECTOR_PIPELINE_VERSION,
                self._library_root,
            )
        repository.set_scan_metadata_many(
            {
                "detector_migration_target": PET_DETECTOR_PIPELINE_VERSION,
                "detector_migration_state": "running",
                "pet_backfill_required": "1",
            }
        )

    # Kept as a compatibility alias for older callers and focused tests.
    def _reset_done_rows_for_detector_upgrade(self) -> None:
        self._prepare_detector_migration()

    def _mark_rows_retry(self, rows: Iterable[dict]) -> None:
        ids = [str(row.get("id") or "") for row in rows if row.get("id")]
        self._update_pet_statuses(ids, PET_STATUS_RETRY)
        for asset_id in ids:
            self._queued_ids.discard(asset_id)

    def _mark_backfill_complete_if_drained(self) -> None:
        repository = self._pet_service.repository()
        store = self._pet_service.asset_repository
        if repository is None or store is None:
            return
        counts = store.count_by_pet_status()
        if (
            int(counts.get(PET_STATUS_PENDING, 0)) == 0
            and int(counts.get(PET_STATUS_RETRY, 0)) == 0
        ):
            repository.set_scan_metadata_many(
                {
                    "detector_pipeline_version": PET_DETECTOR_PIPELINE_VERSION,
                    "detector_migration_target": PET_DETECTOR_PIPELINE_VERSION,
                    "detector_migration_state": "complete",
                    "pet_backfill_required": "0",
                }
            )

    def _cleanup_stale_thumbnail_staging(self, thumbnail_dir: Path) -> None:
        staging_root = (Path(thumbnail_dir) / ".staging").resolve()
        if not staging_root.is_dir():
            return
        active = {str(path) for path in self._pet_service.active_thumbnail_staging_dirs()}
        for candidate in staging_root.iterdir():
            resolved = candidate.resolve()
            if resolved.parent != staging_root or str(resolved) in active:
                continue
            if resolved.is_dir():
                shutil.rmtree(resolved, ignore_errors=True)

    def _update_pet_statuses(self, asset_ids: Iterable[str], status: str) -> None:
        store = self._pet_service.asset_repository
        if store is None:
            return
        store.update_pet_statuses(asset_ids, status)

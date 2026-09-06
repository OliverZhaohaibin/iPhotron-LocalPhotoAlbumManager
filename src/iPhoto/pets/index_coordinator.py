"""Thread-safe coordinator for realtime Pets snapshot updates."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QObject, Qt, Signal, Slot

from iPhoto.application.ports.pets import PetAssetRepositoryPort
from iPhoto.recognition.assignment_recovery import (
    apply_detection_assignment_with_group_refresh,
)
from iPhoto.recognition.mutation_coordinator import (
    RecognitionMutationCoordinator,
    get_recognition_mutation_coordinator,
)
from iPhoto.utils.logging import get_logger
from iPhoto.utils.pathutils import ensure_work_dir

from .errors import PetStateCommitError
from .pipeline import (
    DEFAULT_PET_DISTANCE_THRESHOLD,
    PET_PEOPLE_IOU_THRESHOLD,
    PET_PEOPLE_LARGER_PET_RATIO,
    PET_PEOPLE_MURAL_IMAGE_COVERAGE_THRESHOLD,
    PET_PEOPLE_SMALLER_BOX_COVERAGE_THRESHOLD,
    DetectedAssetPets,
    _pet_people_overlap_decision,
)
from .records import PetMergeOutcome, PetMutationFailure
from .repository import (
    PetClusteringConsolidationCancelledError,
    PetClusteringConsolidationResult,
    PetRepository,
)
from .scan_session import PetScanSession
from .status import PET_STATUS_DONE, PET_STATUS_FAILED, PET_STATUS_PENDING, PET_STATUS_RETRY

LOGGER = get_logger()

_PET_JOURNAL_KINDS = {
    "pet_scan_commit",
    "pet_rename",
    "pet_hide",
    "pet_cover",
    "pet_merge",
    "pet_delete_detection",
    "pet_move_detection",
    "pet_move_detection_new",
    "pet_overlap_reconcile",
    "pet_recluster",
    "pet_cluster_consolidate",
    "recognition_detection_assignment",
    "recognition_merge",
}


class PetSnapshotCommittedError(PetStateCommitError):
    """Raised when the Pets snapshot committed but bookkeeping failed."""


@dataclass(frozen=True)
class PetSnapshotEvent:
    library_root: Path
    revision: int
    operation_id: str | None = None
    event_id: str | None = None
    generation_id: int = 0
    changed_asset_ids: tuple[str, ...] = ()
    added_pet_ids: tuple[str, ...] = ()
    updated_pet_ids: tuple[str, ...] = ()
    removed_pet_ids: tuple[str, ...] = ()
    changed_pet_ids: tuple[str, ...] = ()
    pet_redirects: dict[str, str] = field(default_factory=dict)


class PetIndexCoordinator(QObject):
    """Serialize Pets writes and publish committed snapshot revisions."""

    snapshotCommitted = Signal(object)  # noqa: N815
    _scheduleEmit = Signal(object)  # noqa: N815

    def __init__(
        self,
        library_root: Path,
        *,
        asset_repository: PetAssetRepositoryPort | None = None,
        mutation_coordinator: RecognitionMutationCoordinator | None = None,
    ) -> None:
        super().__init__()
        self._library_root = Path(library_root)
        self._asset_repository = asset_repository
        self._revision = 0
        self._shutdown_requested = False
        self._journal = mutation_coordinator or get_recognition_mutation_coordinator(
            self._library_root
        )
        self._owns_journal = mutation_coordinator is None
        self._lock = self._journal.execution_lock
        self._journal.register_recovery_handler(
            _PET_JOURNAL_KINDS,
            self._recover_registered_pet_operation,
        )
        pets_root = ensure_work_dir(self._library_root) / "pets"
        self._pet_repository = PetRepository(
            pets_root / "pet_index.db",
            pets_root / "pet_state.db",
        )
        self._pet_repository.initialize()
        self._scheduleEmit.connect(self._fire_snapshot, Qt.ConnectionType.QueuedConnection)
        self._recovery_error: Exception | None = None
        self._last_mutation_failure: PetMutationFailure | None = None
        try:
            with self._lock:
                if not self._journal.recover_pending():
                    raise self._journal.recovery_error or RuntimeError(
                        "Pets recognition recovery is incomplete."
                    )
                self._prune_runtime_commits_locked()
        except Exception as exc:
            self._recovery_error = exc
            LOGGER.error(
                "Pets recognition recovery failed during bind for %s",
                self._library_root,
                exc_info=True,
            )

    @Slot(object)
    def _fire_snapshot(self, event: object) -> None:
        self.snapshotCommitted.emit(event)

    @property
    def library_root(self) -> Path:
        return self._library_root

    @property
    def last_mutation_failure(self) -> PetMutationFailure | None:
        return self._last_mutation_failure

    def set_asset_repository(
        self,
        asset_repository: PetAssetRepositoryPort | None,
    ) -> None:
        with self._lock:
            self._asset_repository = asset_repository

    def submit_detected_batch(
        self,
        detected_results: Iterable[DetectedAssetPets],
        *,
        distance_threshold: float,
        detector_pipeline_version: str | None = None,
        clustering_pipeline_target: str | None = None,
        people_boxes_provider: Callable[
            [Iterable[str]],
            dict[str, tuple[tuple[int, int, int, int], ...]],
        ]
        | None = None,
        staged_thumbnail_dir: Path | None = None,
        published_thumbnail_dir: Path | None = None,
        failed_asset_ids: Iterable[str] = (),
    ) -> PetSnapshotEvent | None:
        detected_batch = list(detected_results)
        if not detected_batch:
            return None

        with self._lock:
            if self._shutdown_requested:
                return None
            if not self._ensure_recovered_locked():
                return None
            repository = self._repository()
            filtered_thumbnail_paths: list[str | Path] = []
            if people_boxes_provider is not None:
                asset_ids = tuple(
                    dict.fromkeys(result.asset_id for result in detected_batch if result.asset_id)
                )
                people_boxes = people_boxes_provider(asset_ids)
                revalidated_batch: list[DetectedAssetPets] = []
                for result in detected_batch:
                    retained_detections = []
                    for detection in result.detections:
                        overlap_decision = _pet_people_overlap_decision(
                            (
                                detection.box_x,
                                detection.box_y,
                                detection.box_w,
                                detection.box_h,
                            ),
                            people_boxes.get(result.asset_id, ()),
                            image_dimensions=(
                                detection.image_width,
                                detection.image_height,
                            ),
                        )
                        if overlap_decision.suppressed:
                            LOGGER.info(
                                "Suppressed committed pet detection %s for asset %s: "
                                "reason=%s pet_to_face_area_ratio=%.3f "
                                "pet_image_coverage=%.3f iou_threshold=%.2f "
                                "smaller_box_coverage_threshold=%.2f larger_pet_ratio=%.2f "
                                "mural_image_coverage_threshold=%.2f",
                                detection.detection_id,
                                result.asset_id,
                                overlap_decision.reason,
                                overlap_decision.pet_to_face_area_ratio,
                                overlap_decision.pet_image_coverage,
                                PET_PEOPLE_IOU_THRESHOLD,
                                PET_PEOPLE_SMALLER_BOX_COVERAGE_THRESHOLD,
                                PET_PEOPLE_LARGER_PET_RATIO,
                                PET_PEOPLE_MURAL_IMAGE_COVERAGE_THRESHOLD,
                            )
                            if detection.thumbnail_path:
                                filtered_thumbnail_paths.append(detection.thumbnail_path)
                            continue
                        retained_detections.append(detection)
                    revalidated_batch.append(
                        DetectedAssetPets(
                            asset_id=result.asset_id,
                            asset_rel=result.asset_rel,
                            detections=retained_detections,
                            error=result.error,
                        )
                    )
                detected_batch = revalidated_batch
            terminal_failed_ids = list(
                dict.fromkeys(str(value) for value in failed_asset_ids if value)
            )
            session = PetScanSession()
            done_ids, retry_ids = session.stage_detection_results(detected_batch)
            terminal_failed_set = set(terminal_failed_ids)
            done_ids = [value for value in done_ids if value not in terminal_failed_set]
            retry_ids = [value for value in retry_ids if value not in terminal_failed_set]
            operation_payload = {
                "done_asset_ids": list(done_ids),
                "retry_asset_ids": list(retry_ids),
                "failed_asset_ids": list(terminal_failed_ids),
                "index_applied": False,
                "staged_thumbnail_dir": (
                    str(staged_thumbnail_dir) if staged_thumbnail_dir is not None else None
                ),
            }
            operation_id = self._try_prepare_operation_locked("pet_scan_commit", operation_payload)
            if operation_id is None:
                return None

            staged_detections = [
                detection
                for result in detected_batch
                if not result.error
                for detection in result.detections
            ]
            staged_detections, generation_id = repository.assign_embedding_generation(
                staged_detections
            )
            operation_payload.update(
                {
                    "generation_id": generation_id,
                    "embedding_pipeline_version": (
                        staged_detections[0].embedding_pipeline_version if staged_detections else ""
                    ),
                    "embedding_dimension": (
                        staged_detections[0].embedding_dim if staged_detections else 0
                    ),
                    "detector_pipeline_version": detector_pipeline_version or "",
                    "clustering_pipeline_target": clustering_pipeline_target or "",
                    "published_thumbnail_paths": [
                        str(path)
                        for path in self._planned_thumbnail_targets(
                            staged_thumbnail_dir,
                            published_thumbnail_dir,
                        )
                    ],
                }
            )
            self._journal.transition(
                operation_id,
                "applying",
                payload=operation_payload,
            )
            try:
                published_thumbnail_paths = self._publish_staged_thumbnails(
                    staged_thumbnail_dir,
                    published_thumbnail_dir,
                )
            except Exception as exc:
                self._journal.transition(
                    operation_id,
                    "finalized",
                    payload=operation_payload,
                    error=f"thumbnail_publish_failed: {exc}",
                )
                raise
            try:
                commit_result = repository.replace_assets_incrementally(
                    done_ids,
                    staged_detections,
                    retry_asset_ids=[*retry_ids, *terminal_failed_ids],
                    distance_threshold=distance_threshold,
                    operation_id=operation_id,
                    clustering_pipeline_target=clustering_pipeline_target,
                )
            except Exception as exc:
                runtime_commit = repository.get_runtime_commit(operation_id)
                if runtime_commit is None:
                    self._cleanup_thumbnail_paths(published_thumbnail_paths)
                    self._journal.transition(
                        operation_id,
                        "finalized",
                        payload=operation_payload,
                        error=str(exc),
                    )
                    raise
                operation_payload.update(runtime_commit)
                operation_payload["index_applied"] = True
                self._journal.transition(
                    operation_id,
                    "applying",
                    payload=operation_payload,
                    error=str(exc),
                )
                raise PetSnapshotCommittedError(
                    "Pet runtime committed; durable state recovery is pending."
                ) from exc
            operation_payload.update(
                {
                    "index_applied": True,
                    "generation_id": generation_id,
                    "changed_asset_ids": list(commit_result.changed_asset_ids),
                    "retired_asset_ids": list(commit_result.retired_asset_ids),
                    "added_pet_ids": list(commit_result.added_pet_ids),
                    "updated_pet_ids": list(commit_result.updated_pet_ids),
                    "removed_pet_ids": list(commit_result.removed_pet_ids),
                }
            )
            self._journal.transition(
                operation_id,
                "applying",
                payload=operation_payload,
            )
            if staged_detections:
                repository.activate_embedding_generation(
                    generation_id=generation_id,
                    embedding_pipeline_version=(staged_detections[0].embedding_pipeline_version),
                    embedding_dimension=staged_detections[0].embedding_dim,
                )
            explicit_status_ids = set(done_ids) | set(retry_ids) | set(terminal_failed_ids)
            retired_pending_ids = [
                asset_id
                for asset_id in commit_result.retired_asset_ids
                if asset_id not in explicit_status_ids
            ]
            try:
                self._mark_pending_asset_ids(retired_pending_ids)
                self._mark_retry_asset_ids(retry_ids)
                self._mark_failed_asset_ids(terminal_failed_ids)
                self._mark_done_asset_ids(done_ids)
            except Exception as exc:
                LOGGER.error(
                    "Pets index committed for %s, but asset status update failed: %s",
                    self._library_root,
                    exc,
                    exc_info=True,
                )
                raise PetSnapshotCommittedError(
                    "Pet scan committed, but updating scan bookkeeping failed."
                ) from exc
            changed_asset_ids = commit_result.changed_asset_ids or tuple(
                done_ids + retry_ids + terminal_failed_ids
            )
            outbox_payload = {
                "generation_id": generation_id,
                "changed_asset_ids": list(changed_asset_ids),
                "added_pet_ids": list(commit_result.added_pet_ids),
                "updated_pet_ids": list(commit_result.updated_pet_ids),
                "removed_pet_ids": list(commit_result.removed_pet_ids),
            }
            event = self._emit_snapshot(
                operation_id=operation_id,
                generation_id=generation_id,
                changed_asset_ids=changed_asset_ids,
                added_pet_ids=commit_result.added_pet_ids,
                updated_pet_ids=commit_result.updated_pet_ids,
                removed_pet_ids=commit_result.removed_pet_ids,
                dispatch=False,
            )
            repository.prune_unreferenced_thumbnails(commit_result.previous_thumbnail_paths)
            repository.prune_unreferenced_thumbnails(filtered_thumbnail_paths)
            self._journal.commit_and_dispatch(
                operation_id,
                outbox_payload,
                lambda event=event: self._scheduleEmit.emit(event),
            )
            return event

    def rename_pet(self, pet_id: str, name_or_none: str | None) -> PetSnapshotEvent | None:
        if not pet_id:
            return None
        with self._lock:
            if self._shutdown_requested:
                return None
            if not self._ensure_recovered_locked():
                return None
            repository = self._repository()
            operation_id = self._try_prepare_operation_locked(
                "pet_rename",
                {"pet_id": pet_id, "name": name_or_none},
            )
            if operation_id is None:
                return None
            if not repository.rename_pet(pet_id, name_or_none):
                self._journal.transition(
                    operation_id,
                    "finalized",
                    error="unknown_pet_id",
                )
                return None
            return self._emit_journaled_snapshot(
                operation_id,
                changed_asset_ids=tuple(repository.get_asset_ids_by_pet(pet_id)),
                changed_pet_ids=(pet_id,),
            )

    def set_pet_hidden(self, pet_id: str, hidden: bool) -> bool:
        if not pet_id:
            return False
        with self._lock:
            if self._shutdown_requested:
                return False
            if not self._ensure_recovered_locked():
                return False
            repository = self._repository()
            operation_id = self._try_prepare_operation_locked(
                "pet_hide",
                {"pet_id": pet_id, "hidden": bool(hidden)},
            )
            if operation_id is None:
                return False
            changed = repository.set_pet_hidden(pet_id, hidden)
            if changed:
                self._emit_journaled_snapshot(
                    operation_id,
                    changed_asset_ids=tuple(repository.get_asset_ids_by_pet(pet_id)),
                    changed_pet_ids=(pet_id,),
                )
            else:
                self._journal.transition(
                    operation_id,
                    "finalized",
                    error="unknown_pet_id",
                )
            return changed

    def set_pet_cover(self, pet_id: str, detection_id: str) -> bool:
        if not pet_id or not detection_id:
            return False
        with self._lock:
            if self._shutdown_requested:
                return False
            if not self._ensure_recovered_locked():
                return False
            repository = self._repository()
            operation_id = self._try_prepare_operation_locked(
                "pet_cover",
                {"pet_id": pet_id, "detection_id": detection_id},
            )
            if operation_id is None:
                return False
            changed = repository.set_pet_cover(pet_id, detection_id)
            if changed:
                self._emit_journaled_snapshot(
                    operation_id,
                    changed_asset_ids=tuple(repository.get_asset_ids_by_pet(pet_id)),
                    changed_pet_ids=(pet_id,),
                )
            else:
                self._journal.transition(
                    operation_id,
                    "finalized",
                    error="cover_identity_mismatch",
                )
            return changed

    def merge_pets(self, source_pet_id: str, target_pet_id: str) -> PetMergeOutcome:
        with self._lock:
            if self._shutdown_requested:
                return PetMergeOutcome(False, PetMutationFailure.SHUTTING_DOWN)
            if not self._ensure_recovered_locked():
                return PetMergeOutcome(False, PetMutationFailure.RECOVERY_PENDING)
            repository = self._repository()
            operation_id = self._try_prepare_operation_locked(
                "pet_merge",
                {"source_pet_id": source_pet_id, "target_pet_id": target_pet_id},
            )
            if operation_id is None:
                return PetMergeOutcome(False, PetMutationFailure.RECOVERY_PENDING)
            result = repository.merge_pets(
                source_pet_id,
                target_pet_id,
                operation_id=operation_id,
            )
            if result is None:
                failure = (
                    repository.last_mutation_failure
                    or PetMutationFailure.REJECTED
                )
                self._journal.transition(
                    operation_id,
                    "finalized",
                    error=(
                        "same_asset_conflict"
                        if failure == PetMutationFailure.SAME_ASSET_CONFLICT
                        else "merge_rejected"
                    ),
                )
                return PetMergeOutcome(
                    False,
                    failure,
                    operation_id,
                )
            self._emit_journaled_snapshot(
                operation_id,
                changed_asset_ids=result.changed_asset_ids,
                changed_pet_ids=result.changed_pet_ids,
                pet_redirects=result.pet_redirects,
            )
            return PetMergeOutcome(True, operation_id=operation_id)

    def prepare_clustering_pipeline(self, *, clustering_pipeline_target: str) -> int:
        """Durably queue an upgrade without running clustering before scan drain."""

        with self._lock:
            if self._shutdown_requested or not self._ensure_recovered_locked():
                return 0
            return self._repository().prepare_clustering_pipeline(
                target_version=clustering_pipeline_target
            )

    def has_pending_clustering_consolidation(
        self,
        *,
        clustering_pipeline_target: str,
    ) -> bool:
        with self._lock:
            if self._shutdown_requested or not self._ensure_recovered_locked():
                return False
            return self._repository().has_pending_clustering_consolidation(
                target_version=clustering_pipeline_target
            )

    def consolidate_pending_clustering(
        self,
        *,
        clustering_pipeline_target: str,
        distance_threshold: float,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> PetClusteringConsolidationResult | None:
        """Consolidate only components reachable from durable pending seeds."""

        with self._lock:
            if self._shutdown_requested or not self._ensure_recovered_locked():
                return None
            repository = self._repository()
            if not repository.has_pending_clustering_consolidation(
                target_version=clustering_pipeline_target
            ):
                return PetClusteringConsolidationResult()
            repository.set_clustering_consolidation_state(
                "running",
                target_version=clustering_pipeline_target,
            )
            operation_payload = {
                "clustering_pipeline_target": clustering_pipeline_target,
                "previous_clustering_pipeline_version": (
                    repository.get_scan_metadata("clustering_pipeline_version") or ""
                ),
            }
            operation_id = self._try_prepare_operation_locked(
                "pet_cluster_consolidate",
                operation_payload,
            )
            if operation_id is None:
                repository.set_clustering_consolidation_state(
                    "pending",
                    target_version=clustering_pipeline_target,
                )
                return None
            try:
                result = repository.consolidate_pending_clustering(
                    target_version=clustering_pipeline_target,
                    distance_threshold=distance_threshold,
                    operation_id=operation_id,
                    is_cancelled=is_cancelled,
                )
                repository.refresh_people_group_assets_for_pets(result.changed_pet_ids)
            except PetClusteringConsolidationCancelledError:
                repository.set_clustering_consolidation_state(
                    "pending",
                    target_version=clustering_pipeline_target,
                )
                self._journal.transition(
                    operation_id,
                    "finalized",
                    payload=operation_payload,
                    error="cancelled_before_index_commit",
                )
                return None
            except Exception as exc:
                runtime_commit = repository.get_runtime_commit(operation_id)
                if runtime_commit is None:
                    repository.set_clustering_consolidation_state(
                        "pending",
                        target_version=clustering_pipeline_target,
                    )
                    self._journal.transition(
                        operation_id,
                        "finalized",
                        payload=operation_payload,
                        error=str(exc),
                    )
                    raise
                raise PetSnapshotCommittedError(
                    "Pet clustering committed; durable state recovery is pending."
                ) from exc
            self._emit_journaled_snapshot(
                operation_id,
                changed_asset_ids=result.changed_asset_ids,
                changed_pet_ids=result.changed_pet_ids,
            )
            LOGGER.info(
                "Consolidated %d pending Pet clustering seeds into local components for %s in %s",
                result.processed_seed_count,
                clustering_pipeline_target,
                self._library_root,
            )
            return result

    def delete_detection(self, detection_id: str) -> PetSnapshotEvent | None:
        with self._lock:
            if self._shutdown_requested:
                return None
            if not self._ensure_recovered_locked():
                return None
            repository = self._repository()
            operation_id = self._try_prepare_operation_locked(
                "pet_delete_detection",
                {"detection_id": detection_id},
            )
            if operation_id is None:
                return None
            result = repository.delete_detection(
                detection_id,
                operation_id=operation_id,
            )
            if result is None:
                self._journal.transition(
                    operation_id,
                    "finalized",
                    error="unknown_detection_id",
                )
                return None
            return self._emit_journaled_snapshot(
                operation_id,
                changed_asset_ids=result.changed_asset_ids,
                changed_pet_ids=result.changed_pet_ids,
            )

    def reconcile_people_overlaps(
        self,
        people_boxes_by_asset_id: dict[str, tuple[tuple[int, int, int, int], ...]],
        *,
        distance_threshold: float = DEFAULT_PET_DISTANCE_THRESHOLD,
    ) -> PetSnapshotEvent | None:
        """Remove pet detections that conflict with authoritative People boxes."""

        if not people_boxes_by_asset_id:
            return None
        with self._lock:
            if self._shutdown_requested:
                return None
            if not self._ensure_recovered_locked():
                return None
            repository = self._repository()
            scoped_asset_ids = tuple(people_boxes_by_asset_id)
            previous_detections = repository.get_detections_by_asset_ids(scoped_asset_ids)
            removed = []
            for detection in previous_detections:
                overlap_decision = _pet_people_overlap_decision(
                    (
                        detection.box_x,
                        detection.box_y,
                        detection.box_w,
                        detection.box_h,
                    ),
                    people_boxes_by_asset_id.get(detection.asset_id, ()),
                    image_dimensions=(
                        detection.image_width,
                        detection.image_height,
                    ),
                )
                if not overlap_decision.suppressed:
                    continue
                LOGGER.info(
                    "Reconciled pet detection %s for asset %s: reason=%s "
                    "pet_to_face_area_ratio=%.3f pet_image_coverage=%.3f "
                    "iou_threshold=%.2f smaller_box_coverage_threshold=%.2f "
                    "larger_pet_ratio=%.2f mural_image_coverage_threshold=%.2f",
                    detection.detection_id,
                    detection.asset_id,
                    overlap_decision.reason,
                    overlap_decision.pet_to_face_area_ratio,
                    overlap_decision.pet_image_coverage,
                    PET_PEOPLE_IOU_THRESHOLD,
                    PET_PEOPLE_SMALLER_BOX_COVERAGE_THRESHOLD,
                    PET_PEOPLE_LARGER_PET_RATIO,
                    PET_PEOPLE_MURAL_IMAGE_COVERAGE_THRESHOLD,
                )
                removed.append(detection)
            if not removed:
                return None

            removed_ids = {detection.detection_id for detection in removed}
            changed_asset_ids = tuple(
                dict.fromkeys(detection.asset_id for detection in removed if detection.asset_id)
            )
            operation_id = self._try_prepare_operation_locked(
                "pet_overlap_reconcile",
                {
                    "asset_ids": list(scoped_asset_ids),
                    "changed_asset_ids": list(changed_asset_ids),
                },
            )
            if operation_id is None:
                return None
            commit_result = repository.delete_detections_transactionally(
                removed_ids,
                operation_id=operation_id,
                operation_kind="pet_overlap_reconcile",
            )
            repository.prune_unreferenced_thumbnails(commit_result.previous_thumbnail_paths)
            return self._emit_journaled_snapshot(
                operation_id,
                changed_asset_ids=changed_asset_ids,
                added_pet_ids=commit_result.added_pet_ids,
                updated_pet_ids=commit_result.updated_pet_ids,
                removed_pet_ids=commit_result.removed_pet_ids,
            )

    def move_detection_to_pet(
        self,
        detection_id: str,
        target_pet_id: str,
    ) -> PetSnapshotEvent | None:
        with self._lock:
            self._last_mutation_failure = None
            if self._shutdown_requested:
                self._last_mutation_failure = PetMutationFailure.SHUTTING_DOWN
                return None
            if not self._ensure_recovered_locked():
                self._last_mutation_failure = PetMutationFailure.RECOVERY_PENDING
                return None
            repository = self._repository()
            operation_id = self._try_prepare_operation_locked(
                "pet_move_detection",
                {"detection_id": detection_id, "target_pet_id": target_pet_id},
            )
            if operation_id is None:
                self._last_mutation_failure = PetMutationFailure.RECOVERY_PENDING
                return None
            result = repository.move_detection_to_pet(
                detection_id,
                target_pet_id,
                operation_id=operation_id,
            )
            if result is None:
                self._last_mutation_failure = (
                    repository.last_mutation_failure
                    or PetMutationFailure.REJECTED
                )
                self._journal.transition(
                    operation_id,
                    "finalized",
                    error=(
                        "same_asset_conflict"
                        if self._last_mutation_failure
                        == PetMutationFailure.SAME_ASSET_CONFLICT
                        else "move_rejected"
                    ),
                )
                return None
            return self._emit_journaled_snapshot(
                operation_id,
                changed_asset_ids=result.changed_asset_ids,
                changed_pet_ids=result.changed_pet_ids,
            )

    def move_detection_to_new_pet(
        self,
        detection_id: str,
        new_pet_id: str,
        new_name: str | None,
    ) -> PetSnapshotEvent | None:
        with self._lock:
            if self._shutdown_requested:
                return None
            if not self._ensure_recovered_locked():
                return None
            repository = self._repository()
            operation_id = self._try_prepare_operation_locked(
                "pet_move_detection_new",
                {
                    "detection_id": detection_id,
                    "new_pet_id": new_pet_id,
                    "new_name": new_name,
                },
            )
            if operation_id is None:
                return None
            result = repository.move_detection_to_new_pet(
                detection_id,
                new_pet_id,
                new_name,
                operation_id=operation_id,
            )
            if result is None:
                self._journal.transition(
                    operation_id,
                    "finalized",
                    error="move_rejected",
                )
                return None
            return self._emit_journaled_snapshot(
                operation_id,
                changed_asset_ids=result.changed_asset_ids,
                changed_pet_ids=result.changed_pet_ids,
            )

    def begin_shutdown(self) -> None:
        with self._lock:
            self._shutdown_requested = True

    def resume(self) -> None:
        with self._lock:
            self._shutdown_requested = False

    def close(self) -> None:
        """Permanently release resources owned by this coordinator."""

        self.begin_shutdown()
        if self._owns_journal:
            self._journal.close()

    def _repository(self) -> PetRepository:
        return self._pet_repository

    def _emit_snapshot(
        self,
        *,
        operation_id: str | None = None,
        generation_id: int = 0,
        changed_asset_ids: tuple[str, ...] = (),
        added_pet_ids: tuple[str, ...] = (),
        updated_pet_ids: tuple[str, ...] = (),
        removed_pet_ids: tuple[str, ...] = (),
        changed_pet_ids: tuple[str, ...] = (),
        pet_redirects: dict[str, str] | None = None,
        dispatch: bool = True,
    ) -> PetSnapshotEvent:
        self._revision += 1
        changed_pet_ids = tuple(
            dict.fromkeys(changed_pet_ids + added_pet_ids + updated_pet_ids + removed_pet_ids)
        )
        event = PetSnapshotEvent(
            library_root=self._library_root,
            revision=self._revision,
            operation_id=operation_id,
            event_id=operation_id,
            generation_id=generation_id,
            changed_asset_ids=tuple(dict.fromkeys(changed_asset_ids)),
            added_pet_ids=tuple(dict.fromkeys(added_pet_ids)),
            updated_pet_ids=tuple(dict.fromkeys(updated_pet_ids)),
            removed_pet_ids=tuple(dict.fromkeys(removed_pet_ids)),
            changed_pet_ids=tuple(dict.fromkeys(changed_pet_ids)),
            pet_redirects=dict(pet_redirects or {}),
        )
        if dispatch:
            self._scheduleEmit.emit(event)
        return event

    def _emit_journaled_snapshot(
        self,
        operation_id: str,
        **event_fields,
    ) -> PetSnapshotEvent:
        outbox_payload = {
            key: list(value) if isinstance(value, tuple) else value
            for key, value in event_fields.items()
        }
        event = self._emit_snapshot(
            operation_id=operation_id,
            dispatch=False,
            **event_fields,
        )
        self._journal.commit_and_dispatch(
            operation_id,
            outbox_payload,
            lambda: self._scheduleEmit.emit(event),
        )
        self._prune_runtime_commits_locked()
        return event

    def _prune_runtime_commits_locked(self) -> None:
        protected = tuple(operation.operation_id for operation in self._journal.unfinished())
        self._repository().prune_runtime_commits(
            protected_operation_ids=protected,
        )

    def _try_prepare_operation_locked(
        self,
        kind: str,
        payload: dict[str, object],
    ) -> str | None:
        operation_id = self._journal.try_prepare(kind, payload)
        if operation_id is None:
            self._recovery_error = RuntimeError(
                "Another recognition operation must finish before Pets can continue."
            )
            return None
        return operation_id

    def _mark_done_asset_ids(self, done_ids: list[str]) -> None:
        self._mark_asset_ids_with_status(done_ids, PET_STATUS_DONE)

    def _mark_pending_asset_ids(self, pending_ids: list[str]) -> None:
        self._mark_asset_ids_with_status(pending_ids, PET_STATUS_PENDING)

    def _mark_retry_asset_ids(self, retry_ids: list[str]) -> None:
        self._mark_asset_ids_with_status(retry_ids, PET_STATUS_RETRY)

    def _mark_failed_asset_ids(self, failed_ids: list[str]) -> None:
        self._mark_asset_ids_with_status(failed_ids, PET_STATUS_FAILED)

    def _mark_asset_ids_with_status(self, asset_ids: list[str], status: str) -> None:
        if not asset_ids:
            return
        store = self._asset_repository
        if store is None:
            return
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                store.update_pet_statuses(asset_ids, status)
                return
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt == 2:
                    break
                time.sleep(0.05 * (attempt + 1))
        if last_error is not None:
            raise last_error

    def _recover_registered_pet_operation(self, operation) -> bool:
        if operation.kind == "recognition_merge" and (
            _legacy_pet_identity_id(operation.payload.get("source")) is None
            or _legacy_pet_identity_id(operation.payload.get("target")) is None
        ):
            return False
        self._recover_operations_locked(operation)
        return all(
            pending.operation_id != operation.operation_id for pending in self._journal.unfinished()
        )

    def _recover_operations_locked(self, only_operation=None) -> None:
        repository = self._repository()
        operations = (only_operation,) if only_operation is not None else self._journal.unfinished()
        for operation in operations:
            if operation.kind != "pet_scan_commit":
                if operation.kind == "recognition_merge":
                    if self._recover_legacy_pet_recognition_merge(repository, operation):
                        continue
                if operation.kind in {
                    "pet_merge",
                    "pet_delete_detection",
                    "pet_move_detection",
                    "pet_move_detection_new",
                    "pet_overlap_reconcile",
                    "pet_recluster",
                    "pet_cluster_consolidate",
                }:
                    runtime_commit = repository.get_runtime_commit(operation.operation_id)
                    if runtime_commit is not None:
                        repository.complete_runtime_state_sync(operation.operation_id)
                        self._finish_runtime_backed_recovery(
                            repository,
                            operation,
                            runtime_commit,
                        )
                        continue
                    if operation.kind in {
                        "pet_overlap_reconcile",
                        "pet_recluster",
                        "pet_cluster_consolidate",
                    }:
                        self._journal.transition(
                            operation.operation_id,
                            "finalized",
                            payload=operation.payload,
                            error="superseded_before_index_commit",
                        )
                        continue
                if not self._recover_pet_mutation(operation):
                    raise RuntimeError(
                        "Recognition operation must be recovered by its owner before "
                        f"Pets can continue: {operation.kind}/{operation.operation_id}"
                    )
                continue
            payload = operation.payload
            runtime_commit = repository.get_runtime_commit(operation.operation_id)
            if runtime_commit is None:
                self._cleanup_thumbnail_paths(
                    Path(str(value))
                    for value in payload.get("published_thumbnail_paths", ())
                    if value
                )
                staged_dir = payload.get("staged_thumbnail_dir")
                if staged_dir:
                    self._cleanup_staging_dir(Path(str(staged_dir)))
                self._journal.transition(
                    operation.operation_id,
                    "finalized",
                    payload=payload,
                    error="superseded_before_index_commit",
                )
                continue
            runtime_commit = repository.complete_runtime_state_sync(operation.operation_id)
            if runtime_commit is None:
                raise RuntimeError(
                    f"Missing runtime commit during recovery: {operation.operation_id}"
                )
            payload.update(runtime_commit)
            payload["index_applied"] = True
            generation_id = int(payload.get("generation_id") or 0)
            embedding_version = str(payload.get("embedding_pipeline_version") or "")
            embedding_dimension = int(payload.get("embedding_dimension") or 0)
            if embedding_version and embedding_dimension > 0:
                repository.activate_embedding_generation(
                    generation_id=generation_id,
                    embedding_pipeline_version=embedding_version,
                    embedding_dimension=embedding_dimension,
                )
            clustering_target = str(
                payload.get("clustering_pipeline_target")
                or payload.get("clustering_pipeline_version")
                or ""
            )
            if clustering_target:
                repository.queue_pet_ids_for_clustering(
                    (
                        str(value)
                        for value in (
                            payload.get("affected_pet_ids") or payload.get("changed_pet_ids", ())
                        )
                        if value
                    ),
                    target_version=clustering_target,
                    generation_id=generation_id,
                )
            done_ids = [str(value) for value in payload.get("done_asset_ids", ()) if value]
            retry_ids = [str(value) for value in payload.get("retry_asset_ids", ()) if value]
            failed_ids = [str(value) for value in payload.get("failed_asset_ids", ()) if value]
            explicit_status_ids = set(done_ids) | set(retry_ids) | set(failed_ids)
            retired_pending_ids = [
                str(value)
                for value in payload.get("retired_asset_ids", ())
                if value and str(value) not in explicit_status_ids
            ]
            self._mark_pending_asset_ids(retired_pending_ids)
            self._mark_retry_asset_ids(retry_ids)
            self._mark_failed_asset_ids(failed_ids)
            self._mark_done_asset_ids(done_ids)
            changed_asset_ids = tuple(
                str(value) for value in payload.get("changed_asset_ids", ()) if value
            )
            if not changed_asset_ids:
                changed_asset_ids = tuple(dict.fromkeys((*done_ids, *retry_ids, *failed_ids)))
            event_payload = {
                "changed_asset_ids": list(changed_asset_ids),
                "added_pet_ids": list(payload.get("added_pet_ids", ())),
                "updated_pet_ids": list(payload.get("updated_pet_ids", ())),
                "removed_pet_ids": list(payload.get("removed_pet_ids", ())),
            }
            event = self._emit_snapshot(
                operation_id=operation.operation_id,
                generation_id=int(payload.get("generation_id") or 0),
                changed_asset_ids=tuple(event_payload["changed_asset_ids"]),
                added_pet_ids=tuple(event_payload["added_pet_ids"]),
                updated_pet_ids=tuple(event_payload["updated_pet_ids"]),
                removed_pet_ids=tuple(event_payload["removed_pet_ids"]),
                dispatch=False,
            )
            self._journal.commit_and_dispatch(
                operation.operation_id,
                event_payload,
                lambda event=event: self._scheduleEmit.emit(event),
            )

    def _recover_legacy_pet_recognition_merge(self, repository, operation) -> bool:
        """Forward-recover the obsolete outer journal used by pet-to-pet merges."""

        source_id = _legacy_pet_identity_id(operation.payload.get("source"))
        target_id = _legacy_pet_identity_id(operation.payload.get("target"))
        if source_id is None or target_id is None:
            return False

        runtime_commit = repository.get_runtime_commit(operation.operation_id)
        if runtime_commit is None:
            result = repository.merge_pets(
                source_id,
                target_id,
                operation_id=operation.operation_id,
            )
            if result is None:
                self._journal.transition(
                    operation.operation_id,
                    "finalized",
                    payload=operation.payload,
                    error="legacy_pet_merge_rejected",
                )
                return True
            self._emit_journaled_snapshot(
                operation.operation_id,
                changed_asset_ids=result.changed_asset_ids,
                changed_pet_ids=result.changed_pet_ids,
                pet_redirects=result.pet_redirects,
            )
            return True

        repository.complete_runtime_state_sync(operation.operation_id)
        repository.recover_pet_merge_people_groups(source_id, target_id)
        self._emit_journaled_snapshot(
            operation.operation_id,
            changed_asset_ids=tuple(runtime_commit.get("changed_asset_ids", ())),
            changed_pet_ids=tuple(runtime_commit.get("changed_pet_ids", ())),
            pet_redirects=dict(runtime_commit.get("pet_redirects", {})),
        )
        return True

    def _finish_runtime_backed_recovery(self, repository, operation, payload) -> None:
        if operation.kind == "pet_recluster":
            clustering_version = str(operation.payload.get("clustering_pipeline_version") or "")
            if clustering_version:
                repository.set_scan_metadata(
                    "clustering_pipeline_version",
                    clustering_version,
                )
        elif operation.kind == "pet_merge":
            source_id = str(payload.get("source_pet_id") or "")
            target_id = str(payload.get("target_pet_id") or "")
            if source_id and target_id:
                repository.recover_pet_merge_people_groups(source_id, target_id)
        elif operation.kind in {
            "pet_overlap_reconcile",
            "pet_cluster_consolidate",
        }:
            repository.refresh_people_group_assets_for_pets(
                str(value)
                for value in (payload.get("affected_pet_ids") or payload.get("changed_pet_ids", ()))
                if value
            )

        previous_paths = tuple(
            str(value) for value in payload.get("previous_thumbnail_paths", ()) if value
        )
        if previous_paths:
            repository.prune_unreferenced_thumbnails(previous_paths)
        event_payload = {
            "changed_asset_ids": list(payload.get("changed_asset_ids", ())),
            "changed_pet_ids": list(payload.get("changed_pet_ids", ())),
            "added_pet_ids": list(payload.get("added_pet_ids", ())),
            "updated_pet_ids": list(payload.get("updated_pet_ids", ())),
            "removed_pet_ids": list(payload.get("removed_pet_ids", ())),
            "pet_redirects": dict(payload.get("pet_redirects", {})),
        }
        event = self._emit_snapshot(
            operation_id=operation.operation_id,
            changed_asset_ids=tuple(event_payload["changed_asset_ids"]),
            changed_pet_ids=tuple(event_payload["changed_pet_ids"]),
            added_pet_ids=tuple(event_payload["added_pet_ids"]),
            updated_pet_ids=tuple(event_payload["updated_pet_ids"]),
            removed_pet_ids=tuple(event_payload["removed_pet_ids"]),
            pet_redirects=event_payload["pet_redirects"],
            dispatch=False,
        )
        self._journal.commit_and_dispatch(
            operation.operation_id,
            event_payload,
            lambda: self._scheduleEmit.emit(event),
        )

    def _ensure_recovered_locked(self) -> bool:
        if not self._journal.recover_pending():
            self._recovery_error = self._journal.recovery_error
            LOGGER.error(
                "Pets recognition recovery is incomplete for %s",
                self._library_root,
            )
            return False
        self._recovery_error = None
        return True

    def _recover_pet_mutation(self, operation) -> bool:
        if operation.kind == "recognition_detection_assignment":
            payload = operation.payload
            succeeded = apply_detection_assignment_with_group_refresh(
                self._library_root,
                payload,
            )
            if succeeded:
                self._journal.commit_and_dispatch(
                    operation.operation_id,
                    {"kind": operation.kind, **payload},
                    lambda: None,
                )
            else:
                self._journal.transition(
                    operation.operation_id,
                    "finalized",
                    error="assignment_recovery_rejected",
                )
            return True
        if operation.kind not in {
            "pet_rename",
            "pet_hide",
            "pet_cover",
            "pet_merge",
            "pet_delete_detection",
            "pet_move_detection",
            "pet_move_detection_new",
        }:
            return False
        repository = self._repository()
        payload = operation.payload
        changed_asset_ids: tuple[str, ...] = ()
        changed_pet_ids: tuple[str, ...] = ()
        redirects: dict[str, str] = {}
        succeeded = False
        if operation.kind == "pet_rename":
            pet_id = str(payload.get("pet_id") or "")
            succeeded = repository.rename_pet(pet_id, payload.get("name"))
            changed_pet_ids = (pet_id,)
            changed_asset_ids = tuple(repository.get_asset_ids_by_pet(pet_id))
        elif operation.kind == "pet_hide":
            pet_id = str(payload.get("pet_id") or "")
            succeeded = repository.set_pet_hidden(pet_id, bool(payload.get("hidden")))
            changed_pet_ids = (pet_id,)
            changed_asset_ids = tuple(repository.get_asset_ids_by_pet(pet_id))
        elif operation.kind == "pet_cover":
            pet_id = str(payload.get("pet_id") or "")
            succeeded = repository.set_pet_cover(
                pet_id,
                str(payload.get("detection_id") or ""),
            )
            changed_pet_ids = (pet_id,)
            changed_asset_ids = tuple(repository.get_asset_ids_by_pet(pet_id))
        elif operation.kind == "pet_merge":
            source_id = str(payload.get("source_pet_id") or "")
            target_id = str(payload.get("target_pet_id") or "")
            result = repository.merge_pets(
                source_id,
                target_id,
                operation_id=operation.operation_id,
            )
            succeeded = result is not None
            if result is not None:
                changed_asset_ids = result.changed_asset_ids
                changed_pet_ids = result.changed_pet_ids
                redirects = result.pet_redirects
        elif operation.kind == "pet_delete_detection":
            result = repository.delete_detection(
                str(payload.get("detection_id") or ""),
                operation_id=operation.operation_id,
            )
            succeeded = True
            if result is not None:
                changed_asset_ids = result.changed_asset_ids
                changed_pet_ids = result.changed_pet_ids
            else:
                repository.sync_runtime_state()
        elif operation.kind == "pet_move_detection":
            result = repository.move_detection_to_pet(
                str(payload.get("detection_id") or ""),
                str(payload.get("target_pet_id") or ""),
                operation_id=operation.operation_id,
            )
            succeeded = result is not None
            if result is not None:
                changed_asset_ids = result.changed_asset_ids
                changed_pet_ids = result.changed_pet_ids
        elif operation.kind == "pet_move_detection_new":
            result = repository.move_detection_to_new_pet(
                str(payload.get("detection_id") or ""),
                str(payload.get("new_pet_id") or ""),
                payload.get("new_name"),
                operation_id=operation.operation_id,
            )
            succeeded = result is not None
            if result is not None:
                changed_asset_ids = result.changed_asset_ids
                changed_pet_ids = result.changed_pet_ids
        if not succeeded:
            self._journal.transition(
                operation.operation_id,
                "finalized",
                payload=payload,
                error="recovery_rejected",
            )
            return True
        self._emit_journaled_snapshot(
            operation.operation_id,
            changed_asset_ids=changed_asset_ids,
            changed_pet_ids=changed_pet_ids,
            pet_redirects=redirects,
        )
        return True

    @staticmethod
    def _publish_staged_thumbnails(
        staged_dir: Path | None,
        published_dir: Path | None,
    ) -> tuple[Path, ...]:
        if staged_dir is None or published_dir is None or not staged_dir.is_dir():
            return ()
        published_dir.mkdir(parents=True, exist_ok=True)
        published: list[Path] = []
        try:
            for source in sorted(staged_dir.iterdir()):
                if not source.is_file():
                    continue
                target = published_dir / source.name
                source.replace(target)
                published.append(target)
        except Exception:
            PetIndexCoordinator._cleanup_thumbnail_paths(reversed(published))
            raise
        try:
            staged_dir.rmdir()
        except OSError:
            pass
        return tuple(published)

    @staticmethod
    def _planned_thumbnail_targets(
        staged_dir: Path | None,
        published_dir: Path | None,
    ) -> tuple[Path, ...]:
        if staged_dir is None or published_dir is None or not staged_dir.is_dir():
            return ()
        return tuple(
            published_dir / source.name
            for source in sorted(staged_dir.iterdir())
            if source.is_file()
        )

    @staticmethod
    def _cleanup_thumbnail_paths(paths: Iterable[Path]) -> None:
        for path in paths:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                LOGGER.warning(
                    "Failed to clean unpublished pet thumbnail %s",
                    path,
                    exc_info=True,
                )

    @staticmethod
    def _cleanup_staging_dir(staged_dir: Path) -> None:
        if not staged_dir.is_dir():
            return
        PetIndexCoordinator._cleanup_thumbnail_paths(
            path for path in staged_dir.iterdir() if path.is_file()
        )
        try:
            staged_dir.rmdir()
        except OSError:
            LOGGER.warning("Failed to clean pet staging directory %s", staged_dir)


def _legacy_pet_identity_id(value: object) -> str | None:
    text = str(value or "").strip()
    kind, separator, entity_id = text.partition(":")
    if separator != ":" or kind != "pet" or not entity_id.strip():
        return None
    return entity_id.strip()


def get_pet_index_coordinator(
    library_root: Path,
    *,
    asset_repository: PetAssetRepositoryPort | None = None,
    mutation_coordinator: RecognitionMutationCoordinator | None = None,
) -> PetIndexCoordinator:
    coordinator = PetIndexCoordinator(
        Path(library_root).resolve(),
        asset_repository=asset_repository,
        mutation_coordinator=mutation_coordinator,
    )
    app = QCoreApplication.instance()
    if app is not None:
        coordinator.moveToThread(app.thread())
    return coordinator


def reset_pet_index_coordinators() -> None:
    """Compatibility no-op; coordinators are session-owned."""


__all__ = [
    "PetIndexCoordinator",
    "PetSnapshotCommittedError",
    "PetSnapshotEvent",
    "get_pet_index_coordinator",
    "reset_pet_index_coordinators",
]

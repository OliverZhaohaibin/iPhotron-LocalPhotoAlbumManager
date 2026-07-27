"""Thread-safe coordinator for realtime Pets snapshot updates."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QObject, Qt, Signal, Slot

from iPhoto.application.ports.pets import PetAssetRepositoryPort
from iPhoto.recognition.operation_journal import RecognitionOperationJournal
from iPhoto.utils.logging import get_logger
from iPhoto.utils.pathutils import ensure_work_dir

from .pipeline import (
    DEFAULT_PET_DISTANCE_THRESHOLD,
    DetectedAssetPets,
    _pet_box_overlaps_people_boxes,
)
from .repository import PetRepository
from .scan_session import PetScanSession
from .status import PET_STATUS_DONE, PET_STATUS_RETRY

LOGGER = get_logger()


class PetSnapshotCommittedError(RuntimeError):
    """Raised when the Pets snapshot committed but bookkeeping failed."""


@dataclass(frozen=True)
class PetSnapshotEvent:
    library_root: Path
    revision: int
    operation_id: str | None = None
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
    ) -> None:
        super().__init__()
        self._library_root = Path(library_root)
        self._asset_repository = asset_repository
        self._lock = threading.RLock()
        self._revision = 0
        self._shutdown_requested = False
        self._journal = RecognitionOperationJournal(
            ensure_work_dir(self._library_root) / "recognition" / "operations.db"
        )
        self._scheduleEmit.connect(self._fire_snapshot, Qt.ConnectionType.QueuedConnection)

    @Slot(object)
    def _fire_snapshot(self, event: object) -> None:
        self.snapshotCommitted.emit(event)

    @property
    def library_root(self) -> Path:
        return self._library_root

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
        clustering_pipeline_version: str | None = None,
        people_boxes_provider: Callable[
            [Iterable[str]],
            dict[str, tuple[tuple[int, int, int, int], ...]],
        ]
        | None = None,
        staged_thumbnail_dir: Path | None = None,
        published_thumbnail_dir: Path | None = None,
    ) -> PetSnapshotEvent | None:
        detected_batch = list(detected_results)
        if not detected_batch:
            return None

        with self._lock:
            if self._shutdown_requested:
                return None
            self._recover_operations_locked()
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
                        if _pet_box_overlaps_people_boxes(
                            (
                                detection.box_x,
                                detection.box_y,
                                detection.box_w,
                                detection.box_h,
                            ),
                            people_boxes.get(result.asset_id, ()),
                        ):
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
            session = PetScanSession()
            done_ids, retry_ids = session.stage_detection_results(detected_batch)
            store = self._asset_repository
            if retry_ids and store is not None:
                store.update_pet_statuses(retry_ids, PET_STATUS_RETRY)
            stale_pet_ids = repository.mark_asset_detections_stale(
                retry_ids,
                reason="asset_scan_failed_in_current_generation",
            )
            if not done_ids:
                if not stale_pet_ids:
                    return None
                return self._emit_snapshot(
                    changed_asset_ids=tuple(retry_ids),
                    updated_pet_ids=stale_pet_ids,
                )

            operation_payload = {
                "done_asset_ids": list(done_ids),
                "retry_asset_ids": list(retry_ids),
                "index_applied": False,
                "staged_thumbnail_dir": (
                    str(staged_thumbnail_dir) if staged_thumbnail_dir is not None else None
                ),
            }
            operation_id = self._journal.prepare("pet_scan_commit", operation_payload)
            self._journal.transition(operation_id, "applying")
            published_thumbnail_paths = self._publish_staged_thumbnails(
                staged_thumbnail_dir,
                published_thumbnail_dir,
            )

            staged_detections = [
                detection
                for result in detected_batch
                if not result.error
                for detection in result.detections
            ]
            staged_detections, generation_id = repository.assign_embedding_generation(
                staged_detections
            )
            try:
                commit_result = repository.replace_assets_incrementally(
                    done_ids,
                    staged_detections,
                    distance_threshold=distance_threshold,
                )
            except Exception as exc:
                for thumbnail_path in published_thumbnail_paths:
                    try:
                        thumbnail_path.unlink(missing_ok=True)
                    except OSError:
                        LOGGER.warning(
                            "Failed to clean unpublished pet thumbnail %s",
                            thumbnail_path,
                            exc_info=True,
                        )
                self._journal.transition(
                    operation_id,
                    "finalized",
                    payload=operation_payload,
                    error=str(exc),
                )
                raise
            operation_payload.update(
                {
                    "index_applied": True,
                    "generation_id": generation_id,
                    "added_pet_ids": list(commit_result.added_pet_ids),
                    "updated_pet_ids": list(
                        dict.fromkeys(commit_result.updated_pet_ids + stale_pet_ids)
                    ),
                    "removed_pet_ids": list(commit_result.removed_pet_ids),
                }
            )
            self._journal.transition(
                operation_id,
                "applying",
                payload=operation_payload,
            )
            if detector_pipeline_version:
                repository.set_scan_metadata(
                    "detector_pipeline_version",
                    detector_pipeline_version,
                )
            if clustering_pipeline_version:
                repository.set_scan_metadata(
                    "clustering_pipeline_version",
                    clustering_pipeline_version,
                )
            if staged_detections:
                repository.activate_embedding_generation(
                    generation_id=generation_id,
                    embedding_pipeline_version=(
                        staged_detections[0].embedding_pipeline_version
                    ),
                    embedding_dimension=staged_detections[0].embedding_dim,
                )
            try:
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
            outbox_payload = {
                "generation_id": generation_id,
                "changed_asset_ids": list(done_ids + retry_ids),
                "added_pet_ids": list(commit_result.added_pet_ids),
                "updated_pet_ids": list(
                    dict.fromkeys(commit_result.updated_pet_ids + stale_pet_ids)
                ),
                "removed_pet_ids": list(commit_result.removed_pet_ids),
            }
            self._journal.commit_outbox(operation_id, outbox_payload)
            event = self._emit_snapshot(
                operation_id=operation_id,
                generation_id=generation_id,
                changed_asset_ids=tuple(done_ids + retry_ids),
                added_pet_ids=commit_result.added_pet_ids,
                updated_pet_ids=tuple(
                    dict.fromkeys(commit_result.updated_pet_ids + stale_pet_ids)
                ),
                removed_pet_ids=commit_result.removed_pet_ids,
            )
            repository.prune_unreferenced_thumbnails(
                commit_result.previous_thumbnail_paths
            )
            repository.prune_unreferenced_thumbnails(filtered_thumbnail_paths)
            self._journal.mark_published(operation_id)
            return event

    def rename_pet(self, pet_id: str, name_or_none: str | None) -> PetSnapshotEvent | None:
        if not pet_id:
            return None
        with self._lock:
            if self._shutdown_requested:
                return None
            repository = self._repository()
            operation_id = self._journal.prepare(
                "pet_rename",
                {"pet_id": pet_id, "name": name_or_none},
            )
            self._journal.transition(operation_id, "applying")
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
            repository = self._repository()
            operation_id = self._journal.prepare(
                "pet_hide",
                {"pet_id": pet_id, "hidden": bool(hidden)},
            )
            self._journal.transition(operation_id, "applying")
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
            repository = self._repository()
            operation_id = self._journal.prepare(
                "pet_cover",
                {"pet_id": pet_id, "detection_id": detection_id},
            )
            self._journal.transition(operation_id, "applying")
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

    def merge_pets(self, source_pet_id: str, target_pet_id: str) -> bool:
        with self._lock:
            if self._shutdown_requested:
                return False
            repository = self._repository()
            operation_id = self._journal.prepare(
                "pet_merge",
                {"source_pet_id": source_pet_id, "target_pet_id": target_pet_id},
            )
            self._journal.transition(operation_id, "applying")
            result = repository.merge_pets(source_pet_id, target_pet_id)
            if result is None:
                self._journal.transition(
                    operation_id,
                    "finalized",
                    error="merge_rejected",
                )
                return False
            self._emit_journaled_snapshot(
                operation_id,
                changed_asset_ids=result.changed_asset_ids,
                changed_pet_ids=result.changed_pet_ids,
                pet_redirects=result.pet_redirects,
            )
            return True

    def recluster_for_pipeline_upgrade(
        self,
        *,
        clustering_pipeline_version: str,
        distance_threshold: float,
    ) -> int:
        """Serialize a version-gated recluster with every other Pet mutation."""

        with self._lock:
            if self._shutdown_requested:
                return 0
            repository = self._repository()
            previous_version = repository.get_scan_metadata(
                "clustering_pipeline_version"
            )
            if previous_version == clustering_pipeline_version:
                return 0
            previous_detections = repository.get_all_detections()
            reclustered_count = repository.recluster_detections(
                distance_threshold=distance_threshold,
            )
            repository.set_scan_metadata(
                "clustering_pipeline_version",
                clustering_pipeline_version,
            )
            if reclustered_count:
                self._emit_snapshot(
                    changed_asset_ids=tuple(
                        dict.fromkeys(
                            detection.asset_id
                            for detection in previous_detections
                            if detection.asset_id
                        )
                    ),
                    changed_pet_ids=tuple(
                        pet.pet_id for pet in repository.get_all_pet_records()
                    ),
                )
                LOGGER.info(
                    "Reclustered %d pet detections for clustering pipeline upgrade "
                    "%s -> %s in %s",
                    reclustered_count,
                    previous_version or "<missing>",
                    clustering_pipeline_version,
                    self._library_root,
                )
            return reclustered_count

    def delete_detection(self, detection_id: str) -> PetSnapshotEvent | None:
        with self._lock:
            if self._shutdown_requested:
                return None
            repository = self._repository()
            operation_id = self._journal.prepare(
                "pet_delete_detection",
                {"detection_id": detection_id},
            )
            self._journal.transition(operation_id, "applying")
            result = repository.delete_detection(detection_id)
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
            repository = self._repository()
            scoped_asset_ids = tuple(people_boxes_by_asset_id)
            previous_detections = repository.get_detections_by_asset_ids(
                scoped_asset_ids
            )
            removed = [
                detection
                for detection in previous_detections
                if _pet_box_overlaps_people_boxes(
                    (
                        detection.box_x,
                        detection.box_y,
                        detection.box_w,
                        detection.box_h,
                    ),
                    people_boxes_by_asset_id.get(detection.asset_id, ()),
                )
            ]
            if not removed:
                return None

            removed_ids = {detection.detection_id for detection in removed}
            retained = [
                detection
                for detection in previous_detections
                if detection.detection_id not in removed_ids
            ]
            commit_result = repository.replace_assets_incrementally(
                scoped_asset_ids,
                retained,
                distance_threshold=distance_threshold,
            )
            changed_asset_ids = tuple(
                dict.fromkeys(detection.asset_id for detection in removed if detection.asset_id)
            )
            repository.prune_unreferenced_thumbnails(
                commit_result.previous_thumbnail_paths
            )
            return self._emit_snapshot(
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
            if self._shutdown_requested:
                return None
            repository = self._repository()
            operation_id = self._journal.prepare(
                "pet_move_detection",
                {"detection_id": detection_id, "target_pet_id": target_pet_id},
            )
            self._journal.transition(operation_id, "applying")
            result = repository.move_detection_to_pet(detection_id, target_pet_id)
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

    def move_detection_to_new_pet(
        self,
        detection_id: str,
        new_pet_id: str,
        new_name: str | None,
    ) -> PetSnapshotEvent | None:
        with self._lock:
            if self._shutdown_requested:
                return None
            repository = self._repository()
            operation_id = self._journal.prepare(
                "pet_move_detection_new",
                {
                    "detection_id": detection_id,
                    "new_pet_id": new_pet_id,
                    "new_name": new_name,
                },
            )
            self._journal.transition(operation_id, "applying")
            result = repository.move_detection_to_new_pet(detection_id, new_pet_id, new_name)
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

    def _repository(self) -> PetRepository:
        pets_root = ensure_work_dir(self._library_root) / "pets"
        return PetRepository(
            pets_root / "pet_index.db",
            pets_root / "pet_state.db",
        )

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
    ) -> PetSnapshotEvent:
        self._revision += 1
        changed_pet_ids = tuple(
            dict.fromkeys(
                changed_pet_ids
                + added_pet_ids
                + updated_pet_ids
                + removed_pet_ids
            )
        )
        event = PetSnapshotEvent(
            library_root=self._library_root,
            revision=self._revision,
            operation_id=operation_id,
            generation_id=generation_id,
            changed_asset_ids=tuple(dict.fromkeys(changed_asset_ids)),
            added_pet_ids=tuple(dict.fromkeys(added_pet_ids)),
            updated_pet_ids=tuple(dict.fromkeys(updated_pet_ids)),
            removed_pet_ids=tuple(dict.fromkeys(removed_pet_ids)),
            changed_pet_ids=tuple(dict.fromkeys(changed_pet_ids)),
            pet_redirects=dict(pet_redirects or {}),
        )
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
        self._journal.commit_outbox(operation_id, outbox_payload)
        event = self._emit_snapshot(operation_id=operation_id, **event_fields)
        self._journal.mark_published(operation_id)
        return event

    def _mark_done_asset_ids(self, done_ids: list[str]) -> None:
        if not done_ids:
            return
        store = self._asset_repository
        if store is None:
            return
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                store.update_pet_statuses(done_ids, PET_STATUS_DONE)
                return
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt == 2:
                    break
                time.sleep(0.05 * (attempt + 1))
        if last_error is not None:
            raise last_error

    def _recover_operations_locked(self) -> None:
        for operation in self._journal.unfinished():
            if operation.kind != "pet_scan_commit":
                self._recover_pet_mutation(operation)
                continue
            payload = operation.payload
            if not bool(payload.get("index_applied")):
                self._journal.transition(
                    operation.operation_id,
                    "finalized",
                    payload=payload,
                    error="superseded_before_index_commit",
                )
                continue
            done_ids = [str(value) for value in payload.get("done_asset_ids", ()) if value]
            self._mark_done_asset_ids(done_ids)
            event_payload = {
                "changed_asset_ids": [
                    *done_ids,
                    *[
                        str(value)
                        for value in payload.get("retry_asset_ids", ())
                        if value
                    ],
                ],
                "added_pet_ids": list(payload.get("added_pet_ids", ())),
                "updated_pet_ids": list(payload.get("updated_pet_ids", ())),
                "removed_pet_ids": list(payload.get("removed_pet_ids", ())),
            }
            self._journal.commit_outbox(operation.operation_id, event_payload)
            self._emit_snapshot(
                operation_id=operation.operation_id,
                generation_id=int(payload.get("generation_id") or 0),
                changed_asset_ids=tuple(event_payload["changed_asset_ids"]),
                added_pet_ids=tuple(event_payload["added_pet_ids"]),
                updated_pet_ids=tuple(event_payload["updated_pet_ids"]),
                removed_pet_ids=tuple(event_payload["removed_pet_ids"]),
            )
            self._journal.mark_published(operation.operation_id)

    def _recover_pet_mutation(self, operation) -> None:
        if operation.kind not in {
            "pet_rename",
            "pet_hide",
            "pet_cover",
            "pet_merge",
            "pet_delete_detection",
            "pet_move_detection",
            "pet_move_detection_new",
        }:
            return
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
            result = repository.merge_pets(source_id, target_id)
            succeeded = result is not None
            if result is not None:
                changed_asset_ids = result.changed_asset_ids
                changed_pet_ids = result.changed_pet_ids
                redirects = result.pet_redirects
        elif operation.kind == "pet_delete_detection":
            result = repository.delete_detection(
                str(payload.get("detection_id") or "")
            )
            succeeded = True
            if result is not None:
                changed_asset_ids = result.changed_asset_ids
                changed_pet_ids = result.changed_pet_ids
        elif operation.kind == "pet_move_detection":
            result = repository.move_detection_to_pet(
                str(payload.get("detection_id") or ""),
                str(payload.get("target_pet_id") or ""),
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
            return
        self._emit_journaled_snapshot(
            operation.operation_id,
            changed_asset_ids=changed_asset_ids,
            changed_pet_ids=changed_pet_ids,
            pet_redirects=redirects,
        )

    @staticmethod
    def _publish_staged_thumbnails(
        staged_dir: Path | None,
        published_dir: Path | None,
    ) -> tuple[Path, ...]:
        if staged_dir is None or published_dir is None or not staged_dir.is_dir():
            return ()
        published_dir.mkdir(parents=True, exist_ok=True)
        published: list[Path] = []
        for source in sorted(staged_dir.iterdir()):
            if not source.is_file():
                continue
            target = published_dir / source.name
            source.replace(target)
            published.append(target)
        try:
            staged_dir.rmdir()
        except OSError:
            pass
        return tuple(published)


_COORDINATORS: dict[Path, PetIndexCoordinator] = {}
_COORDINATORS_LOCK = threading.Lock()


def get_pet_index_coordinator(
    library_root: Path,
    *,
    asset_repository: PetAssetRepositoryPort | None = None,
) -> PetIndexCoordinator:
    resolved = Path(library_root).resolve()
    with _COORDINATORS_LOCK:
        coordinator = _COORDINATORS.get(resolved)
        if coordinator is None:
            coordinator = PetIndexCoordinator(resolved, asset_repository=asset_repository)
            app = QCoreApplication.instance()
            if app is not None:
                coordinator.moveToThread(app.thread())
            _COORDINATORS[resolved] = coordinator
        else:
            if asset_repository is not None:
                coordinator.set_asset_repository(asset_repository)
            coordinator.resume()
        return coordinator


def reset_pet_index_coordinators() -> None:
    with _COORDINATORS_LOCK:
        _COORDINATORS.clear()


__all__ = [
    "PetIndexCoordinator",
    "PetSnapshotCommittedError",
    "PetSnapshotEvent",
    "get_pet_index_coordinator",
    "reset_pet_index_coordinators",
]

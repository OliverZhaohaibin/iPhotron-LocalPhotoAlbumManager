"""Thread-safe coordinator for realtime Pets snapshot updates."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QObject, Qt, Signal, Slot

from iPhoto.application.ports.pets import PetAssetRepositoryPort
from iPhoto.utils.logging import get_logger
from iPhoto.utils.pathutils import ensure_work_dir

from .pipeline import DetectedAssetPets
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
    changed_asset_ids: tuple[str, ...] = ()
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
        min_samples: int,
        detector_pipeline_version: str | None = None,
    ) -> PetSnapshotEvent | None:
        detected_batch = list(detected_results)
        if not detected_batch:
            return None

        with self._lock:
            if self._shutdown_requested:
                return None
            repository = self._repository()
            session = PetScanSession()
            done_ids, retry_ids = session.stage_detection_results(detected_batch)
            store = self._asset_repository
            if retry_ids and store is not None:
                store.update_pet_statuses(retry_ids, PET_STATUS_RETRY)
            if not done_ids:
                return None

            previous_detections = repository.get_all_detections()
            detections, pets = session.build_runtime_snapshot(
                repository,
                distance_threshold=distance_threshold,
                min_samples=min_samples,
                existing_detections=previous_detections,
            )
            done_id_set = set(done_ids)
            changed_pet_ids = tuple(
                sorted(
                    {
                        str(detection.pet_id)
                        for detection in detections
                        if detection.pet_id and detection.asset_id in done_id_set
                    }
                )
            )
            session.commit(repository, detections=detections, pets=pets)
            if detector_pipeline_version:
                repository.set_scan_metadata(
                    "detector_pipeline_version",
                    detector_pipeline_version,
                )
            event = self._emit_snapshot(
                changed_asset_ids=tuple(done_ids + retry_ids),
                changed_pet_ids=changed_pet_ids,
            )

        try:
            self._mark_done_asset_ids(done_ids)
            return event
        except Exception as exc:
            LOGGER.error(
                "Pets snapshot committed for %s, but post-commit bookkeeping failed: %s",
                self._library_root,
                exc,
                exc_info=True,
            )
            raise PetSnapshotCommittedError(
                "Pet scan committed, but updating scan bookkeeping failed."
            ) from exc

    def rename_pet(self, pet_id: str, name_or_none: str | None) -> PetSnapshotEvent | None:
        if not pet_id:
            return None
        with self._lock:
            if self._shutdown_requested:
                return None
            repository = self._repository()
            if not repository.rename_pet(pet_id, name_or_none):
                return None
            return self._emit_snapshot(
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
            changed = repository.set_pet_hidden(pet_id, hidden)
            if changed:
                self._emit_snapshot(
                    changed_asset_ids=tuple(repository.get_asset_ids_by_pet(pet_id)),
                    changed_pet_ids=(pet_id,),
                )
            return changed

    def set_pet_cover(self, pet_id: str, detection_id: str) -> bool:
        if not pet_id or not detection_id:
            return False
        with self._lock:
            if self._shutdown_requested:
                return False
            repository = self._repository()
            changed = repository.set_pet_cover(pet_id, detection_id)
            if changed:
                self._emit_snapshot(
                    changed_asset_ids=tuple(repository.get_asset_ids_by_pet(pet_id)),
                    changed_pet_ids=(pet_id,),
                )
            return changed

    def merge_pets(self, source_pet_id: str, target_pet_id: str) -> bool:
        with self._lock:
            if self._shutdown_requested:
                return False
            repository = self._repository()
            result = repository.merge_pets(source_pet_id, target_pet_id)
            if result is None:
                return False
            self._emit_snapshot(
                changed_asset_ids=result.changed_asset_ids,
                changed_pet_ids=result.changed_pet_ids,
                pet_redirects=result.pet_redirects,
            )
            return True

    def delete_detection(self, detection_id: str) -> PetSnapshotEvent | None:
        with self._lock:
            if self._shutdown_requested:
                return None
            repository = self._repository()
            result = repository.delete_detection(detection_id)
            if result is None:
                return None
            return self._emit_snapshot(
                changed_asset_ids=result.changed_asset_ids,
                changed_pet_ids=result.changed_pet_ids,
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
            result = repository.move_detection_to_pet(detection_id, target_pet_id)
            if result is None:
                return None
            return self._emit_snapshot(
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
            result = repository.move_detection_to_new_pet(detection_id, new_pet_id, new_name)
            if result is None:
                return None
            return self._emit_snapshot(
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
        changed_asset_ids: tuple[str, ...] = (),
        changed_pet_ids: tuple[str, ...] = (),
        pet_redirects: dict[str, str] | None = None,
    ) -> PetSnapshotEvent:
        self._revision += 1
        event = PetSnapshotEvent(
            library_root=self._library_root,
            revision=self._revision,
            changed_asset_ids=tuple(dict.fromkeys(changed_asset_ids)),
            changed_pet_ids=tuple(dict.fromkeys(changed_pet_ids)),
            pet_redirects=dict(pet_redirects or {}),
        )
        self._scheduleEmit.emit(event)
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

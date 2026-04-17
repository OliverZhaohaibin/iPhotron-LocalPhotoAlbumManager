"""Thread-safe coordinator for realtime People snapshot updates."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import threading
from typing import Iterable

from PySide6.QtCore import QObject, Qt, Signal, Slot

from iPhoto.cache.index_store import get_global_repository
from iPhoto.config import WORK_DIR_NAME

from .pipeline import DetectedAssetFaces
from .repository import FaceRepository, PeopleGroupRecord
from .scan_session import FaceScanSession
from .status import FACE_STATUS_DONE, FACE_STATUS_RETRY


@dataclass(frozen=True)
class PeopleSnapshotEvent:
    library_root: Path
    revision: int
    changed_asset_ids: tuple[str, ...] = ()
    changed_person_ids: tuple[str, ...] = ()
    changed_group_ids: tuple[str, ...] = ()
    person_redirects: dict[str, str] = field(default_factory=dict)
    group_redirects: dict[str, str | None] = field(default_factory=dict)


class PeopleIndexCoordinator(QObject):
    """Serialize People writes and publish committed snapshot revisions."""

    snapshotCommitted = Signal(object)
    # Internal signal used to marshal snapshot emission back onto the
    # coordinator's own (main) thread, even when _emit_snapshot() is called
    # from a background worker thread.
    _scheduleEmit = Signal(object)

    def __init__(self, library_root: Path) -> None:
        super().__init__()
        self._library_root = Path(library_root)
        self._lock = threading.RLock()
        self._revision = 0
        self._shutdown_requested = False
        # QueuedConnection ensures _fire_snapshot() runs on the coordinator's
        # own thread regardless of which thread calls _emit_snapshot().
        self._scheduleEmit.connect(self._fire_snapshot, Qt.ConnectionType.QueuedConnection)

    @Slot(object)
    def _fire_snapshot(self, event: object) -> None:
        self.snapshotCommitted.emit(event)

    @property
    def library_root(self) -> Path:
        return self._library_root

    def submit_detected_batch(
        self,
        detected_results: Iterable[DetectedAssetFaces],
        *,
        distance_threshold: float,
        min_samples: int,
    ) -> PeopleSnapshotEvent | None:
        detected_batch = list(detected_results)
        if not detected_batch:
            return None

        with self._lock:
            if self._shutdown_requested:
                return None
            repository = self._repository()
            session = FaceScanSession()
            done_ids, retry_ids = session.stage_detection_results(detected_batch)
            store = get_global_repository(self._library_root)
            if retry_ids:
                store.update_face_statuses(retry_ids, FACE_STATUS_RETRY)
            if not done_ids:
                return None

            session.commit(
                repository,
                distance_threshold=distance_threshold,
                min_samples=min_samples,
            )
            store.update_face_statuses(done_ids, FACE_STATUS_DONE)
            changed_person_ids = tuple(
                repository.get_person_ids_for_asset_ids(done_ids)
            )
            return self._emit_snapshot(
                changed_asset_ids=tuple(done_ids + retry_ids),
                changed_person_ids=changed_person_ids,
            )

    def rename_person(self, person_id: str, name_or_none: str | None) -> PeopleSnapshotEvent | None:
        if not person_id:
            return None
        with self._lock:
            if self._shutdown_requested:
                return None
            repository = self._repository()
            repository.rename_person(person_id, name_or_none)
            return self._emit_snapshot(
                changed_asset_ids=tuple(repository.get_asset_ids_by_person(person_id)),
                changed_person_ids=(person_id,),
            )

    def set_person_cover(self, person_id: str, face_id: str) -> bool:
        if not person_id or not face_id:
            return False
        with self._lock:
            if self._shutdown_requested:
                return False
            repository = self._repository()
            changed = repository.set_person_cover(person_id, face_id)
            if changed:
                self._emit_snapshot(
                    changed_asset_ids=tuple(repository.get_asset_ids_by_person(person_id)),
                    changed_person_ids=(person_id,),
                )
            return changed

    def set_person_order(self, person_ids: Iterable[str]) -> PeopleSnapshotEvent | None:
        ordered_ids = tuple(str(person_id) for person_id in person_ids if person_id)
        with self._lock:
            if self._shutdown_requested:
                return None
            repository = self._repository()
            repository.set_person_order(ordered_ids)
            if not ordered_ids:
                return None
            return self._emit_snapshot(changed_person_ids=ordered_ids)

    def merge_persons(
        self,
        source_person_id: str,
        target_person_id: str,
    ) -> bool:
        if not source_person_id or not target_person_id:
            return False
        with self._lock:
            if self._shutdown_requested:
                return False
            repository = self._repository()
            merged, group_redirects = repository.merge_persons_with_redirects(
                source_person_id,
                target_person_id,
            )
            if not merged:
                return False
            affected_group_ids = tuple(
                group_id
                for group_id in set(group_redirects.values()) | set(group_redirects.keys())
                if group_id
            )
            self._emit_snapshot(
                changed_asset_ids=tuple(repository.get_asset_ids_by_person(target_person_id)),
                changed_person_ids=(source_person_id, target_person_id),
                changed_group_ids=affected_group_ids,
                person_redirects={source_person_id: target_person_id},
                group_redirects=group_redirects,
            )
            return True

    def create_group(
        self,
        member_person_ids: Iterable[str],
    ) -> PeopleGroupRecord | None:
        with self._lock:
            if self._shutdown_requested:
                return None
            repository = self._repository()
            group = repository.create_group(member_person_ids)
            if group is not None:
                self._emit_snapshot(
                    changed_asset_ids=tuple(repository.get_common_asset_ids_for_group(group.group_id)),
                    changed_person_ids=tuple(group.member_person_ids),
                    changed_group_ids=(group.group_id,),
                )
            return group

    def set_group_cover(self, group_id: str, asset_id: str) -> bool:
        if not group_id or not asset_id:
            return False
        with self._lock:
            if self._shutdown_requested:
                return False
            repository = self._repository()
            changed = repository.set_group_cover_asset(group_id, asset_id)
            if changed:
                self._emit_snapshot(
                    changed_asset_ids=(asset_id,),
                    changed_group_ids=(group_id,),
                )
            return changed

    def _repository(self) -> FaceRepository:
        faces_root = self._library_root / WORK_DIR_NAME / "faces"
        return FaceRepository(
            faces_root / "face_index.db",
            faces_root / "face_state.db",
        )

    def begin_shutdown(self) -> None:
        with self._lock:
            self._shutdown_requested = True

    def resume(self) -> None:
        with self._lock:
            self._shutdown_requested = False

    def _emit_snapshot(
        self,
        *,
        changed_asset_ids: tuple[str, ...] = (),
        changed_person_ids: tuple[str, ...] = (),
        changed_group_ids: tuple[str, ...] = (),
        person_redirects: dict[str, str] | None = None,
        group_redirects: dict[str, str | None] | None = None,
    ) -> PeopleSnapshotEvent:
        self._revision += 1
        event = PeopleSnapshotEvent(
            library_root=self._library_root,
            revision=self._revision,
            changed_asset_ids=tuple(dict.fromkeys(changed_asset_ids)),
            changed_person_ids=tuple(dict.fromkeys(changed_person_ids)),
            changed_group_ids=tuple(dict.fromkeys(changed_group_ids)),
            person_redirects=dict(person_redirects or {}),
            group_redirects=dict(group_redirects or {}),
        )
        self._scheduleEmit.emit(event)
        return event


_COORDINATORS: dict[Path, PeopleIndexCoordinator] = {}
_COORDINATORS_LOCK = threading.Lock()


def get_people_index_coordinator(library_root: Path) -> PeopleIndexCoordinator:
    resolved = Path(library_root).resolve()
    with _COORDINATORS_LOCK:
        coordinator = _COORDINATORS.get(resolved)
        if coordinator is None:
            coordinator = PeopleIndexCoordinator(resolved)
            _COORDINATORS[resolved] = coordinator
        else:
            coordinator.resume()
        return coordinator


def reset_people_index_coordinators() -> None:
    with _COORDINATORS_LOCK:
        _COORDINATORS.clear()


__all__ = [
    "PeopleIndexCoordinator",
    "PeopleSnapshotEvent",
    "get_people_index_coordinator",
    "reset_people_index_coordinators",
]

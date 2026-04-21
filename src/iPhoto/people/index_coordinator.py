"""Thread-safe coordinator for realtime People snapshot updates."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import threading
import time
from typing import Iterable

from PySide6.QtCore import QCoreApplication, QObject, Qt, Signal, Slot

from iPhoto.cache.index_store import get_global_repository
from iPhoto.config import WORK_DIR_NAME
from iPhoto.utils.logging import get_logger

from .pipeline import DetectedAssetFaces, build_person_records_from_faces
from .repository import FaceRecord, FaceRepository, PeopleGroupRecord
from .scan_session import FaceScanSession
from .status import FACE_STATUS_DONE, FACE_STATUS_RETRY

LOGGER = get_logger()


class PeopleSnapshotCommittedError(RuntimeError):
    """Raised when the People snapshot is committed but follow-up bookkeeping fails."""


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

            previous_faces = repository.get_all_faces()
            previous_persons = repository.get_all_person_records()
            clustered_faces, persons = session.build_runtime_snapshot(
                repository,
                distance_threshold=distance_threshold,
                min_samples=min_samples,
                existing_faces=previous_faces,
            )
            done_id_set = set(done_ids)
            changed_person_ids = tuple(
                sorted(
                    {
                        str(face.person_id)
                        for face in clustered_faces
                        if face.person_id and face.asset_id in done_id_set
                    }
                )
            )
            session.commit(
                repository,
                distance_threshold=distance_threshold,
                min_samples=min_samples,
                previous_faces=previous_faces,
                previous_persons=previous_persons,
                clustered_faces=clustered_faces,
                persons=persons,
            )
            # Emit the snapshot (inside the lock to serialise revision numbering)
            # before releasing so that UI can update while bookkeeping retries.
            event = self._emit_snapshot(
                changed_asset_ids=tuple(done_ids + retry_ids),
                changed_person_ids=changed_person_ids,
            )

        # Post-commit bookkeeping runs outside the coordinator lock so that
        # rename/merge/group operations are not blocked during transient DB
        # retries on the global asset-status store.
        try:
            self._mark_done_asset_ids(store, done_ids)
            return event
        except Exception as exc:
            LOGGER.error(
                "People snapshot committed for %s, but post-commit bookkeeping failed: %s",
                self._library_root,
                exc,
                exc_info=True,
            )
            raise PeopleSnapshotCommittedError(
                "Face scan committed, but updating scan bookkeeping failed."
            ) from exc

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

    def set_person_cover_from_asset(self, person_id: str, asset_id: str) -> bool:
        if not person_id or not asset_id:
            return False
        with self._lock:
            if self._shutdown_requested:
                return False
            repository = self._repository()
            changed = repository.set_person_cover_from_asset(person_id, asset_id)
            if changed:
                self._emit_snapshot(
                    changed_asset_ids=(asset_id,),
                    changed_person_ids=(person_id,),
                )
            return changed

    def add_manual_face(
        self,
        face: FaceRecord,
        *,
        person_name: str | None = None,
    ) -> PeopleSnapshotEvent | None:
        if not face.face_id or not face.asset_id or not face.person_id:
            return None
        with self._lock:
            if self._shutdown_requested:
                return None
            repository = self._repository()
            state_repository = repository.state_repository
            if state_repository is None:
                return None

            previous_faces = repository.get_all_faces()
            previous_persons = repository.get_all_person_records()
            try:
                state_repository.upsert_manual_face(face)
                all_faces = [*previous_faces, face]
                names_by_person_id = {
                    person.person_id: person.name for person in previous_persons
                }
                created_at_by_person_id = {
                    person.person_id: person.created_at for person in previous_persons
                }
                for profile in state_repository.get_profiles():
                    names_by_person_id[profile.person_id] = profile.name
                    created_at_by_person_id[profile.person_id] = profile.created_at
                if person_name is not None:
                    names_by_person_id[str(face.person_id)] = str(person_name).strip() or None
                    created_at_by_person_id.setdefault(str(face.person_id), face.detected_at)

                persons = build_person_records_from_faces(
                    all_faces,
                    names_by_person_id=names_by_person_id,
                    created_at_by_person_id=created_at_by_person_id,
                )
                repository.replace_all(all_faces, persons, sync_runtime_state=False)
                repository.sync_runtime_state()
                state_repository.sync_scan_results(persons, all_faces)
            except Exception:
                state_repository.delete_manual_face(face.face_id)
                repository.replace_all(previous_faces, previous_persons, sync_runtime_state=False)
                repository.sync_runtime_state()
                if face.thumbnail_path:
                    faces_root = (self._library_root / WORK_DIR_NAME / "faces").resolve()
                    thumbnail_file = (faces_root / face.thumbnail_path).resolve()
                    try:
                        thumbnail_file.relative_to(faces_root)
                    except ValueError:
                        LOGGER.warning("Orphaned thumbnail path escapes faces root, skipping: %s", thumbnail_file)
                    else:
                        try:
                            thumbnail_file.unlink(missing_ok=True)
                        except OSError:
                            LOGGER.warning("Failed to remove orphaned thumbnail: %s", thumbnail_file)
                raise
            return self._emit_snapshot(
                changed_asset_ids=(face.asset_id,),
                changed_person_ids=(str(face.person_id),),
            )

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

    def delete_group(self, group_id: str) -> bool:
        if not group_id:
            return False
        with self._lock:
            if self._shutdown_requested:
                return False
            repository = self._repository()
            group = repository.get_group(group_id)
            if group is None:
                return False
            changed_asset_ids = tuple(repository.get_common_asset_ids_for_group(group_id))
            changed = repository.delete_group(group_id)
            if changed:
                self._emit_snapshot(
                    changed_asset_ids=changed_asset_ids,
                    changed_person_ids=tuple(group.member_person_ids),
                    changed_group_ids=(group_id,),
                    group_redirects={group_id: None},
                )
            return changed

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

    def set_person_hidden(self, person_id: str, hidden: bool) -> bool:
        if not person_id:
            return False
        with self._lock:
            if self._shutdown_requested:
                return False
            repository = self._repository()
            changed = repository.set_person_hidden(person_id, hidden)
            if changed:
                self._emit_snapshot(changed_person_ids=(person_id,))
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

    def _mark_done_asset_ids(self, store: object, done_ids: list[str]) -> None:
        if not done_ids:
            return
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                store.update_face_statuses(done_ids, FACE_STATUS_DONE)
                return
            except Exception as exc:
                last_error = exc
                if attempt == 2:
                    break
                time.sleep(0.05 * (attempt + 1))
        if last_error is not None:
            raise last_error


_COORDINATORS: dict[Path, PeopleIndexCoordinator] = {}
_COORDINATORS_LOCK = threading.Lock()


def get_people_index_coordinator(library_root: Path) -> PeopleIndexCoordinator:
    resolved = Path(library_root).resolve()
    with _COORDINATORS_LOCK:
        coordinator = _COORDINATORS.get(resolved)
        if coordinator is None:
            coordinator = PeopleIndexCoordinator(resolved)
            # Ensure the coordinator lives on the Qt main thread so that the
            # QueuedConnection on _scheduleEmit can deliver events via the GUI
            # event loop regardless of which thread first calls this function.
            app = QCoreApplication.instance()
            if app is not None:
                coordinator.moveToThread(app.thread())
            _COORDINATORS[resolved] = coordinator
        else:
            coordinator.resume()
        return coordinator


def reset_people_index_coordinators() -> None:
    with _COORDINATORS_LOCK:
        _COORDINATORS.clear()


__all__ = [
    "PeopleIndexCoordinator",
    "PeopleSnapshotCommittedError",
    "PeopleSnapshotEvent",
    "get_people_index_coordinator",
    "reset_people_index_coordinators",
]

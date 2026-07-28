"""Thread-safe coordinator for realtime People snapshot updates."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QObject, Qt, Signal, Slot

from iPhoto.application.ports import PeopleAssetRepositoryPort
from iPhoto.recognition.operation_journal import RecognitionOperationJournal
from iPhoto.utils.logging import get_logger
from iPhoto.utils.pathutils import ensure_work_dir

from .pipeline import DetectedAssetFaces
from .repository import FaceRepository, ManualFaceRecord, PeopleGroupRecord
from .scan_session import FaceScanSession
from .status import FACE_STATUS_DONE, FACE_STATUS_RETRY

LOGGER = get_logger()

_PEOPLE_JOURNAL_KINDS = {
    "people_scan_commit",
    "people_add_manual_face",
    "people_delete_face",
    "people_move_face",
    "people_move_face_new",
    "people_merge",
    "people_create_group",
    "people_delete_group",
}


class PeopleSnapshotCommittedError(RuntimeError):
    """Raised when the People snapshot is committed but follow-up bookkeeping fails."""


@dataclass(frozen=True)
class PeopleSnapshotEvent:
    library_root: Path
    revision: int
    operation_id: str | None = None
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

    def __init__(
        self,
        library_root: Path,
        *,
        asset_repository: PeopleAssetRepositoryPort | None = None,
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
        self._recovery_error: Exception | None = None
        # QueuedConnection ensures _fire_snapshot() runs on the coordinator's
        # own thread regardless of which thread calls _emit_snapshot().
        self._scheduleEmit.connect(self._fire_snapshot, Qt.ConnectionType.QueuedConnection)
        try:
            with self._lock:
                self._recover_operations_locked()
        except Exception as exc:  # noqa: BLE001
            self._recovery_error = exc
            LOGGER.error(
                "People recognition recovery failed during bind for %s",
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
    def recovery_pending(self) -> bool:
        return self._recovery_error is not None

    def set_asset_repository(
        self,
        asset_repository: PeopleAssetRepositoryPort | None,
    ) -> None:
        """Bind the current library asset-index adapter."""

        with self._lock:
            self._asset_repository = asset_repository

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
            operation_id = self._try_prepare_operation_locked(
                "people_scan_commit",
                {"asset_ids": [result.asset_id for result in detected_batch]},
            )
            if operation_id is None:
                return None
            repository = self._repository()
            session = FaceScanSession()
            done_ids, retry_ids = session.stage_detection_results(detected_batch)
            store = self._asset_repository
            if retry_ids:
                if store is not None:
                    store.update_face_statuses(retry_ids, FACE_STATUS_RETRY)
            if not done_ids:
                self._reject_operation_locked(operation_id, "no_committable_faces")
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
                operation_id=operation_id,
            )
            # Emit the snapshot (inside the lock to serialise revision numbering)
            # before releasing so that UI can update while bookkeeping retries.
            event = self._emit_snapshot(
                operation_id=operation_id,
                changed_asset_ids=tuple(done_ids + retry_ids),
                changed_person_ids=changed_person_ids,
            )
            self._finish_operation_locked(operation_id, event)

        # Post-commit bookkeeping runs outside the coordinator lock so that
        # rename/merge/group operations are not blocked during transient DB
        # retries on the global asset-status store.
        try:
            self._mark_done_asset_ids(done_ids)
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

    def add_manual_face(
        self,
        face: ManualFaceRecord,
        *,
        person_name: str | None = None,
    ) -> PeopleSnapshotEvent | None:
        """Persist a user-created annotation without feeding it into AI clustering.

        Manual faces deliberately use ``ManualFaceRecord`` instead of ``FaceRecord``:
        they have no embedding, no face key, and must not rebuild the automatic
        runtime snapshot. The state repository owns their profile/cover bookkeeping;
        this coordinator only serializes the write and emits the UI refresh event.
        """

        if not face.face_id or not face.asset_id or not face.person_id:
            return None
        with self._lock:
            if self._shutdown_requested:
                return None
            repository = self._repository()
            operation_id = self._try_prepare_operation_locked(
                "people_add_manual_face",
                {
                    "face_id": face.face_id,
                    "asset_id": face.asset_id,
                    "person_id": face.person_id,
                },
            )
            if operation_id is None:
                return None
            state_repository = repository.state_repository
            if state_repository is None:
                self._reject_operation_locked(operation_id, "missing_state_repository")
                return None

            try:
                repository.add_manual_face(
                    face,
                    person_name=person_name,
                    operation_id=operation_id,
                )
            except Exception:
                # The runtime marker owns forward recovery. Keep both the
                # journal entry and thumbnail intact for the retry path.
                raise
            changed_group_ids = tuple(
                group.group_id
                for group in state_repository.list_groups()
                if face.person_id in group.member_person_ids
            )
            event = self._emit_snapshot(
                operation_id=operation_id,
                changed_asset_ids=(face.asset_id,),
                changed_person_ids=(str(face.person_id),),
                changed_group_ids=changed_group_ids,
            )
            self._finish_operation_locked(operation_id, event)
            return event

    def delete_face(self, face_id: str) -> PeopleSnapshotEvent | None:
        if not face_id:
            return None
        with self._lock:
            if self._shutdown_requested:
                return None
            repository = self._repository()
            operation_id = self._try_prepare_operation_locked(
                "people_delete_face",
                {"face_id": face_id},
            )
            if operation_id is None:
                return None
            result = repository.delete_face(face_id, operation_id=operation_id)
            if result is None:
                self._reject_operation_locked(operation_id, "unknown_face_id")
                return None
            event = self._emit_snapshot(
                operation_id=operation_id,
                changed_asset_ids=result.changed_asset_ids,
                changed_person_ids=result.changed_person_ids,
                changed_group_ids=result.changed_group_ids,
                person_redirects=result.person_redirects,
                group_redirects=result.group_redirects,
            )
            self._finish_operation_locked(operation_id, event)
            return event

    def move_face_to_person(
        self,
        face_id: str,
        target_person_id: str,
    ) -> PeopleSnapshotEvent | None:
        if not face_id or not target_person_id:
            return None
        with self._lock:
            if self._shutdown_requested:
                return None
            repository = self._repository()
            operation_id = self._try_prepare_operation_locked(
                "people_move_face",
                {"face_id": face_id, "target_person_id": target_person_id},
            )
            if operation_id is None:
                return None
            result = repository.move_face_to_person(
                face_id,
                target_person_id,
                operation_id=operation_id,
            )
            if result is None:
                self._reject_operation_locked(operation_id, "move_rejected")
                return None
            event = self._emit_snapshot(
                operation_id=operation_id,
                changed_asset_ids=result.changed_asset_ids,
                changed_person_ids=result.changed_person_ids,
                changed_group_ids=result.changed_group_ids,
                person_redirects=result.person_redirects,
                group_redirects=result.group_redirects,
            )
            self._finish_operation_locked(operation_id, event)
            return event

    def move_face_to_new_person(
        self,
        face_id: str,
        new_person_id: str,
        new_name: str,
    ) -> PeopleSnapshotEvent | None:
        if not face_id or not new_person_id:
            return None
        with self._lock:
            if self._shutdown_requested:
                return None
            repository = self._repository()
            operation_id = self._try_prepare_operation_locked(
                "people_move_face_new",
                {
                    "face_id": face_id,
                    "new_person_id": new_person_id,
                    "new_name": new_name,
                },
            )
            if operation_id is None:
                return None
            result = repository.move_face_to_new_person(
                face_id,
                new_person_id,
                new_name,
                operation_id=operation_id,
            )
            if result is None:
                self._reject_operation_locked(operation_id, "move_new_rejected")
                return None
            event = self._emit_snapshot(
                operation_id=operation_id,
                changed_asset_ids=result.changed_asset_ids,
                changed_person_ids=result.changed_person_ids,
                changed_group_ids=result.changed_group_ids,
                person_redirects=result.person_redirects,
                group_redirects=result.group_redirects,
            )
            self._finish_operation_locked(operation_id, event)
            return event

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

    def set_group_order(self, group_ids: Iterable[str]) -> PeopleSnapshotEvent | None:
        ordered_ids = tuple(str(group_id) for group_id in group_ids if group_id)
        with self._lock:
            if self._shutdown_requested:
                return None
            repository = self._repository()
            repository.set_group_order(ordered_ids)
            if not ordered_ids:
                return None
            return self._emit_snapshot(changed_group_ids=ordered_ids)

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
            operation_id = self._try_prepare_operation_locked(
                "people_merge",
                {
                    "source_person_id": source_person_id,
                    "target_person_id": target_person_id,
                },
            )
            if operation_id is None:
                return False
            merged, group_redirects = repository.merge_persons_with_redirects(
                source_person_id,
                target_person_id,
                operation_id=operation_id,
            )
            if not merged:
                self._reject_operation_locked(operation_id, "merge_rejected")
                return False
            affected_group_ids = tuple(
                group_id
                for group_id in set(group_redirects.values()) | set(group_redirects.keys())
                if group_id
            )
            event = self._emit_snapshot(
                operation_id=operation_id,
                changed_asset_ids=tuple(repository.get_asset_ids_by_person(target_person_id)),
                changed_person_ids=(source_person_id, target_person_id),
                changed_group_ids=affected_group_ids,
                person_redirects={source_person_id: target_person_id},
                group_redirects=group_redirects,
            )
            self._finish_operation_locked(operation_id, event)
            return True

    def create_group(
        self,
        member_person_ids: Iterable[str],
    ) -> PeopleGroupRecord | None:
        with self._lock:
            if self._shutdown_requested:
                return None
            repository = self._repository()
            normalized_members = tuple(
                member.key if hasattr(member, "key") else str(member)
                for member in member_person_ids
                if member
            )
            operation_id = self._try_prepare_operation_locked(
                "people_create_group",
                {"members": list(normalized_members)},
            )
            if operation_id is None:
                return None
            group = repository.create_group(
                normalized_members,
                operation_id=operation_id,
            )
            if group is not None:
                event = self._emit_snapshot(
                    operation_id=operation_id,
                    changed_asset_ids=tuple(repository.get_common_asset_ids_for_group(group.group_id)),
                    changed_person_ids=tuple(group.member_person_ids),
                    changed_group_ids=(group.group_id,),
                )
                self._finish_operation_locked(operation_id, event)
            else:
                self._reject_operation_locked(operation_id, "group_create_rejected")
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

    def delete_group(self, group_id: str) -> bool:
        if not group_id:
            return False
        with self._lock:
            if self._shutdown_requested:
                return False
            repository = self._repository()
            operation_id = self._try_prepare_operation_locked(
                "people_delete_group",
                {"group_id": group_id},
            )
            if operation_id is None:
                return False
            deleted, group, asset_ids = repository.delete_group(
                group_id,
                operation_id=operation_id,
            )
            if not deleted or group is None:
                self._reject_operation_locked(operation_id, "group_delete_rejected")
                return False
            event = self._emit_snapshot(
                operation_id=operation_id,
                changed_asset_ids=tuple(asset_ids),
                changed_person_ids=tuple(group.member_person_ids),
                changed_group_ids=(group_id,),
                group_redirects={group_id: None},
            )
            self._finish_operation_locked(operation_id, event)
            return True

    def _repository(self) -> FaceRepository:
        faces_root = ensure_work_dir(self._library_root) / "faces"
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
        operation_id: str | None = None,
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
            operation_id=operation_id,
            changed_asset_ids=tuple(dict.fromkeys(changed_asset_ids)),
            changed_person_ids=tuple(dict.fromkeys(changed_person_ids)),
            changed_group_ids=tuple(dict.fromkeys(changed_group_ids)),
            person_redirects=dict(person_redirects or {}),
            group_redirects=dict(group_redirects or {}),
        )
        self._scheduleEmit.emit(event)
        return event

    def _try_prepare_operation_locked(
        self,
        kind: str,
        payload: dict[str, object],
    ) -> str | None:
        if not self._recover_operations_locked():
            self._recovery_error = RuntimeError(
                "Another recognition operation must finish before People can continue."
            )
            return None
        operation_id = self._journal.try_prepare(kind, payload)
        if operation_id is None:
            self._recovery_error = RuntimeError(
                "Another recognition operation must finish before People can continue."
            )
            return None
        self._journal.transition(operation_id, "applying")
        self._recovery_error = None
        return operation_id

    def _recover_operations_locked(self) -> bool:
        repository: FaceRepository | None = None
        while (operation := self._journal.unfinished_head()) is not None:
            if operation.kind not in _PEOPLE_JOURNAL_KINDS:
                return False
            repository = repository or self._repository()
            runtime_commit = repository.get_runtime_commit(operation.operation_id)
            if runtime_commit is None:
                self._journal.transition(
                    operation.operation_id,
                    "finalized",
                    payload=operation.payload,
                    error="superseded_before_runtime_commit",
                )
                continue
            runtime_commit = repository.complete_runtime_state_sync(
                operation.operation_id
            )
            if runtime_commit is None:
                raise RuntimeError(
                    f"Missing People runtime commit during recovery: {operation.operation_id}"
                )
            self._journal.commit_outbox(
                operation.operation_id,
                {
                    "changed_asset_ids": list(
                        runtime_commit.get("changed_asset_ids", ())
                    ),
                    "changed_person_ids": list(
                        runtime_commit.get("changed_person_ids", ())
                    ),
                    "changed_group_ids": list(
                        runtime_commit.get("changed_group_ids", ())
                    ),
                    "person_redirects": dict(
                        runtime_commit.get("person_redirects", {})
                    ),
                    "group_redirects": dict(
                        runtime_commit.get("group_redirects", {})
                    ),
                },
            )
            self._journal.mark_published(operation.operation_id)
        self._recovery_error = None
        return True

    def _finish_operation_locked(
        self,
        operation_id: str,
        event: PeopleSnapshotEvent,
    ) -> None:
        self._repository().complete_runtime_state_sync(operation_id)
        self._journal.commit_outbox(
            operation_id,
            {
                "changed_asset_ids": list(event.changed_asset_ids),
                "changed_person_ids": list(event.changed_person_ids),
                "changed_group_ids": list(event.changed_group_ids),
                "person_redirects": event.person_redirects,
                "group_redirects": event.group_redirects,
            },
        )
        self._journal.mark_published(operation_id)

    def _reject_operation_locked(self, operation_id: str, error: str) -> None:
        self._journal.transition(operation_id, "finalized", error=error)

    def _mark_done_asset_ids(self, done_ids: list[str]) -> None:
        if not done_ids:
            return
        store = self._asset_repository
        if store is None:
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


def get_people_index_coordinator(
    library_root: Path,
    *,
    asset_repository: PeopleAssetRepositoryPort | None = None,
) -> PeopleIndexCoordinator:
    resolved = Path(library_root).resolve()
    with _COORDINATORS_LOCK:
        coordinator = _COORDINATORS.get(resolved)
        if coordinator is None:
            coordinator = PeopleIndexCoordinator(
                resolved,
                asset_repository=asset_repository,
            )
            # Ensure the coordinator lives on the Qt main thread so that the
            # QueuedConnection on _scheduleEmit can deliver events via the GUI
            # event loop regardless of which thread first calls this function.
            app = QCoreApplication.instance()
            if app is not None:
                coordinator.moveToThread(app.thread())
            _COORDINATORS[resolved] = coordinator
        else:
            if asset_repository is not None:
                coordinator.set_asset_repository(asset_repository)
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

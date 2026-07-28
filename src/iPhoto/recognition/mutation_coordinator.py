"""Library-scoped admission, recovery, and event dispatch for recognition writes."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from iPhoto.utils.pathutils import ensure_work_dir

from .operation_journal import (
    RecognitionOperation,
    RecognitionOperationJournal,
    RecognitionOperationKind,
    RecognitionOperationState,
    RecognitionOutboxEvent,
)


class RecognitionMutationFailure(StrEnum):
    REJECTED = "rejected"
    RECOVERY_PENDING = "recovery_pending"
    SHUTTING_DOWN = "shutting_down"


@dataclass(frozen=True, slots=True)
class RecognitionMutationOutcome[T]:
    succeeded: bool
    value: T | None = None
    failure: RecognitionMutationFailure | None = None
    operation_id: str | None = None


RecoveryHandler = Callable[[RecognitionOperation], bool]
EventSubscriber = Callable[[RecognitionOutboxEvent], None]


class RecognitionMutationCoordinator:
    """The only owner of a library's global recognition operation journal.

    Domain coordinators register typed recovery handlers, while normal writes use
    this object for global admission. Event delivery is at-least-once: a crash
    after publishing but before the dispatched CAS may replay the same event id.
    """

    def __init__(self, library_root: Path) -> None:
        self._library_root = Path(library_root).resolve()
        self._journal = RecognitionOperationJournal(
            ensure_work_dir(self._library_root) / "recognition" / "operations.db"
        )
        self._lock = threading.RLock()
        self._handlers: dict[str, list[RecoveryHandler]] = {}
        self._subscribers: list[EventSubscriber] = []
        self._recovery_error: Exception | None = None

    @property
    def library_root(self) -> Path:
        return self._library_root

    @property
    def recovery_pending(self) -> bool:
        return bool(self._journal.unfinished()) or self._recovery_error is not None

    @property
    def recovery_error(self) -> Exception | None:
        return self._recovery_error

    def register_recovery_handler(
        self,
        kinds: set[str | RecognitionOperationKind],
        handler: RecoveryHandler,
    ) -> None:
        with self._lock:
            for kind in kinds:
                normalized = str(kind)
                handlers = self._handlers.setdefault(normalized, [])
                if handler not in handlers:
                    handlers.insert(0, handler)

    def subscribe(self, subscriber: EventSubscriber) -> None:
        with self._lock:
            if subscriber not in self._subscribers:
                self._subscribers.append(subscriber)
            self.dispatch_pending()

    def unsubscribe(self, subscriber: EventSubscriber) -> None:
        with self._lock:
            if subscriber in self._subscribers:
                self._subscribers.remove(subscriber)

    def recover_pending(self) -> bool:
        with self._lock:
            try:
                while (operation := self._journal.unfinished_head()) is not None:
                    if operation.state == RecognitionOperationState.COMMITTED and self._subscribers:
                        if not self.dispatch_pending():
                            return False
                        continue
                    handlers = self._handlers.get(operation.kind, ())
                    if not handlers:
                        self._recovery_error = RuntimeError(
                            "No recovery handler is registered for recognition operation "
                            f"{operation.kind}/{operation.operation_id}."
                        )
                        return False
                    if not any(handler(operation) for handler in tuple(handlers)):
                        self._recovery_error = RuntimeError(
                            "Recognition operation recovery is incomplete for "
                            f"{operation.kind}/{operation.operation_id}."
                        )
                        return False
                self._recovery_error = None
                return True
            except Exception as exc:  # noqa: BLE001
                self._recovery_error = exc
                return False

    def try_prepare(
        self,
        kind: str | RecognitionOperationKind,
        payload: dict[str, Any],
    ) -> str | None:
        with self._lock:
            operation_id = self._journal.try_prepare(kind, payload)
            if operation_id is None:
                return None
            if not self._journal.transition(
                operation_id,
                RecognitionOperationState.APPLYING,
                expected_state=RecognitionOperationState.PREPARED,
            ):
                raise RuntimeError(f"Recognition operation lost its prepared lease: {operation_id}")
            return operation_id

    def prepare(self, kind: str, payload: dict[str, Any]) -> str:
        """Compatibility primitive for recovery fixtures and legacy tests."""

        return self._journal.prepare(kind, payload)

    def transition(self, *args, **kwargs) -> bool:
        operation_id = str(args[0] if args else kwargs.get("operation_id") or "")
        if kwargs.get("expected_state") is None:
            current = next(
                (
                    operation.state
                    for operation in self._journal.unfinished()
                    if operation.operation_id == operation_id
                ),
                None,
            )
            if current is None:
                raise RuntimeError(
                    f"Recognition operation has no active state lease: {operation_id}"
                )
            kwargs["expected_state"] = current
        succeeded = self._journal.transition(*args, **kwargs)
        if not succeeded:
            raise RuntimeError(f"Recognition operation state CAS failed: {operation_id}")
        return True

    def commit_outbox(self, *args, **kwargs) -> str:
        return self._journal.commit_outbox(*args, **kwargs)

    def commit_and_dispatch(
        self,
        operation_id: str,
        event: dict[str, Any],
        dispatch: Callable[[], None],
    ) -> str:
        """Persist an event before delivery, then finalize its CAS acknowledgment."""

        with self._lock:
            event_id = self._journal.commit_outbox(operation_id, event)
            dispatch()
            if not self._journal.mark_dispatched(operation_id):
                raise RuntimeError(f"Recognition event dispatch CAS failed: {event_id}")
            return event_id

    def mark_dispatched(self, operation_id: str) -> bool:
        return self._journal.mark_dispatched(operation_id)

    def mark_published(self, operation_id: str) -> None:
        self._journal.mark_published(operation_id)

    def unfinished(self) -> tuple[RecognitionOperation, ...]:
        return self._journal.unfinished()

    def unfinished_head(self) -> RecognitionOperation | None:
        return self._journal.unfinished_head()

    def pending_events(self) -> tuple[RecognitionOutboxEvent, ...]:
        return self._journal.pending_events()

    def dispatch_pending(self) -> bool:
        with self._lock:
            for event in self._journal.pending_events():
                try:
                    for subscriber in tuple(self._subscribers):
                        subscriber(event)
                except Exception as exc:  # noqa: BLE001
                    self._recovery_error = exc
                    return False
                if not self._journal.mark_dispatched(event.operation_id):
                    self._recovery_error = RuntimeError(
                        f"Recognition event dispatch CAS failed: {event.event_id}"
                    )
                    return False
            return True


_COORDINATORS: dict[Path, RecognitionMutationCoordinator] = {}
_COORDINATORS_LOCK = threading.Lock()


def get_recognition_mutation_coordinator(
    library_root: Path,
) -> RecognitionMutationCoordinator:
    resolved = Path(library_root).resolve()
    with _COORDINATORS_LOCK:
        coordinator = _COORDINATORS.get(resolved)
        if coordinator is None:
            coordinator = RecognitionMutationCoordinator(resolved)
            _COORDINATORS[resolved] = coordinator
        return coordinator


def reset_recognition_mutation_coordinators() -> None:
    with _COORDINATORS_LOCK:
        _COORDINATORS.clear()


__all__ = [
    "RecognitionMutationCoordinator",
    "RecognitionMutationFailure",
    "RecognitionMutationOutcome",
    "get_recognition_mutation_coordinator",
    "reset_recognition_mutation_coordinators",
]

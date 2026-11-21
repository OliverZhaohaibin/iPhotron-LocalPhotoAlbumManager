"""Library-scoped admission, recovery, and event dispatch for recognition writes."""

from __future__ import annotations

import errno
import os
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
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


@dataclass(slots=True)
class _ExecutionLease:
    identity: tuple[int, int] | Path
    references: int = 0
    lock: threading.RLock = field(default_factory=threading.RLock)
    handlers_lock: threading.RLock = field(default_factory=threading.RLock)
    _held: bool = False

    @contextmanager
    def try_hold_locked(self, path: Path) -> Iterator[bool]:
        if self._held:
            yield True
            return
        # A nonblocking OS lock spans commits to all recognition databases.
        # Closing the handle or exiting the process releases it.
        with path.open("a+b") as handle:
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.lockf(handle, fcntl.LOCK_EX | fcntl.LOCK_NB, 1, 0)
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                yield False
                return
            self._held = True
            try:
                yield True
            finally:
                self._held = False


RecoveryHandler = Callable[[RecognitionOperation], bool]
EventSubscriber = Callable[[RecognitionOutboxEvent], None]


_EXECUTION_LOCKS: dict[tuple[int, int] | Path, _ExecutionLease] = {}
_EXECUTION_LOCKS_GUARD = threading.Lock()
_LEASE_RETRY_SECONDS = 0.1


def _reset_execution_leases_after_fork() -> None:
    # POSIX record locks belong to the parent's PID. A child must acquire its
    # own OS lock even when its spawning thread was inside a mutation.
    global _EXECUTION_LOCKS_GUARD
    _EXECUTION_LOCKS_GUARD = threading.Lock()
    for lease in _EXECUTION_LOCKS.values():
        lease.lock = threading.RLock()
        lease.handlers_lock = threading.RLock()
        lease._held = False


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_execution_leases_after_fork)


def _acquire_execution_lease(library_root: Path) -> _ExecutionLease:
    """Share thread reentrancy and an OS-backed lease for one library."""

    # Case aliases must share one descriptor lifetime for POSIX record locks.
    info = library_root.stat()
    identity = (info.st_dev, info.st_ino) if info.st_ino else library_root
    with _EXECUTION_LOCKS_GUARD:
        lease = _EXECUTION_LOCKS.get(identity)
        if lease is None:
            lease = _ExecutionLease(identity)
            _EXECUTION_LOCKS[identity] = lease
        lease.references += 1
        return lease


def _release_execution_lease(lease: _ExecutionLease) -> None:
    with _EXECUTION_LOCKS_GUARD:
        lease.references -= 1
        if lease.references == 0:
            _EXECUTION_LOCKS.pop(lease.identity)


class RecognitionMutationCoordinator:
    """The only owner of a library's global recognition operation journal.

    Domain coordinators register typed recovery handlers, while normal writes use
    this object for global admission. Event delivery is at-least-once: a crash
    after publishing but before the dispatched CAS may replay the same event id.
    """

    def __init__(self, library_root: Path) -> None:
        self._library_root = Path(library_root).resolve()
        directory = ensure_work_dir(self._library_root) / "recognition"
        directory.mkdir(exist_ok=True)
        self._journal = RecognitionOperationJournal(directory / "operations.db")
        self._lock_path = directory / "operations.lock"
        self._lease = _acquire_execution_lease(self._library_root)
        self._closed = False
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

    @property
    def execution_lock(self) -> threading.RLock:
        """Serialize in-process lifecycle changes with writes."""

        return self._lease.lock

    @contextmanager
    def try_mutation_scope(self) -> Iterator[bool]:
        """Return busy immediately when another process owns this library."""

        with self.execution_lock:
            if self._closed:
                yield False
                return
            with self._lease.try_hold_locked(self._lock_path) as acquired:
                if not acquired:
                    self._recovery_error = BlockingIOError("Another process is updating recognition.")
                yield acquired

    @contextmanager
    def wait_for_mutation_scope(self, *, is_cancelled: Callable[[], bool]) -> Iterator[bool]:
        """Retry the lease for a background batch that has already run inference."""

        lock = self.execution_lock
        while not self._closed and not is_cancelled():
            if not lock.acquire(timeout=_LEASE_RETRY_SECONDS):
                continue
            try:
                with self.try_mutation_scope() as acquired:
                    if acquired:
                        yield not is_cancelled()
                        return
            finally:
                lock.release()
            time.sleep(_LEASE_RETRY_SECONDS)
        yield False

    @contextmanager
    def mutation_scope(self) -> Iterator[None]:
        """Prevent live work from being mistaken for crash recovery.

        The lease is shared by every coordinator instance for this library and
        intentionally remains held from admission through commit/finalize. A
        process crash releases it, allowing the next process to recover the
        durable journal head.
        """

        with self.try_mutation_scope() as acquired:
            if not acquired:
                if self._closed:
                    raise RuntimeError("Recognition mutation coordinator is closed.")
                raise BlockingIOError("Another process is updating recognition.")
            yield

    def register_recovery_handler(
        self,
        kinds: set[str | RecognitionOperationKind],
        handler: RecoveryHandler,
    ) -> None:
        with self._lease.handlers_lock:
            if self._closed:
                raise RuntimeError("Recognition mutation coordinator is closed.")
            for kind in kinds:
                normalized = str(kind)
                handlers = self._handlers.setdefault(normalized, [])
                if handler not in handlers:
                    handlers.insert(0, handler)

    def subscribe(self, subscriber: EventSubscriber) -> None:
        with self._lease.handlers_lock:
            if self._closed:
                raise RuntimeError("Recognition mutation coordinator is closed.")
            if subscriber not in self._subscribers:
                self._subscribers.append(subscriber)
        self.dispatch_pending()

    def unsubscribe(self, subscriber: EventSubscriber) -> None:
        with self._lease.handlers_lock:
            if subscriber in self._subscribers:
                self._subscribers.remove(subscriber)

    def close(self) -> None:
        """Release session-owned handlers, subscribers, and the root lease."""

        if self._closed:
            return
        with self.execution_lock, self._lease.handlers_lock:
            if self._closed:
                return
            self._handlers.clear()
            self._subscribers.clear()
            self._closed = True
        _release_execution_lease(self._lease)

    def recover_pending(self) -> bool:
        with self.try_mutation_scope() as acquired, self._lease.handlers_lock:
            if not acquired:
                return False
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
                    if operation.state == RecognitionOperationState.PREPARED:
                        # Admission and the applying transition are separate durable
                        # writes. Claim interrupted admission before replaying it.
                        self.transition(
                            operation.operation_id,
                            RecognitionOperationState.APPLYING,
                            expected_state=RecognitionOperationState.PREPARED,
                        )
                        continue
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
        with self.try_mutation_scope() as acquired, self._lease.handlers_lock:
            if not acquired:
                return None
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

        with self.mutation_scope():
            return self._journal.prepare(kind, payload)

    def transition(self, *args, **kwargs) -> bool:
        with self.mutation_scope():
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
        with self.mutation_scope():
            return self._journal.commit_outbox(*args, **kwargs)

    def commit_and_dispatch(
        self,
        operation_id: str,
        event: dict[str, Any],
        dispatch: Callable[[], None],
    ) -> str:
        """Persist an event before delivery, then finalize its CAS acknowledgment."""

        with self.mutation_scope(), self._lease.handlers_lock:
            event_id = self._journal.commit_outbox(operation_id, event)
            dispatch()
            if not self._journal.mark_dispatched(operation_id):
                raise RuntimeError(f"Recognition event dispatch CAS failed: {event_id}")
            return event_id

    def mark_dispatched(self, operation_id: str) -> bool:
        with self.mutation_scope():
            return self._journal.mark_dispatched(operation_id)

    def mark_published(self, operation_id: str) -> None:
        with self.mutation_scope():
            self._journal.mark_published(operation_id)

    def unfinished(self) -> tuple[RecognitionOperation, ...]:
        with self.execution_lock:
            if self._closed:
                raise RuntimeError("Recognition mutation coordinator is closed.")
            return self._journal.unfinished()

    def unfinished_head(self) -> RecognitionOperation | None:
        with self.execution_lock:
            if self._closed:
                raise RuntimeError("Recognition mutation coordinator is closed.")
            return self._journal.unfinished_head()

    def pending_events(self) -> tuple[RecognitionOutboxEvent, ...]:
        with self.execution_lock:
            if self._closed:
                raise RuntimeError("Recognition mutation coordinator is closed.")
            return self._journal.pending_events()

    def dispatch_pending(self) -> bool:
        with self.try_mutation_scope() as acquired, self._lease.handlers_lock:
            if not acquired:
                return False
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


def get_recognition_mutation_coordinator(
    library_root: Path,
) -> RecognitionMutationCoordinator:
    """Compatibility factory; production sessions inject their owned instance."""

    return RecognitionMutationCoordinator(Path(library_root).resolve())


def reset_recognition_mutation_coordinators() -> None:
    """Compatibility no-op retained for older tests."""


__all__ = [
    "RecognitionMutationCoordinator",
    "RecognitionMutationFailure",
    "RecognitionMutationOutcome",
    "get_recognition_mutation_coordinator",
    "reset_recognition_mutation_coordinators",
]

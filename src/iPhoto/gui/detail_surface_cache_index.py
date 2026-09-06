"""Transactional metadata index for the rebuildable Detail surface cache."""

from __future__ import annotations

import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Final

from PySide6.QtCore import QLockFile

_ACCESS_BATCH: Final = 128
_ACCESS_INTERVAL_NS: Final = 30 * 1_000_000_000
_NAMESPACE_LOCK_RETRY_NS: Final = 30 * 1_000_000_000
_PROCESS_SESSION_TOKEN: Final = secrets.randbits(63) or 1
_LEASE_REGISTRY_LOCK = RLock()
_ACTIVE_SESSION_LEASES: dict[str, set[int]] = {}


@dataclass(slots=True)
class _NamespaceLockState:
    lock_file: QLockFile
    holders: set[int]


_ACTIVE_NAMESPACE_LOCKS: dict[str, _NamespaceLockState] = {}
_NAMESPACE_LOCK_FILE_FACTORY = QLockFile


class SurfaceCacheIndexUnavailableError(RuntimeError):
    """Raised internally after a runtime SQLite control-plane failure."""


@dataclass(frozen=True, slots=True)
class SurfaceCacheIndexEntry:
    digest: str
    relative_path: str
    container_schema: int
    decoder_contract: int
    payload_bytes: int
    file_bytes: int
    checksum: int
    checksum_state: str
    file_mtime_ns: int
    created_ns: int
    last_access_ns: int
    last_verified_ns: int


class SurfaceCacheIndex:
    """One lock-serialized SQLite connection shared by cache worker threads."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).expanduser().resolve(strict=False)
        self.path = self.root / "index.sqlite3"
        self.lock_path = self.root / "index.sqlite3.lock"
        self._connection: sqlite3.Connection | None = None
        self._lock = RLock()
        self._pending_access: dict[str, int] = {}
        self._last_access_flush_ns = time.time_ns()
        self._session_token = _PROCESS_SESSION_TOKEN
        self._lease_token = secrets.randbits(63) or 1
        self._lease_registry_key = os.path.normcase(str(self.path))
        self._lease_acquired = False
        self._namespace_lock_token = secrets.randbits(63) or 1
        self._namespace_lock_key = os.path.normcase(str(self.lock_path))
        self._namespace_lock_acquired = False
        self._namespace_lock_unavailable_reason: str | None = None
        self._next_namespace_lock_retry_ns = 0
        self._needs_recovery = False
        self._rebuild_required = False
        self._closed = False

    @property
    def needs_recovery(self) -> bool:
        with self._lock:
            if self._connection is None or self._rebuild_required:
                return self._needs_recovery or self._rebuild_required
            try:
                self._needs_recovery = self._recovery_required_locked()
            except sqlite3.DatabaseError as exc:
                raise self._database_failure_locked(exc) from exc
            return self._needs_recovery

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    @property
    def open_unavailable_reason(self) -> str | None:
        """Return the latest namespace-lock failure for diagnostics only."""

        with self._lock:
            return self._namespace_lock_unavailable_reason

    def ensure_open(self) -> bool:
        with self._lock:
            if self._closed:
                return False
            if self._connection is not None:
                return True
            if not self._try_acquire_namespace_lock_locked():
                return False
            rebuilding = self._rebuild_required
            if rebuilding:
                self._needs_recovery = True
                self._discard_broken_index_locked()
            try:
                self._open_connection_locked()
            except (OSError, sqlite3.DatabaseError) as first_error:
                self._discard_broken_index_locked()
                self._needs_recovery = True
                try:
                    self._open_connection_locked()
                except (OSError, sqlite3.DatabaseError) as second_error:
                    self._database_failure_locked(second_error or first_error)
                    return False
            self._rebuild_required = False
            return True

    def get(self, digest: str) -> SurfaceCacheIndexEntry | None:
        with self._lock:
            if not self.ensure_open():
                return None
            try:
                row = self._connection.execute(  # type: ignore[union-attr]
                    """
                    SELECT digest, relative_path, container_schema, decoder_contract,
                           payload_bytes, file_bytes, checksum, checksum_state,
                           file_mtime_ns, created_ns, last_access_ns, last_verified_ns
                    FROM entries
                    WHERE digest = ?
                    """,
                    (str(digest),),
                ).fetchone()
            except sqlite3.DatabaseError as exc:
                raise self._database_failure_locked(exc) from exc
            return self._entry_from_row(row) if row is not None else None

    def all_entries(self) -> tuple[SurfaceCacheIndexEntry, ...]:
        with self._lock:
            if not self.ensure_open():
                return ()
            try:
                rows = self._connection.execute(  # type: ignore[union-attr]
                    """
                    SELECT digest, relative_path, container_schema, decoder_contract,
                           payload_bytes, file_bytes, checksum, checksum_state,
                           file_mtime_ns, created_ns, last_access_ns, last_verified_ns
                    FROM entries
                    """
                ).fetchall()
            except sqlite3.DatabaseError as exc:
                raise self._database_failure_locked(exc) from exc
            return tuple(self._entry_from_row(row) for row in rows)

    def upsert(self, entry: SurfaceCacheIndexEntry) -> bool:
        with self._lock:
            if not self.ensure_open():
                return False
            connection = self._connection
            try:
                previous = connection.execute(  # type: ignore[union-attr]
                    "SELECT file_bytes FROM entries WHERE digest = ?",
                    (entry.digest,),
                ).fetchone()
                previous_bytes = int(previous[0]) if previous is not None else 0
                connection.execute(  # type: ignore[union-attr]
                    """
                    INSERT INTO entries (
                        digest, relative_path, container_schema, decoder_contract,
                        payload_bytes, file_bytes, checksum, checksum_state,
                        file_mtime_ns, created_ns, last_access_ns, last_verified_ns
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(digest) DO UPDATE SET
                        relative_path = excluded.relative_path,
                        container_schema = excluded.container_schema,
                        decoder_contract = excluded.decoder_contract,
                        payload_bytes = excluded.payload_bytes,
                        file_bytes = excluded.file_bytes,
                        checksum = excluded.checksum,
                        checksum_state = excluded.checksum_state,
                        file_mtime_ns = excluded.file_mtime_ns,
                        last_access_ns = excluded.last_access_ns,
                        last_verified_ns = excluded.last_verified_ns
                    """,
                    (
                        entry.digest,
                        entry.relative_path,
                        entry.container_schema,
                        entry.decoder_contract,
                        entry.payload_bytes,
                        entry.file_bytes,
                        f"{entry.checksum:016x}",
                        entry.checksum_state,
                        entry.file_mtime_ns,
                        entry.created_ns,
                        entry.last_access_ns,
                        entry.last_verified_ns,
                    ),
                )
                delta = entry.file_bytes - previous_bytes
                self._set_meta_locked(
                    "indexed_bytes",
                    max(0, self._meta_int_locked("indexed_bytes") + delta),
                )
                self._set_meta_locked(
                    "bytes_since_maintenance",
                    max(0, self._meta_int_locked("bytes_since_maintenance") + entry.file_bytes),
                )
                connection.commit()  # type: ignore[union-attr]
                return True
            except sqlite3.DatabaseError as exc:
                self._rollback_quietly(connection)
                raise self._database_failure_locked(exc) from exc

    def remove(self, digest: str) -> bool:
        with self._lock:
            if not self.ensure_open():
                return False
            connection = self._connection
            try:
                row = connection.execute(  # type: ignore[union-attr]
                    "SELECT file_bytes FROM entries WHERE digest = ?",
                    (str(digest),),
                ).fetchone()
                if row is None:
                    return True
                connection.execute("DELETE FROM entries WHERE digest = ?", (str(digest),))  # type: ignore[union-attr]
                self._set_meta_locked(
                    "indexed_bytes",
                    max(0, self._meta_int_locked("indexed_bytes") - int(row[0])),
                )
                connection.commit()  # type: ignore[union-attr]
                return True
            except sqlite3.DatabaseError as exc:
                self._rollback_quietly(connection)
                raise self._database_failure_locked(exc) from exc

    def mark_checksum_state(
        self,
        digest: str,
        state: str,
        *,
        verified_ns: int | None = None,
    ) -> None:
        with self._lock:
            if not self.ensure_open():
                return
            when = int(verified_ns or 0)
            try:
                self._connection.execute(  # type: ignore[union-attr]
                    """
                    UPDATE entries
                    SET checksum_state = ?,
                        last_verified_ns = CASE WHEN ? > 0 THEN ? ELSE last_verified_ns END
                    WHERE digest = ?
                    """,
                    (str(state), when, when, str(digest)),
                )
                self._connection.commit()  # type: ignore[union-attr]
            except sqlite3.DatabaseError as exc:
                raise self._database_failure_locked(exc) from exc

    def queue_access(self, digest: str, *, accessed_ns: int | None = None) -> bool:
        now = int(accessed_ns or time.time_ns())
        with self._lock:
            self._pending_access[str(digest)] = now
            return (
                len(self._pending_access) >= _ACCESS_BATCH
                or now - self._last_access_flush_ns >= _ACCESS_INTERVAL_NS
            )

    def flush_accesses(self, *, force: bool = False) -> bool:
        with self._lock:
            now = time.time_ns()
            if not self._pending_access:
                return False
            if (
                not force
                and len(self._pending_access) < _ACCESS_BATCH
                and now - self._last_access_flush_ns < _ACCESS_INTERVAL_NS
            ):
                return False
            if not self.ensure_open():
                return False
            pending = tuple(self._pending_access.items())
            try:
                self._connection.executemany(  # type: ignore[union-attr]
                    """
                    UPDATE entries
                    SET last_access_ns = MAX(last_access_ns + 1, ?)
                    WHERE digest = ?
                    """,
                    ((accessed_ns, digest) for digest, accessed_ns in pending),
                )
                self._connection.commit()  # type: ignore[union-attr]
            except sqlite3.DatabaseError as exc:
                self._rollback_quietly(self._connection)
                raise self._database_failure_locked(exc) from exc
            for digest, accessed_ns in pending:
                if self._pending_access.get(digest) == accessed_ns:
                    self._pending_access.pop(digest, None)
            self._last_access_flush_ns = now
            return True

    def maintenance_due(
        self,
        budget_bytes: int,
        *,
        byte_interval: int,
        time_interval_ns: int,
    ) -> bool:
        with self._lock:
            if not self.ensure_open():
                return False
            now = time.time_ns()
            try:
                return (
                    self._recovery_required_locked()
                    or self._meta_int_locked("indexed_bytes") > max(0, int(budget_bytes))
                    or self._meta_int_locked("bytes_since_maintenance") >= int(byte_interval)
                    or now - self._meta_int_locked("last_maintenance_ns", default=now)
                    >= int(time_interval_ns)
                )
            except sqlite3.DatabaseError as exc:
                raise self._database_failure_locked(exc) from exc

    def lru_victims(self, *, limit: int) -> tuple[SurfaceCacheIndexEntry, ...]:
        with self._lock:
            if not self.ensure_open():
                return ()
            try:
                rows = self._connection.execute(  # type: ignore[union-attr]
                    """
                    SELECT digest, relative_path, container_schema, decoder_contract,
                           payload_bytes, file_bytes, checksum, checksum_state,
                           file_mtime_ns, created_ns, last_access_ns, last_verified_ns
                    FROM entries
                    ORDER BY last_access_ns ASC, created_ns ASC, digest ASC
                    LIMIT ?
                    """,
                    (max(1, int(limit)),),
                ).fetchall()
            except sqlite3.DatabaseError as exc:
                raise self._database_failure_locked(exc) from exc
            return tuple(self._entry_from_row(row) for row in rows)

    @property
    def indexed_bytes(self) -> int:
        with self._lock:
            if not self.ensure_open():
                return 0
            try:
                return self._meta_int_locked("indexed_bytes")
            except sqlite3.DatabaseError as exc:
                raise self._database_failure_locked(exc) from exc

    def finish_maintenance(self) -> None:
        with self._lock:
            if not self.ensure_open():
                return
            try:
                self._set_meta_locked("bytes_since_maintenance", 0)
                self._set_meta_locked("last_maintenance_ns", time.time_ns())
                self._connection.commit()  # type: ignore[union-attr]
            except sqlite3.DatabaseError as exc:
                raise self._database_failure_locked(exc) from exc

    def recalculate_indexed_bytes(self) -> int:
        """Repair the cached byte total after an exceptional recovery scan."""

        with self._lock:
            if not self.ensure_open():
                return 0
            try:
                row = self._connection.execute(  # type: ignore[union-attr]
                    "SELECT COALESCE(SUM(file_bytes), 0) FROM entries"
                ).fetchone()
                total = max(0, int(row[0]) if row is not None else 0)
                self._set_meta_locked("indexed_bytes", total)
                self._connection.commit()  # type: ignore[union-attr]
            except sqlite3.DatabaseError as exc:
                raise self._database_failure_locked(exc) from exc
            return total

    def mark_recovered(self) -> None:
        with self._lock:
            if (
                self._connection is None
                or self._rebuild_required
                or not self._lease_acquired
            ):
                return
            connection = self._connection
            try:
                connection.execute("BEGIN IMMEDIATE")
                if self._meta_int_locked("owner_session") != self._session_token:
                    connection.rollback()
                    return
                self._set_meta_locked("recovery_required", 0)
                connection.commit()
                self._needs_recovery = False
            except sqlite3.DatabaseError as exc:
                self._rollback_quietly(connection)
                raise self._database_failure_locked(exc) from exc

    def mark_recovery_required(self) -> None:
        with self._lock:
            self._mark_recovery_required_locked()

    def close(self, *, clean: bool = True) -> None:
        with self._lock:
            if self._closed:
                return
            try:
                self.flush_accesses(force=True)
            except SurfaceCacheIndexUnavailableError:
                clean = False
            if self._connection is not None:
                try:
                    self._release_lease_locked(clean=clean)
                except SurfaceCacheIndexUnavailableError:
                    pass
            self._close_connection_locked()
            self._release_namespace_lock_locked()
            self._closed = True

    def _try_acquire_namespace_lock_locked(self) -> bool:
        if self._namespace_lock_acquired:
            return True
        now = time.monotonic_ns()
        if now < self._next_namespace_lock_retry_ns:
            return False
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError:
            self._record_namespace_lock_failure_locked("lock_io_error", now)
            return False
        with _LEASE_REGISTRY_LOCK:
            shared = _ACTIVE_NAMESPACE_LOCKS.get(self._namespace_lock_key)
            if shared is not None:
                shared.holders.add(self._namespace_lock_token)
                self._namespace_lock_acquired = True
                self._namespace_lock_unavailable_reason = None
                self._next_namespace_lock_retry_ns = 0
                return True
            try:
                lock_file = _NAMESPACE_LOCK_FILE_FACTORY(str(self.lock_path))
                lock_file.setStaleLockTime(0)
                acquired = bool(lock_file.tryLock(0))
            except (OSError, RuntimeError):
                self._record_namespace_lock_failure_locked("lock_io_error", now)
                return False
            if not acquired:
                error = lock_file.error()
                if error == QLockFile.LockError.LockFailedError:
                    reason = "owned_by_other_process"
                elif error == QLockFile.LockError.PermissionError:
                    reason = "lock_permission_error"
                else:
                    reason = "lock_io_error"
                self._record_namespace_lock_failure_locked(reason, now)
                return False
            _ACTIVE_NAMESPACE_LOCKS[self._namespace_lock_key] = _NamespaceLockState(
                lock_file=lock_file,
                holders={self._namespace_lock_token},
            )
            self._namespace_lock_acquired = True
            self._namespace_lock_unavailable_reason = None
            self._next_namespace_lock_retry_ns = 0
            return True

    def _record_namespace_lock_failure_locked(self, reason: str, now: int) -> None:
        self._namespace_lock_unavailable_reason = str(reason)
        self._next_namespace_lock_retry_ns = int(now) + _NAMESPACE_LOCK_RETRY_NS

    def _release_namespace_lock_locked(self) -> None:
        if not self._namespace_lock_acquired:
            return
        with _LEASE_REGISTRY_LOCK:
            shared = _ACTIVE_NAMESPACE_LOCKS.get(self._namespace_lock_key)
            if shared is not None:
                shared.holders.discard(self._namespace_lock_token)
                if not shared.holders:
                    shared.lock_file.unlock()
                    _ACTIVE_NAMESPACE_LOCKS.pop(self._namespace_lock_key, None)
        self._namespace_lock_acquired = False

    def _open_connection_locked(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        self._connection = connection
        try:
            self._create_schema(connection)
            with _LEASE_REGISTRY_LOCK:
                connection.execute("BEGIN IMMEDIATE")
                prior_clean = self._meta_int_locked("clean_shutdown", default=1)
                owner_session = self._meta_int_locked("owner_session")
                live_leases = len(
                    _ACTIVE_SESSION_LEASES.get(self._lease_registry_key, ())
                )
                same_session_active = (
                    owner_session == self._session_token and live_leases > 0
                )
                recovery_required = self._recovery_required_locked()
                recovery_required = bool(
                    recovery_required
                    or self._needs_recovery
                    or (prior_clean != 1 and not same_session_active)
                )
                self._set_meta_locked("owner_session", self._session_token)
                self._set_meta_locked("active_leases", live_leases + 1)
                self._set_meta_locked("recovery_required", int(recovery_required))
                self._set_meta_locked("clean_shutdown", 0)
                connection.commit()
                _ACTIVE_SESSION_LEASES.setdefault(
                    self._lease_registry_key,
                    set(),
                ).add(self._lease_token)
                self._lease_acquired = True
                self._needs_recovery = recovery_required
        except sqlite3.DatabaseError:
            self._rollback_quietly(connection)
            self._close_connection_locked()
            raise

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=5.0,
            check_same_thread=False,
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute("PRAGMA temp_store=MEMORY")
        except sqlite3.DatabaseError:
            # On Windows, an unclosed connection keeps the corrupt database
            # locked and prevents the quarantine rename in ensure_open().
            connection.close()
            raise
        return connection

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS entries (
                digest TEXT PRIMARY KEY,
                relative_path TEXT NOT NULL,
                container_schema INTEGER NOT NULL,
                decoder_contract INTEGER NOT NULL,
                payload_bytes INTEGER NOT NULL,
                file_bytes INTEGER NOT NULL,
                checksum TEXT NOT NULL,
                checksum_state TEXT NOT NULL,
                file_mtime_ns INTEGER NOT NULL,
                created_ns INTEGER NOT NULL,
                last_access_ns INTEGER NOT NULL,
                last_verified_ns INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS entries_lru
                ON entries(last_access_ns, created_ns, digest);
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value INTEGER NOT NULL
            );
            """
        )
        now = time.time_ns()
        for key, value in (
            ("index_schema", 1),
            ("indexed_bytes", 0),
            ("bytes_since_maintenance", 0),
            ("last_maintenance_ns", now),
            ("clean_shutdown", 1),
            ("owner_session", 0),
            ("active_leases", 0),
            ("recovery_required", 0),
        ):
            connection.execute(
                "INSERT OR IGNORE INTO metadata(key, value) VALUES (?, ?)",
                (key, value),
            )
        connection.commit()

    def _meta_int_locked(self, key: str, *, default: int = 0) -> int:
        row = self._connection.execute(  # type: ignore[union-attr]
            "SELECT value FROM metadata WHERE key = ?",
            (str(key),),
        ).fetchone()
        return int(row[0]) if row is not None else int(default)

    def _set_meta_locked(self, key: str, value: int) -> None:
        self._connection.execute(  # type: ignore[union-attr]
            """
            INSERT INTO metadata(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (str(key), int(value)),
        )

    def _mark_recovery_required_locked(self) -> None:
        self._needs_recovery = True
        if self._connection is None:
            return
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._set_meta_locked("recovery_required", 1)
            self._set_meta_locked("clean_shutdown", 0)
            connection.commit()
        except sqlite3.DatabaseError as exc:
            self._rollback_quietly(connection)
            self._database_failure_locked(exc)

    def _recovery_required_locked(self) -> bool:
        return self._meta_int_locked("recovery_required") != 0

    def _release_lease_locked(self, *, clean: bool) -> None:
        connection = self._connection
        if connection is None or not self._lease_acquired:
            return
        try:
            with _LEASE_REGISTRY_LOCK:
                connection.execute("BEGIN IMMEDIATE")
                if self._meta_int_locked("owner_session") != self._session_token:
                    connection.rollback()
                    self._forget_live_lease_locked()
                    return
                live_leases = _ACTIVE_SESSION_LEASES.get(
                    self._lease_registry_key,
                    set(),
                )
                remaining = len(live_leases - {self._lease_token})
                recovery_required = bool(
                    self._recovery_required_locked()
                    or self._needs_recovery
                    or not clean
                )
                self._set_meta_locked("active_leases", remaining)
                self._set_meta_locked("recovery_required", int(recovery_required))
                self._set_meta_locked(
                    "clean_shutdown",
                    int(remaining == 0 and clean and not recovery_required),
                )
                connection.commit()
                self._forget_live_lease_locked()
        except sqlite3.DatabaseError as exc:
            self._rollback_quietly(connection)
            raise self._database_failure_locked(exc) from exc

    def _forget_live_lease_locked(self) -> None:
        leases = _ACTIVE_SESSION_LEASES.get(self._lease_registry_key)
        if leases is not None:
            leases.discard(self._lease_token)
            if not leases:
                _ACTIVE_SESSION_LEASES.pop(self._lease_registry_key, None)
        self._lease_acquired = False

    def _database_failure_locked(
        self,
        exc: sqlite3.DatabaseError,
    ) -> SurfaceCacheIndexUnavailableError:
        self._needs_recovery = True
        self._rebuild_required = True
        connection = self._connection
        if connection is not None:
            self._rollback_quietly(connection)
            try:
                with _LEASE_REGISTRY_LOCK:
                    connection.execute("BEGIN IMMEDIATE")
                    self._set_meta_locked("recovery_required", 1)
                    self._set_meta_locked("clean_shutdown", 0)
                    connection.commit()
            except sqlite3.DatabaseError:
                self._rollback_quietly(connection)
        self._close_connection_locked()
        return SurfaceCacheIndexUnavailableError(
            f"surface cache index unavailable: {exc}"
        )

    @staticmethod
    def _rollback_quietly(connection: sqlite3.Connection | None) -> None:
        if connection is None:
            return
        try:
            connection.rollback()
        except sqlite3.DatabaseError:
            pass

    @staticmethod
    def _entry_from_row(row: sqlite3.Row) -> SurfaceCacheIndexEntry:
        return SurfaceCacheIndexEntry(
            digest=str(row["digest"]),
            relative_path=str(row["relative_path"]),
            container_schema=int(row["container_schema"]),
            decoder_contract=int(row["decoder_contract"]),
            payload_bytes=int(row["payload_bytes"]),
            file_bytes=int(row["file_bytes"]),
            checksum=int(str(row["checksum"]), 16),
            checksum_state=str(row["checksum_state"]),
            file_mtime_ns=int(row["file_mtime_ns"]),
            created_ns=int(row["created_ns"]),
            last_access_ns=int(row["last_access_ns"]),
            last_verified_ns=int(row["last_verified_ns"]),
        )

    def _discard_broken_index_locked(self) -> None:
        self._close_connection_locked()
        if self.path.exists():
            destination = self.path.with_name(
                f"{self.path.name}.corrupt-{time.time_ns()}"
            )
            try:
                os.replace(self.path, destination)
            except OSError:
                try:
                    self.path.unlink(missing_ok=True)
                except OSError:
                    pass
        for suffix in ("-wal", "-shm"):
            try:
                Path(f"{self.path}{suffix}").unlink(missing_ok=True)
            except OSError:
                pass

    def _close_connection_locked(self) -> None:
        connection = self._connection
        self._connection = None
        with _LEASE_REGISTRY_LOCK:
            self._forget_live_lease_locked()
        if connection is not None:
            try:
                connection.close()
            except sqlite3.DatabaseError:
                pass


__all__ = [
    "SurfaceCacheIndex",
    "SurfaceCacheIndexEntry",
    "SurfaceCacheIndexUnavailableError",
]

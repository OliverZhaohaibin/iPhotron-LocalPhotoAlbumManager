"""Transactional metadata index for the rebuildable Detail surface cache."""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Final

_ACCESS_BATCH: Final = 128
_ACCESS_INTERVAL_NS: Final = 30 * 1_000_000_000


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
        self.root = Path(root)
        self.path = self.root / "index.sqlite3"
        self._connection: sqlite3.Connection | None = None
        self._lock = RLock()
        self._pending_access: dict[str, int] = {}
        self._last_access_flush_ns = time.time_ns()
        self._needs_recovery = False

    @property
    def needs_recovery(self) -> bool:
        with self._lock:
            return self._needs_recovery

    def ensure_open(self) -> bool:
        with self._lock:
            if self._connection is not None:
                return True
            try:
                self.root.mkdir(parents=True, exist_ok=True)
                self._connection = self._connect()
                self._create_schema(self._connection)
            except (OSError, sqlite3.DatabaseError):
                self._discard_broken_index_locked()
                try:
                    self.root.mkdir(parents=True, exist_ok=True)
                    self._connection = self._connect()
                    self._create_schema(self._connection)
                    self._needs_recovery = True
                except (OSError, sqlite3.DatabaseError):
                    self._close_connection_locked()
                    return False
            connection = self._connection
            if connection is None:
                return False
            prior_clean = self._meta_int_locked("clean_shutdown", default=1)
            self._needs_recovery = self._needs_recovery or prior_clean != 1
            self._set_meta_locked("clean_shutdown", 0)
            connection.commit()
            return True

    def get(self, digest: str) -> SurfaceCacheIndexEntry | None:
        with self._lock:
            if not self.ensure_open():
                return None
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
            return self._entry_from_row(row) if row is not None else None

    def all_entries(self) -> tuple[SurfaceCacheIndexEntry, ...]:
        with self._lock:
            if not self.ensure_open():
                return ()
            rows = self._connection.execute(  # type: ignore[union-attr]
                """
                SELECT digest, relative_path, container_schema, decoder_contract,
                       payload_bytes, file_bytes, checksum, checksum_state,
                       file_mtime_ns, created_ns, last_access_ns, last_verified_ns
                FROM entries
                """
            ).fetchall()
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
            except sqlite3.DatabaseError:
                connection.rollback()  # type: ignore[union-attr]
                return False

    def remove(self, digest: str) -> None:
        with self._lock:
            if not self.ensure_open():
                return
            connection = self._connection
            try:
                row = connection.execute(  # type: ignore[union-attr]
                    "SELECT file_bytes FROM entries WHERE digest = ?",
                    (str(digest),),
                ).fetchone()
                if row is None:
                    return
                connection.execute("DELETE FROM entries WHERE digest = ?", (str(digest),))  # type: ignore[union-attr]
                self._set_meta_locked(
                    "indexed_bytes",
                    max(0, self._meta_int_locked("indexed_bytes") - int(row[0])),
                )
                connection.commit()  # type: ignore[union-attr]
            except sqlite3.DatabaseError:
                connection.rollback()  # type: ignore[union-attr]

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
            except sqlite3.DatabaseError:
                self._connection.rollback()  # type: ignore[union-attr]
                return False
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
            return (
                self._meta_int_locked("indexed_bytes") > max(0, int(budget_bytes))
                or self._meta_int_locked("bytes_since_maintenance") >= int(byte_interval)
                or now - self._meta_int_locked("last_maintenance_ns", default=now)
                >= int(time_interval_ns)
            )

    def lru_victims(self, *, limit: int) -> tuple[SurfaceCacheIndexEntry, ...]:
        with self._lock:
            if not self.ensure_open():
                return ()
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
            return tuple(self._entry_from_row(row) for row in rows)

    @property
    def indexed_bytes(self) -> int:
        with self._lock:
            if not self.ensure_open():
                return 0
            return self._meta_int_locked("indexed_bytes")

    def finish_maintenance(self) -> None:
        with self._lock:
            if not self.ensure_open():
                return
            self._set_meta_locked("bytes_since_maintenance", 0)
            self._set_meta_locked("last_maintenance_ns", time.time_ns())
            self._connection.commit()  # type: ignore[union-attr]

    def recalculate_indexed_bytes(self) -> int:
        """Repair the cached byte total after an exceptional recovery scan."""

        with self._lock:
            if not self.ensure_open():
                return 0
            row = self._connection.execute(  # type: ignore[union-attr]
                "SELECT COALESCE(SUM(file_bytes), 0) FROM entries"
            ).fetchone()
            total = max(0, int(row[0]) if row is not None else 0)
            self._set_meta_locked("indexed_bytes", total)
            self._connection.commit()  # type: ignore[union-attr]
            return total

    def mark_recovered(self) -> None:
        with self._lock:
            self._needs_recovery = False

    def close(self, *, clean: bool = True) -> None:
        with self._lock:
            self.flush_accesses(force=True)
            if self._connection is not None and clean:
                try:
                    self._set_meta_locked("clean_shutdown", 1)
                    self._connection.commit()
                except sqlite3.DatabaseError:
                    pass
            self._close_connection_locked()

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
        if connection is not None:
            try:
                connection.close()
            except sqlite3.DatabaseError:
                pass


__all__ = ["SurfaceCacheIndex", "SurfaceCacheIndexEntry"]

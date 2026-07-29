"""Crash-recoverable journal and event outbox for recognition mutations."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from iPhoto.sqlite_utils import configure_sqlite_connection, connect_sqlite


class RecognitionOperationKind(StrEnum):
    PEOPLE_SCAN_COMMIT = "people_scan_commit"
    PEOPLE_ADD_MANUAL_FACE = "people_add_manual_face"
    PEOPLE_DELETE_FACE = "people_delete_face"
    PEOPLE_MOVE_FACE = "people_move_face"
    PEOPLE_MOVE_FACE_NEW = "people_move_face_new"
    PEOPLE_MERGE = "people_merge"
    PEOPLE_CREATE_GROUP = "people_create_group"
    PEOPLE_DELETE_GROUP = "people_delete_group"
    PEOPLE_RENAME = "people_rename"
    RECOGNITION_MERGE = "recognition_merge"
    RECOGNITION_DETECTION_ASSIGNMENT = "recognition_detection_assignment"


class RecognitionOperationState(StrEnum):
    PREPARED = "prepared"
    APPLYING = "applying"
    COMMITTED = "committed"
    FINALIZED = "finalized"


@dataclass(frozen=True)
class RecognitionOperation:
    sequence: int
    operation_id: str
    kind: RecognitionOperationKind | str
    state: RecognitionOperationState
    payload: dict[str, Any]


@dataclass(frozen=True)
class RecognitionOutboxEvent:
    event_id: str
    operation_id: str
    event: dict[str, Any]


class RecognitionOperationJournal:
    """Persist recognition operations through prepared/applying/committed/finalized."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._initialize()

    def prepare(self, kind: str | RecognitionOperationKind, payload: dict[str, Any]) -> str:
        """Append an operation without applying the global-empty guard.

        Recovery and test setup use this primitive. New user mutations should
        use :meth:`try_prepare` so two owners cannot both start work.
        """

        operation_id = self._insert_operation(kind, payload, require_empty=False)
        if operation_id is None:
            raise RuntimeError("Unconditional recognition operation insert was rejected.")
        return operation_id

    def try_prepare(
        self,
        kind: str | RecognitionOperationKind,
        payload: dict[str, Any],
    ) -> str | None:
        """Atomically append an operation only when the global queue is empty."""

        return self._insert_operation(kind, payload, require_empty=True)

    def _insert_operation(
        self,
        kind: str | RecognitionOperationKind,
        payload: dict[str, Any],
        *,
        require_empty: bool,
    ) -> str | None:
        operation_id = uuid.uuid4().hex
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            if require_empty:
                pending = conn.execute(
                    "SELECT 1 FROM operations WHERE state != 'finalized' LIMIT 1"
                ).fetchone()
                if pending is not None:
                    conn.rollback()
                    return None
            conn.execute(
                """
                INSERT INTO operations (
                    operation_id, kind, state, payload_json, created_at, updated_at
                ) VALUES (?, ?, 'prepared', ?, datetime('now'), datetime('now'))
                """,
                (operation_id, str(kind), _json(payload)),
            )
            conn.commit()
        return operation_id

    def transition(
        self,
        operation_id: str,
        state: str | RecognitionOperationState,
        *,
        expected_state: str | RecognitionOperationState | None = None,
        payload: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> bool:
        normalized_state = str(state)
        if normalized_state not in {value.value for value in RecognitionOperationState}:
            raise ValueError(f"Unsupported recognition operation state: {state}")
        with closing(self._connect()) as conn:
            if payload is None:
                if expected_state is None:
                    cursor = conn.execute(
                        """
                        UPDATE operations
                        SET state = ?, last_error = ?, updated_at = datetime('now')
                        WHERE operation_id = ?
                        """,
                        (normalized_state, error, operation_id),
                    )
                else:
                    cursor = conn.execute(
                        """
                        UPDATE operations
                        SET state = ?, last_error = ?, updated_at = datetime('now')
                        WHERE operation_id = ? AND state = ?
                        """,
                        (normalized_state, error, operation_id, str(expected_state)),
                    )
            else:
                if expected_state is None:
                    cursor = conn.execute(
                        """
                        UPDATE operations
                        SET state = ?, payload_json = ?, last_error = ?,
                            updated_at = datetime('now')
                        WHERE operation_id = ?
                        """,
                        (normalized_state, _json(payload), error, operation_id),
                    )
                else:
                    cursor = conn.execute(
                        """
                        UPDATE operations
                        SET state = ?, payload_json = ?, last_error = ?,
                            updated_at = datetime('now')
                        WHERE operation_id = ? AND state = ?
                        """,
                        (
                            normalized_state,
                            _json(payload),
                            error,
                            operation_id,
                            str(expected_state),
                        ),
                    )
            conn.commit()
        return int(cursor.rowcount or 0) == 1

    def commit_outbox(
        self,
        operation_id: str,
        event: dict[str, Any],
        *,
        event_id: str | None = None,
    ) -> str:
        stable_event_id = str(event_id or operation_id)
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO event_outbox (
                    event_id, operation_id, event_json, delivery_state
                ) VALUES (?, ?, ?, 'pending')
                ON CONFLICT(operation_id) DO UPDATE SET
                    event_json = excluded.event_json,
                    delivery_state = CASE
                        WHEN event_outbox.delivery_state = 'dispatched' THEN 'dispatched'
                        ELSE 'pending'
                    END
                """,
                (stable_event_id, operation_id, _json(event)),
            )
            cursor = conn.execute(
                """
                UPDATE operations
                SET state = 'committed', updated_at = datetime('now')
                WHERE operation_id = ? AND state IN ('applying', 'committed')
                """,
                (operation_id,),
            )
            if int(cursor.rowcount or 0) != 1:
                conn.rollback()
                raise RuntimeError(
                    "Recognition operation cannot commit outbox from its current state: "
                    f"{operation_id}"
                )
            conn.commit()
        return stable_event_id

    def mark_dispatched(self, operation_id: str) -> bool:
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            event_cursor = conn.execute(
                """
                UPDATE event_outbox
                SET delivery_state = 'dispatched'
                WHERE operation_id = ? AND delivery_state IN ('pending', 'dispatched')
                """,
                (operation_id,),
            )
            operation_cursor = conn.execute(
                """
                UPDATE operations
                SET state = 'finalized', updated_at = datetime('now')
                WHERE operation_id = ? AND state = 'committed'
                """,
                (operation_id,),
            )
            if int(event_cursor.rowcount or 0) != 1 or int(operation_cursor.rowcount or 0) != 1:
                conn.rollback()
                return False
            conn.commit()
        return True

    def mark_published(self, operation_id: str) -> None:
        """Compatibility alias for callers migrating to dispatcher terminology."""

        if not self.mark_dispatched(operation_id):
            raise RuntimeError(
                f"Recognition event cannot be dispatched from its current state: {operation_id}"
            )

    def pending_events(self) -> tuple[RecognitionOutboxEvent, ...]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT event_id, operation_id, event_json
                FROM event_outbox
                WHERE delivery_state = 'pending'
                ORDER BY rowid ASC
                """
            ).fetchall()
        return tuple(
            RecognitionOutboxEvent(
                event_id=str(row["event_id"]),
                operation_id=str(row["operation_id"]),
                event=json.loads(str(row["event_json"] or "{}")),
            )
            for row in rows
        )

    def unfinished(self) -> tuple[RecognitionOperation, ...]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT sequence, operation_id, kind, state, payload_json
                FROM operations
                WHERE state != 'finalized'
                ORDER BY sequence ASC
                """
            ).fetchall()
        return tuple(
            RecognitionOperation(
                sequence=int(row["sequence"]),
                operation_id=str(row["operation_id"]),
                kind=_parse_operation_kind(str(row["kind"])),
                state=RecognitionOperationState(str(row["state"])),
                payload=json.loads(str(row["payload_json"] or "{}")),
            )
            for row in rows
        )

    def unfinished_head(self) -> RecognitionOperation | None:
        pending = self.unfinished()
        return pending[0] if pending else None

    def _initialize(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            existing = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'operations'"
            ).fetchone()
            if existing is None:
                self._create_operations_table(conn, "operations")
            else:
                columns = {
                    str(row["name"])
                    for row in conn.execute("PRAGMA table_info(operations)").fetchall()
                }
                if "sequence" not in columns:
                    conn.execute("BEGIN IMMEDIATE")
                    self._create_operations_table(conn, "operations_v2")
                    conn.execute(
                        """
                        INSERT INTO operations_v2 (
                            operation_id, kind, state, payload_json, last_error,
                            created_at, updated_at
                        )
                        SELECT operation_id, kind, state, payload_json, last_error,
                               created_at, updated_at
                        FROM operations
                        ORDER BY created_at ASC, operation_id ASC
                        """
                    )
                    conn.execute("DROP TABLE operations")
                    conn.execute("ALTER TABLE operations_v2 RENAME TO operations")
            self._initialize_outbox(conn)
            conn.commit()

    @staticmethod
    def _initialize_outbox(conn: sqlite3.Connection) -> None:
        existing = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'event_outbox'"
        ).fetchone()
        if existing is None:
            conn.execute(
                """
                CREATE TABLE event_outbox (
                    event_id TEXT NOT NULL PRIMARY KEY,
                    operation_id TEXT NOT NULL UNIQUE,
                    event_json TEXT NOT NULL,
                    delivery_state TEXT NOT NULL DEFAULT 'pending'
                )
                """
            )
            return
        columns = {
            str(row["name"]) for row in conn.execute("PRAGMA table_info(event_outbox)").fetchall()
        }
        if {"event_id", "delivery_state"}.issubset(columns):
            return
        conn.execute("ALTER TABLE event_outbox RENAME TO event_outbox_legacy")
        conn.execute(
            """
            CREATE TABLE event_outbox (
                event_id TEXT NOT NULL PRIMARY KEY,
                operation_id TEXT NOT NULL UNIQUE,
                event_json TEXT NOT NULL,
                delivery_state TEXT NOT NULL DEFAULT 'pending'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO event_outbox (
                event_id, operation_id, event_json, delivery_state
            )
            SELECT operation_id, operation_id, event_json,
                   CASE WHEN published = 1 THEN 'dispatched' ELSE 'pending' END
            FROM event_outbox_legacy
            """
        )
        conn.execute("DROP TABLE event_outbox_legacy")

    @staticmethod
    def _create_operations_table(conn: sqlite3.Connection, name: str) -> None:
        if name not in {"operations", "operations_v2"}:
            raise ValueError(f"Unsupported operations table name: {name}")
        conn.execute(
            f"""
            CREATE TABLE {name} (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_id TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL,
                state TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

    def _connect(self) -> sqlite3.Connection:
        conn = connect_sqlite(self._db_path)
        conn.row_factory = sqlite3.Row
        configure_sqlite_connection(conn, self._db_path, wal=True)
        return conn


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _parse_operation_kind(value: str) -> RecognitionOperationKind | str:
    try:
        return RecognitionOperationKind(value)
    except ValueError:
        # Preserve unknown legacy/future values so FIFO recovery blocks at the
        # exact operation instead of silently discarding data.
        return value


__all__ = [
    "RecognitionOperation",
    "RecognitionOperationJournal",
    "RecognitionOperationKind",
    "RecognitionOperationState",
    "RecognitionOutboxEvent",
]

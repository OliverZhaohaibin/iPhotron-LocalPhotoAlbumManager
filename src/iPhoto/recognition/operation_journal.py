"""Crash-recoverable journal and event outbox for recognition mutations."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from iPhoto.sqlite_utils import configure_sqlite_connection, connect_sqlite


@dataclass(frozen=True)
class RecognitionOperation:
    sequence: int
    operation_id: str
    kind: str
    state: str
    payload: dict[str, Any]


class RecognitionOperationJournal:
    """Persist recognition operations through prepared/applying/committed/finalized."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._initialize()

    def prepare(self, kind: str, payload: dict[str, Any]) -> str:
        """Append an operation without applying the global-empty guard.

        Recovery and test setup use this primitive. New user mutations should
        use :meth:`try_prepare` so two owners cannot both start work.
        """

        operation_id = self._insert_operation(kind, payload, require_empty=False)
        if operation_id is None:
            raise RuntimeError("Unconditional recognition operation insert was rejected.")
        return operation_id

    def try_prepare(self, kind: str, payload: dict[str, Any]) -> str | None:
        """Atomically append an operation only when the global queue is empty."""

        return self._insert_operation(kind, payload, require_empty=True)

    def _insert_operation(
        self,
        kind: str,
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
        state: str,
        *,
        payload: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        if state not in {"prepared", "applying", "committed", "finalized"}:
            raise ValueError(f"Unsupported recognition operation state: {state}")
        with closing(self._connect()) as conn:
            if payload is None:
                conn.execute(
                    """
                    UPDATE operations
                    SET state = ?, last_error = ?, updated_at = datetime('now')
                    WHERE operation_id = ?
                    """,
                    (state, error, operation_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE operations
                    SET state = ?, payload_json = ?, last_error = ?,
                        updated_at = datetime('now')
                    WHERE operation_id = ?
                    """,
                    (state, _json(payload), error, operation_id),
                )
            conn.commit()

    def commit_outbox(self, operation_id: str, event: dict[str, Any]) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT INTO event_outbox (operation_id, event_json, published)
                VALUES (?, ?, 0)
                ON CONFLICT(operation_id) DO UPDATE SET event_json = excluded.event_json
                """,
                (operation_id, _json(event)),
            )
            conn.execute(
                """
                UPDATE operations
                SET state = 'committed', updated_at = datetime('now')
                WHERE operation_id = ?
                """,
                (operation_id,),
            )
            conn.commit()

    def mark_published(self, operation_id: str) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "UPDATE event_outbox SET published = 1 WHERE operation_id = ?",
                (operation_id,),
            )
            conn.execute(
                """
                UPDATE operations
                SET state = 'finalized', updated_at = datetime('now')
                WHERE operation_id = ?
                """,
                (operation_id,),
            )
            conn.commit()

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
                kind=str(row["kind"]),
                state=str(row["state"]),
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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS event_outbox (
                    operation_id TEXT PRIMARY KEY,
                    event_json TEXT NOT NULL,
                    published INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.commit()

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


__all__ = ["RecognitionOperation", "RecognitionOperationJournal"]

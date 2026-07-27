from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from iPhoto.recognition.operation_journal import RecognitionOperationJournal


def test_unfinished_operations_follow_insertion_sequence(tmp_path: Path) -> None:
    journal = RecognitionOperationJournal(tmp_path / "operations.db")

    inserted = [journal.prepare("test", {"index": index}) for index in range(20)]

    pending = journal.unfinished()
    assert [operation.operation_id for operation in pending] == inserted
    assert [operation.sequence for operation in pending] == list(range(1, 21))


def test_legacy_operations_schema_migrates_deterministically(tmp_path: Path) -> None:
    db_path = tmp_path / "operations.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE operations (
                operation_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                state TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.executemany(
            """
            INSERT INTO operations (
                operation_id, kind, state, payload_json, created_at, updated_at
            ) VALUES (?, 'test', 'applying', ?, '2026-07-27 10:00:00',
                      '2026-07-27 10:00:00')
            """,
            [(operation_id, json.dumps({"id": operation_id})) for operation_id in ("c", "a", "b")],
        )

    journal = RecognitionOperationJournal(db_path)

    assert [operation.operation_id for operation in journal.unfinished()] == ["a", "b", "c"]
    with sqlite3.connect(db_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(operations)")}
    assert "sequence" in columns


def test_try_prepare_allows_only_one_global_owner(tmp_path: Path) -> None:
    db_path = tmp_path / "operations.db"
    RecognitionOperationJournal(db_path)

    def prepare(index: int) -> str | None:
        return RecognitionOperationJournal(db_path).try_prepare(
            f"owner-{index}",
            {"index": index},
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(prepare, range(8)))

    assert sum(result is not None for result in results) == 1
    assert len(RecognitionOperationJournal(db_path).unfinished()) == 1

from __future__ import annotations

from pathlib import Path

from iPhoto import sqlite_utils


class _FakeConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: str):
        self.statements.append(statement)
        return None


def test_configure_sqlite_connection_initializes_wal_once_per_path(tmp_path: Path) -> None:
    sqlite_utils._WAL_INITIALIZED_PATHS.clear()
    db_path = tmp_path / "index.db"
    first = _FakeConnection()
    second = _FakeConnection()

    sqlite_utils.configure_sqlite_connection(first, db_path, foreign_keys=True, wal=True)
    sqlite_utils.configure_sqlite_connection(second, db_path, foreign_keys=True, wal=True)

    assert "PRAGMA busy_timeout=5000" in first.statements
    assert "PRAGMA foreign_keys=ON" in first.statements
    assert first.statements.count("PRAGMA journal_mode=WAL") == 1
    assert "PRAGMA busy_timeout=5000" in second.statements
    assert "PRAGMA foreign_keys=ON" in second.statements
    assert "PRAGMA journal_mode=WAL" not in second.statements
    assert "PRAGMA synchronous=NORMAL" in second.statements

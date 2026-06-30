"""Shared SQLite connection configuration helpers."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

SQLITE_BUSY_TIMEOUT_MS = 5000

_WAL_INITIALIZED_PATHS: set[Path] = set()
_WAL_LOCK = threading.Lock()


def connect_sqlite(db_path: Path, *, check_same_thread: bool = True) -> sqlite3.Connection:
    """Open a SQLite connection with the project-wide busy timeout."""

    return sqlite3.connect(
        Path(db_path),
        timeout=SQLITE_BUSY_TIMEOUT_MS / 1000,
        check_same_thread=check_same_thread,
    )


def configure_sqlite_connection(
    conn: sqlite3.Connection,
    db_path: Path,
    *,
    foreign_keys: bool = False,
    wal: bool = False,
) -> None:
    """Apply common per-connection pragmas, and initialize WAL once per DB path."""

    conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    if foreign_keys:
        conn.execute("PRAGMA foreign_keys=ON")
    if not wal:
        return

    resolved_path = Path(db_path).resolve()
    with _WAL_LOCK:
        if resolved_path in _WAL_INITIALIZED_PATHS:
            conn.execute("PRAGMA synchronous=NORMAL")
            return
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        _WAL_INITIALIZED_PATHS.add(resolved_path)

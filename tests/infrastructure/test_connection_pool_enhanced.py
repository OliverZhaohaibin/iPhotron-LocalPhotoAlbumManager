"""Enhanced tests for the ConnectionPool (Phase 1/2 refactoring)."""

import pytest
import sqlite3
import threading
import time
from pathlib import Path

from iPhoto.infrastructure.db.pool import ConnectionPool
from iPhoto.errors import ConnectionPoolExhausted


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    return path


def test_lazy_creation(db_path):
    pool = ConnectionPool(db_path, pool_size=3)
    assert pool._created == 0
    with pool.connection() as conn:
        assert conn is not None
    assert pool._created == 1
    pool.close_all()


def test_pool_exhausted_raises_error(db_path):
    pool = ConnectionPool(db_path, pool_size=1, timeout=0.2)
    conn1 = pool._acquire()
    with pytest.raises(ConnectionPoolExhausted):
        pool._acquire()
    pool._release(conn1)
    pool.close_all()


def test_timeout_configurable(db_path):
    pool = ConnectionPool(db_path, pool_size=1, timeout=0.1)
    conn1 = pool._acquire()
    start = time.monotonic()
    with pytest.raises(ConnectionPoolExhausted):
        pool._acquire()
    elapsed = time.monotonic() - start
    assert elapsed < 1.0  # should fail fast, well under 1s
    pool._release(conn1)
    pool.close_all()


def test_concurrent_access(db_path):
    pool = ConnectionPool(db_path, pool_size=4, timeout=10.0)
    errors = []

    def worker(n):
        try:
            with pool.connection() as conn:
                conn.execute("INSERT INTO t (id) VALUES (?)", (n,))
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0
    pool.close_all()


def test_rollback_on_exception(db_path):
    pool = ConnectionPool(db_path, pool_size=1)

    with pytest.raises(RuntimeError):
        with pool.connection() as conn:
            conn.execute("INSERT INTO t (id) VALUES (100)")
            raise RuntimeError("force rollback")

    # The INSERT should have been rolled back
    with pool.connection() as conn:
        row = conn.execute("SELECT COUNT(*) FROM t WHERE id=100").fetchone()
        assert row[0] == 0

    pool.close_all()

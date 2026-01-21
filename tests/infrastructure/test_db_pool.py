import pytest
import sqlite3
from pathlib import Path
from src.iPhoto.infrastructure.db.pool import ConnectionPool

@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, name TEXT)")
    conn.close()
    return path

def test_connection_acquisition(db_path):
    pool = ConnectionPool(db_path, pool_size=1)

    with pool.connection() as conn:
        assert isinstance(conn, sqlite3.Connection)
        cursor = conn.execute("SELECT 1")
        assert cursor.fetchone()[0] == 1

def test_connection_recycling(db_path):
    pool = ConnectionPool(db_path, pool_size=1)

    conn_id = None
    with pool.connection() as conn:
        conn_id = id(conn)

    with pool.connection() as conn:
        assert id(conn) == conn_id

def test_transaction_commit(db_path):
    pool = ConnectionPool(db_path)

    with pool.connection() as conn:
        conn.execute("INSERT INTO test (name) VALUES (?)", ("foo",))

    # Verify in new connection
    conn = sqlite3.connect(db_path)
    cursor = conn.execute("SELECT name FROM test")
    assert cursor.fetchone()[0] == "foo"
    conn.close()

def test_transaction_rollback(db_path):
    pool = ConnectionPool(db_path)

    try:
        with pool.connection() as conn:
            conn.execute("INSERT INTO test (name) VALUES (?)", ("bar",))
            raise RuntimeError("oops")
    except RuntimeError:
        pass

    conn = sqlite3.connect(db_path)
    cursor = conn.execute("SELECT name FROM test WHERE name='bar'")
    assert cursor.fetchone() is None
    conn.close()

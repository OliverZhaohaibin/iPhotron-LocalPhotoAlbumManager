import sqlite3
import queue
from contextlib import contextmanager
from pathlib import Path

class ConnectionPool:
    def __init__(self, db_path: Path, pool_size: int = 5):
        self._db_path = db_path
        self._pool = queue.Queue(maxsize=pool_size)
        for _ in range(pool_size):
            conn = sqlite3.connect(db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            self._pool.put(conn)

    @contextmanager
    def connection(self):
        conn = self._pool.get()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._pool.put(conn)

    def close_all(self):
        while not self._pool.empty():
            conn = self._pool.get()
            conn.close()

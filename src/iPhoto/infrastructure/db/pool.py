import sqlite3
import queue
import threading
from contextlib import contextmanager
from pathlib import Path

from iPhoto.errors import ConnectionPoolExhausted


class ConnectionPool:
    def __init__(self, db_path: Path, pool_size: int = 5, timeout: float = 30.0):
        self._db_path = db_path
        self._pool_size = pool_size
        self._timeout = timeout
        self._pool: queue.Queue = queue.Queue(maxsize=pool_size)
        self._created = 0
        self._lock = threading.Lock()

    def _create_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _acquire(self) -> sqlite3.Connection:
        # Try to get an existing connection without blocking
        try:
            return self._pool.get_nowait()
        except queue.Empty:
            pass

        # Lazily create a new connection if under the limit
        with self._lock:
            if self._created < self._pool_size:
                self._created += 1
                return self._create_connection()

        # All connections created and in use; wait with timeout
        try:
            return self._pool.get(timeout=self._timeout)
        except queue.Empty:
            raise ConnectionPoolExhausted(
                f"No connections available within {self._timeout}s "
                f"(pool_size={self._pool_size})"
            )

    def _release(self, conn: sqlite3.Connection):
        try:
            self._pool.put_nowait(conn)
        except queue.Full:
            conn.close()

    @contextmanager
    def connection(self):
        conn = self._acquire()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._release(conn)

    def close_all(self):
        while not self._pool.empty():
            try:
                conn = self._pool.get_nowait()
                conn.close()
            except queue.Empty:
                break

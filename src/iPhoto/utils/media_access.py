"""Process-local coordination for source media file access."""

from __future__ import annotations

import os
import threading
import weakref
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path


class _MediaPathLock:
    """Writer-preferring read/write lock for one normalized media path."""

    def __init__(self) -> None:
        self._condition = threading.Condition(threading.RLock())
        self._readers: dict[int, int] = {}
        self._writer: int | None = None
        self._write_depth = 0
        self._waiting_writers = 0

    def acquire_read(self) -> None:
        ident = threading.get_ident()
        with self._condition:
            if self._writer == ident or ident in self._readers:
                self._readers[ident] = self._readers.get(ident, 0) + 1
                return
            while self._writer is not None or self._waiting_writers > 0:
                self._condition.wait()
            self._readers[ident] = self._readers.get(ident, 0) + 1

    def release_read(self) -> None:
        ident = threading.get_ident()
        with self._condition:
            depth = self._readers.get(ident, 0)
            if depth <= 1:
                self._readers.pop(ident, None)
            else:
                self._readers[ident] = depth - 1
            if not self._readers:
                self._condition.notify_all()

    def acquire_write(self) -> None:
        ident = threading.get_ident()
        with self._condition:
            if self._writer == ident:
                self._write_depth += 1
                return
            self._waiting_writers += 1
            try:
                while self._writer is not None or self._has_other_readers(ident):
                    self._condition.wait()
                self._writer = ident
                self._write_depth = 1
            finally:
                self._waiting_writers -= 1

    def release_write(self) -> None:
        ident = threading.get_ident()
        with self._condition:
            if self._writer != ident:
                raise RuntimeError("Cannot release a media write lock owned by another thread")
            self._write_depth -= 1
            if self._write_depth <= 0:
                self._writer = None
                self._write_depth = 0
                self._condition.notify_all()

    def _has_other_readers(self, ident: int) -> bool:
        return any(
            reader_ident != ident and depth > 0
            for reader_ident, depth in self._readers.items()
        )


class MediaAccessCoordinator:
    """Coordinate read/write access to media files inside this process."""

    def __init__(self) -> None:
        self._locks: weakref.WeakValueDictionary[str, _MediaPathLock] = (
            weakref.WeakValueDictionary()
        )
        self._registry_lock = threading.Lock()

    @contextmanager
    def read(self, path: Path) -> Iterator[None]:
        lock = self._lock_for(path)
        lock.acquire_read()
        try:
            yield
        finally:
            lock.release_read()

    @contextmanager
    def write(self, path: Path) -> Iterator[None]:
        lock = self._lock_for(path)
        lock.acquire_write()
        try:
            yield
        finally:
            lock.release_write()

    @contextmanager
    def read_many(self, paths: Iterable[Path]) -> Iterator[None]:
        keys = sorted({self._key_for(path) for path in paths})
        with self._registry_lock:
            locks = [self._lock_for_key(key) for key in keys]
        acquired: list[_MediaPathLock] = []
        try:
            for lock in locks:
                lock.acquire_read()
                acquired.append(lock)
            yield
        finally:
            for lock in reversed(acquired):
                lock.release_read()

    def _lock_for(self, path: Path) -> _MediaPathLock:
        key = self._key_for(path)
        with self._registry_lock:
            return self._lock_for_key(key)

    def _lock_for_key(self, key: str) -> _MediaPathLock:
        lock = self._locks.get(key)
        if lock is None:
            lock = _MediaPathLock()
            self._locks[key] = lock
        return lock

    @staticmethod
    def _key_for(path: Path) -> str:
        try:
            raw = os.path.realpath(path)
        except OSError:
            raw = str(Path(path).absolute())
        return os.path.normcase(raw)


media_access = MediaAccessCoordinator()


__all__ = ["MediaAccessCoordinator", "media_access"]

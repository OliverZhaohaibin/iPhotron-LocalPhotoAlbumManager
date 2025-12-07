"""Persistent storage for album index rows."""

from __future__ import annotations

import json
from pathlib import Path
from contextlib import contextmanager
from typing import Dict, Iterable, Iterator, List, Optional

from ..config import WORK_DIR_NAME
from .lock import FileLock
from ..errors import IndexCorruptedError
from ..utils.jsonio import atomic_write_text


class IndexStore:
    """Read/write helper for ``index.jsonl`` files."""

    def __init__(self, album_root: Path):
        self.album_root = album_root
        self.path = album_root / WORK_DIR_NAME / "index.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._pending_transaction = False
        self._batch_cache: Optional[Dict[str, Dict[str, object]]] = None

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Batch multiple updates into a single disk write."""
        if self._pending_transaction:
            # Nested transactions are not supported, just yield
            yield
            return

        with FileLock(self.album_root, "index"):
            self._pending_transaction = True
            try:
                # Pre-load data
                self._batch_cache = {
                    Path(str(r["rel"])).as_posix(): r for r in self.read_all()
                }
                yield
                # Commit: Write all data from _batch_cache to disk
                if self._batch_cache is not None:
                    self.write_rows(self._batch_cache.values(), locked=True)
            finally:
                self._pending_transaction = False
                self._batch_cache = None

    def write_rows(self, rows: Iterable[Dict[str, object]], *, locked: bool = False) -> None:
        """Rewrite the entire index with *rows*."""

        payload = "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows)
        if payload:
            payload += "\n"

        if locked:
            atomic_write_text(self.path, payload)
        else:
            with FileLock(self.album_root, "index"):
                atomic_write_text(self.path, payload)

    def read_all(self) -> Iterator[Dict[str, object]]:
        """Yield all rows from the index."""

        if not self.path.exists():
            return iter(())

        def _iterator() -> Iterator[Dict[str, object]]:
            try:
                with self.path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        line = line.strip()
                        if not line:
                            continue
                        yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise IndexCorruptedError(f"Corrupted index file: {self.path}") from exc

        return _iterator()

    def upsert_row(self, rel: str, row: Dict[str, object]) -> None:
        """Insert or update a single row identified by *rel*."""

        if self._pending_transaction and self._batch_cache is not None:
            rel_key = Path(str(rel)).as_posix()
            self._batch_cache[rel_key] = row
        else:
            data = {existing["rel"]: existing for existing in self.read_all()}
            data[rel] = row
            self.write_rows(data.values())

    def remove_rows(self, rels: Iterable[str]) -> None:
        """Drop any index rows whose ``rel`` key matches *rels*.

        The helper loads the existing payload once, filters out the requested
        entries, and rewrites the file atomically.  This mirrors the behaviour
        of :meth:`write_rows` so concurrent processes never observe a partially
        written ``index.jsonl`` file.
        """

        removable = {Path(rel).as_posix() for rel in rels}
        if not removable:
            return

        if self._pending_transaction and self._batch_cache is not None:
            for rel_key in removable:
                self._batch_cache.pop(rel_key, None)
        else:
            remaining: List[Dict[str, object]] = []
            removed_any = False
            for row in self.read_all():
                rel_key = Path(str(row.get("rel", ""))).as_posix()
                if rel_key in removable:
                    removed_any = True
                    continue
                remaining.append(row)

            # Rewrite the file only when something actually changed.  Skipping the
            # write keeps the lock duration short if the target rows were absent.
            if not removed_any:
                return

            self.write_rows(remaining)

    def append_rows(self, rows: Iterable[Dict[str, object]]) -> None:
        """Merge *rows* into the index, replacing duplicates by ``rel`` key.

        Appending new entries requires keeping existing rows intact.  The
        implementation reads the current snapshot once, merges the incoming
        payload, and relies on :meth:`write_rows` to persist the result using an
        atomic rename so interrupted writes cannot corrupt the cache.
        """

        additions = list(rows)
        if not additions:
            return

        if self._pending_transaction and self._batch_cache is not None:
            for row in additions:
                rel_value = row.get("rel")
                if rel_value is None:
                    continue
                rel_key = Path(str(rel_value)).as_posix()
                self._batch_cache[rel_key] = row
        else:
            merged: Dict[str, Dict[str, object]] = {}
            for row in self.read_all():
                rel_key = Path(str(row.get("rel", ""))).as_posix()
                merged[rel_key] = row

            changed = False
            for row in additions:
                rel_value = row.get("rel")
                if rel_value is None:
                    continue
                rel_key = Path(str(rel_value)).as_posix()
                existing = merged.get(rel_key)
                if existing != row:
                    changed = True
                merged[rel_key] = row

            if not changed:
                return

            self.write_rows(merged.values())

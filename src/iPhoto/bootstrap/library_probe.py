"""Out-of-process probing for potentially slow or unavailable libraries."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from ..config import (
    ALBUM_MANIFEST_NAMES,
    ALL_WORK_DIR_NAMES,
    EXPORT_DIR_NAME,
    RECENTLY_DELETED_DIR_NAME,
    WORK_DIR_NAME,
)

_RESERVED_NAMES = frozenset(
    name.casefold()
    for name in (*ALL_WORK_DIR_NAMES, RECENTLY_DELETED_DIR_NAME, EXPORT_DIR_NAME)
)


@dataclass(frozen=True, slots=True)
class LibraryProbeRequest:
    request_id: str
    path: str
    timeout_ms: int = 3000

    @classmethod
    def create(cls, path: Path, *, timeout_ms: int = 3000) -> LibraryProbeRequest:
        return cls(uuid.uuid4().hex, os.fspath(path), max(250, int(timeout_ms)))


@dataclass(frozen=True, slots=True)
class PreparedAlbum:
    path: str
    level: int
    title: str
    has_manifest: bool


@dataclass(frozen=True, slots=True)
class PreparedLibrary:
    request_id: str
    root: Path
    database_path: Path
    schema_version: int
    albums: tuple[PreparedAlbum, ...]
    storage_kind: str
    scan_complete: bool
    warnings: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["root"] = os.fspath(self.root)
        payload["database_path"] = os.fspath(self.database_path)
        payload["albums"] = [asdict(album) for album in self.albums]
        payload["warnings"] = list(self.warnings)
        return payload

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> PreparedLibrary:
        return cls(
            request_id=str(payload["request_id"]),
            root=Path(payload["root"]),
            database_path=Path(payload["database_path"]),
            schema_version=int(payload.get("schema_version", 0)),
            albums=tuple(PreparedAlbum(**item) for item in payload.get("albums", ())),
            storage_kind=str(payload.get("storage_kind") or "local"),
            scan_complete=bool(payload.get("scan_complete", False)),
            warnings=tuple(str(item) for item in payload.get("warnings", ())),
        )


@dataclass(frozen=True, slots=True)
class LibraryProbeFailure:
    request_id: str
    message: str
    exception_type: str
    timed_out: bool = False


def _album_description(path: Path) -> tuple[str, bool]:
    for name in ALBUM_MANIFEST_NAMES:
        manifest = path / name
        if not manifest.exists():
            continue
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            return str(payload.get("title") or path.name), True
        except Exception:  # noqa: BLE001 - an invalid album must not fail the library
            return path.name, True
    return path.name, (path / ".iphoto.album").exists()


def _album_dirs(root: Path) -> list[Path]:
    return sorted(
        (
            entry
            for entry in root.iterdir()
            if entry.is_dir() and entry.name.casefold() not in _RESERVED_NAMES
        ),
        key=lambda item: item.name.casefold(),
    )


def _snapshot_albums(root: Path) -> tuple[PreparedAlbum, ...]:
    result: list[PreparedAlbum] = []
    for top_level in _album_dirs(root):
        title, has_manifest = _album_description(top_level)
        result.append(PreparedAlbum(str(top_level), 1, title, has_manifest))
        for child in _album_dirs(top_level):
            title, has_manifest = _album_description(child)
            result.append(PreparedAlbum(str(child), 2, title, has_manifest))
    return tuple(result)


def _database_snapshot(root: Path) -> tuple[Path, int, bool]:
    database_path = root / WORK_DIR_NAME / "global_index.db"
    database_path.parent.mkdir(parents=True, exist_ok=True)
    # Schema creation/migration is intentionally performed in this killable
    # helper, never on the GUI thread.  Versioned migrations make the common
    # already-current path a constant-time PRAGMA check.
    from ..cache.index_store.migrations import CURRENT_SCHEMA_VERSION, SchemaMigrator

    with sqlite3.connect(database_path, timeout=0.5) as connection:
        existing_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if existing_version > CURRENT_SCHEMA_VERSION:
            raise RuntimeError(
                f"Library index schema {existing_version} is newer than supported "
                f"schema {CURRENT_SCHEMA_VERSION}"
            )
        SchemaMigrator.initialize_schema(connection)
        schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        try:
            row = connection.execute(
                "SELECT 1 FROM scan_jobs WHERE status = 'completed' "
                "AND scope = 'library' AND root = ? LIMIT 1",
                (root.as_posix(),),
            ).fetchone()
        except sqlite3.OperationalError:
            row = None
    return database_path, schema_version, row is not None


def _storage_kind(path: Path, elapsed_ms: float) -> str:
    raw = os.fspath(path)
    if os.name == "nt" and raw.startswith(("\\\\", "//")):
        return "network"
    if elapsed_ms >= 500:
        return "slow"
    return "local"


def probe_library(request: LibraryProbeRequest) -> PreparedLibrary:
    """Synchronously inspect one library; callers should isolate this in a process."""

    started = time.perf_counter_ns()
    root = Path(request.path).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise NotADirectoryError(f"Library path is not a directory: {request.path}")
    albums = _snapshot_albums(root)
    database_path, schema_version, scan_complete = _database_snapshot(root)
    elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000.0
    return PreparedLibrary(
        request_id=request.request_id,
        root=root,
        database_path=database_path,
        schema_version=schema_version,
        albums=albums,
        storage_kind=_storage_kind(root, elapsed_ms),
        scan_complete=scan_complete,
    )


class LibraryProbeController(QObject):
    """Run library probes in a killable child process with stale-result rejection."""

    ready = Signal(object)
    failed = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._process: QProcess | None = None
        self._request: LibraryProbeRequest | None = None
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(self._on_timeout)

    def start(self, request: LibraryProbeRequest) -> None:
        self.cancel()
        self._request = request
        process = QProcess(self)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        process.finished.connect(self._on_finished)
        self._process = process
        payload = json.dumps(asdict(request), ensure_ascii=False)
        if getattr(sys, "frozen", False) or "__compiled__" in globals():
            arguments = ["--startup-library-probe", payload]
        else:
            arguments = ["-m", "iPhoto.bootstrap.library_probe", payload]
        process.start(sys.executable, arguments)
        self._timeout.start(request.timeout_ms)

    def cancel(self) -> None:
        self._timeout.stop()
        process = self._process
        self._process = None
        self._request = None
        if process is None:
            return
        if process.state() != QProcess.ProcessState.NotRunning:
            process.kill()
            process.waitForFinished(250)
        process.deleteLater()

    def _on_timeout(self) -> None:
        request = self._request
        if request is None:
            return
        failure = LibraryProbeFailure(
            request_id=request.request_id,
            message=f"Library probe timed out after {request.timeout_ms} ms",
            exception_type="TimeoutError",
            timed_out=True,
        )
        self.cancel()
        self.failed.emit(failure)

    def _on_finished(self, _exit_code: int, _status: QProcess.ExitStatus) -> None:
        process = self._process
        request = self._request
        if process is None or request is None:
            return
        self._timeout.stop()
        stdout = bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace")
        stderr = bytes(process.readAllStandardError()).decode("utf-8", errors="replace")
        self._process = None
        self._request = None
        process.deleteLater()
        try:
            envelope = json.loads(stdout)
            if not envelope.get("ok"):
                raise RuntimeError(str(envelope.get("error") or stderr or "Library probe failed"))
            prepared = PreparedLibrary.from_payload(envelope["prepared"])
            if prepared.request_id != request.request_id:
                return
        except Exception as exc:  # noqa: BLE001 - child-process protocol boundary
            self.failed.emit(
                LibraryProbeFailure(
                    request_id=request.request_id,
                    message=str(exc) or stderr or "Library probe failed",
                    exception_type=type(exc).__name__,
                )
            )
            return
        self.ready.emit(prepared)


def _main(arguments: list[str]) -> int:
    try:
        payload = json.loads(arguments[0])
        request = LibraryProbeRequest(**payload)
        prepared = probe_library(request)
        envelope = {"ok": True, "prepared": prepared.to_payload()}
    except Exception as exc:  # noqa: BLE001 - helper process boundary
        envelope = {
            "ok": False,
            "error": str(exc) or type(exc).__name__,
            "exception_type": type(exc).__name__,
        }
    sys.stdout.write(json.dumps(envelope, ensure_ascii=False))
    sys.stdout.flush()
    return 0 if envelope["ok"] else 2


if __name__ == "__main__":  # pragma: no cover - exercised through QProcess
    raise SystemExit(_main(sys.argv[1:]))


__all__ = [
    "LibraryProbeController",
    "LibraryProbeFailure",
    "LibraryProbeRequest",
    "PreparedAlbum",
    "PreparedLibrary",
    "probe_library",
]

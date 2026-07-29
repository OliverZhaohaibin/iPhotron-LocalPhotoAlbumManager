"""Out-of-process probing for potentially slow or unavailable libraries."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import uuid
from contextlib import closing
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
PROBE_PROTOCOL_VERSION = 1
MAX_STDOUT_BYTES = 4 * 1024 * 1024
MAX_STDERR_BYTES = 16 * 1024
MAX_ALBUMS = 10_000

_FAILURE_MESSAGES = {
    "db_locked": "The photo library index is currently in use. Please retry.",
    "db_corrupt": "The photo library index is damaged and could not be recovered.",
    "db_read_only": "The photo library cannot be updated because it is read-only.",
    "disk_full": "There is not enough free space to prepare the photo library.",
    "future_schema": "This photo library was opened by a newer app version.",
    "migration_recovery_failed": "The interrupted index migration could not be recovered.",
    "process_failed_to_start": "The library helper could not be started.",
    "process_crashed": "The library helper stopped unexpectedly.",
    "timeout": "The photo library did not respond in time.",
    "output_too_large": "The library helper returned too much data.",
    "invalid_protocol": "The library helper returned an incompatible response.",
    "root_mismatch": "The library helper returned a result for another library.",
    "library_unavailable": "The saved photo library is unavailable.",
    "probe_failed": "The photo library could not be prepared.",
}


@dataclass(frozen=True, slots=True)
class LibraryProbeRequest:
    request_id: str
    path: str
    timeout_ms: int = 3000
    protocol_version: int = PROBE_PROTOCOL_VERSION

    @classmethod
    def create(cls, path: Path, *, timeout_ms: int = 3000) -> LibraryProbeRequest:
        return cls(
            uuid.uuid4().hex,
            os.fspath(path),
            max(250, int(timeout_ms)),
            PROBE_PROTOCOL_VERSION,
        )


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
    code: str = "probe_failed"
    recoverable: bool = True
    suggested_action: str = "retry"
    timed_out: bool = False

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_payload(
        cls, request_id: str, payload: dict[str, Any]
    ) -> LibraryProbeFailure:
        code = str(payload.get("code") or "probe_failed")
        if code not in _FAILURE_MESSAGES:
            raise ValueError("unknown probe failure code")
        suggested_action = str(payload.get("suggested_action") or "retry")
        if suggested_action not in {"retry", "continue_without_library"}:
            raise ValueError("unknown probe recovery action")
        exception_type = str(payload.get("exception_type") or "RuntimeError")
        if (
            len(exception_type) > 80
            or not exception_type.replace("_", "").replace(".", "").isalnum()
        ):
            exception_type = "RuntimeError"
        return cls(
            request_id=request_id,
            message=_FAILURE_MESSAGES.get(code, _FAILURE_MESSAGES["probe_failed"]),
            exception_type=exception_type,
            code=code,
            recoverable=bool(payload.get("recoverable", True)),
            suggested_action=suggested_action,
            timed_out=bool(payload.get("timed_out", False)),
        )


def _failure(
    request_id: str,
    code: str,
    *,
    exception_type: str = "RuntimeError",
    recoverable: bool = True,
    suggested_action: str = "retry",
    timed_out: bool = False,
) -> LibraryProbeFailure:
    return LibraryProbeFailure(
        request_id=request_id,
        message=_FAILURE_MESSAGES.get(code, _FAILURE_MESSAGES["probe_failed"]),
        exception_type=exception_type,
        code=code,
        recoverable=recoverable,
        suggested_action=suggested_action,
        timed_out=timed_out,
    )


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
        if len(result) >= MAX_ALBUMS:
            raise RuntimeError("album_snapshot_limit")
        title, has_manifest = _album_description(top_level)
        result.append(PreparedAlbum(str(top_level), 1, title, has_manifest))
        for child in _album_dirs(top_level):
            if len(result) >= MAX_ALBUMS:
                raise RuntimeError("album_snapshot_limit")
            title, has_manifest = _album_description(child)
            result.append(PreparedAlbum(str(child), 2, title, has_manifest))
    return tuple(result)


def _database_snapshot(root: Path) -> tuple[Path, int, bool, tuple[str, ...]]:
    database_path = root / WORK_DIR_NAME / "global_index.db"
    # Schema creation/migration is intentionally performed in this killable
    # helper, never on the GUI thread.  Versioned migrations make the common
    # already-current path a constant-time PRAGMA check.
    from ..cache.index_store.migrations import SchemaMigrator

    schema_version, warnings = SchemaMigrator.prepare_database(database_path)
    with closing(sqlite3.connect(database_path, timeout=0.5)) as connection:
        try:
            row = connection.execute(
                "SELECT 1 FROM scan_jobs WHERE status = 'completed' "
                "AND scope = 'library' AND root = ? LIMIT 1",
                (root.as_posix(),),
            ).fetchone()
        except sqlite3.OperationalError:
            row = None
    return database_path, schema_version, row is not None, warnings


def _storage_kind(path: Path, elapsed_ms: float) -> str:
    raw = os.fspath(path)
    if os.name == "nt" and raw.startswith(("\\\\", "//")):
        return "network"
    if elapsed_ms >= 500:
        return "slow"
    return "local"


def probe_library(request: LibraryProbeRequest) -> PreparedLibrary:
    """Synchronously inspect one library; callers should isolate this in a process."""

    if request.protocol_version != PROBE_PROTOCOL_VERSION:
        raise ValueError("unsupported probe protocol")
    started = time.perf_counter_ns()
    root = Path(request.path).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise NotADirectoryError(f"Library path is not a directory: {request.path}")
    albums = _snapshot_albums(root)
    database_path, schema_version, scan_complete, warnings = _database_snapshot(root)
    elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000.0
    return PreparedLibrary(
        request_id=request.request_id,
        root=root,
        database_path=database_path,
        schema_version=schema_version,
        albums=albums,
        storage_kind=_storage_kind(root, elapsed_ms),
        scan_complete=scan_complete,
        warnings=warnings,
    )


class LibraryProbeController(QObject):
    """Run library probes in a killable child process with stale-result rejection."""

    ready = Signal(object)
    failed = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._process: QProcess | None = None
        self._request: LibraryProbeRequest | None = None
        self._stdout = bytearray()
        self._stderr = bytearray()
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(self._on_timeout)

    def start(self, request: LibraryProbeRequest) -> None:
        self.cancel()
        self._request = request
        process = QProcess(self)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        process.readyReadStandardOutput.connect(self._read_stdout_from_sender)
        process.readyReadStandardError.connect(self._read_stderr_from_sender)
        process.errorOccurred.connect(self._on_process_error)
        process.finished.connect(self._on_process_finished)
        self._process = process
        self._stdout.clear()
        self._stderr.clear()
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
        self._stop_process(process)

    @staticmethod
    def _stop_process(process: QProcess) -> None:
        if process.state() == QProcess.ProcessState.NotRunning:
            process.deleteLater()
            return
        process.terminate()

        def _kill_if_running() -> None:
            try:
                if process.state() != QProcess.ProcessState.NotRunning:
                    process.kill()
            except RuntimeError:
                # deleteLater may have run after terminate() completed.
                return

        QTimer.singleShot(250, _kill_if_running)

    def _emit_failure(self, failure: LibraryProbeFailure) -> None:
        process = self._process
        self._timeout.stop()
        self._process = None
        self._request = None
        if process is not None:
            self._stop_process(process)
        self.failed.emit(failure)

    def _read_stdout(self, process: QProcess) -> None:
        if process is not self._process:
            process.readAllStandardOutput()
            return
        self._stdout.extend(bytes(process.readAllStandardOutput()))
        if len(self._stdout) > MAX_STDOUT_BYTES and self._request is not None:
            self._emit_failure(_failure(self._request.request_id, "output_too_large"))

    def _read_stdout_from_sender(self) -> None:
        process = self.sender()
        if isinstance(process, QProcess):
            self._read_stdout(process)

    def _read_stderr(self, process: QProcess) -> None:
        chunk = bytes(process.readAllStandardError())
        if process is not self._process:
            return
        remaining = MAX_STDERR_BYTES - len(self._stderr)
        if remaining > 0:
            self._stderr.extend(chunk[:remaining])

    def _read_stderr_from_sender(self) -> None:
        process = self.sender()
        if isinstance(process, QProcess):
            self._read_stderr(process)

    def _on_process_error(self, error: QProcess.ProcessError) -> None:
        process = self.sender()
        if isinstance(process, QProcess):
            self._on_error(process, error)

    def _on_error(self, process: QProcess, error: QProcess.ProcessError) -> None:
        if process is not self._process or self._request is None:
            return
        if error == QProcess.ProcessError.FailedToStart:
            self._emit_failure(
                _failure(self._request.request_id, "process_failed_to_start")
            )
        elif error == QProcess.ProcessError.Crashed:
            self._emit_failure(_failure(self._request.request_id, "process_crashed"))

    def _on_timeout(self) -> None:
        request = self._request
        if request is None:
            return
        failure = _failure(
            request.request_id,
            "timeout",
            exception_type="TimeoutError",
            timed_out=True,
        )
        self._emit_failure(failure)

    def _on_finished(
        self,
        process: QProcess,
        exit_code: int,
        status: QProcess.ExitStatus,
    ) -> None:
        request = self._request
        if process is not self._process or request is None:
            process.deleteLater()
            return
        self._timeout.stop()
        self._read_stdout(process)
        self._read_stderr(process)
        if process is not self._process:
            return
        stdout = bytes(self._stdout).decode("utf-8", errors="replace")
        self._process = None
        self._request = None
        process.deleteLater()
        if status == QProcess.ExitStatus.CrashExit:
            self.failed.emit(_failure(request.request_id, "process_crashed"))
            return
        try:
            envelope = json.loads(stdout)
            if int(envelope.get("protocol_version", -1)) != PROBE_PROTOCOL_VERSION:
                raise ValueError("protocol version mismatch")
            if envelope.get("request_path") != request.path:
                self.failed.emit(_failure(request.request_id, "root_mismatch"))
                return
            if not envelope.get("ok"):
                payload = envelope.get("failure")
                if not isinstance(payload, dict):
                    raise ValueError("missing structured failure")
                self.failed.emit(LibraryProbeFailure.from_payload(request.request_id, payload))
                return
            if exit_code != 0:
                raise ValueError("successful response with non-zero exit")
            prepared = PreparedLibrary.from_payload(envelope["prepared"])
            if prepared.request_id != request.request_id:
                return
            root = prepared.root
            expected_database = root / WORK_DIR_NAME / "global_index.db"
            requested_root = Path(request.path).expanduser().absolute()
            root_matches = os.path.normcase(os.fspath(requested_root)) == os.path.normcase(
                os.fspath(root)
            )
            if not root_matches:
                try:
                    root_matches = os.path.samefile(requested_root, root)
                except OSError:
                    root_matches = False
            album_paths_valid = all(
                (album_path := Path(album.path)).is_absolute()
                and Path(os.path.normpath(album_path)) == album_path
                and album_path.is_relative_to(root)
                for album in prepared.albums
            )
            if (
                not root.is_absolute()
                or Path(os.path.normpath(root)) != root
                or not root_matches
                or prepared.database_path != expected_database
                or not album_paths_valid
            ):
                self.failed.emit(_failure(request.request_id, "root_mismatch"))
                return
        except Exception:  # noqa: BLE001 - child-process protocol boundary
            self.failed.emit(_failure(request.request_id, "invalid_protocol"))
            return
        self.ready.emit(prepared)

    def _on_process_finished(
        self, exit_code: int, status: QProcess.ExitStatus
    ) -> None:
        process = self.sender()
        if isinstance(process, QProcess):
            self._on_finished(process, exit_code, status)


def _exception_failure(request_id: str, exc: Exception) -> LibraryProbeFailure:
    from ..cache.index_store.migrations import SchemaPreparationError

    if isinstance(exc, SchemaPreparationError):
        code = exc.code
    elif isinstance(exc, (FileNotFoundError, NotADirectoryError, PermissionError)):
        code = "library_unavailable"
    elif isinstance(exc, sqlite3.DatabaseError):
        code = "db_corrupt"
    elif str(exc) == "album_snapshot_limit":
        code = "output_too_large"
    elif isinstance(exc, (json.JSONDecodeError, TypeError, ValueError, KeyError)):
        code = "invalid_protocol"
    else:
        code = "probe_failed"
    recoverable = code != "future_schema"
    action = "continue_without_library" if not recoverable else "retry"
    return _failure(
        request_id,
        code,
        exception_type=type(exc).__name__,
        recoverable=recoverable,
        suggested_action=action,
    )


def _main(arguments: list[str]) -> int:
    request_id = "unknown"
    request_path = ""
    try:
        payload = json.loads(arguments[0])
        request = LibraryProbeRequest(**payload)
        request_id = request.request_id
        request_path = request.path
        prepared = probe_library(request)
        envelope = {
            "protocol_version": PROBE_PROTOCOL_VERSION,
            "request_path": request.path,
            "ok": True,
            "prepared": prepared.to_payload(),
        }
    except Exception as exc:  # noqa: BLE001 - helper process boundary
        failure = _exception_failure(request_id, exc)
        envelope = {
            "protocol_version": PROBE_PROTOCOL_VERSION,
            "request_path": request_path,
            "ok": False,
            "failure": failure.to_payload(),
        }
    sys.stdout.write(json.dumps(envelope, ensure_ascii=False))
    sys.stdout.flush()
    return 0 if envelope["ok"] else 2


if __name__ == "__main__":  # pragma: no cover - exercised through QProcess
    raise SystemExit(_main(sys.argv[1:]))


__all__ = [
    "MAX_ALBUMS",
    "MAX_STDERR_BYTES",
    "MAX_STDOUT_BYTES",
    "PROBE_PROTOCOL_VERSION",
    "LibraryProbeController",
    "LibraryProbeFailure",
    "LibraryProbeRequest",
    "PreparedAlbum",
    "PreparedLibrary",
    "probe_library",
]

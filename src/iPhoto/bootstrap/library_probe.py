"""Out-of-process probing for potentially slow or unavailable libraries."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

_HELPER_PROCESS = any(
    argument in {"--startup-library-probe", "--stdin"} for argument in sys.argv[1:]
)
if _HELPER_PROCESS:
    # The helper never constructs the Qt controller.  Lightweight placeholders
    # let the protocol/worker module load without importing the GUI framework.
    class QObject:  # pragma: no cover - helper-only compatibility base
        pass

    def Signal(*_args):  # pragma: no cover - helper-only class declaration
        return None

    QProcess = Any  # type: ignore[misc,assignment]
    QTimer = Any  # type: ignore[misc,assignment]
    QCoreApplication = Any  # type: ignore[misc,assignment]
else:
    from PySide6.QtCore import QCoreApplication, QObject, QProcess, QTimer, Signal

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
PROBE_PROTOCOL_VERSION = 2
MAX_STDOUT_BYTES = 4 * 1024 * 1024
MAX_STDERR_BYTES = 16 * 1024
MAX_ALBUMS = 10_000
MAX_REQUEST_BYTES = 256 * 1024
ALBUM_SNAPSHOT_BUDGET_MS = 750.0
_LOGGER = logging.getLogger(__name__)

_FAILURE_MESSAGES = {
    "db_locked": (
        "The photo library database is in use by another process. "
        "Close other iPhotron versions and retry."
    ),
    "migration_file_busy": (
        "Windows or security software is temporarily using a migration file. "
        "Close other programs or retry shortly."
    ),
    "db_corrupt": "The photo library database is damaged. The original file has been preserved.",
    "db_read_only": (
        "The photo library database is read-only. "
        "Remove its read-only attribute or grant modify access."
    ),
    "workspace_unwritable": (
        "The .iPhoto work folder cannot be updated. Check that folder's permissions."
    ),
    "disk_full": "There is not enough free disk space to prepare the photo library.",
    "db_open_failed": (
        "The photo library database could not be opened. "
        "Check the path, drive, and security software."
    ),
    "migration_backup_failed": (
        "A safety backup could not be created. Migration did not start and "
        "the original database was preserved."
    ),
    "migration_failed": (
        "The photo library database update failed. "
        "The original database and backup were preserved."
    ),
    "future_schema": (
        "This photo library was created by a newer app version. "
        "Open it with the same or a newer version."
    ),
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
class StorageProfile:
    """Cross-platform storage classification produced inside the helper."""

    kind: str = "unknown"
    latency_class: str = "normal"
    basis: str = "fallback"
    removable: bool = False


@dataclass(frozen=True, slots=True)
class FileIdentity:
    """Identity used to reject a replaced prepared database."""

    device: int
    inode: int
    size: int
    modified_ns: int

    @classmethod
    def capture(cls, path: Path) -> "FileIdentity":
        stat = path.stat()
        return cls(
            device=int(stat.st_dev),
            inode=int(stat.st_ino),
            size=int(stat.st_size),
            modified_ns=int(stat.st_mtime_ns),
        )

    def matches(self, path: Path) -> bool:
        try:
            current = self.capture(path)
        except OSError:
            return False
        return current == self


def _database_schema_version_matches(path: Path, expected_version: int) -> bool:
    """Revalidate schema state, including changes that still live in SQLite WAL."""

    try:
        uri = f"{Path(path).resolve().as_uri()}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=0.1)) as connection:
            row = connection.execute("PRAGMA user_version").fetchone()
    except (OSError, sqlite3.Error, ValueError):
        return False
    return bool(row and int(row[0]) == int(expected_version))


@dataclass(frozen=True, slots=True)
class PreparedLibraryCredential:
    root: Path
    database_path: Path
    schema_version: int
    database_identity: FileIdentity
    prepared_at_ns: int

    def matches_current(self) -> bool:
        return self.database_identity.matches(
            self.database_path
        ) and _database_schema_version_matches(
            self.database_path,
            self.schema_version,
        )


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
    storage_profile: StorageProfile = StorageProfile()
    credential: PreparedLibraryCredential | None = None

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["root"] = os.fspath(self.root)
        payload["database_path"] = os.fspath(self.database_path)
        payload["albums"] = [asdict(album) for album in self.albums]
        payload["warnings"] = list(self.warnings)
        payload["storage_profile"] = asdict(self.storage_profile)
        if self.credential is not None:
            payload["credential"] = asdict(self.credential)
            payload["credential"]["root"] = os.fspath(self.credential.root)
            payload["credential"]["database_path"] = os.fspath(
                self.credential.database_path
            )
        return payload

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> PreparedLibrary:
        credential_payload = payload.get("credential")
        credential = None
        if isinstance(credential_payload, dict):
            identity_payload = credential_payload.get("database_identity")
            if not isinstance(identity_payload, dict):
                raise ValueError("missing database identity")
            credential = PreparedLibraryCredential(
                root=Path(credential_payload["root"]),
                database_path=Path(credential_payload["database_path"]),
                schema_version=int(credential_payload["schema_version"]),
                database_identity=FileIdentity(**identity_payload),
                prepared_at_ns=int(credential_payload["prepared_at_ns"]),
            )
        profile_payload = payload.get("storage_profile")
        storage_profile = (
            StorageProfile(**profile_payload)
            if isinstance(profile_payload, dict)
            else StorageProfile(kind=str(payload.get("storage_kind") or "unknown"))
        )
        return cls(
            request_id=str(payload["request_id"]),
            root=Path(payload["root"]),
            database_path=Path(payload["database_path"]),
            schema_version=int(payload.get("schema_version", 0)),
            albums=tuple(PreparedAlbum(**item) for item in payload.get("albums", ())),
            storage_kind=str(payload.get("storage_kind") or "local"),
            scan_complete=bool(payload.get("scan_complete", False)),
            warnings=tuple(str(item) for item in payload.get("warnings", ())),
            storage_profile=storage_profile,
            credential=credential,
        )


def _credential_matches(
    credential: PreparedLibraryCredential,
    prepared: PreparedLibrary,
) -> bool:
    return (
        credential.root == prepared.root
        and credential.database_path == prepared.database_path
        and credential.schema_version == prepared.schema_version
        and credential.matches_current()
    )


@dataclass(slots=True)
class ValidatedPreparedLibrary:
    """Freshly revalidated, single-use library commit capability."""

    prepared: PreparedLibrary
    validation_id: str
    validated_at_ns: int
    _consumed: bool = False

    @classmethod
    def create(cls, prepared: PreparedLibrary) -> "ValidatedPreparedLibrary":
        credential = prepared.credential
        if credential is None or not _credential_matches(credential, prepared):
            raise ValueError("prepared library credential is stale")
        return cls(prepared, uuid.uuid4().hex, time.time_ns())

    def consume(self) -> PreparedLibrary:
        if self._consumed:
            raise RuntimeError("validated prepared library was already consumed")
        credential = self.prepared.credential
        if credential is None or not _credential_matches(credential, self.prepared):
            raise RuntimeError("prepared library changed before commit")
        self._consumed = True
        return self.prepared

    def __getattr__(self, name: str) -> Any:
        return getattr(self.prepared, name)


@dataclass(frozen=True, slots=True)
class LibraryProbeFailure:
    request_id: str
    message: str
    exception_type: str
    code: str = "probe_failed"
    recoverable: bool = True
    suggested_action: str = "retry"
    timed_out: bool = False
    operation: str | None = None
    native_code: str | None = None

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

        def diagnostic_token(name: str) -> str | None:
            value = payload.get(name)
            if value is None:
                return None
            token = str(value)
            if (
                len(token) > 80
                or not token.replace("_", "").replace(".", "").isalnum()
            ):
                return None
            return token

        return cls(
            request_id=request_id,
            message=_FAILURE_MESSAGES.get(code, _FAILURE_MESSAGES["probe_failed"]),
            exception_type=exception_type,
            code=code,
            recoverable=bool(payload.get("recoverable", True)),
            suggested_action=suggested_action,
            timed_out=bool(payload.get("timed_out", False)),
            operation=diagnostic_token("operation"),
            native_code=diagnostic_token("native_code"),
        )


def _failure(
    request_id: str,
    code: str,
    *,
    exception_type: str = "RuntimeError",
    recoverable: bool = True,
    suggested_action: str = "retry",
    timed_out: bool = False,
    operation: str | None = None,
    native_code: str | None = None,
) -> LibraryProbeFailure:
    return LibraryProbeFailure(
        request_id=request_id,
        message=_FAILURE_MESSAGES.get(code, _FAILURE_MESSAGES["probe_failed"]),
        exception_type=exception_type,
        code=code,
        recoverable=recoverable,
        suggested_action=suggested_action,
        timed_out=timed_out,
        operation=operation,
        native_code=native_code,
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


def _bounded_album_dirs(
    root: Path,
    *,
    deadline_ns: int,
    limit: int,
) -> tuple[list[Path], str | None]:
    """Enumerate album directories without overrunning the snapshot budget."""

    result: list[Path] = []
    iterator = root.iterdir()
    while True:
        if time.perf_counter_ns() >= deadline_ns:
            return sorted(result, key=lambda item: item.name.casefold()), "time"
        try:
            entry = next(iterator)
        except StopIteration:
            return sorted(result, key=lambda item: item.name.casefold()), None
        if time.perf_counter_ns() >= deadline_ns:
            return sorted(result, key=lambda item: item.name.casefold()), "time"
        if not entry.is_dir() or entry.name.casefold() in _RESERVED_NAMES:
            continue
        if len(result) >= limit:
            return sorted(result, key=lambda item: item.name.casefold()), "count"
        result.append(entry)


def _snapshot_albums(root: Path) -> tuple[tuple[PreparedAlbum, ...], tuple[str, ...]]:
    result: list[PreparedAlbum] = []
    warnings: list[str] = []
    deadline_ns = time.perf_counter_ns() + int(ALBUM_SNAPSHOT_BUDGET_MS * 1_000_000)
    top_levels, truncated = _bounded_album_dirs(
        root,
        deadline_ns=deadline_ns,
        limit=MAX_ALBUMS,
    )
    for top_level in top_levels:
        if time.perf_counter_ns() >= deadline_ns:
            if "album_snapshot_truncated_time" not in warnings:
                warnings.append("album_snapshot_truncated_time")
            break
        title, has_manifest = _album_description(top_level)
        result.append(PreparedAlbum(str(top_level), 1, title, has_manifest))
        remaining = MAX_ALBUMS - len(result)
        if remaining <= 0:
            if "album_snapshot_truncated_count" not in warnings:
                warnings.append("album_snapshot_truncated_count")
            break
        children, child_truncated = _bounded_album_dirs(
            top_level,
            deadline_ns=deadline_ns,
            limit=remaining,
        )
        if child_truncated is not None:
            warning = f"album_snapshot_truncated_{child_truncated}"
            if warning not in warnings:
                warnings.append(warning)
        for child in children:
            if time.perf_counter_ns() >= deadline_ns:
                if "album_snapshot_truncated_time" not in warnings:
                    warnings.append("album_snapshot_truncated_time")
                break
            title, has_manifest = _album_description(child)
            result.append(PreparedAlbum(str(child), 2, title, has_manifest))
        if warnings:
            break
    if truncated is not None:
        warning = f"album_snapshot_truncated_{truncated}"
        if warning not in warnings:
            warnings.append(warning)
    return tuple(result), tuple(warnings)


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


def _unix_mount_profile(path: Path) -> StorageProfile | None:
    if sys.platform.startswith("linux"):
        try:
            lines = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        resolved = path.as_posix()
        matches: list[tuple[int, str, str]] = []
        for line in lines:
            parts = line.split()
            if "-" not in parts or len(parts) < 10:
                continue
            separator = parts.index("-")
            mount_point = parts[4].replace("\\040", " ")
            filesystem = parts[separator + 1]
            if resolved == mount_point or resolved.startswith(mount_point.rstrip("/") + "/"):
                matches.append((len(mount_point), mount_point, filesystem))
        if not matches:
            return None
        _length, mount_point, filesystem = max(matches)
        network_fs = {"nfs", "nfs4", "cifs", "smb3", "sshfs", "afpfs", "davfs"}
        if filesystem.casefold() in network_fs:
            return StorageProfile("network", "slow", f"mount:{filesystem}")
        removable = mount_point.startswith(("/media/", "/run/media/", "/mnt/"))
        return StorageProfile(
            "removable" if removable else "local",
            "normal",
            f"mount:{filesystem}",
            removable,
        )
    if sys.platform == "darwin":
        try:
            output = subprocess.run(
                ["/sbin/mount"],
                capture_output=True,
                text=True,
                timeout=0.25,
                check=False,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            output = ""
        resolved = path.as_posix()
        matches: list[tuple[int, str, str]] = []
        for line in output.splitlines():
            if " on " not in line or " (" not in line:
                continue
            _source, remainder = line.split(" on ", 1)
            mount_point, options = remainder.split(" (", 1)
            if resolved == mount_point or resolved.startswith(
                mount_point.rstrip("/") + "/"
            ):
                filesystem = options.split(",", 1)[0].rstrip(")").casefold()
                matches.append((len(mount_point), mount_point, filesystem))
        if matches:
            _length, mount_point, filesystem = max(matches)
            if filesystem in {"smbfs", "nfs", "afpfs", "webdav"}:
                return StorageProfile("network", "slow", f"mount:{filesystem}")
            removable = mount_point.startswith("/Volumes/")
            return StorageProfile(
                "removable" if removable else "local",
                "normal",
                f"mount:{filesystem}",
                removable,
            )
    return None


def _windows_storage_profile(path: Path) -> StorageProfile | None:
    if os.name != "nt":
        return None
    raw = os.fspath(path)
    if raw.startswith(("\\\\", "//")):
        return StorageProfile("network", "slow", "windows:unc")
    try:
        import ctypes

        root = path.anchor or raw[:3]
        drive_type = int(ctypes.windll.kernel32.GetDriveTypeW(root))
    except (AttributeError, OSError, ValueError):
        return None
    if drive_type == 4:
        return StorageProfile("network", "slow", "windows:drive_type")
    if drive_type == 2:
        return StorageProfile("removable", "normal", "windows:drive_type", True)
    if drive_type == 3:
        return StorageProfile("local", "normal", "windows:drive_type")
    return StorageProfile("unknown", "normal", "windows:drive_type")


def _storage_profile(path: Path, elapsed_ms: float) -> StorageProfile:
    windows_profile = _windows_storage_profile(path)
    if windows_profile is not None:
        return windows_profile
    mount_profile = _unix_mount_profile(path)
    if mount_profile is not None:
        return mount_profile
    if elapsed_ms >= 500:
        return StorageProfile("unknown", "slow", "elapsed")
    return StorageProfile("local", "normal", "elapsed")


def probe_library(request: LibraryProbeRequest) -> PreparedLibrary:
    """Synchronously inspect one library; callers should isolate this in a process."""

    if request.protocol_version != PROBE_PROTOCOL_VERSION:
        raise ValueError("unsupported probe protocol")
    started = time.perf_counter_ns()
    root = Path(request.path).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise NotADirectoryError(f"Library path is not a directory: {request.path}")
    database_path, schema_version, scan_complete, warnings = _database_snapshot(root)
    albums, snapshot_warnings = _snapshot_albums(root)
    elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000.0
    storage_profile = _storage_profile(root, elapsed_ms)
    credential = PreparedLibraryCredential(
        root=root,
        database_path=database_path,
        schema_version=schema_version,
        database_identity=FileIdentity.capture(database_path),
        prepared_at_ns=time.time_ns(),
    )
    return PreparedLibrary(
        request_id=request.request_id,
        root=root,
        database_path=database_path,
        schema_version=schema_version,
        albums=albums,
        storage_kind=storage_profile.kind,
        scan_complete=scan_complete,
        warnings=tuple((*warnings, *snapshot_warnings)),
        storage_profile=storage_profile,
        credential=credential,
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
        self._termination_callbacks: set[Callable[[], None]] = set()

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
        program, arguments = _probe_process_command()
        process.start(program, arguments)
        process.write(payload.encode("utf-8"))
        process.closeWriteChannel()
        self._timeout.start(request.timeout_ms)

    def cancel(self) -> None:
        self._timeout.stop()
        process = self._process
        self._process = None
        self._request = None
        if process is None:
            return
        self._stop_process(process)

    def _stop_process(self, process: QProcess) -> None:
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
                pass
            finally:
                self._termination_callbacks.discard(_kill_if_running)

        # Keep the Python callable strongly referenced until Qt invokes it.
        # Some PySide/macOS builds otherwise discard static singleShot callables.
        self._termination_callbacks.add(_kill_if_running)
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
        response_protocol: int | None = None
        try:
            envelope = json.loads(stdout)
            response_protocol = int(envelope.get("protocol_version", -1))
            if response_protocol != PROBE_PROTOCOL_VERSION:
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
                raise ValueError("request id mismatch")
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
        except Exception as exc:  # noqa: BLE001 - child-process protocol boundary
            _LOGGER.warning(
                "Library helper protocol rejected: expected=%s response=%s "
                "exit_code=%s stdout_bytes=%s stderr_bytes=%s exception=%s",
                PROBE_PROTOCOL_VERSION,
                response_protocol,
                exit_code,
                len(self._stdout),
                len(self._stderr),
                type(exc).__name__,
            )
            self.failed.emit(_failure(request.request_id, "invalid_protocol"))
            return
        try:
            validated = ValidatedPreparedLibrary.create(prepared)
        except ValueError:
            self.failed.emit(_failure(request.request_id, "root_mismatch"))
            return
        self.ready.emit(validated)

    def _on_process_finished(
        self, exit_code: int, status: QProcess.ExitStatus
    ) -> None:
        process = self.sender()
        if isinstance(process, QProcess):
            self._on_finished(process, exit_code, status)


def _source_probe_entrypoint() -> Path | None:
    """Return the checkout entrypoint that imports the same source tree."""

    candidate = Path(__file__).resolve().parents[2] / "entrypoint.py"
    try:
        return candidate if candidate.is_file() else None
    except OSError:
        return None


def _probe_process_command() -> tuple[str, list[str]]:
    """Return a helper command that survives renamed packaged executables.

    Nuitka does not guarantee that ``sys.executable`` names the executable in
    the final application bundle.  Qt already knows the path used to launch
    the current application, so packaged builds must use that value when they
    start their helper copy.
    """

    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        application_path = os.fspath(QCoreApplication.applicationFilePath())
        return application_path or sys.executable, [
            "--startup-library-probe",
            "--stdin",
        ]
    source_entrypoint = _source_probe_entrypoint()
    if source_entrypoint is not None:
        return sys.executable, [
            os.fspath(source_entrypoint),
            "--startup-library-probe",
            "--stdin",
        ]
    return sys.executable, ["-m", "iPhoto.bootstrap.library_probe", "--stdin"]


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
        operation=getattr(exc, "operation", None),
        native_code=getattr(exc, "native_code", None),
    )


def _main(arguments: list[str]) -> int:
    request_id = "unknown"
    request_path = ""
    try:
        if arguments and arguments[0] == "--stdin":
            raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
            if len(raw) > MAX_REQUEST_BYTES:
                raise ValueError("probe request too large")
            payload = json.loads(raw.decode("utf-8"))
        else:
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
    "ALBUM_SNAPSHOT_BUDGET_MS",
    "FileIdentity",
    "MAX_ALBUMS",
    "MAX_REQUEST_BYTES",
    "MAX_STDERR_BYTES",
    "MAX_STDOUT_BYTES",
    "PROBE_PROTOCOL_VERSION",
    "LibraryProbeController",
    "LibraryProbeFailure",
    "LibraryProbeRequest",
    "PreparedAlbum",
    "PreparedLibraryCredential",
    "PreparedLibrary",
    "StorageProfile",
    "ValidatedPreparedLibrary",
    "probe_library",
]

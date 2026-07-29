from __future__ import annotations

import errno
import json
import os
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from dataclasses import asdict
from pathlib import Path

import pytest
from PySide6.QtCore import QProcess
from PySide6.QtTest import QTest

import iPhoto.cache.index_store.migrations as migrations_module
from iPhoto.bootstrap.library_probe import (
    MAX_STDERR_BYTES,
    MAX_STDOUT_BYTES,
    PROBE_PROTOCOL_VERSION,
    LibraryProbeController,
    LibraryProbeRequest,
    PreparedLibrary,
    _main,
    _storage_kind,
    probe_library,
)
from iPhoto.cache.index_store.migrations import (
    CURRENT_SCHEMA_VERSION,
    MIGRATION_PROTOCOL_VERSION,
    MIGRATION_STATE_NAME,
    MigrationState,
    SchemaMigrator,
    SchemaPreparationError,
)


def test_probe_returns_two_level_album_snapshot_without_creating_work_dir(
    tmp_path: Path,
) -> None:
    library = tmp_path / "library"
    child = library / "Trips" / "Berlin"
    child.mkdir(parents=True)
    (library / ".iPhoto").mkdir()
    (library / "Trips" / ".iphoto.album.json").write_text(
        json.dumps({"title": "Travel"}),
        encoding="utf-8",
    )

    prepared = probe_library(LibraryProbeRequest.create(library))

    assert prepared.root == library.resolve()
    assert [(Path(item.path).name, item.level, item.title) for item in prepared.albums] == [
        ("Trips", 1, "Travel"),
        ("Berlin", 2, "Berlin"),
    ]
    assert prepared.storage_kind in {"local", "slow"}


def test_storage_kind_uses_a_deterministic_latency_threshold(tmp_path: Path) -> None:
    assert _storage_kind(tmp_path, 499.999) == "local"
    assert _storage_kind(tmp_path, 500.0) == "slow"


def test_probe_reads_schema_and_completed_scan_without_writing(tmp_path: Path) -> None:
    library = tmp_path / "library"
    work_dir = library / ".iPhoto"
    work_dir.mkdir(parents=True)
    database = work_dir / "global_index.db"
    with closing(sqlite3.connect(database)) as connection:
        with connection:
            connection.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")
            connection.execute(
                "CREATE TABLE scan_jobs (status TEXT NOT NULL, scope TEXT NOT NULL, root TEXT)"
            )
            connection.execute(
                "INSERT INTO scan_jobs(status, scope, root) VALUES ('completed', 'library', ?)",
                (library.as_posix(),),
            )

    prepared = probe_library(LibraryProbeRequest.create(library))
    round_trip = PreparedLibrary.from_payload(prepared.to_payload())

    assert round_trip.schema_version == CURRENT_SCHEMA_VERSION
    assert round_trip.scan_complete is True
    assert round_trip.database_path == database


def test_probe_missing_library_is_recoverable_error(tmp_path: Path) -> None:
    missing = tmp_path / "missing"

    try:
        probe_library(LibraryProbeRequest.create(missing))
    except FileNotFoundError:
        pass
    else:  # pragma: no cover - assertion branch
        raise AssertionError("missing library unexpectedly probed successfully")


def test_probe_broken_symlink_is_recoverable_error(tmp_path: Path) -> None:
    link = tmp_path / "disconnected-library"
    try:
        link.symlink_to(tmp_path / "missing-target", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are unavailable")

    with pytest.raises(FileNotFoundError):
        probe_library(LibraryProbeRequest.create(link))


def _create_legacy_database(database: Path) -> None:
    database.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database)) as connection:
        with connection:
            connection.execute(
                "CREATE TABLE assets ("
                "rel TEXT PRIMARY KEY, parent_album_path TEXT, mime TEXT, "
                "media_type INTEGER, live_role INTEGER, gps TEXT)"
            )
            connection.execute(
                "INSERT INTO assets(rel, parent_album_path, mime) "
                "VALUES ('album/photo.jpg', 'album', 'image/jpeg')"
            )
            connection.execute("PRAGMA user_version = 0")


def test_prepare_database_migrates_legacy_database_transactionally(tmp_path: Path) -> None:
    database = tmp_path / ".iPhoto" / "global_index.db"
    _create_legacy_database(database)

    version, warnings = SchemaMigrator.prepare_database(database)

    assert version == CURRENT_SCHEMA_VERSION
    assert warnings == ()
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT rel FROM assets").fetchone() == (
            "album/photo.jpg",
        )
    assert not (database.parent / MIGRATION_STATE_NAME).exists()
    assert list(database.parent.glob("*.migration-*.bak")) == []


def test_prepare_database_rolls_back_failed_version_and_keeps_recovery_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / ".iPhoto" / "global_index.db"
    _create_legacy_database(database)

    def fail_migration(connection: sqlite3.Connection) -> None:
        connection.execute("ALTER TABLE assets ADD COLUMN should_rollback TEXT")
        raise RuntimeError("injected migration failure")

    monkeypatch.setattr(SchemaMigrator, "_migrate_to_v1", staticmethod(fail_migration))

    with pytest.raises(RuntimeError, match="injected migration failure"):
        SchemaMigrator.prepare_database(database)

    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        columns = {row[1] for row in connection.execute("PRAGMA table_info(assets)")}
    assert "should_rollback" not in columns
    assert (database.parent / MIGRATION_STATE_NAME).exists()
    assert len(list(database.parent.glob("*.migration-*.bak"))) == 1


def test_prepare_database_restores_valid_backup_after_interrupted_corruption(
    tmp_path: Path,
) -> None:
    database = tmp_path / ".iPhoto" / "global_index.db"
    _create_legacy_database(database)
    migration_id = "test-recovery"
    backup = database.parent / f"global_index.db.migration-{migration_id}.bak"
    with closing(sqlite3.connect(database)) as source, closing(
        sqlite3.connect(backup)
    ) as target:
        source.backup(target)
    state = MigrationState(
        protocol_version=MIGRATION_PROTOCOL_VERSION,
        migration_id=migration_id,
        database_name=database.name,
        backup_name=backup.name,
        source_version=0,
        target_version=CURRENT_SCHEMA_VERSION,
        stage="migration_pending",
        started_at_ms=1,
    )
    (database.parent / MIGRATION_STATE_NAME).write_text(
        json.dumps(asdict(state)), encoding="utf-8"
    )
    database.write_bytes(b"not a database")

    version, warnings = SchemaMigrator.prepare_database(database)

    assert version == CURRENT_SCHEMA_VERSION
    assert warnings == ("migration_restored",)
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT rel FROM assets").fetchone() == (
            "album/photo.jpg",
        )


def test_prepare_database_keeps_evidence_when_database_and_backup_are_corrupt(
    tmp_path: Path,
) -> None:
    database = tmp_path / ".iPhoto" / "global_index.db"
    database.parent.mkdir(parents=True)
    database.write_bytes(b"broken-main")
    migration_id = "test-broken"
    backup = database.parent / f"global_index.db.migration-{migration_id}.bak"
    backup.write_bytes(b"broken-backup")
    state = MigrationState(
        protocol_version=MIGRATION_PROTOCOL_VERSION,
        migration_id=migration_id,
        database_name=database.name,
        backup_name=backup.name,
        source_version=0,
        target_version=CURRENT_SCHEMA_VERSION,
        stage="migration_pending",
        started_at_ms=1,
    )
    state_path = database.parent / MIGRATION_STATE_NAME
    state_path.write_text(json.dumps(asdict(state)), encoding="utf-8")

    with pytest.raises(SchemaPreparationError) as caught:
        SchemaMigrator.prepare_database(database)

    assert caught.value.code == "migration_recovery_failed"
    assert database.read_bytes() == b"broken-main"
    assert backup.read_bytes() == b"broken-backup"
    assert state_path.exists()


def test_prepare_database_rejects_future_schema(tmp_path: Path) -> None:
    database = tmp_path / ".iPhoto" / "global_index.db"
    database.parent.mkdir(parents=True)
    with closing(sqlite3.connect(database)) as connection:
        with connection:
            connection.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION + 1}")

    with pytest.raises(SchemaPreparationError) as caught:
        SchemaMigrator.prepare_database(database)

    assert caught.value.code == "future_schema"


def test_prepare_database_current_version_skips_full_integrity_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / ".iPhoto" / "global_index.db"
    SchemaMigrator.prepare_database(database)

    monkeypatch.setattr(
        migrations_module,
        "_integrity_ok",
        lambda _path: (_ for _ in ()).throw(AssertionError("unexpected integrity scan")),
    )

    version, warnings = SchemaMigrator.prepare_database(database)

    assert version == CURRENT_SCHEMA_VERSION
    assert warnings == ()


def test_prepare_database_classifies_locked_database(tmp_path: Path) -> None:
    database = tmp_path / ".iPhoto" / "global_index.db"
    _create_legacy_database(database)
    blocker = sqlite3.connect(database)
    blocker.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(SchemaPreparationError) as caught:
            SchemaMigrator.prepare_database(database)
    finally:
        blocker.rollback()
        blocker.close()

    assert caught.value.code == "db_locked"


def test_prepare_database_classifies_sqlite_cantopen_as_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / ".iPhoto" / "global_index.db"

    def fail_to_open(*_args, **_kwargs):
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(migrations_module.sqlite3, "connect", fail_to_open)

    with pytest.raises(SchemaPreparationError) as caught:
        SchemaMigrator.prepare_database(database)

    assert caught.value.code == "db_read_only"


@pytest.mark.parametrize(
    ("error_number", "expected_code"),
    [(errno.EROFS, "db_read_only"), (errno.ENOSPC, "disk_full")],
)
def test_prepare_database_classifies_state_write_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_number: int,
    expected_code: str,
) -> None:
    database = tmp_path / ".iPhoto" / "global_index.db"
    _create_legacy_database(database)
    real_open = Path.open

    def fail_state_write(path: Path, *args, **kwargs):
        if path.name == f"{MIGRATION_STATE_NAME}.tmp":
            raise OSError(error_number, os.strerror(error_number))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_state_write)

    with pytest.raises(SchemaPreparationError) as caught:
        SchemaMigrator.prepare_database(database)

    assert caught.value.code == expected_code


def test_real_child_interruption_is_recovered_on_next_prepare(tmp_path: Path) -> None:
    database = tmp_path / ".iPhoto" / "global_index.db"
    _create_legacy_database(database)
    script = "\n".join(
        [
            "import os, sys",
            "from pathlib import Path",
            "from iPhoto.cache.index_store.migrations import SchemaMigrator",
            "def interrupt(connection):",
            "    connection.execute('ALTER TABLE assets ADD COLUMN interrupted TEXT')",
            "    os._exit(91)",
            "SchemaMigrator._migrate_to_v1 = staticmethod(interrupt)",
            "SchemaMigrator.prepare_database(Path(sys.argv[1]))",
        ]
    )

    child = subprocess.run([sys.executable, "-c", script, str(database)], check=False)

    assert child.returncode == 91
    assert (database.parent / MIGRATION_STATE_NAME).exists()
    version, warnings = SchemaMigrator.prepare_database(database)
    assert version == CURRENT_SCHEMA_VERSION
    assert warnings == ()
    with closing(sqlite3.connect(database)) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(assets)")}
        assert connection.execute("SELECT rel FROM assets").fetchone() == (
            "album/photo.jpg",
        )
    assert "interrupted" not in columns
    assert not (database.parent / MIGRATION_STATE_NAME).exists()


def test_helper_envelope_uses_versioned_structured_protocol(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    request = LibraryProbeRequest.create(library)

    exit_code = _main([json.dumps(asdict(request))])
    envelope = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert envelope["protocol_version"] == PROBE_PROTOCOL_VERSION
    assert envelope["request_path"] == request.path
    assert envelope["ok"] is True


def test_helper_failure_does_not_expose_library_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "private-name" / "missing"
    request = LibraryProbeRequest.create(missing)

    exit_code = _main([json.dumps(asdict(request))])
    raw_output = capsys.readouterr().out
    envelope = json.loads(raw_output)

    assert exit_code == 2
    assert envelope["failure"]["code"] == "library_unavailable"
    assert str(missing) not in json.dumps(envelope["failure"])


class _FakeProcess:
    def __init__(
        self, stdout: bytes = b"", *, stderr: bytes = b"", running: bool = False
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.running = running
        self.terminated = False
        self.killed = False
        self.deleted = False

    def readAllStandardOutput(self) -> bytes:
        output, self.stdout = self.stdout, b""
        return output

    def readAllStandardError(self) -> bytes:
        output, self.stderr = self.stderr, b""
        return output

    def state(self):
        if self.running:
            return QProcess.ProcessState.Running
        return QProcess.ProcessState.NotRunning

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True
        self.running = False

    def deleteLater(self) -> None:
        self.deleted = True


def test_controller_rejects_incompatible_protocol(qapp, tmp_path: Path) -> None:
    request = LibraryProbeRequest.create(tmp_path)
    process = _FakeProcess(
        json.dumps(
            {
                "protocol_version": PROBE_PROTOCOL_VERSION + 1,
                "request_path": request.path,
                "ok": True,
                "prepared": {},
            }
        ).encode()
    )
    controller = LibraryProbeController()
    failures = []
    controller.failed.connect(failures.append)
    controller._request = request
    controller._process = process  # type: ignore[assignment]

    controller._on_finished(process, 0, QProcess.ExitStatus.NormalExit)  # type: ignore[arg-type]

    assert failures[0].code == "invalid_protocol"


def test_controller_rejects_invalid_json(qapp, tmp_path: Path) -> None:
    request = LibraryProbeRequest.create(tmp_path)
    process = _FakeProcess(b"{invalid")
    controller = LibraryProbeController()
    failures = []
    controller.failed.connect(failures.append)
    controller._request = request
    controller._process = process  # type: ignore[assignment]

    controller._on_finished(process, 2, QProcess.ExitStatus.NormalExit)  # type: ignore[arg-type]

    assert failures[0].code == "invalid_protocol"


def test_controller_rejects_result_for_another_root(qapp, tmp_path: Path) -> None:
    requested = tmp_path / "requested"
    other = tmp_path / "other"
    requested.mkdir()
    other.mkdir()
    request = LibraryProbeRequest.create(requested)
    prepared = PreparedLibrary(
        request_id=request.request_id,
        root=other.resolve(),
        database_path=other.resolve() / ".iPhoto" / "global_index.db",
        schema_version=CURRENT_SCHEMA_VERSION,
        albums=(),
        storage_kind="local",
        scan_complete=False,
    )
    process = _FakeProcess(
        json.dumps(
            {
                "protocol_version": PROBE_PROTOCOL_VERSION,
                "request_path": request.path,
                "ok": True,
                "prepared": prepared.to_payload(),
            }
        ).encode()
    )
    controller = LibraryProbeController()
    failures = []
    controller.failed.connect(failures.append)
    controller._request = request
    controller._process = process  # type: ignore[assignment]

    controller._on_finished(process, 0, QProcess.ExitStatus.NormalExit)  # type: ignore[arg-type]

    assert failures[0].code == "root_mismatch"


def test_controller_stops_helper_when_stdout_exceeds_limit(qapp, tmp_path: Path) -> None:
    request = LibraryProbeRequest.create(tmp_path)
    process = _FakeProcess(b"x" * (MAX_STDOUT_BYTES + 1), running=True)
    controller = LibraryProbeController()
    failures = []
    controller.failed.connect(failures.append)
    controller._request = request
    controller._process = process  # type: ignore[assignment]

    controller._read_stdout(process)  # type: ignore[arg-type]

    assert failures[0].code == "output_too_large"
    assert process.terminated is True


def test_controller_truncates_stderr(qapp, tmp_path: Path) -> None:
    request = LibraryProbeRequest.create(tmp_path)
    process = _FakeProcess(stderr=b"x" * (MAX_STDERR_BYTES + 100))
    controller = LibraryProbeController()
    controller._request = request
    controller._process = process  # type: ignore[assignment]

    controller._read_stderr(process)  # type: ignore[arg-type]

    assert len(controller._stderr) == MAX_STDERR_BYTES


def test_controller_timeout_uses_stable_failure_and_terminates(qapp, tmp_path: Path) -> None:
    request = LibraryProbeRequest.create(tmp_path)
    process = _FakeProcess(running=True)
    controller = LibraryProbeController()
    failures = []
    controller.failed.connect(failures.append)
    controller._request = request
    controller._process = process  # type: ignore[assignment]

    controller._on_timeout()

    assert failures[0].code == "timeout"
    assert failures[0].timed_out is True
    assert process.terminated is True
    QTest.qWait(300)


@pytest.mark.parametrize(
    ("process_error", "expected_code"),
    [
        (QProcess.ProcessError.FailedToStart, "process_failed_to_start"),
        (QProcess.ProcessError.Crashed, "process_crashed"),
    ],
)
def test_controller_classifies_process_errors(
    qapp, tmp_path: Path, process_error: QProcess.ProcessError, expected_code: str
) -> None:
    request = LibraryProbeRequest.create(tmp_path)
    process = _FakeProcess()
    controller = LibraryProbeController()
    failures = []
    controller.failed.connect(failures.append)
    controller._request = request
    controller._process = process  # type: ignore[assignment]

    controller._on_error(process, process_error)  # type: ignore[arg-type]

    assert failures[0].code == expected_code


def test_controller_cancel_terminates_then_kills_without_emitting(qapp, tmp_path: Path) -> None:
    request = LibraryProbeRequest.create(tmp_path)
    process = _FakeProcess(running=True)
    controller = LibraryProbeController()
    failures = []
    controller.failed.connect(failures.append)
    controller._request = request
    controller._process = process  # type: ignore[assignment]

    controller.cancel()

    assert process.terminated is True
    assert process.killed is False
    deadline = time.monotonic() + 1.0
    while not process.killed and time.monotonic() < deadline:
        QTest.qWait(25)
    assert process.killed is True
    assert failures == []


def test_controller_ignores_late_result_after_cancel(qapp, tmp_path: Path) -> None:
    request = LibraryProbeRequest.create(tmp_path)
    process = _FakeProcess()
    controller = LibraryProbeController()
    ready = []
    failures = []
    controller.ready.connect(ready.append)
    controller.failed.connect(failures.append)
    controller._request = request
    controller._process = process  # type: ignore[assignment]

    controller.cancel()
    controller._on_finished(process, 0, QProcess.ExitStatus.NormalExit)  # type: ignore[arg-type]

    assert ready == []
    assert failures == []


def test_controller_real_helper_round_trip(qapp, tmp_path: Path) -> None:
    library = tmp_path / "library"
    library.mkdir()
    request = LibraryProbeRequest.create(library)
    controller = LibraryProbeController()
    ready = []
    failures = []
    controller.ready.connect(ready.append)
    controller.failed.connect(failures.append)

    controller.start(request)
    deadline = time.monotonic() + 5
    while not ready and not failures and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)

    assert failures == []
    assert ready[0].request_id == request.request_id
    assert controller._process is None

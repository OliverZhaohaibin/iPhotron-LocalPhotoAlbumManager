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

import iPhoto.bootstrap.library_probe as probe_module
import iPhoto.cache.index_store.migrations as migrations_module
from iPhoto.bootstrap.library_probe import (
    MAX_STDERR_BYTES,
    MAX_STDOUT_BYTES,
    PROBE_PROTOCOL_VERSION,
    LibraryProbeController,
    LibraryProbeRequest,
    PreparedLibrary,
    ValidatedPreparedLibrary,
    _main,
    _probe_process_command,
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
from iPhoto.cache.index_store.repository import AssetRepository
from iPhoto.domain.models.query import CollectionQuery


def test_schema_migrates_source_and_thumbnail_revision_columns() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE assets (rel TEXT PRIMARY KEY)")
    connection.execute("INSERT INTO assets(rel) VALUES ('legacy.jpg')")
    connection.commit()
    connection.execute("PRAGMA user_version = 2")

    SchemaMigrator.initialize_schema(connection)

    columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(assets)")
    }
    assert {"source_mtime_ns", "image_orientation", "thumb_revision"}.issubset(
        columns
    )
    orientation = connection.execute(
        "SELECT image_orientation FROM assets WHERE rel = 'legacy.jpg'"
    ).fetchone()[0]
    assert orientation == 0
    assert (
        connection.execute("PRAGMA user_version").fetchone()[0]
        == CURRENT_SCHEMA_VERSION
    )
    connection.close()


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
    assert prepared.storage_kind == "local"


def test_probe_reads_schema_and_completed_scan_without_writing(tmp_path: Path) -> None:
    library = tmp_path / "library"
    work_dir = library / ".iPhoto"
    work_dir.mkdir(parents=True)
    database = work_dir / "global_index.db"
    with closing(sqlite3.connect(database)) as connection, connection:
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


def test_validated_prepared_library_is_single_use_and_revalidates_identity(
    tmp_path: Path,
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    prepared = probe_library(LibraryProbeRequest.create(library))
    validated = ValidatedPreparedLibrary.create(prepared)

    assert validated.consume() is prepared
    with pytest.raises(RuntimeError, match="already consumed"):
        validated.consume()

    second = probe_library(LibraryProbeRequest.create(library))
    stale = ValidatedPreparedLibrary.create(second)
    replacement = tmp_path / "replacement.db"
    replacement.write_bytes(second.database_path.read_bytes())
    os.replace(replacement, second.database_path)

    with pytest.raises(RuntimeError, match="changed before commit"):
        stale.consume()


def test_validated_prepared_library_rejects_in_place_database_changes(
    tmp_path: Path,
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    prepared = probe_library(LibraryProbeRequest.create(library))
    validated = ValidatedPreparedLibrary.create(prepared)

    with closing(sqlite3.connect(prepared.database_path)) as connection, connection:
        connection.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION + 1}")

    with pytest.raises(RuntimeError, match="changed before commit"):
        validated.consume()


def test_album_snapshot_budget_returns_partial_result_with_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library = tmp_path / "library"
    (library / "A").mkdir(parents=True)
    (library / "B").mkdir()
    monkeypatch.setattr(probe_module, "ALBUM_SNAPSHOT_BUDGET_MS", 0.0)

    albums, warnings = probe_module._snapshot_albums(library)

    assert len(albums) < 2
    assert warnings == ("album_snapshot_truncated_time",)


def test_album_snapshot_budget_stops_incremental_directory_enumeration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [0]
    consumed = [0]

    class _SlowEntries:
        def __iter__(self):
            return self

        def __next__(self):
            consumed[0] += 1
            clock[0] += 1_000_000_000
            return Path(f"/album-{consumed[0]}")

    monkeypatch.setattr(Path, "iterdir", lambda _self: _SlowEntries())
    monkeypatch.setattr(probe_module.time, "perf_counter_ns", lambda: clock[0])

    albums, warnings = probe_module._snapshot_albums(Path("/library"))

    assert albums == ()
    assert warnings == ("album_snapshot_truncated_time",)
    assert consumed == [1]


def test_linux_mountinfo_classifies_network_and_removable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(probe_module.sys, "platform", "linux")
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda _self, **_kwargs: (
            "1 0 0:1 / / rw - ext4 /dev/root rw\n"
            "2 1 0:2 / /mnt/share rw - nfs server:/share rw\n"
        ),
    )

    profile = probe_module._unix_mount_profile(Path("/mnt/share/photos"))

    assert profile is not None
    assert profile.kind == "network"
    assert profile.latency_class == "slow"


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
    with closing(sqlite3.connect(database)) as connection, connection:
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


def _create_branch_base_database(database: Path) -> None:
    """Create the complete, unversioned schema used at branch base 6ff592f7."""

    database.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database)) as connection, connection:
        SchemaMigrator.initialize_schema(connection)
        connection.execute(
            "INSERT INTO assets("
            "rel, id, parent_album_path, dt, mime, media_type, live_role, "
            "is_favorite, is_deleted, face_status, pet_status"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "album/photo.jpg",
                "asset-1",
                "album",
                "2026-01-01T00:00:00",
                "image/jpeg",
                0,
                0,
                1,
                0,
                "completed",
                "skipped",
            ),
        )
        connection.execute(
            "INSERT INTO scan_jobs(job_id, root, scope, status) "
            "VALUES ('scan-1', '/library', 'library', 'completed')"
        )
        connection.execute("PRAGMA user_version = 0")


def _downgrade_to_v1_without_video_columns(database: Path) -> None:
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("ALTER TABLE assets DROP COLUMN video_rotation_cw")
        connection.execute("ALTER TABLE assets DROP COLUMN video_linux_180_hint")
        connection.execute("PRAGMA user_version = 1")


def test_prepare_database_adopts_complete_branch_base_schema_without_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / ".iPhoto" / "global_index.db"
    _create_branch_base_database(database)
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "INSERT INTO assets(rel, id, dt, mime, media_type, face_status, pet_status) "
            "VALUES ('album/unprocessed.jpg', 'asset-2', '2026-01-02T00:00:00', "
            "'image/jpeg', 0, NULL, NULL)"
        )

    def unexpected(*_args, **_kwargs):
        raise AssertionError("complete unversioned schema entered migration path")

    monkeypatch.setattr(migrations_module, "_online_backup", unexpected)
    monkeypatch.setattr(migrations_module, "_atomic_write_state", unexpected)
    monkeypatch.setattr(SchemaMigrator, "_migrate_to_v1", staticmethod(unexpected))

    version, warnings = SchemaMigrator.prepare_database(database)

    assert version == CURRENT_SCHEMA_VERSION
    assert warnings == ()
    assert not (database.parent / MIGRATION_STATE_NAME).exists()
    assert list(database.parent.glob("*.migration-*.bak")) == []
    with closing(sqlite3.connect(database)) as connection, connection:
        row = connection.execute(
            "SELECT is_favorite, is_deleted, face_status, pet_status "
            "FROM assets WHERE rel = 'album/photo.jpg'"
        ).fetchone()
        assert row == (1, 0, "completed", "skipped")
        repaired_statuses = connection.execute(
            "SELECT face_status, pet_status FROM assets "
            "WHERE rel = 'album/unprocessed.jpg'"
        ).fetchone()
        assert repaired_statuses == ("pending", "pending")
        assert connection.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION


def test_prepare_database_adopts_complete_schema_with_pending_state(
    tmp_path: Path,
) -> None:
    database = tmp_path / ".iPhoto" / "global_index.db"
    _create_branch_base_database(database)
    migration_id = "branch-base-adopt"
    backup = database.parent / f"global_index.db.migration-{migration_id}.bak"
    backup.write_bytes(b"unused backup evidence")
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

    version, warnings = SchemaMigrator.prepare_database(database)

    assert version == CURRENT_SCHEMA_VERSION
    assert warnings == ()
    assert not state_path.exists()
    assert not backup.exists()


def test_prepare_database_migrates_legacy_database_transactionally(tmp_path: Path) -> None:
    database = tmp_path / ".iPhoto" / "global_index.db"
    _create_legacy_database(database)

    version, warnings = SchemaMigrator.prepare_database(database)

    assert version == CURRENT_SCHEMA_VERSION
    assert warnings == ()
    with closing(sqlite3.connect(database)) as connection, connection:
        assert connection.execute("SELECT rel FROM assets").fetchone() == (
            "album/photo.jpg",
        )
    assert not (database.parent / MIGRATION_STATE_NAME).exists()
    assert list(database.parent.glob("*.migration-*.bak")) == []


def test_prepare_database_migrates_v1_gallery_projection_columns(tmp_path: Path) -> None:
    """Existing libraries must gain columns selected by every Gallery window."""

    library = tmp_path / "library"
    library.mkdir()
    repository = AssetRepository(library)
    repository.write_rows(
        [
            {
                "rel": "photo.jpg",
                "id": "photo",
                "media_type": 0,
                "thumbnail_state": "ready",
                "thumb_cache_key": "photo-thumb",
            }
        ]
    )
    database = repository.path
    repository.close()

    # Reproduce a database created before cached video rotation metadata was
    # introduced, while retaining the rest of the v1 Gallery schema.
    _downgrade_to_v1_without_video_columns(database)

    reopened = AssetRepository(library)
    try:
        window = reopened.read_gallery_collection_window(CollectionQuery(), 0, 10)
        assert [row["id"] for row in window.rows] == ["photo"]
        assert window.rows[0]["video_rotation_cw"] is None
        assert window.rows[0]["video_linux_180_hint"] is None
        with closing(sqlite3.connect(database)) as connection, connection:
            assert (
                connection.execute("PRAGMA user_version").fetchone()[0]
                == CURRENT_SCHEMA_VERSION
            )
    finally:
        reopened.close()


def test_prepare_database_resumes_interrupted_multi_version_migration(
    tmp_path: Path,
) -> None:
    database = tmp_path / ".iPhoto" / "global_index.db"
    _create_branch_base_database(database)
    _downgrade_to_v1_without_video_columns(database)
    migration_id = "resume-at-v1"
    backup = database.parent / f"global_index.db.migration-{migration_id}.bak"
    with (
        closing(sqlite3.connect(database)) as source,
        closing(sqlite3.connect(backup)) as target,
        source,
        target,
    ):
        source.backup(target)
        target.execute("PRAGMA user_version = 0")
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

    version, warnings = SchemaMigrator.prepare_database(database)

    assert version == CURRENT_SCHEMA_VERSION
    assert warnings == ()
    assert not state_path.exists()
    assert not backup.exists()
    with closing(sqlite3.connect(database)) as connection, connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(assets)")}
    assert {"video_rotation_cw", "video_linux_180_hint"} <= columns


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

    with closing(sqlite3.connect(database)) as connection, connection:
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
    with (
        closing(sqlite3.connect(database)) as source,
        closing(sqlite3.connect(backup)) as target,
        source,
        target,
    ):
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
    with closing(sqlite3.connect(database)) as connection, connection:
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
    with closing(sqlite3.connect(database)) as connection, connection:
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


def test_prepare_database_classifies_sqlite_cantopen_as_open_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / ".iPhoto" / "global_index.db"

    def fail_to_open(*_args, **_kwargs):
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(migrations_module.sqlite3, "connect", fail_to_open)

    with pytest.raises(SchemaPreparationError) as caught:
        SchemaMigrator.prepare_database(database)

    assert caught.value.code == "db_open_failed"
    assert caught.value.operation == "database_open"


@pytest.mark.parametrize(
    ("error_number", "expected_code"),
    [
        (errno.EROFS, "db_read_only"),
        (errno.ENOSPC, "disk_full"),
        (errno.EACCES, "workspace_unwritable"),
    ],
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
    assert caught.value.operation == "state_write"


@pytest.mark.parametrize(
    ("winerror", "expected_code"),
    [(32, "migration_file_busy"), (33, "migration_file_busy")],
)
def test_windows_sharing_violations_are_not_reported_as_read_only(
    winerror: int, expected_code: str
) -> None:
    error = PermissionError(errno.EACCES, "sharing violation")
    error.winerror = winerror  # type: ignore[attr-defined]

    assert (
        migrations_module._classify_filesystem_error(
            error, operation="backup_publish"
        )
        == expected_code
    )


@pytest.mark.parametrize(
    ("native_code", "expected_code"),
    [
        ("SQLITE_BUSY", "db_locked"),
        ("SQLITE_LOCKED_SHAREDCACHE", "db_locked"),
        ("SQLITE_READONLY_DBMOVED", "db_read_only"),
        ("SQLITE_FULL", "disk_full"),
        ("SQLITE_CANTOPEN_ISDIR", "db_open_failed"),
    ],
)
def test_sqlite_extended_codes_drive_failure_classification(
    native_code: str, expected_code: str
) -> None:
    error = sqlite3.OperationalError("platform-dependent text")
    error.sqlite_errorname = native_code  # type: ignore[attr-defined]

    assert (
        migrations_module._classify_sqlite_operational_error(
            error, fallback="migration_failed"
        )
        == expected_code
    )


def test_managed_connection_explicitly_closes_database_handle(tmp_path: Path) -> None:
    database = tmp_path / "connection.db"

    with migrations_module._managed_connection(database) as connection:
        connection.execute("CREATE TABLE sample(value INTEGER)")

    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        connection.execute("SELECT 1")


def test_schema_failure_diagnostics_cross_probe_boundary() -> None:
    error = SchemaPreparationError(
        "migration_file_busy",
        "busy",
        operation="backup_publish",
        native_code="WinError_32",
    )

    failure = probe_module._exception_failure("request-1", error)
    round_trip = probe_module.LibraryProbeFailure.from_payload(
        "request-1", failure.to_payload()
    )

    assert round_trip.code == "migration_file_busy"
    assert round_trip.operation == "backup_publish"
    assert round_trip.native_code == "WinError_32"
    assert "read-only" not in round_trip.message


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
    with closing(sqlite3.connect(database)) as connection, connection:
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


def test_controller_rejects_result_for_another_request_id(qapp, tmp_path: Path) -> None:
    request = LibraryProbeRequest.create(tmp_path)
    prepared = probe_library(request)
    payload = prepared.to_payload()
    payload["request_id"] = "another-request"
    process = _FakeProcess(
        json.dumps(
            {
                "protocol_version": PROBE_PROTOCOL_VERSION,
                "request_path": request.path,
                "ok": True,
                "prepared": payload,
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
    deadline = time.monotonic() + 2.0
    while not process.killed and time.monotonic() < deadline:
        QTest.qWait(10)
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


def test_probe_process_command_uses_checkout_entrypoint_for_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delitem(probe_module.__dict__, "__compiled__", raising=False)
    monkeypatch.delattr(probe_module.sys, "frozen", raising=False)

    program, arguments = _probe_process_command()

    assert program == sys.executable
    assert arguments == [
        str(Path(probe_module.__file__).resolve().parents[2] / "entrypoint.py"),
        "--startup-library-probe",
        "--stdin",
    ]


def test_probe_process_command_falls_back_to_module_without_checkout_entrypoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delitem(probe_module.__dict__, "__compiled__", raising=False)
    monkeypatch.delattr(probe_module.sys, "frozen", raising=False)
    monkeypatch.setattr(probe_module, "_source_probe_entrypoint", lambda: None)

    program, arguments = _probe_process_command()

    assert program == sys.executable
    assert arguments == ["-m", "iPhoto.bootstrap.library_probe", "--stdin"]


def test_checkout_helper_ignores_stale_pythonpath_install(tmp_path: Path) -> None:
    stale_package = tmp_path / "stale-install" / "iPhoto"
    stale_package.mkdir(parents=True)
    (stale_package / "__init__.py").write_text(
        'raise RuntimeError("stale iPhoto install was imported")\n',
        encoding="utf-8",
    )
    library = tmp_path / "library"
    library.mkdir()
    request = LibraryProbeRequest.create(library)
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(stale_package.parent)
    program, arguments = _probe_process_command()

    completed = subprocess.run(
        [program, *arguments],
        input=json.dumps(asdict(request)),
        text=True,
        capture_output=True,
        check=False,
        env=environment,
        cwd=tmp_path,
    )

    envelope = json.loads(completed.stdout)
    assert completed.returncode == 0
    assert envelope["protocol_version"] == PROBE_PROTOCOL_VERSION
    assert envelope["prepared"]["request_id"] == request.request_id


def test_probe_process_command_uses_qt_application_path_when_packaged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packaged_executable = "/Applications/iPhotron.app/Contents/MacOS/iPhotron"
    monkeypatch.setitem(probe_module.__dict__, "__compiled__", object())
    monkeypatch.setattr(
        probe_module.QCoreApplication,
        "applicationFilePath",
        staticmethod(lambda: packaged_executable),
    )

    program, arguments = _probe_process_command()

    assert program == packaged_executable
    assert arguments == ["--startup-library-probe", "--stdin"]

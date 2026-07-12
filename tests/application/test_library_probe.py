from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from iPhoto.bootstrap.library_probe import (
    LibraryProbeRequest,
    PreparedLibrary,
    probe_library,
)
from iPhoto.cache.index_store.migrations import CURRENT_SCHEMA_VERSION


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
    with sqlite3.connect(database) as connection:
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

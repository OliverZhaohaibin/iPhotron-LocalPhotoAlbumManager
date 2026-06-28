from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from iPhoto.cache.index_store import get_global_repository, reset_global_repository
from iPhoto.infrastructure.repositories.location_assignment_repository import (
    IndexStoreLocationAssignmentRepository,
)


@pytest.fixture(autouse=True)
def clean_global_repository():
    reset_global_repository()
    yield
    reset_global_repository()


def _ensure_metadata_column(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(assets)")
        }
        if "metadata" not in columns:
            conn.execute("ALTER TABLE assets ADD COLUMN metadata TEXT")


def test_location_assignment_repository_updates_asset_and_creates_job_atomically(
    tmp_path: Path,
) -> None:
    repo = get_global_repository(tmp_path)
    repo.write_rows([{"rel": "clip.mov", "id": "asset-1", "bytes": 10}])
    _ensure_metadata_column(repo.path)
    assignment_repo = IndexStoreLocationAssignmentRepository(tmp_path)

    job = assignment_repo.assign_location(
        asset_rel="clip.mov",
        asset_path=tmp_path / "clip.mov",
        gps={"lat": 48.137154, "lon": 11.576124},
        location="Munich",
        is_video=True,
        metadata_updates={"codec": "hevc"},
    )

    row = next(repo.read_all())
    assert row["gps"] == {"lat": 48.137154, "lon": 11.576124}
    assert row["location"] == "Munich"
    metadata = json.loads(row["metadata"])
    assert metadata["codec"] == "hevc"
    assert metadata["location_name"] == "Munich"
    with sqlite3.connect(repo.path) as conn:
        stored_job = conn.execute(
            "SELECT asset_rel, gps_json, location, media_kind, status FROM metadata_write_jobs WHERE job_id = ?",
            (job.job_id,),
        ).fetchone()
    assert stored_job is not None
    assert stored_job[0] == "clip.mov"
    assert json.loads(stored_job[1]) == {"lat": 48.137154, "lon": 11.576124}
    assert stored_job[2:] == ("Munich", "video", "queued")


def test_location_assignment_repository_rolls_back_job_when_asset_is_missing(
    tmp_path: Path,
) -> None:
    repo = get_global_repository(tmp_path)
    assignment_repo = IndexStoreLocationAssignmentRepository(tmp_path)

    with pytest.raises(ValueError):
        assignment_repo.assign_location(
            asset_rel="missing.mov",
            asset_path=tmp_path / "missing.mov",
            gps={"lat": 48.137154, "lon": 11.576124},
            location="Munich",
            is_video=True,
            metadata_updates={},
        )

    with sqlite3.connect(repo.path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM metadata_write_jobs").fetchone()[0]
    assert count == 0


def test_location_assignment_repository_supersedes_old_failed_jobs_for_asset(
    tmp_path: Path,
) -> None:
    repo = get_global_repository(tmp_path)
    repo.write_rows([{"rel": "clip.mov", "id": "asset-1", "bytes": 10}])
    now = 1_700_000_000_000
    with sqlite3.connect(repo.path) as conn:
        conn.execute(
            """
            INSERT INTO metadata_write_jobs (
                job_id, asset_rel, asset_path, gps_json, location, media_kind,
                status, attempts, last_error, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "old-job",
                "clip.mov",
                str(tmp_path / "clip.mov"),
                json.dumps({"lat": 1.0, "lon": 2.0}),
                "Old",
                "video",
                "failed",
                1,
                "permission denied",
                now,
                now,
            ),
        )
    assignment_repo = IndexStoreLocationAssignmentRepository(tmp_path)

    new_job = assignment_repo.assign_location(
        asset_rel="clip.mov",
        asset_path=tmp_path / "clip.mov",
        gps={"lat": 48.137154, "lon": 11.576124},
        location="Munich",
        is_video=True,
        metadata_updates={},
    )

    with sqlite3.connect(repo.path) as conn:
        rows = conn.execute(
            """
            SELECT job_id, status, last_error
            FROM metadata_write_jobs
            WHERE asset_rel = ?
            ORDER BY created_at ASC
            """,
            ("clip.mov",),
        ).fetchall()

    assert rows[0] == (
        "old-job",
        "superseded",
        "Superseded by a newer location assignment",
    )
    assert rows[1][0] == new_job.job_id
    assert rows[1][1:] == ("queued", None)

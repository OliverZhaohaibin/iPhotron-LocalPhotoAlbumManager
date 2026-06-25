"""Persistence for durable original-file metadata write jobs."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from ...application.ports import LocationWriteJobRecord
from ...cache.index_store.repository import get_global_repository


class MetadataWriteJobRepository:
    """Store and recover ExifTool write-back jobs in the library index DB."""

    _RECOVERABLE_STATUSES = ("queued", "writing")

    def __init__(self, library_root: Path) -> None:
        self._library_root = Path(library_root)

    def create_location_job(
        self,
        *,
        asset_rel: str,
        asset_path: Path,
        gps: dict[str, float],
        location: str,
        is_video: bool,
    ) -> LocationWriteJobRecord:
        now = _utc_ms()
        job = LocationWriteJobRecord(
            job_id=str(uuid.uuid4()),
            asset_rel=str(asset_rel),
            asset_path=Path(asset_path),
            gps={"lat": float(gps["lat"]), "lon": float(gps["lon"])},
            location=str(location or ""),
            media_kind="video" if is_video else "image",
            status="queued",
            attempts=0,
            last_error=None,
        )
        repo = get_global_repository(self._library_root)
        with repo.transaction(begin_mode="IMMEDIATE") as conn:
            conn.execute(
                """
                INSERT INTO metadata_write_jobs (
                    job_id, asset_rel, asset_path, gps_json, location, media_kind,
                    status, attempts, last_error, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.job_id,
                    job.asset_rel,
                    str(job.asset_path),
                    json.dumps(job.gps, ensure_ascii=False),
                    job.location,
                    job.media_kind,
                    job.status,
                    job.attempts,
                    job.last_error,
                    now,
                    now,
                ),
            )
        return job

    def list_recoverable_jobs(self) -> list[LocationWriteJobRecord]:
        repo = get_global_repository(self._library_root)
        with repo.transaction() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM metadata_write_jobs
                WHERE status IN (?, ?)
                ORDER BY created_at ASC, job_id ASC
                """,
                self._RECOVERABLE_STATUSES,
            ).fetchall()
        return [self._job_from_row(row) for row in rows]

    def list_failed_jobs(self, *, asset_rel: str | None = None) -> list[LocationWriteJobRecord]:
        repo = get_global_repository(self._library_root)
        params: list[object] = ["failed"]
        rel_clause = ""
        if asset_rel:
            rel_clause = " AND asset_rel = ?"
            params.append(str(asset_rel))
        with repo.transaction() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM metadata_write_jobs
                WHERE status = ?{rel_clause}
                ORDER BY updated_at ASC, created_at ASC, job_id ASC
                """,
                params,
            ).fetchall()
        return [self._job_from_row(row) for row in rows]

    def mark_writing(self, job_id: str) -> None:
        self._update_status(
            job_id,
            status="writing",
            increment_attempts=True,
            last_error=None,
        )

    def mark_queued(self, job_id: str, *, last_error: str | None = None) -> None:
        self._update_status(
            job_id,
            status="queued",
            increment_attempts=False,
            last_error=last_error,
        )

    def mark_verified(self, job_id: str) -> None:
        self._update_status(
            job_id,
            status="verified",
            increment_attempts=False,
            last_error=None,
        )

    def mark_failed(self, job_id: str, error: str) -> None:
        self._update_status(
            job_id,
            status="failed",
            increment_attempts=False,
            last_error=str(error),
        )

    def _update_status(
        self,
        job_id: str,
        *,
        status: str,
        increment_attempts: bool,
        last_error: str | None,
    ) -> None:
        repo = get_global_repository(self._library_root)
        attempts_sql = "attempts = attempts + 1," if increment_attempts else ""
        with repo.transaction(begin_mode="IMMEDIATE") as conn:
            conn.execute(
                f"""
                UPDATE metadata_write_jobs
                SET status = ?,
                    {attempts_sql}
                    last_error = ?,
                    updated_at = ?
                WHERE job_id = ?
                """,
                (status, last_error, _utc_ms(), job_id),
            )

    @staticmethod
    def _job_from_row(row: Any) -> LocationWriteJobRecord:
        raw_gps = row["gps_json"] if isinstance(row, dict) else row[3]
        try:
            gps = json.loads(raw_gps)
        except (TypeError, json.JSONDecodeError):
            gps = {}
        if not isinstance(gps, dict):
            gps = {}
        return LocationWriteJobRecord(
            job_id=str(row["job_id"] if isinstance(row, dict) else row[0]),
            asset_rel=str(row["asset_rel"] if isinstance(row, dict) else row[1]),
            asset_path=Path(row["asset_path"] if isinstance(row, dict) else row[2]),
            gps={
                "lat": float(gps.get("lat")),
                "lon": float(gps.get("lon")),
            },
            location=str(row["location"] if isinstance(row, dict) else row[4] or ""),
            media_kind=str(row["media_kind"] if isinstance(row, dict) else row[5]),
            status=str(row["status"] if isinstance(row, dict) else row[6]),
            attempts=int(row["attempts"] if isinstance(row, dict) else row[7] or 0),
            last_error=(row["last_error"] if isinstance(row, dict) else row[8]),
        )


def _utc_ms() -> int:
    return int(time.time() * 1000)


__all__ = ["MetadataWriteJobRepository"]

"""Atomic persistence for user-assigned asset locations."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from ...application.ports import LocationWriteJobRecord
from ...cache.index_store.repository import (
    _sanitize_metadata_for_json,
    get_global_repository,
)


class IndexStoreLocationAssignmentRepository:
    """Persist local geodata and its write-back job in one index transaction."""

    def __init__(self, library_root: Path) -> None:
        self._library_root = Path(library_root)

    def assign_location(
        self,
        *,
        asset_rel: str,
        asset_path: Path,
        gps: dict[str, float],
        location: str,
        is_video: bool,
        metadata_updates: dict[str, Any],
    ) -> LocationWriteJobRecord:
        normalized_location = str(location or "").strip()
        normalized_gps = {"lat": float(gps["lat"]), "lon": float(gps["lon"])}
        now = _utc_ms()
        job = LocationWriteJobRecord(
            job_id=str(uuid.uuid4()),
            asset_rel=str(asset_rel),
            asset_path=Path(asset_path),
            gps=normalized_gps,
            location=normalized_location,
            media_kind="video" if is_video else "image",
            status="queued",
            attempts=0,
            last_error=None,
        )

        repo = get_global_repository(self._library_root)
        with repo.transaction(begin_mode="IMMEDIATE") as conn:
            columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(assets)")
            }
            metadata_column_present = "metadata" in columns
            select_sql = (
                "SELECT metadata FROM assets WHERE rel = ?"
                if metadata_column_present
                else "SELECT rel FROM assets WHERE rel = ?"
            )
            row = conn.execute(select_sql, (asset_rel,)).fetchone()
            if row is None:
                raise ValueError(f"Asset is not indexed in this library: {asset_rel}")

            update_parts = ["gps = ?", "has_gps = ?", "location = ?"]
            params: list[Any] = [
                json.dumps(normalized_gps, ensure_ascii=False),
                1,
                normalized_location,
            ]

            if metadata_column_present:
                metadata = _decode_metadata(row[0])
                metadata.update(
                    _sanitize_metadata_for_json(
                        {
                            key: value
                            for key, value in metadata_updates.items()
                            if value is not None
                        }
                    )
                )
                metadata["gps"] = dict(normalized_gps)
                metadata["location"] = normalized_location
                metadata["location_name"] = normalized_location
                update_parts.append("metadata = ?")
                params.append(json.dumps(metadata, ensure_ascii=False))

            params.append(asset_rel)
            conn.execute(
                f"UPDATE assets SET {', '.join(update_parts)} WHERE rel = ?",
                params,
            )
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
        try:
            repo._clear_collection_anchor_cache()
        except AttributeError:
            pass
        return job


def _decode_metadata(raw_metadata: object) -> dict[str, Any]:
    if not raw_metadata:
        return {}
    try:
        decoded = json.loads(str(raw_metadata))
    except (TypeError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _utc_ms() -> int:
    return int(time.time() * 1000)


__all__ = ["IndexStoreLocationAssignmentRepository"]

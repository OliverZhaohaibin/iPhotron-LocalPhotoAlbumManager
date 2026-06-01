"""Row mapping helpers for converting between dicts and DB rows.

This module contains standalone functions for mapping asset dictionaries
to database parameters and vice versa, as well as bulk insert logic.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List

from ...config import RECENTLY_DELETED_DIR_NAME


def insert_rows(
    conn: sqlite3.Connection,
    rows: Iterable[Dict[str, Any]],
) -> None:
    """Bulk insert rows into the assets table."""
    data_list = []
    for row in rows:
        data = row_to_db_params(row)
        data_list.append(data)

    if not data_list:
        return

    columns = [
        "rel", "id", "parent_album_path", "dt", "ts", "sort_ts", "bytes", "mime",
        "make", "model", "lens", "iso", "f_number", "exposure_time",
        "exposure_compensation", "focal_length", "w", "h", "gps",
        "content_id", "frame_rate", "codec", "still_image_time", "dur",
        "original_rel_path", "original_album_id", "original_album_subpath",
        "live_role", "live_partner_rel", "aspect_ratio", "year", "month",
        "media_type", "is_favorite", "is_deleted", "has_gps", "thumbnail_state",
        "location", "micro_thumbnail", "thumb_cache_key", "thumb_updated_at",
        "thumb_error", "scan_job_id", "index_revision", "face_status"
    ]
    placeholders = ", ".join(["?"] * len(columns))
    query = (
        f"INSERT OR REPLACE INTO assets ({', '.join(columns)}) "
        f"VALUES ({placeholders})"
    )

    conn.executemany(query, data_list)


def row_to_db_params(row: Dict[str, Any]) -> List[Any]:
    """Map a dictionary row to a list of values for the DB."""
    gps_val = row.get("gps")
    gps_str = json.dumps(gps_val) if gps_val is not None else None
    has_gps = row.get("has_gps")
    if has_gps is None:
        has_gps = 1 if gps_val is not None else 0
    sort_ts = row.get("sort_ts")
    if sort_ts is None:
        sort_ts = row.get("ts")
    thumbnail_state = _thumbnail_state_for_row(row)

    # Compute parent_album_path from rel if not provided
    rel = row.get("rel")
    parent_album_path = row.get("parent_album_path")
    if parent_album_path is None and rel:
        rel_path = Path(rel)
        parent = rel_path.parent
        parent_album_path = parent.as_posix() if parent != Path(".") else ""
    is_deleted = row.get("is_deleted")
    if is_deleted is None:
        rel_str = str(rel or "")
        is_deleted = 1 if (
            parent_album_path == RECENTLY_DELETED_DIR_NAME
            or rel_str.startswith(f"{RECENTLY_DELETED_DIR_NAME}/")
        ) else 0

    return [
        rel,
        row.get("id"),
        parent_album_path,
        row.get("dt"),
        row.get("ts"),
        sort_ts,
        row.get("bytes"),
        row.get("mime"),
        row.get("make"),
        row.get("model"),
        row.get("lens"),
        row.get("iso"),
        row.get("f_number"),
        row.get("exposure_time"),
        row.get("exposure_compensation"),
        row.get("focal_length"),
        row.get("w"),
        row.get("h"),
        gps_str,
        row.get("content_id"),
        row.get("frame_rate"),
        row.get("codec"),
        row.get("still_image_time"),
        row.get("dur"),
        row.get("original_rel_path"),
        row.get("original_album_id"),
        row.get("original_album_subpath"),
        row.get("live_role", 0),
        row.get("live_partner_rel"),
        row.get("aspect_ratio"),
        row.get("year"),
        row.get("month"),
        row.get("media_type"),
        row.get("is_favorite", 0),
        is_deleted,
        has_gps,
        thumbnail_state,
        row.get("location"),
        row.get("micro_thumbnail"),
        row.get("thumb_cache_key"),
        row.get("thumb_updated_at", 0),
        row.get("thumb_error"),
        row.get("scan_job_id"),
        row.get("index_revision", 0),
        row.get("face_status"),
    ]


def _thumbnail_state_for_row(row: Dict[str, Any]) -> str:
    state = str(row.get("thumbnail_state") or "").strip().lower()
    if not state:
        state = "ready" if _has_thumbnail_payload(row) else "stale"
    if state == "ready" and not _has_thumbnail_payload(row):
        return "stale"
    if state not in {"ready", "pending", "failed", "stale"}:
        return "stale"
    return state


def _has_thumbnail_payload(row: Dict[str, Any]) -> bool:
    thumb_key = row.get("thumb_cache_key")
    return isinstance(thumb_key, str) and bool(thumb_key.strip())


def db_row_to_dict(db_row: sqlite3.Row) -> Dict[str, Any]:
    """Map a DB row back to a dictionary."""
    d = dict(db_row)
    if d.get("gps") is not None:
        try:
            d["gps"] = json.loads(d["gps"])
        except json.JSONDecodeError:
            d["gps"] = None
    return d

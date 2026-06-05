"""Helpers for merging scan rows with persisted library state."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any

from ...people import (
    FACE_STATUS_DONE,
    FACE_STATUS_SKIPPED,
    initial_face_status,
    normalize_face_status,
)

_PRESERVED_SCAN_STATE_FIELDS = (
    "is_favorite",
    "original_rel_path",
    "original_album_id",
    "original_album_subpath",
    "live_role",
    "live_partner_rel",
    "is_deleted",
)


def merge_scan_rows(
    scanned_rows: Iterable[dict[str, Any]],
    existing_rows_by_rel: Mapping[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge freshly scanned rows with persisted library-managed state."""

    return [
        merge_scan_row(row, existing_rows_by_rel.get(str(row.get("rel") or "")))
        for row in scanned_rows
    ]


def merge_scan_row(
    scanned_row: dict[str, Any],
    existing_row: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge one scanned row with an existing persisted row."""

    merged = dict(scanned_row)

    if existing_row is not None:
        for field in _PRESERVED_SCAN_STATE_FIELDS:
            if field in existing_row and (field not in merged or merged.get(field) in (None, "")):
                merged[field] = existing_row.get(field)
        _preserve_location_state(merged, existing_row)

    identity_unchanged = (
        existing_row is not None and _asset_identity_unchanged(existing_row, merged)
    )
    existing_face_status = None
    if existing_row is not None:
        existing_face_status = normalize_face_status(existing_row.get("face_status"))

    if (
        existing_face_status is not None
        and existing_face_status in {FACE_STATUS_DONE, FACE_STATUS_SKIPPED}
        and identity_unchanged
    ):
        merged["face_status"] = existing_face_status
    else:
        merged["face_status"] = initial_face_status(
            _row_for_face_status(
                merged,
                preserve_live_state=identity_unchanged,
            )
        )

    return merged


def _preserve_location_state(
    merged: dict[str, Any],
    existing_row: dict[str, Any],
) -> None:
    existing_metadata = _metadata_dict(existing_row.get("metadata"))
    existing_gps = existing_row.get("gps")
    existing_location = (
        existing_row.get("location")
        or existing_metadata.get("location")
        or existing_metadata.get("location_name")
    )
    has_manual_location = _meaningful(existing_location)
    if has_manual_location and _meaningful(existing_gps):
        merged["gps"] = existing_gps
        merged["has_gps"] = 1
    elif (
        has_manual_location
        and existing_row.get("has_gps") in (1, True)
        and merged.get("gps") is None
    ):
        merged["has_gps"] = 1

    if has_manual_location:
        merged["location"] = existing_location

    scanned_metadata = _metadata_dict(merged.get("metadata"))
    if existing_metadata or scanned_metadata:
        metadata = dict(existing_metadata)
        metadata.update(scanned_metadata)
        for key in ("gps", "location", "location_name"):
            value = existing_metadata.get(key)
            if _meaningful(value):
                metadata[key] = value
        if "gps" in merged and _meaningful(merged["gps"]):
            metadata["gps"] = merged["gps"]
        if "location" in merged and _meaningful(merged["location"]):
            metadata["location"] = merged["location"]
            metadata.setdefault("location_name", merged["location"])
        merged["metadata"] = metadata


def _metadata_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        if isinstance(decoded, dict):
            return decoded
    return {}


def _meaningful(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return bool(value)
    return True


def _asset_identity_unchanged(
    existing_row: dict[str, Any],
    scanned_row: dict[str, Any],
) -> bool:
    existing_id = existing_row.get("id")
    scanned_id = scanned_row.get("id")
    if existing_id is None or scanned_id is None:
        return False
    return str(existing_id) == str(scanned_id)


def _row_for_face_status(
    merged_row: dict[str, Any],
    *,
    preserve_live_state: bool,
) -> dict[str, Any]:
    if preserve_live_state:
        return merged_row

    row = dict(merged_row)
    row.pop("live_role", None)
    row.pop("live_partner_rel", None)
    return row

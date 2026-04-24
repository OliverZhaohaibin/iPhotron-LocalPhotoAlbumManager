"""Helpers for merging scan rows with persisted library state."""

from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping

from ...people import FACE_STATUS_FAILED, initial_face_status, normalize_face_status

_PRESERVED_SCAN_STATE_FIELDS = (
    "is_favorite",
    "original_rel_path",
    "original_album_id",
    "original_album_subpath",
    "live_role",
    "live_partner_rel",
)


def merge_scan_rows(
    scanned_rows: Iterable[Dict[str, Any]],
    existing_rows_by_rel: Mapping[str, Dict[str, Any]],
) -> list[Dict[str, Any]]:
    """Merge freshly scanned rows with persisted library-managed state."""

    return [
        merge_scan_row(row, existing_rows_by_rel.get(str(row.get("rel") or "")))
        for row in scanned_rows
    ]


def merge_scan_row(
    scanned_row: Dict[str, Any],
    existing_row: Dict[str, Any] | None,
) -> Dict[str, Any]:
    """Merge one scanned row with an existing persisted row."""

    merged = dict(scanned_row)

    if existing_row is not None:
        for field in _PRESERVED_SCAN_STATE_FIELDS:
            if field in existing_row and (field not in merged or merged.get(field) in (None, "")):
                merged[field] = existing_row.get(field)

    identity_unchanged = existing_row is not None and _asset_identity_unchanged(existing_row, merged)
    existing_face_status = None
    if existing_row is not None:
        existing_face_status = normalize_face_status(existing_row.get("face_status"))

    if (
        existing_face_status is not None
        and existing_face_status != FACE_STATUS_FAILED
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


def _asset_identity_unchanged(
    existing_row: Dict[str, Any],
    scanned_row: Dict[str, Any],
) -> bool:
    existing_id = existing_row.get("id")
    scanned_id = scanned_row.get("id")
    if existing_id is None or scanned_id is None:
        return False
    return str(existing_id) == str(scanned_id)


def _row_for_face_status(
    merged_row: Dict[str, Any],
    *,
    preserve_live_state: bool,
) -> Dict[str, Any]:
    if preserve_live_state:
        return merged_row

    row = dict(merged_row)
    row.pop("live_role", None)
    row.pop("live_partner_rel", None)
    return row

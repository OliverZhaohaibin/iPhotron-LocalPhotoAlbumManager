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
from ...pets import (
    PET_STATUS_DONE,
    PET_STATUS_SKIPPED,
    initial_pet_status,
    normalize_pet_status,
)

_PRESERVED_SCAN_STATE_FIELDS = (
    "is_favorite",
    "original_rel_path",
    "original_album_id",
    "original_album_subpath",
    "is_deleted",
)
_THUMBNAIL_STATE_FIELDS = (
    "thumbnail_state",
    "micro_thumbnail",
    "thumb_cache_key",
    "thumb_updated_at",
    "thumb_error",
    "thumb_revision",
    "thumb_refresh_pending",
)


def merge_scan_rows(
    scanned_rows: Iterable[dict[str, Any]],
    existing_rows_by_rel: Mapping[str, dict[str, Any]],
    *,
    current_thumbnail_revisions: Mapping[str, str | None],
) -> list[dict[str, Any]]:
    """Merge freshly scanned rows with persisted library-managed state."""

    return [
        merge_scan_row(
            row,
            existing_rows_by_rel.get(str(row.get("rel") or "")),
            current_thumbnail_revision=current_thumbnail_revisions.get(str(row.get("rel") or "")),
        )
        for row in scanned_rows
    ]


def merge_scan_row(
    scanned_row: dict[str, Any],
    existing_row: dict[str, Any] | None,
    *,
    current_thumbnail_revision: str | None,
) -> dict[str, Any]:
    """Merge one scanned row with an existing persisted row."""

    merged = dict(scanned_row)
    identity_unchanged = existing_row is not None and _asset_identity_unchanged(existing_row, merged)
    # Pairing is computed from the indexed media. Cached scan rows can carry
    # old links, and a replacement must be visible even if pairing is skipped.
    if identity_unchanged and existing_row is not None:
        merged["live_role"] = existing_row.get("live_role", 0)
        merged["live_partner_rel"] = existing_row.get("live_partner_rel")
    else:
        merged["live_role"] = 0
        merged["live_partner_rel"] = None

    if existing_row is not None:
        # Cached scan rows can contain state captured before a user edit.
        for field in _PRESERVED_SCAN_STATE_FIELDS:
            if field in existing_row:
                merged[field] = existing_row.get(field)
        _preserve_location_state(merged, existing_row)
        _preserve_current_thumbnail_revision(
            merged, existing_row, current_thumbnail_revision
        )
        # The stat cache can predate a concurrent thumbnail commit. Preserve
        # its row version and advance it if this merge replaces thumbnail facts.
        previous_revision = int(existing_row.get("index_revision") or 0)
        refresh_changed = bool(merged.get("thumb_refresh_pending")) != bool(existing_row.get("thumb_refresh_pending"))
        thumbnail_changed = any(
            merged.get(field) != existing_row.get(field)
            for field in _THUMBNAIL_STATE_FIELDS
            if field != "thumb_refresh_pending"
        ) or refresh_changed
        merged["index_revision"] = previous_revision + int(not identity_unchanged or thumbnail_changed)

    existing_face_status = None
    existing_pet_status = None
    if existing_row is not None:
        existing_face_status = normalize_face_status(existing_row.get("face_status"))
        existing_pet_status = normalize_pet_status(existing_row.get("pet_status"))

    if (
        existing_face_status is not None
        and existing_face_status in {FACE_STATUS_DONE, FACE_STATUS_SKIPPED}
        and identity_unchanged
    ):
        merged["face_status"] = existing_face_status
    else:
        merged["face_status"] = initial_face_status(merged)

    if (
        existing_pet_status is not None
        and existing_pet_status in {PET_STATUS_DONE, PET_STATUS_SKIPPED}
        and identity_unchanged
    ):
        merged["pet_status"] = existing_pet_status
    else:
        merged["pet_status"] = initial_pet_status(merged)

    return merged


def conflicting_thumbnail_revisions(
    rows: Iterable[dict[str, Any]],
    existing_rows: Mapping[str, dict[str, Any]],
) -> dict[str, tuple[str, int]]:
    """Identify rows that need input validation and the DB versions read for them."""

    conflicts = {}
    for row in rows:
        rel = str(row.get("rel") or "")
        existing = existing_rows.get(rel)
        if existing is None:
            continue
        revision = _thumbnail_revision(existing)
        if revision and revision != _thumbnail_revision(row):
            conflicts[rel] = revision, int(existing.get("index_revision") or 0)
    return conflicts


def _thumbnail_revision(row: Mapping[str, Any]) -> str:
    return str(row.get("thumb_revision") or "").strip()


def _preserve_current_thumbnail_revision(
    merged: dict[str, Any],
    existing_row: dict[str, Any],
    current_revision: str | None,
) -> None:
    """Resolve conflicting versions against the current render inputs."""

    existing_revision = _thumbnail_revision(existing_row)
    incoming_revision = _thumbnail_revision(merged)
    if existing_revision == incoming_revision:
        late_failure = (
            merged.get("thumbnail_state") != "ready"
            and int(merged.get("index_revision") or 0) < int(existing_row.get("index_revision") or 0)
        )
        if (
            (merged.get("thumb_refresh_pending") or late_failure)
            and not existing_row.get("thumb_refresh_pending")
            and existing_row.get("thumbnail_state") == "ready"
            and existing_revision
            and _asset_identity_unchanged(existing_row, merged)
        ):
            for field in _THUMBNAIL_STATE_FIELDS:
                merged[field] = existing_row.get(field)
        return
    if not existing_revision:
        return
    if current_revision is not None:
        if current_revision == incoming_revision:
            return
        if current_revision != existing_revision:
            merged.update(
                thumb_revision=current_revision,
                thumbnail_state="stale",
                thumb_refresh_pending=False,
                micro_thumbnail=None,
                thumb_cache_key=(
                    merged.get("thumb_cache_key") or existing_row.get("thumb_cache_key")
                ),
                thumb_error=None,
            )
            return
    for field in _THUMBNAIL_STATE_FIELDS:
        if field in existing_row:
            merged[field] = existing_row[field]
        else:
            merged.pop(field, None)


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

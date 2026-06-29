"""Shared pet-processing status helpers."""

from __future__ import annotations

from typing import Any

PET_STATUS_PENDING = "pending"
PET_STATUS_DONE = "done"
PET_STATUS_FAILED = "failed"
PET_STATUS_RETRY = "retry"
PET_STATUS_SKIPPED = "skipped"

PET_STATUS_VALUES = frozenset(
    {
        PET_STATUS_PENDING,
        PET_STATUS_DONE,
        PET_STATUS_FAILED,
        PET_STATUS_RETRY,
        PET_STATUS_SKIPPED,
    }
)


def normalize_pet_status(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized in PET_STATUS_VALUES:
        return normalized
    return None


def is_pet_scan_candidate(row: dict[str, Any]) -> bool:
    media_type = row.get("media_type")
    if isinstance(media_type, str):
        normalized_media = media_type.strip().lower()
    elif isinstance(media_type, int):
        normalized_media = str(media_type)
    else:
        normalized_media = ""

    if normalized_media in {"1", "video"}:
        return False

    live_role = row.get("live_role")
    if isinstance(live_role, (int, float)) and int(live_role) != 0:
        return False

    mime = row.get("mime")
    if isinstance(mime, str) and mime.lower().startswith("video/"):
        return False

    return normalized_media in {"0", "photo", "image", "live", ""} or bool(
        row.get("live_partner_rel") or row.get("live_photo_group_id")
    )


def initial_pet_status(row: dict[str, Any]) -> str:
    return PET_STATUS_PENDING if is_pet_scan_candidate(row) else PET_STATUS_SKIPPED

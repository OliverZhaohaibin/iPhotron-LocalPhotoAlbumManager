"""Shared face-processing status helpers."""

from __future__ import annotations

from typing import Any


FACE_STATUS_PENDING = "pending"
FACE_STATUS_DONE = "done"
FACE_STATUS_FAILED = "failed"
FACE_STATUS_RETRY = "retry"
FACE_STATUS_SKIPPED = "skipped"

FACE_STATUS_VALUES = frozenset(
    {
        FACE_STATUS_PENDING,
        FACE_STATUS_DONE,
        FACE_STATUS_FAILED,
        FACE_STATUS_RETRY,
        FACE_STATUS_SKIPPED,
    }
)


def normalize_face_status(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized in FACE_STATUS_VALUES:
        return normalized
    return None


def is_face_scan_candidate(row: dict[str, Any]) -> bool:
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


def initial_face_status(row: dict[str, Any]) -> str:
    return FACE_STATUS_PENDING if is_face_scan_candidate(row) else FACE_STATUS_SKIPPED


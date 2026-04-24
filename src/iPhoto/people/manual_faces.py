"""Manual People annotations that are intentionally separate from AI clustering."""

from __future__ import annotations

import uuid
from pathlib import Path

from .image_utils import load_image_rgb, save_face_thumbnail
from .records import ManualFaceRecord
from .repository_utils import _utc_now_iso

MANUAL_FACE_MIN_SIZE = 40


class ManualFaceValidationError(ValueError):
    """Raised when a manual face selection cannot be saved safely."""


def build_manual_face_record(
    *,
    asset_id: str,
    asset_rel: str,
    image_path: Path,
    requested_box: tuple[int, int, int, int],
    thumbnail_dir: Path,
    target_person_id: str,
    min_face_size: int = MANUAL_FACE_MIN_SIZE,
) -> ManualFaceRecord:
    if not asset_id or not asset_rel or not target_person_id:
        raise ManualFaceValidationError("Manual face details are incomplete.")

    image = load_image_rgb(image_path)
    image_width, image_height = image.size
    x, y, width, height = [int(value) for value in requested_box]
    if (
        x < 0
        or y < 0
        or width <= 0
        or height <= 0
        or (x + width) > image_width
        or (y + height) > image_height
    ):
        raise ManualFaceValidationError("Please place the face circle fully inside the photo.")
    if width < int(min_face_size) or height < int(min_face_size):
        raise ManualFaceValidationError("The selected face is too small to save reliably.")

    face_id = uuid.uuid4().hex
    thumbnail_path = thumbnail_dir / f"{face_id}.png"
    save_face_thumbnail(image, (x, y, width, height), thumbnail_path)
    return ManualFaceRecord(
        face_id=face_id,
        asset_id=asset_id,
        asset_rel=asset_rel,
        box_x=x,
        box_y=y,
        box_w=width,
        box_h=height,
        thumbnail_path=thumbnail_path.relative_to(thumbnail_dir.parent).as_posix(),
        person_id=target_person_id,
        created_at=_utc_now_iso(),
        image_width=image_width,
        image_height=image_height,
    )


__all__ = [
    "MANUAL_FACE_MIN_SIZE",
    "ManualFaceValidationError",
    "build_manual_face_record",
]

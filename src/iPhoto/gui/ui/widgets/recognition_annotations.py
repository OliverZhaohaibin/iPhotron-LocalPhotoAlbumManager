"""Shared annotation adapters for person faces and pet detections."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from iPhoto.pets.records import AssetPetAnnotation


@dataclass(frozen=True)
class RecognitionIdentitySuggestion:
    identity_key: str
    name: str
    thumbnail_path: Path | None
    count: int = 0

    @property
    def person_id(self) -> str:
        return self.identity_key

    @property
    def face_count(self) -> int:
        return self.count


@dataclass(frozen=True)
class RecognitionAnnotation:
    kind: str
    annotation_id: str
    entity_id: str | None
    display_name: str | None
    box_x: int
    box_y: int
    box_w: int
    box_h: int
    image_width: int
    image_height: int
    thumbnail_path: Path | None = None
    is_manual: bool = False
    canonical_display_name: str | None = None
    is_stale: bool = False
    stale_reason: str | None = None

    @property
    def face_id(self) -> str:
        return f"{self.kind}:{self.annotation_id}"

    @property
    def detection_id(self) -> str:
        return self.annotation_id

    @property
    def person_id(self) -> str | None:
        if not self.entity_id:
            return None
        return f"{self.kind}:{self.entity_id}"


def pet_annotation_adapter(annotation: AssetPetAnnotation) -> RecognitionAnnotation:
    display_name = annotation.canonical_display_name or annotation.display_name
    return RecognitionAnnotation(
        kind="pet",
        annotation_id=annotation.detection_id,
        entity_id=annotation.pet_id,
        display_name=display_name,
        box_x=annotation.box_x,
        box_y=annotation.box_y,
        box_w=annotation.box_w,
        box_h=annotation.box_h,
        image_width=annotation.image_width,
        image_height=annotation.image_height,
        thumbnail_path=annotation.thumbnail_path,
        canonical_display_name=display_name,
        is_stale=annotation.is_stale,
        stale_reason=annotation.stale_reason,
    )

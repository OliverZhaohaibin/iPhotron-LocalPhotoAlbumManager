"""Shared annotation adapters for person faces and pet detections."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from iPhoto.people.records import AssetFaceAnnotation
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
    source_detection_kind: str
    source_annotation_id: str
    source_identity_kind: str
    source_identity_id: str | None
    canonical_identity_kind: str
    canonical_identity_id: str | None
    canonical_display_name: str | None
    box_x: int
    box_y: int
    box_w: int
    box_h: int
    image_width: int
    image_height: int
    thumbnail_path: Path | None = None
    is_manual: bool = False
    is_stale: bool = False
    stale_reason: str | None = None

    @property
    def kind(self) -> str:
        """Compatibility alias; mutation routing must always use the source kind."""

        return self.source_detection_kind

    @property
    def annotation_id(self) -> str:
        return self.source_annotation_id

    @property
    def entity_id(self) -> str | None:
        return self.canonical_identity_id

    @property
    def display_name(self) -> str | None:
        return self.canonical_display_name

    @property
    def face_id(self) -> str:
        return f"{self.source_detection_kind}:{self.source_annotation_id}"

    @property
    def detection_id(self) -> str:
        return self.source_annotation_id

    @property
    def person_id(self) -> str | None:
        if not self.canonical_identity_id:
            return None
        return f"{self.canonical_identity_kind}:{self.canonical_identity_id}"


def face_annotation_adapter(annotation: AssetFaceAnnotation) -> RecognitionAnnotation:
    return RecognitionAnnotation(
        source_detection_kind="person",
        source_annotation_id=annotation.face_id,
        source_identity_kind=annotation.source_identity_kind or "person",
        source_identity_id=annotation.source_identity_id or annotation.person_id,
        canonical_identity_kind=annotation.canonical_identity_kind or "person",
        canonical_identity_id=annotation.canonical_identity_id or annotation.person_id,
        canonical_display_name=(
            annotation.canonical_display_name or annotation.display_name
        ),
        box_x=annotation.box_x,
        box_y=annotation.box_y,
        box_w=annotation.box_w,
        box_h=annotation.box_h,
        image_width=annotation.image_width,
        image_height=annotation.image_height,
        thumbnail_path=annotation.thumbnail_path,
        is_manual=annotation.is_manual,
    )


def pet_annotation_adapter(annotation: AssetPetAnnotation) -> RecognitionAnnotation:
    display_name = annotation.canonical_display_name or annotation.display_name
    return RecognitionAnnotation(
        source_detection_kind="pet",
        source_annotation_id=annotation.detection_id,
        source_identity_kind="pet",
        source_identity_id=annotation.source_identity_id or annotation.pet_id,
        canonical_identity_kind=annotation.canonical_identity_kind or "pet",
        canonical_identity_id=annotation.canonical_identity_id or annotation.pet_id,
        canonical_display_name=display_name,
        box_x=annotation.box_x,
        box_y=annotation.box_y,
        box_w=annotation.box_w,
        box_h=annotation.box_h,
        image_width=annotation.image_width,
        image_height=annotation.image_height,
        thumbnail_path=annotation.thumbnail_path,
        is_stale=annotation.is_stale,
        stale_reason=annotation.stale_reason,
    )

"""Shared annotation adapters for person faces and pet detections."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from iPhoto.pets.records import AssetPetAnnotation


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
    species_label: str | None = None
    is_manual: bool = False

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
    display_name = annotation.display_name
    if not (isinstance(display_name, str) and display_name.strip()):
        display_name = annotation.species_label.title() if annotation.species_label else None
    return RecognitionAnnotation(
        kind="pet",
        annotation_id=annotation.detection_id,
        entity_id=annotation.pet_id,
        display_name=display_name,
        species_label=annotation.species_label,
        box_x=annotation.box_x,
        box_y=annotation.box_y,
        box_w=annotation.box_w,
        box_h=annotation.box_h,
        image_width=annotation.image_width,
        image_height=annotation.image_height,
        thumbnail_path=annotation.thumbnail_path,
    )

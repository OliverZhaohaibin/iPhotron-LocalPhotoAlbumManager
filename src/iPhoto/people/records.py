"""Data records for the People feature repositories."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class FaceRecord:
    face_id: str
    face_key: str
    asset_id: str
    asset_rel: str
    box_x: int
    box_y: int
    box_w: int
    box_h: int
    confidence: float
    embedding: np.ndarray
    embedding_dim: int
    thumbnail_path: str | None
    person_id: str | None
    detected_at: str
    image_width: int
    image_height: int
    is_manual: bool = False


@dataclass(frozen=True)
class ManualFaceRecord:
    face_id: str
    asset_id: str
    asset_rel: str
    box_x: int
    box_y: int
    box_w: int
    box_h: int
    thumbnail_path: str | None
    person_id: str
    created_at: str
    image_width: int
    image_height: int


@dataclass(frozen=True)
class PersonRecord:
    person_id: str
    name: str | None
    key_face_id: str
    face_count: int
    center_embedding: np.ndarray
    created_at: str
    updated_at: str
    sample_count: int = 0
    profile_state: str = "unstable"


@dataclass(frozen=True)
class PersonSummary:
    person_id: str
    name: str | None
    key_face_id: str
    face_count: int
    thumbnail_path: Path | None
    created_at: str
    is_hidden: bool = False


@dataclass(frozen=True)
class PeopleGroupRecord:
    group_id: str
    member_person_ids: tuple[str, ...]
    member_key: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class PeopleGroupSummary:
    group_id: str
    name: str
    member_person_ids: tuple[str, ...]
    members: tuple[PersonSummary, ...]
    asset_count: int
    cover_asset_path: Path | None
    created_at: str


@dataclass(frozen=True)
class AssetFaceAnnotation:
    face_id: str
    person_id: str | None
    display_name: str | None
    box_x: int
    box_y: int
    box_w: int
    box_h: int
    image_width: int
    image_height: int
    thumbnail_path: Path | None = None
    is_manual: bool = False


@dataclass(frozen=True)
class PersonProfile:
    person_id: str
    name: str | None
    center_embedding: np.ndarray
    embedding_dim: int
    created_at: str
    updated_at: str
    sample_count: int = 0
    profile_state: str = "unstable"

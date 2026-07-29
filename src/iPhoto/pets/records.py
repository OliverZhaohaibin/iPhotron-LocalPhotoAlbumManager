"""Data records for the Pets bounded context."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import numpy as np


class PetMutationFailure(StrEnum):
    REJECTED = "rejected"
    RECOVERY_PENDING = "recovery_pending"
    SHUTTING_DOWN = "shutting_down"


@dataclass(frozen=True, slots=True)
class PetMergeOutcome:
    merged: bool
    failure: PetMutationFailure | None = None
    operation_id: str | None = None

    def __bool__(self) -> bool:
        return self.merged


@dataclass(frozen=True)
class PetDetectionRecord:
    detection_id: str
    pet_key: str
    asset_id: str
    asset_rel: str
    box_x: int
    box_y: int
    box_w: int
    box_h: int
    confidence: float
    embedding: np.ndarray
    embedding_dim: int
    embedding_model: str
    detector_model: str
    thumbnail_path: str | None
    pet_id: str | None
    detected_at: str
    image_width: int
    image_height: int
    species_label: str | None = None
    quality_score: float | None = None
    pet_key_version: str = "v2"
    embedding_pipeline_version: str = "dinov2-vits14-imagenet-normalized-v1"
    generation_id: int = 0
    is_stale: bool = False
    stale_reason: str | None = None
    source_generation_id: int | None = None


@dataclass(frozen=True)
class PetRecord:
    pet_id: str
    name: str | None
    key_detection_id: str
    detection_count: int
    center_embedding: np.ndarray
    embedding_dim: int
    created_at: str
    updated_at: str
    sample_count: int = 0
    profile_state: str = "unstable"
    species_label: str | None = None
    embedding_pipeline_version: str = "dinov2-vits14-imagenet-normalized-v1"
    generation_id: int = 0
    boundary_embeddings: tuple[np.ndarray, ...] = ()


@dataclass(frozen=True)
class PetProfile:
    pet_id: str
    name: str | None
    center_embedding: np.ndarray
    embedding_dim: int
    created_at: str
    updated_at: str
    sample_count: int = 0
    profile_state: str = "unstable"
    species_label: str | None = None
    embedding_pipeline_version: str = "dinov2-vits14-imagenet-normalized-v1"
    generation_id: int = 0
    boundary_embeddings: tuple[np.ndarray, ...] = ()


@dataclass(frozen=True)
class PetSummary:
    pet_id: str
    name: str | None
    key_detection_id: str
    detection_count: int
    thumbnail_path: Path | None
    created_at: str
    is_hidden: bool = False
    asset_count: int = 0
    profile_state: str = "unstable"
    species_label: str | None = None


@dataclass(frozen=True)
class AssetPetAnnotation:
    detection_id: str
    pet_id: str | None
    display_name: str | None
    box_x: int
    box_y: int
    box_w: int
    box_h: int
    image_width: int
    image_height: int
    thumbnail_path: Path | None = None
    source_identity_kind: str = "pet"
    source_identity_id: str | None = None
    canonical_identity_kind: str = "pet"
    canonical_identity_id: str | None = None
    canonical_display_name: str | None = None
    is_stale: bool = False
    stale_reason: str | None = None
    source_generation_id: int | None = None

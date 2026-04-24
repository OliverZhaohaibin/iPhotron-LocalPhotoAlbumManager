"""Compatibility exports for People repository types and implementations."""

from __future__ import annotations

from .face_repository import FaceRepository
from .records import (
    AssetFaceAnnotation,
    FaceRecord,
    ManualFaceRecord,
    PeopleGroupRecord,
    PeopleGroupSummary,
    PersonProfile,
    PersonRecord,
    PersonSummary,
)
from .repository_utils import compute_cluster_center, normalize_vector
from .state_repository import FaceStateRepository

__all__ = [
    "AssetFaceAnnotation",
    "FaceRecord",
    "ManualFaceRecord",
    "FaceRepository",
    "FaceStateRepository",
    "PeopleGroupRecord",
    "PeopleGroupSummary",
    "PersonProfile",
    "PersonRecord",
    "PersonSummary",
    "compute_cluster_center",
    "normalize_vector",
]

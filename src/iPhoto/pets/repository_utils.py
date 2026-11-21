"""Shared helpers for Pets repositories and clustering."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime

import numpy as np

from .records import PetDetectionRecord

STABLE_PET_PROFILE_MIN_SAMPLES = 2


def compute_cluster_center(embeddings: np.ndarray) -> np.ndarray:
    if embeddings.size == 0:
        return np.empty((0,), dtype=np.float32)
    center = embeddings.mean(axis=0).astype(np.float32)
    return normalize_vector(center)


def normalize_vector(vector: np.ndarray) -> np.ndarray:
    vector = np.asarray(vector, dtype=np.float32).flatten()
    if vector.size == 0:
        return vector
    norm = float(np.linalg.norm(vector))
    if norm <= 0.0:
        return vector
    return (vector / norm).astype(np.float32)


def cosine_distance(left: np.ndarray, right: np.ndarray) -> float:
    left_normalized = normalize_vector(left)
    right_normalized = normalize_vector(right)
    if left_normalized.size == 0 or right_normalized.size == 0:
        return float("inf")
    similarity = float(left_normalized @ right_normalized)
    return float(np.clip(1.0 - similarity, 0.0, 2.0))


def cosine_distance_matrix(embeddings: np.ndarray) -> np.ndarray:
    if embeddings.size == 0:
        return np.empty((0, 0), dtype=np.float32)
    normalized = np.stack([normalize_vector(vector) for vector in embeddings], axis=0)
    similarity = normalized @ normalized.T
    distance = 1.0 - similarity
    np.clip(distance, 0.0, 2.0, out=distance)
    return distance.astype(np.float32)


def key_detection_sort_key(detection: PetDetectionRecord) -> tuple[float, int]:
    return detection.confidence, detection.box_w * detection.box_h


def serialize_embedding(embedding: np.ndarray) -> sqlite3.Binary:
    vector = normalize_vector(embedding)
    return sqlite3.Binary(vector.astype(np.float32).tobytes())


def deserialize_embedding(blob: bytes | None, embedding_dim: int) -> np.ndarray:
    if not blob or embedding_dim <= 0:
        return np.empty((0,), dtype=np.float32)
    return np.frombuffer(blob, dtype=np.float32, count=embedding_dim).copy()


def normalize_name(name_or_none: str | None) -> str | None:
    if name_or_none is None:
        return None
    normalized = str(name_or_none).strip()
    return normalized or None


def unique_pet_ids(pet_ids: Iterable[str]) -> tuple[str, ...]:
    unique: list[str] = []
    seen: set[str] = set()
    for pet_id in pet_ids:
        normalized = str(pet_id).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return tuple(unique)


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def profile_state_for_sample_count(sample_count: int) -> str:
    return "stable" if int(sample_count) >= STABLE_PET_PROFILE_MIN_SAMPLES else "unstable"


def stable_id_for_payload(prefix: str, payload: str) -> str:
    digest = hashlib.sha1(payload.encode("utf-8"), usedforsecurity=False).hexdigest()
    return f"{prefix}-{digest}"

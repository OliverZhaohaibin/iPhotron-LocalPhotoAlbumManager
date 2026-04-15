"""Shared helpers for People repository implementations."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from typing import Iterable

import numpy as np

from iPhoto.people.records import FaceRecord


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


def _key_face_sort_key(face: FaceRecord) -> tuple[float, int]:
    return face.confidence, face.box_w * face.box_h


def _serialize_embedding(embedding: np.ndarray) -> sqlite3.Binary:
    vector = normalize_vector(embedding)
    return sqlite3.Binary(vector.astype(np.float32).tobytes())


def _deserialize_embedding(blob: bytes | None, embedding_dim: int) -> np.ndarray:
    if not blob or embedding_dim <= 0:
        return np.empty((0,), dtype=np.float32)
    return np.frombuffer(blob, dtype=np.float32, count=embedding_dim).copy()


def _normalize_name(name_or_none: str | None) -> str | None:
    if name_or_none is None:
        return None
    normalized = str(name_or_none).strip()
    return normalized or None


def _unique_person_ids(person_ids: Iterable[str]) -> tuple[str, ...]:
    unique: list[str] = []
    seen: set[str] = set()
    for person_id in person_ids:
        normalized = str(person_id).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return tuple(unique)


def _group_member_key(member_person_ids: Iterable[str]) -> str:
    return "\x1f".join(sorted(_unique_person_ids(member_person_ids)))


def _group_id_for_member_key(member_key: str) -> str:
    digest = hashlib.sha1(member_key.encode("utf-8")).hexdigest()
    return f"group-{digest}"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

"""Face detection and clustering helpers for the People feature."""

from __future__ import annotations

import os
import uuid
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
from pathlib import Path

import numpy as np

from .image_utils import load_image_rgb, pil_image_to_bgr, save_face_thumbnail
from .repository import (
    FaceRecord,
    FaceStateRepository,
    PersonProfile,
    PersonRecord,
    compute_cluster_center,
    normalize_vector,
)


@dataclass(frozen=True)
class DetectedAssetFaces:
    asset_id: str
    asset_rel: str
    faces: list[FaceRecord]
    error: str | None = None


class FaceClusterPipeline:
    def __init__(
        self,
        *,
        model_root: Path,
        model_pack: str = "buffalo_s",
        distance_threshold: float = 0.6,
        min_samples: int = 2,
        min_face_size: int = 40,
    ) -> None:
        self._model_root = Path(model_root)
        self._model_pack = model_pack
        self._distance_threshold = float(distance_threshold)
        self._min_samples = int(min_samples)
        self._min_face_size = int(min_face_size)
        self._analysis_app = None

    @property
    def distance_threshold(self) -> float:
        return self._distance_threshold

    @property
    def min_samples(self) -> int:
        return self._min_samples

    def detect_faces_for_rows(
        self,
        rows: list[dict],
        *,
        library_root: Path,
        thumbnail_dir: Path,
    ) -> list[DetectedAssetFaces]:
        if not rows:
            return []

        face_app = self._ensure_face_analysis()
        results: list[DetectedAssetFaces] = []
        for row in rows:
            asset_id = str(row.get("id") or "")
            asset_rel = Path(str(row.get("rel") or "")).as_posix()
            image_path = (library_root / asset_rel).resolve()
            try:
                image = load_image_rgb(image_path)
                image_bgr = pil_image_to_bgr(image)
                detected_faces = face_app.get(image_bgr)
            except Exception as exc:
                results.append(
                    DetectedAssetFaces(
                        asset_id=asset_id,
                        asset_rel=asset_rel,
                        faces=[],
                        error=str(exc),
                    )
                )
                continue

            image_width, image_height = image.size
            faces: list[FaceRecord] = []
            for detected in detected_faces:
                bbox = _normalize_bbox(
                    detected.bbox,
                    image_width=image_width,
                    image_height=image_height,
                )
                if bbox[2] < self._min_face_size or bbox[3] < self._min_face_size:
                    continue

                embedding = _extract_embedding(detected)
                if embedding is None:
                    continue

                face_id = uuid.uuid4().hex
                thumbnail_path = thumbnail_dir / f"{face_id}.png"
                save_face_thumbnail(image, bbox, thumbnail_path)
                faces.append(
                    FaceRecord(
                        face_id=face_id,
                        face_key=build_face_key(
                            asset_id=asset_id,
                            bbox=bbox,
                            image_width=image_width,
                            image_height=image_height,
                        ),
                        asset_id=asset_id,
                        asset_rel=asset_rel,
                        box_x=bbox[0],
                        box_y=bbox[1],
                        box_w=bbox[2],
                        box_h=bbox[3],
                        confidence=float(getattr(detected, "det_score", 0.0)),
                        embedding=embedding,
                        embedding_dim=int(embedding.shape[0]),
                        thumbnail_path=thumbnail_path.relative_to(thumbnail_dir.parent).as_posix(),
                        person_id=None,
                        detected_at=_utc_now_iso(),
                        image_width=image_width,
                        image_height=image_height,
                    )
                )

            results.append(
                DetectedAssetFaces(
                    asset_id=asset_id,
                    asset_rel=asset_rel,
                    faces=faces,
                )
            )
        return results

    def _ensure_face_analysis(self):
        if self._analysis_app is not None:
            return self._analysis_app

        try:
            from insightface.app import FaceAnalysis
        except ImportError as exc:
            raise RuntimeError(
                "Face scanning unavailable: install the optional AI dependencies and rescan."
            ) from exc

        self._model_root.mkdir(parents=True, exist_ok=True)
        insightface_root = self._model_root.parent.resolve()
        os.environ.setdefault("INSIGHTFACE_HOME", str(insightface_root))
        _patch_insightface_alignment_estimate()
        providers = _resolve_execution_providers()
        ctx_id = 0 if "CUDAExecutionProvider" in providers else -1
        app = FaceAnalysis(name=self._model_pack, root=str(insightface_root), providers=providers)
        app.prepare(ctx_id=ctx_id, det_size=(640, 640))
        self._analysis_app = app
        return app


def build_face_key(
    *,
    asset_id: str,
    bbox: tuple[int, int, int, int],
    image_width: int,
    image_height: int,
    quantization: int = 8,
) -> str:
    x, y, width, height = bbox
    center_x = x + width / 2.0
    center_y = y + height / 2.0
    quantized = (
        _quantize_value(center_x, quantization),
        _quantize_value(center_y, quantization),
        _quantize_value(width, quantization),
        _quantize_value(height, quantization),
    )
    payload = (
        f"{asset_id}|{image_width}x{image_height}|"
        f"{quantized[0]}|{quantized[1]}|{quantized[2]}|{quantized[3]}"
    )
    return hashlib.sha1(payload.encode("utf-8"), usedforsecurity=False).hexdigest()


def cluster_face_records(
    faces: list[FaceRecord],
    *,
    distance_threshold: float = 0.6,
    min_samples: int = 2,
) -> tuple[list[FaceRecord], list[PersonRecord]]:
    if not faces:
        return [], []

    embeddings = np.stack([face.embedding for face in faces], axis=0).astype(np.float32)
    labels = run_dbscan(
        embeddings,
        eps=distance_threshold,
        min_samples=min_samples,
    )

    grouped_indices: dict[str, list[int]] = defaultdict(list)
    for index, label in enumerate(labels.tolist()):
        if label == -1:
            grouped_indices[f"noise-{index}"].append(index)
        else:
            grouped_indices[f"cluster-{label}"].append(index)

    updated_faces = list(faces)
    persons: list[PersonRecord] = []
    for indices in grouped_indices.values():
        members = [faces[index] for index in indices]
        key_face = max(members, key=_key_face_sort_key)
        person_id = uuid.uuid4().hex
        center_embedding = compute_cluster_center(
            np.stack([member.embedding for member in members], axis=0)
        )
        timestamp = _utc_now_iso()
        persons.append(
            PersonRecord(
                person_id=person_id,
                name=None,
                key_face_id=key_face.face_id,
                face_count=len(members),
                center_embedding=center_embedding,
                created_at=timestamp,
                updated_at=timestamp,
            )
        )
        for index in indices:
            updated_faces[index] = replace(updated_faces[index], person_id=person_id)
    persons.sort(key=lambda person: (-person.face_count, person.created_at))
    return updated_faces, persons


def canonicalize_cluster_identities(
    faces: list[FaceRecord],
    persons: list[PersonRecord],
    state_repository: FaceStateRepository,
    *,
    distance_threshold: float,
) -> tuple[list[FaceRecord], list[PersonRecord]]:
    if not faces or not persons:
        return faces, persons

    profiles = {profile.person_id: profile for profile in state_repository.get_profiles()}
    face_key_map = state_repository.get_face_key_map(face.face_key for face in faces)

    faces_by_person_id: dict[str, list[FaceRecord]] = defaultdict(list)
    for face in faces:
        if face.person_id is not None:
            faces_by_person_id[face.person_id].append(face)

    canonical_members: dict[str, list[FaceRecord]] = defaultdict(list)
    canonical_names: dict[str, str | None] = {}
    canonical_created_at: dict[str, str] = {}

    for person in persons:
        members = faces_by_person_id.get(person.person_id, [])
        canonical_id = resolve_canonical_person_id(
            person,
            members,
            profiles=profiles,
            face_key_map=face_key_map,
            distance_threshold=distance_threshold,
        )
        profile = profiles.get(canonical_id)
        canonical_members[canonical_id].extend(members)
        canonical_names.setdefault(canonical_id, profile.name if profile is not None else None)
        canonical_created_at.setdefault(
            canonical_id,
            profile.created_at if profile is not None else person.created_at,
        )

    updated_faces = list(faces)
    faces_by_face_id = {face.face_id: index for index, face in enumerate(faces)}
    canonical_persons: list[PersonRecord] = []
    for canonical_id, members in canonical_members.items():
        if not members:
            continue
        key_face = max(members, key=_key_face_sort_key)
        center_embedding = compute_cluster_center(
            np.stack([member.embedding for member in members], axis=0)
        )
        profile = profiles.get(canonical_id)
        updated_at = _utc_now_iso()
        canonical_persons.append(
            PersonRecord(
                person_id=canonical_id,
                name=profile.name if profile is not None else canonical_names.get(canonical_id),
                key_face_id=key_face.face_id,
                face_count=len(members),
                center_embedding=center_embedding,
                created_at=profile.created_at if profile is not None else canonical_created_at[canonical_id],
                updated_at=updated_at,
            )
        )
        for member in members:
            updated_faces[faces_by_face_id[member.face_id]] = replace(member, person_id=canonical_id)

    canonical_persons.sort(key=lambda person: (-person.face_count, person.created_at))
    return updated_faces, canonical_persons


def resolve_canonical_person_id(
    person: PersonRecord,
    members: list[FaceRecord],
    *,
    profiles: dict[str, PersonProfile],
    face_key_map: dict[str, str],
    distance_threshold: float,
) -> str:
    vote_counter = Counter(
        face_key_map[member.face_key]
        for member in members
        if member.face_key in face_key_map
    )
    if vote_counter:
        return max(
            vote_counter.items(),
            key=lambda item: (
                item[1],
                profiles[item[0]].updated_at if item[0] in profiles else "",
                item[0],
            ),
        )[0]

    best_profile_id: str | None = None
    best_distance = float("inf")
    for profile in profiles.values():
        if profile.embedding_dim <= 0 or profile.center_embedding.size == 0:
            continue
        if profile.center_embedding.shape != person.center_embedding.shape:
            continue
        distance = cosine_distance(person.center_embedding, profile.center_embedding)
        if distance < best_distance:
            best_distance = distance
            best_profile_id = profile.person_id

    if best_profile_id is not None and best_distance <= distance_threshold:
        return best_profile_id

    return uuid.uuid4().hex


def run_dbscan(
    embeddings: np.ndarray,
    *,
    eps: float,
    min_samples: int,
) -> np.ndarray:
    if embeddings.size == 0:
        return np.empty((0,), dtype=np.int32)

    distance_matrix = cosine_distance_matrix(embeddings)
    neighbor_map = [
        np.flatnonzero(distance_matrix[index] <= eps).tolist()
        for index in range(distance_matrix.shape[0])
    ]

    unvisited = -99
    labels = np.full(distance_matrix.shape[0], unvisited, dtype=np.int32)
    cluster_id = 0
    for point_index in range(distance_matrix.shape[0]):
        if labels[point_index] != unvisited:
            continue

        neighbors = neighbor_map[point_index]
        if len(neighbors) < min_samples:
            labels[point_index] = -1
            continue

        labels[point_index] = cluster_id
        queue: deque[int] = deque(neighbors)
        queued = set(neighbors)
        while queue:
            neighbor_index = queue.popleft()
            queued.discard(neighbor_index)
            if labels[neighbor_index] == -1:
                labels[neighbor_index] = cluster_id
            if labels[neighbor_index] != unvisited:
                continue

            labels[neighbor_index] = cluster_id
            neighbor_neighbors = neighbor_map[neighbor_index]
            if len(neighbor_neighbors) < min_samples:
                continue
            for candidate in neighbor_neighbors:
                if labels[candidate] == unvisited and candidate not in queued:
                    queue.append(candidate)
                    queued.add(candidate)
                elif labels[candidate] == -1:
                    labels[candidate] = cluster_id

        cluster_id += 1

    labels[labels == unvisited] = -1
    return labels


def cosine_distance_matrix(embeddings: np.ndarray) -> np.ndarray:
    normalized = np.stack([normalize_vector(vector) for vector in embeddings], axis=0)
    similarity = normalized @ normalized.T
    distance = 1.0 - similarity
    np.clip(distance, 0.0, 2.0, out=distance)
    return distance.astype(np.float32)


def cosine_distance(left: np.ndarray, right: np.ndarray) -> float:
    left_normalized = normalize_vector(left)
    right_normalized = normalize_vector(right)
    if left_normalized.size == 0 or right_normalized.size == 0:
        return float("inf")
    similarity = float(left_normalized @ right_normalized)
    return float(np.clip(1.0 - similarity, 0.0, 2.0))


def _normalize_bbox(
    raw_bbox,
    *,
    image_width: int,
    image_height: int,
) -> tuple[int, int, int, int]:
    box = np.asarray(raw_bbox, dtype=np.float32).flatten().tolist()
    x1, y1, x2, y2 = [int(round(value)) for value in box[:4]]
    x1 = max(0, min(x1, image_width - 1))
    y1 = max(0, min(y1, image_height - 1))
    x2 = max(x1 + 1, min(x2, image_width))
    y2 = max(y1 + 1, min(y2, image_height))
    return x1, y1, x2 - x1, y2 - y1


def _extract_embedding(face) -> np.ndarray | None:
    embedding = getattr(face, "embedding", None)
    if embedding is None:
        return None
    return normalize_vector(np.asarray(embedding, dtype=np.float32).flatten())


def _key_face_sort_key(face: FaceRecord) -> tuple[float, int]:
    return face.confidence, face.box_w * face.box_h


def _quantize_value(value: float, step: int) -> int:
    return int(round(float(value) / float(step)) * step)


def _resolve_execution_providers() -> list[str]:
    try:
        import onnxruntime as ort
    except ImportError:
        return ["CPUExecutionProvider"]

    available = ort.get_available_providers()
    providers: list[str] = []
    if "CUDAExecutionProvider" in available:
        providers.append("CUDAExecutionProvider")
    if "CPUExecutionProvider" in available:
        providers.append("CPUExecutionProvider")
    return providers or ["CPUExecutionProvider"]


def _patch_insightface_alignment_estimate() -> None:
    try:
        from insightface.utils import face_align
        from skimage import transform as trans
    except ImportError:
        return

    if getattr(face_align, "_iphoto_from_estimate_patch", False):
        return

    similarity_transform_cls = getattr(trans, "SimilarityTransform", None)
    from_estimate = getattr(similarity_transform_cls, "from_estimate", None)
    if from_estimate is None:
        return

    def estimate_norm(lmk, image_size=112, mode="arcface"):
        del mode
        assert lmk.shape == (5, 2)
        assert image_size % 112 == 0 or image_size % 128 == 0
        if image_size % 112 == 0:
            ratio = float(image_size) / 112.0
            diff_x = 0.0
        else:
            ratio = float(image_size) / 128.0
            diff_x = 8.0 * ratio

        dst = face_align.arcface_dst * ratio
        dst[:, 0] += diff_x
        tform = similarity_transform_cls.from_estimate(lmk, dst)
        return tform.params[0:2, :]

    face_align.estimate_norm = estimate_norm
    face_align._iphoto_from_estimate_patch = True


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

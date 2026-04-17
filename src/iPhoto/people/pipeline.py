"""Face detection and clustering helpers for the People feature."""

from __future__ import annotations

import logging
import os
import uuid
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Sequence

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
from .repository_utils import profile_state_for_sample_count

_LOGGER = logging.getLogger(__name__)
_MANUAL_FACE_MIN_OVERLAP = 0.12
_MANUAL_FACE_MIN_FACE_COVERAGE = 0.6
_MANUAL_FACE_DUPLICATE_OVERLAP = 0.8
_MANUAL_FACE_DUPLICATE_MIN_OVERLAP = 0.2
_MANUAL_FACE_DUPLICATE_CENTER_RATIO = 0.28


class ManualFaceValidationError(ValueError):
    """Raised when a manual face selection cannot be validated safely."""


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

    def build_manual_face_record(
        self,
        *,
        asset_id: str,
        asset_rel: str,
        image_path: Path,
        requested_box: tuple[int, int, int, int],
        thumbnail_dir: Path,
        target_person_id: str,
        existing_faces: Sequence[FaceRecord] = (),
    ) -> FaceRecord:
        if not asset_id or not asset_rel or not target_person_id:
            raise ManualFaceValidationError("Manual face details are incomplete.")
        face_app = self._ensure_face_analysis()
        image = load_image_rgb(image_path)
        image_width, image_height = image.size
        x, y, width, height = [int(value) for value in requested_box]
        _LOGGER.info(
            "Manual face validation started for asset %s with requested_box=%s image_size=%sx%s existing_faces=%d",
            asset_id,
            requested_box,
            image_width,
            image_height,
            len(existing_faces),
        )
        if (
            x < 0
            or y < 0
            or width <= 0
            or height <= 0
            or (x + width) > image_width
            or (y + height) > image_height
        ):
            raise ManualFaceValidationError("Please place the face circle fully inside the photo.")
        if width < self._min_face_size or height < self._min_face_size:
            raise ManualFaceValidationError("The selected face is too small to save reliably.")

        image_bgr = pil_image_to_bgr(image)
        detected_faces = list(face_app.get(image_bgr))
        candidates = []
        candidate_debug_lines: list[str] = []
        self._collect_manual_face_candidates(
            candidates,
            candidate_debug_lines,
            detections=detected_faces,
            requested_box=(x, y, width, height),
            image_width=image_width,
            image_height=image_height,
            source="global",
        )
        crop_detections: list[object] = []
        crop_image_fn = getattr(image, "crop", None)
        if callable(crop_image_fn):
            crop_image = crop_image_fn((x, y, x + width, y + height))
            crop_bgr = pil_image_to_bgr(crop_image)
            crop_detections = list(face_app.get(crop_bgr))
            self._collect_manual_face_candidates(
                candidates,
                candidate_debug_lines,
                detections=crop_detections,
                requested_box=(x, y, width, height),
                image_width=image_width,
                image_height=image_height,
                source="crop",
                source_offset=(x, y),
                source_size=(width, height),
            )
        if candidate_debug_lines:
            _LOGGER.info(
                "Manual face validation candidates for asset %s: %s",
                asset_id,
                " | ".join(candidate_debug_lines),
            )
        if not candidates:
            _LOGGER.warning(
                "Manual face validation rejected for asset %s: no candidates overlapped requested_box=%s total_detections=%d",
                asset_id,
                requested_box,
                len(detected_faces) + len(crop_detections),
            )
            raise ManualFaceValidationError(
                "No face was detected inside the selected circle."
            )

        (
            _score,
            best_face_coverage,
            best_overlap,
            _neg_distance,
            best_confidence,
            bbox,
            embedding,
            best_source,
        ) = max(candidates)
        if (
            best_overlap < _MANUAL_FACE_MIN_OVERLAP
            and best_face_coverage < _MANUAL_FACE_MIN_FACE_COVERAGE
        ):
            _LOGGER.warning(
                "Manual face validation rejected for asset %s: source=%s best_bbox=%s conf=%.3f overlap=%.3f coverage=%.3f required_overlap=%.3f required_coverage=%.3f requested_box=%s",
                asset_id,
                best_source,
                bbox,
                best_confidence,
                best_overlap,
                best_face_coverage,
                _MANUAL_FACE_MIN_OVERLAP,
                _MANUAL_FACE_MIN_FACE_COVERAGE,
                requested_box,
            )
            raise ManualFaceValidationError(
                "Please place the circle closer to the face before saving."
            )

        duplicate_match = self._find_duplicate_manual_face(existing_faces, bbox)
        if duplicate_match is not None:
            duplicate_overlap, duplicate_center_ratio, duplicate_bbox = duplicate_match
            _LOGGER.warning(
                "Manual face validation rejected for asset %s: duplicate overlap %.3f center_ratio %.3f for bbox=%s matched_existing_bbox=%s",
                asset_id,
                duplicate_overlap,
                duplicate_center_ratio,
                bbox,
                duplicate_bbox,
            )
            raise ManualFaceValidationError(
                "This face is already tagged nearby."
            )

        face_id = uuid.uuid4().hex
        thumbnail_path = thumbnail_dir / f"{face_id}.png"
        save_face_thumbnail(image, bbox, thumbnail_path)
        _LOGGER.info(
            "Manual face validation accepted for asset %s: face_id=%s source=%s bbox=%s conf=%.3f overlap=%.3f coverage=%.3f",
            asset_id,
            face_id,
            best_source,
            bbox,
            best_confidence,
            best_overlap,
            best_face_coverage,
        )
        return FaceRecord(
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
            confidence=best_confidence,
            embedding=embedding,
            embedding_dim=int(embedding.shape[0]),
            thumbnail_path=thumbnail_path.relative_to(thumbnail_dir.parent).as_posix(),
            person_id=target_person_id,
            detected_at=_utc_now_iso(),
            image_width=image_width,
            image_height=image_height,
            is_manual=True,
        )

    def _collect_manual_face_candidates(
        self,
        candidates: list[tuple[float, float, float, float, float, tuple[int, int, int, int], np.ndarray, str]],
        candidate_debug_lines: list[str],
        *,
        detections: Sequence[object],
        requested_box: tuple[int, int, int, int],
        image_width: int,
        image_height: int,
        source: str,
        source_offset: tuple[int, int] = (0, 0),
        source_size: tuple[int, int] | None = None,
    ) -> None:
        source_width = source_size[0] if source_size is not None else image_width
        source_height = source_size[1] if source_size is not None else image_height
        offset_x, offset_y = source_offset
        for index, detected in enumerate(detections, start=1):
            local_bbox = _normalize_bbox(
                detected.bbox,
                image_width=source_width,
                image_height=source_height,
            )
            bbox = (
                local_bbox[0] + offset_x,
                local_bbox[1] + offset_y,
                local_bbox[2],
                local_bbox[3],
            )
            if bbox[2] < self._min_face_size or bbox[3] < self._min_face_size:
                candidate_debug_lines.append(
                    f"{source}#{index} bbox={bbox} skipped=min-size"
                )
                continue
            embedding = _extract_embedding(detected)
            if embedding is None:
                candidate_debug_lines.append(
                    f"{source}#{index} bbox={bbox} skipped=no-embedding"
                )
                continue
            overlap = _bbox_iou(requested_box, bbox)
            face_coverage = _bbox_overlap_ratio(bbox, requested_box)
            center_distance = _bbox_center_distance(requested_box, bbox)
            confidence = float(getattr(detected, "det_score", 0.0))
            candidate_debug_lines.append(
                "{source}#{index} bbox={bbox} conf={confidence:.3f} overlap={overlap:.3f} "
                "coverage={face_coverage:.3f} center_dist={center_distance:.1f}".format(
                    source=source,
                    index=index,
                    bbox=bbox,
                    confidence=confidence,
                    overlap=overlap,
                    face_coverage=face_coverage,
                    center_distance=center_distance,
                )
            )
            if overlap <= 0.0 and face_coverage <= 0.0:
                continue
            candidates.append(
                (
                    max(face_coverage, overlap),
                    face_coverage,
                    overlap,
                    -center_distance,
                    confidence,
                    bbox,
                    embedding,
                    source,
                )
            )

    def _find_duplicate_manual_face(
        self,
        existing_faces: Sequence[FaceRecord],
        candidate_bbox: tuple[int, int, int, int],
    ) -> tuple[float, float, tuple[int, int, int, int]] | None:
        best_match: tuple[float, float, tuple[int, int, int, int]] | None = None
        for face in existing_faces:
            existing_bbox = (face.box_x, face.box_y, face.box_w, face.box_h)
            overlap = _bbox_iou(existing_bbox, candidate_bbox)
            center_ratio = _bbox_center_distance_ratio(existing_bbox, candidate_bbox)
            candidate_center = _bbox_center(candidate_bbox)
            existing_center = _bbox_center(existing_bbox)
            same_face = (
                overlap >= _MANUAL_FACE_DUPLICATE_OVERLAP
                or (
                    overlap >= _MANUAL_FACE_DUPLICATE_MIN_OVERLAP
                    and center_ratio <= _MANUAL_FACE_DUPLICATE_CENTER_RATIO
                )
                or (
                    _bbox_contains_point(existing_bbox, candidate_center)
                    and _bbox_contains_point(candidate_bbox, existing_center)
                )
            )
            if not same_face:
                continue
            match = (overlap, center_ratio, existing_bbox)
            if best_match is None or match > best_match:
                best_match = match
        return best_match

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
        # Keep downloaded models in the shared extension cache instead of
        # library-specific folders so they are reused across rescans.
        os.environ["INSIGHTFACE_HOME"] = str(insightface_root)
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
                sample_count=len(members),
                profile_state=profile_state_for_sample_count(len(members)),
            )
        )
        for index in indices:
            updated_faces[index] = replace(updated_faces[index], person_id=person_id)
    persons.sort(key=lambda person: (-person.face_count, person.created_at))
    return updated_faces, persons


def build_person_records_from_faces(
    faces: Sequence[FaceRecord],
    *,
    names_by_person_id: dict[str, str | None] | None = None,
    created_at_by_person_id: dict[str, str] | None = None,
) -> list[PersonRecord]:
    if not faces:
        return []

    grouped: dict[str, list[FaceRecord]] = defaultdict(list)
    for face in faces:
        if face.person_id:
            grouped[str(face.person_id)].append(face)

    if not grouped:
        return []

    resolved_names = dict(names_by_person_id or {})
    resolved_created_at = dict(created_at_by_person_id or {})
    updated_at = _utc_now_iso()
    persons: list[PersonRecord] = []
    for person_id, members in grouped.items():
        key_face = max(members, key=_key_face_sort_key)
        center_embedding = compute_cluster_center(
            np.stack([member.embedding for member in members], axis=0)
        )
        sample_count = len(members)
        persons.append(
            PersonRecord(
                person_id=person_id,
                name=resolved_names.get(person_id),
                key_face_id=key_face.face_id,
                face_count=sample_count,
                center_embedding=center_embedding,
                created_at=resolved_created_at.get(
                    person_id,
                    min((member.detected_at for member in members), default=updated_at),
                ),
                updated_at=updated_at,
                sample_count=sample_count,
                profile_state=profile_state_for_sample_count(sample_count),
            )
        )
    persons.sort(key=lambda person: (-person.face_count, person.created_at))
    return persons


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
    for canonical_id, members in canonical_members.items():
        if not members:
            continue
        for member in members:
            updated_faces[faces_by_face_id[member.face_id]] = replace(member, person_id=canonical_id)
    canonical_persons = build_person_records_from_faces(
        updated_faces,
        names_by_person_id=canonical_names,
        created_at_by_person_id=canonical_created_at,
    )
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
        if str(profile.profile_state or "unstable") != "stable":
            continue
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


def _bbox_iou(
    left: tuple[int, int, int, int],
    right: tuple[int, int, int, int],
) -> float:
    inter_area = _bbox_intersection_area(left, right)
    if inter_area <= 0:
        return 0.0
    union = (left[2] * left[3]) + (right[2] * right[3]) - inter_area
    if union <= 0:
        return 0.0
    return float(inter_area / union)


def _bbox_overlap_ratio(
    subject: tuple[int, int, int, int],
    covering: tuple[int, int, int, int],
) -> float:
    inter_area = _bbox_intersection_area(subject, covering)
    if inter_area <= 0:
        return 0.0
    subject_area = subject[2] * subject[3]
    if subject_area <= 0:
        return 0.0
    return float(inter_area / subject_area)


def _bbox_intersection_area(
    left: tuple[int, int, int, int],
    right: tuple[int, int, int, int],
) -> int:
    left_x1, left_y1, left_w, left_h = left
    right_x1, right_y1, right_w, right_h = right
    left_x2 = left_x1 + left_w
    left_y2 = left_y1 + left_h
    right_x2 = right_x1 + right_w
    right_y2 = right_y1 + right_h
    inter_left = max(left_x1, right_x1)
    inter_top = max(left_y1, right_y1)
    inter_right = min(left_x2, right_x2)
    inter_bottom = min(left_y2, right_y2)
    inter_w = max(0, inter_right - inter_left)
    inter_h = max(0, inter_bottom - inter_top)
    return int(inter_w * inter_h)


def _bbox_center_distance(
    left: tuple[int, int, int, int],
    right: tuple[int, int, int, int],
) -> float:
    left_cx, left_cy = _bbox_center(left)
    right_cx, right_cy = _bbox_center(right)
    return float(np.hypot(left_cx - right_cx, left_cy - right_cy))


def _bbox_center_distance_ratio(
    left: tuple[int, int, int, int],
    right: tuple[int, int, int, int],
) -> float:
    distance = _bbox_center_distance(left, right)
    normalizer = min(_bbox_diagonal(left), _bbox_diagonal(right))
    if normalizer <= 0.0:
        return float("inf")
    return float(distance / normalizer)


def _bbox_center(bbox: tuple[int, int, int, int]) -> tuple[float, float]:
    return (
        float(bbox[0]) + (float(bbox[2]) / 2.0),
        float(bbox[1]) + (float(bbox[3]) / 2.0),
    )


def _bbox_contains_point(
    bbox: tuple[int, int, int, int],
    point: tuple[float, float],
) -> bool:
    left, top, width, height = bbox
    right = left + width
    bottom = top + height
    return left <= point[0] <= right and top <= point[1] <= bottom


def _bbox_diagonal(bbox: tuple[int, int, int, int]) -> float:
    return float(np.hypot(float(bbox[2]), float(bbox[3])))


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

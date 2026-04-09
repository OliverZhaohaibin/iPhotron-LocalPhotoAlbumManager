from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
import uuid

import numpy as np

from db import FaceRecord, PersonRecord
from image_utils import load_image_rgb, pil_image_to_bgr, save_face_thumbnail


SUPPORTED_IMAGE_SUFFIXES = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".bmp",
    ".heic",
    ".heif",
}


@dataclass(frozen=True)
class PipelineResult:
    faces: list[FaceRecord]
    persons: list[PersonRecord]
    image_count: int
    warning_messages: list[str]

    @property
    def face_count(self) -> int:
        return len(self.faces)

    @property
    def cluster_count(self) -> int:
        return len(self.persons)


class FaceClusterPipeline:
    def __init__(
        self,
        *,
        model_pack: str = "buffalo_s",
        distance_threshold: float = 0.6,
        min_samples: int = 2,
        min_face_size: int = 40,
    ) -> None:
        self._model_pack = model_pack
        self._distance_threshold = float(distance_threshold)
        self._min_samples = int(min_samples)
        self._min_face_size = int(min_face_size)
        self._analysis_app = None

    def scan_folder(
        self,
        folder: Path,
        workspace_root: Path,
        thumbnail_dir: Path,
        *,
        progress_callback=None,
        status_callback=None,
    ) -> PipelineResult:
        image_paths = collect_image_files(folder)
        warning_messages: list[str] = []
        faces: list[FaceRecord] = []

        if not image_paths:
            return PipelineResult(
                faces=[],
                persons=[],
                image_count=0,
                warning_messages=[],
            )

        face_app = self._ensure_face_analysis()
        total = len(image_paths)
        for index, image_path in enumerate(image_paths, start=1):
            if status_callback is not None:
                status_callback(f"正在扫描 {image_path.name} ({index}/{total})")
            try:
                image = load_image_rgb(image_path)
                image_bgr = pil_image_to_bgr(image)
                detected_faces = face_app.get(image_bgr)
            except Exception as exc:
                warning_messages.append(f"跳过 {image_path.name}: {exc}")
                if progress_callback is not None:
                    progress_callback(index, total)
                continue

            image_width, image_height = image.size
            asset_rel = image_path.relative_to(folder).as_posix()
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
                    warning_messages.append(
                        f"{image_path.name} 中有一张人脸缺少 embedding，已跳过。"
                    )
                    continue

                face_id = uuid.uuid4().hex
                thumbnail_path = thumbnail_dir / f"{face_id}.png"
                save_face_thumbnail(image, bbox, thumbnail_path)
                faces.append(
                    FaceRecord(
                        face_id=face_id,
                        asset_rel=asset_rel,
                        box_x=bbox[0],
                        box_y=bbox[1],
                        box_w=bbox[2],
                        box_h=bbox[3],
                        confidence=float(getattr(detected, "det_score", 0.0)),
                        embedding=embedding,
                        embedding_dim=int(embedding.shape[0]),
                        thumbnail_path=thumbnail_path.relative_to(workspace_root).as_posix(),
                        person_id=None,
                        detected_at=_utc_now_iso(),
                        image_width=image_width,
                        image_height=image_height,
                    )
                )

            if progress_callback is not None:
                progress_callback(index, total)

        clustered_faces, persons = cluster_face_records(
            faces,
            distance_threshold=self._distance_threshold,
            min_samples=self._min_samples,
        )
        return PipelineResult(
            faces=clustered_faces,
            persons=persons,
            image_count=total,
            warning_messages=warning_messages,
        )

    def _ensure_face_analysis(self):
        if self._analysis_app is not None:
            return self._analysis_app

        try:
            from insightface.app import FaceAnalysis
        except ImportError as exc:
            raise RuntimeError(
                "缺少 insightface 依赖。请先安装 demo 需要的 AI 依赖后再运行。"
            ) from exc

        _patch_insightface_alignment_estimate()
        providers = _resolve_execution_providers()
        ctx_id = 0 if "CUDAExecutionProvider" in providers else -1
        app = FaceAnalysis(name=self._model_pack, providers=providers)
        app.prepare(ctx_id=ctx_id, det_size=(640, 640))
        self._analysis_app = app
        return app


def collect_image_files(folder: Path) -> list[Path]:
    return sorted(
        path
        for path in folder.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES
    )


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
                key_face_id=key_face.face_id,
                face_count=len(members),
                center_embedding=center_embedding,
                created_at=timestamp,
                updated_at=timestamp,
            )
        )
        for index in indices:
            updated_faces[index] = replace(updated_faces[index], person_id=person_id)
    persons.sort(key=lambda person: person.face_count, reverse=True)
    return updated_faces, persons


def compute_cluster_center(embeddings: np.ndarray) -> np.ndarray:
    center = embeddings.mean(axis=0).astype(np.float32)
    return _normalize_vector(center)


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
    normalized = np.stack([_normalize_vector(vector) for vector in embeddings], axis=0)
    similarity = normalized @ normalized.T
    distance = 1.0 - similarity
    np.clip(distance, 0.0, 2.0, out=distance)
    return distance.astype(np.float32)


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
    return _normalize_vector(np.asarray(embedding, dtype=np.float32).flatten())


def _normalize_vector(vector: np.ndarray) -> np.ndarray:
    vector = np.asarray(vector, dtype=np.float32).flatten()
    norm = float(np.linalg.norm(vector))
    if norm <= 0.0:
        return vector
    return (vector / norm).astype(np.float32)


def _key_face_sort_key(face: FaceRecord) -> tuple[float, int]:
    return face.confidence, face.box_w * face.box_h


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

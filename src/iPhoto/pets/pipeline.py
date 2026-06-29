"""Pet detection, embedding, clustering, and identity helpers."""

from __future__ import annotations

import hashlib
import logging
import os
import ssl
import tempfile
import uuid
from collections import Counter, defaultdict, deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from urllib import request

import numpy as np

from .image_utils import (
    PetImageLoadError,
    crop_pet_region,
    image_to_chw_float,
    load_image_rgb,
    save_pet_thumbnail,
)
from .records import PetDetectionRecord, PetProfile, PetRecord
from .repository_utils import (
    compute_cluster_center,
    cosine_distance,
    cosine_distance_matrix,
    key_detection_sort_key,
    normalize_vector,
    profile_state_for_sample_count,
    utc_now_iso,
)
from .state_repository import PetStateRepository

SUPPORTED_DEFAULT_SPECIES = frozenset({"cat", "dog"})
PET_DETECTOR_PIPELINE_VERSION = "yolox-raw-grid-v2"
DEFAULT_PET_DETECTOR_MODEL_URL = (
    "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/"
    "0.1.1rc0/yolox_nano.onnx"
)
PET_MODEL_AUTO_DOWNLOAD_ENV = "IPHOTO_PET_MODEL_AUTO_DOWNLOAD"
PET_DETECTOR_MODEL_URL_ENV = "IPHOTO_PET_DETECTOR_MODEL_URL"
_DINO_HUB_REPO = "facebookresearch/dinov2"
_DOWNLOAD_TIMEOUT_SECONDS = 60
_DOWNLOAD_CHUNK_SIZE = 1024 * 256
_YOLOX_STRIDES = (8, 16, 32)
_YOLOX_RAW_COORD_LIMIT = 32.0
_LOGGER = logging.getLogger(__name__)
COCO_ANIMAL_LABELS = {
    15: "cat",
    16: "dog",
    17: "horse",
    18: "sheep",
    19: "cow",
    20: "elephant",
    21: "bear",
    22: "zebra",
    23: "giraffe",
}


@dataclass(frozen=True)
class DetectedAssetPets:
    asset_id: str
    asset_rel: str
    detections: list[PetDetectionRecord]
    error: str | None = None


@dataclass(frozen=True)
class _DetectedPetBox:
    bbox: tuple[int, int, int, int]
    confidence: float
    species_label: str


@dataclass(frozen=True)
class PetScanMetrics:
    candidate_boxes: int = 0
    unsupported_species: int = 0
    too_small: int = 0
    accepted_detections: int = 0


class PetClusterPipeline:
    def __init__(
        self,
        *,
        model_root: Path,
        detector_model_name: str = "yolox_nano_coco.onnx",
        embedding_model_name: str = "dinov2_vits14",
        allow_model_download: bool | None = None,
        distance_threshold: float = 0.42,
        min_samples: int = 2,
        min_pet_size: int = 48,
        supported_species: frozenset[str] = SUPPORTED_DEFAULT_SPECIES,
    ) -> None:
        self._model_root = Path(model_root)
        self._detector_model_name = detector_model_name
        self._embedding_model_name = embedding_model_name
        self._allow_model_download = (
            pet_model_auto_download_enabled()
            if allow_model_download is None
            else bool(allow_model_download)
        )
        self._distance_threshold = float(distance_threshold)
        self._min_samples = int(min_samples)
        self._min_pet_size = int(min_pet_size)
        self._supported_species = frozenset(supported_species)
        self._detector: _YoloxOnnxPetDetector | None = None
        self._embedder: _DinoV2Embedder | None = None
        self._last_scan_metrics = PetScanMetrics()

    @property
    def distance_threshold(self) -> float:
        return self._distance_threshold

    @property
    def min_samples(self) -> int:
        return self._min_samples

    @property
    def detector_pipeline_version(self) -> str:
        return PET_DETECTOR_PIPELINE_VERSION

    @property
    def last_scan_metrics(self) -> PetScanMetrics:
        return self._last_scan_metrics

    def detect_pets_for_rows(
        self,
        rows: list[dict],
        *,
        library_root: Path,
        thumbnail_dir: Path,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> list[DetectedAssetPets]:
        if not rows:
            return []
        embedder = self._ensure_embedder()
        detector = self._ensure_detector()
        cancellation_requested = is_cancelled or (lambda: False)
        results: list[DetectedAssetPets] = []
        candidate_boxes = 0
        unsupported_species = 0
        too_small = 0
        accepted_detections = 0
        for row in rows:
            if cancellation_requested():
                break
            asset_id = str(row.get("id") or "")
            asset_rel = Path(str(row.get("rel") or "")).as_posix()
            image_path = (Path(library_root) / asset_rel).resolve()
            try:
                image = load_image_rgb(image_path)
                boxes = detector.detect(image)
            except PetImageLoadError as exc:
                if cancellation_requested():
                    break
                results.append(
                    DetectedAssetPets(
                        asset_id=asset_id,
                        asset_rel=asset_rel,
                        detections=[],
                        error=str(exc).strip() or exc.__class__.__name__,
                    )
                )
                continue
            except Exception as exc:  # noqa: BLE001
                if cancellation_requested():
                    break
                results.append(
                    DetectedAssetPets(
                        asset_id=asset_id,
                        asset_rel=asset_rel,
                        detections=[],
                        error=str(exc).strip() or exc.__class__.__name__,
                    )
                )
                continue

            image_width, image_height = image.size
            detections: list[PetDetectionRecord] = []
            candidate_boxes += len(boxes)
            for detected in boxes:
                if detected.species_label not in self._supported_species:
                    unsupported_species += 1
                    continue
                bbox = _normalize_bbox(
                    detected.bbox,
                    image_width=image_width,
                    image_height=image_height,
                )
                if bbox[2] < self._min_pet_size or bbox[3] < self._min_pet_size:
                    too_small += 1
                    continue
                detection_id = uuid.uuid4().hex
                thumbnail_path = thumbnail_dir / f"{detection_id}.png"
                try:
                    crop = crop_pet_region(image, bbox, padding_ratio=0.08)
                    embedding = embedder.embed(crop)
                    save_pet_thumbnail(image, bbox, thumbnail_path, padding_ratio=0.08)
                except Exception as exc:  # noqa: BLE001
                    results.append(
                        DetectedAssetPets(
                            asset_id=asset_id,
                            asset_rel=asset_rel,
                            detections=[],
                            error=str(exc).strip() or exc.__class__.__name__,
                        )
                    )
                    detections = []
                    break
                detections.append(
                    PetDetectionRecord(
                        detection_id=detection_id,
                        pet_key=build_pet_key(
                            asset_id=asset_id,
                            bbox=bbox,
                            image_width=image_width,
                            image_height=image_height,
                            species_label=detected.species_label,
                        ),
                        asset_id=asset_id,
                        asset_rel=asset_rel,
                        species_label=detected.species_label,
                        box_x=bbox[0],
                        box_y=bbox[1],
                        box_w=bbox[2],
                        box_h=bbox[3],
                        confidence=float(detected.confidence),
                        embedding=embedding,
                        embedding_dim=int(embedding.shape[0]),
                        embedding_model=self._embedding_model_name,
                        detector_model=self._detector_model_name,
                        thumbnail_path=thumbnail_path.relative_to(thumbnail_dir.parent).as_posix(),
                        pet_id=None,
                        detected_at=utc_now_iso(),
                        image_width=image_width,
                        image_height=image_height,
                    )
                )
                accepted_detections += 1
            has_asset_error = any(
                result.asset_id == asset_id and result.error for result in results
            )
            if detections or not has_asset_error:
                results.append(
                    DetectedAssetPets(
                        asset_id=asset_id,
                        asset_rel=asset_rel,
                        detections=detections,
                    )
                )
        self._last_scan_metrics = PetScanMetrics(
            candidate_boxes=candidate_boxes,
            unsupported_species=unsupported_species,
            too_small=too_small,
            accepted_detections=accepted_detections,
        )
        return results

    def _ensure_detector(self) -> _YoloxOnnxPetDetector:
        if self._detector is None:
            model_path = self._model_root / "detector" / self._detector_model_name
            self._detector = _YoloxOnnxPetDetector(
                model_path,
                allow_model_download=self._allow_model_download,
            )
        return self._detector

    def _ensure_embedder(self) -> _DinoV2Embedder:
        if self._embedder is None:
            model_dir = self._model_root / "embedding" / self._embedding_model_name
            self._embedder = _DinoV2Embedder(
                model_dir,
                model_name=self._embedding_model_name,
                allow_model_download=self._allow_model_download,
            )
        return self._embedder


def build_pet_key(
    *,
    asset_id: str,
    bbox: tuple[int, int, int, int],
    image_width: int,
    image_height: int,
    species_label: str,
    quantization: int = 12,
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
        f"{asset_id}|{image_width}x{image_height}|{species_label}|"
        f"{quantized[0]}|{quantized[1]}|{quantized[2]}|{quantized[3]}"
    )
    return hashlib.sha1(payload.encode("utf-8"), usedforsecurity=False).hexdigest()


def cluster_pet_records(
    detections: list[PetDetectionRecord],
    *,
    distance_threshold: float = 0.42,
    min_samples: int = 2,
    prefer_hdbscan: bool = True,
) -> tuple[list[PetDetectionRecord], list[PetRecord]]:
    if not detections:
        return [], []

    updated_detections = list(detections)
    pets: list[PetRecord] = []
    by_species: dict[str, list[int]] = defaultdict(list)
    for index, detection in enumerate(detections):
        by_species[detection.species_label].append(index)

    for species_label, indices in by_species.items():
        embeddings = np.stack(
            [detections[index].embedding for index in indices],
            axis=0,
        ).astype(np.float32)
        labels = _cluster_embeddings(
            embeddings,
            distance_threshold=distance_threshold,
            min_samples=min_samples,
            prefer_hdbscan=prefer_hdbscan,
        )
        grouped_indices: dict[str, list[int]] = defaultdict(list)
        for local_index, label in enumerate(labels.tolist()):
            if label == -1:
                grouped_indices[f"noise-{indices[local_index]}"].append(indices[local_index])
            else:
                grouped_indices[f"{species_label}-cluster-{label}"].append(indices[local_index])

        for grouped in grouped_indices.values():
            members = [detections[index] for index in grouped]
            key_detection = max(members, key=key_detection_sort_key)
            pet_id = uuid.uuid4().hex
            center_embedding = compute_cluster_center(
                np.stack([member.embedding for member in members], axis=0)
            )
            timestamp = utc_now_iso()
            pets.append(
                PetRecord(
                    pet_id=pet_id,
                    name=None,
                    species_label=species_label,
                    key_detection_id=key_detection.detection_id,
                    detection_count=len(members),
                    center_embedding=center_embedding,
                    embedding_dim=int(center_embedding.shape[0]),
                    created_at=timestamp,
                    updated_at=timestamp,
                    sample_count=len(members),
                    profile_state=profile_state_for_sample_count(len(members)),
                )
            )
            for index in grouped:
                updated_detections[index] = replace(updated_detections[index], pet_id=pet_id)

    pets.sort(key=lambda pet: (-pet.detection_count, pet.created_at, pet.pet_id))
    return updated_detections, pets


def build_pet_records_from_detections(
    detections: Sequence[PetDetectionRecord],
    *,
    names_by_pet_id: dict[str, str | None] | None = None,
    created_at_by_pet_id: dict[str, str] | None = None,
) -> list[PetRecord]:
    grouped: dict[str, list[PetDetectionRecord]] = defaultdict(list)
    for detection in detections:
        if detection.pet_id:
            grouped[str(detection.pet_id)].append(detection)

    names = dict(names_by_pet_id or {})
    created = dict(created_at_by_pet_id or {})
    updated_at = utc_now_iso()
    pets: list[PetRecord] = []
    for pet_id, members in grouped.items():
        key_detection = max(members, key=key_detection_sort_key)
        center_embedding = compute_cluster_center(
            np.stack([member.embedding for member in members], axis=0)
        )
        sample_count = len(members)
        pets.append(
            PetRecord(
                pet_id=pet_id,
                name=names.get(pet_id),
                species_label=key_detection.species_label,
                key_detection_id=key_detection.detection_id,
                detection_count=sample_count,
                center_embedding=center_embedding,
                embedding_dim=int(center_embedding.shape[0]),
                created_at=created.get(
                    pet_id,
                    min((member.detected_at for member in members), default=updated_at),
                ),
                updated_at=updated_at,
                sample_count=sample_count,
                profile_state=profile_state_for_sample_count(sample_count),
            )
        )
    pets.sort(key=lambda pet: (-pet.detection_count, pet.created_at, pet.pet_id))
    return pets


def canonicalize_pet_identities(
    detections: list[PetDetectionRecord],
    pets: list[PetRecord],
    state_repository: PetStateRepository,
    *,
    distance_threshold: float,
) -> tuple[list[PetDetectionRecord], list[PetRecord]]:
    if not detections or not pets:
        return detections, pets

    profiles = {profile.pet_id: profile for profile in state_repository.get_profiles()}
    pet_key_map = state_repository.get_pet_key_map(detection.pet_key for detection in detections)
    detections_by_pet_id: dict[str, list[PetDetectionRecord]] = defaultdict(list)
    for detection in detections:
        if detection.pet_id is not None:
            detections_by_pet_id[detection.pet_id].append(detection)

    canonical_members: dict[str, list[PetDetectionRecord]] = defaultdict(list)
    canonical_names: dict[str, str | None] = {}
    canonical_created_at: dict[str, str] = {}

    for pet in pets:
        members = detections_by_pet_id.get(pet.pet_id, [])
        canonical_id = resolve_canonical_pet_id(
            pet,
            members,
            profiles=profiles,
            pet_key_map=pet_key_map,
            distance_threshold=distance_threshold,
        )
        profile = profiles.get(canonical_id)
        canonical_members[canonical_id].extend(members)
        canonical_names.setdefault(canonical_id, profile.name if profile is not None else None)
        canonical_created_at.setdefault(
            canonical_id,
            profile.created_at if profile is not None else pet.created_at,
        )

    updated = list(detections)
    index_by_detection_id = {
        detection.detection_id: index for index, detection in enumerate(detections)
    }
    for canonical_id, members in canonical_members.items():
        for member in members:
            updated[index_by_detection_id[member.detection_id]] = replace(
                member,
                pet_id=canonical_id,
            )
    canonical_pets = build_pet_records_from_detections(
        updated,
        names_by_pet_id=canonical_names,
        created_at_by_pet_id=canonical_created_at,
    )
    return updated, canonical_pets


def resolve_canonical_pet_id(
    pet: PetRecord,
    members: list[PetDetectionRecord],
    *,
    profiles: dict[str, PetProfile],
    pet_key_map: dict[str, str],
    distance_threshold: float,
) -> str:
    vote_counter = Counter(
        pet_key_map[member.pet_key]
        for member in members
        if member.pet_key in pet_key_map
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
        if profile.species_label != pet.species_label:
            continue
        if str(profile.profile_state or "unstable") != "stable":
            continue
        if profile.embedding_dim <= 0 or profile.center_embedding.size == 0:
            continue
        if profile.center_embedding.shape != pet.center_embedding.shape:
            continue
        distance = cosine_distance(pet.center_embedding, profile.center_embedding)
        if distance < best_distance:
            best_distance = distance
            best_profile_id = profile.pet_id

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


def _cluster_embeddings(
    embeddings: np.ndarray,
    *,
    distance_threshold: float,
    min_samples: int,
    prefer_hdbscan: bool,
) -> np.ndarray:
    if embeddings.shape[0] < max(1, min_samples):
        return np.full(embeddings.shape[0], -1, dtype=np.int32)
    if prefer_hdbscan:
        try:
            import hdbscan
        except ImportError:
            hdbscan = None
        if hdbscan is not None:
            distance = cosine_distance_matrix(embeddings)
            clusterer = hdbscan.HDBSCAN(
                metric="precomputed",
                min_cluster_size=max(2, min_samples),
                min_samples=max(1, min_samples - 1),
            )
            return np.asarray(clusterer.fit_predict(distance), dtype=np.int32)
    return run_dbscan(
        embeddings,
        eps=distance_threshold,
        min_samples=min_samples,
    )


def _normalize_bbox(
    raw_bbox,
    *,
    image_width: int,
    image_height: int,
) -> tuple[int, int, int, int]:
    box = np.asarray(raw_bbox, dtype=np.float32).flatten().tolist()
    x, y, width, height = [round(value) for value in box[:4]]
    x = max(0, min(x, image_width - 1))
    y = max(0, min(y, image_height - 1))
    width = max(1, min(width, image_width - x))
    height = max(1, min(height, image_height - y))
    return x, y, width, height


def _quantize_value(value: float, step: int) -> int:
    step = max(1, int(step))
    return int(round(float(value) / step) * step)


class _YoloxOnnxPetDetector:
    def __init__(
        self,
        model_path: Path,
        *,
        score_threshold: float = 0.35,
        allow_model_download: bool = True,
    ) -> None:
        self._model_path = Path(model_path)
        self._score_threshold = float(score_threshold)
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise RuntimeError(
                "Pet scanning unavailable: missing onnxruntime. Install the optional "
                'Pets AI runtime with: pip install -e ".[pets-ai]"'
            ) from exc
        ensure_pet_detector_model(
            self._model_path,
            allow_model_download=allow_model_download,
        )
        try:
            providers = _resolve_execution_providers(ort)
            self._session = ort.InferenceSession(str(self._model_path), providers=providers)
            self._input_name = self._session.get_inputs()[0].name
            shape = self._session.get_inputs()[0].shape
            self._input_size = _input_size_from_shape(shape)
        except Exception as exc:
            if isinstance(exc, RuntimeError) and str(exc).startswith(
                "Pet scanning unavailable:"
            ):
                raise
            raise RuntimeError(
                "Pet scanning unavailable: failed to initialize YOLOX detector model at "
                f"{self._model_path} ({_error_reason(exc)}). Check the model cache, "
                "disable unsupported execution providers, or reinstall the Pets AI runtime."
            ) from exc

    def detect(self, image) -> list[_DetectedPetBox]:
        image_width, image_height = image.size
        input_width, input_height = self._input_size
        tensor = _preprocess_yolox(image, input_width=input_width, input_height=input_height)
        outputs = self._session.run(None, {self._input_name: tensor})
        predictions = _flatten_predictions(outputs)
        boxes: list[_DetectedPetBox] = []
        scale_x = image_width / float(input_width)
        scale_y = image_height / float(input_height)
        for x0, y0, x1, y1, confidence, class_id in _decode_yolox_predictions(
            predictions,
            input_size=self._input_size,
        ):
            species = COCO_ANIMAL_LABELS.get(int(class_id))
            if species is None or confidence < self._score_threshold:
                continue
            left = round(x0 * scale_x)
            top = round(y0 * scale_y)
            right = round(x1 * scale_x)
            bottom = round(y1 * scale_y)
            boxes.append(
                _DetectedPetBox(
                    bbox=(left, top, max(1, right - left), max(1, bottom - top)),
                    confidence=float(confidence),
                    species_label=species,
                )
            )
        return _nms_pet_boxes(boxes)


class _DinoV2Embedder:
    def __init__(
        self,
        model_dir: Path,
        *,
        model_name: str,
        allow_model_download: bool = True,
    ) -> None:
        self._model_dir = Path(model_dir)
        self._model_name = model_name
        try:
            import torch
        except ImportError as exc:
            raise RuntimeError(
                "Pet scanning unavailable: missing torch for DINOv2 pet embeddings. "
                'Install the optional Pets AI runtime with: pip install -e ".[pets-ai]"'
            ) from exc
        self._torch = torch
        self._device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        model_path = self._model_dir / f"{model_name}.pt"
        if model_path.is_file():
            self._model = torch.jit.load(str(model_path), map_location=self._device)
        elif allow_model_download:
            self._model = self._download_dinov2_model(model_path)
        else:
            raise RuntimeError(
                "Pet scanning unavailable: missing DINOv2 TorchScript model at "
                f"{model_path}. Set IPHOTO_PET_MODEL_DIR or enable pet model downloads."
            )
        self._model.eval()

    def embed(self, image) -> np.ndarray:
        tensor = image_to_chw_float(image, (224, 224))
        torch = self._torch
        with torch.no_grad():
            input_tensor = torch.from_numpy(tensor).to(self._device)
            output = self._model(input_tensor)
            if isinstance(output, (list, tuple)):
                output = output[0]
            vector = output.detach().cpu().numpy().reshape(-1)
        return normalize_vector(vector.astype(np.float32))

    def _download_dinov2_model(self, model_path: Path):
        torch = self._torch
        try:
            _install_certifi_environment()
            try:
                model = torch.hub.load(
                    _DINO_HUB_REPO,
                    self._model_name,
                    pretrained=True,
                    trust_repo=True,
                )
            except TypeError:
                model = torch.hub.load(
                    _DINO_HUB_REPO,
                    self._model_name,
                    pretrained=True,
                )
        except Exception as exc:
            raise RuntimeError(
                "Pet scanning unavailable: failed to download DINOv2 model "
                f"'{self._model_name}' from {_DINO_HUB_REPO} ({_error_reason(exc)}). "
                "Check your network connection, disable pet model auto-download, "
                "or place the model in IPHOTO_PET_MODEL_DIR."
            ) from exc

        model.eval()
        model.to(self._device)
        self._cache_dinov2_torchscript(model, model_path)
        return model

    def _cache_dinov2_torchscript(self, model, model_path: Path) -> None:
        torch = self._torch
        previous_device = self._device
        try:
            model_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="iphoto-pet-dinov2-") as tmp_dir:
                tmp_path = Path(tmp_dir) / model_path.name
                try:
                    previous_device = next(model.parameters()).device
                except StopIteration:
                    previous_device = self._device
                model.cpu()
                example = torch.zeros((1, 3, 224, 224), dtype=torch.float32)
                traced = torch.jit.trace(model, example, strict=False)
                traced.save(str(tmp_path))
                tmp_path.replace(model_path)
        except Exception:  # noqa: BLE001
            _LOGGER.warning(
                "Failed to cache downloaded DINOv2 model at %s",
                model_path,
                exc_info=True,
            )
        finally:
            model.to(previous_device)


def _resolve_execution_providers(ort) -> list[str]:
    available = set(ort.get_available_providers())
    preferred = [
        "CUDAExecutionProvider",
        "CoreMLExecutionProvider",
        "OpenVINOExecutionProvider",
        "CPUExecutionProvider",
    ]
    providers = [provider for provider in preferred if provider in available]
    return providers or ["CPUExecutionProvider"]


def _input_size_from_shape(shape: Sequence[object]) -> tuple[int, int]:
    if len(shape) >= 4 and isinstance(shape[2], int) and isinstance(shape[3], int):
        return int(shape[3]), int(shape[2])
    return 640, 640


def _preprocess_yolox(image, *, input_width: int, input_height: int) -> np.ndarray:
    resized = image.resize((input_width, input_height))
    array = np.asarray(resized, dtype=np.float32)
    array = np.transpose(array, (2, 0, 1))[None, :, :, :]
    return np.ascontiguousarray(array)


def _flatten_predictions(outputs: Sequence[np.ndarray]) -> np.ndarray:
    if not outputs:
        return np.empty((0, 0), dtype=np.float32)
    prediction = np.asarray(outputs[0], dtype=np.float32)
    return prediction.reshape(-1, prediction.shape[-1])


def _decode_yolox_predictions(
    predictions: np.ndarray,
    *,
    input_size: tuple[int, int],
) -> list[tuple[float, float, float, float, float, int]]:
    if predictions.size == 0:
        return []
    decoded = np.asarray(predictions, dtype=np.float32)
    if _looks_like_raw_yolox_output(decoded, input_size=input_size):
        decoded = _decode_raw_yolox_output(decoded, input_size=input_size)
    return [
        _decode_prediction(prediction)
        for prediction in decoded
        if prediction.shape[0] >= 6
    ]


def _decode_prediction(prediction: np.ndarray) -> tuple[float, float, float, float, float, int]:
    if prediction.shape[0] >= 85:
        cx, cy, width, height = [float(value) for value in prediction[:4]]
        object_score = float(prediction[4])
        class_scores = prediction[5:]
        class_index = int(np.argmax(class_scores))
        confidence = object_score * float(class_scores[class_index])
        x0 = cx - width / 2.0
        y0 = cy - height / 2.0
        x1 = cx + width / 2.0
        y1 = cy + height / 2.0
        return x0, y0, x1, y1, confidence, class_index
    x0, y0, x1, y1 = [float(value) for value in prediction[:4]]
    confidence = float(prediction[4])
    class_id = round(float(prediction[5]))
    return x0, y0, x1, y1, confidence, class_id


def _looks_like_raw_yolox_output(
    predictions: np.ndarray,
    *,
    input_size: tuple[int, int],
) -> bool:
    if predictions.ndim != 2 or predictions.shape[1] < 85:
        return False
    grids, _strides = _yolox_grids(input_size)
    if predictions.shape[0] != grids.shape[0]:
        return False
    coord_max = float(np.nanmax(np.abs(predictions[:, :4]))) if predictions.size else 0.0
    return coord_max <= _YOLOX_RAW_COORD_LIMIT


def _decode_raw_yolox_output(
    predictions: np.ndarray,
    *,
    input_size: tuple[int, int],
) -> np.ndarray:
    grids, strides = _yolox_grids(input_size)
    decoded = np.array(predictions, dtype=np.float32, copy=True)
    decoded[:, :2] = (decoded[:, :2] + grids) * strides
    decoded[:, 2:4] = np.exp(np.clip(decoded[:, 2:4], -20.0, 20.0)) * strides
    return decoded


def _yolox_grids(input_size: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    input_width, input_height = input_size
    grid_parts: list[np.ndarray] = []
    stride_parts: list[np.ndarray] = []
    for stride in _YOLOX_STRIDES:
        grid_height = int(input_height) // stride
        grid_width = int(input_width) // stride
        yv, xv = np.meshgrid(
            np.arange(grid_height, dtype=np.float32),
            np.arange(grid_width, dtype=np.float32),
            indexing="ij",
        )
        grid = np.stack((xv, yv), axis=-1).reshape(-1, 2)
        grid_parts.append(grid)
        stride_parts.append(np.full((grid.shape[0], 1), stride, dtype=np.float32))
    return np.concatenate(grid_parts, axis=0), np.concatenate(stride_parts, axis=0)


def _nms_pet_boxes(
    boxes: list[_DetectedPetBox],
    *,
    threshold: float = 0.5,
) -> list[_DetectedPetBox]:
    selected: list[_DetectedPetBox] = []
    for box in sorted(boxes, key=lambda item: item.confidence, reverse=True):
        if any(
            existing.species_label == box.species_label
            and _bbox_iou(existing.bbox, box.bbox) >= threshold
            for existing in selected
        ):
            continue
        selected.append(box)
    return selected


def _bbox_iou(left: tuple[int, int, int, int], right: tuple[int, int, int, int]) -> float:
    lx, ly, lw, lh = left
    rx, ry, rw, rh = right
    left_x2 = lx + lw
    left_y2 = ly + lh
    right_x2 = rx + rw
    right_y2 = ry + rh
    inter_left = max(lx, rx)
    inter_top = max(ly, ry)
    inter_right = min(left_x2, right_x2)
    inter_bottom = min(left_y2, right_y2)
    inter_width = max(0, inter_right - inter_left)
    inter_height = max(0, inter_bottom - inter_top)
    intersection = inter_width * inter_height
    union = lw * lh + rw * rh - intersection
    if union <= 0:
        return 0.0
    return intersection / float(union)


def pet_model_auto_download_enabled() -> bool:
    raw = str(os.environ.get(PET_MODEL_AUTO_DOWNLOAD_ENV, "")).strip().lower()
    return raw not in {"0", "false", "no", "off"}


def ensure_pet_detector_model(
    model_path: Path,
    *,
    allow_model_download: bool = True,
    model_url: str | None = None,
) -> Path:
    target = Path(model_path)
    if target.is_file():
        return target
    if not allow_model_download:
        raise RuntimeError(
            "Pet scanning unavailable: missing YOLOX model at "
            f"{target}. Set IPHOTO_PET_MODEL_DIR or enable pet model downloads."
        )

    url = str(
        model_url
        or os.environ.get(PET_DETECTOR_MODEL_URL_ENV)
        or DEFAULT_PET_DETECTOR_MODEL_URL
    ).strip()
    if not url:
        raise RuntimeError(
            "Pet scanning unavailable: missing YOLOX model at "
            f"{target} and no pet detector download URL is configured."
        )
    _download_file(
        url,
        target,
        label="YOLOX pet detector model",
    )
    return target


def default_pet_model_dir() -> Path:
    override = os.environ.get("IPHOTO_PET_MODEL_DIR")
    if override:
        return Path(override).expanduser()
    package_root = Path(__file__).resolve().parents[2]
    return package_root / "extension" / "models" / "pets"


def _download_file(url: str, destination: Path, *, label: str) -> None:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="iphoto-pet-model-") as tmp_dir:
            tmp_path = Path(tmp_dir) / destination.name
            with request.urlopen(  # noqa: S310
                url,
                timeout=_DOWNLOAD_TIMEOUT_SECONDS,
                context=_download_ssl_context(url),
            ) as response, tmp_path.open("wb") as handle:
                while True:
                    chunk = response.read(_DOWNLOAD_CHUNK_SIZE)
                    if not chunk:
                        break
                    handle.write(chunk)
            if tmp_path.stat().st_size <= 0:
                raise RuntimeError(f"Downloaded {label} is empty.")
            tmp_path.replace(destination)
    except TimeoutError as exc:
        raise RuntimeError(
            f"Pet scanning unavailable: downloading {label} timed out. "
            "Check your network connection or install the model manually."
        ) from exc
    except Exception as exc:
        if isinstance(exc, RuntimeError) and str(exc).startswith("Pet scanning unavailable:"):
            raise
        raise RuntimeError(
            f"Pet scanning unavailable: failed to download {label} from {url} "
            f"({_error_reason(exc)}). Check your network connection, set "
            f"{PET_DETECTOR_MODEL_URL_ENV}, or install the model manually."
        ) from exc


def _error_reason(exc: Exception) -> str:
    return str(exc).strip() or exc.__class__.__name__


def _download_ssl_context(url: str) -> ssl.SSLContext | None:
    if not url.lower().startswith("https://"):
        return None
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    _install_certifi_environment()
    return ssl.create_default_context(cafile=certifi.where())


def _install_certifi_environment() -> None:
    try:
        import certifi
    except ImportError:
        return
    cafile = certifi.where()
    os.environ.setdefault("SSL_CERT_FILE", cafile)
    os.environ.setdefault("REQUESTS_CA_BUNDLE", cafile)

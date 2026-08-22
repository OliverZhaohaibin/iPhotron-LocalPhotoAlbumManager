"""Pet detection, embedding, clustering, and identity helpers."""

from __future__ import annotations

import errno
import hashlib
import json
import logging
import math
import os
import ssl
import sys
import tempfile
import uuid
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from urllib import request
from urllib.parse import urlparse

import numpy as np
from PIL import Image

from iPhoto.utils.pathutils import LibraryAssetPathError, resolve_library_asset_path

from .errors import (
    PetInferenceError,
    PetModelUnavailableError,
    PetPipelineInvariantError,
    PetRuntimeUnavailableError,
)
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


class _ModelStoragePermissionError(OSError):
    pass


_MODEL_STORAGE_ERRNOS = {
    errno.EACCES,
    errno.EPERM,
    errno.EROFS,
}


def _raise_if_model_storage_error(exc: OSError, path: Path) -> None:
    if exc.errno in _MODEL_STORAGE_ERRNOS:
        raise _ModelStoragePermissionError(
            exc.errno,
            f"model storage is not writable: {path}",
        ) from exc
    raise exc


def _load_pet_model_manifest() -> dict:
    manifest_path = Path(__file__).with_name("model_manifest.json")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        detector = manifest["detector"]
        embedder = manifest["embedder"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise PetPipelineInvariantError(f"Invalid Pets model manifest: {manifest_path}") from exc
    if int(manifest.get("schema_version") or 0) != 1:
        raise PetPipelineInvariantError(f"Unsupported Pets model manifest: {manifest_path}")
    if urlparse(str(detector.get("url") or "")).scheme.lower() != "https":
        raise PetPipelineInvariantError("Pets detector manifest URL must use HTTPS.")
    if detector.get("input") != {
        "layout": "NCHW",
        "channel_order": "BGR",
        "dtype": "float32",
        "range": [0, 255],
        "shape": [1, 3, 416, 416],
    }:
        raise PetPipelineInvariantError("Pets detector manifest input contract is invalid.")
    if embedder.get("input_shape") != [1, 3, 224, 224]:
        raise PetPipelineInvariantError("Pets embedder manifest input contract is invalid.")
    weights_url = str(embedder.get("weights_url") or "").strip()
    if urlparse(weights_url).scheme.lower() != "https":
        raise PetPipelineInvariantError("Pets embedder checkpoint URL must use HTTPS.")
    weights_sha256 = str(embedder.get("weights_sha256") or "")
    if len(weights_sha256) != 64 or any(
        char not in "0123456789abcdef" for char in weights_sha256.lower()
    ):
        raise PetPipelineInvariantError("Pets embedder checkpoint SHA-256 is invalid.")
    if int(embedder.get("weights_size") or 0) <= 0:
        raise PetPipelineInvariantError("Pets embedder checkpoint size is invalid.")
    torchscript_url = str(embedder.get("torchscript_url") or "").strip()
    if torchscript_url and urlparse(torchscript_url).scheme.lower() != "https":
        raise PetPipelineInvariantError("Pets embedder TorchScript URL must use HTTPS.")
    if len(str(embedder.get("torchscript_sha256") or "")) != 64:
        raise PetPipelineInvariantError("Pets embedder TorchScript SHA-256 is invalid.")
    if int(embedder.get("torchscript_size") or 0) <= 0:
        raise PetPipelineInvariantError("Pets embedder TorchScript size is invalid.")
    return manifest


PET_MODEL_MANIFEST = _load_pet_model_manifest()
_DETECTOR_MANIFEST = PET_MODEL_MANIFEST["detector"]
_EMBEDDER_MANIFEST = PET_MODEL_MANIFEST["embedder"]
SUPPORTED_DEFAULT_SPECIES = frozenset({"cat", "dog"})
PET_DETECTOR_PIPELINE_VERSION = "yolox-letterbox-tiles-people-priority-v6"
PET_CLUSTERING_PIPELINE_VERSION = "species-bounded-single-link-v3"
PET_EMBEDDING_PIPELINE_VERSION = "dinov2-vits14-imagenet-normalized-v1"
PET_KEY_VERSION = "v2"
PET_DETECTOR_KEY_VERSION = "yolox-nano-coco-0.1.1rc0-raw-bgr-v1"
DEFAULT_PET_DISTANCE_THRESHOLD = 0.42
PET_CLUSTER_DIAMETER_MULTIPLIER = 1.5
PET_PET_IOU_THRESHOLD = 0.50
PET_PET_SMALLER_BOX_COVERAGE_THRESHOLD = 0.90
PET_PET_NORMALIZED_CENTER_DISTANCE_THRESHOLD = 0.40
PET_PET_CROSS_SPECIES_MUTUAL_COVERAGE_THRESHOLD = 0.90
PET_PEOPLE_IOU_THRESHOLD = 0.50
PET_PEOPLE_SMALLER_BOX_COVERAGE_THRESHOLD = 0.90
PET_PEOPLE_LARGER_PET_RATIO = 1.50
PET_PEOPLE_MURAL_IMAGE_COVERAGE_THRESHOLD = 0.60
PET_CANDIDATE_QUALITY_VERSION = "tiny-low-confidence-v1"
DEFAULT_PET_TINY_AREA_RATIO = 0.001
DEFAULT_PET_TINY_MAX_CONFIDENCE = 0.45
DEFAULT_PET_DETECTOR_MODEL_URL = str(_DETECTOR_MANIFEST["url"])
PET_MODEL_AUTO_DOWNLOAD_ENV = "IPHOTO_PET_MODEL_AUTO_DOWNLOAD"
IPHOTO_PET_MODEL_DIR_ENV = "IPHOTO_PET_MODEL_DIR"
PET_DETECTOR_MODEL_URL_ENV = "IPHOTO_PET_DETECTOR_MODEL_URL"
PET_DETECTOR_MODEL_SHA256_ENV = "IPHOTO_PET_DETECTOR_MODEL_SHA256"
DEFAULT_PET_DETECTOR_MODEL_SHA256 = str(_DETECTOR_MANIFEST["sha256"])
DEFAULT_PET_DETECTOR_MODEL_MAX_BYTES = int(_DETECTOR_MANIFEST["max_bytes"])
# The development conversion tool uses this immutable source revision. The
# production runtime builds a local cache from the fixed, hash-verified Meta
# checkpoint when no valid bundled artifact is available.
_DINO_SOURCE_REVISION = str(_EMBEDDER_MANIFEST["source_revision"])
_DINO_WEIGHTS_URL = str(_EMBEDDER_MANIFEST["weights_url"])
_DINO_WEIGHTS_SHA256 = str(_EMBEDDER_MANIFEST["weights_sha256"]).lower()
_DINO_WEIGHTS_SIZE = int(_EMBEDDER_MANIFEST["weights_size"])
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


class PetIdentityResolutionSource(StrEnum):
    KEY = "key"
    REDIRECT_KEY = "redirect_key"
    PROFILE = "profile"
    REDIRECT_PROFILE = "redirect_profile"
    NEW = "new"


@dataclass(frozen=True)
class PetIdentityResolution:
    raw_pet_id: str
    canonical_pet_id: str
    source: PetIdentityResolutionSource

    @property
    def is_redirect_alias(self) -> bool:
        return self.source in {
            PetIdentityResolutionSource.REDIRECT_KEY,
            PetIdentityResolutionSource.REDIRECT_PROFILE,
        }


@dataclass(frozen=True)
class _DetectedPetBox:
    bbox: tuple[int, int, int, int]
    confidence: float
    species_label: str
    quality_score: float = 0.0


@dataclass(frozen=True)
class _YoloxPreprocessResult:
    tensor: np.ndarray
    resize_ratio: float
    pad_left: int = 0
    pad_top: int = 0


@dataclass(frozen=True)
class PetScanMetrics:
    candidate_boxes: int = 0
    unsupported_species: int = 0
    too_small: int = 0
    pet_quality_rejected: int = 0
    people_overlaps: int = 0
    accepted_detections: int = 0
    pet_candidate_identities: int = 0
    pet_promotions: int = 0
    same_asset_cannot_link_hits: int = 0
    same_asset_manual_conflicts: int = 0


@dataclass(frozen=True)
class _PetPeopleOverlapDecision:
    suppressed: bool
    reason: str = ""
    pet_to_face_area_ratio: float = 0.0
    pet_image_coverage: float = 0.0


class PetClusterPipeline:
    def __init__(
        self,
        *,
        model_root: Path,
        detector_model_name: str = "yolox_nano_coco.onnx",
        embedding_model_name: str = "dinov2_vits14",
        allow_model_download: bool | None = None,
        distance_threshold: float = DEFAULT_PET_DISTANCE_THRESHOLD,
        min_pet_size: int = 48,
        supported_species: frozenset[str] = SUPPORTED_DEFAULT_SPECIES,
        detector_score_threshold: float = 0.30,
        enable_tiled_detection: bool = True,
        tile_scan_min_confidence: float | None = None,
        tiny_area_ratio: float = DEFAULT_PET_TINY_AREA_RATIO,
        tiny_max_confidence: float = DEFAULT_PET_TINY_MAX_CONFIDENCE,
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
        self._min_pet_size = int(min_pet_size)
        self._supported_species = frozenset(supported_species)
        self._detector_score_threshold = float(detector_score_threshold)
        self._enable_tiled_detection = bool(enable_tiled_detection)
        self._tile_scan_min_confidence = (
            self._detector_score_threshold
            if tile_scan_min_confidence is None
            else float(tile_scan_min_confidence)
        )
        self._tiny_area_ratio = float(tiny_area_ratio)
        self._tiny_max_confidence = float(tiny_max_confidence)
        self._detector: _YoloxOnnxPetDetector | None = None
        self._embedder: _DinoV2Embedder | None = None
        self._last_scan_metrics = PetScanMetrics()

    @property
    def distance_threshold(self) -> float:
        return self._distance_threshold

    @property
    def detector_pipeline_version(self) -> str:
        return PET_DETECTOR_PIPELINE_VERSION

    @property
    def candidate_quality_version(self) -> str:
        return PET_CANDIDATE_QUALITY_VERSION

    @property
    def last_scan_metrics(self) -> PetScanMetrics:
        return self._last_scan_metrics

    def detect_pets_for_rows(
        self,
        rows: list[dict],
        *,
        library_root: Path,
        thumbnail_dir: Path,
        published_thumbnail_dir: Path | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        people_boxes_by_asset_id: dict[str, Sequence[tuple[int, int, int, int]]] | None = None,
    ) -> list[DetectedAssetPets]:
        if not rows:
            return []
        embedder = self._ensure_embedder()
        detector = self._ensure_detector()
        cancellation_requested = is_cancelled or (lambda: False)
        stored_thumbnail_dir = Path(published_thumbnail_dir or thumbnail_dir)
        results: list[DetectedAssetPets] = []
        candidate_boxes = 0
        unsupported_species = 0
        too_small = 0
        pet_quality_rejected = 0
        people_overlaps = 0
        accepted_detections = 0
        excluded_people_boxes = people_boxes_by_asset_id or {}
        for row in rows:
            if cancellation_requested():
                break
            asset_id = str(row.get("id") or "")
            asset_rel = Path(str(row.get("rel") or "")).as_posix()
            try:
                image_path = resolve_library_asset_path(library_root, asset_rel)
                image = load_image_rgb(image_path)
                boxes = detector.detect(image)
            except LibraryAssetPathError as exc:
                results.append(
                    DetectedAssetPets(
                        asset_id=asset_id,
                        asset_rel=asset_rel,
                        detections=[],
                        error=str(exc),
                    )
                )
                continue
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
            except PetPipelineInvariantError:
                raise
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
            created_thumbnail_paths: list[Path] = []
            supported_boxes: list[_DetectedPetBox] = []
            quality_candidates: list[
                tuple[tuple[int, int, int, int], float, str, float]
            ] = []
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
                area_ratio = (bbox[2] * bbox[3]) / max(1, image_width * image_height)
                if (
                    area_ratio < self._tiny_area_ratio
                    and float(detected.confidence) < self._tiny_max_confidence
                ):
                    pet_quality_rejected += 1
                    continue
                quality_candidates.append(
                    (bbox, float(detected.confidence), detected.species_label, area_ratio)
                )

            largest_pet_area_ratio = max(
                (area_ratio for _bbox, _confidence, _species, area_ratio in quality_candidates),
                default=0.0,
            )
            for bbox, confidence, species_label, area_ratio in quality_candidates:
                supported_boxes.append(
                    _DetectedPetBox(
                        bbox=bbox,
                        confidence=confidence,
                        species_label=species_label,
                        quality_score=_pet_candidate_quality_score(
                            confidence=confidence,
                            relative_area_ratio=area_ratio
                            / max(largest_pet_area_ratio, np.finfo(np.float32).eps),
                        ),
                    )
                )

            deduped_boxes = _dedupe_supported_species_boxes(supported_boxes)
            people_boxes = excluded_people_boxes.get(asset_id, ())
            accepted_boxes: list[_DetectedPetBox] = []
            for detected in deduped_boxes:
                decision = _pet_people_overlap_decision(
                    detected.bbox,
                    people_boxes,
                    image_dimensions=(image_width, image_height),
                )
                if decision.suppressed:
                    people_overlaps += 1
                    _LOGGER.info(
                        "Suppressed pet candidate for asset %s: reason=%s "
                        "pet_to_face_area_ratio=%.3f pet_image_coverage=%.3f "
                        "iou_threshold=%.2f smaller_box_coverage_threshold=%.2f "
                        "larger_pet_ratio=%.2f mural_image_coverage_threshold=%.2f",
                        asset_id,
                        decision.reason,
                        decision.pet_to_face_area_ratio,
                        decision.pet_image_coverage,
                        PET_PEOPLE_IOU_THRESHOLD,
                        PET_PEOPLE_SMALLER_BOX_COVERAGE_THRESHOLD,
                        PET_PEOPLE_LARGER_PET_RATIO,
                        PET_PEOPLE_MURAL_IMAGE_COVERAGE_THRESHOLD,
                    )
                    continue
                accepted_boxes.append(detected)

            for detected in accepted_boxes:
                bbox = detected.bbox
                detection_id = uuid.uuid4().hex
                thumbnail_path = thumbnail_dir / f"{detection_id}.png"
                try:
                    crop = crop_pet_region(image, bbox, padding_ratio=0.08)
                    embedding = embedder.embed(crop)
                    save_pet_thumbnail(image, bbox, thumbnail_path, padding_ratio=0.08)
                    created_thumbnail_paths.append(thumbnail_path)
                except PetPipelineInvariantError:
                    for created_path in created_thumbnail_paths:
                        created_path.unlink(missing_ok=True)
                    raise
                except Exception as exc:  # noqa: BLE001
                    for created_path in created_thumbnail_paths:
                        try:
                            created_path.unlink(missing_ok=True)
                        except OSError:
                            _LOGGER.warning(
                                "Failed to roll back pet thumbnail %s",
                                created_path,
                                exc_info=True,
                            )
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
                        box_x=bbox[0],
                        box_y=bbox[1],
                        box_w=bbox[2],
                        box_h=bbox[3],
                        confidence=float(detected.confidence),
                        embedding=embedding,
                        embedding_dim=int(embedding.shape[0]),
                        embedding_model=self._embedding_model_name,
                        detector_model=self._detector_model_name,
                        thumbnail_path=(stored_thumbnail_dir / thumbnail_path.name)
                        .relative_to(stored_thumbnail_dir.parent)
                        .as_posix(),
                        pet_id=None,
                        detected_at=utc_now_iso(),
                        image_width=image_width,
                        image_height=image_height,
                        species_label=detected.species_label,
                        quality_score=detected.quality_score,
                        pet_key_version=PET_KEY_VERSION,
                        embedding_pipeline_version=PET_EMBEDDING_PIPELINE_VERSION,
                    )
                )
            has_asset_error = any(
                result.asset_id == asset_id and result.error for result in results
            )
            if detections or not has_asset_error:
                accepted_detections += len(detections)
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
            pet_quality_rejected=pet_quality_rejected,
            people_overlaps=people_overlaps,
            accepted_detections=accepted_detections,
        )
        return results

    def _ensure_detector(self) -> _YoloxOnnxPetDetector:
        if self._detector is None:
            model_path = self._resolve_model_path(Path("detector") / self._detector_model_name)
            try:
                self._detector = _YoloxOnnxPetDetector(
                    model_path,
                    score_threshold=self._detector_score_threshold,
                    allow_model_download=self._allow_model_download,
                    enable_tiled_detection=self._enable_tiled_detection,
                    tile_scan_min_confidence=self._tile_scan_min_confidence,
                    tile_species=self._supported_species,
                )
            except (
                PetRuntimeUnavailableError,
                PetModelUnavailableError,
                PetPipelineInvariantError,
            ):
                raise
            except RuntimeError as exc:
                raise PetModelUnavailableError(str(exc)) from exc
        return self._detector

    def _ensure_embedder(self) -> _DinoV2Embedder:
        if self._embedder is None:
            model_dir = self._resolve_model_path(
                Path("embedding") / self._embedding_model_name,
                directory=True,
            )
            try:
                self._embedder = _DinoV2Embedder(
                    model_dir,
                    model_name=self._embedding_model_name,
                    allow_model_download=self._allow_model_download,
                )
            except (
                PetRuntimeUnavailableError,
                PetModelUnavailableError,
                PetPipelineInvariantError,
            ):
                raise
            except RuntimeError as exc:
                raise PetModelUnavailableError(str(exc)) from exc
        return self._embedder

    def _resolve_model_path(self, relative_path: Path, *, directory: bool = False) -> Path:
        override = pet_model_override_dir()
        if override is not None and self._model_root != override:
            raise PetModelUnavailableError(
                "Pet scanning unavailable: model root does not match "
                f"{IPHOTO_PET_MODEL_DIR_ENV}."
            )
        if self._model_root == default_pet_model_dir():
            return resolve_pet_model_path(relative_path, directory=directory)
        return self._model_root / relative_path


def build_pet_key(
    *,
    asset_id: str,
    bbox: tuple[int, int, int, int],
    image_width: int,
    image_height: int,
    species_label: str | None = None,
    detector_key_version: str = PET_DETECTOR_KEY_VERSION,
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
    species = _normalize_species_label(species_label) or "unknown"
    payload = (
        f"{PET_KEY_VERSION}|{detector_key_version}|{asset_id}|"
        f"{image_width}x{image_height}|{species}|"
        f"{quantized[0]}|{quantized[1]}|{quantized[2]}|{quantized[3]}"
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"{PET_KEY_VERSION}:{digest}"


def cluster_pet_records(
    detections: list[PetDetectionRecord],
    *,
    distance_threshold: float = 0.42,
) -> tuple[list[PetDetectionRecord], list[PetRecord]]:
    if not detections:
        return [], []

    updated_detections = list(detections)
    pets: list[PetRecord] = []
    labels = _cluster_pet_detection_labels(
        detections,
        distance_threshold=distance_threshold,
    )
    grouped_indices: dict[str, list[int]] = defaultdict(list)
    for index, label in enumerate(labels.tolist()):
        grouped_indices[f"cluster-{label}"].append(index)

    for grouped in grouped_indices.values():
        members = [detections[index] for index in grouped]
        key_detection = max(members, key=key_detection_sort_key)
        pet_id = uuid.uuid4().hex
        center_embedding = compute_cluster_center(
            np.stack([member.embedding for member in members], axis=0)
        )
        timestamp = utc_now_iso()
        evidence_asset_count = len({member.asset_id for member in members if member.asset_id})
        pets.append(
            PetRecord(
                pet_id=pet_id,
                name=None,
                key_detection_id=key_detection.detection_id,
                detection_count=len(members),
                center_embedding=center_embedding,
                embedding_dim=int(center_embedding.shape[0]),
                created_at=timestamp,
                updated_at=timestamp,
                sample_count=len(members),
                profile_state=profile_state_for_sample_count(evidence_asset_count),
                species_label=_dominant_species_label(members),
                embedding_pipeline_version=members[0].embedding_pipeline_version,
                generation_id=members[0].generation_id,
                boundary_embeddings=_boundary_embeddings(members, center_embedding),
                evidence_asset_count=evidence_asset_count,
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
    allow_mixed_identity_members: bool = False,
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
        species_labels = {
            label
            for label in (_normalize_species_label(member.species_label) for member in members)
            if label is not None
        }
        if len(species_labels) > 1:
            if not allow_mixed_identity_members:
                raise ValueError(
                    f"Pet {pet_id} mixes incompatible species labels: "
                    f"{sorted(species_labels)}"
                )
            _LOGGER.info(
                "Preserving mixed-species Pet identity %s: species=%s",
                pet_id,
                sorted(species_labels),
            )
        contract_groups: dict[tuple[str, int, int], list[PetDetectionRecord]] = defaultdict(list)
        for member in members:
            contract_groups[
                (
                    str(member.embedding_pipeline_version or ""),
                    int(member.embedding_dim),
                    int(member.generation_id),
                )
            ].append(member)
        if len(contract_groups) != 1:
            if not allow_mixed_identity_members:
                raise ValueError(
                    f"Pet {pet_id} mixes incompatible embedding contracts: "
                    f"{sorted(contract_groups)}"
                )
            _LOGGER.info(
                "Preserving mixed-contract Pet identity %s: contracts=%s",
                pet_id,
                sorted(contract_groups),
            )
        _profile_contract, profile_members = max(
            contract_groups.items(),
            key=lambda item: (
                len(item[1]),
                item[0][2],
                item[0][0],
                item[0][1],
            ),
        )
        key_detection = max(members, key=key_detection_sort_key)
        center_embedding = compute_cluster_center(
            np.stack([member.embedding for member in profile_members], axis=0)
        )
        sample_count = len(members)
        evidence_asset_count = len({member.asset_id for member in members if member.asset_id})
        pets.append(
            PetRecord(
                pet_id=pet_id,
                name=names.get(pet_id),
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
                profile_state=profile_state_for_sample_count(evidence_asset_count),
                species_label=_dominant_species_label(members),
                embedding_pipeline_version=profile_members[0].embedding_pipeline_version,
                generation_id=profile_members[0].generation_id,
                boundary_embeddings=_boundary_embeddings(profile_members, center_embedding),
                evidence_asset_count=evidence_asset_count,
            )
        )
    pets.sort(key=lambda pet: (-pet.detection_count, pet.created_at, pet.pet_id))
    return pets


def _boundary_embeddings(
    members: Sequence[PetDetectionRecord],
    center_embedding: np.ndarray,
) -> tuple[np.ndarray, ...]:
    ranked = sorted(
        members,
        key=lambda member: (
            -cosine_distance(member.embedding, center_embedding),
            member.detection_id,
        ),
    )
    return tuple(normalize_vector(member.embedding) for member in ranked[:8])


def canonicalize_pet_identities(
    detections: list[PetDetectionRecord],
    pets: list[PetRecord],
    state_repository: PetStateRepository,
    *,
    distance_threshold: float,
) -> tuple[list[PetDetectionRecord], list[PetRecord]]:
    if not detections or not pets:
        return detections, pets

    profiles = {profile.pet_id: profile for profile in state_repository.get_identity_profiles()}
    redirects = state_repository.get_merge_redirect_map()
    pet_key_map = state_repository.get_pet_key_map(detection.pet_key for detection in detections)
    detections_by_pet_id: dict[str, list[PetDetectionRecord]] = defaultdict(list)
    for detection in detections:
        if detection.pet_id is not None:
            detections_by_pet_id[detection.pet_id].append(detection)

    canonical_members: dict[str, list[PetDetectionRecord]] = defaultdict(list)
    canonical_names: dict[str, str | None] = {}
    canonical_created_at: dict[str, str] = {}
    direct_anchors: set[str] = set()

    for pet in pets:
        members = detections_by_pet_id.get(pet.pet_id, [])
        resolution = resolve_canonical_pet_id(
            pet,
            members,
            profiles=profiles,
            pet_key_map=pet_key_map,
            redirects=redirects,
            distance_threshold=distance_threshold,
        )
        canonical_id = resolution.canonical_pet_id
        is_incompatible = bool(
            canonical_members.get(canonical_id)
            and not _detection_groups_compatible(
                canonical_members[canonical_id],
                members,
                distance_threshold=distance_threshold,
            )
        )
        same_asset_conflict = bool(
            canonical_members.get(canonical_id)
            and _detection_groups_share_asset(canonical_members[canonical_id], members)
        )
        if is_incompatible and (
            same_asset_conflict
            or (not resolution.is_redirect_alias and canonical_id in direct_anchors)
        ):
            canonical_id = uuid.uuid4().hex
            resolution = PetIdentityResolution(
                raw_pet_id=canonical_id,
                canonical_pet_id=canonical_id,
                source=PetIdentityResolutionSource.NEW,
            )
        if not resolution.is_redirect_alias:
            direct_anchors.add(canonical_id)
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
    redirects: dict[str, str],
    distance_threshold: float,
) -> PetIdentityResolution:
    vote_counter = Counter(
        pet_key_map[member.pet_key] for member in members if member.pet_key in pet_key_map
    )
    if vote_counter:
        raw_pet_id = max(
            vote_counter.items(),
            key=lambda item: (
                item[1],
                profiles[item[0]].updated_at if item[0] in profiles else "",
                item[0],
            ),
        )[0]
        canonical_pet_id = redirects.get(raw_pet_id, raw_pet_id)
        return PetIdentityResolution(
            raw_pet_id=raw_pet_id,
            canonical_pet_id=canonical_pet_id,
            source=(
                PetIdentityResolutionSource.REDIRECT_KEY
                if canonical_pet_id != raw_pet_id
                else PetIdentityResolutionSource.KEY
            ),
        )

    best_profile_id: str | None = None
    best_distance = float("inf")
    pet_species = _normalize_species_label(pet.species_label)
    for profile in profiles.values():
        is_redirect_alias = profile.pet_id in redirects
        if not is_redirect_alias and str(profile.profile_state or "unstable") != "stable":
            continue
        profile_species = _normalize_species_label(profile.species_label)
        if pet_species != profile_species:
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
        canonical_pet_id = redirects.get(best_profile_id, best_profile_id)
        return PetIdentityResolution(
            raw_pet_id=best_profile_id,
            canonical_pet_id=canonical_pet_id,
            source=(
                PetIdentityResolutionSource.REDIRECT_PROFILE
                if canonical_pet_id != best_profile_id
                else PetIdentityResolutionSource.PROFILE
            ),
        )
    new_pet_id = uuid.uuid4().hex
    return PetIdentityResolution(
        raw_pet_id=new_pet_id,
        canonical_pet_id=new_pet_id,
        source=PetIdentityResolutionSource.NEW,
    )


def _cluster_pet_detection_labels(
    detections: Sequence[PetDetectionRecord],
    *,
    distance_threshold: float,
) -> np.ndarray:
    if not detections:
        return np.empty((0,), dtype=np.int32)
    embeddings = np.stack([detection.embedding for detection in detections], axis=0).astype(
        np.float32
    )
    return _cluster_embeddings_bounded_single_link(
        embeddings,
        compatibility_keys=[
            (
                _normalize_species_label(detection.species_label),
                str(detection.embedding_pipeline_version or ""),
                int(detection.embedding_dim),
                int(detection.generation_id),
            )
            for detection in detections
        ],
        member_keys=[detection.detection_id for detection in detections],
        cannot_link_keys=[detection.asset_id for detection in detections],
        distance_threshold=distance_threshold,
    )


def _cluster_embeddings_bounded_single_link(
    embeddings: np.ndarray,
    *,
    compatibility_keys: Sequence[object],
    member_keys: Sequence[str] | None = None,
    cannot_link_keys: Sequence[str] | None = None,
    distance_threshold: float,
) -> np.ndarray:
    count = int(embeddings.shape[0])
    if count == 0:
        return np.empty((0,), dtype=np.int32)
    return _cluster_distance_matrix_bounded_single_link(
        cosine_distance_matrix(embeddings),
        compatibility_keys=compatibility_keys,
        member_keys=member_keys,
        cannot_link_keys=cannot_link_keys,
        link_threshold=distance_threshold,
        diameter_threshold=distance_threshold * PET_CLUSTER_DIAMETER_MULTIPLIER,
    )


def _cluster_distance_matrix_bounded_single_link(
    distance_matrix: np.ndarray,
    *,
    compatibility_keys: Sequence[object],
    link_threshold: float,
    diameter_threshold: float,
    member_keys: Sequence[str] | None = None,
    cannot_link_keys: Sequence[str] | None = None,
) -> np.ndarray:
    """Cluster nearest-neighbour links without allowing unbounded similarity chains."""

    count = int(distance_matrix.shape[0])
    if count == 0:
        return np.empty((0,), dtype=np.int32)
    if distance_matrix.shape != (count, count):
        raise ValueError("Pet distance matrix must be square.")
    if len(compatibility_keys) != count:
        raise ValueError("Pet compatibility key count must match the distance matrix.")
    stable_keys = tuple(member_keys or (str(index) for index in range(count)))
    if len(stable_keys) != count:
        raise ValueError("Pet member key count must match the distance matrix.")
    resolved_cannot_link_keys = tuple(cannot_link_keys or ("" for _ in range(count)))
    if len(resolved_cannot_link_keys) != count:
        raise ValueError("Pet cannot-link key count must match the distance matrix.")

    clusters: list[list[int]] = [[index] for index in range(count)]
    diameters: list[float] = [0.0] * count
    cannot_link_sets: list[set[str]] = [
        ({key} if key else set()) for key in resolved_cannot_link_keys
    ]

    while True:
        best_pair: tuple[int, int] | None = None
        best_key: tuple[float, float, tuple[str, ...], tuple[str, ...]] | None = None
        best_diameter = 0.0
        for left_index in range(len(clusters)):
            for right_index in range(left_index + 1, len(clusters)):
                left = clusters[left_index]
                right = clusters[right_index]
                if not _cluster_keys_compatible(left, right, compatibility_keys):
                    continue
                if cannot_link_sets[left_index] & cannot_link_sets[right_index]:
                    _LOGGER.debug(
                        "Pet clustering constraint hit: same_asset_cannot_link_hits=1"
                    )
                    continue
                cross_distances = [
                    float(distance_matrix[left_member, right_member])
                    for left_member in left
                    for right_member in right
                ]
                connection_distance = min(cross_distances)
                if connection_distance > link_threshold:
                    continue
                merged_diameter = max(
                    diameters[left_index],
                    diameters[right_index],
                    max(cross_distances),
                )
                if merged_diameter > diameter_threshold:
                    continue
                left_keys = tuple(sorted(stable_keys[index] for index in left))
                right_keys = tuple(sorted(stable_keys[index] for index in right))
                ordered_cluster_keys = tuple(sorted((left_keys, right_keys)))
                tie_key = (
                    connection_distance,
                    merged_diameter,
                    ordered_cluster_keys[0],
                    ordered_cluster_keys[1],
                )
                if best_key is None or tie_key < best_key:
                    best_key = tie_key
                    best_pair = (left_index, right_index)
                    best_diameter = merged_diameter
        if best_pair is None:
            break
        left_index, right_index = best_pair
        merged = sorted(clusters[left_index] + clusters[right_index])
        clusters[left_index] = merged
        diameters[left_index] = best_diameter
        cannot_link_sets[left_index].update(cannot_link_sets[right_index])
        del clusters[right_index]
        del diameters[right_index]
        del cannot_link_sets[right_index]
        ordering = sorted(
            range(len(clusters)),
            key=lambda cluster_index: tuple(
                sorted(stable_keys[index] for index in clusters[cluster_index])
            ),
        )
        clusters = [clusters[index] for index in ordering]
        diameters = [diameters[index] for index in ordering]
        cannot_link_sets = [cannot_link_sets[index] for index in ordering]

    labels = np.empty((count,), dtype=np.int32)
    for cluster_id, members in enumerate(clusters):
        for member in members:
            labels[member] = cluster_id
    return labels


def _cluster_keys_compatible(
    left: Sequence[int],
    right: Sequence[int],
    compatibility_keys: Sequence[object],
) -> bool:
    keys = {compatibility_keys[index] for index in [*left, *right]}
    return len(keys) == 1


def _detection_species_compatible(
    left: Sequence[PetDetectionRecord],
    right: Sequence[PetDetectionRecord],
) -> bool:
    labels = [
        *(_normalize_species_label(detection.species_label) for detection in left),
        *(_normalize_species_label(detection.species_label) for detection in right),
    ]
    return len(set(labels)) <= 1


def _detection_contracts_compatible(
    left: Sequence[PetDetectionRecord],
    right: Sequence[PetDetectionRecord],
) -> bool:
    contracts = {
        (
            str(detection.embedding_pipeline_version or ""),
            int(detection.embedding_dim),
            int(detection.generation_id),
        )
        for detection in [*left, *right]
    }
    return len(contracts) <= 1


def _detection_groups_compatible(
    left: Sequence[PetDetectionRecord],
    right: Sequence[PetDetectionRecord],
    *,
    distance_threshold: float,
) -> bool:
    if not _detection_species_compatible(left, right) or not _detection_contracts_compatible(
        left, right
    ):
        return False
    if _detection_groups_share_asset(left, right):
        return False
    if not left or not right:
        return True
    cross_distances = [
        cosine_distance(left_detection.embedding, right_detection.embedding)
        for left_detection in left
        for right_detection in right
    ]
    if min(cross_distances) > distance_threshold:
        return False
    members = [*left, *right]
    distance_matrix = cosine_distance_matrix(
        np.stack([member.embedding for member in members], axis=0)
    )
    return float(distance_matrix.max()) <= (distance_threshold * PET_CLUSTER_DIAMETER_MULTIPLIER)


def _detection_groups_share_asset(
    left: Sequence[PetDetectionRecord],
    right: Sequence[PetDetectionRecord],
) -> bool:
    left_assets = {detection.asset_id for detection in left if detection.asset_id}
    right_assets = {detection.asset_id for detection in right if detection.asset_id}
    return bool(left_assets & right_assets)


def _dominant_species_label(detections: Sequence[PetDetectionRecord]) -> str | None:
    counter = Counter(
        label
        for label in (_normalize_species_label(detection.species_label) for detection in detections)
        if label is not None
    )
    if not counter:
        return None
    return max(counter.items(), key=lambda item: (item[1], item[0]))[0]


def _normalize_species_label(value: object) -> str | None:
    if value is None:
        return None
    label = str(value).strip().lower()
    return label or None


def _pet_candidate_quality_score(
    *,
    confidence: float,
    relative_area_ratio: float,
) -> float:
    """Rank retained detections without turning relative size into a hard gate."""

    normalized_area = min(1.0, math.sqrt(max(0.0, float(relative_area_ratio))))
    return float(0.75 * max(0.0, min(1.0, confidence)) + 0.25 * normalized_area)


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
        score_threshold: float = 0.30,
        allow_model_download: bool = True,
        enable_tiled_detection: bool = True,
        tile_scan_min_confidence: float | None = None,
        tile_species: frozenset[str] = SUPPORTED_DEFAULT_SPECIES,
        execution_providers: Sequence[str] | None = None,
    ) -> None:
        self._model_path = Path(model_path)
        self._score_threshold = float(score_threshold)
        self._enable_tiled_detection = bool(enable_tiled_detection)
        self._tile_scan_min_confidence = (
            self._score_threshold
            if tile_scan_min_confidence is None
            else float(tile_scan_min_confidence)
        )
        self._tile_species = frozenset(tile_species)
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise PetRuntimeUnavailableError(
                "Pet scanning unavailable: missing onnxruntime. Install the optional "
                'Pets AI runtime with: pip install -e ".[pets-ai]"'
            ) from exc
        self._model_path = ensure_pet_detector_model(
            self._model_path,
            allow_model_download=allow_model_download,
        )
        try:
            providers = list(execution_providers or _resolve_execution_providers(ort))
            self._session = ort.InferenceSession(str(self._model_path), providers=providers)
            self._input_name = self._session.get_inputs()[0].name
            shape = self._session.get_inputs()[0].shape
            self._input_size = _input_size_from_shape(shape)
            _validate_yolox_session_contract(self._session)
        except Exception as exc:  # noqa: BLE001 - provider failures vary by backend
            if isinstance(exc, PetPipelineInvariantError):
                raise
            raise PetModelUnavailableError(
                "Pet scanning unavailable: failed to initialize YOLOX detector model at "
                f"{self._model_path} ({_error_reason(exc)}). Check the model cache, "
                "disable unsupported execution providers, or reinstall the Pets AI runtime."
            ) from exc

    def detect(self, image) -> list[_DetectedPetBox]:
        boxes = self._detect_single_image(image)
        if not self._enable_tiled_detection:
            return _dedupe_supported_species_boxes(boxes)

        image_width, image_height = image.size
        for crop_box in _select_uncovered_tile_regions(
            image_width,
            image_height,
            boxes,
            max_regions=4,
        ):
            left, top, *_ = crop_box
            crop = image.crop(crop_box)
            boxes.extend(self._detect_single_image(crop, offset=(left, top)))
        return _dedupe_supported_species_boxes(boxes)

    def _detect_single_image(
        self,
        image,
        *,
        offset: tuple[int, int] = (0, 0),
    ) -> list[_DetectedPetBox]:
        image_width, image_height = image.size
        input_width, input_height = self._input_size
        preprocessed = _preprocess_yolox(
            image,
            input_width=input_width,
            input_height=input_height,
        )
        try:
            outputs = self._session.run(None, {self._input_name: preprocessed.tensor})
        except Exception as exc:
            raise PetInferenceError(f"Pet detector inference failed: {_error_reason(exc)}") from exc
        predictions = _flatten_predictions(outputs)
        boxes: list[_DetectedPetBox] = []
        for x0, y0, x1, y1, confidence, class_id in _decode_yolox_predictions(
            predictions,
            input_size=self._input_size,
        ):
            species = COCO_ANIMAL_LABELS.get(int(class_id))
            if species is None or confidence < self._score_threshold:
                continue
            bbox = _map_yolox_box_to_source(
                (x0, y0, x1, y1),
                preprocessed=preprocessed,
                image_width=image_width,
                image_height=image_height,
                offset=offset,
            )
            boxes.append(
                _DetectedPetBox(
                    bbox=bbox,
                    confidence=float(confidence),
                    species_label=species,
                )
            )
        return boxes

    def _has_tile_species_box(self, boxes: list[_DetectedPetBox]) -> bool:
        return any(
            box.species_label in self._tile_species
            and box.confidence >= self._tile_scan_min_confidence
            for box in boxes
        )


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
            raise PetRuntimeUnavailableError(
                "Pet scanning unavailable: missing torch for DINOv2 pet embeddings. "
                'Install the optional Pets AI runtime with: pip install -e ".[pets-ai]"'
            ) from exc
        self._torch = torch
        self._device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        model_path = self._model_dir / f"{model_name}.pt"
        if model_path.is_file():
            _validate_dinov2_cache_metadata(model_path, model_name=model_name)
            self._model = torch.jit.load(str(model_path), map_location=self._device)
        elif allow_model_download:
            self._model = self._download_dinov2_model(model_path)
        else:
            raise PetModelUnavailableError(
                "Pet scanning unavailable: missing DINOv2 TorchScript model at "
                f"{model_path}. Set IPHOTO_PET_MODEL_DIR or enable pet model downloads."
            )
        self._model.eval()

    def embed(self, image) -> np.ndarray:
        tensor = image_to_chw_float(image, (224, 224))
        torch = self._torch
        try:
            with torch.no_grad():
                input_tensor = torch.from_numpy(tensor).to(self._device)
                output = self._model(input_tensor)
                if isinstance(output, (list, tuple)):
                    output = output[0]
                vector = output.detach().cpu().numpy().reshape(-1)
        except Exception as exc:
            raise PetInferenceError(
                f"Pet embedding inference failed: {_error_reason(exc)}"
            ) from exc
        expected_dimension = int(_EMBEDDER_MANIFEST["output_shape"][-1])
        if vector.size != expected_dimension:
            raise PetPipelineInvariantError(
                "Pet scanning unavailable: DINOv2 output contract mismatch "
                f"({vector.size} != {expected_dimension})."
            )
        return normalize_vector(vector.astype(np.float32))

    def _download_dinov2_model(self, model_path: Path):
        try:
            return self._build_dinov2_cache(model_path)
        except _ModelStoragePermissionError as exc:
            fallback = _model_storage_fallback_path(model_path)
            if fallback is None:
                raise PetModelUnavailableError(
                    "Pet scanning unavailable: model storage is not writable at "
                    f"{model_path.parent}."
                ) from exc
            _LOGGER.warning(
                "Falling back to the user Pets model cache after %s was not writable",
                model_path.parent,
                exc_info=exc,
            )
            try:
                return self._build_dinov2_cache(fallback)
            except _ModelStoragePermissionError as fallback_exc:
                raise PetModelUnavailableError(
                    "Pet scanning unavailable: model storage is not writable at "
                    f"{fallback.parent}."
                ) from fallback_exc

    def _build_dinov2_cache(self, model_path: Path):
        try:
            _install_certifi_environment()
            try:
                model_path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                _raise_if_model_storage_error(exc, model_path.parent)
            try:
                temp_context = tempfile.TemporaryDirectory(
                    prefix="iphoto-dinov2-build-",
                    dir=model_path.parent,
                )
            except OSError as exc:
                _raise_if_model_storage_error(exc, model_path.parent)
            with temp_context as temp_dir:
                checkpoint = Path(temp_dir) / "dinov2_vits14_pretrain.pth"
                candidate = Path(temp_dir) / model_path.name
                metadata_path = _dinov2_metadata_path(candidate)
                _download_file(
                    _DINO_WEIGHTS_URL,
                    checkpoint,
                    label="DINOv2 checkpoint",
                    expected_sha256=_DINO_WEIGHTS_SHA256,
                    max_bytes=_DINO_WEIGHTS_SIZE,
                    exact_size=_DINO_WEIGHTS_SIZE,
                )
                source = (
                    f"{_EMBEDDER_MANIFEST['source_repository']}:"
                    f"{_DINO_SOURCE_REVISION}"
                )
                model = self._torch.hub.load(
                    source,
                    self._model_name,
                    source="github",
                    trust_repo=True,
                    weights=str(checkpoint),
                ).eval().cpu()
                example = self._torch.randn(
                    tuple(_EMBEDDER_MANIFEST["input_shape"]),
                    dtype=self._torch.float32,
                )
                with self._torch.no_grad():
                    eager_output = model(example)
                    traced = self._torch.jit.trace(model, example, strict=False)
                    traced.save(str(candidate))
                    scripted = self._torch.jit.load(str(candidate), map_location="cpu").eval()
                    scripted_output = scripted(example)
                if isinstance(eager_output, (list, tuple)):
                    eager_output = eager_output[0]
                if isinstance(scripted_output, (list, tuple)):
                    scripted_output = scripted_output[0]
                output_shape = tuple(_EMBEDDER_MANIFEST["output_shape"])
                actual_shape = tuple(scripted_output.shape)
                if actual_shape != output_shape:
                    raise RuntimeError(
                        f"DINOv2 output shape mismatch: {actual_shape} != {output_shape}"
                    )
                self._torch.testing.assert_close(
                    scripted_output,
                    eager_output,
                    rtol=1e-4,
                    atol=1e-5,
                )
                metadata = {
                    "artifact_kind": "derived_checkpoint_cache",
                    "model_name": self._model_name,
                    "source_repository": str(_EMBEDDER_MANIFEST["source_repository"]),
                    "source_revision": _DINO_SOURCE_REVISION,
                    "weights_sha256": _DINO_WEIGHTS_SHA256,
                    "weights_size": _DINO_WEIGHTS_SIZE,
                    "derived_torchscript_sha256": _file_sha256(candidate).lower(),
                    "derived_torchscript_size": candidate.stat().st_size,
                    "input_shape": list(_EMBEDDER_MANIFEST["input_shape"]),
                    "output_shape": list(output_shape),
                }
                try:
                    metadata_path.write_text(
                        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )
                except OSError as exc:
                    _raise_if_model_storage_error(exc, metadata_path)
                final_metadata_path = _dinov2_metadata_path(model_path)
                try:
                    candidate.replace(model_path)
                    metadata_path.replace(final_metadata_path)
                except OSError as exc:
                    _raise_if_model_storage_error(exc, model_path.parent)
                _validate_dinov2_cache_metadata(model_path, model_name=self._model_name)
                loaded = self._torch.jit.load(str(model_path), map_location=self._device)
            loaded.eval()
            loaded.to(self._device)
            return loaded
        except _ModelStoragePermissionError:
            raise
        except Exception as exc:
            model_path.unlink(missing_ok=True)
            _dinov2_metadata_path(model_path).unlink(missing_ok=True)
            raise PetModelUnavailableError(
                "Pet scanning unavailable: failed to build the verified DINOv2 "
                f"model cache ({_error_reason(exc)})."
            ) from exc


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


def _validate_yolox_session_contract(session) -> None:
    inputs = session.get_inputs()
    outputs = session.get_outputs()
    if len(inputs) != 1 or len(inputs[0].shape) != 4:
        raise RuntimeError("Pet scanning unavailable: YOLOX input contract is invalid.")
    if inputs[0].shape[1] not in {3, "3"}:
        raise RuntimeError("Pet scanning unavailable: YOLOX input must have three channels.")
    concrete_input = tuple(
        int(value) if isinstance(value, (int, np.integer)) else None for value in inputs[0].shape
    )
    expected_input = tuple(int(value) for value in _DETECTOR_MANIFEST["input"]["shape"])
    if concrete_input != expected_input:
        raise RuntimeError(
            "Pet scanning unavailable: YOLOX input shape does not match the manifest."
        )
    if not outputs or len(outputs[0].shape) < 2:
        raise RuntimeError("Pet scanning unavailable: YOLOX output contract is invalid.")
    last_output_dim = outputs[0].shape[-1]
    expected_output = tuple(int(value) for value in _DETECTOR_MANIFEST["output_shape"])
    concrete_output = tuple(
        int(value) if isinstance(value, (int, np.integer)) else None for value in outputs[0].shape
    )
    if isinstance(last_output_dim, (int, np.integer)) and concrete_output != expected_output:
        raise RuntimeError(
            "Pet scanning unavailable: YOLOX output shape does not match the manifest."
        )


def _preprocess_yolox(
    image,
    *,
    input_width: int,
    input_height: int,
) -> _YoloxPreprocessResult:
    image_width, image_height = image.size
    resize_ratio = min(
        input_width / float(max(1, image_width)),
        input_height / float(max(1, image_height)),
    )
    resized_width = max(1, int(image_width * resize_ratio))
    resized_height = max(1, int(image_height * resize_ratio))
    resized = image.resize((resized_width, resized_height), Image.Resampling.BILINEAR)
    canvas = Image.new("RGB", (input_width, input_height), (114, 114, 114))
    canvas.paste(resized, (0, 0))
    array = np.asarray(canvas, dtype=np.float32)
    # YOLOX 0.1.1rc0 deployment weights consume OpenCV-style BGR bytes.
    # This release explicitly removed legacy mean/std normalization.
    array = array[:, :, ::-1]
    array = np.transpose(array, (2, 0, 1))[None, :, :, :]
    return _YoloxPreprocessResult(
        tensor=np.ascontiguousarray(array),
        resize_ratio=float(resize_ratio),
    )


def _map_yolox_box_to_source(
    box: tuple[float, float, float, float],
    *,
    preprocessed: _YoloxPreprocessResult,
    image_width: int,
    image_height: int,
    offset: tuple[int, int] = (0, 0),
) -> tuple[int, int, int, int]:
    ratio = max(float(preprocessed.resize_ratio), 1e-6)
    offset_x, offset_y = offset
    x0, y0, x1, y1 = box
    left = round((x0 - preprocessed.pad_left) / ratio)
    top = round((y0 - preprocessed.pad_top) / ratio)
    right = round((x1 - preprocessed.pad_left) / ratio)
    bottom = round((y1 - preprocessed.pad_top) / ratio)
    left = max(0, min(left, image_width - 1))
    top = max(0, min(top, image_height - 1))
    right = max(left + 1, min(right, image_width))
    bottom = max(top + 1, min(bottom, image_height))
    return (
        left + offset_x,
        top + offset_y,
        max(1, right - left),
        max(1, bottom - top),
    )


def _tile_scan_regions(image_width: int, image_height: int) -> list[tuple[int, int, int, int]]:
    width = max(1, int(image_width))
    height = max(1, int(image_height))

    def region(left: float, top: float, right: float, bottom: float) -> tuple[int, int, int, int]:
        x0 = max(0, min(round(width * left), width - 1))
        y0 = max(0, min(round(height * top), height - 1))
        x1 = max(x0 + 1, min(round(width * right), width))
        y1 = max(y0 + 1, min(round(height * bottom), height))
        return x0, y0, x1, y1

    candidates = [
        region(0.0, 0.0, 0.70, 1.0),
        region(0.30, 0.0, 1.0, 1.0),
        region(0.0, 0.0, 1.0, 0.70),
        region(0.0, 0.30, 1.0, 1.0),
        region(0.0, 0.0, 0.65, 0.65),
        region(0.35, 0.0, 1.0, 0.65),
        region(0.15, 0.15, 0.85, 0.85),
    ]
    return list(dict.fromkeys(candidates))


def _select_uncovered_tile_regions(
    image_width: int,
    image_height: int,
    boxes: Sequence[_DetectedPetBox],
    *,
    max_regions: int,
) -> list[tuple[int, int, int, int]]:
    """Choose a bounded set of tiles by area not covered by full-frame pets."""

    supported = [box for box in boxes if box.species_label in SUPPORTED_DEFAULT_SPECIES]
    ranked: list[tuple[float, tuple[int, int, int, int]]] = []
    for region in _tile_scan_regions(image_width, image_height):
        x0, y0, x1, y1 = region
        tile_box = (x0, y0, x1 - x0, y1 - y0)
        tile_area = max(1, tile_box[2] * tile_box[3])
        covered = min(
            tile_area,
            sum(_bbox_intersection_area(tile_box, box.bbox) for box in supported),
        )
        uncovered_ratio = 1.0 - (covered / float(tile_area))
        if uncovered_ratio >= 0.20:
            ranked.append((uncovered_ratio, region))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [region for _, region in ranked[: max(0, int(max_regions))]]


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
    return [_decode_prediction(prediction) for prediction in decoded if prediction.shape[0] >= 6]


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


def _dedupe_supported_species_boxes(
    boxes: list[_DetectedPetBox],
    *,
    threshold: float = PET_PET_IOU_THRESHOLD,
    smaller_box_coverage_threshold: float = PET_PET_SMALLER_BOX_COVERAGE_THRESHOLD,
    normalized_center_distance_threshold: float = PET_PET_NORMALIZED_CENTER_DISTANCE_THRESHOLD,
    cross_species_mutual_coverage_threshold: float = (
        PET_PET_CROSS_SPECIES_MUTUAL_COVERAGE_THRESHOLD
    ),
    cross_species_threshold: float = 0.90,
    cross_species_score_margin: float = 0.25,
) -> list[_DetectedPetBox]:
    selected: list[_DetectedPetBox] = []
    for box in sorted(
        boxes,
        key=lambda item: (item.quality_score, item.confidence),
        reverse=True,
    ):
        suppress = False
        for existing in selected:
            overlap = _bbox_iou(existing.bbox, box.bbox)
            existing_box_coverage, candidate_box_coverage = _bbox_pair_coverages(
                existing.bbox,
                box.bbox,
            )
            smaller_box_coverage = max(existing_box_coverage, candidate_box_coverage)
            normalized_center_distance = _bbox_normalized_center_distance(
                existing.bbox,
                box.bbox,
            )
            reason = ""
            if existing.species_label == box.species_label:
                if overlap >= threshold:
                    reason = "same_species_iou"
                elif (
                    smaller_box_coverage >= smaller_box_coverage_threshold
                    and normalized_center_distance <= normalized_center_distance_threshold
                ):
                    reason = "same_species_containment"
            elif (
                existing.species_label in SUPPORTED_DEFAULT_SPECIES
                and box.species_label in SUPPORTED_DEFAULT_SPECIES
                and existing_box_coverage >= cross_species_mutual_coverage_threshold
                and candidate_box_coverage >= cross_species_mutual_coverage_threshold
            ):
                reason = "cross_species_mutual_coverage"
            elif (
                existing.species_label != box.species_label
                and overlap >= cross_species_threshold
                and existing.confidence - box.confidence >= cross_species_score_margin
            ):
                reason = "cross_species_iou"

            if reason:
                _LOGGER.debug(
                    "Suppressed pet box: reason=%s species=%s candidate_confidence=%.3f "
                    "candidate_bbox=%s kept_species=%s kept_confidence=%.3f kept_bbox=%s "
                    "iou=%.3f smaller_box_coverage=%.3f kept_box_coverage=%.3f "
                    "candidate_box_coverage=%.3f "
                    "normalized_center_distance=%.3f",
                    reason,
                    box.species_label,
                    box.confidence,
                    box.bbox,
                    existing.species_label,
                    existing.confidence,
                    existing.bbox,
                    overlap,
                    smaller_box_coverage,
                    existing_box_coverage,
                    candidate_box_coverage,
                    normalized_center_distance,
                )
                suppress = True
                break
        if suppress:
            continue
        selected.append(box)
    return selected


def _bbox_iou(left: tuple[int, int, int, int], right: tuple[int, int, int, int]) -> float:
    intersection = _bbox_intersection_area(left, right)
    left_area = max(0, left[2]) * max(0, left[3])
    right_area = max(0, right[2]) * max(0, right[3])
    union = left_area + right_area - intersection
    if union <= 0:
        return 0.0
    return intersection / float(union)


def _bbox_pair_coverages(
    left: tuple[int, int, int, int],
    right: tuple[int, int, int, int],
) -> tuple[float, float]:
    left_area = max(0, left[2]) * max(0, left[3])
    right_area = max(0, right[2]) * max(0, right[3])
    if left_area <= 0 or right_area <= 0:
        return 0.0, 0.0
    intersection = _bbox_intersection_area(left, right)
    return intersection / float(left_area), intersection / float(right_area)


def _bbox_normalized_center_distance(
    left: tuple[int, int, int, int],
    right: tuple[int, int, int, int],
) -> float:
    left_area = max(0, left[2]) * max(0, left[3])
    right_area = max(0, right[2]) * max(0, right[3])
    smaller_area = min(left_area, right_area)
    if smaller_area <= 0:
        return float("inf")
    left_center = (left[0] + left[2] / 2.0, left[1] + left[3] / 2.0)
    right_center = (right[0] + right[2] / 2.0, right[1] + right[3] / 2.0)
    return math.hypot(
        left_center[0] - right_center[0],
        left_center[1] - right_center[1],
    ) / math.sqrt(smaller_area)


def _bbox_intersection_area(
    left: tuple[int, int, int, int],
    right: tuple[int, int, int, int],
) -> int:
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
    return inter_width * inter_height


def _pet_box_overlaps_people_boxes(
    pet_box: tuple[int, int, int, int],
    people_boxes: Sequence[tuple[int, int, int, int]],
    *,
    image_dimensions: tuple[int, int] | None = None,
    iou_threshold: float = PET_PEOPLE_IOU_THRESHOLD,
    smaller_box_coverage_threshold: float = PET_PEOPLE_SMALLER_BOX_COVERAGE_THRESHOLD,
) -> bool:
    """Return whether a pet detection conflicts with a People face region."""

    return _pet_people_overlap_decision(
        pet_box,
        people_boxes,
        image_dimensions=image_dimensions,
        iou_threshold=iou_threshold,
        smaller_box_coverage_threshold=smaller_box_coverage_threshold,
    ).suppressed


def _pet_people_overlap_decision(
    pet_box: tuple[int, int, int, int],
    people_boxes: Sequence[tuple[int, int, int, int]],
    *,
    image_dimensions: tuple[int, int] | None = None,
    iou_threshold: float = PET_PEOPLE_IOU_THRESHOLD,
    smaller_box_coverage_threshold: float = PET_PEOPLE_SMALLER_BOX_COVERAGE_THRESHOLD,
    larger_pet_ratio: float = PET_PEOPLE_LARGER_PET_RATIO,
    mural_image_coverage_threshold: float = PET_PEOPLE_MURAL_IMAGE_COVERAGE_THRESHOLD,
) -> _PetPeopleOverlapDecision:
    """Classify face/pet overlap without losing pets held by people.

    A pet body may legitimately contain a much smaller human face.  Preserve
    that candidate unless it also spans most of the image, which is the shape
    produced by the known wall-mural false-positive regression.
    """

    pet_area = max(0, pet_box[2]) * max(0, pet_box[3])
    if pet_area <= 0:
        return _PetPeopleOverlapDecision(False)
    image_area = 0
    if image_dimensions is not None:
        image_area = max(0, int(image_dimensions[0])) * max(0, int(image_dimensions[1]))
    pet_image_coverage = pet_area / float(image_area) if image_area else 0.0
    for people_box in people_boxes:
        people_area = max(0, people_box[2]) * max(0, people_box[3])
        if people_area <= 0:
            continue
        intersection = _bbox_intersection_area(pet_box, people_box)
        if intersection <= 0:
            continue
        pet_to_face_ratio = pet_area / float(people_area)
        preserve_larger_pet = (
            image_area > 0
            and pet_to_face_ratio > larger_pet_ratio
            and pet_image_coverage < mural_image_coverage_threshold
        )
        if preserve_larger_pet:
            continue
        if _bbox_iou(pet_box, people_box) >= iou_threshold:
            return _PetPeopleOverlapDecision(
                True,
                "iou",
                pet_to_face_ratio,
                pet_image_coverage,
            )
        smaller_area = min(pet_area, people_area)
        if intersection / float(smaller_area) >= smaller_box_coverage_threshold:
            return _PetPeopleOverlapDecision(
                True,
                "smaller_box_coverage",
                pet_to_face_ratio,
                pet_image_coverage,
            )
    return _PetPeopleOverlapDecision(False, pet_image_coverage=pet_image_coverage)


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
    custom_url = str(model_url or os.environ.get(PET_DETECTOR_MODEL_URL_ENV) or "").strip()
    expected_sha256 = (
        str(os.environ.get(PET_DETECTOR_MODEL_SHA256_ENV) or "").strip().lower()
        if custom_url
        else DEFAULT_PET_DETECTOR_MODEL_SHA256
    )
    if custom_url and not expected_sha256:
        raise RuntimeError(
            "Pet scanning unavailable: a custom detector URL requires "
            f"{PET_DETECTOR_MODEL_SHA256_ENV}."
        )
    if target.is_file():
        try:
            _validate_downloaded_file(
                target,
                label="YOLOX pet detector model",
                expected_sha256=expected_sha256,
                max_bytes=DEFAULT_PET_DETECTOR_MODEL_MAX_BYTES,
            )
            return target
        except OSError as exc:
            _raise_if_model_storage_error(exc, target)
    if not allow_model_download:
        raise RuntimeError(
            "Pet scanning unavailable: missing YOLOX model at "
            f"{target}. Set IPHOTO_PET_MODEL_DIR or enable pet model downloads."
        )

    url = str(custom_url or DEFAULT_PET_DETECTOR_MODEL_URL).strip()
    if not url:
        raise RuntimeError(
            "Pet scanning unavailable: missing YOLOX model at "
            f"{target} and no pet detector download URL is configured."
        )
    try:
        return _download_file(
            url,
            target,
            label="YOLOX pet detector model",
            expected_sha256=expected_sha256,
            max_bytes=DEFAULT_PET_DETECTOR_MODEL_MAX_BYTES,
        )
    except _ModelStoragePermissionError as exc:
        fallback = _model_storage_fallback_path(target)
        if fallback is None:
            raise RuntimeError(
                "Pet scanning unavailable: model storage is not writable at "
                f"{target.parent}."
            ) from exc
        _LOGGER.warning(
            "Falling back to %s after model storage failure",
            fallback.parent,
            exc_info=exc,
        )
        try:
            return _download_file(
                url,
                fallback,
                label="YOLOX pet detector model",
                expected_sha256=expected_sha256,
                max_bytes=DEFAULT_PET_DETECTOR_MODEL_MAX_BYTES,
            )
        except _ModelStoragePermissionError as fallback_exc:
            raise RuntimeError(
                "Pet scanning unavailable: model storage is not writable at "
                f"{fallback.parent}."
            ) from fallback_exc


def default_pet_model_dir() -> Path:
    return user_pet_model_cache_dir()


def bundled_pet_model_dir() -> Path:
    package_root = Path(__file__).resolve().parents[2]
    return package_root / "extension" / "models" / "pets"


def user_pet_model_cache_dir() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "iPhoto" / "models" / "pets"


def pet_model_search_roots() -> tuple[Path, ...]:
    override = pet_model_override_dir()
    if override is not None:
        return (override,)
    return (bundled_pet_model_dir(), user_pet_model_cache_dir())


def pet_model_override_dir() -> Path | None:
    override = str(os.environ.get(IPHOTO_PET_MODEL_DIR_ENV) or "").strip()
    if not override:
        return None
    return Path(override).expanduser()


def _is_packaged_macos_app_path(path: Path) -> bool:
    if sys.platform != "darwin":
        return False
    parts = path.parts
    return any(
        part.lower().endswith(".app")
        and index + 1 < len(parts)
        and parts[index + 1] == "Contents"
        for index, part in enumerate(parts)
    )


def _directory_is_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".iphoto-write-probe-{uuid.uuid4().hex}"
        with probe.open("xb"):
            pass
        probe.unlink(missing_ok=True)
        return True
    except OSError as exc:
        if exc.errno in _MODEL_STORAGE_ERRNOS:
            return False
        raise


def pet_model_install_root() -> Path:
    override = pet_model_override_dir()
    if override is not None:
        return override

    bundled = bundled_pet_model_dir()
    if not _is_packaged_macos_app_path(bundled) and _directory_is_writable(bundled):
        return bundled
    return user_pet_model_cache_dir()


def _model_storage_fallback_path(path: Path) -> Path | None:
    if pet_model_override_dir() is not None:
        return None
    target = Path(path)
    bundled = bundled_pet_model_dir()
    try:
        relative = target.relative_to(bundled)
    except ValueError:
        return None
    fallback = user_pet_model_cache_dir() / relative
    if fallback == target:
        return None
    return fallback


def resolve_pet_model_path(relative_path: Path, *, directory: bool = False) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Pet model path must be relative to a configured model root.")
    override = pet_model_override_dir()
    user_cache = user_pet_model_cache_dir()
    bundled_invalid = False
    for root in pet_model_search_roots():
        candidate = root / relative
        exists = candidate.is_dir() if directory else candidate.is_file()
        if not exists:
            continue
        try:
            if directory:
                model_name = relative.name
                model_path = candidate / f"{model_name}.pt"
                if not model_path.is_file():
                    raise RuntimeError("DINOv2 model file is missing")
                _validate_dinov2_cache_metadata(model_path, model_name=model_name)
            else:
                _validate_downloaded_file(
                    candidate,
                    label="YOLOX pet detector model",
                    expected_sha256=(
                        str(
                            os.environ.get(PET_DETECTOR_MODEL_SHA256_ENV)
                            or DEFAULT_PET_DETECTOR_MODEL_SHA256
                        ).lower()
                    ),
                    max_bytes=DEFAULT_PET_DETECTOR_MODEL_MAX_BYTES,
                )
            return candidate
        except (OSError, RuntimeError) as exc:
            if override is not None and root == override:
                raise RuntimeError(
                    f"Pet scanning unavailable: invalid model override artifact at {candidate}."
                ) from exc
            if root == user_cache:
                model_path = candidate / f"{relative.name}.pt" if directory else candidate
                try:
                    model_path.unlink(missing_ok=True)
                    if directory:
                        _dinov2_metadata_path(model_path).unlink(missing_ok=True)
                except OSError:
                    _LOGGER.warning(
                        "Failed to quarantine invalid Pets model cache %s",
                        candidate,
                        exc_info=True,
                    )
            if root == bundled_pet_model_dir():
                bundled_invalid = True
    if override is not None:
        return override / relative
    if bundled_invalid:
        return user_cache / relative
    return pet_model_install_root() / relative


def _download_file(
    url: str,
    destination: Path,
    *,
    label: str,
    expected_sha256: str,
    max_bytes: int,
    exact_size: int | None = None,
) -> Path:
    destination = Path(destination)
    if urlparse(url).scheme.lower() != "https":
        raise RuntimeError(f"Pet scanning unavailable: {label} URL must use HTTPS.")
    try:
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            _raise_if_model_storage_error(exc, destination.parent)
        try:
            temp_context = tempfile.TemporaryDirectory(
                prefix="iphoto-pet-model-",
                dir=destination.parent,
            )
        except OSError as exc:
            _raise_if_model_storage_error(exc, destination.parent)
        with temp_context as tmp_dir:
            tmp_path = Path(tmp_dir) / destination.name
            try:
                handle = tmp_path.open("wb")
            except OSError as exc:
                _raise_if_model_storage_error(exc, tmp_path)
            with (
                request.urlopen(  # noqa: S310
                    url,
                    timeout=_DOWNLOAD_TIMEOUT_SECONDS,
                    context=_download_ssl_context(url),
                ) as response,
                handle,
            ):
                total = 0
                while True:
                    chunk = response.read(_DOWNLOAD_CHUNK_SIZE)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > int(max_bytes):
                        raise RuntimeError(f"Downloaded {label} exceeds its size limit.")
                    try:
                        handle.write(chunk)
                    except OSError as exc:
                        _raise_if_model_storage_error(exc, tmp_path)
            try:
                _validate_downloaded_file(
                    tmp_path,
                    label=label,
                    expected_sha256=expected_sha256,
                    max_bytes=max_bytes,
                    exact_size=exact_size,
                )
            except OSError as exc:
                _raise_if_model_storage_error(exc, tmp_path)
            try:
                tmp_path.replace(destination)
            except OSError as exc:
                _raise_if_model_storage_error(exc, destination)
            return destination
    except _ModelStoragePermissionError:
        raise
    except TimeoutError as exc:
        raise RuntimeError(
            f"Pet scanning unavailable: downloading {label} timed out. "
            "Check your network connection or install the model manually."
        ) from exc
    except OSError as exc:
        raise RuntimeError(
            f"Pet scanning unavailable: failed to download {label} from {url} "
            f"({_error_reason(exc)}). Check your network connection, set "
            f"{PET_DETECTOR_MODEL_URL_ENV}, or install the model manually."
        ) from exc
    except Exception as exc:
        if isinstance(exc, RuntimeError) and str(exc).startswith("Pet scanning unavailable:"):
            raise
        raise RuntimeError(
            f"Pet scanning unavailable: failed to download {label} from {url} "
            f"({_error_reason(exc)}). Check your network connection, set "
            f"{PET_DETECTOR_MODEL_URL_ENV}, or install the model manually."
        ) from exc


def _validate_downloaded_file(
    path: Path,
    *,
    label: str,
    expected_sha256: str,
    max_bytes: int,
    exact_size: int | None = None,
) -> None:
    size = Path(path).stat().st_size
    if size <= 0:
        raise RuntimeError(f"Downloaded {label} is empty.")
    if size > int(max_bytes):
        raise RuntimeError(f"Downloaded {label} exceeds its size limit.")
    if exact_size is not None and size != int(exact_size):
        raise RuntimeError(f"Downloaded {label} has the wrong file size.")
    digest = _file_sha256(path)
    if not expected_sha256 or digest != expected_sha256.lower():
        raise RuntimeError(f"Downloaded {label} failed SHA-256 verification.")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(_DOWNLOAD_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dinov2_metadata_path(model_path: Path) -> Path:
    return Path(model_path).with_suffix(f"{Path(model_path).suffix}.metadata.json")


def _validate_dinov2_cache_metadata(model_path: Path, *, model_name: str) -> None:
    metadata_path = _dinov2_metadata_path(model_path)
    if not metadata_path.is_file():
        raise RuntimeError(
            "Pet scanning unavailable: DINOv2 TorchScript metadata is missing for "
            f"{model_path}. Remove the incomplete cache so it can be rebuilt."
        )
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Pet scanning unavailable: invalid DINOv2 metadata at {metadata_path}."
        ) from exc
    expected = {
        "model_name": model_name,
        "source_repository": _EMBEDDER_MANIFEST["source_repository"],
        "source_revision": _DINO_SOURCE_REVISION,
        "input_shape": _EMBEDDER_MANIFEST["input_shape"],
        "output_shape": _EMBEDDER_MANIFEST["output_shape"],
    }
    if any(metadata.get(key) != value for key, value in expected.items()):
        raise RuntimeError(
            f"Pet scanning unavailable: DINOv2 metadata contract mismatch at {metadata_path}."
        )
    manifest_matches = (
        metadata.get("torchscript_sha256")
        == str(_EMBEDDER_MANIFEST["torchscript_sha256"]).lower()
        and int(metadata.get("torchscript_size") or -1)
        == int(_EMBEDDER_MANIFEST["torchscript_size"])
    )
    derived_matches = (
        metadata.get("artifact_kind") == "derived_checkpoint_cache"
        and metadata.get("weights_sha256") == _DINO_WEIGHTS_SHA256
        and int(metadata.get("weights_size") or -1) == _DINO_WEIGHTS_SIZE
        and isinstance(metadata.get("derived_torchscript_sha256"), str)
        and len(metadata.get("derived_torchscript_sha256")) == 64
        and isinstance(metadata.get("derived_torchscript_size"), int)
        and int(metadata.get("derived_torchscript_size")) > 0
    )
    if manifest_matches:
        size = int(metadata["torchscript_size"])
        digest = str(metadata["torchscript_sha256"])
    elif derived_matches:
        size = int(metadata["derived_torchscript_size"])
        digest = str(metadata["derived_torchscript_sha256"]).lower()
    else:
        raise RuntimeError(
            f"Pet scanning unavailable: DINOv2 cache integrity contract failed for {model_path}."
        )
    size_matches = size == model_path.stat().st_size
    hash_matches = digest.lower() == _file_sha256(model_path)
    if not size_matches or not hash_matches:
        raise RuntimeError(
            f"Pet scanning unavailable: DINOv2 cache integrity check failed for {model_path}."
        )


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

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from urllib import request

import numpy as np
import pytest
from PIL import Image

from iPhoto.pets.pipeline import (
    DEFAULT_PET_DETECTOR_MODEL_MAX_BYTES,
    DEFAULT_PET_DETECTOR_MODEL_SHA256,
    DEFAULT_PET_DETECTOR_MODEL_URL,
    _bbox_iou,
    _download_ssl_context,
    _preprocess_yolox,
    _YoloxOnnxPetDetector,
)

MODEL_RELATIVE_PATH = Path(
    "src/extension/models/pets/detector/yolox_nano_coco.onnx"
)
DOG_IMAGE_URL = (
    "https://raw.githubusercontent.com/Megvii-BaseDetection/YOLOX/main/assets/dog.jpg"
)
DOG_IMAGE_SHA256 = "5a9522051c3cec2bbd2f6323fccba32e8fbf3ddcc2b3e2fd46b04c720bc6f866"


pytestmark = pytest.mark.pets_model_contract


def test_official_yolox_raw_bgr_model_contract() -> None:
    if os.environ.get("IPHOTO_RUN_PETS_MODEL_CONTRACT") != "1":
        pytest.skip("real model contract is run by the pets-model-contract PR job")

    repository_root = Path(__file__).resolve().parents[2]
    model_path = _contract_model_path(repository_root)
    assert _sha256(model_path) == DEFAULT_PET_DETECTOR_MODEL_SHA256

    image_path = _cached_dog_image()
    assert _sha256(image_path) == DOG_IMAGE_SHA256
    image = Image.open(image_path).convert("RGB")

    pixel = Image.new("RGB", (1, 1), color=(10, 20, 30))
    tensor = _preprocess_yolox(pixel, input_width=416, input_height=416).tensor
    assert tensor.dtype == np.float32
    assert tensor.shape == (1, 3, 416, 416)
    assert tensor[0, :, 0, 0].tolist() == [30.0, 20.0, 10.0]
    assert float(tensor.min()) >= 0.0
    assert float(tensor.max()) <= 255.0

    detector = _YoloxOnnxPetDetector(
        model_path,
        enable_tiled_detection=False,
        execution_providers=["CPUExecutionProvider"],
    )
    boxes = detector.detect(image)
    dogs = [box for box in boxes if box.species_label == "dog"]
    assert len(dogs) == 1
    assert 0.70 <= dogs[0].confidence <= 0.95
    assert _bbox_iou(dogs[0].bbox, (133, 207, 192, 335)) >= 0.90
    assert not [box for box in boxes if box.species_label == "cat"]


def _cached_dog_image() -> Path:
    cache_root = _contract_cache_root()
    cache_root.mkdir(parents=True, exist_ok=True)
    target = cache_root / "yolox-dog.jpg"
    if target.is_file() and _sha256(target) == DOG_IMAGE_SHA256:
        return target
    with request.urlopen(  # noqa: S310
        DOG_IMAGE_URL,
        timeout=60,
        context=_download_ssl_context(DOG_IMAGE_URL),
    ) as response:
        payload = response.read(2 * 1024 * 1024)
    if hashlib.sha256(payload).hexdigest() != DOG_IMAGE_SHA256:
        raise AssertionError("official YOLOX dog fixture SHA-256 mismatch")
    target.write_bytes(payload)
    return target


def _contract_model_path(repository_root: Path) -> Path:
    bundled = repository_root / MODEL_RELATIVE_PATH
    if bundled.is_file() and _sha256(bundled) == DEFAULT_PET_DETECTOR_MODEL_SHA256:
        return bundled
    cache_root = _contract_cache_root()
    cache_root.mkdir(parents=True, exist_ok=True)
    target = cache_root / "yolox_nano_coco.onnx"
    if target.is_file() and _sha256(target) == DEFAULT_PET_DETECTOR_MODEL_SHA256:
        return target
    with request.urlopen(  # noqa: S310
        DEFAULT_PET_DETECTOR_MODEL_URL,
        timeout=60,
        context=_download_ssl_context(DEFAULT_PET_DETECTOR_MODEL_URL),
    ) as response:
        payload = response.read(DEFAULT_PET_DETECTOR_MODEL_MAX_BYTES + 1)
    if len(payload) > DEFAULT_PET_DETECTOR_MODEL_MAX_BYTES:
        raise AssertionError("official YOLOX model exceeds manifest size limit")
    if hashlib.sha256(payload).hexdigest() != DEFAULT_PET_DETECTOR_MODEL_SHA256:
        raise AssertionError("official YOLOX model SHA-256 mismatch")
    target.write_bytes(payload)
    return target


def _contract_cache_root() -> Path:
    return Path(
        os.environ.get("IPHOTO_PETS_CONTRACT_CACHE")
        or Path.home() / ".cache" / "iphoto-contracts"
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(256 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

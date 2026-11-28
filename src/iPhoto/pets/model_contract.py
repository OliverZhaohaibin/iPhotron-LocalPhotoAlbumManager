"""Lightweight verification contract for Pets model artifacts."""

import json
from pathlib import Path
from urllib.parse import urlparse

from .errors import PetPipelineInvariantError


def _load_pet_model_manifest() -> dict:
    manifest_path = Path(__file__).with_name("model_manifest.json")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        detector = manifest["detector"]
        embedder = manifest["embedder"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise PetPipelineInvariantError(f"Invalid Pets model manifest: {manifest_path}") from exc
    if manifest.get("schema_version") != 2:
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
    onnx_url = str(embedder.get("onnx_url") or "").strip()
    if onnx_url and urlparse(onnx_url).scheme.lower() != "https":
        raise PetPipelineInvariantError("Pets embedder ONNX URL must use HTTPS.")
    digest = str(embedder.get("onnx_sha256") or "")
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise PetPipelineInvariantError("Pets embedder ONNX SHA-256 is invalid.")
    if type(embedder.get("onnx_size")) is not int or not 0 < embedder["onnx_size"] <= 128 * 1024 * 1024:
        raise PetPipelineInvariantError("Pets embedder ONNX size is invalid.")
    if (
        embedder.get("format") != "onnx"
        or embedder.get("filename") != "embedding/dinov2_vits14/dinov2_vits14.onnx"
        or embedder.get("opset") != 17
        or embedder.get("input_name") != "image"
        or embedder.get("output_name") != "embedding"
        or embedder.get("dtype") != "float32"
        or embedder.get("output_shape") != [1, 384]
        or embedder.get("source_revision") != "7764ea0f912e53c92e82eb78a2a1631e92725fc8"
        or embedder.get("source_repository") != "facebookresearch/dinov2"
        or embedder.get("source_sha256") != "36e01591cfd52d194845d5b23b870dcb698f79c33f6140144180568f2155ab94"
        or embedder.get("layout") != "NCHW"
        or embedder.get("channel_order") != "RGB"
        or embedder.get("normalization") != {"mean": [0.485, 0.456, 0.406], "std": [0.229, 0.224, 0.225]}
        or embedder.get("resize") != "ImageOps.fit:BICUBIC"
        or embedder.get("output_normalization") != "L2"
    ):
        raise PetPipelineInvariantError("Pets embedder ONNX contract is invalid.")
    return manifest


PET_MODEL_MANIFEST = _load_pet_model_manifest()


def dinov2_metadata(embedder: dict, model_name: str) -> dict:
    return {
        **{key: value for key, value in embedder.items() if key not in {"filename", "onnx_url"}},
        "model_name": model_name,
    }

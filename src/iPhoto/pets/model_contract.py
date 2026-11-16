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
    torchscript_url = str(embedder.get("torchscript_url") or "").strip()
    if torchscript_url and urlparse(torchscript_url).scheme.lower() != "https":
        raise PetPipelineInvariantError("Pets embedder TorchScript URL must use HTTPS.")
    if len(str(embedder.get("torchscript_sha256") or "")) != 64:
        raise PetPipelineInvariantError("Pets embedder TorchScript SHA-256 is invalid.")
    if int(embedder.get("torchscript_size") or 0) <= 0:
        raise PetPipelineInvariantError("Pets embedder TorchScript size is invalid.")
    return manifest


PET_MODEL_MANIFEST = _load_pet_model_manifest()


def dinov2_metadata(embedder: dict, model_name: str) -> dict:
    return {
        "model_name": model_name,
        **{
            key: embedder[key]
            for key in (
                "source_repository",
                "source_revision",
                "torchscript_sha256",
                "torchscript_size",
                "input_shape",
                "output_shape",
            )
        },
    }

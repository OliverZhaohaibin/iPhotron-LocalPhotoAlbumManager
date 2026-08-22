from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from iPhoto.pets import pipeline as pet_pipeline


def _write_cache(tmp_path: Path, *, content: bytes, metadata: dict) -> Path:
    model_path = tmp_path / "dinov2_vits14.pt"
    metadata_path = pet_pipeline._dinov2_metadata_path(model_path)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    model_path.write_bytes(content)
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    return model_path


def _base_metadata(content: bytes) -> dict:
    manifest = pet_pipeline._EMBEDDER_MANIFEST
    return {
        "artifact_kind": "derived_checkpoint_cache",
        "weights_sha256": "b938bf1bc15cd2ec0feacfe3a1bb553fe8ea9ca46a7e1d8d00217f29aef60cd9",
        "weights_size": 88283115,
        "model_name": manifest["model_name"],
        "source_repository": manifest["source_repository"],
        "source_revision": manifest["source_revision"],
        "input_shape": manifest["input_shape"],
        "output_shape": manifest["output_shape"],
        "derived_torchscript_size": len(content),
        "derived_torchscript_sha256": hashlib.sha256(content).hexdigest(),
    }


def test_prebuilt_torchscript_requires_manifest_integrity(tmp_path: Path) -> None:
    manifest = pet_pipeline._EMBEDDER_MANIFEST
    content = b"prebuilt-torchscript"
    metadata = _base_metadata(content)
    del metadata["derived_torchscript_size"]
    del metadata["derived_torchscript_sha256"]
    metadata.update(
        {
            "torchscript_sha256": manifest["torchscript_sha256"],
            "torchscript_size": int(manifest["torchscript_size"]),
            "download_sha256": manifest["torchscript_sha256"],
            "download_size": int(manifest["torchscript_size"]),
        }
    )
    model_path = _write_cache(tmp_path, content=content, metadata=metadata)

    with pytest.raises(RuntimeError, match="integrity check failed"):
        pet_pipeline._validate_dinov2_cache_metadata(
            model_path,
            model_name=manifest["model_name"],
        )


def test_corrupt_derived_user_cache_fails_discovery_validation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("IPHOTO_PET_MODEL_DIR", raising=False)
    cache_root = tmp_path / "cache"
    monkeypatch.setattr(pet_pipeline, "user_pet_model_cache_dir", lambda: cache_root)
    good = b"derived-torchscript"
    corrupt = good[:-8]
    metadata = _base_metadata(good)
    model_path = _write_cache(tmp_path, content=corrupt, metadata=metadata)

    with pytest.raises(RuntimeError, match="cache integrity check failed"):
        pet_pipeline._validate_dinov2_cache_metadata(
            model_path,
            model_name=pet_pipeline._EMBEDDER_MANIFEST["model_name"],
        )

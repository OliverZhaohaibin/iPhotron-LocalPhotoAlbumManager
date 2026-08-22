"""Install the first-use DINOv2 cache contract for the Pets pipeline.

The upstream artifact is Meta's official state-dict checkpoint.  The TorchScript
file stored by iPhotron is a local derived cache, so its serialized bytes are
not treated as a cross-PyTorch stable identity.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlparse

from .errors import PetPipelineInvariantError

_INSTALLED = False


def install_pet_model_runtime(pipeline) -> None:
    """Patch the Pets pipeline's legacy TorchScript-release hooks in one place."""

    global _INSTALLED
    if _INSTALLED:
        return

    manifest = pipeline.PET_MODEL_MANIFEST["embedder"]
    weights_url = str(manifest.get("weights_url") or "").strip()
    if urlparse(weights_url).scheme.lower() != "https":
        raise PetPipelineInvariantError("Pets embedder weights URL must use HTTPS.")
    if int(manifest.get("weights_max_bytes") or 0) <= 0:
        raise PetPipelineInvariantError("Pets embedder weights size limit is invalid.")

    def pet_embedder_model_url() -> str:
        return str(
            os.environ.get(pipeline.PET_EMBEDDER_MODEL_URL_ENV)
            or manifest.get("weights_url")
            or ""
        ).strip()

    def validate_dinov2_cache_metadata(model_path: Path, *, model_name: str) -> None:
        model_path = Path(model_path)
        metadata_path = pipeline._dinov2_metadata_path(model_path)
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
            "source_repository": manifest["source_repository"],
            "source_revision": manifest["source_revision"],
            "input_shape": manifest["input_shape"],
            "output_shape": manifest["output_shape"],
        }
        if any(metadata.get(key) != value for key, value in expected.items()):
            raise RuntimeError(
                f"Pet scanning unavailable: DINOv2 metadata contract mismatch at {metadata_path}."
            )
        if model_path.stat().st_size <= 0:
            raise RuntimeError(
                f"Pet scanning unavailable: DINOv2 TorchScript cache is empty at {model_path}."
            )

        # TorchScript serialization is not byte-stable across all supported
        # PyTorch builds. Validate the derived cache semantically instead.
        try:
            import torch

            torch.jit.load(str(model_path), map_location="cpu").eval()
        except ImportError as exc:
            raise RuntimeError(
                "Pet scanning unavailable: torch is required to validate the DINOv2 cache."
            ) from exc
        except Exception as exc:  # noqa: BLE001 - torch loader errors vary by version
            raise RuntimeError(
                f"Pet scanning unavailable: DINOv2 TorchScript cache is not loadable at {model_path}."
            ) from exc

    def download_dinov2_model(self, model_path: Path):
        from .model_bootstrap import _download_dinov2_release

        url = pet_embedder_model_url()
        if not url:
            raise RuntimeError(
                "Pet scanning unavailable: no DINOv2 weights download source is configured."
            )
        _download_dinov2_release(Path(model_path), url=url)
        model = self._torch.jit.load(str(model_path), map_location=self._device)
        model.eval()
        model.to(self._device)
        return model

    pipeline.pet_embedder_model_url = pet_embedder_model_url
    pipeline._validate_dinov2_cache_metadata = validate_dinov2_cache_metadata
    pipeline._DinoV2Embedder._download_dinov2_model = download_dinov2_model
    _INSTALLED = True

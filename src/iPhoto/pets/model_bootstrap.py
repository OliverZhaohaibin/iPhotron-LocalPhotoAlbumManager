"""Resolve and acquire verified Pets recognition model artifacts."""

from __future__ import annotations

import json
from pathlib import Path

from . import pipeline as pet_pipeline
from .errors import PetModelUnavailableError


def ensure_pet_model_artifacts(
    model_root: Path | None = None,
    *,
    allow_model_download: bool | None = None,
) -> bool:
    """Ensure missing Pets artifacts use extension-first storage.

    Existing artifacts are resolved from the preferred extension root and then
    the user cache. An explicit override is authoritative: it is neither
    supplemented by nor silently replaced by another storage root. Missing
    artifacts install to the selected writable root without changing bundled
    artifacts.
    """

    allow_download = (
        pet_pipeline.pet_model_auto_download_enabled()
        if allow_model_download is None
        else bool(allow_model_download)
    )
    if not allow_download:
        return False

    override_root, _default_roots = pet_pipeline.pet_model_storage_roots()
    requested_root = Path(model_root) if model_root is not None else None
    if override_root is not None and requested_root is not None and requested_root != override_root:
        raise PetModelUnavailableError(
            "Pet scanning unavailable: IPHOTO_PET_MODEL_DIR is authoritative and "
            "does not fall back automatically."
        )

    detector_manifest = pet_pipeline.PET_MODEL_MANIFEST["detector"]
    embedder_manifest = pet_pipeline.PET_MODEL_MANIFEST["embedder"]

    detector_relative = Path(str(detector_manifest["filename"]))
    resolved_detector = _resolve_artifact(detector_relative, model_root=requested_root)
    detector_path = resolved_detector.path or _acquisition_path(
        requested_root,
        resolved_detector.invalid_bundled,
        detector_relative,
    )
    detector_missing = resolved_detector.path is None

    if detector_missing:
        try:
            pet_pipeline.ensure_pet_detector_model(
                detector_path,
                allow_model_download=True,
            )
        except Exception as exc:  # noqa: BLE001 - normalize acquisition errors
            raise PetModelUnavailableError(
                "Pet scanning unavailable: failed to acquire the YOLOX detector "
                f"model ({_error_reason(exc)})."
            ) from exc

    embedder_relative = Path(str(embedder_manifest["filename"]))
    resolved_embedder = _resolve_artifact(
        embedder_relative.parent,
        directory=True,
        model_root=requested_root,
    )
    embedder_directory = resolved_embedder.path or _acquisition_path(
        requested_root,
        resolved_embedder.invalid_bundled,
        embedder_relative.parent,
    )
    embedder_path = embedder_directory / embedder_relative.name
    embedder_missing = resolved_embedder.path is None

    url = pet_pipeline.pet_embedder_model_url()
    if not url:
        raise PetModelUnavailableError(
            "Pet scanning unavailable: no fixed DINOv2 TorchScript release artifact "
            "is configured. Set IPHOTO_PET_EMBEDDER_MODEL_URL or install the "
            "verified model in IPHOTO_PET_MODEL_DIR."
        )
    if embedder_missing:
        _download_dinov2_release(embedder_path, url=url)

    return detector_missing or embedder_missing


def _download_dinov2_release(model_path: Path, *, url: str) -> None:
    manifest = pet_pipeline.PET_MODEL_MANIFEST["embedder"]
    try:
        pet_pipeline._install_certifi_environment()
        pet_pipeline._download_file(
            url,
            model_path,
            label="DINOv2 TorchScript model",
            expected_sha256=str(manifest["torchscript_sha256"]),
            max_bytes=int(manifest["torchscript_size"]),
        )
        _write_dinov2_metadata(model_path)
        pet_pipeline._validate_dinov2_cache_metadata(
            model_path,
            model_name=str(manifest["model_name"]),
        )
    except Exception as exc:  # noqa: BLE001 - urllib/SSL/runtime failures vary
        _remove_dinov2_artifact(model_path)
        raise PetModelUnavailableError(
            "Pet scanning unavailable: failed to download the verified DINOv2 "
            f"TorchScript model from {url} ({_error_reason(exc)})."
        ) from exc


def _acquisition_path(
    model_root: Path | None,
    invalid_bundled: bool,
    relative_path: Path,
) -> Path:
    if model_root is not None:
        return model_root / relative_path
    if invalid_bundled:
        return pet_pipeline.user_pet_model_cache_dir() / relative_path
    return pet_pipeline.pet_model_install_root() / relative_path


def _resolve_artifact(
    relative_path: Path,
    *,
    directory: bool = False,
    model_root: Path | None,
) -> pet_pipeline.PetArtifactResolution:
    if model_root is None:
        return pet_pipeline.resolve_pet_model_path(relative_path, directory=directory)
    try:
        return pet_pipeline.resolve_pet_model_path(
            relative_path,
            directory=directory,
            search_roots=(model_root,),
        )
    except PetModelUnavailableError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise PetModelUnavailableError(
            "Pet scanning unavailable: invalid explicit model artifact at "
            f"{model_root / relative_path}."
        ) from exc


def _write_dinov2_metadata(model_path: Path) -> None:
    manifest = pet_pipeline.PET_MODEL_MANIFEST["embedder"]
    metadata_path = pet_pipeline._dinov2_metadata_path(model_path)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "model_name": manifest["model_name"],
        "source_repository": manifest["source_repository"],
        "source_revision": manifest["source_revision"],
        "torchscript_sha256": manifest["torchscript_sha256"],
        "torchscript_size": int(manifest["torchscript_size"]),
        "input_shape": manifest["input_shape"],
        "output_shape": manifest["output_shape"],
    }
    metadata_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _remove_dinov2_artifact(model_path: Path) -> None:
    model_path.unlink(missing_ok=True)
    pet_pipeline._dinov2_metadata_path(model_path).unlink(missing_ok=True)
    for parent in (model_path.parent, model_path.parent.parent):
        try:
            parent.rmdir()
        except OSError:
            break


def _error_reason(exc: Exception) -> str:
    return str(exc).strip() or exc.__class__.__name__

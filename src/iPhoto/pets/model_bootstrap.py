"""Resolve and acquire verified Pets recognition model artifacts."""

from __future__ import annotations

import json
import sys
import tempfile
import uuid
from pathlib import Path

from . import pipeline as pet_pipeline
from .errors import PetModelUnavailableError, PetRuntimeUnavailableError


def ensure_pet_model_artifacts(
    model_root: Path | None = None,
    *,
    allow_model_download: bool | None = None,
) -> bool:
    """Ensure missing Pets artifacts use extension-first storage.

    DINOv2 is acquired from Meta's official checkpoint and converted locally to
    a TorchScript cache. The derived TorchScript bytes are deliberately not
    pinned by SHA-256 because serialization can vary across supported PyTorch
    versions; source identity and loadability are validated instead.
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
    allow_storage_fallback = requested_root is None and override_root is None
    detector_missing = resolved_detector.path is None

    acquired_root: Path | None = None
    if detector_missing:
        acquired_path = _acquire_detector_with_fallback(
            detector_path,
            allow_storage_fallback=allow_storage_fallback,
        )
        if acquired_path is not None and acquired_path != detector_path:
            acquired_root = _storage_root(acquired_path, detector_relative)

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
    if acquired_root is not None:
        embedder_directory = acquired_root / embedder_relative.parent
    embedder_path = embedder_directory / embedder_relative.name
    embedder_missing = resolved_embedder.path is None

    url = pet_pipeline.pet_embedder_model_url()
    if not url:
        raise PetModelUnavailableError(
            "Pet scanning unavailable: no DINOv2 checkpoint source is configured. "
            "Set IPHOTO_PET_EMBEDDER_MODEL_URL or install the model in "
            "IPHOTO_PET_MODEL_DIR."
        )
    if embedder_missing:
        _download_dinov2_release_with_fallback(
            embedder_path,
            url=url,
            allow_storage_fallback=allow_storage_fallback,
        )

    return detector_missing or embedder_missing


def _storage_root(path: Path, relative_path: Path) -> Path:
    depth = len(relative_path.parts) - 1
    return path.parents[depth]


def _is_macos_app_bundle_path(path: Path) -> bool:
    if sys.platform != "darwin":
        return False
    parts = Path(path).parts
    for index, part in enumerate(parts[:-1]):
        if (
            part.lower().endswith(".app")
            and index + 1 < len(parts)
            and parts[index + 1] == "Contents"
        ):
            return True
    return False


def _directory_is_writable_for_install(path: Path) -> bool:
    path = Path(path)
    probe_path = path / f".iphoto-pet-model-write-probe-{uuid.uuid4().hex}"
    try:
        path.mkdir(parents=True, exist_ok=True)
        with probe_path.open("xb"):
            pass
        probe_path.unlink()
        return True
    except OSError as exc:
        if exc.errno in pet_pipeline._MODEL_STORAGE_ERRNOS:
            return False
        raise
    finally:
        try:
            probe_path.unlink(missing_ok=True)
        except OSError:
            pass


def _default_install_root() -> Path:
    override = pet_pipeline.pet_model_override_dir()
    if override is not None:
        return override

    preferred = pet_pipeline.bundled_pet_model_dir()
    if not _is_macos_app_bundle_path(preferred) and _directory_is_writable_for_install(preferred):
        return preferred

    fallback = pet_pipeline.user_pet_model_cache_dir()
    if not _directory_is_writable_for_install(fallback):
        raise PetModelUnavailableError(
            "Pet scanning unavailable: neither the extension model directory nor "
            "the user model cache is writable."
        )
    return fallback


def _ensure_dino_metadata_writable(model_path: Path) -> None:
    metadata_path = pet_pipeline._dinov2_metadata_path(model_path)
    probe_path = metadata_path.with_name(
        f".{metadata_path.name}.{uuid.uuid4().hex}.probe"
    )
    try:
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        probe_path.write_text("", encoding="utf-8")
    except OSError as exc:
        pet_pipeline._raise_if_model_storage_error(exc, metadata_path)
    finally:
        try:
            probe_path.unlink(missing_ok=True)
        except OSError:
            pass


def _download_dinov2_release(model_path: Path, *, url: str) -> None:
    """Build a local TorchScript cache from the configured DINOv2 checkpoint."""

    manifest = pet_pipeline.PET_MODEL_MANIFEST["embedder"]
    try:
        import torch
    except ImportError as exc:
        raise PetRuntimeUnavailableError(
            "Pet scanning unavailable: torch is required to build the DINOv2 cache. "
            'Install the optional Pets AI runtime with: pip install -e ".[pets-ai]"'
        ) from exc

    source = f"{manifest['source_repository']}:{manifest['source_revision']}"
    input_shape = tuple(int(value) for value in manifest["input_shape"])
    expected_shape = tuple(int(value) for value in manifest["output_shape"])
    model_path = Path(model_path)

    try:
        pet_pipeline._install_certifi_environment()
        _ensure_dino_metadata_writable(model_path)
        model_path.parent.mkdir(parents=True, exist_ok=True)
        model = torch.hub.load(
            source,
            str(manifest["model_name"]),
            source="github",
            trust_repo=True,
            weights=url,
        ).eval().cpu()
        example = torch.zeros(input_shape, dtype=torch.float32)

        with tempfile.TemporaryDirectory(
            prefix="iphoto-dinov2-build-",
            dir=model_path.parent,
        ) as temp_dir:
            candidate = Path(temp_dir) / model_path.name
            with torch.no_grad():
                eager_output = model(example)
                traced = torch.jit.trace(model, example, strict=False)
                traced.save(str(candidate))
                scripted = torch.jit.load(str(candidate), map_location="cpu").eval()
                scripted_output = scripted(example)

            if isinstance(eager_output, (list, tuple)):
                eager_output = eager_output[0]
            if isinstance(scripted_output, (list, tuple)):
                scripted_output = scripted_output[0]
            if tuple(scripted_output.shape) != expected_shape:
                raise RuntimeError(
                    "DINOv2 output shape mismatch: "
                    f"{tuple(scripted_output.shape)} != {expected_shape}"
                )
            torch.testing.assert_close(scripted_output, eager_output, rtol=1e-4, atol=1e-5)
            candidate.replace(model_path)

        _write_dinov2_metadata(model_path, weights_url=url)
        pet_pipeline._validate_dinov2_cache_metadata(
            model_path,
            model_name=str(manifest["model_name"]),
        )
    except pet_pipeline._ModelStoragePermissionError:
        _remove_dinov2_artifact(model_path)
        raise
    except Exception as exc:  # noqa: BLE001 - hub/SSL/torch failures vary
        _remove_dinov2_artifact(model_path)
        if isinstance(exc, (PetModelUnavailableError, PetRuntimeUnavailableError)):
            raise
        raise PetModelUnavailableError(
            "Pet scanning unavailable: failed to build DINOv2 from the configured "
            f"checkpoint {url} ({_error_reason(exc)})."
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
    return _default_install_root() / relative_path


def _fallback_artifact_path(path: Path) -> Path | None:
    override_root = pet_pipeline.pet_model_override_dir()
    bundled_root = pet_pipeline.bundled_pet_model_dir()
    user_cache = pet_pipeline.user_pet_model_cache_dir()
    path = Path(path)
    try:
        relative = path.resolve().relative_to(bundled_root.resolve())
    except (OSError, ValueError):
        return None
    if override_root is not None or relative == Path("."):
        return None
    return user_cache / relative


def _acquire_to(path: Path, *, acquire, allow_fallback: bool) -> Path | None:
    try:
        acquire(path)
        return path
    except Exception as exc:
        fallback = _fallback_artifact_path(path)
        storage_permission = isinstance(
            exc,
            (pet_pipeline._ModelStoragePermissionError,),
        )
        if fallback is None or not storage_permission or not allow_fallback:
            raise
        try:
            acquire(fallback)
            return fallback
        except Exception as fallback_exc:
            raise fallback_exc from exc


def _acquire_detector_with_fallback(
    path: Path,
    *,
    allow_storage_fallback: bool,
) -> Path | None:
    def _acquire(target: Path) -> None:
        pet_pipeline.ensure_pet_detector_model(
            target,
            allow_model_download=True,
        )

    try:
        return _acquire_to(
            path,
            acquire=_acquire,
            allow_fallback=allow_storage_fallback,
        )
    except Exception as exc:
        raise PetModelUnavailableError(
            "Pet scanning unavailable: failed to acquire the YOLOX detector "
            f"model ({_error_reason(exc)})."
        ) from exc


def _download_dinov2_release_with_fallback(
    path: Path,
    *,
    url: str,
    allow_storage_fallback: bool,
) -> Path | None:
    def _acquire(target: Path) -> None:
        _download_dinov2_release(target, url=url)

    try:
        return _acquire_to(
            path,
            acquire=_acquire,
            allow_fallback=allow_storage_fallback,
        )
    except Exception as exc:
        if isinstance(exc, (PetModelUnavailableError, PetRuntimeUnavailableError)):
            raise
        if isinstance(exc, (PermissionError, pet_pipeline._ModelStoragePermissionError)):
            raise PetModelUnavailableError(
                "Pet scanning unavailable: filesystem permission denied while "
                f"storing the DINOv2 cache ({_error_reason(exc)})."
            ) from exc
        raise PetModelUnavailableError(
            "Pet scanning unavailable: failed to acquire the DINOv2 checkpoint from "
            f"{url} ({_error_reason(exc)})."
        ) from exc


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


def _write_dinov2_metadata(model_path: Path, *, weights_url: str) -> None:
    manifest = pet_pipeline.PET_MODEL_MANIFEST["embedder"]
    metadata_path = pet_pipeline._dinov2_metadata_path(model_path)
    payload = {
        "model_name": manifest["model_name"],
        "source_repository": manifest["source_repository"],
        "source_revision": manifest["source_revision"],
        "weights_url": weights_url,
        "derived_torchscript_size": int(Path(model_path).stat().st_size),
        "input_shape": manifest["input_shape"],
        "output_shape": manifest["output_shape"],
    }
    temp_path = metadata_path.with_name(
        f".{metadata_path.name}.{uuid.uuid4().hex}.tmp"
    )
    try:
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temp_path.replace(metadata_path)
    except OSError as exc:
        pet_pipeline._raise_if_model_storage_error(exc, metadata_path)
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _remove_dinov2_artifact(model_path: Path) -> None:
    model_path = Path(model_path)
    metadata_path = pet_pipeline._dinov2_metadata_path(model_path)
    for artifact in (model_path, metadata_path):
        try:
            artifact.unlink(missing_ok=True)
        except OSError:
            pass
    for parent in (model_path.parent, model_path.parent.parent):
        try:
            parent.rmdir()
        except OSError:
            break


def _error_reason(exc: Exception) -> str:
    return str(exc).strip() or exc.__class__.__name__

"""Resolve and acquire verified Pets recognition model artifacts."""

from __future__ import annotations

import json
import uuid
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
            "Pet scanning unavailable: no fixed DINOv2 TorchScript release artifact "
            "is configured. Set IPHOTO_PET_EMBEDDER_MODEL_URL or install the "
            "verified model in IPHOTO_PET_MODEL_DIR."
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


def _ensure_dino_metadata_writable(model_path: Path) -> None:
    metadata_path = pet_pipeline._dinov2_metadata_path(model_path)
    probe_path = metadata_path.with_name(
        f".{metadata_path.name}.{uuid.uuid4().hex}.probe"
    )
    try:
        probe_path.write_text("", encoding="utf-8")
    except OSError as exc:
        pet_pipeline._raise_if_model_storage_error(exc, metadata_path)
    finally:
        try:
            probe_path.unlink(missing_ok=True)
        except OSError:
            pass


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
        _ensure_dino_metadata_writable(model_path)
        _write_dinov2_metadata(model_path)
        pet_pipeline._validate_dinov2_cache_metadata(
            model_path,
            model_name=str(manifest["model_name"]),
        )
    except pet_pipeline._ModelStoragePermissionError:
        _remove_dinov2_artifact(model_path)
        raise
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
    return None


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
        if isinstance(exc, PetModelUnavailableError):
            raise
        if isinstance(exc, (PermissionError, pet_pipeline._ModelStoragePermissionError)):
            raise PetModelUnavailableError(
                "Pet scanning unavailable: filesystem permission denied while "
                f"storing the verified DINOv2 TorchScript model ({_error_reason(exc)})."
            ) from exc
        raise PetModelUnavailableError(
            "Pet scanning unavailable: failed to download the verified DINOv2 "
            f"TorchScript model from {url} ({_error_reason(exc)})."
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


def _write_dinov2_metadata(model_path: Path) -> None:
    manifest = pet_pipeline.PET_MODEL_MANIFEST["embedder"]
    metadata_path = pet_pipeline._dinov2_metadata_path(model_path)
    payload = {
        "model_name": manifest["model_name"],
        "source_repository": manifest["source_repository"],
        "source_revision": manifest["source_revision"],
        "torchscript_sha256": manifest["torchscript_sha256"],
        "torchscript_size": int(manifest["torchscript_size"]),
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
    if model_path.exists():
        return
    for parent in (model_path.parent, model_path.parent.parent):
        try:
            parent.rmdir()
        except OSError:
            break


def _error_reason(exc: Exception) -> str:
    return str(exc).strip() or exc.__class__.__name__

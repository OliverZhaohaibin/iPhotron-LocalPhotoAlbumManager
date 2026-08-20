"""First-use acquisition for Pets recognition model artifacts.

The runtime prefers fixed, hash-verified release artifacts. When no DINOv2
TorchScript release URL is configured, it may build an equivalent artifact from
the manifest-pinned upstream revision. Locally built TorchScript archives are
validated by numeric equivalence during conversion and by their own persisted
SHA-256/size metadata afterward; they are not required to reproduce the byte
identity of a release artifact produced by another PyTorch build or platform.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from . import pipeline as pet_pipeline
from .errors import PetModelUnavailableError, PetRuntimeUnavailableError

_LOCAL_BOOTSTRAP_ORIGIN = "pinned-source-bootstrap"


def ensure_pet_model_artifacts(
    model_root: Path,
    *,
    allow_model_download: bool | None = None,
) -> bool:
    """Ensure missing Pets models are acquired before the first scan batch.

    Returns ``True`` when at least one artifact was installed. Downloads stay
    under ``model_root``; temporary Torch Hub files are also staged there and
    removed after the DINOv2 TorchScript artifact is produced.
    """

    allow_download = (
        pet_pipeline.pet_model_auto_download_enabled()
        if allow_model_download is None
        else bool(allow_model_download)
    )
    if not allow_download:
        return False

    root = Path(model_root)
    detector_manifest = pet_pipeline.PET_MODEL_MANIFEST["detector"]
    embedder_manifest = pet_pipeline.PET_MODEL_MANIFEST["embedder"]

    detector_path = root / Path(str(detector_manifest["filename"]))
    detector_missing = not detector_path.is_file()
    try:
        pet_pipeline.ensure_pet_detector_model(
            detector_path,
            allow_model_download=True,
        )
    except Exception as exc:  # noqa: BLE001 - normalize model acquisition errors
        raise PetModelUnavailableError(
            "Pet scanning unavailable: failed to acquire the YOLOX detector model "
            f"({_error_reason(exc)})."
        ) from exc

    embedder_path = root / Path(str(embedder_manifest["filename"]))
    embedder_missing = not embedder_path.is_file()
    if not embedder_missing:
        try:
            _activate_local_bootstrap_contract(embedder_path)
            pet_pipeline._validate_dinov2_cache_metadata(
                embedder_path,
                model_name=str(embedder_manifest["model_name"]),
            )
            return detector_missing
        except (OSError, RuntimeError):
            _remove_dinov2_artifact(embedder_path)
            embedder_missing = True

    url = pet_pipeline.pet_embedder_model_url()
    if url:
        _download_dinov2_release(embedder_path, url=url)
    else:
        _bootstrap_dinov2_from_pinned_source(embedder_path)

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


def _bootstrap_dinov2_from_pinned_source(model_path: Path) -> None:
    manifest = pet_pipeline.PET_MODEL_MANIFEST["embedder"]
    try:
        import torch
    except ImportError as exc:
        raise PetRuntimeUnavailableError(
            "Pet scanning unavailable: missing torch for DINOv2 model bootstrap. "
            'Install the optional Pets AI runtime with: pip install -e ".[pets-ai]"'
        ) from exc

    repository = str(manifest["source_repository"])
    revision = str(manifest["source_revision"])
    model_name = str(manifest["model_name"])
    source = f"{repository}:{revision}"

    model_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        previous_hub_dir = torch.hub.get_dir()
    except Exception as exc:  # noqa: BLE001 - torch builds may differ
        raise PetRuntimeUnavailableError(
            "Pet scanning unavailable: PyTorch Hub is not available for DINOv2 "
            f"bootstrap ({_error_reason(exc)})."
        ) from exc

    artifact_sha256 = ""
    artifact_size = 0
    try:
        # Windows can transiently keep files from the cloned Torch Hub source
        # open while TemporaryDirectory tears down. Cleanup is best-effort only:
        # a cleanup failure must not roll back a numerically validated model.
        with tempfile.TemporaryDirectory(
            prefix="iphoto-dinov2-bootstrap-",
            dir=model_path.parent,
            ignore_cleanup_errors=True,
        ) as temp_dir:
            temp_root = Path(temp_dir)
            torch.hub.set_dir(str(temp_root / "torch-hub"))
            candidate = temp_root / model_path.name
            with torch.random.fork_rng(devices=[]):
                torch.manual_seed(0)
                example = torch.randn(tuple(manifest["input_shape"]), dtype=torch.float32)
                eager_model = torch.hub.load(
                    source,
                    model_name,
                    source="github",
                    trust_repo=True,
                ).eval().cpu()
                with torch.no_grad():
                    eager_output = eager_model(example)
                    traced = torch.jit.trace(eager_model, example, strict=False)
                    traced.save(str(candidate))
                    scripted = torch.jit.load(str(candidate), map_location="cpu").eval()
                    scripted_output = scripted(example)

            if isinstance(eager_output, (list, tuple)):
                eager_output = eager_output[0]
            if isinstance(scripted_output, (list, tuple)):
                scripted_output = scripted_output[0]
            expected_shape = tuple(manifest["output_shape"])
            if tuple(scripted_output.shape) != expected_shape:
                raise RuntimeError(
                    "DINOv2 bootstrap output shape mismatch: "
                    f"{tuple(scripted_output.shape)} != {expected_shape}"
                )
            torch.testing.assert_close(scripted_output, eager_output, rtol=1e-4, atol=1e-5)

            artifact_sha256 = pet_pipeline._file_sha256(candidate)
            artifact_size = candidate.stat().st_size
            if artifact_size <= 0 or len(artifact_sha256) != 64:
                raise RuntimeError("the pinned-source bootstrap produced an invalid artifact")
            candidate.replace(model_path)
    except Exception as exc:  # noqa: BLE001 - torch/Torch Hub failures vary by version
        _remove_dinov2_artifact(model_path)
        raise PetModelUnavailableError(
            "Pet scanning unavailable: automatic DINOv2 acquisition from the pinned "
            f"source {source} failed ({_error_reason(exc)})."
        ) from exc
    finally:
        try:
            torch.hub.set_dir(previous_hub_dir)
        except Exception:
            pass

    try:
        _write_dinov2_metadata(
            model_path,
            artifact_origin=_LOCAL_BOOTSTRAP_ORIGIN,
            artifact_sha256=artifact_sha256,
            artifact_size=artifact_size,
            numeric_equivalence=True,
        )
        if not _activate_local_bootstrap_contract(model_path):
            raise RuntimeError("local DINOv2 bootstrap metadata was not activated")
        pet_pipeline._validate_dinov2_cache_metadata(
            model_path,
            model_name=model_name,
        )
    except Exception as exc:  # noqa: BLE001 - normalize post-install validation failures
        _remove_dinov2_artifact(model_path)
        raise PetModelUnavailableError(
            "Pet scanning unavailable: the automatically acquired DINOv2 artifact "
            f"failed validation ({_error_reason(exc)})."
        ) from exc


def _activate_local_bootstrap_contract(model_path: Path) -> bool:
    """Activate a self-verified local TorchScript artifact for this process.

    A TorchScript archive generated from the same pinned model can have different
    bytes across PyTorch versions/platforms. The sidecar records the actual local
    hash/size after eager-vs-traced numeric equivalence was checked. If that
    sidecar is intact, teach the existing pipeline cache validator the local
    artifact identity for this process without changing the checked-in manifest.
    """

    metadata_path = pet_pipeline._dinov2_metadata_path(model_path)
    if not metadata_path.is_file():
        return False
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if metadata.get("artifact_origin") != _LOCAL_BOOTSTRAP_ORIGIN:
        return False

    manifest = pet_pipeline.PET_MODEL_MANIFEST["embedder"]
    expected_common = {
        "model_name": manifest["model_name"],
        "source_repository": manifest["source_repository"],
        "source_revision": manifest["source_revision"],
        "input_shape": manifest["input_shape"],
        "output_shape": manifest["output_shape"],
        "numeric_equivalence": True,
    }
    if any(metadata.get(key) != value for key, value in expected_common.items()):
        raise RuntimeError(
            f"Pet scanning unavailable: local DINOv2 bootstrap metadata contract mismatch at "
            f"{metadata_path}."
        )

    actual_size = Path(model_path).stat().st_size
    actual_sha256 = pet_pipeline._file_sha256(model_path)
    if (
        int(metadata.get("torchscript_size") or -1) != actual_size
        or str(metadata.get("torchscript_sha256") or "").lower() != actual_sha256
    ):
        raise RuntimeError(
            f"Pet scanning unavailable: local DINOv2 bootstrap integrity check failed for "
            f"{model_path}."
        )

    # _EMBEDDER_MANIFEST and PET_MODEL_MANIFEST['embedder'] reference the same
    # dictionary. Override only the byte identity fields in memory; the source,
    # model and tensor contracts remain the checked-in manifest values.
    pet_pipeline._EMBEDDER_MANIFEST["torchscript_sha256"] = actual_sha256
    pet_pipeline._EMBEDDER_MANIFEST["torchscript_size"] = actual_size
    return True


def _write_dinov2_metadata(
    model_path: Path,
    *,
    artifact_origin: str | None = None,
    artifact_sha256: str | None = None,
    artifact_size: int | None = None,
    numeric_equivalence: bool | None = None,
) -> None:
    manifest = pet_pipeline.PET_MODEL_MANIFEST["embedder"]
    metadata_path = pet_pipeline._dinov2_metadata_path(model_path)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "model_name": manifest["model_name"],
        "source_repository": manifest["source_repository"],
        "source_revision": manifest["source_revision"],
        "torchscript_sha256": artifact_sha256 or manifest["torchscript_sha256"],
        "torchscript_size": (
            int(artifact_size) if artifact_size is not None else manifest["torchscript_size"]
        ),
        "input_shape": manifest["input_shape"],
        "output_shape": manifest["output_shape"],
    }
    if artifact_origin is not None:
        payload["artifact_origin"] = artifact_origin
    if numeric_equivalence is not None:
        payload["numeric_equivalence"] = bool(numeric_equivalence)
    metadata_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _remove_dinov2_artifact(model_path: Path) -> None:
    try:
        Path(model_path).unlink(missing_ok=True)
        pet_pipeline._dinov2_metadata_path(model_path).unlink(missing_ok=True)
    except OSError:
        pass


def _error_reason(exc: Exception) -> str:
    return str(exc).strip() or exc.__class__.__name__

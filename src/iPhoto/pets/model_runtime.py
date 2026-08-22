"""Install the first-use DINOv2 cache contract for the Pets pipeline.

The upstream artifact is Meta's official state-dict checkpoint. The TorchScript
file stored by iPhotron is a local derived cache, so its serialized bytes are
not treated as a cross-PyTorch stable identity. Integrity verification applies
to the downloaded official checkpoint only; the derived cache is validated by
metadata during discovery and by TorchScript/runtime checks when it is loaded.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlparse

from .errors import (
    PetModelUnavailableError,
    PetPipelineInvariantError,
    PetRuntimeUnavailableError,
)

_INSTALLED = False


def _manifest_source_contract(pipeline) -> tuple[dict, str, str, int]:
    manifest = pipeline.PET_MODEL_MANIFEST["embedder"]
    weights_url = str(manifest.get("weights_url") or "").strip()
    weights_sha256 = str(manifest.get("weights_sha256") or "").strip().lower()
    weights_size = int(manifest.get("weights_size") or 0)

    if urlparse(weights_url).scheme.lower() != "https":
        raise PetPipelineInvariantError("Pets embedder weights URL must use HTTPS.")
    if len(weights_sha256) != 64 or any(
        char not in "0123456789abcdef" for char in weights_sha256
    ):
        raise PetPipelineInvariantError(
            "Pets embedder checkpoint SHA-256 is invalid."
        )
    if weights_size <= 0:
        raise PetPipelineInvariantError("Pets embedder checkpoint size is invalid.")
    return manifest, weights_url, weights_sha256, weights_size


def install_pet_model_runtime(pipeline) -> None:
    """Patch legacy model hooks onto the official-checkpoint cache contract."""

    global _INSTALLED
    if _INSTALLED:
        return

    manifest, _weights_url, weights_sha256, weights_size = _manifest_source_contract(
        pipeline
    )

    def pet_embedder_model_url() -> str:
        # A deliberately configured prebuilt TorchScript URL remains supported
        # for compatibility/offline packaging. The checked-in default is empty,
        # so normal first-use acquisition always selects Meta's official weights.
        return str(
            os.environ.get(pipeline.PET_EMBEDDER_MODEL_URL_ENV)
            or manifest.get("torchscript_url")
            or manifest.get("weights_url")
            or ""
        ).strip()

    def validate_dinov2_cache_metadata(model_path: Path, *, model_name: str) -> None:
        """Validate discovery metadata without importing the optional AI runtime.

        The derived TorchScript bytes are intentionally not hashed here. A broken
        or incompatible secondary artifact is reported later by torch.jit.load or
        by the embedder's runtime shape/behavior checks.
        """

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

        # New checkpoint-derived caches record the verified upstream identity.
        # Legacy bundled TorchScript metadata does not, and remains discoverable;
        # loadability is deliberately deferred to the actual runtime loader.
        recorded_sha256 = metadata.get("weights_sha256")
        if recorded_sha256 is not None and str(recorded_sha256).lower() != weights_sha256:
            raise RuntimeError(
                f"Pet scanning unavailable: DINOv2 checkpoint identity mismatch at {metadata_path}."
            )
        recorded_size = metadata.get("weights_size")
        if recorded_size is not None and int(recorded_size) != weights_size:
            raise RuntimeError(
                f"Pet scanning unavailable: DINOv2 checkpoint size contract mismatch at {metadata_path}."
            )

        try:
            cache_size = model_path.stat().st_size
        except OSError as exc:
            raise RuntimeError(
                f"Pet scanning unavailable: DINOv2 TorchScript cache is unreadable at {model_path}."
            ) from exc
        if cache_size <= 0:
            raise RuntimeError(
                f"Pet scanning unavailable: DINOv2 TorchScript cache is empty at {model_path}."
            )

    def write_prebuilt_torchscript_metadata(
        model_path: Path,
        *,
        download_url: str,
        download_sha256: str,
        download_size: int,
    ) -> None:
        """Publish metadata for an explicitly configured prebuilt cache artifact."""

        model_path = Path(model_path)
        metadata_path = pipeline._dinov2_metadata_path(model_path)
        payload = {
            "model_name": manifest["model_name"],
            "source_repository": manifest["source_repository"],
            "source_revision": manifest["source_revision"],
            "download_url": download_url,
            "download_sha256": download_sha256,
            "download_size": int(download_size),
            "derived_torchscript_size": int(model_path.stat().st_size),
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
            pipeline._raise_if_model_storage_error(exc, metadata_path)
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass

    pipeline.pet_embedder_model_url = pet_embedder_model_url
    pipeline._validate_dinov2_cache_metadata = validate_dinov2_cache_metadata
    _install_bootstrap_runtime(
        pipeline,
        manifest=manifest,
        weights_sha256=weights_sha256,
        weights_size=weights_size,
    )

    def download_dinov2_model(self, model_path: Path):
        from .model_bootstrap import _download_dinov2_release

        url = pet_embedder_model_url()
        if not url:
            raise RuntimeError(
                "Pet scanning unavailable: no DINOv2 weights download source is configured."
            )

        model_path = Path(model_path)
        legacy_torchscript_url = str(manifest.get("torchscript_url") or "").strip()
        if legacy_torchscript_url and url == legacy_torchscript_url:
            # This path is only used when a deployment explicitly configures a
            # prebuilt TorchScript artifact. Here that file is the downloaded
            # product itself, so validating its download hash does not reintroduce
            # a hash identity for locally derived TorchScript caches.
            legacy_sha256 = str(manifest.get("torchscript_sha256") or "").strip().lower()
            legacy_size = int(manifest.get("torchscript_size") or 0)
            if len(legacy_sha256) != 64 or legacy_size <= 0:
                raise RuntimeError(
                    "Pet scanning unavailable: configured prebuilt DINOv2 "
                    "TorchScript integrity metadata is invalid."
                )
            pipeline._install_certifi_environment()
            pipeline._download_file(
                url,
                model_path,
                label="DINOv2 TorchScript model",
                expected_sha256=legacy_sha256,
                max_bytes=legacy_size,
            )
            actual_size = model_path.stat().st_size
            if actual_size != legacy_size:
                raise RuntimeError(
                    "Pet scanning unavailable: DINOv2 TorchScript download size mismatch "
                    f"({actual_size} != {legacy_size})."
                )
            write_prebuilt_torchscript_metadata(
                model_path,
                download_url=url,
                download_sha256=legacy_sha256,
                download_size=legacy_size,
            )
        else:
            _download_dinov2_release(model_path, url=url)

        # Secondary-artifact validation belongs to the runtime loader, not to
        # the upstream checkpoint hash stage.
        model = self._torch.jit.load(str(model_path), map_location=self._device)
        model.eval()
        model.to(self._device)
        return model

    pipeline._DinoV2Embedder._download_dinov2_model = download_dinov2_model
    _INSTALLED = True


def _install_bootstrap_runtime(
    pipeline,
    *,
    manifest: dict,
    weights_sha256: str,
    weights_size: int,
) -> None:
    from . import model_bootstrap as bootstrap

    def write_dinov2_metadata(model_path: Path, *, weights_url: str) -> None:
        model_path = Path(model_path)
        metadata_path = pipeline._dinov2_metadata_path(model_path)
        payload = {
            "model_name": manifest["model_name"],
            "source_repository": manifest["source_repository"],
            "source_revision": manifest["source_revision"],
            "weights_url": weights_url,
            "weights_sha256": weights_sha256,
            "weights_size": weights_size,
            "derived_torchscript_size": int(model_path.stat().st_size),
            "input_shape": manifest["input_shape"],
            "output_shape": manifest["output_shape"],
        }
        temp_path = metadata_path.with_name(
            f".{metadata_path.name}.{bootstrap.uuid.uuid4().hex}.tmp"
        )
        try:
            metadata_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            temp_path.replace(metadata_path)
        except OSError as exc:
            pipeline._raise_if_model_storage_error(exc, metadata_path)
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass

    def remove_dinov2_artifact(model_path: Path) -> None:
        # Final model/metadata paths are shared publication points. Never delete
        # one just because this acquisition attempt failed: another concurrent
        # attempt may already have published a valid artifact there.
        model_path = Path(model_path)
        if model_path.exists():
            return
        for parent in (model_path.parent, model_path.parent.parent):
            try:
                parent.rmdir()
            except OSError:
                break

    def download_dinov2_release(model_path: Path, *, url: str) -> None:
        """Download and verify Meta's checkpoint, then build a local TorchScript cache."""

        source = f"{manifest['source_repository']}:{manifest['source_revision']}"
        input_shape = tuple(int(value) for value in manifest["input_shape"])
        expected_shape = tuple(int(value) for value in manifest["output_shape"])
        model_path = Path(model_path)

        try:
            pipeline._install_certifi_environment()
            bootstrap._ensure_dino_metadata_writable(model_path)

            try:
                temp_context = tempfile.TemporaryDirectory(
                    prefix="iphoto-dinov2-build-",
                    dir=model_path.parent,
                )
            except OSError as exc:
                pipeline._raise_if_model_storage_error(exc, model_path.parent)

            with temp_context as temp_dir:
                temp_root = Path(temp_dir)
                checkpoint_path = temp_root / "dinov2_vits14_pretrain.pth"
                candidate = temp_root / model_path.name

                # Integrity validation stops at the downloaded upstream artifact.
                # The local TorchScript serialization is intentionally not hashed.
                pipeline._download_file(
                    url,
                    checkpoint_path,
                    label="DINOv2 official checkpoint",
                    expected_sha256=weights_sha256,
                    max_bytes=weights_size,
                )
                actual_size = checkpoint_path.stat().st_size
                if actual_size != weights_size:
                    raise RuntimeError(
                        "Pet scanning unavailable: DINOv2 checkpoint size mismatch "
                        f"({actual_size} != {weights_size})."
                    )

                # Import the optional AI runtime only after the checkpoint has
                # completed the download-integrity stage. This keeps integrity
                # tests and model discovery independent of an installed torch.
                try:
                    import torch
                except ImportError as exc:
                    raise PetRuntimeUnavailableError(
                        "Pet scanning unavailable: torch is required to build the "
                        "DINOv2 cache. Install the optional Pets AI runtime with: "
                        'pip install -e ".[pets-ai]"'
                    ) from exc

                model = torch.hub.load(
                    source,
                    str(manifest["model_name"]),
                    source="github",
                    trust_repo=True,
                    weights=str(checkpoint_path),
                ).eval().cpu()
                example = torch.zeros(input_shape, dtype=torch.float32)

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
                torch.testing.assert_close(
                    scripted_output,
                    eager_output,
                    rtol=1e-4,
                    atol=1e-5,
                )
                try:
                    candidate.replace(model_path)
                except OSError as exc:
                    pipeline._raise_if_model_storage_error(exc, model_path)

            write_dinov2_metadata(model_path, weights_url=url)
            pipeline._validate_dinov2_cache_metadata(
                model_path,
                model_name=str(manifest["model_name"]),
            )
        except pipeline._ModelStoragePermissionError:
            remove_dinov2_artifact(model_path)
            raise
        except Exception as exc:  # noqa: BLE001 - network/hub/torch failures vary
            remove_dinov2_artifact(model_path)
            if isinstance(
                exc,
                (PetModelUnavailableError, PetRuntimeUnavailableError),
            ):
                raise
            raise PetModelUnavailableError(
                "Pet scanning unavailable: failed to build DINOv2 from the "
                f"verified checkpoint {url} ({bootstrap._error_reason(exc)})."
            ) from exc

    bootstrap._download_dinov2_release = download_dinov2_release
    bootstrap._write_dinov2_metadata = write_dinov2_metadata
    bootstrap._remove_dinov2_artifact = remove_dinov2_artifact

    # Keep one authoritative install-root policy. The public pipeline helpers
    # delegate to the corrected bootstrap policy instead of retaining the old
    # permissive write-probe implementation.
    pipeline.pet_model_install_root = bootstrap._default_install_root
    pipeline._directory_is_writable = bootstrap._directory_is_writable_for_install

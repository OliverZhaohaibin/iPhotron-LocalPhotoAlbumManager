"""Pets recognition pipeline compatibility surface.

The stable public import path remains ``iPhoto.pets.pipeline`` while the bulk of
legacy recognition logic lives in ``_pipeline_impl``. First-use model
acquisition hardening is expressed here with normal inheritance and composition;
this module never replaces itself in ``sys.modules`` and never mutates classes or
function globals in the implementation module.
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from . import _pipeline_impl as _impl
from ._pipeline_impl import *  # noqa: F403


# Private implementation helpers intentionally re-exported for the established
# test/debug surface. Keeping real module attributes here also means targeted
# monkeypatches used by model-acquisition tests affect the hardened code below
# without modifying the implementation module globally.
_EMBEDDER_MANIFEST = _impl._EMBEDDER_MANIFEST
_DINO_SOURCE_REVISION = _impl._DINO_SOURCE_REVISION
_DINO_WEIGHTS_URL = _impl._DINO_WEIGHTS_URL
_DINO_WEIGHTS_SHA256 = _impl._DINO_WEIGHTS_SHA256
_DINO_WEIGHTS_SIZE = _impl._DINO_WEIGHTS_SIZE
_ModelStoragePermissionError = _impl._ModelStoragePermissionError
_raise_if_model_storage_error = _impl._raise_if_model_storage_error
_model_storage_fallback_path = _impl._model_storage_fallback_path
_install_certifi_environment = _impl._install_certifi_environment
_dinov2_metadata_path = _impl._dinov2_metadata_path
_publish_dinov2_cache_pair = _impl._publish_dinov2_cache_pair
_validate_dinov2_cache_metadata = _impl._validate_dinov2_cache_metadata
_file_sha256 = _impl._file_sha256
_error_reason = _impl._error_reason
_original_download_file = _impl._download_file

_DINO_EXPECTED_REPOSITORY = "facebookresearch/dinov2"
_DINO_EXPECTED_REVISION = "7764ea0f912e53c92e82eb78a2a1631e92725fc8"
_DINO_EXPECTED_TREE_SHA1 = "2a27257b79b0633b027a21014bc9360e3c1b3f43"
_DINO_SOURCE_ARCHIVE_MAX_BYTES = 64 * 1024 * 1024
_DINO_SOURCE_TREE_SHA1 = str(_EMBEDDER_MANIFEST.get("source_tree_sha1") or "").lower()
_DINO_SOURCE_ARCHIVE_URL = str(
    _EMBEDDER_MANIFEST.get("source_archive_url")
    or (
        "https://github.com/"
        f"{_EMBEDDER_MANIFEST['source_repository']}/archive/"
        f"{_DINO_SOURCE_REVISION}.zip"
    )
)


def _validate_dinov2_source_pin() -> None:
    """Reject mutable or redirected DINOv2 source declarations."""

    repository = str(_EMBEDDER_MANIFEST.get("source_repository") or "")
    revision = str(_DINO_SOURCE_REVISION or "").lower()
    if repository != _DINO_EXPECTED_REPOSITORY:
        raise _impl.PetPipelineInvariantError(
            "Pets embedder source repository does not match the production pin."
        )
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None or revision != _DINO_EXPECTED_REVISION:
        raise _impl.PetPipelineInvariantError(
            "Pets embedder source revision must match the immutable production commit."
        )
    if (
        re.fullmatch(r"[0-9a-f]{40}", _DINO_SOURCE_TREE_SHA1) is None
        or _DINO_SOURCE_TREE_SHA1 != _DINO_EXPECTED_TREE_SHA1
    ):
        raise _impl.PetPipelineInvariantError(
            "Pets embedder source tree does not match the production pin."
        )
    expected_url = f"https://github.com/{repository}/archive/{revision}.zip"
    parsed = urlparse(_DINO_SOURCE_ARCHIVE_URL)
    if parsed.scheme.lower() != "https" or _DINO_SOURCE_ARCHIVE_URL != expected_url:
        raise _impl.PetPipelineInvariantError(
            "Pets embedder source archive URL does not match the pinned repository revision."
        )


_validate_dinov2_source_pin()


def _download_file(*args, **kwargs):
    """Keep detector-specific remediation out of DINOv2 download failures."""

    label = str(kwargs.get("label") or "")
    try:
        return _original_download_file(*args, **kwargs)
    except RuntimeError as exc:
        if not label.startswith("DINOv2"):
            raise
        message = str(exc)
        detector_hint = (
            "Check your network connection, set "
            f"{_impl.PET_DETECTOR_MODEL_URL_ENV}, or install the model manually."
        )
        if detector_hint not in message:
            raise
        raise RuntimeError(
            message.replace(
                detector_hint,
                "Check your network connection or install the model manually.",
            )
        ) from exc


_LegacyDinoV2Embedder = _impl._DinoV2Embedder


class _DinoV2Embedder(_LegacyDinoV2Embedder):
    """Build a verified DINOv2 cache without coupling it to device activation."""

    def _build_dinov2_cache(self, model_path: Path):
        loaded = self._build_verified_dinov2_cpu_cache(model_path)
        try:
            loaded.eval()
            loaded.to(self._device)
        except Exception as exc:  # noqa: BLE001 - backend failures vary by runtime
            raise _impl.PetModelUnavailableError(
                "Pet scanning unavailable: DINOv2 cache was built and verified, "
                f"but the runtime device could not load it ({_error_reason(exc)})."
            ) from exc
        return loaded

    def _build_verified_dinov2_cpu_cache(self, model_path: Path):
        """Build, publish, validate, and reload a DINOv2 cache entirely on CPU."""

        published = False
        try:
            _install_certifi_environment()
            try:
                model_path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                _raise_if_model_storage_error(exc, model_path.parent)
            try:
                temp_context = tempfile.TemporaryDirectory(
                    prefix="iphoto-dinov2-build-",
                    dir=model_path.parent,
                )
            except OSError as exc:
                _raise_if_model_storage_error(exc, model_path.parent)

            with temp_context as temp_dir:
                checkpoint = Path(temp_dir) / "dinov2_vits14_pretrain.pth"
                candidate = Path(temp_dir) / model_path.name
                metadata_path = _dinov2_metadata_path(candidate)
                _download_file(
                    _DINO_WEIGHTS_URL,
                    checkpoint,
                    label="DINOv2 checkpoint",
                    expected_sha256=_DINO_WEIGHTS_SHA256,
                    max_bytes=_DINO_WEIGHTS_SIZE,
                    exact_size=_DINO_WEIGHTS_SIZE,
                )
                source = f"{_EMBEDDER_MANIFEST['source_repository']}:{_DINO_SOURCE_REVISION}"
                model = self._torch.hub.load(
                    source,
                    self._model_name,
                    source="github",
                    trust_repo=True,
                    skip_validation=True,
                    pretrained=False,
                ).eval().cpu()
                state_dict = self._torch.load(
                    str(checkpoint),
                    map_location="cpu",
                    weights_only=True,
                )
                model.load_state_dict(state_dict, strict=True)
                example = self._torch.randn(
                    tuple(_EMBEDDER_MANIFEST["input_shape"]),
                    dtype=self._torch.float32,
                )
                with self._torch.no_grad():
                    eager_output = model(example)
                    traced = self._torch.jit.trace(model, example, strict=False)
                    traced.save(str(candidate))
                    scripted = self._torch.jit.load(str(candidate), map_location="cpu").eval()
                    scripted_output = scripted(example)
                if isinstance(eager_output, (list, tuple)):
                    eager_output = eager_output[0]
                if isinstance(scripted_output, (list, tuple)):
                    scripted_output = scripted_output[0]
                output_shape = tuple(_EMBEDDER_MANIFEST["output_shape"])
                actual_shape = tuple(scripted_output.shape)
                if actual_shape != output_shape:
                    raise RuntimeError(
                        f"DINOv2 output shape mismatch: {actual_shape} != {output_shape}"
                    )
                self._torch.testing.assert_close(
                    scripted_output,
                    eager_output,
                    rtol=1e-4,
                    atol=1e-5,
                )
                metadata = {
                    "artifact_kind": "derived_checkpoint_cache",
                    "model_name": self._model_name,
                    "source_repository": str(_EMBEDDER_MANIFEST["source_repository"]),
                    "source_revision": _DINO_SOURCE_REVISION,
                    "weights_sha256": _DINO_WEIGHTS_SHA256,
                    "weights_size": _DINO_WEIGHTS_SIZE,
                    "derived_torchscript_sha256": _file_sha256(candidate).lower(),
                    "derived_torchscript_size": candidate.stat().st_size,
                    "input_shape": list(_EMBEDDER_MANIFEST["input_shape"]),
                    "output_shape": list(output_shape),
                }
                try:
                    metadata_path.write_text(
                        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )
                except OSError as exc:
                    _raise_if_model_storage_error(exc, metadata_path)
                _publish_dinov2_cache_pair(candidate, metadata_path, model_path)
                _validate_dinov2_cache_metadata(model_path, model_name=self._model_name)
                published = True
                return self._torch.jit.load(str(model_path), map_location="cpu")
        except _ModelStoragePermissionError:
            raise
        except Exception as exc:
            if not published:
                model_path.unlink(missing_ok=True)
                _dinov2_metadata_path(model_path).unlink(missing_ok=True)
            raise _impl.PetModelUnavailableError(
                "Pet scanning unavailable: failed to build the verified DINOv2 "
                f"model cache ({_error_reason(exc)})."
            ) from exc


class _LazyDinoV2Embedder:
    """Delay torch import/model acquisition until YOLOX accepted a pet crop."""

    def __init__(self, factory) -> None:
        self._factory = factory
        self._delegate = None

    def _ensure_delegate(self):
        if self._delegate is None:
            self._delegate = self._factory()
        return self._delegate

    def embed(self, image):
        return self._ensure_delegate().embed(image)


class PetClusterPipeline(_impl.PetClusterPipeline):
    """Pipeline with DINOv2 construction deferred until the first accepted crop."""

    def _ensure_embedder(self):
        if self._embedder is None:
            model_dir = self._resolve_model_path(
                Path("embedding") / self._embedding_model_name,
                directory=True,
            )

            def create_embedder():
                try:
                    return _DinoV2Embedder(
                        model_dir,
                        model_name=self._embedding_model_name,
                        allow_model_download=self._allow_model_download,
                    )
                except (
                    _impl.PetRuntimeUnavailableError,
                    _impl.PetModelUnavailableError,
                    _impl.PetPipelineInvariantError,
                ):
                    raise
                except RuntimeError as exc:
                    raise _impl.PetModelUnavailableError(str(exc)) from exc

            self._embedder = _LazyDinoV2Embedder(create_embedder)
        return self._embedder


def __getattr__(name: str):
    """Expose legacy private helpers without replacing this module object."""

    try:
        return getattr(_impl, name)
    except AttributeError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_impl)))

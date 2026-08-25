"""Pets recognition pipeline compatibility surface.

The stable public import path remains ``iPhoto.pets.pipeline`` while the bulk of
legacy recognition logic lives in ``_pipeline_impl``. First-use model
acquisition hardening is expressed here with normal inheritance and composition;
this module never replaces itself in ``sys.modules`` and never mutates classes or
function globals in the implementation module.
"""

from __future__ import annotations

from pathlib import Path

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
_DINO_TORCHSCRIPT_URL = _impl._DINO_TORCHSCRIPT_URL
_DINO_TORCHSCRIPT_SHA256 = _impl._DINO_TORCHSCRIPT_SHA256
_DINO_TORCHSCRIPT_SIZE = _impl._DINO_TORCHSCRIPT_SIZE
_DINO_CACHE_SCHEMA_VERSION = _impl._DINO_CACHE_SCHEMA_VERSION
_DINO_TORCH_VERSION = _impl._DINO_TORCH_VERSION
_ModelStoragePermissionError = _impl._ModelStoragePermissionError
_raise_if_model_storage_error = _impl._raise_if_model_storage_error
_model_storage_fallback_path = _impl._model_storage_fallback_path
_install_certifi_environment = _impl._install_certifi_environment
_dinov2_metadata_path = _impl._dinov2_metadata_path
_validate_dinov2_cache_metadata = _impl._validate_dinov2_cache_metadata
_dinov2_release_metadata = _impl._dinov2_release_metadata
_dinov2_acquisition_lock = _impl._dinov2_acquisition_lock
_publish_dinov2_cache_pair = _impl._publish_dinov2_cache_pair
_file_sha256 = _impl._file_sha256
_error_reason = _impl._error_reason
_original_download_file = _impl._download_file

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


def resolve_pet_model_path(relative_path: Path, *, directory: bool = False) -> Path:
    """Resolve Pets models without mutating a DINOv2 cache from the read path."""

    if not directory:
        return _impl.resolve_pet_model_path(relative_path, directory=False)

    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Pet model path must be relative to a configured model root.")

    override = _impl.pet_model_override_dir()
    if override is not None:
        candidate = override / relative
        model_path = candidate / f"{relative.name}.pt"
        if model_path.is_file():
            try:
                _validate_dinov2_cache_metadata(model_path, model_name=relative.name)
            except (OSError, RuntimeError):
                # Acquisition repairs this exact authoritative location under lock.
                pass
        return candidate

    user_cache = _impl.user_pet_model_cache_dir()
    bundled = _impl.bundled_pet_model_dir()
    bundled_invalid = False
    for root in _impl.pet_model_search_roots():
        candidate = root / relative
        if not candidate.is_dir():
            continue
        try:
            model_name = relative.name
            model_path = candidate / f"{model_name}.pt"
            if not model_path.is_file():
                raise RuntimeError("DINOv2 model file is missing")
            _validate_dinov2_cache_metadata(model_path, model_name=model_name)
            return candidate
        except (OSError, RuntimeError):
            # DINOv2 cache cleanup is intentionally deferred to the acquisition
            # owner. A resolver can race with metadata-first publication, so it
            # must never unlink either side of the cache pair here.
            if root == bundled:
                bundled_invalid = True

    if bundled_invalid:
        return user_cache / relative
    return _impl.pet_model_install_root() / relative


_LegacyDinoV2Embedder = _impl._DinoV2Embedder


class _DinoV2Embedder(_LegacyDinoV2Embedder):
    """Acquire the fixed DINOv2 Release without coupling it to device activation."""

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

    def _load_verified_dinov2_cpu_cache(self, model_path: Path):
        if not model_path.is_file():
            return None
        try:
            _validate_dinov2_cache_metadata(model_path, model_name=self._model_name)
        except RuntimeError:
            return None
        return self._torch.jit.load(str(model_path), map_location="cpu")

    def _build_verified_dinov2_cpu_cache(self, model_path: Path):
        """Download, publish, validate, and reload the Release artifact on CPU."""

        try:
            return _impl._acquire_dinov2_release(
                self._torch,
                model_path,
                model_name=self._model_name,
                download_file=_download_file,
                validate_metadata=_validate_dinov2_cache_metadata,
            )
        except _ModelStoragePermissionError:
            raise
        except Exception as exc:
            raise _impl.PetModelUnavailableError(
                "Pet scanning unavailable: failed to acquire the verified DINOv2 "
                f"Release artifact ({_error_reason(exc)})."
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

    def _resolve_model_path(self, relative_path: Path, *, directory: bool = False) -> Path:
        override = _impl.pet_model_override_dir()
        if override is not None and self._model_root != override:
            raise _impl.PetModelUnavailableError(
                "Pet scanning unavailable: model root does not match "
                f"{_impl.IPHOTO_PET_MODEL_DIR_ENV}."
            )
        if self._model_root == _impl.default_pet_model_dir():
            return resolve_pet_model_path(relative_path, directory=directory)
        return self._model_root / relative_path

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

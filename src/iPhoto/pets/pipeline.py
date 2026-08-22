"""Hardened compatibility facade for the Pets recognition pipeline.

The implementation lives in ``_pipeline_impl`` so this module can keep the
public import surface stable while applying narrowly scoped first-use model
acquisition hardening. The implementation module is exported as this module
object at the end of the file so existing monkeypatches of private helpers keep
operating on the globals used by the implementation.
"""

from __future__ import annotations

import re as _re
import sys as _sys
from pathlib import Path as _Path
from urllib.parse import urlparse as _urlparse

from . import _pipeline_impl as _impl


_DINO_EXPECTED_REPOSITORY = "facebookresearch/dinov2"
_DINO_EXPECTED_REVISION = "7764ea0f912e53c92e82eb78a2a1631e92725fc8"
_DINO_EXPECTED_TREE_SHA1 = "2a27257b79b0633b027a21014bc9360e3c1b3f43"
_DINO_SOURCE_ARCHIVE_MAX_BYTES = 64 * 1024 * 1024
_DINO_SOURCE_TREE_SHA1 = str(_impl._EMBEDDER_MANIFEST.get("source_tree_sha1") or "").lower()
_DINO_SOURCE_ARCHIVE_URL = str(
    _impl._EMBEDDER_MANIFEST.get("source_archive_url")
    or (
        "https://github.com/"
        f"{_impl._EMBEDDER_MANIFEST['source_repository']}/archive/"
        f"{_impl._DINO_SOURCE_REVISION}.zip"
    )
)


def _validate_dinov2_source_pin() -> None:
    """Reject mutable or redirected DINOv2 source declarations.

    ``torch.hub`` cannot use its branch/tag ownership validation for an arbitrary
    historical commit SHA. The production contract therefore pins the exact
    upstream repository, a full 40-hex Git commit id, and the Git tree id that
    commit resolves to. The real-source CI contract exercises that exact ref.
    """

    repository = str(_impl._EMBEDDER_MANIFEST.get("source_repository") or "")
    revision = str(_impl._DINO_SOURCE_REVISION or "").lower()
    if repository != _DINO_EXPECTED_REPOSITORY:
        raise _impl.PetPipelineInvariantError(
            "Pets embedder source repository does not match the production pin."
        )
    if _re.fullmatch(r"[0-9a-f]{40}", revision) is None or revision != _DINO_EXPECTED_REVISION:
        raise _impl.PetPipelineInvariantError(
            "Pets embedder source revision must match the immutable production commit."
        )
    if (
        _re.fullmatch(r"[0-9a-f]{40}", _DINO_SOURCE_TREE_SHA1) is None
        or _DINO_SOURCE_TREE_SHA1 != _DINO_EXPECTED_TREE_SHA1
    ):
        raise _impl.PetPipelineInvariantError(
            "Pets embedder source tree does not match the production pin."
        )
    expected_url = f"https://github.com/{repository}/archive/{revision}.zip"
    parsed = _urlparse(_DINO_SOURCE_ARCHIVE_URL)
    if parsed.scheme.lower() != "https" or _DINO_SOURCE_ARCHIVE_URL != expected_url:
        raise _impl.PetPipelineInvariantError(
            "Pets embedder source archive URL does not match the pinned repository revision."
        )


_validate_dinov2_source_pin()


_LegacyDinoV2Embedder = _impl._DinoV2Embedder
_original_download_file = _impl._download_file


class _VerifiedDinoV2Embedder(_LegacyDinoV2Embedder):
    """Preserve a verified cache when only runtime device activation fails."""

    def _build_dinov2_cache(self, model_path: _Path):
        target_device = self._device
        # The legacy builder already verifies the checkpoint, traces on CPU,
        # verifies numerical equivalence, publishes model+metadata atomically,
        # and validates the published pair. Force its final load to CPU so a
        # CUDA/runtime-provider failure cannot make it delete that valid cache.
        self._device = "cpu"
        try:
            model = super()._build_dinov2_cache(model_path)
        finally:
            self._device = target_device

        try:
            model.eval()
            model.to(target_device)
        except Exception as exc:  # noqa: BLE001 - backend failures vary by runtime
            raise _impl.PetModelUnavailableError(
                "Pet scanning unavailable: DINOv2 cache was built and verified, "
                f"but the runtime device could not load it ({_impl._error_reason(exc)})."
            ) from exc
        return model


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


def _lazy_ensure_embedder(self):
    if self._embedder is None:
        model_dir = self._resolve_model_path(
            _Path("embedding") / self._embedding_model_name,
            directory=True,
        )

        def create_embedder():
            try:
                return _impl._DinoV2Embedder(
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


def _download_file(*args, **kwargs):
    """Keep detector guidance out of DINOv2 network failures."""

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


# Export the hardening on the implementation module itself. Functions defined
# in _pipeline_impl resolve globals from that module, so tests and callers that
# monkeypatch ``iPhoto.pets.pipeline`` continue to affect the actual runtime.
_impl._DINO_SOURCE_ARCHIVE_URL = _DINO_SOURCE_ARCHIVE_URL
_impl._DINO_SOURCE_ARCHIVE_MAX_BYTES = _DINO_SOURCE_ARCHIVE_MAX_BYTES
_impl._DINO_SOURCE_TREE_SHA1 = _DINO_SOURCE_TREE_SHA1
_impl._validate_dinov2_source_pin = _validate_dinov2_source_pin
_impl._LegacyDinoV2Embedder = _LegacyDinoV2Embedder
_impl._LazyDinoV2Embedder = _LazyDinoV2Embedder
_impl._DinoV2Embedder = _VerifiedDinoV2Embedder
_impl.PetClusterPipeline._ensure_embedder = _lazy_ensure_embedder
_impl._download_file = _download_file

_sys.modules[__name__] = _impl

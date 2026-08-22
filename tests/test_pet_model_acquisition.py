from __future__ import annotations

import errno
import hashlib
from pathlib import Path

import pytest

from iPhoto.pets import pipeline as pet_pipeline

DETECTOR_RELATIVE = Path("detector") / "yolox_nano_coco.onnx"
EMBEDDER_RELATIVE = Path("embedding") / "dinov2_vits14"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4096), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _valid_detector(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"detector")
    return path


def _invalid_detector(path: Path) -> None:
    _valid_detector(path)
    with path.open("ab"):
        pass
    path.write_bytes(b"invalid")


@pytest.fixture(autouse=True)
def _clear_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("IPHOTO_PET_MODEL_DIR", raising=False)


class TestStoragePolicy:
    def test_extension_first_lookup_without_writable_probe(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        bundled = tmp_path / "extension"
        cache = tmp_path / "cache"
        _valid_detector(bundled / DETECTOR_RELATIVE)
        cache_model = _valid_detector(cache / DETECTOR_RELATIVE)
        def bundled_dir() -> Path:
            return bundled

        def cache_dir() -> Path:
            return cache

        monkeypatch.setattr(pet_pipeline, "bundled_pet_model_dir", bundled_dir)
        monkeypatch.setattr(pet_pipeline, "user_pet_model_cache_dir", cache_dir)
        probed: list[Path] = []

        def record_probe(path: Path) -> bool:
            probed.append(path)
            return True

        monkeypatch.setattr(pet_pipeline, "_directory_is_writable", record_probe)
        resolved = pet_pipeline.resolve_pet_model_path(DETECTOR_RELATIVE)
        assert resolved in {bundled / DETECTOR_RELATIVE, cache_model}
        assert not probed

    def test_cache_lookup_falls_back_after_invalid_bundled(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        bundled = tmp_path / "extension"
        cache = tmp_path / "cache"
        _invalid_detector(bundled / DETECTOR_RELATIVE)
        model = _valid_detector(cache / DETECTOR_RELATIVE)
        monkeypatch.setattr(pet_pipeline, "bundled_pet_model_dir", lambda: bundled)
        monkeypatch.setattr(pet_pipeline, "user_pet_model_cache_dir", lambda: cache)
        assert pet_pipeline.resolve_pet_model_path(DETECTOR_RELATIVE) == model
        assert (bundled / DETECTOR_RELATIVE).exists()

    def test_missing_prefers_writable_extension(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        bundled = tmp_path / "extension"
        monkeypatch.setattr(pet_pipeline, "bundled_pet_model_dir", lambda: bundled)
        monkeypatch.setattr(
            pet_pipeline,
            "_directory_is_writable",
            lambda path: path == bundled,
        )
        assert pet_pipeline.resolve_pet_model_path(DETECTOR_RELATIVE) == bundled / DETECTOR_RELATIVE

    def test_macos_app_installs_to_cache_without_probe(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        bundled = tmp_path / "iPhoto.app" / "Contents" / "Resources" / "models"
        cache = tmp_path / "cache"
        monkeypatch.setattr(pet_pipeline.sys, "platform", "darwin")
        monkeypatch.setattr(pet_pipeline, "bundled_pet_model_dir", lambda: bundled)
        monkeypatch.setattr(pet_pipeline, "user_pet_model_cache_dir", lambda: cache)

        def fail_probe(_path: Path) -> bool:
            raise AssertionError("packaged app bundles must not be probed")

        monkeypatch.setattr(pet_pipeline, "_directory_is_writable", fail_probe)
        assert pet_pipeline.pet_model_install_root() == cache
        assert pet_pipeline.resolve_pet_model_path(DETECTOR_RELATIVE) == cache / DETECTOR_RELATIVE

    @pytest.mark.parametrize("errno_value", [errno.EACCES, errno.EPERM, errno.EROFS])
    def test_permission_errors_are_typed_for_storage_fallback(
        self,
        errno_value: int,
        tmp_path: Path,
    ) -> None:
        with pytest.raises(pet_pipeline._ModelStoragePermissionError):
            pet_pipeline._raise_if_model_storage_error(OSError(errno_value, "denied"), tmp_path)

    @pytest.mark.parametrize("errno_value", [errno.ENOSPC, errno.EIO])
    def test_non_permission_errors_fail_without_retry(
        self,
        errno_value: int,
        tmp_path: Path,
    ) -> None:
        with pytest.raises(OSError) as raised:
            pet_pipeline._raise_if_model_storage_error(OSError(errno_value, "failed"), tmp_path)
        assert type(raised.value) is not pet_pipeline._ModelStoragePermissionError


class TestOverrideAuthority:
    def test_search_and_install_are_override_only(self, tmp_path, monkeypatch) -> None:
        override = tmp_path / "override"
        monkeypatch.setenv("IPHOTO_PET_MODEL_DIR", str(override))
        assert pet_pipeline.pet_model_search_roots() == (override,)
        assert pet_pipeline.pet_model_install_root() == override
        assert (
            pet_pipeline.resolve_pet_model_path(DETECTOR_RELATIVE)
            == override / DETECTOR_RELATIVE
        )

    def test_pipeline_rejects_mismatched_model_root(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("IPHOTO_PET_MODEL_DIR", str(tmp_path / "override"))
        pipeline_instance = pet_pipeline.PetClusterPipeline(model_root=tmp_path / "models")
        with pytest.raises(pet_pipeline.PetModelUnavailableError):
            pipeline_instance._resolve_model_path(DETECTOR_RELATIVE)


class TestDownloadsAndMetadata:
    def test_exact_size_is_enforced(self, tmp_path: Path, monkeypatch) -> None:
        target = tmp_path / "model.bin"
        payload = b"exact-size"

        class Response:
            def __init__(self, payload: bytes) -> None:
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size: int):
                value, self.payload = self.payload, b""
                return value

        monkeypatch.setattr(
            pet_pipeline.request,
            "urlopen",
            lambda *_args, **_kwargs: Response(payload),
        )
        pet_pipeline._download_file(
            "https://example.test/model.bin",
            target,
            label="test model",
            expected_sha256=_sha256_target(payload),
            max_bytes=100,
            exact_size=len(payload),
        )
        assert target.read_bytes() == payload

        short = payload[:-1]
        monkeypatch.setattr(
            pet_pipeline.request,
            "urlopen",
            lambda *_args, **_kwargs: ShortResponse(short),
        )
        with pytest.raises(RuntimeError, match="wrong file size"):
            pet_pipeline._download_file(
                "https://example.test/model.bin",
                target.with_suffix(".short"),
                label="test model",
                expected_sha256=_sha256_target(short),
                max_bytes=100,
                exact_size=len(payload),
            )

    def test_manifest_dino_contract_uses_official_checkpoint(self) -> None:
        manifest = pet_pipeline._EMBEDDER_MANIFEST
        assert manifest["weights_url"] == (
            "https://dl.fbaipublicfiles.com/dinov2/dinov2_vits14/"
            "dinov2_vits14_pretrain.pth"
        )
        assert manifest["weights_sha256"] == (
            "b938bf1bc15cd2ec0feacfe3a1bb553fe8ea9ca46a7e1d8d00217f29aef60cd9"
        )
        assert manifest["weights_size"] == 88283115
        assert manifest["torchscript_url"] is None

    def test_derived_metadata_requires_local_integrity(self, tmp_path: Path) -> None:
        content = b"derived-torchscript"
        metadata = {
            "artifact_kind": "derived_checkpoint_cache",
            "model_name": "dinov2_vits14",
            "source_repository": pet_pipeline._EMBEDDER_MANIFEST["source_repository"],
            "source_revision": pet_pipeline._DINO_SOURCE_REVISION,
            "weights_sha256": pet_pipeline._DINO_WEIGHTS_SHA256,
            "weights_size": pet_pipeline._DINO_WEIGHTS_SIZE,
            "derived_torchscript_sha256": hashlib.sha256(content).hexdigest(),
            "derived_torchscript_size": len(content),
            "input_shape": [1, 3, 224, 224],
            "output_shape": [1, 384],
        }
        model_path = tmp_path / "dinov2_vits14.pt"
        model_path.write_bytes(content)
        pet_pipeline._dinov2_metadata_path(model_path).write_text(
            hashlib_json(metadata), encoding="utf-8"
        )
        pet_pipeline._validate_dinov2_cache_metadata(model_path, model_name="dinov2_vits14")
        model_path.write_bytes(content + b"corrupt")
        with pytest.raises(RuntimeError, match="integrity check failed"):
            pet_pipeline._validate_dinov2_cache_metadata(
                model_path,
                model_name="dinov2_vits14",
            )


class ShortResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _size: int):
        value, self.payload = self.payload, b""
        return value


def _sha256_target(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def hashlib_json(value: dict) -> str:
    import json

    return json.dumps(value)


class TestLazyPipeline:
    def test_empty_batch_does_not_initialize_models(self, tmp_path: Path) -> None:
        pipeline_instance = pet_pipeline.PetClusterPipeline(model_root=tmp_path / "models")

        def fail_initialize():
            raise AssertionError("empty batches must not load or download models")

        pipeline_instance._ensure_detector = fail_initialize
        pipeline_instance._ensure_embedder = fail_initialize
        assert pipeline_instance.detect_pets_for_rows([], library_root=tmp_path, thumbnail_dir=tmp_path) == []
